#!/bin/sh
# Run Playwright's WebKit bundle with privately extracted compatibility libraries.
set -eu

: "${DIAMOND_LP8_WEBKIT_BUNDLE:?set the Playwright minibrowser-wpe directory}"
: "${DIAMOND_LP8_WEBKIT_DEPS:?set the extracted compatibility library directory}"

export WEBKIT_EXEC_PATH="${DIAMOND_LP8_WEBKIT_BUNDLE}/bin"
export WEBKIT_INJECTED_BUNDLE_PATH="${DIAMOND_LP8_WEBKIT_BUNDLE}/lib"
export WEBKIT_INSPECTOR_RESOURCES_PATH="${DIAMOND_LP8_WEBKIT_BUNDLE}/share"
export LD_LIBRARY_PATH="${DIAMOND_LP8_WEBKIT_DEPS}:${DIAMOND_LP8_WEBKIT_BUNDLE}/lib:${DIAMOND_LP8_WEBKIT_BUNDLE}/sys/lib"
exec "${DIAMOND_LP8_WEBKIT_BUNDLE}/bin/MiniBrowser" "$@"
