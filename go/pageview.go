package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"time"
)

// pageGroups are the Found tab's six categories (pageview.GROUPS): the only values a group argument may take.
var pageGroups = map[string]bool{"people": true, "bodies": true, "places": true, "events": true, "ties": true, "posts": true}

// pageview is a page-flow run's page strip (?run=ID), or one page's box (?run=ID&page=N), or, with all=1, the whole run's
// rich data (people, bodies, places, events, ties, posts, each with the pages it is on), optionally for one group and one
// page: the section, counts, people, bodies, places with their country outlines, events and quotes, read by the manager
// from the run's files (pageview.py). The manager's JSON goes through as it is.
func (s *Server) pageview(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	id := q.Get("run")
	if !validID(id) {
		fail(w, http.StatusBadRequest, "not a run id")
		return
	}
	args := []string{"pageview", id, "--json"}
	if p := q.Get("page"); p != "" {
		n, err := strconv.Atoi(p)
		if err != nil || n < 1 || n > 100000 {
			fail(w, http.StatusBadRequest, "page: a page number")
			return
		}
		args = append(args, "--page", strconv.Itoa(n))
	}
	if q.Get("all") == "1" {
		args = append(args, "--all")
		if g := q.Get("group"); g != "" {
			if !pageGroups[g] {
				fail(w, http.StatusBadRequest, "group: people, bodies, places, events, ties or posts")
				return
			}
			args = append(args, "--group", g)
		}
	} else if q.Get("group") != "" {
		fail(w, http.StatusBadRequest, "group: only with all=1")
		return
	}
	raw, err := s.call(r, 4*time.Second, args...)
	passJSON(w, raw, err, http.StatusNotFound)
}

// ExportRequest is what the window asks of POST /api/export/{id}: the format, and how to cut the run's results.
type ExportRequest struct {
	Format string `json:"format"`
	Group  string `json:"group"`
	Page   int    `json:"page"`
	By     string `json:"by"`
}

// ExportArgs checks a request and builds the manager's arguments (`export ID --format F [--group G] [--page N] --by B`).
func ExportArgs(id string, r ExportRequest) ([]string, error) {
	if !validID(id) {
		return nil, errString("not a run id")
	}
	if r.Format != "json" && r.Format != "jsonl" {
		return nil, errString("format: json or jsonl")
	}
	by := r.By
	if by == "" {
		by = "none"
	}
	if by != "none" && by != "group" && by != "page" {
		return nil, errString("by: none, group or page")
	}
	args := []string{"export", id, "--format", r.Format, "--by", by}
	if r.Group != "" {
		if !pageGroups[r.Group] {
			return nil, errString("group: people, bodies, places, events, ties or posts")
		}
		args = append(args, "--group", r.Group)
	}
	if r.Page != 0 {
		if r.Page < 1 || r.Page > 100000 {
			return nil, errString("page: a page number")
		}
		args = append(args, "--page", strconv.Itoa(r.Page))
	}
	return args, nil
}

// export writes a finished run's results into its exports/ folder (its own manager process: it writes files) and answers
// {ok, files, count, status}, or the manager's refusal (a run still running) as a 409.
func (s *Server) export(w http.ResponseWriter, r *http.Request) {
	var req ExportRequest
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<12))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: format, group, page, by")
		return
	}
	args, err := ExportArgs(r.PathValue("id"), req)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 60*time.Second, args...)
	passJSON(w, raw, err, http.StatusConflict)
}
