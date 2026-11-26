package main

import "strconv"

// useBrowser says whether Apollo's page opens in the system browser. On Windows Apollo is a desktop application
// with its own WebView2 window and never opens a browser; Linux and macOS serve the page and open the browser.
func useBrowser(goos string) bool { return goos != "windows" }

// instanceNames are the names of the Windows objects that make Apollo a single instance for one port: a mutex the
// running instance holds, and an event a second launch sets to ask the first to bring its window to the front.
func instanceNames(port int) (mutex, event string) {
	p := strconv.Itoa(port)
	return `Local\Apollo.instance.` + p, `Local\Apollo.show.` + p
}

// Texts of the native message boxes.
const (
	boxTitle        = "Apollo"
	runtimeMissing  = "Apollo needs the Microsoft Edge WebView2 Runtime, and this PC does not have it.\n\nInstall the Evergreen runtime from https://developer.microsoft.com/microsoft-edge/webview2/ and start Apollo again."
)
