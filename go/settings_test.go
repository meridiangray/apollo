package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

const keyValue = "sk-test-VALUE-0123456789"

func TestSettingsArgsKeepTheKeyOffTheCommandLine(t *testing.T) {
	args, stdin, op, err := SettingsArgs([]byte(`{"op":"key","name":"ANTHROPIC_API_KEY","value":" ` + keyValue + ` "}`))
	if err != nil || op != "key" || string(stdin) != keyValue {
		t.Fatalf("%v %q %q %v", args, stdin, op, err)
	}
	if strings.Contains(strings.Join(args, " "), keyValue) || args[0] != "settingsset" || args[1] != "--stdin" {
		t.Fatalf("the value is on the command line: %v", args)
	}
	raw, _ := base64.StdEncoding.DecodeString(args[len(args)-1])
	var req map[string]any
	if json.Unmarshal(raw, &req) != nil || req["name"] != "ANTHROPIC_API_KEY" || req["value"] != nil {
		t.Fatalf("req %s", raw)
	}
	if args, stdin, _, _ := SettingsArgs([]byte(`{"op":"key","name":"K","remove":true}`)); stdin != nil || len(args) != 3 {
		t.Fatalf("remove takes no stdin: %v", args)
	}
	for _, bad := range []string{``, `[]`, `{"op":"rm"}`, `{"op":"key","name":"K","value":""}`, `{"op":"key","name":"K","value":"a\nb"}`,
		`{"op":"key","name":"K","value":5}`} {
		if _, _, _, err := SettingsArgs([]byte(bad)); err == nil {
			t.Errorf("accepted %s", bad)
		}
	}
}

func TestSettingsRoutes(t *testing.T) {
	s, rec := testServer(t, map[string]string{"settings": `{"ok": true, "models": []}`})
	var gotArgv []string
	var gotIn []byte
	s.Backend.ExecIn = func(ctx context.Context, argv []string, stdin []byte) ([]byte, []byte, int, error) {
		gotArgv, gotIn = argv, stdin
		return []byte(`{"ok": true, "models": []}`), nil, 0, nil
	}
	h := s.Handler()
	const host = "127.0.0.1:8787"
	if w := do(h, "GET", "/api/settings", host, nil, ""); w.Code != 200 || len(rec.calls) != 1 || rec.calls[0][2] != "settings" {
		t.Fatalf("get: %d %v", w.Code, rec.calls)
	}
	hdr := map[string]string{"X-Apollo-Token": s.Token, "Origin": "http://" + host}
	w := do(h, "POST", "/api/settings", host, hdr, `{"op":"key","name":"ANTHROPIC_API_KEY","value":"`+keyValue+`"}`)
	if w.Code != 200 || string(gotIn) != keyValue || strings.Contains(strings.Join(gotArgv, " "), keyValue) || strings.Contains(w.Body.String(), keyValue) {
		t.Fatalf("post: %d %v %q", w.Code, gotArgv, gotIn)
	}
	if w := do(h, "POST", "/api/settings", host, map[string]string{"Origin": "http://" + host}, `{"op":"test","id":"x"}`); w.Code != 403 {
		t.Fatalf("no token: %d", w.Code)
	}
	s.Backend.ExecIn = func(ctx context.Context, argv []string, stdin []byte) ([]byte, []byte, int, error) {
		return []byte(`{"ok": false, "why": "no model uses that key"}`), nil, 2, nil
	}
	if w := do(h, "POST", "/api/settings", host, hdr, `{"op":"key","name":"PATH","remove":true}`); w.Code != 409 || !strings.Contains(w.Body.String(), "no model uses") {
		t.Fatalf("refusal: %d %s", w.Code, w.Body.String())
	}
}
