package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"time"
)

// catalogueEdit changes one field of the accepted record ({source, index, value}), or of the record waiting for Accept
// ({pending: true}); an empty value removes it. The manager checks it.
func (s *Server) catalogueEdit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Source  string `json:"source"`
		Index   int    `json:"index"`
		Value   string `json:"value"`
		Pending bool   `json:"pending"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<15))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil || !reSlug.MatchString(req.Source) || req.Index < 0 {
		fail(w, http.StatusBadRequest, "a JSON body: source, index, value")
		return
	}
	args := []string{"catalogueedit", "--source", req.Source, "--index", strconv.Itoa(req.Index), "--value=" + req.Value}
	if req.Pending {
		args = append(args, "--pending")
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 30*time.Second, args...)
	s.cache.clear()
	s.outcome(w, raw, err)
}

// catalogueMark rejects ({source, index, rejected: true}) or restores one field of the waiting record before Accept.
func (s *Server) catalogueMark(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Source   string `json:"source"`
		Index    int    `json:"index"`
		Rejected bool   `json:"rejected"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<12))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil || !reSlug.MatchString(req.Source) || req.Index < 0 {
		fail(w, http.StatusBadRequest, "a JSON body: source, index, rejected")
		return
	}
	flag := "0"
	if req.Rejected {
		flag = "1"
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 30*time.Second, "cataloguemark", "--source", req.Source, "--index", strconv.Itoa(req.Index), "--rejected", flag)
	s.cache.clear()
	s.outcome(w, raw, err)
}
