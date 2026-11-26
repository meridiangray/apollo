# Third-party Go modules

Apollo's own code uses the Go standard library. Three modules are added for the Windows desktop window, and they are
pinned by `go.mod` and `go.sum`. Nothing is vendored: the build downloads them from the module proxy, checked against
`go.sum`.
They are only compiled into the Windows build (build tags); Linux and macOS builds do not contain them.

| Module | Version | Licence | Used for |
|---|---|---|---|
| `github.com/jchv/go-webview2` | v0.0.0-20260205173254-56598839c808 | MIT | The native WebView2 window. Pure Go, no cgo; embeds the WebView2 loader. |
| `github.com/jchv/go-winloader` | v0.0.0-20250406163304-c1995be93bd1 | ISC | Loads the embedded WebView2 loader DLL from memory (a dependency of go-webview2). |
| `golang.org/x/sys` | v0.50.0 | BSD-3-Clause | Windows API calls: mutex, event, message box. |

The icon and version information are made by a separate tool, `go/icon/` (its own module; it is
not part of the Apollo binary):

| Module | Version | Licence | Used for |
|---|---|---|---|
| `github.com/tc-hib/winres` | v0.2.1 | ISC | Writes `rsrc_windows_amd64.syso`: icon, version info, manifest. |
| `github.com/nfnt/resize` | v0.0.0-20180221191011-83c6a9932646 | ISC | Dependency of winres. |
| `golang.org/x/image` | v0.12.0 | BSD-3-Clause | Dependency of winres. |

## Refreshing

    cd go
    go get <module>@<version>
    GOOS=windows go mod tidy

`go mod tidy` must run with `GOOS=windows`, since the window code is Windows-only. Redo the same inside `go/icon/`
for the icon tool.

## Page libraries (`ui/vendor/`)

Served from the embedded UI by name in `server.go`; the page loads no script from anywhere else.

| File | Version | Licence | Used for |
|---|---|---|---|
| `vendor/gsap-3.13.0/gsap.min.js` | GSAP 3.13.0 | GreenSock standard licence (free for this use) | The page's animations: the route, arrivals, the heatmap, Ready |
| `vendor/gsap-3.13.0/DrawSVGPlugin.min.js` | GSAP 3.13.0 | GreenSock standard licence | The Flow tab's route draws itself in |
| `vendor/gsap-3.13.0/Flip.min.js` | GSAP 3.13.0 | GreenSock standard licence | The Found, Collections and Sources lists glide when filtered |

Copied from `hydra-docs/docs/plans/designs/vendor/gsap-3.13.0/` (the motion showcase's copy), unchanged.
