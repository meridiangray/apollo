package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// The fixed places of the owner's machine: the WSL venv's python and the manager on the shared drive.
const (
	defaultWSLPython = "/home/manager/.venvs/apollo/bin/python"
	defaultManagerPy   = "/mnt/e/Documents/development/stable/apollo/py/apollo.py"
	defaultDistro    = "Debian"
	defaultPort      = 8787
	defaultTimeout   = 30
)

// Config is where the manager is and how to reach it. Later sources win: defaults, apollo.json next to the
// executable, APOLLO_* environment variables, flags.
type Config struct {
	Python   string `json:"python"`
	Manager    string `json:"manager"`
	Distro   string `json:"distro"`
	Port     int    `json:"port"`
	TimeoutS int    `json:"timeout_s"`
	NoOpen   bool   `json:"-"`
	Source   string `json:"-"` // the apollo.json read, if any
}

func (c Config) Timeout() time.Duration { return time.Duration(c.TimeoutS) * time.Second }

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// Defaults for one OS. The manager is the py/apollo.py beside the bin/ folder the executable sits in, when there is one
// (on Windows read through WSL: E:\x\py\apollo.py is /mnt/e/x/py/apollo.py); else the fixed path on the shared drive. On
// Windows the python is the WSL venv's; on Linux too when it exists; else python3.
func Defaults(goos, exeDir string, have func(string) bool) Config {
	c := Config{Python: defaultWSLPython, Manager: defaultManagerPy, Distro: defaultDistro, Port: defaultPort,
		TimeoutS: defaultTimeout}
	if goos != "windows" && (goos == "darwin" || !have(defaultWSLPython)) {
		c.Python = "python3"
	}
	for _, near := range managerCandidates(exeDir) {
		if have(near) {
			c.Manager = near
			if goos == "windows" {
				c.Manager = ToWSL(near)
			}
			break
		}
	}
	return c
}

// managerCandidates are the places to look for py/apollo.py near the executable, in order: beside it
// (<exeDir>/py/apollo.py, where Apollo.exe sits), then one folder up (<exeDir>/../py/apollo.py, for bin/). Either kind
// of separator.
func managerCandidates(exeDir string) []string {
	d := strings.TrimRight(exeDir, `\/`)
	if d == "" {
		return nil
	}
	sep := "/"
	if strings.Contains(d, `\`) {
		sep = `\`
	}
	out := []string{d + sep + "py" + sep + "apollo.py"}
	if i := strings.LastIndexAny(d, `\/`); i >= 0 {
		out = append(out, d[:i+1]+"py"+sep+"apollo.py")
	}
	return out
}

// merge copies the set fields of o over c.
func (c *Config) merge(o Config) {
	if o.Python != "" {
		c.Python = o.Python
	}
	if o.Manager != "" {
		c.Manager = o.Manager
	}
	if o.Distro != "" {
		c.Distro = o.Distro
	}
	if o.Port != 0 {
		c.Port = o.Port
	}
	if o.TimeoutS != 0 {
		c.TimeoutS = o.TimeoutS
	}
}

func fromEnv(get func(string) string) (Config, error) {
	c := Config{Python: get("APOLLO_PYTHON"), Manager: get("APOLLO_MANAGER"), Distro: get("APOLLO_DISTRO")}
	for name, dst := range map[string]*int{"APOLLO_PORT": &c.Port, "APOLLO_TIMEOUT": &c.TimeoutS} {
		if v := get(name); v != "" {
			n, err := strconv.Atoi(v)
			if err != nil {
				return c, fmt.Errorf("%s: a number, not %q", name, v)
			}
			*dst = n
		}
	}
	return c, nil
}

// LoadConfig builds the config for this OS from the sources in order.
func LoadConfig(goos, exeDir string, args []string, get func(string) string) (Config, error) {
	c := Defaults(goos, exeDir, exists)
	fsys := flag.NewFlagSet("apollo", flag.ContinueOnError)
	var f Config
	cfgPath := fsys.String("config", "", "a apollo.json to read (default: apollo.json next to the executable)")
	fsys.StringVar(&f.Python, "python", "", "the python that runs apollo.py (Windows: inside WSL)")
	fsys.StringVar(&f.Manager, "manager", "", "the path of apollo.py (Windows: a WSL path)")
	fsys.StringVar(&f.Distro, "distro", "", "Windows: the WSL distribution")
	fsys.IntVar(&f.Port, "port", 0, fmt.Sprintf("the port on 127.0.0.1 (default %d)", defaultPort))
	fsys.IntVar(&f.TimeoutS, "timeout", 0, fmt.Sprintf("seconds a manager call may take (default %d)", defaultTimeout))
	fsys.BoolVar(&f.NoOpen, "no-open", false, "serve only: open no window and no browser")
	if err := fsys.Parse(args); err != nil {
		return c, err
	}
	path := *cfgPath
	if path == "" && exeDir != "" {
		path = filepath.Join(exeDir, "apollo.json")
	}
	if path != "" {
		b, err := os.ReadFile(path)
		switch {
		case err == nil:
			var j Config
			if err := json.Unmarshal(b, &j); err != nil {
				return c, fmt.Errorf("%s: %v", path, err)
			}
			c.merge(j)
			c.Source = path
		case *cfgPath != "" || !errors.Is(err, fs.ErrNotExist):
			return c, err
		}
	}
	e, err := fromEnv(get)
	if err != nil {
		return c, err
	}
	c.merge(e)
	c.merge(f)
	c.NoOpen = f.NoOpen
	if c.Port < 1 || c.Port > 65535 {
		return c, fmt.Errorf("port %d: 1 to 65535", c.Port)
	}
	if c.TimeoutS < 1 {
		return c, fmt.Errorf("timeout %d: at least 1 second", c.TimeoutS)
	}
	return c, nil
}
