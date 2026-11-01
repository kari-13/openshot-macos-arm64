#!/usr/bin/env zsh
#
# fix-qt-links.sh - make every Mach-O library in a tree use ONE Qt.
#
# Problem it solves: a library (e.g. libopenshot.dylib, _openshot.so) was linked
# against one Qt (Homebrew, MacPorts, Qt online installer, ...) while the host
# application (PyQt5/PyQt6/PySide wheels) loads its own bundled Qt. Two Qt copies
# in one process register the same Objective-C classes and crash, typically with
# a SIGSEGV during window exposure.
#
# What it does: finds .so/.dylib files (plus any --file you name), finds absolute
# references to Qt frameworks/dylibs that point OUTSIDE the target Qt directory,
# and rewrites them (install_name_tool) to the same library inside the target Qt
# directory. Relative references (@rpath, @loader_path, ...) are left alone.
#
# Safety model:
#   * Original files are never modified in place. Each change is made on a temp
#     copy, signed, verified, and only then moved over the original (atomic, and
#     it gives the file a fresh inode so macOS does not keep a stale signature).
#   * A file is all-or-nothing: if any reference in it cannot be repaired, the
#     file is left untouched and reported.
#   * Every modified file is backed up first; --restore puts backups back.
#   * Qt major versions are checked (Qt5 refs are never pointed at Qt6 libs).
#   * --dry-run and --check never write anything.
#
# Exit codes: 0 success | 1 repair/verification errors (or --check found work)
#             2 usage error | 3 environment problem (tools, directories)
#
# Requires macOS (otool, install_name_tool, codesign, file) and zsh.

setopt extended_glob pipe_fail no_unset

typeset -r SCRIPT_VERSION="2.0.0"
typeset -r SCRIPT_NAME="${0:t}"

# ---------------------------------------------------------------- defaults ---
typeset ROOT="${OPENSHOT_ROOT:-$PWD}"
typeset QT_LIB_DIR="${OPENSHOT_QT_LIB_DIR:-${QT_LIB_DIR:-}}"
typeset BACKUP_ROOT="${QT_LINK_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/qt-link-repair}"
typeset SIGN_ID="-"            # "-" = ad-hoc signature
typeset LOG_FILE=""
typeset RESTORE_FROM=""
typeset -i DRY_RUN=0 CHECK=0 SIGN=1 VERBOSE=0 QUIET=0
typeset -i INCLUDE_VENV=0 ALLOW_MAJOR_MISMATCH=0
typeset -a MATCHES EXCLUDES EXTRA_FILES
typeset -a EXTS=(so dylib)

# ---------------------------------------------------------------- counters ---
typeset -i SCANNED=0 NOT_MACHO=0 UNCHANGED=0 PENDING=0 REPAIRED=0
typeset -i FAILED=0 SKIPPED=0 WARNINGS=0 ERRORS=0 RESTORED=0

# ----------------------------------------------------------------- state -----
typeset SESSION_DIR=""
typeset CURRENT_TMP=""
typeset -A MAJOR_CACHE

# ----------------------------------------------------------------- logging ---
if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
    typeset C_RED=$'\e[31m' C_YELLOW=$'\e[33m' C_CYAN=$'\e[36m' C_GREEN=$'\e[32m' C_DIM=$'\e[2m' C_OFF=$'\e[0m'
else
    typeset C_RED="" C_YELLOW="" C_CYAN="" C_GREEN="" C_DIM="" C_OFF=""
fi

log_file() {
    [[ -n $LOG_FILE ]] || return 0
    print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE" 2>/dev/null
    return 0
}
log_info()    { (( QUIET )) || print -r -- "${C_CYAN}[INFO]${C_OFF}  $*"; log_file "[INFO]  $*"; return 0; }
log_ok()      { (( QUIET )) || print -r -- "${C_GREEN}[OK]${C_OFF}    $*"; log_file "[OK]    $*"; return 0; }
log_detail()  { (( QUIET )) || print -r -- "${C_DIM}        $*${C_OFF}"; log_file "        $*"; return 0; }
log_verbose() { (( VERBOSE )) && print -r -- "${C_DIM}[DEBUG] $*${C_OFF}"; log_file "[DEBUG] $*"; return 0; }
log_warn()    { (( WARNINGS++ )); print -r -- "${C_YELLOW}[WARN]${C_OFF}  $*" >&2; log_file "[WARN]  $*"; return 0; }
log_error()   { (( ERRORS++ ));   print -r -- "${C_RED}[ERROR]${C_OFF} $*" >&2; log_file "[ERROR] $*"; return 0; }
die()         { local code=$1; shift; log_error "$*"; exit "$code"; }

usage() {
    cat <<EOF
$SCRIPT_NAME $SCRIPT_VERSION - make every Mach-O library in a tree use one Qt

Usage: $SCRIPT_NAME [options]
       $SCRIPT_NAME --restore DIR|latest [--dry-run]

Scope:
  --root DIR              Tree to scan (default: \$OPENSHOT_ROOT or current directory)
  --qt-lib-dir DIR        Target Qt lib directory (default: auto-detect PyQt5/PyQt6/
                          PySide wheel Qt under the active venv or --root)
  --match GLOB            Only rewrite references whose path matches GLOB
                          (repeatable), e.g. --match '*qt@5*'
  --exclude DIR           Skip a directory (repeatable; relative to --root)
  --ext EXT               Also scan files with this extension (repeatable;
                          default: so, dylib)
  --file FILE             Also process this exact file, e.g. an app's main binary
                          (repeatable)
  --include-venv          Also scan .venv/venv directories (skipped by default)

Behaviour:
  -n, --dry-run           Show what would change; write nothing
  --check                 Like --dry-run, but exit 1 if anything needs repair
  --no-sign               Do not re-sign modified files (arm64 may kill unsigned
                          or invalidly signed binaries)
  --sign-identity ID      codesign identity to use (default: ad-hoc "-")
  --allow-major-mismatch  Allow rewriting e.g. a Qt 5 ref to a Qt 6 library (unsafe)
  --backup-dir DIR        Where backups go (default: $BACKUP_ROOT)
  --restore DIR|latest    Restore files from a backup session and exit

Output:
  -v, --verbose           Show skipped files and extra detail
  -q, --quiet             Only warnings, errors and the final summary line
  --log FILE              Also append all messages to FILE
  -h, --help              This help
  --version               Print version

Environment:
  OPENSHOT_ROOT, OPENSHOT_QT_LIB_DIR, QT_LINK_BACKUP_DIR, NO_COLOR

Examples:
  $SCRIPT_NAME --root ~/openshot-research --dry-run
  $SCRIPT_NAME --root ~/openshot-research
  $SCRIPT_NAME --root ~/openshot-research --check          # CI-style verification
  $SCRIPT_NAME --root ./build --qt-lib-dir /path/to/PyQt5/Qt5/lib --match '*qt@5*'
  $SCRIPT_NAME --restore latest
EOF
}

# -------------------------------------------------------- cleanup & traps ----
cleanup() {
    if [[ -n $CURRENT_TMP && -e $CURRENT_TMP ]]; then
        rm -f -- "$CURRENT_TMP"
    fi
    CURRENT_TMP=""
}
trap cleanup EXIT
trap 'cleanup; print -r -- "Interrupted." >&2; exit 130' INT TERM HUP

# --------------------------------------------------------- argument parsing --
need_arg() {
    (( $# >= 2 )) && [[ $2 != --* ]] || die 2 "Option $1 requires a value"
}

while (( $# )); do
    case $1 in
        --root)                 need_arg "$@"; ROOT=$2; shift 2 ;;
        --root=*)               ROOT=${1#*=}; shift ;;
        --qt-lib-dir)           need_arg "$@"; QT_LIB_DIR=$2; shift 2 ;;
        --qt-lib-dir=*)         QT_LIB_DIR=${1#*=}; shift ;;
        --match)                need_arg "$@"; MATCHES+=("$2"); shift 2 ;;
        --match=*)              MATCHES+=("${1#*=}"); shift ;;
        --exclude)              need_arg "$@"; EXCLUDES+=("$2"); shift 2 ;;
        --exclude=*)            EXCLUDES+=("${1#*=}"); shift ;;
        --ext)                  need_arg "$@"; EXTS+=("${2#.}"); shift 2 ;;
        --ext=*)                EXTS+=("${${1#*=}#.}"); shift ;;
        --file)                 need_arg "$@"; EXTRA_FILES+=("$2"); shift 2 ;;
        --file=*)               EXTRA_FILES+=("${1#*=}"); shift ;;
        --include-venv)         INCLUDE_VENV=1; shift ;;
        -n|--dry-run)           DRY_RUN=1; shift ;;
        --check)                CHECK=1; DRY_RUN=1; shift ;;
        --no-sign)              SIGN=0; shift ;;
        --sign-identity)        need_arg "$@"; SIGN_ID=$2; shift 2 ;;
        --sign-identity=*)      SIGN_ID=${1#*=}; shift ;;
        --allow-major-mismatch) ALLOW_MAJOR_MISMATCH=1; shift ;;
        --backup-dir)           need_arg "$@"; BACKUP_ROOT=$2; shift 2 ;;
        --backup-dir=*)         BACKUP_ROOT=${1#*=}; shift ;;
        --restore)              need_arg "$@"; RESTORE_FROM=$2; shift 2 ;;
        --restore=*)            RESTORE_FROM=${1#*=}; shift ;;
        --log)                  need_arg "$@"; LOG_FILE=$2; shift 2 ;;
        --log=*)                LOG_FILE=${1#*=}; shift ;;
        -v|--verbose)           VERBOSE=1; shift ;;
        -q|--quiet)             QUIET=1; shift ;;
        -h|--help)              usage; exit 0 ;;
        --version)              print -r -- "$SCRIPT_NAME $SCRIPT_VERSION"; exit 0 ;;
        --)                     shift; break ;;
        -*)                     log_error "Unknown option: $1"; usage >&2; exit 2 ;;
        *)                      log_error "Unexpected argument: $1"; usage >&2; exit 2 ;;
    esac
done
(( $# == 0 )) || die 2 "Unexpected argument: $1"

if [[ -n $LOG_FILE ]]; then
    mkdir -p -- "${LOG_FILE:h}" 2>/dev/null && : >> "$LOG_FILE" 2>/dev/null \
        || die 3 "Cannot write log file: $LOG_FILE"
fi
BACKUP_ROOT=${BACKUP_ROOT:A}

# ------------------------------------------------------------- restore mode --
do_restore() {
    local dir=$1 manifest orig bak tmp
    if [[ $dir == latest ]]; then
        local -a sessions
        sessions=("$BACKUP_ROOT"/*(N/om))
        (( ${#sessions} )) || die 3 "No backup sessions found in $BACKUP_ROOT"
        dir=${sessions[1]}
    fi
    dir=${dir:A}
    manifest="$dir/manifest.tsv"
    [[ -f $manifest ]] || die 3 "Not a backup session (manifest.tsv missing): $dir"

    log_info "Restoring from: $dir"
    (( DRY_RUN )) && log_warn "DRY-RUN: no files will be modified."

    while IFS=$'\t' read -r orig bak; do
        [[ -n $orig && -n $bak ]] || continue
        if [[ ! -f $bak ]]; then
            log_error "Backup missing for $orig: $bak"; (( FAILED++ )); continue
        fi
        if [[ ! -d ${orig:h} || ! -w ${orig:h} ]]; then
            log_error "Cannot write to directory of $orig"; (( FAILED++ )); continue
        fi
        if (( DRY_RUN )); then
            log_info "WOULD RESTORE $orig"; (( RESTORED++ )); continue
        fi
        tmp="$orig.qtrestore.$$"
        CURRENT_TMP=$tmp
        if cp -p -- "$bak" "$tmp" && mv -f -- "$tmp" "$orig"; then
            CURRENT_TMP=""
            log_ok "RESTORED $orig"; (( RESTORED++ ))
        else
            cleanup
            log_error "Could not restore $orig"; (( FAILED++ ))
        fi
    done < "$manifest"

    log_info "Restore summary: restored=$RESTORED failed=$FAILED"
    (( FAILED == 0 )) && exit 0 || exit 1
}

if [[ -n $RESTORE_FROM ]]; then
    do_restore "$RESTORE_FROM"
fi

# --------------------------------------------------- environment validation --
[[ $(uname -s) == Darwin ]] || die 3 "This script only works on macOS (needs otool/install_name_tool)."

typeset tool
for tool in otool install_name_tool file find awk sort cp mv mkdir date; do
    command -v "$tool" >/dev/null 2>&1 || die 3 "Required command not found: $tool (install Xcode Command Line Tools: xcode-select --install)"
done
if (( SIGN && ! DRY_RUN )); then
    command -v codesign >/dev/null 2>&1 || die 3 "codesign not found. Install the Command Line Tools or pass --no-sign."
fi

[[ -d $ROOT ]] || die 3 "Root directory does not exist: $ROOT"
ROOT=${ROOT:A}

# --------------------------------------------------- Qt target discovery -----
qt_dir_is_valid() {
    local d=$1
    [[ -d $d ]] || return 1
    [[ -d $d/QtCore.framework ]] && return 0
    local -a g
    g=("$d"/libQt*Core*.dylib(N))
    (( ${#g} ))
}

find_qt_candidates() {      # $1 = directory to search; fills global CANDS
    local line
    CANDS=()
    [[ -d $1 ]] || return 0
    while IFS= read -r line; do
        [[ -n $line ]] && CANDS+=("$line")
    done < <(find "$1" -maxdepth 10 -type d \( \
                -path '*/site-packages/PyQt5/Qt5/lib' -o \
                -path '*/site-packages/PyQt6/Qt6/lib' -o \
                -path '*/site-packages/PySide6/Qt/lib' -o \
                -path '*/site-packages/PySide2/Qt/lib' \) 2>/dev/null | sort)
}

discover_qt_lib_dir() {
    typeset -a CANDS
    find_qt_candidates "$ROOT"
    if (( ${#CANDS} > 1 )) && [[ -n ${VIRTUAL_ENV-} ]]; then
        local -a narrowed c
        for c in "${CANDS[@]}"; do
            [[ $c == "${VIRTUAL_ENV:A}"/* ]] && narrowed+=("$c")
        done
        (( ${#narrowed} == 1 )) && CANDS=("${narrowed[@]}")
    fi
    if (( ${#CANDS} == 0 )) && [[ -n ${VIRTUAL_ENV-} ]]; then
        find_qt_candidates "${VIRTUAL_ENV:A}"
    fi
    if (( ${#CANDS} == 0 )); then
        die 3 "Could not find a PyQt5/PyQt6/PySide Qt lib directory under $ROOT. Pass --qt-lib-dir DIR."
    elif (( ${#CANDS} > 1 )); then
        log_error "Several Qt lib directories found; refusing to guess:"
        local c
        for c in "${CANDS[@]}"; do print -r -- "         $c" >&2; done
        die 3 "Re-run with --qt-lib-dir DIR (or activate the right venv)."
    fi
    QT_LIB_DIR=${CANDS[1]}
}

if [[ -z $QT_LIB_DIR ]]; then
    log_verbose "Auto-detecting Qt lib directory..."
    discover_qt_lib_dir
fi
[[ -d $QT_LIB_DIR ]] || die 3 "Qt lib directory does not exist: $QT_LIB_DIR"
QT_LIB_DIR=${QT_LIB_DIR:A}
qt_dir_is_valid "$QT_LIB_DIR" || die 3 "Does not look like a Qt lib directory (no QtCore): $QT_LIB_DIR"

# ------------------------------------------------------------ Mach-O helpers -
# Prints "<path>\t<compat-major>" for every dependency line of `otool -L` output.
typeset -r AWK_DEPS='
/^[[:space:]]/ {
    sub(/^[[:space:]]+/, "")
    tag = " (compatibility version "
    idx = index($0, tag)
    if (idx > 0) { p = substr($0, 1, idx - 1); rest = substr($0, idx + length(tag)) }
    else         { p = $0; rest = "" }
    m = rest; sub(/\..*/, "", m)
    if (m !~ /^[0-9]+$/) m = ""
    print p "\t" m
}'

# Compatibility major version of a library's own install-name line. Sets REPLY.
target_major() {
    local f=$1 out line re='compatibility version ([0-9]+)'
    if (( ${+MAJOR_CACHE[$f]} )); then REPLY=${MAJOR_CACHE[$f]}; return 0; fi
    REPLY=""
    if out=$(otool -L "$f" 2>/dev/null); then
        line=${${(f)out}[2]-}
        [[ $line =~ $re ]] && REPLY=${match[1]}
    fi
    MAJOR_CACHE[$f]=$REPLY
    return 0
}

# Is this dependency path a Qt reference that should be repaired?
is_qt_candidate() {
    local p=$1 m
    [[ $p == /* ]] || return 1                          # relative refs stay as they are
    [[ $p == /usr/lib/* || $p == /System/* ]] && return 1
    [[ $p == "$QT_LIB_DIR"/* ]] && return 1             # already points at the target
    [[ $p == */Qt*.framework/Versions/*/Qt* || $p == */libQt*.dylib ]] || return 1
    if (( ${#MATCHES} )); then
        for m in "${MATCHES[@]}"; do
            [[ $p == ${~m} ]] && return 0
        done
        return 1
    fi
    return 0
}

# Map a foreign Qt path to the same library inside QT_LIB_DIR. Sets REPLY.
resolve_target() {
    local old=$1 base fw ver cand
    local -a found
    base=${old##*/}
    REPLY=""
    if [[ $old == */Qt*.framework/Versions/*/* ]]; then
        fw=${${old%/Versions/*}##*/}
        ver=${${old#*/Versions/}%%/*}
        for cand in "$QT_LIB_DIR/$fw/Versions/$ver/$base" "$QT_LIB_DIR/$fw/Versions/Current/$base"; do
            [[ -f $cand ]] && { REPLY=$cand; return 0; }
        done
        found=("$QT_LIB_DIR/$fw"/Versions/*/"$base"(N-.))
        (( ${#found} )) && { REPLY=${found[1]}; return 0; }
    else
        cand="$QT_LIB_DIR/$base"
        [[ -f $cand ]] && { REPLY=$cand; return 0; }
    fi
    return 1
}

# ------------------------------------------------------------------ backups --
ensure_session() {
    [[ -n $SESSION_DIR ]] && return 0
    local stamp
    stamp=$(date +%Y%m%d-%H%M%S)
    SESSION_DIR="$BACKUP_ROOT/$stamp-$$"
    mkdir -p -- "$SESSION_DIR/files" || { SESSION_DIR=""; return 1; }
    : > "$SESSION_DIR/manifest.tsv" || { SESSION_DIR=""; return 1; }
    {
        print -r -- "created=$(date '+%Y-%m-%d %H:%M:%S')"
        print -r -- "root=$ROOT"
        print -r -- "qt_lib_dir=$QT_LIB_DIR"
        print -r -- "tool=$SCRIPT_NAME $SCRIPT_VERSION"
    } > "$SESSION_DIR/info.txt"
    return 0
}

backup_file() {
    local src=$1 dest
    ensure_session || return 1
    dest="$SESSION_DIR/files$src"
    mkdir -p -- "${dest:h}" && cp -p -- "$src" "$dest" || return 1
    print -r -- "$src"$'\t'"$dest" >> "$SESSION_DIR/manifest.tsv"
}

# ------------------------------------------------------------ per-file work --
fail_file() {
    (( FAILED++ ))
    log_error "$1: $2"
}

process_file() {
    local file=$1 rel
    rel=${file#"$ROOT/"}
    (( SCANNED++ ))

    if [[ $file == *$'\t'* || $file == *$'\n'* ]]; then
        log_warn "$rel: skipped (tab or newline in the file name is unsupported)"
        (( SKIPPED++ )); return 0
    fi
    if [[ -L $file || ! -f $file ]]; then
        log_verbose "$rel: skipped (not a regular file)"
        (( SKIPPED++ )); return 0
    fi
    if [[ ! -r $file ]]; then
        fail_file "$rel" "not readable"; return 1
    fi

    local ftype
    ftype=$(file -b "$file" 2>/dev/null) || ftype=""
    if [[ $ftype != *Mach-O* ]]; then
        log_verbose "$rel: skipped (not a Mach-O file)"
        (( NOT_MACHO++ )); return 0
    fi

    local otool_out
    if ! otool_out=$(otool -L "$file" 2>&1); then
        fail_file "$rel" "otool could not read it: ${otool_out//$'\n'/ }"
        return 1
    fi

    local -aU rows
    rows=("${(@f)$(print -r -- "$otool_out" | awk "$AWK_DEPS")}")

    local -a old_refs new_refs problems
    local row old major new nmajor own=""
    # For shared libraries the first entry is the library's own install name.
    if [[ $ftype == *"shared library"* && -n ${rows[1]-} ]]; then
        own=${rows[1]%%$'\t'*}
    fi

    for row in "${rows[@]}"; do
        [[ -n $row ]] || continue
        old=${row%%$'\t'*}
        major=${row#*$'\t'}
        [[ -n $own && $old == "$own" ]] && continue
        is_qt_candidate "$old" || continue

        if ! resolve_target "$old"; then
            problems+=("no matching library in $QT_LIB_DIR for: $old")
            continue
        fi
        new=$REPLY

        if (( ! ALLOW_MAJOR_MISMATCH )) && [[ -n $major ]]; then
            target_major "$new"; nmajor=$REPLY
            if [[ -n $nmajor && $nmajor != "$major" ]]; then
                problems+=("Qt major mismatch (ref is v$major, target is v$nmajor): $old")
                continue
            fi
        fi
        old_refs+=("$old"); new_refs+=("$new")
    done

    if (( ${#problems} )); then
        local p
        (( FAILED++ ))
        log_error "$rel: left untouched, ${#problems} reference(s) cannot be repaired:"
        for p in "${problems[@]}"; do print -r -- "         - $p" >&2; done
        return 1
    fi

    if (( ${#old_refs} == 0 )); then
        log_verbose "$rel: nothing to repair"
        (( UNCHANGED++ )); return 0
    fi

    local i
    if (( DRY_RUN )); then
        log_info "WOULD REPAIR $rel (${#old_refs} reference(s))"
        for (( i = 1; i <= ${#old_refs}; i++ )); do
            log_detail "- ${old_refs[i]}"
            log_detail "+ ${new_refs[i]}"
        done
        (( PENDING++ )); return 0
    fi

    # ---- real repair -------------------------------------------------------
    if [[ ! -d ${file:h} || ! -w ${file:h} ]]; then
        fail_file "$rel" "directory is not writable: ${file:h}"; return 1
    fi
    if ! backup_file "$file"; then
        fail_file "$rel" "could not create backup in ${SESSION_DIR:-$BACKUP_ROOT}; file not modified"
        return 1
    fi

    local tmp="$file.qtfix.$$" out
    CURRENT_TMP=$tmp
    if ! cp -p -- "$file" "$tmp"; then
        cleanup; fail_file "$rel" "could not create temporary copy"; return 1
    fi

    for (( i = 1; i <= ${#old_refs}; i++ )); do
        if ! out=$(install_name_tool -change "${old_refs[i]}" "${new_refs[i]}" "$tmp" 2>&1); then
            cleanup
            fail_file "$rel" "install_name_tool failed (${out//$'\n'/ }). If it says there is no room, relink the library with -headerpad_max_install_names."
            return 1
        fi
    done

    if (( SIGN )); then
        if ! out=$(codesign --force --sign "$SIGN_ID" "$tmp" 2>&1); then
            cleanup
            fail_file "$rel" "codesign failed (${out//$'\n'/ }); original left untouched"
            return 1
        fi
    fi

    # ---- verify the temp copy before it replaces the original --------------
    local verify_out
    if ! verify_out=$(otool -L "$tmp" 2>&1); then
        cleanup; fail_file "$rel" "repaired copy is unreadable by otool; original left untouched"; return 1
    fi
    for (( i = 1; i <= ${#old_refs}; i++ )); do
        if [[ $verify_out == *"${old_refs[i]}"* ]]; then
            cleanup; fail_file "$rel" "verification failed: still references ${old_refs[i]}; original left untouched"; return 1
        fi
        if [[ $verify_out != *"${new_refs[i]}"* ]]; then
            cleanup; fail_file "$rel" "verification failed: new reference missing (${new_refs[i]}); original left untouched"; return 1
        fi
    done
    if (( SIGN )) && ! out=$(codesign --verify "$tmp" 2>&1); then
        cleanup; fail_file "$rel" "signature verification failed (${out//$'\n'/ }); original left untouched"; return 1
    fi

    if ! mv -f -- "$tmp" "$file"; then
        cleanup; fail_file "$rel" "could not replace the original file"; return 1
    fi
    CURRENT_TMP=""

    (( REPAIRED++ ))
    log_ok "REPAIRED $rel (${#old_refs} reference(s))"
    if (( VERBOSE )); then
        for (( i = 1; i <= ${#old_refs}; i++ )); do
            log_detail "- ${old_refs[i]}"
            log_detail "+ ${new_refs[i]}"
        done
    fi
    return 0
}

# ------------------------------------------------------------- discovery -----
discover_files() {          # fills global FILES (unique, sorted)
    typeset -ga FILES
    FILES=()
    local -a prune_args name_args
    local d e f

    prune_args=(-name .git -o -name .hg -o -name node_modules)
    (( INCLUDE_VENV )) || prune_args+=(-o -name .venv -o -name venv)
    prune_args+=(-o -path "$QT_LIB_DIR" -o -path "$BACKUP_ROOT")
    for d in "${EXCLUDES[@]}"; do
        [[ $d == /* ]] || d="$ROOT/$d"
        prune_args+=(-o -path "${d%/}")
    done

    for e in "${EXTS[@]}"; do
        (( ${#name_args} )) && name_args+=(-o)
        name_args+=(-name "*.$e")
    done

    while IFS= read -r -d '' f; do
        FILES+=("$f")
    done < <(find "$ROOT" \( "${prune_args[@]}" \) -prune -o -type f \( "${name_args[@]}" \) -print0)

    for f in "${EXTRA_FILES[@]}"; do
        if [[ -e $f ]]; then
            FILES+=("${f:A}")
        else
            log_warn "--file does not exist, ignored: $f"
        fi
    done
    FILES=("${(@ou)FILES}")
}

# ------------------------------------------------------------------ main -----
log_info "Root:     $ROOT"
log_info "Qt libs:  $QT_LIB_DIR"
(( ${#MATCHES} )) && log_info "Filter:   ${(j:, :)MATCHES}"
if (( CHECK )); then
    log_info "Mode:     check (read-only, exit 1 if anything needs repair)"
elif (( DRY_RUN )); then
    log_warn "DRY-RUN: no files will be modified."
fi
if (( ! SIGN && ! DRY_RUN )); then
    log_warn "--no-sign: modified binaries will carry an invalid signature; arm64 macOS may refuse to run them."
fi

discover_files
log_info "Scanning ${#FILES} candidate file(s)..."

typeset f
for f in "${FILES[@]}"; do
    process_file "$f"
done

# --------------------------------------------------------------- summary -----
typeset summary
if (( DRY_RUN )); then
    summary="scanned=$SCANNED would_repair=$PENDING already_ok=$UNCHANGED not_macho=$NOT_MACHO skipped=$SKIPPED failed=$FAILED"
else
    summary="scanned=$SCANNED repaired=$REPAIRED already_ok=$UNCHANGED not_macho=$NOT_MACHO skipped=$SKIPPED failed=$FAILED"
fi
print -r -- ""
print -r -- "=========== SUMMARY ==========="
print -r -- "$summary"
if [[ -n $SESSION_DIR ]]; then
    print -r -- "Backups:   $SESSION_DIR"
    print -r -- "Undo with: $SCRIPT_NAME --restore ${(q)SESSION_DIR}"
fi
print -r -- "==============================="
log_file "SUMMARY $summary"

(( FAILED == 0 )) || { log_error "Finished with $FAILED failed file(s)."; exit 1; }
if (( CHECK && PENDING > 0 )); then
    log_warn "$PENDING file(s) still reference a different Qt."
    exit 1
fi
(( DRY_RUN )) && log_info "Dry run complete." || log_ok "Done."
exit 0
