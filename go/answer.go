package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"time"
)

// answering: the Questions tab's one click. A question is an agent's (ask-…) or one of the build's (merge-…, place-…,
// ref-…); a choice is an option's number, confirm, reject, none, dismiss, or clear (take the answer back).
var (
	reQuestion = regexp.MustCompile(`^(ask|merge|place|ref)-[A-Za-z0-9]{4,40}$`)
	reChoice   = regexp.MustCompile(`^([0-9]{1,3}|confirm|reject|none|dismiss|clear)$`)
)

// AnswerArgs checks a request and builds the manager's arguments (`answer ID --question Q --choice C --json`).
func AnswerArgs(id string, question, choice string) ([]string, error) {
	if !validID(id) {
		return nil, errString("not a run id")
	}
	if !reQuestion.MatchString(question) {
		return nil, errString("question: an ask-, merge-, place- or ref- id")
	}
	if !reChoice.MatchString(choice) {
		return nil, errString("choice: an option's number, confirm, reject, none, dismiss or clear")
	}
	return []string{"answer", id, "--question", question, "--choice", choice, "--json"}, nil
}

// answer records one click through hydra (its own manager process: it writes the archive, under hydra's lock) and
// answers {ok, id, said}, or hydra's refusal as a 409.
func (s *Server) answer(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Question string `json:"question"`
		Choice   string `json:"choice"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<12))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: question, choice")
		return
	}
	args, err := AnswerArgs(r.PathValue("id"), req.Question, req.Choice)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 180*time.Second, args...)
	s.cache.clear()
	passJSON(w, raw, err, http.StatusConflict)
}
