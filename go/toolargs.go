package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"time"
)

// Tool arguments: each tool declares its own (toolargs.py). The window asks for them, with the sentence of what the tool will
// do with the values it holds, and sends the values back with the run as one JSON object that the manager checks again.

var reToolName = regexp.MustCompile(`^(ocr|text|check|build|catalogue|proofread)$`)

// argsJSON checks the `args` of a request: absent, or a JSON object of modest size. It returns the text to pass on, or "".
func argsJSON(raw json.RawMessage) (string, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return "", nil
	}
	var m map[string]any
	if len(raw) > 1<<13 || json.Unmarshal(raw, &m) != nil {
		return "", errors.New("args: a JSON object")
	}
	b, _ := json.Marshal(m)
	return string(b), nil
}

// toolAbout answers GET /api/tool/about?tool=&source=&args=: the declared fields, the values they come to, and the sentence.
func (s *Server) toolAbout(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	if !reToolName.MatchString(q.Get("tool")) || (q.Get("source") != "" && !reSlug.MatchString(q.Get("source"))) {
		fail(w, http.StatusBadRequest, "a tool, and a source")
		return
	}
	args := []string{"toolabout", "--tool", q.Get("tool")}
	if src := q.Get("source"); src != "" {
		args = append(args, "--source", src)
	}
	if a, err := argsJSON(json.RawMessage(q.Get("args"))); err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	} else if a != "" {
		args = append(args, "--args", a)
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 20*time.Second, args...)
	passJSON(w, raw, err, http.StatusConflict)
}
