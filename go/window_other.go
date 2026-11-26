//go:build !windows

package main

import (
	"context"
	"errors"
	"log"
)

var errNoWindow = errors.New("no native window on this OS")

// runWindow: Linux and macOS have no native window; main serves and opens the browser instead.
func runWindow(ctx context.Context, url string, port int) error { return errNoWindow }

// applyLook: only Windows has a native title bar to colour.
func applyLook(l Look) {}

// takeInstance: only Windows holds a single instance by name; elsewhere the port is the lock.
func takeInstance(port int) bool { return true }

// showRunning: nothing to bring forward; main opens the browser on the running instance.
func showRunning(port int) {}

// fatal logs and exits.
func fatal(msg string) { log.Fatal(msg) }
