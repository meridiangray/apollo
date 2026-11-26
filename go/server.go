package main

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"apollo/appicon"
)

//go:embed ui
var uiFiles embed.FS

const tokenHeader = "X-Apollo-Token"

var tabs = map[string]bool{"run": true, "found": true, "questions": true, "calls": true, "raw": true, "report": true}

// Look is the page's bar colours, posted on load and on every theme or mode change, for the window's title bar.
type Look struct {
	BG      string `json:"bg,omitempty"` // the page background, ink, primary and accent colours: the window icon is drawn in them
	Ink     string `json:"ink,omitempty"`
	Primary string `json:"primary,omitempty"`
	Accent  string `json:"accent,omitempty"`
	Caption string `json:"caption"`
	Text    string `json:"text"`
	Dark    bool   `json:"dark"`
}

var hexColour = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)

func (l Look) valid() bool {
	for _, c := range []string{l.BG, l.Ink, l.Primary, l.Accent} {
		if c != "" && !hexColour.MatchString(c) {
			return false
		}
	}
	return hexColour.MatchString(l.Caption) && hexColour.MatchString(l.Text)
}

// palette is the icon's colours for this look; false when the page did not send them.
func (l Look) palette() (appicon.Palette, bool) {
	bg, a := appicon.Hex(l.BG)
	ink, b := appicon.Hex(l.Ink)
	pri, c := appicon.Hex(l.Primary)
	acc, d := appicon.Hex(l.Accent)
	if !(a && b && c && d) {
		return appicon.Palette{}, false
	}
	return appicon.ThemePalette(bg, ink, pri, acc), true
}

func (s *Server) look(w http.ResponseWriter, r *http.Request) {
	var l Look
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<10))
	if err == nil {
		err = json.Unmarshal(body, &l)
	}
	if err != nil || !l.valid() {
		fail(w, http.StatusBadRequest, "a JSON body: caption and text as #rrggbb, dark as true or false")
		return
	}
	applyLook(l) // a no-op off Windows; never fails the app
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// Server is Apollo, the run manager's web app: the page, and the API over the manager.
type Server struct {
	GOOS    string
	Port    int
	Token   string
	Backend *Backend
	Quit    func()
	page    []byte
	cache   *cache
}

func NewToken() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func NewServer(goos string, port int, b *Backend, quit func()) (*Server, error) {
	s := &Server{GOOS: goos, Port: port, Token: NewToken(), Backend: b, Quit: quit, cache: newCache()}
	page, err := uiFiles.ReadFile("ui/index.html")
	if err != nil {
		return nil, err
	}
	s.page = []byte(strings.Replace(string(page), "{{TOKEN}}", s.Token, 1))
	return s, nil
}

// allowedHost: the page is only ever reached as 127.0.0.1:<port> or localhost:<port>; any other Host is a page
// elsewhere reaching in by DNS rebinding.
func (s *Server) allowedHost(host string) bool {
	p := strconv.Itoa(s.Port)
	return host == "127.0.0.1:"+p || host == "localhost:"+p
}

func (s *Server) allowedOrigin(origin string) bool {
	if origin == "" {
		return true
	}
	p := strconv.Itoa(s.Port)
	return origin == "http://127.0.0.1:"+p || origin == "http://localhost:"+p
}

func (s *Server) tokenOK(r *http.Request) bool {
	got := r.Header.Get(tokenHeader)
	return got != "" && subtle.ConstantTimeCompare([]byte(got), []byte(s.Token)) == 1
}

// guard checks every request's Host, and every POST's Origin and token.
func (s *Server) guard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !s.allowedHost(r.Host) {
			http.Error(w, "forbidden host", http.StatusForbidden)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			if !s.allowedOrigin(r.Header.Get("Origin")) || !s.tokenOK(r) {
				writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden: no valid token"})
				return
			}
		}
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy",
			"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
		h.Set("Cache-Control", "no-store")
		next.ServeHTTP(w, r)
	})
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	static, _ := fs.Sub(uiFiles, "ui")
	files := http.FileServer(http.FS(static))
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(s.page)
	})
	mux.HandleFunc("GET /index.html", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/", http.StatusMovedPermanently)
	})
	mux.Handle("GET /app.js", files)
	mux.Handle("GET /entity-icons.js", files)
	mux.Handle("GET /rail.js", files)
	mux.Handle("GET /sources.js", files)
	mux.Handle("GET /collections.js", files)
	mux.Handle("GET /data.js", files)
	mux.Handle("GET /toast.js", files)
	mux.Handle("GET /motion.js", files)
	mux.Handle("GET /history.js", files)
	mux.Handle("GET /agent.js", files)
	mux.Handle("GET /scrollline.js", files)
	mux.Handle("GET /btnlabel.js", files)
	mux.Handle("GET /flowroute.js", files)
	mux.Handle("GET /app.css", files)
	mux.Handle("GET /launch/launch.js", files) // the launch screen (ui/launch/)
	mux.Handle("GET /launch/launch.css", files)
	for _, n := range []string{"gsap", "DrawSVGPlugin", "Flip"} { // GSAP 3.13.0, the copies in ui/vendor: the core and the two plugins used
		mux.Handle("GET /vendor/gsap-3.13.0/"+n+".min.js", files)

	}
	mux.HandleFunc("GET /api/recipe/{name}", s.recipe)
	mux.HandleFunc("GET /api/prompt/{name}", s.prompt)
	mux.HandleFunc("GET /api/analyse", s.analyse)
	mux.HandleFunc("GET /api/tool", s.toolGet)
	mux.HandleFunc("GET /api/tool/about", s.toolAbout) // tool args
	mux.HandleFunc("GET /api/ready", s.ready)
	mux.HandleFunc("POST /api/tool", s.toolStart)
	mux.HandleFunc("GET /api/sources", s.sources)
	mux.HandleFunc("POST /api/sources/upload", s.upload)
	mux.HandleFunc("POST /api/source/{slug}", s.sourceUpdate)
	mux.HandleFunc("POST /api/source/{slug}/remove", s.sourceRemove)
	mux.HandleFunc("GET /api/source/thumb", s.sourceThumb)
	mux.HandleFunc("POST /api/wikipedia", s.wikipedia)
	mux.HandleFunc("POST /api/notice", s.notice)
	mux.HandleFunc("GET /api/pagetext", s.pagetext)
	mux.HandleFunc("GET /api/proofread", s.proofreadGet)
	mux.HandleFunc("POST /api/proofread", s.proofreadPost(false))
	mux.HandleFunc("POST /api/proofread/act", s.proofreadPost(true))
	mux.HandleFunc("GET /api/catalogue", s.catalogueGet)
	mux.HandleFunc("POST /api/catalogue", s.cataloguePost(false))
	mux.HandleFunc("POST /api/catalogue/act", s.cataloguePost(true))
	mux.HandleFunc("POST /api/catalogue/edit", s.catalogueEdit) // tool args
	mux.HandleFunc("POST /api/brief/generate", s.briefGenerate)
	mux.HandleFunc("POST /api/catalogue/mark", s.catalogueMark)
	mux.HandleFunc("GET /api/catalogue/export", s.catalogueExport)
	mux.HandleFunc("GET /api/history", s.history)
	mux.HandleFunc("POST /api/logdb", s.logdb)
	mux.HandleFunc("GET /api/collections", s.collections)
	mux.HandleFunc("GET /api/entitytrace", s.entitytrace) // entity trace
	mux.Handle("GET /entitytrace.js", files)              // entity trace
	mux.Handle("GET /pageview.js", files)                 // pageview
	mux.HandleFunc("GET /api/pageview", s.pageview)       // pageview
	mux.HandleFunc("POST /api/export/{id}", s.export)     // export
	s.chatRoutes(mux, files)                              // chat: /chat.js, /chat.css, /api/chat
	mux.HandleFunc("GET /api/ping", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"app": "apollo"})
	})
	mux.HandleFunc("GET /api/runs", s.runs)
	mux.HandleFunc("GET /api/run/{id}", s.run)
	mux.HandleFunc("GET /api/view/{id}", s.view)
	mux.HandleFunc("GET /api/choices/{id}", s.choices) // choices
	mux.HandleFunc("GET /api/configs", s.configs)
	mux.HandleFunc("POST /api/look", s.look)
	mux.HandleFunc("POST /api/start", s.start)
	mux.HandleFunc("POST /api/stop/{id}", s.stop)
	mux.HandleFunc("POST /api/limits/{id}", s.limits) // limits
	mux.HandleFunc("POST /api/clear/{id}", s.clear)
	mux.HandleFunc("POST /api/rerun/{id}", s.rerun)
	mux.HandleFunc("POST /api/resume/{id}", s.resume)
	mux.HandleFunc("POST /api/quit", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
		if s.Quit != nil {
			go func() { time.Sleep(200 * time.Millisecond); s.Quit() }()
		}
	})
	return s.guard(mux)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

// call runs the manager; reads share one call per second at most (several tabs, one poll).
func (s *Server) call(r *http.Request, ttl time.Duration, args ...string) ([]byte, error) {
	key := strings.Join(args, "\x00")
	return s.cache.get(key, ttl, func() ([]byte, error) {
		return s.Backend.Call(context.WithoutCancel(r.Context()), 0, args...)
	})
}

func (s *Server) runs(w http.ResponseWriter, r *http.Request) {
	raw, err := s.call(r, 700*time.Millisecond, "ls", "--json")
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	rows, err := ParseRuns(s.GOOS, raw)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) run(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.call(r, 700*time.Millisecond, "status", id, "--json")
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	st, out, err := ParseStatus(s.GOOS, raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case out != nil:
		fail(w, http.StatusNotFound, out.Why)
	default:
		writeJSON(w, http.StatusOK, st)
	}
}

func (s *Server) view(w http.ResponseWriter, r *http.Request) {
	id, tab := r.PathValue("id"), r.URL.Query().Get("tab")
	if tab == "" {
		tab = "run"
	}
	if !validID(id) || !tabs[tab] {
		fail(w, http.StatusBadRequest, "a run id and a tab: run, found, questions, calls, raw or report")
		return
	}
	args := []string{"view", id, "--tab", tab, "--json"}
	// one log row's detail (?row=12) or one model call in full (?n=7): whole numbers only
	for _, p := range [][2]string{{"row", "--row"}, {"n", "--n"}} {
		if v := r.URL.Query().Get(p[0]); v != "" {
			if _, err := strconv.ParseUint(v, 10, 32); err != nil {
				fail(w, http.StatusBadRequest, p[0]+": a whole number")
				return
			}
			args = append(args, p[1], v)
		}
	}
	raw, err := s.call(r, 700*time.Millisecond, args...)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	v, out, err := ParseView(s.GOOS, raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case out != nil:
		fail(w, http.StatusNotFound, out.Why)
	default:
		writeJSON(w, http.StatusOK, v)
	}
}

func (s *Server) configs(w http.ResponseWriter, r *http.Request) {
	raw, err := s.call(r, 5*time.Second, "configs", "--json")
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	rows, err := ParseConfigs(s.GOOS, raw)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) detail(w http.ResponseWriter, r *http.Request, args ...string) {
	raw, err := s.call(r, 5*time.Second, args...)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	got, out, err := ParseDetail(raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case out != nil:
		fail(w, http.StatusNotFound, out.Why)
	default:
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Write(got)
	}
}

// recipe is one recipe's configuration (read only), on the source the page names.
func (s *Server) recipe(w http.ResponseWriter, r *http.Request) {
	name, src := r.PathValue("name"), r.URL.Query().Get("source")
	if !reSlug.MatchString(name) || (src != "" && !reSlug.MatchString(src)) {
		fail(w, http.StatusBadRequest, "a recipe name and a source name")
		return
	}
	args := []string{"recipe", name, "--json"}
	if src != "" {
		args = append(args, "--source", src)
	}
	s.detail(w, r, args...)
}

// analyse is the page flow's triage of a source (no model calls): segments, text layer, words and redaction per page.
func (s *Server) analyse(w http.ResponseWriter, r *http.Request) {
	src, pages, rec := r.URL.Query().Get("source"), r.URL.Query().Get("pages"), r.URL.Query().Get("recipe")
	if rec == "" {
		rec = "full"
	}
	if !reSlug.MatchString(src) || !reSlug.MatchString(rec) || (pages != "" && !rePages.MatchString(pages)) {
		fail(w, http.StatusBadRequest, "a source name and pages A-B")
		return
	}
	args := []string{"analyse", "--source", src, "--recipe", rec, "--json"}
	if pages != "" {
		args = append(args, "--pages", pages)
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 5*time.Minute, args...)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	got, out, err := ParseDetail(raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case out != nil:
		fail(w, http.StatusConflict, out.Why)
	default:
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Write(got)
	}
}

// prompt is one prompt file of the page flow (read only).
func (s *Server) prompt(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if !reSlug.MatchString(name) {
		fail(w, http.StatusBadRequest, "a prompt name")
		return
	}
	s.detail(w, r, "prompt", name, "--json")
}

func (s *Server) start(w http.ResponseWriter, r *http.Request) {
	var req StartRequest
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: recipe and source (or config, or workflow and archive), name, model, dry, pages, budget")
		return
	}
	args, err := StartArgs(s.GOOS, req)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 90*time.Second, args...)
	s.cache.clear()
	s.outcome(w, raw, err)
}

func (s *Server) stop(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 0, "stop", id, "--json")
	s.cache.clear()
	s.outcome(w, raw, err)
}

// limits turns a live run's run-level caps on or off: {"limits": bool}. // limits
func (s *Server) limits(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var req struct {
		Limits bool `json:"limits"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<10))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if !validID(id) || err != nil {
		fail(w, http.StatusBadRequest, "a run id and {limits: true|false}")
		return
	}
	state := "off"
	if req.Limits {
		state = "on"
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 0, "limits", id, state, "--json")
	s.cache.clear()
	s.outcome(w, raw, err)
}

func (s *Server) outcome(w http.ResponseWriter, raw []byte, err error) {
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	out, err := ParseOutcome(raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case !out.OK:
		fail(w, http.StatusConflict, out.Why)
	default:
		writeJSON(w, http.StatusOK, out)
	}
}

// ---- a small cache: one manager call per key at a time, reused for ttl ------------------------------------------

type entry struct {
	done chan struct{}
	at   time.Time
	out  []byte
	err  error
}

type cache struct {
	mu sync.Mutex
	m  map[string]*entry
}

func newCache() *cache { return &cache{m: map[string]*entry{}} }

func (c *cache) get(key string, ttl time.Duration, fn func() ([]byte, error)) ([]byte, error) {
	c.mu.Lock()
	e := c.m[key]
	if e != nil {
		select {
		case <-e.done:
			if time.Since(e.at) < ttl {
				c.mu.Unlock()
				return e.out, e.err
			}
			e = nil
		default: // a call is under way: wait for it
			c.mu.Unlock()
			<-e.done
			return e.out, e.err
		}
	}
	e = &entry{done: make(chan struct{})}
	c.m[key] = e
	c.mu.Unlock()
	e.out, e.err = fn()
	e.at = time.Now()
	close(e.done)
	if e.err != nil {
		var me *ManagerError
		if !errors.As(e.err, &me) {
			c.mu.Lock()
			delete(c.m, key)
			c.mu.Unlock()
		}
	}
	return e.out, e.err
}

func (c *cache) clear() {
	c.mu.Lock()
	for k, e := range c.m {
		select {
		case <-e.done:
			delete(c.m, k)
		default:
		}
	}
	c.mu.Unlock()
}

// clear deletes a finished run and what it made (apollo.py clear refuses a running one).
func (s *Server) clear(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 0, "clear", id, "--json")
	s.cache.clear()
	s.outcome(w, raw, err)
}

// rerun clears a finished run and starts it again with the same settings (apollo.py rerun).
func (s *Server) rerun(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 90*time.Second, "rerun", id, "--json")
	s.cache.clear()
	s.outcome(w, raw, err)
}

// toolGet is a tool's last result and progress. The manager's worker answers it: a file read.
func (s *Server) toolGet(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	args, err := ToolArgs(s.GOOS, ToolRequest{Tool: q.Get("tool"), Source: q.Get("source"), Archive: q.Get("archive")}, true)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 20*time.Second, args...)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Write(raw)
}

// toolStart starts a tool in its own manager process (never the worker) and returns at once.
func (s *Server) toolStart(w http.ResponseWriter, r *http.Request) {
	var req ToolRequest
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: tool, source or archive, pages, lang")
		return
	}
	args, err := ToolArgs(s.GOOS, req, false)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 60*time.Second, args...)
	s.outcome(w, raw, err)
}

// ready answers "is this ready for a run": the page flow's preflight without a model call, and the archive's check.
func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	args, err := ReadyArgs(s.GOOS, q.Get("source"), q.Get("recipe"), q.Get("pages"), q.Get("archive"))
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 3*time.Minute, args...)
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	got, out, err := ParseDetail(raw)
	switch {
	case err != nil:
		fail(w, http.StatusBadGateway, err.Error())
	case out != nil && !out.OK:
		fail(w, http.StatusConflict, out.Why)
	default:
		if got == nil {
			got = raw
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Write(got)
	}
}

// choices: a run's ticks and the agent's choices behind them, read from the run's files by the manager (choices.py).
func (s *Server) choices(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.call(r, 700*time.Millisecond, "choices", id, "--json")
	passJSON(w, raw, err, http.StatusNotFound)
}

// resume carries a stopped page-flow run on from its saved state, clearing nothing (apollo.py resume).
func (s *Server) resume(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 30*time.Second, "resume", id, "--json")
	s.cache.clear()
	s.outcome(w, raw, err)
}
