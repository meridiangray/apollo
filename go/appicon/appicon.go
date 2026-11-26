// Package appicon draws Apollo's icon: a rounded square, a crescent moon, an orbit around it and a small body on the orbit. The
// window repaints it in the theme's colours (a Palette); the exe's file icon is drawn once, in the default palette, by go/icon.
// Standard library only, so the same file serves both.
package appicon

import (
	"image"
	"image/color"
	"math"
	"strconv"
)

// RGB is a colour as three 0-255 values.
type RGB [3]float64

// Palette is the icon's three colours: the square, the moon and the orbit with its body.
type Palette struct{ Square, Moon, Orbit RGB }

// Default is the hydra dune palette the exe's file icon is drawn in: teal, cream and accent.
var Default = Palette{Square: RGB{0x1b, 0x5e, 0x52}, Moon: RGB{0xf6, 0xed, 0xe7}, Orbit: RGB{0xc8, 0x7a, 0x4f}}

// Hex reads #rrggbb.
func Hex(s string) (RGB, bool) {
	if len(s) != 7 || s[0] != '#' {
		return RGB{}, false
	}
	v, err := strconv.ParseUint(s[1:], 16, 32)
	if err != nil {
		return RGB{}, false
	}
	return RGB{float64(v >> 16 & 0xff), float64(v >> 8 & 0xff), float64(v & 0xff)}, true
}

func lum(c RGB) float64 {
	f := func(v float64) float64 {
		v /= 255
		if v <= 0.03928 {
			return v / 12.92
		}
		return math.Pow((v+0.055)/1.055, 2.4)
	}
	return 0.2126*f(c[0]) + 0.7152*f(c[1]) + 0.0722*f(c[2])
}

// Contrast is the WCAG contrast ratio of two colours (1 to 21).
func Contrast(a, b RGB) float64 {
	x, y := lum(a), lum(b)
	if x < y {
		x, y = y, x
	}
	return (x + 0.05) / (y + 0.05)
}

// ThemePalette paints the icon in a theme: the square in its primary colour, the orbit in its accent, the moon in the page's
// background or its ink, whichever stands out more from the square. An orbit that would vanish on the square takes the moon's colour.
func ThemePalette(bg, ink, primary, accent RGB) Palette {
	moon := bg
	if Contrast(ink, primary) > Contrast(bg, primary) {
		moon = ink
	}
	orbit := accent
	if Contrast(accent, primary) < 1.8 {
		orbit = moon
	}
	return Palette{Square: primary, Moon: moon, Orbit: orbit}
}

// at is the icon at the point (x, y) of the unit square: the colour and its alpha.
func at(x, y float64, p Palette) (RGB, float64) {
	const half, rad = 0.5, 0.22
	qx, qy := math.Abs(x-0.5)-(half-rad), math.Abs(y-0.5)-(half-rad)
	d := math.Hypot(math.Max(qx, 0), math.Max(qy, 0)) + math.Min(math.Max(qx, qy), 0) - rad
	if d > 0 {
		return p.Square, 0
	}
	c := p.Square
	// orbit: an ellipse tilted by -28 degrees
	const a, b, th = 0.37, 0.125, -28 * math.Pi / 180
	ox, oy := x-0.5, y-0.53
	ex := ox*math.Cos(th) + oy*math.Sin(th)
	ey := -ox*math.Sin(th) + oy*math.Cos(th)
	f := math.Hypot(ex/a, ey/b)
	g := math.Hypot(ex/(a*a), ey/(b*b)) / math.Max(f, 1e-9)
	ring := math.Abs(f-1) / math.Max(g, 1e-9)
	// crescent: a disc with a second disc bitten out of it
	inMoon := math.Hypot(x-0.46, y-0.5) < 0.27 && math.Hypot(x-0.57, y-0.44) >= 0.23
	// the orbit passes in front of the moon's lower half and behind its upper half
	front := ey > 0
	if ring < 0.016 && !(inMoon && !front) {
		c = p.Orbit
	}
	if inMoon && !(ring < 0.016 && front) {
		c = p.Moon
	}
	// the small body on the orbit, upper right
	bx, by := a*math.Cos(-0.55), b*math.Sin(-0.55)
	px := bx*math.Cos(th) - by*math.Sin(th) + 0.5
	py := bx*math.Sin(th) + by*math.Cos(th) + 0.53
	if math.Hypot(x-px, y-py) < 0.058 {
		c = p.Moon
	}
	if math.Hypot(x-px, y-py) < 0.04 {
		c = p.Orbit
	}
	return c, 1
}

// Render draws the icon at n x n pixels, each pixel the mean of 8 x 8 samples.
func Render(n int, p Palette) *image.NRGBA {
	const ss = 8
	img := image.NewNRGBA(image.Rect(0, 0, n, n))
	for py := 0; py < n; py++ {
		for px := 0; px < n; px++ {
			var r, g, b, al float64
			for sy := 0; sy < ss; sy++ {
				for sx := 0; sx < ss; sx++ {
					c, a := at((float64(px)+(float64(sx)+0.5)/ss)/float64(n), (float64(py)+(float64(sy)+0.5)/ss)/float64(n), p)
					r, g, b, al = r+c[0]*a, g+c[1]*a, b+c[2]*a, al+a
				}
			}
			if al > 0 {
				img.SetNRGBA(px, py, color.NRGBA{uint8(r/al + 0.5), uint8(g/al + 0.5), uint8(b/al + 0.5), uint8(al/(ss*ss)*255 + 0.5)})
			}
		}
	}
	return img
}
