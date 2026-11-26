package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// The Settings page (CZ330): GET is what the page draws (models and keys by name, defaults, limits, folders, network);
// POST is one change, {op, ...}, passed to the manager as base64 JSON. A key's value never goes on a command line or
// into a log: it is taken out of the body here and handed to the manager on its standard input.

var settingsOps = map[string]bool{"set": true, "models": true, "key": true, "test": true, "network": true}

// SettingsArgs is the manager call for one change, and the stdin it needs (a key's value, else none).
func SettingsArgs(body []byte) (args []string, stdin []byte, op string, err error) {
	var req map[string]any
	if json.Unmarshal(body, &req) != nil || req == nil {
		return nil, nil, "", errString("a JSON body: op and its fields")
	}
	op, _ = req["op"].(string)
	if !settingsOps[op] {
		return nil, nil, "", errString("op: set, models, key, test or network")
	}
	args = []string{"settingsset"}
	if v, has := req["value"]; op == "key" && has {
		s, ok := v.(string)
		s = strings.TrimSpace(s)
		if !ok || s == "" || len(s) > 4096 || strings.ContainsAny(s, "\x00\r\n") {
			return nil, nil, "", errString("a key is one line of text")
		}
		delete(req, "value")
		stdin = []byte(s)
		args = append(args, "--stdin")
	}
	b, _ := json.Marshal(req)
	return append(args, "--req64", base64.StdEncoding.EncodeToString(b)), stdin, op, nil
}

func (s *Server) settingsGet(w http.ResponseWriter, r *http.Request) {
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 10*time.Second, "settings")
	passJSON(w, raw, err, http.StatusConflict)
}

func (s *Server) settingsPost(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: op and its fields")
		return
	}
	args, stdin, op, err := SettingsArgs(body)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	timeout := 30 * time.Second
	if op == "test" {
		timeout = 2 * time.Minute // one tiny call to the provider, at the person's click
	}
	raw, err := s.Backend.CallIn(context.WithoutCancel(r.Context()), timeout, stdin, args...)
	s.cache.clear() // New run's flows carry the agent defaults
	passJSON(w, raw, err, http.StatusConflict)
}
