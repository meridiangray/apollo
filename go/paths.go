package main

import (
	"regexp"
	"strings"
)

var (
	wslDrive = regexp.MustCompile(`^/mnt/([a-zA-Z])(/.*)?$`)
	winDrive = regexp.MustCompile(`^([a-zA-Z]):([\\/].*)?$`)
)

// ToHost is a manager path as this OS shows it: on Windows, /mnt/e/x/y is E:\x\y; elsewhere it is unchanged. A
// path on WSL's own disk (/home/...) has no drive letter and stays as it is.
func ToHost(goos, p string) string {
	if goos != "windows" {
		return p
	}
	m := wslDrive.FindStringSubmatch(p)
	if m == nil {
		return p
	}
	rest := strings.ReplaceAll(m[2], "/", `\`)
	if rest == "" {
		rest = `\`
	}
	return strings.ToUpper(m[1]) + ":" + rest
}

// ToWSL is a path as the manager in WSL reads it: E:\x\y (or E:/x/y) is /mnt/e/x/y; a POSIX path is unchanged.
func ToWSL(p string) string {
	m := winDrive.FindStringSubmatch(p)
	if m == nil {
		return p
	}
	rest := strings.ReplaceAll(m[2], `\`, "/")
	return "/mnt/" + strings.ToLower(m[1]) + strings.TrimRight(rest, "/")
}

// managerPath is a path the user gave, as the manager on this OS needs it: Windows and Linux (WSL) take a drive
// path too; macOS takes it as it is.
func managerPath(goos, p string) string {
	if goos == "darwin" {
		return p
	}
	return ToWSL(p)
}
