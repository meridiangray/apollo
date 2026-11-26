package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// ProofreadRequest starts Haiku on pages of a PDF source ("7" or "7-9"), or decides one page's proposal.
type ProofreadRequest struct {
	Source string `json:"source"`
	Pages  string `json:"pages"`
	Page   int    `json:"page"`
	Action string `json:"action"`
	Model  string `json:"model"`
}

// ProofreadArgs checks a request and builds the manager's arguments: `proofreadstart`, or `proofreadact` when act is set.
// The manager checks everything again (the source, the PDF, the 20-page limit); this only keeps odd values off the command line.
func ProofreadArgs(r ProofreadRequest, act bool) ([]string, error) {
	if !reSlug.MatchString(r.Source) {
		return nil, errString("pick a source")
	}
	if act {
		if r.Page < 1 || (r.Action != "accept" && r.Action != "reject" && r.Action != "revert") {
			return nil, errString("a page, and accept, reject or revert")
		}
		return []string{"proofreadact", "--source", r.Source, "--page", strconv.Itoa(r.Page), "--action", r.Action}, nil
	}
	if !rePages.MatchString(strings.TrimSpace(r.Pages)) {
		return nil, errString("pages: A-B, e.g. 7-9")
	}
	args := []string{"proofreadstart", "--source", r.Source, "--pages", strings.TrimSpace(r.Pages)}
	if r.Model != "" {
		if !reTier.MatchString(r.Model) {
			return nil, errString("model: haiku, sonnet or opus")
		}
		args = append(args, "--model", r.Model)
	}
	return args, nil
}

type errString string

func (e errString) Error() string { return string(e) }

// proofreadGet is the Text tab's Proofread state for a source (and one page's proposal): files read by the worker.
func (s *Server) proofreadGet(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	src := q.Get("source")
	if !reSlug.MatchString(src) {
		fail(w, http.StatusBadRequest, "pick a source")
		return
	}
	args := []string{"proofreadget", "--source", src}
	if p := q.Get("page"); p != "" {
		if !rePageNo.MatchString(p) {
			fail(w, http.StatusBadRequest, "a page number")
			return
		}
		args = append(args, "--page", p)
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 10*time.Second, args...)
	passJSON(w, raw, err, http.StatusConflict)
}

// proofreadPost starts a job (its own manager process; returns at once) or, at /act, accepts, rejects or reverts a page.
func (s *Server) proofreadPost(act bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req ProofreadRequest
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
		if err == nil {
			err = json.Unmarshal(body, &req)
		}
		if err != nil {
			fail(w, http.StatusBadRequest, "a JSON body: source, pages (or page and action)")
			return
		}
		args, err := ProofreadArgs(req, act)
		if err != nil {
			fail(w, http.StatusBadRequest, err.Error())
			return
		}
		raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 60*time.Second, args...)
		s.cache.clear() // the page text answers (pagetext) must show an accepted page at once
		s.outcome(w, raw, err)
	}
}

// LogdbArgs builds the read-only query call. The query travels base64-coded so no quoting survives to the command line.
func LogdbArgs(sql string) ([]string, error) {
	sql = strings.TrimSpace(sql)
	if sql == "" || len(sql) > 20000 || strings.ContainsRune(sql, 0) {
		return nil, errString("a SELECT or WITH query")
	}
	return []string{"logdb", "--sql64", base64.RawURLEncoding.EncodeToString([]byte(sql))}, nil
}

// logdb runs a SELECT or WITH on the DuckDB log, read-only (the manager refuses anything else).
func (s *Server) logdb(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SQL string `json:"sql"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<17))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: sql")
		return
	}
	args, err := LogdbArgs(req.SQL)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 30*time.Second, args...)
	passJSON(w, raw, err, http.StatusUnprocessableEntity)
}
