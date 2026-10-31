#!/bin/bash
# Builds "Strike List.app" and a .dmg for macOS. Run on a Mac from the StrikeList folder:
#     bash build/build_mac.sh
# Output: build/release/StrikeList-<version>-mac.dmg   (Apple Silicon build when run on an M-series Mac)
set -e
cd "$(dirname "$0")/.."
ROOT="$PWD"
VER=$(grep -m1 '^VERSION' strikelist.py | cut -d'"' -f2)
echo "Building Strike List $VER for macOS…"

python3 -m venv build/.venv
build/.venv/bin/pip install -q --upgrade pip
build/.venv/bin/pip install -q -r requirements.txt pyinstaller

# App icon (.icns) from assets/logo.png
ICON=build/work/StrikeList.iconset; rm -rf "$ICON"; mkdir -p "$ICON"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/logo.png --out "$ICON/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/logo.png --out "$ICON/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICON" -o build/work/StrikeList.icns

build/.venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "Strike List" --icon "$ROOT/build/work/StrikeList.icns" \
  --osx-bundle-identifier com.strikelist.app \
  --add-data "$ROOT/assets:assets" \
  --distpath build/dist --workpath build/work/pyi --specpath build/work \
  strikelist.py

# Source code zip (GPL: the source goes with the app)
SRC="build/work/StrikeList-$VER-source.zip"; rm -f "$SRC"
zip -qr "$SRC" core.py strikelist.py requirements.txt README.txt LICENSE.txt THIRD-PARTY-NOTICES.txt \
  "Start Strike List.command" "Start Strike List.bat" assets yata_awards build/build_mac.sh build/build_windows.bat \
  -x '*/__pycache__/*'

# Disk image: app + Applications shortcut + licence + source
STAGE=build/work/dmg; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "build/dist/Strike List.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp LICENSE.txt THIRD-PARTY-NOTICES.txt "$SRC" "$STAGE/"
cp build/HOW-TO-OPEN-MAC.txt "$STAGE/How to open.txt"
mkdir -p build/release
hdiutil create -quiet -volname "Strike List $VER" -srcfolder "$STAGE" -ov -format UDZO "build/release/StrikeList-$VER-mac.dmg"
cp "$SRC" build/release/
rm -rf build/work build/dist
echo "Done: build/release/StrikeList-$VER-mac.dmg"
