package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// briefGenerate drafts a source's brief with a model ({source, model}) and answers {ok, brief}; nothing is saved: the
// person edits the draft and the Brief box saves it as before. The manager reads the source and calls the model.
func (s *Server) briefGenerate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Source string `json:"source"`
		Model  string `json:"model"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<12))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil || !reSlug.MatchString(req.Source) || (req.Model != "" && !reModel.MatchString(req.Model)) {
		fail(w, http.StatusBadRequest, "a JSON body: source, model")
		return
	}
	args := []string{"briefgen", "--source", req.Source}
	if req.Model != "" {
		args = append(args, "--model", req.Model)
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 4*time.Minute, args...)
	s.outcome(w, raw, err)
}
