package main

import (
	"testing"

	"apollo/appicon"
)

func TestLookValidatesColours(t *testing.T) {
	if !(Look{Caption: "#f6ede7", Text: "#22201d"}).valid() {
		t.Fatal("two #rrggbb colours are valid")
	}
	for _, bad := range []Look{{Caption: "red", Text: "#000000"}, {Caption: "#fff", Text: "#000000"}, {Caption: "#000000", Text: "#00000g"}, {}} {
		if bad.valid() {
			t.Fatalf("%+v should be refused", bad)
		}
	}
}

func TestLookRouteNeedsTokenAndAcceptsColours(t *testing.T) {
	s, err := NewServer("linux", 8787, NewBackend("linux", Config{}), nil)
	if err != nil {
		t.Fatal(err)
	}
	h := s.Handler()
	host := "127.0.0.1:8787"
	good := `{"caption":"#000000","text":"#ffffff","dark":true}`
	if w := do(h, "POST", "/api/look", host, nil, good); w.Code != 403 {
		t.Fatalf("no token: %d", w.Code)
	}
	tok := map[string]string{tokenHeader: s.Token}
	if w := do(h, "POST", "/api/look", host, tok, good); w.Code != 200 {
		t.Fatalf("good look: %d", w.Code)
	}
	if w := do(h, "POST", "/api/look", host, tok, `{"caption":"nope","text":"#ffffff"}`); w.Code != 400 {
		t.Fatalf("bad look: %d", w.Code)
	}
}

func TestLookCarriesTheIconColours(t *testing.T) {
	l := Look{Caption: "#f6ede7", Text: "#22201d", BG: "#f6ede7", Ink: "#22201d", Primary: "#1b5e52", Accent: "#c87a4f"}
	if !l.valid() {
		t.Fatal("icon colours are valid")
	}
	if p, ok := l.palette(); !ok || p.Square != (appicon.RGB{0x1b, 0x5e, 0x52}) {
		t.Fatalf("palette %+v %v", p, ok)
	}
	if _, ok := (Look{Caption: "#f6ede7", Text: "#22201d"}).palette(); ok {
		t.Fatal("no icon colours, no palette")
	}
	if (Look{Caption: "#f6ede7", Text: "#22201d", Accent: "orange"}).valid() {
		t.Fatal("a bad accent is refused")
	}
}

func TestCatalogueAndHistoryRoutesGuardTheirInput(t *testing.T) {
	s, err := NewServer("linux", 8787, NewBackend("linux", Config{}), nil)
	if err != nil {
		t.Fatal(err)
	}
	h := s.Handler()
	host := "127.0.0.1:8787"
	tok := map[string]string{tokenHeader: s.Token}
	for _, c := range []struct{ method, path, body string }{
		{"GET", "/api/catalogue?source=../x", ""},
		{"GET", "/api/catalogue/export?source=pollard&format=docx", ""},
		{"GET", "/api/history?kind=everything", ""},
		{"POST", "/api/catalogue", `{"source":"pollard","model":"gpt"}`},
		{"POST", "/api/catalogue/act", `{"source":"pollard","action":"delete"}`},
		{"POST", "/api/catalogue/add", `{"source":"../x","key":"title","value":"x"}`},
		{"POST", "/api/proofread", `{"source":"pollard","pages":"1","model":"gpt"}`},
	} {
		if w := do(h, c.method, c.path, host, tok, c.body); w.Code != 400 {
			t.Fatalf("%s %s: %d", c.method, c.path, w.Code)
		}
	}
	if w := do(h, "POST", "/api/catalogue", host, nil, `{"source":"pollard"}`); w.Code != 403 {
		t.Fatalf("no token: %d", w.Code)
	}
}
