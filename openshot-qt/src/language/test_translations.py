#!/usr/bin/python3
"""
 @file
 @brief This file verifies all translations are correctly formatted and have the correct # of string replacements
 @author Jonathan Thomas <jonathan@openshot.org>

 @section LICENSE

 Copyright (c) 2008-2018 OpenShot Studios, LLC
 (http://www.openshotstudios.com). This file is part of
 OpenShot Video Editor (http://www.openshot.org), an open-source project
 dedicated to delivering high quality video editing and animation solutions
 to the world.

 OpenShot Video Editor is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 OpenShot Video Editor is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with OpenShot Library.  If not, see <http://www.gnu.org/licenses/>.
 """

import argparse
import ast
import shutil
import os
import re
import fnmatch
import subprocess
import sys
from collections import Counter
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

SRC_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from qt_api import QCoreApplication, QTranslator


# Absolute path of the translations directory
LANG_PATH = os.path.dirname(os.path.abspath(__file__))
REPO_PATH = os.path.dirname(os.path.dirname(LANG_PATH))
DOC_LOCALE_PATH = os.path.join(REPO_PATH, 'doc', 'locale')

# Match '%(name)x' format placeholders
TAG_RE = re.compile(r'%\(([^\)]*)\)(.)')
DOC_SUB_RE = re.compile(r'\|[A-Za-z0-9_]+\|')
PRINTF_TOKEN_RE = re.compile(
    r'%(?:'
    r'%'  # escaped percent literal
    r'|\(([^)]+)\)[#0\-+]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]'  # named placeholder
    r'|[#0\-+]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]'  # positional/unnamed placeholder
    r')'
)

# Match numbers in strings (for detecting number-embedded strings like "Line 1")
NUMBER_RE = re.compile(r'\d+')


class BadTranslationsError(Exception):
    pass


class POEntry:
    """Minimal PO entry representation for doc validation."""

    def __init__(self) -> None:
        self.msgid = ""
        self.msgid_plural = ""
        self.msgstrs: Dict[int, str] = {}
        self.references: List[str] = []


def get_doc_locale_from_path(path: str) -> Optional[str]:
    """Extract the doc locale code from a PO file path."""
    parts = os.path.normpath(path).split(os.sep)
    try:
        locale_index = parts.index('locale')
    except ValueError:
        return None
    if locale_index + 1 >= len(parts):
        return None
    return parts[locale_index + 1]


class Color:
    """Color for message output"""
    _red = '\u001b[31m'
    _yellow = '\u001b[33m'
    _green = '\u001b[32m'
    _reset = '\u001b[0m'

    @classmethod
    def red(cls, *args: Optional[Any]) -> str:
        return cls._red + str(*args) + cls._reset

    @classmethod
    def yellow(cls, *args: Optional[Any]) -> str:
        return cls._yellow + str(*args) + cls._reset

    @classmethod
    def green(cls, *args: Optional[Any]) -> str:
        return cls._green + str(*args) + cls._reset


def get_app() -> QCoreApplication:
    """Create the translation app lazily so test discovery doesn't lock in QCoreApplication."""
    app = QCoreApplication.instance()
    if app is not None:
        return app
    return QCoreApplication(sys.argv)


def build_stringlists() -> Dict[str, List]:
    """ Create a dict containing lists of strings, keyed on source filename"""
    all_strings = {}

    for pot_file in [
        'OpenShot.pot',
        'OpenShot_transitions.pot',
        'OpenShot_blender.pot',
        'OpenShot_emojis.pot',
    ]:
        with open(os.path.join(LANG_PATH, 'OpenShot', pot_file)) as f:
            data = f.read()
        all_strings.update({
            pot_file: re.findall('^msgid \"(.*)\"', data, re.MULTILINE)
        })
    return all_strings


def check_trans(strings: List) -> List[Tuple[str, str]]:
    """Check all strings in a list against a given .qm file"""
    app = get_app()
    # Test translation of all strings
    translations = {
        source: app.translate("", source)
        for source in strings
    }
    # Check for replacements with mismatched number of % escapes
    errors = {}
    for s, t in translations.items():
        # Count placeholders in source and translation
        s_count = s.count('%s') + s.count('%d') + s.count('%f')
        t_count = t.count('%s') + t.count('%d') + t.count('%f')

        # If counts match, no error
        if s.count('%s') == t.count('%s') and \
           s.count('%d') == t.count('%d') and \
           s.count('%f') == t.count('%f'):
            continue

        # Special case: source has embedded numbers but no placeholders,
        # and translation has %s placeholders instead.
        # This is valid when translators use a placeholder pattern
        # (e.g., "Line 1" translated as "Linia %s" where %s = "1")
        source_numbers = NUMBER_RE.findall(s)
        if source_numbers and s_count == 0 and t_count == len(source_numbers):
            continue

        errors[s] = t
    # Check for missing/added variable names
    # e.g.: "%(clip_id)s %(value)d" changed to "%(clip)s %(value)d"
    # or mismatched types
    # e.g.: "%(seconds)s" changed to "%(seconds)d"
    named_variables = {
        s: (TAG_RE.findall(s), TAG_RE.findall(t))
        for s, t in translations.items()
        if s.count('%(') > 0
    }
    errors.update({
        s: translations[s]
        for s, (s_vars, t_vars) in named_variables.items()
        # Ensure that t_vars is a strict subset of s_vars
        if not set(t_vars) <= set(s_vars)
    })
    return list(errors.items())


def process_qm(file: str, stringlists: Dict[str, List[str]]) -> int:
    """Scan a translation file against all provided strings"""
    app = get_app()
    # Attempt to load translation file
    basename = os.path.splitext(file)[0]
    translator = QTranslator(app)
    if not translator.load(basename, LANG_PATH):
        print(Color.red('QTranslator failed to load') + f' {file}')
        return 1

    app.installTranslator(translator)

    # Build a dict mapping source POTfiles to lists of error pairs
    error_sets = {
        sourcefile: check_trans(strings)
        for sourcefile, strings in stringlists.items()
    }

    app.removeTranslator(translator)

    # Display any errors found, grouped by source POT file
    error_count = sum([len(v) for v in error_sets.values()])
    invalid_msg = "Invalid"
    if error_count:
        print(f'{file}: ' + Color.red(f'{error_count} total errors'))
    for pot, errset in error_sets.items():
        if not errset:
            continue
        width = len(pot)
        for source, trans in errset:
            print(Color.yellow(f'{pot}:') + f' {source}')
            print(Color.red(f'{invalid_msg:>{width}}:') + f' {trans}\n')
    return error_count


def scan_all_qm(filenames: List[str] = None) -> None:
    all_strings = build_stringlists()
    if not filenames:
        filenames = fnmatch.filter(os.listdir(LANG_PATH), 'OpenShot*.qm')
    # Loop through language files and count errors
    total_errors = sum([
        process_qm(filename, all_strings)
        for filename in filenames
    ])

    string_count = sum([len(s) for s in all_strings.values()])
    lang_count = len(filenames)

    print(f"Tested {Color.yellow(string_count)} strings on "
          + f"{Color.yellow(lang_count)} translation files.")
    if total_errors > 0:
        raise BadTranslationsError(f"Found {total_errors} translation errors")


def po_unquote(text: str) -> str:
    """Decode a quoted PO string token."""
    return ast.literal_eval(text)


def extract_percent_tokens(text: str) -> Tuple[Counter, Counter]:
    """Extract printf-style percent tokens."""
    tokens = Counter()
    named_tokens = Counter()

    for match in PRINTF_TOKEN_RE.finditer(text):
        token = match.group(0)
        tokens[token] += 1
        named_match = TAG_RE.match(token)
        if named_match:
            named_tokens[named_match.groups()] += 1

    return tokens, named_tokens


def extract_doc_substitutions(text: str) -> Counter:
    """Extract Sphinx substitution tokens like |icon_name|."""
    return Counter(DOC_SUB_RE.findall(text))


def flush_po_entry(entries: List[POEntry], entry: POEntry) -> POEntry:
    """Append the current PO entry if it contains any data, then start a new one."""
    if entry.msgid or entry.msgstrs or entry.msgid_plural or entry.references:
        entries.append(entry)
    return POEntry()


def parse_po_file(path: str) -> List[POEntry]:
    """Parse enough of a PO file to inspect msgid/msgstr tokens."""
    entries: List[POEntry] = []
    entry = POEntry()
    active_field: Optional[Tuple[str, Optional[int]]] = None

    with open(path, encoding='utf-8') as handle:
        for raw_line in handle:
            line = raw_line.rstrip('\n')

            if not line.strip():
                entry = flush_po_entry(entries, entry)
                active_field = None
                continue

            if line.startswith('#~'):
                continue

            if line.startswith('#:'):
                entry.references.extend(line[2:].strip().split())
                continue

            if line.startswith('#'):
                continue

            if line.startswith('msgid_plural '):
                entry.msgid_plural = po_unquote(line[len('msgid_plural '):])
                active_field = ('msgid_plural', None)
                continue

            if line.startswith('msgid '):
                if entry.msgid or entry.msgstrs or entry.msgid_plural:
                    entry = flush_po_entry(entries, entry)
                entry.msgid = po_unquote(line[len('msgid '):])
                active_field = ('msgid', None)
                continue

            if line.startswith('msgstr['):
                index_text, value = line.split(']', 1)
                index = int(index_text[len('msgstr['):])
                entry.msgstrs[index] = po_unquote(value.strip()[1:])
                active_field = ('msgstr', index)
                continue

            if line.startswith('msgstr '):
                entry.msgstrs[0] = po_unquote(line[len('msgstr '):])
                active_field = ('msgstr', 0)
                continue

            if line.startswith('msgctxt '):
                active_field = ('msgctxt', None)
                continue

            if line.startswith('"'):
                value = po_unquote(line)
                if active_field == ('msgid', None):
                    entry.msgid += value
                elif active_field == ('msgid_plural', None):
                    entry.msgid_plural += value
                elif active_field and active_field[0] == 'msgstr' and active_field[1] is not None:
                    entry.msgstrs[active_field[1]] = entry.msgstrs.get(active_field[1], '') + value
                continue

    flush_po_entry(entries, entry)
    return entries


def validate_doc_entry(path: str, entry: POEntry) -> List[str]:
    """Check doc translation entries for placeholder and substitution preservation."""
    if entry.msgid == "":
        header = entry.msgstrs.get(0, '')
        header_fields: Dict[str, str] = {}
        for line in header.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            header_fields[key.strip()] = value.strip()

        expected_language = get_doc_locale_from_path(path)
        actual_language = header_fields.get('Language', '')
        if not expected_language:
            return []

        errors = []
        if not actual_language:
            errors.append(f"{path}: PO header is missing a Language value")
        elif actual_language != expected_language:
            errors.append(
                f"{path}: PO header Language '{actual_language}' does not match locale '{expected_language}'"
            )
        return errors

    source_strings = [entry.msgid]
    if entry.msgid_plural:
        source_strings.append(entry.msgid_plural)

    expected_named = Counter()
    expected_tokens = Counter()
    expected_subs = Counter()
    for source in source_strings:
        source_tokens, source_named = extract_percent_tokens(source)
        expected_named |= source_named
        expected_tokens |= source_tokens
        expected_subs |= extract_doc_substitutions(source)

    errors = []
    for plural_index, translation in sorted(entry.msgstrs.items()):
        if not translation.strip():
            continue

        actual_tokens, actual_named = extract_percent_tokens(translation)
        actual_subs = extract_doc_substitutions(translation)

        label = path
        if entry.references:
            label += f" ({', '.join(entry.references[:2])})"
        if len(entry.msgstrs) > 1:
            label += f" [msgstr[{plural_index}]]"

        if actual_named != expected_named:
            errors.append(
                f"{label}: named placeholders differ\n"
                f"  msgid: {entry.msgid}\n"
                f"  msgstr: {translation}"
            )

        if actual_tokens != expected_tokens:
            errors.append(
                f"{label}: printf tokens differ\n"
                f"  msgid: {entry.msgid}\n"
                f"  msgstr: {translation}"
            )

        if actual_subs != expected_subs:
            errors.append(
                f"{label}: Sphinx substitution tokens differ\n"
                f"  msgid: {entry.msgid}\n"
                f"  msgstr: {translation}"
            )

    return errors


def process_doc_po(path: str) -> int:
    """Run syntax and token-preservation checks on a doc PO file."""
    errors: List[str] = []
    msgfmt_path = shutil.which('msgfmt')
    trusted_path = os.path.abspath(path)
    trusted_roots = (os.path.abspath(LANG_PATH), os.path.abspath(DOC_LOCALE_PATH))

    if not msgfmt_path:
        errors.append(f"{path}: msgfmt executable not found")
        return len(errors)

    if not any(
        os.path.commonpath([trusted_path, root]) == root
        for root in trusted_roots
    ):
        errors.append(f"{path}: path is outside trusted locale roots")
        return len(errors)

    msgfmt_args = [
        msgfmt_path,
        '--check',
        '--check-format',
        '--output-file=/dev/null',
        trusted_path,
    ]
    msgfmt = subprocess.run(
        msgfmt_args,
        capture_output=True,
        text=True,
        check=False,
    )
    if msgfmt.returncode != 0:
        stderr = msgfmt.stderr.strip() or msgfmt.stdout.strip() or 'msgfmt validation failed'
        errors.append(f"{path}: {stderr}")

    try:
        entries = parse_po_file(path)
    except Exception as ex:
        errors.append(f"{path}: PO parse failed: {ex}")
        entries = []

    for entry in entries:
        errors.extend(validate_doc_entry(path, entry))

    if errors:
        print(Color.red(f'{path}: {len(errors)} total errors'))
        for error in errors:
            print(error + '\n')
    return len(errors)


def scan_doc_po_files() -> None:
    """Validate all documentation PO files."""
    po_files = []
    for root, _, files in os.walk(DOC_LOCALE_PATH):
        for filename in files:
            if filename.endswith('.po'):
                po_files.append(os.path.join(root, filename))
    po_files.sort()

    total_errors = sum(process_doc_po(path) for path in po_files)
    print(f"Tested {Color.yellow(len(po_files))} documentation PO files.")
    if total_errors > 0:
        raise BadTranslationsError(f"Found {total_errors} documentation translation errors")


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse CLI arguments without breaking the existing filename-only behavior."""
    parser = argparse.ArgumentParser(description='Validate OpenShot translation files.')
    parser.add_argument('filenames', nargs='*', help='Optional .qm files to validate')
    parser.add_argument('--docs', action='store_true', help='Validate doc gettext PO files')
    parser.add_argument('--all', action='store_true', help='Validate both app and doc translations')
    return parser.parse_args(argv)


# Autorun if used as script
if __name__ == '__main__':
    try:
        args = parse_args(sys.argv[1:])
        if args.all:
            scan_all_qm(args.filenames)
            scan_doc_po_files()
        elif args.docs:
            scan_doc_po_files()
        else:
            scan_all_qm(args.filenames)
    except BadTranslationsError as ex:
        print(Color.red(str(ex) + "! See above."))
        exit(1)
    else:
        print(Color.green("No errors found."))
        exit(0)
