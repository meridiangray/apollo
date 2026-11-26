//go:build windows

package main

import (
	"context"
	"errors"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"unsafe"

	"github.com/jchv/go-webview2"
	"github.com/jchv/go-webview2/webviewloader"
	"golang.org/x/sys/windows"
)

var errNoWindow = errors.New("the WebView2 runtime is missing")

var (
	user32            = windows.NewLazySystemDLL("user32.dll")
	procSysMetrics    = user32.NewProc("GetSystemMetrics")
	procDpiForSystem  = user32.NewProc("GetDpiForSystem")
	procForeground    = user32.NewProc("SetForegroundWindow")
	procShowWindow    = user32.NewProc("ShowWindow")
	procIsIconic      = user32.NewProc("IsIconic")
	procAllowSetFront = user32.NewProc("AllowSetForegroundWindow")
)

const (
	winWidth, winHeight = 1280, 820 // at 96 dpi
	iconID              = 1         // the resource id rsrc_windows_amd64.syso gives the icon
	swRestore           = 9
	smCxScreen          = 0
	smCyScreen          = 1
	asfwAny             = ^uintptr(0) // (DWORD)-1
)

func init() {
	// The window and its message loop must stay on the main thread.
	runtime.LockOSThread()
}

// fatal tells the owner in a message box (there is no console), logs, and exits.
func fatal(msg string) {
	log.Print(msg)
	box(msg)
	os.Exit(1)
}

func box(msg string) {
	t, _ := windows.UTF16PtrFromString(boxTitle)
	m, _ := windows.UTF16PtrFromString(msg)
	windows.MessageBox(0, m, t, windows.MB_OK|windows.MB_ICONERROR)
}

var instanceMutex windows.Handle

// takeInstance claims the named mutex; false means another Apollo already holds it.
func takeInstance(port int) bool {
	name, _ := instanceNames(port)
	n, _ := windows.UTF16PtrFromString(name)
	h, err := windows.CreateMutex(nil, false, n)
	if err == windows.ERROR_ALREADY_EXISTS {
		windows.CloseHandle(h)
		return false
	}
	if err != nil {
		log.Printf("apollo: mutex: %v (going on without single-instance)", err)
		return true
	}
	instanceMutex = h
	return true
}

// showRunning asks the running Apollo to bring its window to the front: it lets that process take the foreground,
// then sets the event it waits on.
func showRunning(port int) {
	_, name := instanceNames(port)
	n, _ := windows.UTF16PtrFromString(name)
	ev, err := windows.OpenEvent(windows.EVENT_MODIFY_STATE, false, n)
	if err != nil {
		log.Printf("apollo: no window to show: %v", err)
		return
	}
	defer windows.CloseHandle(ev)
	procAllowSetFront.Call(asfwAny)
	windows.SetEvent(ev)
}

// bringToFront restores and raises the window.
func bringToFront(hwnd uintptr) {
	if r, _, _ := procIsIconic.Call(hwnd); r != 0 {
		procShowWindow.Call(hwnd, swRestore)
	}
	procForeground.Call(hwnd)
}

// listenForShow raises the window each time a second launch sets the event, until ctx ends.
func listenForShow(ctx context.Context, port int, hwnd uintptr) {
	_, name := instanceNames(port)
	n, _ := windows.UTF16PtrFromString(name)
	ev, err := windows.CreateEvent(nil, 0, 0, n) // auto-reset
	if err != nil {
		log.Printf("apollo: show event: %v", err)
		return
	}
	defer windows.CloseHandle(ev)
	for ctx.Err() == nil {
		if r, _ := windows.WaitForSingleObject(ev, 500); r == windows.WAIT_OBJECT_0 {
			bringToFront(hwnd)
		}
	}
}

// ---- the title bar follows the page's theme --------------------------------------------------------------------------
// Windows 11 takes a caption colour and a caption text colour (DWMWA_CAPTION_COLOR 35, DWMWA_TEXT_COLOR 36); Windows 10
// and 11 take the dark flag (DWMWA_USE_IMMERSIVE_DARK_MODE 20). A system that refuses an attribute keeps its own bar.

var (
	dwmapi      = windows.NewLazySystemDLL("dwmapi.dll")
	procDwmSet  = dwmapi.NewProc("DwmSetWindowAttribute")
	lookMu      sync.Mutex
	lookHwnd    uintptr
	lookPending *Look
)

const (
	dwmDarkMode     = 20
	dwmCaptionColor = 35
	dwmTextColor    = 36
)

// colorref turns #rrggbb into the 0x00BBGGRR a COLORREF holds.
func colorref(hex string) (uint32, bool) {
	if len(hex) != 7 || hex[0] != '#' {
		return 0, false
	}
	v, err := strconv.ParseUint(hex[1:], 16, 32)
	if err != nil {
		return 0, false
	}
	r, g, b := uint32(v>>16&0xff), uint32(v>>8&0xff), uint32(v&0xff)
	return b<<16 | g<<8 | r, true
}

func dwmSet(hwnd uintptr, attr uintptr, value uint32) {
	defer func() { recover() }() // DWM refusing is never a reason to fail
	procDwmSet.Call(hwnd, attr, uintptr(unsafe.Pointer(&value)), unsafe.Sizeof(value))
}

func paintLook(hwnd uintptr, l Look) {
	dark := uint32(0)
	if l.Dark {
		dark = 1
	}
	dwmSet(hwnd, dwmDarkMode, dark)
	paintIcon(hwnd, l)
	if c, ok := colorref(l.Caption); ok {
		dwmSet(hwnd, dwmCaptionColor, c)
	}
	if c, ok := colorref(l.Text); ok {
		dwmSet(hwnd, dwmTextColor, c)
	}
}

// applyLook paints the bar now, or when the window exists.
func applyLook(l Look) {
	lookMu.Lock()
	defer lookMu.Unlock()
	lookPending = &l
	if lookHwnd != 0 {
		paintLook(lookHwnd, l)
	}
}

func windowReady(hwnd uintptr) {
	lookMu.Lock()
	defer lookMu.Unlock()
	lookHwnd = hwnd
	if lookPending != nil {
		paintLook(hwnd, *lookPending)
	}
}

func metric(i uintptr) int { r, _, _ := procSysMetrics.Call(i); return int(r) }

// windowSize is 1280x820 at 96 dpi, scaled to the system dpi and kept within the screen.
func windowSize() (w, h uint) {
	dpi := 96
	if r, _, _ := procDpiForSystem.Call(); r != 0 {
		dpi = int(r)
	}
	ww, hh := winWidth*dpi/96, winHeight*dpi/96
	if sw := metric(smCxScreen) * 9 / 10; sw > 0 && ww > sw {
		ww = sw
	}
	if sh := metric(smCyScreen) * 9 / 10; sh > 0 && hh > sh {
		hh = sh
	}
	return uint(ww), uint(hh)
}

// runWindow opens Apollo's own window on url and returns when it is closed or ctx ends. It needs the WebView2
// runtime; errNoWindow says it is missing (after telling the owner in a message box).
func runWindow(ctx context.Context, url string, port int) error {
	if _, err := webviewloader.GetInstalledVersion(); err != nil {
		log.Printf("apollo: webview2 runtime: %v", err)
		box(runtimeMissing)
		return errNoWindow
	}
	data := filepath.Join(os.Getenv("LocalAppData"), "Apollo", "WebView2")
	ww, hh := windowSize()
	w := webview2.NewWithOptions(webview2.WebViewOptions{
		DataPath: data,
		WindowOptions: webview2.WindowOptions{
			Title: "Apollo", Width: ww, Height: hh, IconId: iconID, Center: true,
		},
	})
	if w == nil {
		box(runtimeMissing)
		return errNoWindow
	}
	hwnd := uintptr(w.Window())
	windowReady(hwnd)
	inner, stopInner := context.WithCancel(ctx)
	defer stopInner()
	go listenForShow(inner, port, hwnd)
	go func() {
		<-inner.Done()
		w.Destroy() // posts WM_CLOSE; safe from any thread
	}()
	w.Navigate(url)
	w.Run()
	return nil
}
