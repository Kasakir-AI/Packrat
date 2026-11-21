#!/bin/bash
set -euo pipefail

check_only=false
if [[ ${1:-} == --check ]]; then
    check_only=true
    shift
fi
if [[ $# != 1 ]]; then
    echo "Usage: $0 [--check] PATH_TO_DMG" >&2
    exit 2
fi
output="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
cd "$(dirname "$0")/.."

scratch=$(mktemp -d "${TMPDIR:-/tmp}/packrat-dmg.XXXXXXXX")
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/stage"
app="$scratch/stage/Packrat.app"

if "$check_only"; then
    ditto app/dist/Packrat.app "$app"
    codesign --verify --deep --strict --all-architectures "$app"
else
    source scripts/macos-notary.sh
    export MACOS_SIGN_IDENTITY
    (cd app && "${PACKRAT_PYTHON:-python3}" -m PyInstaller --noconfirm --distpath "$scratch/dist" --workpath "$scratch/build" packrat.spec)
    ditto "$scratch/dist/Packrat.app" "$app"
    framework="$app/Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework"
    helper="$framework/Versions/Current/Helpers/QtWebEngineProcess.app"
    sign=(codesign --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY")
    "${sign[@]}" --entitlements "$helper/Contents/Resources/QtWebEngineProcess.entitlements" "$helper"
    "${sign[@]}" "$framework" "$app"
    "$app/Contents/MacOS/packrat-cli" --version
    notarize_app "$app" "$scratch/notary.zip"
fi

ln -s /Applications "$scratch/stage/Applications"
dmg="$scratch/Packrat-macOS.dmg"
hdiutil create -quiet -volname Packrat -srcfolder "$scratch/stage" -fs HFS+ -format UDZO "$dmg"
if ! "$check_only"; then
    notarize_dmg "$dmg"
fi
hdiutil verify -quiet "$dmg"
mv "$dmg" "$output"
