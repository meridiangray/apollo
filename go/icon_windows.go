//go:build windows

package main

import (
	"image"
	"log"
	"unsafe"

	"apollo/appicon"
	"golang.org/x/sys/windows"
)

// The window's icon follows the theme: the page posts its colours with every theme change, the icon is drawn again in them
// (appicon) and set on the window, small (title bar) and big (taskbar, Alt-Tab). The exe's file icon stays as built.

var (
	gdi32                  = windows.NewLazySystemDLL("gdi32.dll")
	procCreateDIBSection   = gdi32.NewProc("CreateDIBSection")
	procCreateBitmap       = gdi32.NewProc("CreateBitmap")
	procDeleteObject       = gdi32.NewProc("DeleteObject")
	procCreateIconIndirect = user32.NewProc("CreateIconIndirect")
	procDestroyIcon        = user32.NewProc("DestroyIcon")
	procSendMessage        = user32.NewProc("SendMessageW")
	ownIcons               [2]uintptr // the icons this file made (small, big): destroyed when replaced
)

const (
	wmSetIcon  = 0x0080
	iconSmall  = 0
	iconBig    = 1
	smCxIcon   = 11
	smCxSmIcon = 49
)

type bitmapInfoHeader struct {
	Size          uint32
	Width, Height int32
	Planes, Bits  uint16
	Compression   uint32
	SizeImage     uint32
	XPels, YPels  int32
	ClrUsed       uint32
	ClrImportant  uint32
}

type iconInfo struct {
	FIcon    uint32
	XHotspot uint32
	YHotspot uint32
	_        uint32
	HbmMask  uintptr
	HbmColor uintptr
}

// hicon builds an HICON from the image: a 32-bit colour bitmap with alpha (premultiplied) and an empty mask.
func hicon(img *image.NRGBA) uintptr {
	n := img.Bounds().Dx()
	bi := bitmapInfoHeader{Size: uint32(unsafe.Sizeof(bitmapInfoHeader{})), Width: int32(n), Height: -int32(n), Planes: 1, Bits: 32}
	var bits unsafe.Pointer
	color, _, _ := procCreateDIBSection.Call(0, uintptr(unsafe.Pointer(&bi)), 0, uintptr(unsafe.Pointer(&bits)), 0, 0)
	if color == 0 || bits == nil {
		return 0
	}
	px := unsafe.Slice((*byte)(bits), n*n*4)
	for i := 0; i < n*n; i++ {
		c := img.NRGBAAt(i%n, i/n)
		a := uint32(c.A)
		px[i*4+0] = uint8(uint32(c.B) * a / 255)
		px[i*4+1] = uint8(uint32(c.G) * a / 255)
		px[i*4+2] = uint8(uint32(c.R) * a / 255)
		px[i*4+3] = c.A
	}
	mask, _, _ := procCreateBitmap.Call(uintptr(n), uintptr(n), 1, 1, 0)
	info := iconInfo{FIcon: 1, HbmMask: mask, HbmColor: color}
	h, _, _ := procCreateIconIndirect.Call(uintptr(unsafe.Pointer(&info)))
	procDeleteObject.Call(color)
	if mask != 0 {
		procDeleteObject.Call(mask)
	}
	return h
}

// paintIcon draws the icon in the look's colours at 16, 32 and 48 px, and sets the sizes this window's dpi asks for.
func paintIcon(hwnd uintptr, l Look) {
	defer func() { recover() }() // a refused icon is never a reason to fail
	p, ok := l.palette()
	if !ok {
		return
	}
	small, big := 16, 32
	if metric(smCxSmIcon) > 16 {
		small = 32
	}
	if metric(smCxIcon) > 32 {
		big = 48
	}
	for i, size := range []int{small, big} {
		h := hicon(appicon.Render(size, p))
		if h == 0 {
			log.Printf("apollo: icon %d px: not made", size)
			continue
		}
		procSendMessage.Call(hwnd, wmSetIcon, uintptr(i), h)
		if ownIcons[i] != 0 {
			procDestroyIcon.Call(ownIcons[i])
		}
		ownIcons[i] = h
	}
}
