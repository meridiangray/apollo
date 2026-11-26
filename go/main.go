// Command apollo serves Apollo's page on 127.0.0.1 and drives the Python manager, apollo.py: on Linux and macOS
// directly, on Windows inside WSL through wsl.exe. On Windows it is a desktop application: the page opens in its
// own WebView2 window (window_windows.go) and no browser is used; on Linux and macOS it opens the system browser.
// The page is embedded.
//
//	apollo [-port 8787] [-no-open] [-python P] [-manager PATH] [-distro Debian] [-timeout 30] [-config apollo.json]
//
// Closing the page or this program never stops a run: each run's wrapper leads its own session (apollo.py).
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"time"
)

// openCommand is the argv that opens url in the default browser on this OS.
func openCommand(goos, url string) []string {
	switch goos {
	case "windows":
		return []string{"rundll32", "url.dll,FileProtocolHandler", url}
	case "darwin":
		return []string{"open", url}
	default:
		return []string{"xdg-open", url}
	}
}

func openBrowser(url string) {
	argv := openCommand(runtime.GOOS, url)
	cmd := exec.Command(argv[0], argv[1:]...)
	hideWindow(cmd)
	if err := cmd.Start(); err != nil {
		log.Printf("open %s: %v", url, err)
		return
	}
	go cmd.Wait()
}

// another asks whether an Apollo app already answers on the port.
func another(port int) bool {
	c := http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get("http://127.0.0.1:" + strconv.Itoa(port) + "/api/ping")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	var v map[string]string
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
	return json.Unmarshal(b, &v) == nil && v["app"] == "apollo"
}

func exeDir() string {
	p, err := os.Executable()
	if err != nil {
		return ""
	}
	if r, err := filepath.EvalSymlinks(p); err == nil {
		p = r
	}
	return filepath.Dir(p)
}

// logTo: a Windows GUI program has no console, so its log goes to a file in the temp folder.
func logTo() {
	if runtime.GOOS != "windows" {
		return
	}
	f, err := os.OpenFile(filepath.Join(os.TempDir(), "apollo.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err == nil {
		log.SetOutput(f)
	}
}

func main() {
	logTo()
	cfg, err := LoadConfig(runtime.GOOS, exeDir(), os.Args[1:], os.Getenv)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			os.Exit(0)
		}
		fatal(fmt.Sprintf("apollo: %v", err))
	}
	addr := fmt.Sprintf("127.0.0.1:%d", cfg.Port)
	url := "http://" + addr + "/"
	windowed := !useBrowser(runtime.GOOS) && !cfg.NoOpen
	if windowed && !takeInstance(cfg.Port) {
		log.Printf("apollo: already running; bringing its window forward")
		showRunning(cfg.Port)
		return
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		if another(cfg.Port) {
			log.Printf("apollo: already running at %s", url)
			switch {
			case windowed:
				showRunning(cfg.Port)
			case !cfg.NoOpen:
				openBrowser(url)
			}
			return
		}
		fatal(fmt.Sprintf("apollo: port %d is in use by another program: %v", cfg.Port, err))
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	srv := &http.Server{ReadHeaderTimeout: 10 * time.Second}
	app, err := NewServer(runtime.GOOS, cfg.Port, NewBackend(runtime.GOOS, cfg), stop)
	if err != nil {
		log.Fatalf("apollo: %v", err)
	}
	srv.Handler = app.Handler()
	backend := app.Backend.Command()
	log.Printf("apollo: %s (pid %d) -> %v", url, os.Getpid(), backend)
	if cfg.Source != "" {
		log.Printf("apollo: settings from %s", cfg.Source)
	}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("apollo: %v", err)
			stop()
		}
	}()
	switch {
	case windowed:
		// The window is the client; closing it quits Apollo (runs keep going: each leads its own session).
		if err := runWindow(ctx, url, cfg.Port); err != nil {
			log.Printf("apollo: window: %v", err)
		}
		stop()
	case !cfg.NoOpen:
		openBrowser(url)
	}
	<-ctx.Done()
	shut, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	srv.Shutdown(shut)
	log.Printf("apollo: stopped")
}
