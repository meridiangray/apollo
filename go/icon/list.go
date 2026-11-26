package main

import (
	"fmt"
	"os"

	"github.com/tc-hib/winres"
	"github.com/tc-hib/winres/version"
)

var typeNames = map[winres.ID]string{winres.RT_ICON: "RT_ICON", winres.RT_GROUP_ICON: "RT_GROUP_ICON",
	winres.RT_VERSION: "RT_VERSION", winres.RT_MANIFEST: "RT_MANIFEST"}

// list prints the resources of a built exe: its icons, version strings and manifest. It reads the file only.
func list(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	rs, err := winres.LoadFromEXE(f)
	if err != nil {
		return err
	}
	rs.Walk(func(typeID, resID winres.Identifier, lang uint16, data []byte) bool {
		name := fmt.Sprint(typeID)
		if id, ok := typeID.(winres.ID); ok && typeNames[id] != "" {
			name = typeNames[id]
		}
		fmt.Printf("%-14s id %-6v lang 0x%04x  %6d bytes\n", name, resID, lang, len(data))
		if typeID == winres.RT_VERSION {
			if vi, err := version.FromBytes(data); err == nil {
				b, _ := vi.MarshalJSON()
				fmt.Printf("    %s\n", b)
			}
		}
		return true
	})
	return nil
}
