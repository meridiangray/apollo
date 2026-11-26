package appicon

import (
	"image/color"
	"testing"
)

func TestDefaultPaletteDrawsTheExeIcon(t *testing.T) {
	img := Render(48, Default)
	if img.Bounds().Dx() != 48 || img.Bounds().Dy() != 48 {
		t.Fatalf("size %v", img.Bounds())
	}
	if c := img.NRGBAAt(0, 0); c.A != 0 {
		t.Fatalf("the corner is outside the rounded square: %+v", c)
	}
	if c := img.NRGBAAt(4, 24); c != (color.NRGBA{0x1b, 0x5e, 0x52, 255}) {
		t.Fatalf("the square is teal: %+v", c)
	}
}

func TestThemeColoursComeOut(t *testing.T) {
	bg, _ := Hex("#141618")
	ink, _ := Hex("#ece8e3")
	primary, _ := Hex("#9db4cc")
	accent, _ := Hex("#7a2d1a")
	p := ThemePalette(bg, ink, primary, accent)
	if p.Square != primary || p.Orbit != accent {
		t.Fatalf("square and orbit follow the theme: %+v", p)
	}
	if p.Moon != bg {
		t.Fatalf("a dark background stands out from a light square: %+v", p.Moon)
	}
	for _, n := range []int{16, 32, 48} {
		img := Render(n, p)
		seen := map[color.NRGBA]bool{}
		for y := 0; y < n; y++ {
			for x := 0; x < n; x++ {
				if c := img.NRGBAAt(x, y); c.A == 255 {
					seen[c] = true
				}
			}
		}
		wants := []RGB{p.Square, p.Moon}
		if n >= 32 {
			wants = append(wants, p.Orbit) // at 16 px the orbit is a one-pixel line, blended with its neighbours
		}
		for _, want := range wants {
			if !seen[color.NRGBA{uint8(want[0]), uint8(want[1]), uint8(want[2]), 255}] {
				t.Fatalf("size %d: colour %v missing from the icon", n, want)
			}
		}
	}
}

func TestMoonTakesTheInkWhenTheBackgroundBlendsIn(t *testing.T) {
	bg, _ := Hex("#f6ede7")
	ink, _ := Hex("#22201d")
	primary, _ := Hex("#e8dccf")
	if p := ThemePalette(bg, ink, primary, RGB{200, 122, 79}); p.Moon != ink {
		t.Fatalf("moon %v, want the ink", p.Moon)
	}
}
