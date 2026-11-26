package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Catalogue: an agent reads a source's first pages and writes a library record (catalogue.py). Each route is a thin pass over one
// manager command; the start runs in its own manager process and returns at once. The export is a download.

var reTier = regexp.MustCompile(`^(haiku|sonnet|opus)$`)

type catalogueRequest struct {
	Source string          `json:"source"`
	Model  string          `json:"model"`
	Action string          `json:"action"`
	Args   json.RawMessage `json:"args"`
}

func (s *Server) catalogueGet(w http.ResponseWriter, r *http.Request) {
	src := r.URL.Query().Get("source")
	if !reSlug.MatchString(src) {
		fail(w, http.StatusBadRequest, "pick a source")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 20*time.Second, "catalogueget", "--source", src)
	passJSON(w, raw, err, http.StatusConflict)
}

// cataloguePost starts a job ({source, model}) or, at /act, decides the waiting record ({source, action}).
func (s *Server) cataloguePost(act bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req catalogueRequest
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<14))
		if err == nil {
			err = json.Unmarshal(body, &req)
		}
		if err != nil || !reSlug.MatchString(req.Source) {
			fail(w, http.StatusBadRequest, "a JSON body: source, and model (or action)")
			return
		}
		var args []string
		if act {
			if req.Action != "accept" && req.Action != "reject" && req.Action != "revert" {
				fail(w, http.StatusBadRequest, "accept, reject or revert")
				return
			}
			args = []string{"catalogueact", "--source", req.Source, "--action", req.Action}
		} else {
			if req.Model == "" {
				req.Model = "haiku"
			}
			if !reTier.MatchString(req.Model) {
				fail(w, http.StatusBadRequest, "model: haiku, sonnet or opus")
				return
			}
			args = []string{"cataloguestart", "--source", req.Source, "--model", req.Model}
			a, err := argsJSON(req.Args)
			if err != nil {
				fail(w, http.StatusBadRequest, err.Error())
				return
			}
			if a != "" {
				args = append(args, "--args", a)
			}
		}
		raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 60*time.Second, args...)
		s.cache.clear()
		s.outcome(w, raw, err)
	}
}

// catalogueExport sends the accepted record as MARCXML (?format=marcxml) or CSL-JSON (?format=csl), as a file to save.
func (s *Server) catalogueExport(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	src, format := q.Get("source"), q.Get("format")
	if !reSlug.MatchString(src) || (format != "marcxml" && format != "csl") {
		fail(w, http.StatusBadRequest, "a source, and format marcxml or csl")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 20*time.Second, "catalogueexport", "--source", src, "--format", format)
	var got struct {
		OK       bool   `json:"ok"`
		Why      string `json:"why"`
		Filename string `json:"filename"`
		Mime     string `json:"mime"`
		Text     string `json:"text"`
	}
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	if json.Unmarshal(raw, &got) != nil {
		fail(w, http.StatusBadGateway, "the manager's answer is not JSON")
		return
	}
	if !got.OK {
		fail(w, http.StatusConflict, got.Why)
		return
	}
	w.Header().Set("Content-Type", got.Mime)
	w.Header().Set("Content-Disposition", `attachment; filename="`+strings.ReplaceAll(got.Filename, `"`, "")+`"`)
	w.Write([]byte(got.Text))
}

// history is Action History: what the person and the agents did, newest first, optionally one kind of it.
func (s *Server) history(w http.ResponseWriter, r *http.Request) {
	args := []string{"history"}
	if k := r.URL.Query().Get("kind"); k != "" {
		if k != "run" && k != "tool" && k != "source" && k != "toast" {
			fail(w, http.StatusBadRequest, "kind: run, tool, source or toast")
			return
		}
		args = append(args, "--kind", k)
	}
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err != nil || n < 1 || n > 2000 {
			fail(w, http.StatusBadRequest, "limit: 1 to 2000")
			return
		}
		args = append(args, "--limit", l)
	}
	raw, err := s.call(r, 700*time.Millisecond, args...)
	passJSON(w, raw, err, http.StatusConflict)
}
