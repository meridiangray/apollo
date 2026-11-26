package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
	"unicode/utf16"
)

// Backend runs the Python manager, apollo.py, and reads its JSON. On Windows the manager runs in WSL through
// wsl.exe; on Linux and macOS it runs directly.
type Backend struct {
	GOOS    string
	Python  string
	Manager string
	Distro  string
	Timeout time.Duration
	// Exec runs argv and returns stdout, stderr and the exit code (a test replaces it).
	Exec func(ctx context.Context, argv []string) (stdout, stderr []byte, code int, err error)
	// ExecIn is Exec with standard input (CallIn); nil runs the process (a test replaces it).
	ExecIn func(ctx context.Context, argv []string, stdin []byte) (stdout, stderr []byte, code int, err error)
	// Worker, when set, answers the quick read-only calls from one long-lived manager (worker.go).
	Worker *worker
}

func NewBackend(goos string, c Config) *Backend {
	b := &Backend{GOOS: goos, Python: c.Python, Manager: c.Manager, Distro: c.Distro, Timeout: c.Timeout(),
		Exec: runProcess}
	b.Worker = &worker{argv: b.Command("serve")}
	return b
}

// Command is the argv that runs `apollo.py args...`. On Windows: `wsl.exe -d <distro> --exec <python> <apollo.py>
// args...`; --exec runs the program with no shell between, so no argument is ever read as shell syntax.
func (b *Backend) Command(args ...string) []string {
	var argv []string
	if b.GOOS == "windows" {
		argv = []string{"wsl.exe", "-d", b.Distro, "--exec", b.Python, b.Manager}
	} else {
		argv = []string{b.Python, b.Manager}
	}
	return append(argv, args...)
}

func runProcess(ctx context.Context, argv []string) ([]byte, []byte, int, error) {
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	hideWindow(cmd)
	cmd.WaitDelay = 3 * time.Second
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	code := 0
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code, err = ee.ExitCode(), nil
	}
	return out.Bytes(), errb.Bytes(), code, err
}

// ManagerError is a call that gave no JSON: the manager failed, timed out or could not be started.
type ManagerError struct{ Msg string }

func (e *ManagerError) Error() string { return e.Msg }

// Call runs `apollo.py args...` with the backend's timeout (or `timeout`, when longer) and returns its JSON output,
// whatever its exit code: the manager says why in JSON (`{"ok": false, "why": ...}`).
func (b *Backend) Call(ctx context.Context, timeout time.Duration, args ...string) (json.RawMessage, error) {
	if timeout < b.Timeout {
		timeout = b.Timeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	argv := b.Command(args...)
	what := "apollo.py " + strings.Join(args[:min(len(args), 2)], " ")
	var out, errb []byte
	var code int
	var err error
	if b.Worker != nil && len(args) > 0 && workerCalls[args[0]] {
		out, code, err = b.Worker.call(ctx, args)
		if err != nil && ctx.Err() == nil { // the worker could not answer: one process, as before
			out, errb, code, err = b.Exec(ctx, argv)
		}
	} else {
		out, errb, code, err = b.Exec(ctx, argv)
	}
	return finish(ctx, timeout, what, out, errb, code, err)
}

// CallIn is Call with `stdin` given to the manager, always in a process of its own (never the worker, whose stdin is
// its request pipe): a key's value travels this way, never on a command line.
func (b *Backend) CallIn(ctx context.Context, timeout time.Duration, stdin []byte, args ...string) (json.RawMessage, error) {
	if timeout < b.Timeout {
		timeout = b.Timeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	run := b.ExecIn
	if run == nil {
		run = runProcessIn
	}
	out, errb, code, err := run(ctx, b.Command(args...), stdin)
	return finish(ctx, timeout, "apollo.py "+strings.Join(args[:min(len(args), 1)], " "), out, errb, code, err)
}

func runProcessIn(ctx context.Context, argv []string, stdin []byte) ([]byte, []byte, int, error) {
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	hideWindow(cmd)
	cmd.WaitDelay = 3 * time.Second
	cmd.Stdin = bytes.NewReader(stdin)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	code := 0
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code, err = ee.ExitCode(), nil
	}
	return out.Bytes(), errb.Bytes(), code, err
}

// finish reads a manager call's output: its JSON, or a ManagerError saying why there is none.
func finish(ctx context.Context, timeout time.Duration, what string, out, errb []byte, code int, err error) (json.RawMessage, error) {
	if ctx.Err() == context.DeadlineExceeded {
		return nil, &ManagerError{fmt.Sprintf("%s: no answer in %s", what, timeout)}
	}
	if err != nil {
		return nil, &ManagerError{fmt.Sprintf("%s: %v", what, err)}
	}
	trimmed := bytes.TrimSpace(out)
	if len(trimmed) > 0 && (trimmed[0] == '{' || trimmed[0] == '[') && json.Valid(trimmed) {
		return json.RawMessage(trimmed), nil
	}
	msg := lastLines(decodeOutput(errb), 4)
	if msg == "" {
		msg = lastLines(decodeOutput(out), 4)
	}
	if msg == "" {
		msg = "no output"
	}
	return nil, &ManagerError{fmt.Sprintf("%s: exit %d: %s", what, code, msg)}
}

// decodeOutput reads a process's output as text: wsl.exe writes its own messages in UTF-16LE.
func decodeOutput(b []byte) string {
	if len(b) >= 2 && len(b)%2 == 0 {
		zeros := 0
		for i := 1; i < len(b); i += 2 {
			if b[i] == 0 {
				zeros++
			}
		}
		if zeros*2 >= len(b)/2 {
			u := make([]uint16, 0, len(b)/2)
			for i := 0; i+1 < len(b); i += 2 {
				u = append(u, uint16(b[i])|uint16(b[i+1])<<8)
			}
			s := string(utf16.Decode(u))
			return strings.TrimPrefix(s, string(rune(0xFEFF))) // a byte order mark
		}
	}
	return string(b)
}

func lastLines(s string, n int) string {
	var keep []string
	for _, ln := range strings.Split(strings.ReplaceAll(s, "\r", ""), "\n") {
		if strings.TrimSpace(ln) != "" {
			keep = append(keep, strings.TrimSpace(ln))
		}
	}
	if len(keep) > n {
		keep = keep[len(keep)-n:]
	}
	return strings.Join(keep, " | ")
}

// ---- the manager's JSON ---------------------------------------------------------------------------------------

type Progress struct {
	Fraction     *float64       `json:"fraction"`
	Stage        *string        `json:"stage"`
	Label        *string        `json:"label"`
	Done         *int           `json:"done"`
	Total        *int           `json:"total"`
	Pages        map[string]int `json:"pages"`
	Chunk        *Chunk         `json:"chunk"`
	PagesRead    int            `json:"pages_read"`
	PagesContent int            `json:"pages_content"`
}

type Calls struct {
	Hydra  int  `json:"hydra"`
	Errors int  `json:"errors"`
	Model  *int `json:"model"`
}

type Cost struct {
	USD       *float64   `json:"usd"`
	Haiku     []*float64 `json:"haiku"`
	Sonnet    []*float64 `json:"sonnet"`
	Turns     *int       `json:"turns"`
	BudgetUSD *float64   `json:"budget_usd"`
}

// Summary is one `ls --json` row. The *_host fields are added here: the folders as this OS shows them.
type Summary struct {
	Schema        int             `json:"schema"`
	ID            string          `json:"id"`
	Name          *string         `json:"name"`
	Kind          string          `json:"kind"`
	Model         *string         `json:"model"`
	Status        string          `json:"status"`
	Detail        *string         `json:"detail"`
	Alive         bool            `json:"alive"`
	PID           *int            `json:"pid"`
	Dry           bool            `json:"dry"`
	Fast          bool            `json:"fast"`
	Started       *string         `json:"started"`
	Ended         *string         `json:"ended"`
	ElapsedS      *float64        `json:"elapsed_s"`
	ExitCode      *int            `json:"exit_code"`
	RunFolder     string          `json:"run_folder"`
	Archive       *string         `json:"archive"`
	Config        *string         `json:"config"`
	Recipe        *string         `json:"recipe"`
	Source        *string         `json:"source"`
	Flow          *string         `json:"flow"`     // the flow's one-word name
	FlowKey       *string         `json:"flow_key"` // read, links, places, stubs or enrich
	Agents        map[string]*int `json:"agents"`   // agents per grade: haiku, sonnet, opus
	SourceTitle   *string         `json:"source_title"`
	ArchiveName   *string         `json:"archive_name"`
	Progress      Progress        `json:"progress"`
	Cells         []Cell          `json:"cells"`
	Calls         Calls           `json:"calls"`
	Cost          Cost            `json:"cost"`
	Last          string          `json:"last"`
	Limits        *bool           `json:"limits"` // limits
	Resumable     bool            `json:"resumable"`
	Now           string          `json:"now"`
	Report        *string         `json:"report"`
	RunFolderHost string          `json:"run_folder_host"`
	ArchiveHost   string          `json:"archive_host,omitempty"`
}

type Task struct {
	ID         string  `json:"id"`
	Title      string  `json:"title"`
	Status     *string `json:"status"`
	Model      *string `json:"model"`
	StepsDone  int     `json:"steps_done"`
	StepsTotal int     `json:"steps_total"`
	Last       string  `json:"last"`
}

type SlowCall struct {
	Tool string   `json:"tool"`
	MS   *float64 `json:"ms"`
	At   *string  `json:"at"`
}

type CallError struct {
	Tool  string  `json:"tool"`
	At    *string `json:"at"`
	Error string  `json:"error"`
}

// Status is `status <id> --json`: a summary and the rest.
type Status struct {
	Summary
	Questions    map[string]int `json:"questions"`
	Tasks        []Task         `json:"tasks"`
	Budgets      map[string]any `json:"budgets"`
	Command      []string       `json:"command"`
	SlowestCalls []SlowCall     `json:"slowest_calls"`
	Errors       []CallError    `json:"errors"`
}

// PageCell is one [page, status] pair of the page strip; the UI gets {page, status}.
type PageCell struct {
	Page   int    `json:"page"`
	Status string `json:"status"`
}

func (p *PageCell) UnmarshalJSON(b []byte) error {
	var pair []json.RawMessage
	if err := json.Unmarshal(b, &pair); err != nil || len(pair) != 2 {
		return fmt.Errorf("a page cell is [page, status]: %s", b)
	}
	if err := json.Unmarshal(pair[0], &p.Page); err != nil {
		return err
	}
	return json.Unmarshal(pair[1], &p.Status)
}

// Cell is one [page, status, records] of a run's pages strip.
type Cell struct {
	Page    int    `json:"page"`
	Status  string `json:"status"`
	Records int    `json:"records"`
}

func (c *Cell) UnmarshalJSON(b []byte) error {
	var t []json.RawMessage
	if err := json.Unmarshal(b, &t); err != nil || len(t) != 3 {
		return fmt.Errorf("a cell is [page, status, records]: %s", b)
	}
	if err := json.Unmarshal(t[0], &c.Page); err != nil {
		return err
	}
	if err := json.Unmarshal(t[1], &c.Status); err != nil {
		return err
	}
	return json.Unmarshal(t[2], &c.Records)
}

// HeatRow is one page of the heatmap: what the run did on it.
type HeatRow struct {
	Page     int            `json:"page"`
	Status   string         `json:"status"`
	Records  int            `json:"records"`
	Quotes   int            `json:"quotes"`
	Places   int            `json:"places"`
	Groups   map[string]int `json:"groups"` // heat groups
	Calls    int            `json:"calls"`
	Seconds  float64        `json:"seconds"`
	Problems map[string]int `json:"problems"`
	Asked    int            `json:"asked,omitempty"`   // answering: the questions put to the person on this page
	Changed  int            `json:"changed,omitempty"` // a workflow run: the records it added, changed or removed on this page
	Reason   string         `json:"reason,omitempty"`
}

type RouteNode struct {
	Key   string `json:"key"`
	Label string `json:"label"`
	Kind  string `json:"kind"`
	Pages string `json:"pages"`
	State string `json:"state"`
}

type Chunk struct {
	At int `json:"at"`
	Of int `json:"of"`
}

// Route is the run's steps; Now is the node in hand (null once the run is over); Group is the [from, to) nodes of the chunks.
type Route struct {
	Nodes []RouteNode `json:"nodes"`
	Now   *int        `json:"now"`
	Chunk *Chunk      `json:"chunk"`
	Group []int       `json:"group"`
}

// Measures are the Report's numbers.
type Measures struct {
	Targets []struct {
		Key   string `json:"key"`
		Label string `json:"label"`
		Value any    `json:"value"`
		Bar   any    `json:"bar"`
		OK    *bool  `json:"ok"`
	} `json:"targets"`
	Passes []struct {
		Key     string  `json:"key"`
		Label   string  `json:"label"`
		Seconds float64 `json:"seconds"`
	} `json:"passes"`
	WallSeconds *float64 `json:"wall_seconds"`
	Tools       []struct {
		Tool    string  `json:"tool"`
		Calls   int     `json:"calls"`
		Seconds float64 `json:"seconds"`
	} `json:"tools"`
	ToolsTotal int `json:"tools_total"`
	Slowest    []struct {
		Tool    string  `json:"tool"`
		Seconds float64 `json:"seconds"`
		At      *string `json:"at"`
	} `json:"slowest"`
	Cost []struct {
		Model string  `json:"model"`
		Label string  `json:"label"`
		USD   float64 `json:"usd"`
		Used  int     `json:"used"`
		Cap   *int    `json:"cap"`
	} `json:"cost"`
	USD     float64 `json:"usd"`
	Records *int    `json:"records"`
}

type RunRow struct {
	At         string  `json:"at"`
	Verb       string  `json:"verb"`
	Count      int     `json:"count"`
	N          any     `json:"n"`
	Page       any     `json:"page"`
	Object     string  `json:"object"`
	More       any     `json:"more"`
	Descriptor string  `json:"descriptor"`
	Who        *string `json:"who"`
	Kind       string  `json:"kind"`
	Tag        string  `json:"tag"`
}

type FoundRecord struct {
	Schema   string `json:"schema"`
	Thing    string `json:"thing"`
	Category string `json:"category"`
	State    string `json:"state"`
	Cited    bool   `json:"cited"`
	Quote    string `json:"quote"`
}

type FoundGroup struct {
	Page    int           `json:"page"`
	Found   int           `json:"found"`
	Cited   int           `json:"cited"`
	Records []FoundRecord `json:"records"`
}

type Answer struct {
	Choice any `json:"choice"`
	Text   any `json:"text"`
	By     any `json:"by"`
	At     any `json:"at"`
}

type Question struct {
	ID         string            `json:"id"`
	Title      string            `json:"title"`
	Status     string            `json:"status"`
	Kind       *string           `json:"kind"`
	Page       any               `json:"page"`
	Records    []string          `json:"records"`
	Labels     map[string]string `json:"labels"`
	Asked      *string           `json:"asked"`
	Question   string            `json:"question"`
	Quote      string            `json:"quote"`
	Options    []string          `json:"options"`
	Suggestion *string           `json:"suggestion"`
	Answer     *Answer           `json:"answer"`
	Choices    json.RawMessage   `json:"choices,omitempty"` // answering: the buttons, {choice, label}
	Group      string            `json:"group,omitempty"`   // asked (an agent) or build
}

type CallRow struct {
	At    *string  `json:"at"`
	Tool  *string  `json:"tool"`
	MS    *float64 `json:"ms"`
	OK    *bool    `json:"ok"`
	Error *string  `json:"error"`
	Slow  bool     `json:"slow"`
}

type Block struct {
	Type   string     `json:"type"`
	Text   string     `json:"text,omitempty"`
	Rows   [][]string `json:"rows,omitempty"`
	Indent int        `json:"indent,omitempty"`
}

// View is `view <id> --tab <tab> --json`: the fields of every tab; each tab fills its own.
type View struct {
	Schema        int        `json:"schema"`
	ID            string     `json:"id"`
	Kind          string     `json:"kind"`
	Tab           string     `json:"tab"`
	RunFolder     string     `json:"run_folder"`
	RunFolderHost string     `json:"run_folder_host"`
	Pages         []PageCell `json:"pages"`
	Route         *Route     `json:"route,omitempty"`
	Heat          []HeatRow  `json:"heat,omitempty"`
	Measures      *Measures  `json:"measures,omitempty"`
	// run
	Rows  json.RawMessage `json:"rows,omitempty"`
	Total *int            `json:"total,omitempty"`
	// found
	Groups  []FoundGroup `json:"groups,omitempty"`
	OnPages *int         `json:"on_pages,omitempty"`
	// questions
	Open      *int `json:"open,omitempty"`
	Answered  *int `json:"answered,omitempty"`
	Dismissed *int `json:"dismissed,omitempty"`
	// calls
	Errors  *int       `json:"errors,omitempty"`
	Slowest []SlowCall `json:"slowest,omitempty"`
	// report
	Path     *string `json:"path,omitempty"`
	PathHost string  `json:"path_host,omitempty"`
	Blocks   []Block `json:"blocks,omitempty"`
	// found, by entity (passed on as the manager wrote them)
	Entities    json.RawMessage `json:"entities,omitempty"`
	Types       json.RawMessage `json:"types,omitempty"`
	EntityCount *int            `json:"entity_count,omitempty"`
	Changes     json.RawMessage `json:"changes,omitempty"`
	// run: the live Flow card and the run's configuration
	Flow      json.RawMessage `json:"flow,omitempty"`
	Config    json.RawMessage `json:"config,omitempty"`
	FlowError string          `json:"flow_error,omitempty"`
	// raw, and one log row's or one model call's detail
	Raw    json.RawMessage `json:"raw,omitempty"`
	Detail json.RawMessage `json:"detail,omitempty"`
	Call   json.RawMessage `json:"call,omitempty"`
}

// rowsOf checks a tab's rows against that tab's row type, so a changed shape fails here, not in the page.
func rowsOf(tab string, raw json.RawMessage) error {
	if len(raw) == 0 {
		return nil
	}
	switch tab {
	case "run":
		var r []RunRow
		return json.Unmarshal(raw, &r)
	case "questions":
		var r []Question
		return json.Unmarshal(raw, &r)
	case "calls":
		var r []CallRow
		return json.Unmarshal(raw, &r)
	}
	return nil
}

type RecipeEntry struct {
	Name        string         `json:"name"`
	Path        string         `json:"path"`
	PathHost    string         `json:"path_host"`
	Description string         `json:"description"`
	Defaults    map[string]any `json:"defaults"`
}

type SourceEntry struct {
	Name      string   `json:"name"`
	Path      string   `json:"path"`
	PathHost  string   `json:"path_host"`
	Title     string   `json:"title"`
	Publisher *string  `json:"publisher"`
	File      *string  `json:"file"`
	Pages     *string  `json:"pages"`
	Reference bool     `json:"reference"`
	Dropped   bool     `json:"dropped"`
	Context   *string  `json:"context"`
	Needs     []string `json:"needs"`
}

// Configs is `configs --json`: the recipes and sources a page flow is made of, and the briefs a session runs.
type Configs struct {
	Recipes  []RecipeEntry   `json:"recipes"`
	Sources  []SourceEntry   `json:"sources"`
	Briefs   []ConfigEntry   `json:"briefs"`
	Flows    []FlowEntry     `json:"flows"`
	Contexts json.RawMessage `json:"contexts,omitempty"`
	Draft    *DraftEntry     `json:"draft,omitempty"`
}

// FlowEntry is one flow New run offers: its one-word name (Label is the same, for older readers), its domain, what it
// needs (a source or an archive) and the model grades it can use with each grade's default and largest agent count.
type FlowEntry struct {
	Key    string               `json:"key"`
	Name   string               `json:"name"`
	Label  string               `json:"label"`
	Domain string               `json:"domain"`
	Needs  string               `json:"needs"`
	After  string               `json:"after,omitempty"`
	Grades map[string]GradeSpec `json:"grades"`
}

type GradeSpec struct {
	Default int `json:"default"`
	Max     int `json:"max"`
}

// DraftEntry is Apollo's own empty archive every session starts with.
type DraftEntry struct {
	Path     string `json:"path"`
	PathHost string `json:"path_host"`
	Exists   bool   `json:"exists"`
	Name     string `json:"name"`
	Records  int    `json:"records"`
}

type ConfigEntry struct {
	Name        string         `json:"name"`
	Kind        string         `json:"kind"`
	Path        string         `json:"path"`
	PathHost    string         `json:"path_host"`
	Description string         `json:"description"`
	Defaults    map[string]any `json:"defaults"`
}

// Outcome is what start and stop say: ok, and the run, or why not.
type Outcome struct {
	OK        bool   `json:"ok"`
	ID        string `json:"id,omitempty"`
	PID       *int   `json:"pid,omitempty"`
	Kind      string `json:"kind,omitempty"`
	RunFolder string `json:"run_folder,omitempty"`
	Dry       *bool  `json:"dry,omitempty"`
	How       string `json:"how,omitempty"`
	Why       string `json:"why,omitempty"`
}

// ---- parsing, with the host's paths added ----------------------------------------------------------------------

func strp(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func ParseRuns(goos string, raw []byte) ([]Summary, error) {
	var rows []Summary
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, fmt.Errorf("ls --json: %v", err)
	}
	for i := range rows {
		rows[i].hostPaths(goos)
	}
	return rows, nil
}

func (s *Summary) hostPaths(goos string) {
	s.RunFolderHost = ToHost(goos, s.RunFolder)
	if s.Archive != nil {
		s.ArchiveHost = ToHost(goos, *s.Archive)
	}
}

// ParseStatus reads `status --json`; a missing run is an Outcome with ok false.
func ParseStatus(goos string, raw []byte) (*Status, *Outcome, error) {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(raw, &probe); err != nil {
		return nil, nil, fmt.Errorf("status --json: %v", err)
	}
	if _, ok := probe["ok"]; ok {
		var o Outcome
		err := json.Unmarshal(raw, &o)
		return nil, &o, err
	}
	var s Status
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, nil, fmt.Errorf("status --json: %v", err)
	}
	s.hostPaths(goos)
	return &s, nil, nil
}

func ParseView(goos string, raw []byte) (*View, *Outcome, error) {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(raw, &probe); err != nil {
		return nil, nil, fmt.Errorf("view --json: %v", err)
	}
	if _, ok := probe["ok"]; ok {
		var o Outcome
		err := json.Unmarshal(raw, &o)
		return nil, &o, err
	}
	var v View
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, nil, fmt.Errorf("view --json: %v", err)
	}
	if err := rowsOf(v.Tab, v.Rows); err != nil {
		return nil, nil, fmt.Errorf("view --json, %s rows: %v", v.Tab, err)
	}
	v.RunFolderHost = ToHost(goos, v.RunFolder)
	if v.Path != nil {
		v.PathHost = ToHost(goos, *v.Path)
	}
	return &v, nil, nil
}

func ParseConfigs(goos string, raw []byte) (*Configs, error) {
	var c Configs
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("configs --json: %v", err)
	}
	if c.Recipes == nil {
		c.Recipes = []RecipeEntry{}
	}
	if c.Sources == nil {
		c.Sources = []SourceEntry{}
	}
	if c.Flows == nil {
		c.Flows = []FlowEntry{}
	}
	if c.Briefs == nil {
		c.Briefs = []ConfigEntry{}
	}
	for i := range c.Recipes {
		c.Recipes[i].PathHost = ToHost(goos, c.Recipes[i].Path)
	}
	for i := range c.Sources {
		c.Sources[i].PathHost = ToHost(goos, c.Sources[i].Path)
	}
	for i := range c.Briefs {
		c.Briefs[i].PathHost = ToHost(goos, c.Briefs[i].Path)
	}
	if c.Draft != nil {
		c.Draft.PathHost = ToHost(goos, c.Draft.Path)
	}
	return &c, nil
}

// ParseDetail reads a manager answer that is one JSON object (a recipe, a prompt), or the refusal {ok: false, why}.
func ParseDetail(raw []byte) (json.RawMessage, *Outcome, error) {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(raw, &probe); err != nil {
		return nil, nil, fmt.Errorf("manager answer: %v", err)
	}
	if _, ok := probe["ok"]; ok {
		var o Outcome
		err := json.Unmarshal(raw, &o)
		return nil, &o, err
	}
	return raw, nil, nil
}

func ParseOutcome(raw []byte) (*Outcome, error) {
	var o Outcome
	if err := json.Unmarshal(raw, &o); err != nil {
		return nil, fmt.Errorf("manager answer: %v", err)
	}
	return &o, nil
}

// ---- what a start may carry ------------------------------------------------------------------------------------

type StartRequest struct {
	Config   string         `json:"config"`
	Recipe   string         `json:"recipe"`
	Source   string         `json:"source"`
	Name     string         `json:"name"`
	Model    string         `json:"model"`
	Dry      bool           `json:"dry"`
	Pages    string         `json:"pages"`
	Budget   string         `json:"budget"`
	Workflow string         `json:"workflow"`
	Archive  string         `json:"archive"`
	Models   string         `json:"models"`
	Without  string         `json:"without"`
	Flow     string         `json:"flow"`
	Lag      bool           `json:"lag"`
	After    string         `json:"after"`
	Answer   bool           `json:"answer"`    // wikidata or assemble: the session answers its own questions
	NoAnswer bool           `json:"no_answer"` // assemble: Answer questions off (it is on unless this is set)
	NoLimits bool           `json:"no_limits"` // limits
	Agents   map[string]int `json:"agents"`    // agents per model grade; a missing grade takes the flow's default
}

var (
	reModel   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$`)
	rePages   = regexp.MustCompile(`^\d{1,5}(-\d{1,5})?$`)
	reBudget  = regexp.MustCompile(`^(\d{1,7}(\.\d{1,2})?|none)$`)
	reModels  = regexp.MustCompile(`^[a-z]+=[A-Za-z0-9._:/-]+(,[a-z]+=[A-Za-z0-9._:/-]+)*$`)
	reWithout = regexp.MustCompile(`^(lag|rate|supervise|escalate)(,(lag|rate|supervise|escalate))*$`)
	reLang    = regexp.MustCompile(`^(auto|[a-z]{2,3})$`)
	reSlug    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$`)
	reID      = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$`)
)

// engineFlow: the flows pageflow/engine.py drives (apollo.py ENGINE_FLOWS); each takes the source its Basic run read.
var engineFlow = map[string]bool{"assemble": true, "refine": true, "timeline": true, "finance": true, "wikidata": true, "wiki": true, "places": true}

// StartArgs checks a start and builds the manager's arguments. Every value is one argument of its own; none can
// start with `-`, so none is read as an option.
func StartArgs(goos string, r StartRequest) ([]string, error) {
	cfg := strings.TrimSpace(r.Config)
	var args []string
	switch {
	case r.Flow != "":
		if !reSlug.MatchString(r.Flow) {
			return nil, errors.New("pick a flow")
		}
		a := strings.TrimSpace(r.Archive)
		if strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
			return nil, errors.New("archive: draft, new, or a path")
		}
		if r.Flow == "read" {
			if !reSlug.MatchString(r.Source) {
				return nil, errors.New("pick a source")
			}
			args = []string{"start", "--flow", "read", "--source", r.Source, "--json"}
			if r.Lag {
				args = append(args, "--lag")
			}
			if r.After != "" {
				if r.After != "assemble" {
					return nil, errors.New("after: assemble")
				}
				args = append(args, "--after", r.After)
			}
		} else {
			if a == "" {
				return nil, errors.New("pick an archive")
			}
			args = []string{"start", "--flow", r.Flow, "--json"}
			if r.Answer && r.NoAnswer {
				return nil, errors.New("answer: on or off, not both")
			}
			if r.Answer {
				if r.Flow != "wikidata" && r.Flow != "assemble" {
					return nil, errors.New("answer: wikidata or assemble only")
				}
				args = append(args, "--answer")
			}
			if r.NoAnswer {
				if r.Flow != "assemble" {
					return nil, errors.New("no_answer: assemble only")
				}
				args = append(args, "--no-answer")
			}
			if engineFlow[r.Flow] { // the engine flows (apollo.py ENGINE_FLOWS) read after a source's Basic run
				if r.Source != "" {
					if !reSlug.MatchString(r.Source) {
						return nil, errors.New("pick a source")
					}
					args = append(args, "--source", r.Source)
				}
			}
		}
		if a != "" {
			args = append(args, "--archive", managerPath(goos, a))
		}
	case r.Workflow != "":
		a := strings.TrimSpace(r.Archive)
		if !reSlug.MatchString(r.Workflow) || a == "" || strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
			return nil, errors.New("pick a workflow and an archive")
		}
		args = []string{"start", "--workflow", r.Workflow, "--archive", managerPath(goos, a), "--json"}
	case r.Recipe != "":
		if !reSlug.MatchString(r.Recipe) || !reSlug.MatchString(r.Source) {
			return nil, errors.New("pick a recipe and a source")
		}
		args = []string{"start", "--recipe", r.Recipe, "--source", r.Source, "--json"}
	case cfg == "":
		return nil, errors.New("pick a recipe and a source, or a brief")
	case strings.HasPrefix(cfg, "-") || strings.ContainsAny(cfg, "\x00\r\n"):
		return nil, errors.New("config: not a path")
	default:
		args = []string{"start", managerPath(goos, cfg), "--json"}
	}
	name := strings.TrimSpace(r.Name)
	if name != "" {
		if strings.HasPrefix(name, "-") || len(name) > 80 || strings.ContainsAny(name, "\x00\r\n\t") {
			return nil, errors.New("name: up to 80 characters on one line, not starting with -")
		}
		args = append(args, "--name", name)
	}
	if m := strings.TrimSpace(r.Model); m != "" {
		if !reModel.MatchString(m) {
			return nil, errors.New("model: a model id, e.g. claude-haiku-5-5")
		}
		args = append(args, "--model", m)
	}
	if p := strings.TrimSpace(r.Pages); p != "" {
		if !rePages.MatchString(p) {
			return nil, errors.New("pages: A-B, e.g. 20-22")
		}
		args = append(args, "--pages", p)
	}
	if b := strings.ToLower(strings.TrimSpace(r.Budget)); b != "" {
		if !reBudget.MatchString(b) {
			return nil, errors.New("budget: a number, or none")
		}
		args = append(args, "--budget", b)
	}
	if m := strings.TrimSpace(r.Models); m != "" {
		if (r.Recipe == "" && r.Flow != "read") || !reModels.MatchString(m) {
			return nil, errors.New("models: role=model,role=model on a page flow")
		}
		args = append(args, "--models", m)
	}
	if w := strings.TrimSpace(r.Without); w != "" {
		if (r.Recipe == "" && r.Flow != "read") || !reWithout.MatchString(w) {
			return nil, errors.New("without: lag, rate or supervise on a page flow")
		}
		args = append(args, "--without", w)
	}
	if len(r.Agents) > 0 {
		if r.Flow == "" {
			return nil, errors.New("agents: pick a flow")
		}
		var parts []string
		for _, g := range []string{"haiku", "sonnet", "opus"} {
			if n, ok := r.Agents[g]; ok {
				if n < 0 || n > 99 {
					return nil, errors.New("agents: a whole number from 0")
				}
				parts = append(parts, fmt.Sprintf("%s=%d", g, n))
			}
		}
		if len(parts) != len(r.Agents) {
			return nil, errors.New("agents: haiku, sonnet or opus")
		}
		args = append(args, "--agents", strings.Join(parts, ","))
	}
	if r.Dry {
		args = append(args, "--dry")
	}
	if r.NoLimits { // limits
		args = append(args, "--no-limits")
	}
	return args, nil
}

func validID(id string) bool { return reID.MatchString(id) }

// ---- the Tools group: OCR, Text, Check, Build ------------------------------------------------------------------

// ToolRequest names a tool and what it works on: a source (OCR, Text) or an archive (Check, Build).
type ToolRequest struct {
	Tool    string          `json:"tool"`
	Source  string          `json:"source"`
	Archive string          `json:"archive"`
	Pages   string          `json:"pages"`
	Lang    string          `json:"lang"`
	Args    json.RawMessage `json:"args"` // tool args: the declared arguments (toolargs.py), checked again by the manager
}

// ToolArgs checks a tool request and builds the manager's arguments (`toolstart`, or `toolget` when get is set).
// The manager checks the archive against its allowed roots again: this only keeps odd values out of the command line.
func ToolArgs(goos string, r ToolRequest, get bool) ([]string, error) {
	cmd := "toolstart"
	if get {
		cmd = "toolget"
	}
	var args []string
	switch r.Tool {
	case "ocr", "text":
		if !reSlug.MatchString(r.Source) {
			return nil, errors.New("pick a source")
		}
		args = []string{cmd, "--tool", r.Tool, "--source", r.Source}
	case "check":
		a := strings.TrimSpace(r.Archive)
		if a == "" || strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
			return nil, errors.New("pick an archive")
		}
		args = []string{cmd, "--tool", r.Tool, "--archive", managerPath(goos, a)}
	default:
		return nil, errors.New("tool: ocr, text or check")
	}
	if get {
		return append(args, "--json"), nil
	}
	if a, err := argsJSON(r.Args); err != nil { // tool args
		return nil, err
	} else if a != "" {
		return append(args, "--args", a, "--json"), nil
	}
	if p := strings.TrimSpace(r.Pages); p != "" {
		if !rePages.MatchString(p) {
			return nil, errors.New("pages: A-B, e.g. 20-22")
		}
		args = append(args, "--pages", p)
	}
	if l := strings.TrimSpace(r.Lang); l != "" {
		if !reLang.MatchString(l) {
			return nil, errors.New("lang: auto, or a language code")
		}
		args = append(args, "--lang", l)
	}
	return append(args, "--json"), nil
}

// ReadyArgs checks a ready request (is this source, these pages, this archive ready for a run) and builds the manager's arguments.
func ReadyArgs(goos, source, recipe, pages, archive string) ([]string, error) {
	if recipe == "" {
		recipe = "full"
	}
	if !reSlug.MatchString(source) || !reSlug.MatchString(recipe) || (pages != "" && !rePages.MatchString(pages)) {
		return nil, errors.New("a source, a recipe and pages A-B")
	}
	args := []string{"ready", "--source", source, "--recipe", recipe}
	if pages != "" {
		args = append(args, "--pages", pages)
	}
	if a := strings.TrimSpace(archive); a != "" {
		if strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
			return nil, errors.New("archive: not a path")
		}
		args = append(args, "--archive", managerPath(goos, a))
	}
	return append(args, "--json"), nil
}
