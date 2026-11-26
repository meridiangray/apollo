package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAnswerArgsCheckEveryValue(t *testing.T) {
	got, err := AnswerArgs("2026-11-26-workflow-links", "ask-a1ca0b81a533", "2")
	if err != nil || strings.Join(got, " ") != "answer 2026-11-26-workflow-links --question ask-a1ca0b81a533 --choice 2 --json" {
		t.Fatalf("a good click: %v %v", got, err)
	}
	for _, c := range []string{"confirm", "reject", "none", "dismiss", "clear", "12"} {
		if _, err := AnswerArgs("run-1", "merge-1fdcee78dc61f9af", c); err != nil {
			t.Fatalf("choice %s: %v", c, err)
		}
	}
	for _, bad := range [][3]string{
		{"../x", "ask-a1ca0b81a533", "1"},                // not a run id
		{"run-1", "end-abc12345", "confirm"},             // not a question this tab answers
		{"run-1", "ask-a1ca0b81a533 --x", "1"},           // no extra argument
		{"run-1", "ask-a1ca0b81a533", "text:anything"},   // no free text
		{"run-1", "ask-a1ca0b81a533", "-1"},              // never an option flag
	} {
		if _, err := AnswerArgs(bad[0], bad[1], bad[2]); err == nil {
			t.Fatalf("%v should be refused", bad)
		}
	}
}

func TestAnswerRefusesABodyThatIsNotAClick(t *testing.T) {
	s := &Server{cache: newCache()}
	r := httptest.NewRequest("POST", "/api/answer/run-1", strings.NewReader(`{"question":"ask-a1ca0b81a533","choice":"drop table"}`))
	r.SetPathValue("id", "run-1")
	w := httptest.NewRecorder()
	s.answer(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
}
