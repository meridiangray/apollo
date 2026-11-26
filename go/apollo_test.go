package main

import (
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// ---- the command per OS -------------------------------------------------------------------------------------------

func TestCommandPerOS(t *testing.T) {
	c := Config{Python: "/py", Manager: "/f/apollo.py", Distro: "Debian", TimeoutS: 5}
	got := NewBackend("windows", c).Command("ls", "--json")
	want := []string{"wsl.exe", "-d", "Debian", "--exec", "/py", "/f/apollo.py", "ls", "--json"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("windows: %v", got)
	}
	for _, goos := range []string{"linux", "darwin"} {
		got := NewBackend(goos, c).Command("stop", "x", "--json")
		if !reflect.DeepEqual(got, []string{"/py", "/f/apollo.py", "stop", "x", "--json"}) {
			t.Fatalf("%s: %v", goos, got)
		}
	}
}

func TestDefaultsPerOS(t *testing.T) {
	none := func(string) bool { return false }
	w := Defaults("windows", "", none)
	if w.Python != defaultWSLPython || w.Manager != defaultManagerPy || w.Distro != "Debian" || w.Port != 8787 {
		t.Fatalf("windows: %+v", w)
	}
	if d := Defaults("darwin", "", none); d.Python != "python3" {
		t.Fatalf("darwin: %+v", d)
	}
	if l := Defaults("linux", "", func(p string) bool { return p == defaultWSLPython }); l.Python != defaultWSLPython {
		t.Fatalf("linux: %+v", l)
	}
	// the manager: beside the executable first, then one folder up (bin/); Linux as it is, Windows through WSL
	only := func(want string) func(string) bool { return func(p string) bool { return p == want } }
	all := func(string) bool { return true }
	if l := Defaults("linux", "/srv/apollo/bin", only("/srv/apollo/py/apollo.py")); l.Manager != "/srv/apollo/py/apollo.py" {
		t.Fatalf("linux up: %s", l.Manager)
	}
	if l := Defaults("linux", "/srv/apollo/bin", all); l.Manager != "/srv/apollo/bin/py/apollo.py" {
		t.Fatalf("linux beside wins: %s", l.Manager)
	}
	if w := Defaults("windows", `E:\Documents\apollo`, only(`E:\Documents\apollo\py\apollo.py`)); w.Manager != "/mnt/e/Documents/apollo/py/apollo.py" {
		t.Fatalf("windows beside: %s", w.Manager)
	}
	if w := Defaults("windows", `E:\Documents\apollo\bin`, only(`E:\Documents\apollo\py\apollo.py`)); w.Manager != "/mnt/e/Documents/apollo/py/apollo.py" {
		t.Fatalf("windows up: %s", w.Manager)
	}
	if w := Defaults("windows", `E:\Documents\apollo`, all); w.Manager != "/mnt/e/Documents/apollo/py/apollo.py" {
		t.Fatalf("windows both: %s", w.Manager)
	}
	if got := managerCandidates(`E:\a\apollo\`); !reflect.DeepEqual(got, []string{`E:\a\apollo\py\apollo.py`, `E:\a\py\apollo.py`}) {
		t.Fatalf("candidates: %v", got)
	}
	if managerCandidates("") != nil {
		t.Fatal("no exe dir, no candidates")
	}
}

func TestNoBrowserOnWindows(t *testing.T) {
	for goos, want := range map[string]bool{"windows": false, "linux": true, "darwin": true} {
		if useBrowser(goos) != want {
			t.Fatalf("%s: useBrowser should be %v", goos, want)
		}
	}
}

func TestInstanceNamesPerPort(t *testing.T) {
	m, e := instanceNames(8787)
	if m != `Local\Apollo.instance.8787` || e != `Local\Apollo.show.8787` {
		t.Fatalf("%q %q", m, e)
	}
	m2, e2 := instanceNames(9000)
	if m == m2 || e == e2 || m == e {
		t.Fatal("each port and each object needs its own name")
	}
}

func TestConfigOrderFileEnvFlags(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "apollo.json"), []byte(`{"python":"/j/py","distro":"Ubuntu","port":9000}`), 0o644)
	env := map[string]string{"APOLLO_PORT": "9100", "APOLLO_TIMEOUT": "7"}
	c, err := LoadConfig("windows", dir, []string{"-port", "9200", "-no-open"}, func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	if c.Python != "/j/py" || c.Distro != "Ubuntu" || c.Port != 9200 || c.TimeoutS != 7 || !c.NoOpen || c.Source == "" {
		t.Fatalf("%+v", c)
	}
	if _, err := LoadConfig("linux", dir, nil, func(k string) string { return map[string]string{"APOLLO_PORT": "x"}[k] }); err == nil {
		t.Fatal("a bad port must fail")
	}
	if c, err := LoadConfig("linux", t.TempDir(), nil, func(string) string { return "" }); err != nil || c.Source != "" {
		t.Fatalf("no apollo.json is fine: %v %+v", err, c)
	}
}

func TestOpenCommandPerOS(t *testing.T) {
	u := "http://127.0.0.1:8787/"
	for goos, want := range map[string][]string{
		"windows": {"rundll32", "url.dll,FileProtocolHandler", u}, "darwin": {"open", u}, "linux": {"xdg-open", u}} {
		if got := openCommand(goos, u); !reflect.DeepEqual(got, want) {
			t.Fatalf("%s: %v", goos, got)
		}
	}
}

// ---- paths --------------------------------------------------------------------------------------------------------

func TestPathMapping(t *testing.T) {
	cases := []struct{ goos, in, want string }{
		{"windows", "/mnt/e/Documents/development/stable/hydra-mcp-testing/runs/x", `E:\Documents\development\stable\hydra-mcp-testing\runs\x`},
		{"windows", "/mnt/c", `C:\`},
		{"windows", "/home/manager/hydra-ws/x", "/home/manager/hydra-ws/x"},
		{"windows", "/mnt/wsl/x", "/mnt/wsl/x"},
		{"linux", "/mnt/e/a", "/mnt/e/a"},
		{"darwin", "/mnt/e/a", "/mnt/e/a"},
	}
	for _, c := range cases {
		if got := ToHost(c.goos, c.in); got != c.want {
			t.Errorf("ToHost(%s, %s) = %s", c.goos, c.in, got)
		}
	}
	for in, want := range map[string]string{
		`E:\Documents\x\pollard-smoke.yaml`: "/mnt/e/Documents/x/pollard-smoke.yaml",
		`e:/a/b/`:                           "/mnt/e/a/b",
		`E:\`:                               "/mnt/e",
		"/mnt/e/a":                          "/mnt/e/a",
		"relative/x":                        "relative/x",
	} {
		if got := ToWSL(in); got != want {
			t.Errorf("ToWSL(%s) = %s", in, got)
		}
	}
	if managerPath("darwin", `E:\x`) != `E:\x` || managerPath("linux", `E:\x`) != "/mnt/e/x" {
		t.Error("managerPath")
	}
	// a round trip
	p := "/mnt/e/Documents/development/stable/apollo/apollo.py"
	if ToWSL(ToHost("windows", p)) != p {
		t.Error("round trip")
	}
}

// ---- calling the manager ------------------------------------------------------------------------------------------

func fakeExec(out, errb string, code int, wait time.Duration) func(context.Context, []string) ([]byte, []byte, int, error) {
	return func(ctx context.Context, argv []string) ([]byte, []byte, int, error) {
		select {
		case <-time.After(wait):
		case <-ctx.Done():
			return nil, nil, -1, ctx.Err()
		}
		return []byte(out), []byte(errb), code, nil
	}
}

func TestCallReadsJSONWhateverTheExitCode(t *testing.T) {
	b := &Backend{GOOS: "linux", Python: "p", Manager: "f", Timeout: time.Second}
	b.Exec = fakeExec(`{"ok": false, "why": "no run x"}`+"\n", "", 2, 0)
	raw, err := b.Call(context.Background(), 0, "status", "x", "--json")
	if err != nil || !strings.Contains(string(raw), "no run x") {
		t.Fatalf("%s %v", raw, err)
	}
	b.Exec = fakeExec("", "Traceback...\nValueError: boom\n", 1, 0)
	if _, err := b.Call(context.Background(), 0, "ls", "--json"); err == nil || !strings.Contains(err.Error(), "exit 1: Traceback... | ValueError: boom") {
		t.Fatalf("%v", err)
	}
	b.Timeout = 50 * time.Millisecond
	b.Exec = fakeExec("[]", "", 0, 2*time.Second)
	if _, err := b.Call(context.Background(), 0, "ls", "--json"); err == nil || !strings.Contains(err.Error(), "no answer in 50ms") {
		t.Fatalf("%v", err)
	}
}

func TestWSLMessagesInUTF16AreRead(t *testing.T) {
	msg := "There is no distribution with the supplied name.\r\n"
	var b []byte
	for _, r := range msg {
		b = append(b, byte(r), 0)
	}
	if got := decodeOutput(b); got != msg {
		t.Fatalf("%q", got)
	}
	if got := decodeOutput(append([]byte{0xff, 0xfe}, b...)); got != msg {
		t.Fatalf("with a byte order mark: %q", got)
	}
	if decodeOutput([]byte("plain\n")) != "plain\n" {
		t.Fatal("utf-8 stays")
	}
}

// ---- the manager's JSON, from fixtures ----------------------------------------------------------------------------

func TestParseRuns(t *testing.T) {
	rows, err := ParseRuns("windows", fixture(t, "ls.json"))
	if err != nil || len(rows) != 3 {
		t.Fatalf("%v %d", err, len(rows))
	}
	var smoke *Summary
	for i := range rows {
		if rows[i].ID == "2026-11-24-dry-smoke-2" {
			smoke = &rows[i]
		}
	}
	if smoke == nil || smoke.Kind != "pageflow" || smoke.Status != "done" || !smoke.Dry || smoke.Calls.Hydra != 79 {
		t.Fatalf("%+v", smoke)
	}
	if *smoke.Progress.Fraction != 1.0 || *smoke.Cost.Haiku[0] != 31 || *smoke.Cost.Haiku[1] != 80 {
		t.Fatalf("progress/cost: %+v", smoke.Cost)
	}
	if !strings.HasPrefix(smoke.RunFolderHost, `E:\Documents\development\stable\apollo\dry\runs\`) {
		t.Fatalf("host path: %s", smoke.RunFolderHost)
	}
	lin, _ := ParseRuns("linux", fixture(t, "ls.json"))
	if lin[0].RunFolderHost != lin[0].RunFolder {
		t.Fatal("linux keeps the path")
	}
	if _, err := ParseRuns("linux", []byte(`{"not":"a list"}`)); err == nil {
		t.Fatal("a wrong shape must fail")
	}
}

func TestParseStatus(t *testing.T) {
	st, out, err := ParseStatus("linux", fixture(t, "status.json"))
	if err != nil || out != nil {
		t.Fatal(err, out)
	}
	if st.ID != "2026-11-24-dry-smoke-2" || len(st.Tasks) != 1 || st.Tasks[0].StepsDone != 10 || len(st.SlowestCalls) != 5 {
		t.Fatalf("%+v", st)
	}
	if st.Questions["open"] != 0 || len(st.Command) == 0 {
		t.Fatalf("%+v", st)
	}
	_, out, err = ParseStatus("linux", fixture(t, "status-missing.json"))
	if err != nil || out == nil || out.OK || out.Why != "no run nothing-like-this" {
		t.Fatalf("%v %+v", err, out)
	}
}

func TestParseEveryView(t *testing.T) {
	for _, tab := range []string{"run", "found", "questions", "calls", "report"} {
		v, out, err := ParseView("windows", fixture(t, "view-"+tab+".json"))
		if err != nil || out != nil || v.Tab != tab {
			t.Fatalf("%s: %v %+v", tab, err, out)
		}
		if len(v.Pages) != 3 || v.Pages[0] != (PageCell{20, "done"}) {
			t.Fatalf("%s pages: %+v", tab, v.Pages)
		}
		if !strings.HasPrefix(v.RunFolderHost, `E:\`) {
			t.Fatalf("%s: %s", tab, v.RunFolderHost)
		}
		switch tab {
		case "run":
			var rows []RunRow
			json.Unmarshal(v.Rows, &rows)
			if len(rows) == 0 || rows[0].Verb != "Ran" || rows[0].Object != "run done" || *v.Total < len(rows) {
				t.Fatalf("run: %+v", rows[:1])
			}
		case "found":
			if len(v.Groups) == 0 || v.Groups[0].Page != 20 || len(v.Groups[0].Records) != v.Groups[0].Found || *v.Total == 0 {
				t.Fatalf("found: %+v", v.Groups)
			}
		case "questions":
			if *v.Open != 0 || string(v.Rows) != "[]" {
				t.Fatalf("questions: %s", v.Rows)
			}
		case "calls":
			var rows []CallRow
			json.Unmarshal(v.Rows, &rows)
			slow := 0
			for _, c := range rows {
				if c.Slow {
					slow++
				}
			}
			if slow != 5 || len(v.Slowest) != 5 || *v.Total != len(rows) {
				t.Fatalf("calls: slow %d, %d rows", slow, len(rows))
			}
		case "report":
			if v.Blocks[0].Type != "h1" || !strings.HasSuffix(v.PathHost, `\report.md`) {
				t.Fatalf("report: %+v %s", v.Blocks[0], v.PathHost)
			}
			tables := 0
			for _, b := range v.Blocks {
				if b.Type == "table" && len(b.Rows) > 1 {
					tables++
				}
			}
			if tables == 0 {
				t.Fatal("report: no table")
			}
		}
	}
	if _, _, err := ParseView("linux", []byte(`{"tab":"calls","rows":[{"ok":"yes"}]}`)); err == nil {
		t.Fatal("a changed row shape must fail")
	}
}

func TestParseConfigs(t *testing.T) {
	c, err := ParseConfigs("windows", fixture(t, "configs.json"))
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, r := range c.Recipes {
		names[r.Name] = true
		if !strings.HasPrefix(r.PathHost, `E:`) || !strings.HasSuffix(r.PathHost, `.yaml`) {
			t.Fatalf("%+v", r)
		}
	}
	if !names["full"] || !names["smoke"] || !names["lag"] || len(c.Sources) < 2 || c.Sources[0].Title == "" {
		t.Fatalf("%v %+v", names, c.Sources)
	}
	if len(c.Briefs) == 0 || c.Briefs[0].Kind != "mcp-session" {
		t.Fatalf("%+v", c.Briefs)
	}
	if _, err := ParseConfigs("linux", []byte(`[]`)); err == nil {
		t.Fatal("the old flat list must fail")
	}
}

func TestParseDetail(t *testing.T) {
	got, out, err := ParseDetail([]byte(`{"name":"full","agents":[]}`))
	if err != nil || out != nil || !strings.Contains(string(got), "agents") {
		t.Fatal(err, out)
	}
	_, out, err = ParseDetail([]byte(`{"ok":false,"why":"no recipe x"}`))
	if err != nil || out == nil || out.Why != "no recipe x" {
		t.Fatal(err, out)
	}
}

func TestNewFields(t *testing.T) {
	rows, err := ParseRuns("linux", fixture(t, "ls.json"))
	if err != nil || len(rows[0].Cells) != 5 || rows[0].Cells[2] != (Cell{22, "done", 6}) || rows[0].Progress.PagesRead != 5 {
		t.Fatalf("%v %+v", err, rows[0].Cells)
	}
	v, _, err := ParseView("linux", fixture(t, "view-run.json"))
	if err != nil || v.Route == nil || len(v.Route.Nodes) < 8 || v.Route.Nodes[0].Key != "preflight" || len(v.Heat) != 3 || v.Heat[0].Quotes == 0 {
		t.Fatalf("%v %+v", err, v.Route)
	}
	v, _, err = ParseView("linux", fixture(t, "view-report.json"))
	if err != nil || v.Measures == nil || len(v.Measures.Targets) < 5 || len(v.Measures.Passes) == 0 || len(v.Measures.Tools) == 0 || len(v.Measures.Cost) != 2 {
		t.Fatalf("%v %+v", err, v.Measures)
	}
	if _, _, err := ParseView("linux", []byte(`{"tab":"run","pages":[],"heat":[{"page":"x"}]}`)); err == nil {
		t.Fatal("a changed heat row must fail")
	}
}

func TestStartArgs(t *testing.T) {
	args, err := StartArgs("windows", StartRequest{Config: `E:\x\pollard-smoke.yaml`, Name: "smoke a; rm -rf /", Model: "claude-haiku-5-5",
		Dry: true, Pages: "20-22", Budget: "None"})
	want := []string{"start", "/mnt/e/x/pollard-smoke.yaml", "--json", "--name", "smoke a; rm -rf /", "--model",
		"claude-haiku-5-5", "--pages", "20-22", "--budget", "none", "--dry"}
	if err != nil || !reflect.DeepEqual(args, want) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = StartArgs("linux", StartRequest{Recipe: "full", Source: "pollard", Pages: "20-22", Dry: true})
	if err != nil || !reflect.DeepEqual(args, []string{"start", "--recipe", "full", "--source", "pollard", "--json", "--pages", "20-22", "--dry"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = StartArgs("linux", StartRequest{Recipe: "full", Source: "pollard", Models: "haiku=claude-opus-5-5", Without: "lag,rate"})
	if err != nil || !reflect.DeepEqual(args, []string{"start", "--recipe", "full", "--source", "pollard", "--json", "--models", "haiku=claude-opus-5-5", "--without", "lag,rate"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = StartArgs("linux", StartRequest{Workflow: "review", Archive: "/a/b", Model: "claude-sonnet-5-5", Dry: true})
	if err != nil || !reflect.DeepEqual(args, []string{"start", "--workflow", "review", "--archive", "/a/b", "--json", "--model", "claude-sonnet-5-5", "--dry"}) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range []StartRequest{{Workflow: "review"}, {Workflow: "-x", Archive: "/a"}, {Workflow: "review", Archive: "--x"},
		{Recipe: "full", Source: "p", Models: "x y"}, {Recipe: "full", Source: "p", Without: "reader"}, {Config: "/c", Models: "haiku=x"}} {
		if _, err := StartArgs("linux", bad); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
	for _, bad := range []StartRequest{{Recipe: "full"}, {Recipe: "-x", Source: "p"}, {Recipe: "full", Source: "a b"}, {Recipe: "../x", Source: "p"}} {
		if _, err := StartArgs("linux", bad); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
	if args, _ := StartArgs("linux", StartRequest{Config: "/c.yaml"}); !reflect.DeepEqual(args, []string{"start", "/c.yaml", "--json"}) {
		t.Fatalf("live, no options: %v", args)
	}
	for _, bad := range []StartRequest{{}, {Config: "--fast"}, {Config: "/c", Name: "--dry"}, {Config: "/c", Pages: "20-x"},
		{Config: "/c", Budget: "lots"}, {Config: "/c", Model: "a b"}, {Config: "/c", Name: "two\nlines"}} {
		if _, err := StartArgs("linux", bad); err == nil {
			t.Errorf("accepted %+v", bad)
		}
	}
}

// ---- the server: Host, token, the API -----------------------------------------------------------------------------

type recorder struct{ calls [][]string }

func testServer(t *testing.T, out map[string]string) (*Server, *recorder) {
	t.Helper()
	rec := &recorder{}
	b := &Backend{GOOS: "linux", Python: "p", Manager: "f", Timeout: time.Second}
	b.Exec = func(ctx context.Context, argv []string) ([]byte, []byte, int, error) {
		rec.calls = append(rec.calls, argv)
		return []byte(out[argv[2]]), nil, 0, nil
	}
	s, err := NewServer("linux", 8787, b, nil)
	if err != nil {
		t.Fatal(err)
	}
	return s, rec
}

func do(h http.Handler, method, path, host string, hdr map[string]string, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Host = host
	for k, v := range hdr {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestHostCheck(t *testing.T) {
	s, _ := testServer(t, map[string]string{"ls": "[]"})
	h := s.Handler()
	for host, code := range map[string]int{"127.0.0.1:8787": 200, "localhost:8787": 200, "evil.example:8787": 403,
		"127.0.0.1:9999": 403, "": 403, "127.0.0.1": 403} {
		if w := do(h, "GET", "/api/runs", host, nil, ""); w.Code != code {
			t.Errorf("host %q: %d", host, w.Code)
		}
	}
}

func TestTokenOnEveryPost(t *testing.T) {
	s, rec := testServer(t, map[string]string{"stop": `{"ok": true, "id": "r1", "how": "SIGTERM to process group 5"}`})
	h := s.Handler()
	const host = "127.0.0.1:8787"
	if len(s.Token) != 64 {
		t.Fatalf("token %q", s.Token)
	}
	if other, _ := testServer(t, nil); other.Token == s.Token {
		t.Fatal("a token per launch")
	}
	page := do(h, "GET", "/", host, nil, "")
	if page.Code != 200 || !strings.Contains(page.Body.String(), `content="`+s.Token+`"`) || strings.Contains(page.Body.String(), "{{TOKEN}}") {
		t.Fatal("the page carries the token")
	}
	if !strings.Contains(page.Header().Get("Content-Security-Policy"), "default-src 'self'") {
		t.Fatal("CSP")
	}
	for name, hdr := range map[string]map[string]string{
		"none":   nil,
		"wrong":  {tokenHeader: strings.Repeat("0", 64)},
		"origin": {tokenHeader: s.Token, "Origin": "http://evil.example"},
	} {
		if w := do(h, "POST", "/api/stop/r1", host, hdr, "{}"); w.Code != 403 {
			t.Errorf("%s: %d", name, w.Code)
		}
	}
	if len(rec.calls) != 0 {
		t.Fatal("nothing reached the manager")
	}
	w := do(h, "POST", "/api/stop/r1", host, map[string]string{tokenHeader: s.Token, "Origin": "http://127.0.0.1:8787"}, "{}")
	if w.Code != 200 || !reflect.DeepEqual(rec.calls[0], []string{"p", "f", "stop", "r1", "--json"}) {
		t.Fatalf("%d %s %v", w.Code, w.Body, rec.calls)
	}
	if w := do(h, "POST", "/api/stop/-x", host, map[string]string{tokenHeader: s.Token}, ""); w.Code != 400 {
		t.Fatalf("a bad id: %d", w.Code)
	}
}

func TestAPIRoutes(t *testing.T) {
	out := map[string]string{
		"ls":      string(fixture(t, "ls.json")),
		"status":  string(fixture(t, "status.json")),
		"view":    string(fixture(t, "view-calls.json")),
		"configs": string(fixture(t, "configs.json")),
		"recipe":  `{"name":"full","agents":[]}`,
		"prompt":  `{"ok":false,"why":"no prompt x"}`,
		"start":   `{"ok": false, "why": "x.json: a page-flow config (.yaml) or a brief (.md)"}`,
	}
	s, rec := testServer(t, out)
	h := s.Handler()
	const host = "127.0.0.1:8787"
	for _, p := range []string{"/api/runs", "/api/run/2026-11-24-dry-smoke-2", "/api/view/2026-11-24-dry-smoke-2?tab=calls", "/api/configs", "/api/recipe/full?source=pollard", "/app.js", "/app.css", "/launch/launch.js", "/launch/launch.css", "/vendor/gsap-3.13.0/gsap.min.js", "/vendor/gsap-3.13.0/DrawSVGPlugin.min.js", "/vendor/gsap-3.13.0/Flip.min.js", "/toast.js", "/motion.js", "/flowroute.js"} {
		if w := do(h, "GET", p, host, nil, ""); w.Code != 200 {
			t.Errorf("%s: %d %s", p, w.Code, w.Body)
		}
	}
	if w := do(h, "GET", "/api/prompt/x", host, nil, ""); w.Code != 404 {
		t.Errorf("a missing prompt: %d", w.Code)
	}
	if w := do(h, "GET", "/api/recipe/-x", host, nil, ""); w.Code != 400 {
		t.Errorf("a bad recipe name: %d", w.Code)
	}
	if last := rec.calls[len(rec.calls)-2]; !reflect.DeepEqual(last, []string{"p", "f", "recipe", "full", "--json", "--source", "pollard"}) {
		t.Errorf("recipe call: %v", last)
	}
	if w := do(h, "GET", "/api/view/x?tab=nope", host, nil, ""); w.Code != 400 {
		t.Errorf("bad tab: %d", w.Code)
	}
	n := len(rec.calls)
	do(h, "GET", "/api/runs", host, nil, "")
	if len(rec.calls) != n {
		t.Error("a second read within the second shares the first call")
	}
	w := do(h, "POST", "/api/start", host, map[string]string{tokenHeader: s.Token},
		`{"config":"/x.json","dry":true,"pages":"20-22"}`)
	if w.Code != 409 || !strings.Contains(w.Body.String(), "a page-flow config") {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	last := rec.calls[len(rec.calls)-1]
	if !reflect.DeepEqual(last, []string{"p", "f", "start", "/x.json", "--json", "--pages", "20-22", "--dry"}) {
		t.Fatalf("%v", last)
	}
	if w := do(h, "POST", "/api/start", host, map[string]string{tokenHeader: s.Token}, `{"config":"/c.yaml","pages":"x"}`); w.Code != 400 {
		t.Fatalf("bad pages: %d", w.Code)
	}
}

func TestManagerErrorsReachThePage(t *testing.T) {
	b := &Backend{GOOS: "linux", Python: "p", Manager: "f", Timeout: time.Second,
		Exec: fakeExec("", "python: can't open file 'f'\n", 2, 0)}
	s, _ := NewServer("linux", 8787, b, nil)
	w := do(s.Handler(), "GET", "/api/runs", "127.0.0.1:8787", nil, "")
	var body map[string]string
	json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != 502 || !strings.Contains(body["error"], "can't open file") {
		t.Fatalf("%d %v", w.Code, body)
	}
}

func TestToolArgs(t *testing.T) {
	args, err := ToolArgs("windows", ToolRequest{Tool: "ocr", Source: "pollard", Pages: "3-4", Lang: "fr"}, false)
	if err != nil || !reflect.DeepEqual(args, []string{"toolstart", "--tool", "ocr", "--source", "pollard", "--pages", "3-4", "--lang", "fr", "--json"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = ToolArgs("windows", ToolRequest{Tool: "check", Archive: `E:\x\dry\archives\a\b`}, false)
	if err != nil || !reflect.DeepEqual(args, []string{"toolstart", "--tool", "check", "--archive", "/mnt/e/x/dry/archives/a/b", "--json"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = ToolArgs("linux", ToolRequest{Tool: "text", Source: "pollard", Pages: "3-4"}, true)
	if err != nil || !reflect.DeepEqual(args, []string{"toolget", "--tool", "text", "--source", "pollard", "--json"}) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range []ToolRequest{{Tool: "rm"}, {Tool: "ocr"}, {Tool: "ocr", Source: "-x"}, {Tool: "check"}, {Tool: "check", Archive: "--x"},
		{Tool: "ocr", Source: "p", Pages: "1;2"}, {Tool: "ocr", Source: "p", Lang: "French"}, {Tool: "build", Archive: "/a"}} {
		if _, err := ToolArgs("linux", bad, false); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
	if !workerCalls["toolget"] || workerCalls["toolstart"] || workerCalls["toolrun"] {
		t.Error("only toolget is the worker's")
	}
}

func TestReadyArgs(t *testing.T) {
	args, err := ReadyArgs("windows", "pollard", "", "3-12", `E:\x\dry\archives\a\b`)
	want := []string{"ready", "--source", "pollard", "--recipe", "full", "--pages", "3-12", "--archive", "/mnt/e/x/dry/archives/a/b", "--json"}
	if err != nil || !reflect.DeepEqual(args, want) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range [][]string{{"", "full", "", ""}, {"p", "-x", "", ""}, {"p", "full", "1;2", ""}, {"p", "full", "", "--x"}} {
		if _, err := ReadyArgs("linux", bad[0], bad[1], bad[2], bad[3]); err == nil {
			t.Errorf("%v must fail", bad)
		}
	}
	if workerCalls["ready"] {
		t.Error("ready runs in its own process")
	}
}

func TestStartArgsFlow(t *testing.T) {
	args, err := StartArgs("windows", StartRequest{Flow: "read", Source: "pollard", Pages: "1-11", Budget: "1", Lag: true, Without: "rate,escalate",
		Archive: "draft", Dry: true})
	want := []string{"start", "--flow", "read", "--source", "pollard", "--json", "--lag", "--archive", "draft", "--pages", "1-11", "--budget", "1",
		"--without", "rate,escalate", "--dry"}
	if err != nil || !reflect.DeepEqual(args, want) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = StartArgs("windows", StartRequest{Flow: "stubs", Archive: `E:\a\archives\r\x`})
	if err != nil || !reflect.DeepEqual(args, []string{"start", "--flow", "stubs", "--json", "--archive", "/mnt/e/a/archives/r/x"}) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range []StartRequest{{Flow: "read"}, {Flow: "stubs"}, {Flow: "-x", Source: "p"}, {Flow: "read", Source: "p", Archive: "--x"}} {
		if _, err := StartArgs("linux", bad); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
}

func multipartBody(t *testing.T, files map[string]string) (string, string) {
	t.Helper()
	var b strings.Builder
	mw := multipart.NewWriter(&b)
	for name, content := range files {
		fw, _ := mw.CreateFormFile("files", name)
		fw.Write([]byte(content))
	}
	mw.Close()
	return mw.FormDataContentType(), b.String()
}

func TestUploadSavesToIncomingAndAsksTheManagerToTakeItIn(t *testing.T) {
	dir := t.TempDir()
	var seen []string
	b := &Backend{GOOS: "linux", Python: "p", Manager: dir + "/apollo.py", Timeout: time.Second}
	b.Exec = func(ctx context.Context, argv []string) ([]byte, []byte, int, error) {
		seen = append(seen, argv[3])
		if _, err := os.Stat(argv[3]); err != nil {
			t.Errorf("the file must be there when the manager is asked: %v", err)
		}
		return []byte(`{"ok": true, "slug": "minutes", "needs": ["context"]}`), nil, 0, nil
	}
	s, _ := NewServer("linux", 8787, b, nil)
	ct, body := multipartBody(t, map[string]string{"../../Minutes.txt": "hello", "run.exe": "MZ"})
	r := httptest.NewRequest("POST", "/api/sources/upload", strings.NewReader(body))
	r.Host = "127.0.0.1:8787"
	r.Header.Set("Content-Type", ct)
	r.Header.Set(tokenHeader, s.Token)
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	var got struct{ Results []UploadResult }
	json.Unmarshal(w.Body.Bytes(), &got)
	if w.Code != 200 || len(got.Results) != 2 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	ok, refused := got.Results[1], got.Results[0]
	if ok.Name != "Minutes.txt" {
		ok, refused = refused, ok
	}
	if !ok.OK || ok.Slug != "minutes" || refused.OK || !strings.Contains(refused.Why, "PDF, TXT") {
		t.Fatalf("%+v %+v", ok, refused)
	}
	if len(seen) != 1 || !strings.HasPrefix(seen[0], dir+"/sources/incoming/") || !strings.HasSuffix(seen[0], "/Minutes.txt") {
		t.Fatalf("%v", seen)
	}
}

func TestSourceEditsAndReadsGoToTheManager(t *testing.T) {
	s, rec := testServer(t, map[string]string{"sourceupdate": `{"ok": true}`, "sourceremove": `{"ok": true}`,
		"sourcelist": `{"ok": true, "dropped": []}`, "pagetext": `{"ok": false, "why": "the source has 3 pages"}`,
		"collections": `{"ok": true, "collections": []}`})
	h, host, hdr := s.Handler(), "127.0.0.1:8787", map[string]string{tokenHeader: s.Token}
	if w := do(h, "POST", "/api/source/minutes", host, hdr, `{"title":"The Minutes","brief":"a\nb"}`); w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"sourceupdate", "minutes", "--fields", `{"title":"The Minutes","brief":"a\nb"}`}) {
		t.Fatalf("%q", last)
	}
	for _, bad := range []string{`{"file":"/etc/passwd"}`, `nonsense`} {
		if w := do(h, "POST", "/api/source/minutes", host, hdr, bad); w.Code != 400 {
			t.Errorf("%s: %d", bad, w.Code)
		}
	}
	if w := do(h, "POST", "/api/source/..%2Fx/remove", host, hdr, ""); w.Code != 400 && w.Code != 404 {
		t.Errorf("a bad slug: %d", w.Code)
	}
	if w := do(h, "POST", "/api/source/minutes/remove", host, hdr, ""); w.Code != 200 {
		t.Errorf("remove: %d", w.Code)
	}
	if w := do(h, "GET", "/api/sources", host, nil, ""); w.Code != 200 {
		t.Errorf("list: %d", w.Code)
	}
	if w := do(h, "GET", "/api/pagetext?source=pollard&page=9", host, nil, ""); w.Code != 404 || !strings.Contains(w.Body.String(), "3 pages") {
		t.Errorf("pagetext: %d %s", w.Code, w.Body)
	}
	if w := do(h, "GET", "/api/pagetext?source=pollard&page=x", host, nil, ""); w.Code != 400 {
		t.Errorf("pagetext page: %d", w.Code)
	}
	if w := do(h, "GET", "/api/collections?archive=/a/b&collection=actors&offset=200", host, nil, ""); w.Code != 200 {
		t.Errorf("collections: %d", w.Code)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"collections", "/a/b", "--collection", "actors", "--offset", "200"}) {
		t.Fatalf("%q", last)
	}
	if w := do(h, "GET", "/api/collections?archive=--x", host, nil, ""); w.Code != 400 {
		t.Errorf("collections archive: %d", w.Code)
	}
}

func TestProofreadAndLogdbArgs(t *testing.T) {
	args, err := ProofreadArgs(ProofreadRequest{Source: "pollard", Pages: " 7-9 "}, false)
	if err != nil || !reflect.DeepEqual(args, []string{"proofreadstart", "--source", "pollard", "--pages", "7-9"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, err = ProofreadArgs(ProofreadRequest{Source: "pollard", Page: 8, Action: "accept"}, true)
	if err != nil || !reflect.DeepEqual(args, []string{"proofreadact", "--source", "pollard", "--page", "8", "--action", "accept"}) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range []ProofreadRequest{{}, {Source: "-x", Pages: "1"}, {Source: "p", Pages: "1;2"}, {Source: "p"}, {Source: "p", Pages: "a-b"}} {
		if _, err := ProofreadArgs(bad, false); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
	for _, bad := range []ProofreadRequest{{Source: "p", Page: 0, Action: "accept"}, {Source: "p", Page: 1, Action: "burn"}, {Source: "p", Page: 1}} {
		if _, err := ProofreadArgs(bad, true); err == nil {
			t.Errorf("%+v must fail", bad)
		}
	}
	got, err := LogdbArgs("select 'a b' as \"x\";")
	if err != nil || got[0] != "logdb" || got[1] != "--sql64" || strings.ContainsAny(got[2], " '\"=+/") {
		t.Fatalf("%v %v", got, err)
	}
	if _, err := LogdbArgs("  "); err == nil {
		t.Error("an empty query must fail")
	}
	if !workerCalls["proofreadget"] || !workerCalls["logdb"] || workerCalls["proofreadstart"] || workerCalls["proofreadact"] {
		t.Error("the worker answers only the reads")
	}
}

// limits: a start without run-level caps passes --no-limits.
func TestStartArgsNoLimits(t *testing.T) {
	args, err := StartArgs("linux", StartRequest{Flow: "read", Source: "pollard", NoLimits: true})
	if err != nil || !reflect.DeepEqual(args[len(args)-1:], []string{"--no-limits"}) {
		t.Fatalf("%v %v", args, err)
	}
	args, _ = StartArgs("linux", StartRequest{Flow: "read", Source: "pollard"})
	for _, a := range args {
		if a == "--no-limits" {
			t.Fatalf("%v", args)
		}
	}
}

// every key runfiles.heat() emits (tests/test_view.py checks the same fixture) must survive the round trip through HeatRow
func TestHeatRowKeepsEveryKey(t *testing.T) {
	var want map[string]json.RawMessage
	raw := fixture(t, "heat-row.json")
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	var row HeatRow
	if err := json.Unmarshal(raw, &row); err != nil {
		t.Fatal(err)
	}
	back, _ := json.Marshal(row)
	var got map[string]json.RawMessage
	if err := json.Unmarshal(back, &got); err != nil {
		t.Fatal(err)
	}
	for k := range want {
		if _, ok := got[k]; !ok {
			t.Errorf("HeatRow drops %q", k)
		}
	}
	if row.Groups["people"] != 2 || row.Groups["ties"] != 1 {
		t.Fatalf("%+v", row.Groups)
	}
}

// tool args: a request's declared arguments go to the manager as one JSON object, and only an object does
func TestToolArgsPassTheDeclaredArguments(t *testing.T) {
	args, err := ToolArgs("linux", ToolRequest{Tool: "ocr", Source: "pollard", Pages: "1-2", Args: json.RawMessage(`{"pages": "1,40-45", "dpi": 150}`)}, false)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(args, " "); got != `toolstart --tool ocr --source pollard --args {"dpi":150,"pages":"1,40-45"} --json` {
		t.Errorf("args = %s", got)
	}
	for _, bad := range []string{`[1]`, `"x"`, `{"a":`, `{"note":"` + strings.Repeat("x", 9000) + `"}`} {
		if _, err := ToolArgs("linux", ToolRequest{Tool: "text", Source: "pollard", Args: json.RawMessage(bad)}, false); err == nil {
			t.Errorf("accepted %.20s", bad)
		}
	}
	if !reToolName.MatchString("catalogue") || reToolName.MatchString("rm") {
		t.Error("tool names")
	}
}

func TestStartArgsAgents(t *testing.T) {
	args, err := StartArgs("linux", StartRequest{Flow: "read", Source: "pollard", Agents: map[string]int{"sonnet": 3, "haiku": 6}})
	if err != nil || !reflect.DeepEqual(args[len(args)-2:], []string{"--agents", "haiku=6,sonnet=3"}) {
		t.Fatalf("%v %v", args, err)
	}
	for _, bad := range []StartRequest{{Flow: "read", Source: "p", Agents: map[string]int{"gpt": 1}}, {Flow: "read", Source: "p", Agents: map[string]int{"haiku": -1}}} {
		if _, err := StartArgs("linux", bad); err == nil {
			t.Fatalf("accepted %v", bad)
		}
	}
}
