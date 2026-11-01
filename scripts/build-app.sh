#!/bin/zsh
set -e
ROOT="${OPENSHOT_ROOT:-$HOME/openshot-research}"
cd "$ROOT/openshot-qt"
export DYLD_LIBRARY_PATH="$ROOT/install/lib:$ROOT/libopenshot/build/src:$ROOT/libopenshot-audio/build/src"
export PYTHONPATH="$ROOT/libopenshot/build/bindings/python"
rm -rf openshot_qt build src/OpenShot_Video_Editor.egg-info
.venv/bin/python freeze.py bdist_mac \
  --qt-menu-nib="$PWD/.venv/lib/python3.13/site-packages/PyQt5/Qt5/plugins/platforms/" \
  --iconfile=installer/openshot.icns \
  --custom-info-plist=installer/Info.plist \
  --bundle-name="OpenShot Video Editor"
echo "Built: $PWD/build/OpenShot Video Editor.app (run with DYLD_LIBRARY_PATH/PYTHONPATH unset)"
