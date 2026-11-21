#!/bin/bash
set -euo pipefail

: "${MACOS_SIGN_IDENTITY:?Set MACOS_SIGN_IDENTITY to your Developer ID Application certificate fingerprint.}"
: "${MACOS_NOTARY_PROFILE:?Set MACOS_NOTARY_PROFILE to your notarytool Keychain profile.}"
if [[ ! $MACOS_SIGN_IDENTITY =~ ^[A-F0-9]{40}$ ]] ||
   ! security find-identity -v -p codesigning | awk -v id="$MACOS_SIGN_IDENTITY" '
      $2 == id && /"Developer ID Application: / { found = 1 } END { exit !found }
   '; then
    echo "The selected Developer ID Application signing identity is unavailable." >&2
    exit 1
fi

verify_signature() {
    codesign --verify --deep --strict --all-architectures \
        -R "=anchor apple generic and certificate leaf = H\"$MACOS_SIGN_IDENTITY\"" "$1"
}

notarize() {
    local status
    status=$(xcrun notarytool submit "$1" --keychain-profile "$MACOS_NOTARY_PROFILE" --wait --output-format json)
    if [[ $(printf '%s' "$status" | plutil -extract status raw -o - -) != Accepted ]]; then
        printf '%s\n' "$status" >&2
        echo "Get Apple's diagnostic log with notarytool log and the submission ID above." >&2
        return 1
    fi
}

notarize_app() {
    local app=$1 archive=$2
    verify_signature "$app"
    ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
    notarize "$archive"
    xcrun stapler staple "$app"
    xcrun stapler validate "$app"
    spctl --assess --type execute --verbose "$app"
}

notarize_dmg() {
    local dmg=$1
    codesign --sign "$MACOS_SIGN_IDENTITY" --timestamp "$dmg"
    verify_signature "$dmg"
    notarize "$dmg"
    xcrun stapler staple "$dmg"
    xcrun stapler validate "$dmg"
    spctl --assess --type open --context context:primary-signature --verbose "$dmg"
}
