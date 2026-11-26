//go:build windows

package main

import (
	"os/exec"
	"syscall"
)

// hideWindow keeps wsl.exe from opening a console window: apollo.exe is a GUI program with no console of its own.
func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}
