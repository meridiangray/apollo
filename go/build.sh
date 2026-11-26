#!/bin/sh
# Build Apollo for Windows, Linux and macOS from WSL: no cgo. Go fetches the pinned libraries (go-webview2,
# go-winloader, x/sys; see THIRD_PARTY.md) from go.mod/go.sum when it builds; nothing is vendored.
#   sh build.sh            vet (Windows too), test, then build every binary into ../bin/ (git ignores it): bin/Apollo.exe,
#                          the one to double-click; bin/apollo for Linux; bin/apollo-darwin-* for macOS
# GO names the go binary (default: the toolchain in ~/toolchains/go).
# To redraw the icon or change the version information: cd icon && go run . -syso ../rsrc_windows_amd64.syso
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
GO=${GO:-$HOME/toolchains/go/bin/go}
OUT=$HERE/../bin
export GOTOOLCHAIN=local GOFLAGS="-mod=readonly -trimpath" CGO_ENABLED=0
mkdir -p "$OUT"
cd "$HERE"
"$GO" version
"$GO" vet ./...
GOOS=windows GOARCH=amd64 "$GO" vet ./...
"$GO" test -count=1 ./...
build() {   # GOOS GOARCH extra-ldflags name
	GOOS=$1 GOARCH=$2 "$GO" build -ldflags "-s -w $3" -o "$OUT/$4" .
}
build linux amd64 "" apollo
build windows amd64 "-H windowsgui" Apollo.exe.new                # embeds rsrc_windows_amd64.syso: icon, version info
build darwin arm64 "" apollo-darwin-arm64
build darwin amd64 "" apollo-darwin-amd64
# a running Apollo.exe cannot be replaced: the new one waits as Apollo.exe.new until Apollo is closed and this runs again
mv -f "$OUT/Apollo.exe.new" "$OUT/Apollo.exe" 2>/dev/null || echo "NOTE: Apollo is open: the new build is bin/Apollo.exe.new; close Apollo and move it over bin/Apollo.exe"
chmod +x "$OUT/apollo" "$OUT"/apollo-darwin-*
ls -l "$OUT"
