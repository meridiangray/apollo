// Command icon draws Apollo's icon and writes the Windows resources that carry it.
//
//	go run . -syso ../rsrc_windows_amd64.syso -png ./png
//
// It is a separate module (its own go.mod) so the app itself never depends on the resource library. Run
// it only when the icon or the version information changes; the .syso it writes is checked in beside main.go.
package main

import (
	"flag"
	"image"
	"image/png"
	"log"
	"os"
	"path/filepath"

	"apollo/appicon"
	"github.com/tc-hib/winres"
	"github.com/tc-hib/winres/version"
)

var sizes = []int{16, 24, 32, 48, 64, 256}

func main() {
	syso := flag.String("syso", "rsrc_windows_amd64.syso", "the resource object to write")
	listExe := flag.String("list", "", "instead: list the resources of this exe")
	pngDir := flag.String("png", "", "also write icon-<n>.png for each size into this folder")
	flag.Parse()
	if *listExe != "" {
		if err := list(*listExe); err != nil {
			log.Fatal(err)
		}
		return
	}
	var imgs []image.Image
	for _, n := range sizes {
		img := appicon.Render(n, appicon.Default)
		imgs = append(imgs, img)
		if *pngDir != "" {
			if err := os.MkdirAll(*pngDir, 0o755); err != nil {
				log.Fatal(err)
			}
			f, err := os.Create(filepath.Join(*pngDir, "icon-"+itoa(n)+".png"))
			if err != nil {
				log.Fatal(err)
			}
			if err := png.Encode(f, img); err != nil {
				log.Fatal(err)
			}
			f.Close()
		}
	}
	icon, err := winres.NewIconFromImages(imgs)
	if err != nil {
		log.Fatal(err)
	}
	rs := winres.ResourceSet{}
	if err := rs.SetIcon(winres.ID(1), icon); err != nil { // id 1: the exe's file icon, and the window's (IconId 1)
		log.Fatal(err)
	}
	vi := version.Info{FileVersion: [4]uint16{1, 0, 0, 0}, ProductVersion: [4]uint16{1, 0, 0, 0}}
	for k, v := range map[string]string{
		version.ProductName:      "Apollo",
		version.FileDescription:  "Apollo: run and watch hydra-mcp runs",
		version.InternalName:     "apollo",
		version.OriginalFilename: "Apollo.exe",
		version.FileVersion:      "1.0.0.0",
		version.ProductVersion:   "1.0.0.0",
	} {
		if err := vi.Set(version.LangDefault, k, v); err != nil {
			log.Fatal(err)
		}
	}
	rs.SetVersionInfo(vi)
	rs.SetManifest(winres.AppManifest{
		Description:         "Apollo",
		ExecutionLevel:      winres.AsInvoker,
		DPIAwareness:        winres.DPIPerMonitorV2,
		UseCommonControlsV6: true,
		Compatibility:       winres.Win10AndAbove,
	})
	out, err := os.Create(*syso)
	if err != nil {
		log.Fatal(err)
	}
	defer out.Close()
	if err := rs.WriteObject(out, winres.ArchAMD64); err != nil {
		log.Fatal(err)
	}
}

func itoa(n int) string {
	s := ""
	for ; n > 0; n /= 10 {
		s = string(rune('0'+n%10)) + s
	}
	return s
}
