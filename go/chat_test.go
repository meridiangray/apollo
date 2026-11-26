package main

import (
	"encoding/base64"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func decodeChat(t *testing.T, args []string) ChatRequest {
	t.Helper()
	if len(args) != 3 || args[0] != "chatstart" || args[1] != "--req64" {
		t.Fatalf("args %v", args)
	}
	b, err := base64.RawURLEncoding.DecodeString(args[2])
	if err != nil {
		t.Fatal(err)
	}
	var r ChatRequest
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestChatArgs(t *testing.T) {
	args, err := ChatArgs(ChatRequest{Messages: []ChatMessage{{"user", "  Who wrote it?  "}},
		Context: ChatContext{View: "runs", Run: "2026-11-24-dry-smoke-2", Source: "pollard", Archive: "/a/b"}})
	if err != nil {
		t.Fatal(err)
	}
	got := decodeChat(t, args)
	want := ChatRequest{Model: "sonnet", Messages: []ChatMessage{{"user", "Who wrote it?"}},
		Context: ChatContext{View: "runs", Run: "2026-11-24-dry-smoke-2", Source: "pollard", Archive: "/a/b"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%+v", got)
	}
	for name, r := range map[string]ChatRequest{
		"model":   {Model: "gpt", Messages: []ChatMessage{{"user", "x"}}},
		"empty":   {Model: "opus"},
		"last":    {Model: "opus", Messages: []ChatMessage{{"user", "x"}, {"assistant", "y"}}},
		"role":    {Model: "opus", Messages: []ChatMessage{{"system", "x"}}},
		"session": {Model: "opus", Session: "../x", Messages: []ChatMessage{{"user", "x"}}},
	} {
		if _, err := ChatArgs(r); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
	// odd context values are dropped, not refused
	args, _ = ChatArgs(ChatRequest{Model: "haiku", Session: "20261125T101500Z-0a1b2c", Messages: []ChatMessage{{"user", "x"}},
		Context: ChatContext{View: "nope", Run: "-x", Source: "a b", Archive: "x\ny"}})
	if got := decodeChat(t, args); got.Context != (ChatContext{}) || got.Session != "20261125T101500Z-0a1b2c" || got.Model != "haiku" {
		t.Fatalf("%+v", got)
	}
}

func TestChatArgsFitTheCommandLine(t *testing.T) {
	long := strings.Repeat("é", 5000) // clipped to 4000 characters
	var msgs []ChatMessage
	for i := 0; i < 30; i++ {
		msgs = append(msgs, ChatMessage{"user", long}, ChatMessage{"assistant", "ok"})
	}
	msgs = append(msgs, ChatMessage{"user", "the last"})
	args, err := ChatArgs(ChatRequest{Model: "opus", Messages: msgs})
	if err != nil {
		t.Fatal(err)
	}
	if len(args[2]) > 32000 {
		t.Fatalf("too long: %d", len(args[2]))
	}
	got := decodeChat(t, args)
	if n := len(got.Messages); n < 2 || got.Messages[n-1].Text != "the last" || got.Messages[0].Role != "user" {
		t.Fatalf("%d messages, first %s", n, got.Messages[0].Role)
	}
	if len([]rune(got.Messages[0].Text)) != 4000 {
		t.Fatalf("clipped to %d", len([]rune(got.Messages[0].Text)))
	}
	one, _ := ChatArgs(ChatRequest{Model: "opus", Messages: []ChatMessage{{"user", strings.Repeat("一", 4000)}}})
	if len(one[2]) > 32000 {
		t.Fatalf("one long message: %d", len(one[2]))
	}
}

func TestChatRoutes(t *testing.T) {
	s, rec := testServer(t, map[string]string{
		"chatstart": `{"ok": true, "id": "20261125T101500Z-0a1b2c-1", "session": "20261125T101500Z-0a1b2c"}`,
		"chatget":   `{"ok": true, "id": "20261125T101500Z-0a1b2c-1", "status": "done", "text": "It was Pollard."}`,
	})
	s.Backend.Worker = nil
	h := s.Handler()
	const host = "127.0.0.1:8787"
	tok := map[string]string{tokenHeader: s.Token}
	for _, p := range []string{"/chat.js", "/chat.css"} {
		if w := do(h, "GET", p, host, nil, ""); w.Code != 200 {
			t.Errorf("%s: %d", p, w.Code)
		}
	}
	if w := do(h, "POST", "/api/chat", host, nil, `{"messages":[{"role":"user","text":"x"}]}`); w.Code != 403 {
		t.Fatalf("no token: %d", w.Code)
	}
	w := do(h, "POST", "/api/chat", host, tok, `{"model":"opus","messages":[{"role":"user","text":"Who?"}],"context":{"view":"sources","source":"pollard"}}`)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"session"`) {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	last := rec.calls[len(rec.calls)-1]
	if got := decodeChat(t, last[2:]); got.Model != "opus" || got.Context.Source != "pollard" || got.Context.View != "sources" {
		t.Fatalf("%+v", got)
	}
	if w := do(h, "POST", "/api/chat", host, tok, `{"model":"gpt","messages":[{"role":"user","text":"x"}]}`); w.Code != 400 {
		t.Fatalf("a bad model: %d", w.Code)
	}
	w = do(h, "GET", "/api/chat/20261125T101500Z-0a1b2c-1", host, nil, "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "It was Pollard.") {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last, []string{"p", "f", "chatget", "--id", "20261125T101500Z-0a1b2c-1"}) {
		t.Fatalf("%v", last)
	}
	if w := do(h, "GET", "/api/chat/..%2Fx", host, nil, ""); w.Code != 400 {
		t.Fatalf("a bad id: %d", w.Code)
	}
	if !workerCalls["chatget"] || workerCalls["chatstart"] {
		t.Fatal("the worker answers chatget only")
	}
}
