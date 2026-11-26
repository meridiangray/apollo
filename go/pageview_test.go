package main

import (
	"reflect"
	"strings"
	"testing"
)

func TestPageViewGoesToTheManager(t *testing.T) {
	s, rec := testServer(t, map[string]string{"pageview": `{"ok": true, "pages": []}`})
	h, host := s.Handler(), "127.0.0.1:8787"
	if w := do(h, "GET", "/api/pageview?run=2026-11-25-read-x", host, nil, ""); w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"pageview", "2026-11-25-read-x", "--json"}) {
		t.Fatalf("%q", last)
	}
	if w := do(h, "GET", "/api/pageview?run=r1&page=38", host, nil, ""); w.Code != 200 {
		t.Errorf("page: %d", w.Code)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"pageview", "r1", "--json", "--page", "38"}) {
		t.Fatalf("%q", last)
	}
	for _, bad := range []string{"", "run=--x", "run=r1&page=0", "run=r1&page=x", "run=r1&page=1%0A", "run=a%0Ab"} {
		if w := do(h, "GET", "/api/pageview?"+bad, host, nil, ""); w.Code != 400 {
			t.Errorf("%q: %d", bad, w.Code)
		}
	}
	if w := do(h, "GET", "/pageview.js", host, nil, ""); w.Code != 200 {
		t.Errorf("pageview.js: %d", w.Code)
	}
}

func TestPageViewAllAndGroup(t *testing.T) {
	s, rec := testServer(t, map[string]string{"pageview": `{"ok": true, "people": []}`})
	h, host := s.Handler(), "127.0.0.1:8787"
	if w := do(h, "GET", "/api/pageview?run=r1&all=1&group=ties&page=41", host, nil, ""); w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"pageview", "r1", "--json", "--page", "41", "--all", "--group", "ties"}) {
		t.Fatalf("%q", last)
	}
	for _, bad := range []string{"run=r1&all=1&group=other", "run=r1&all=1&group=--x", "run=r1&group=people", "run=r1&all=1&page=0"} {
		if w := do(h, "GET", "/api/pageview?"+bad, host, nil, ""); w.Code != 400 {
			t.Errorf("%q: %d", bad, w.Code)
		}
	}
}

func TestExportArgs(t *testing.T) {
	got, err := ExportArgs("r1", ExportRequest{Format: "jsonl", Group: "people", Page: 41, By: "page"})
	if err != nil || !reflect.DeepEqual(got, []string{"export", "r1", "--format", "jsonl", "--by", "page", "--group", "people", "--page", "41"}) {
		t.Fatalf("%q %v", got, err)
	}
	if got, err = ExportArgs("r1", ExportRequest{Format: "json"}); err != nil || !reflect.DeepEqual(got, []string{"export", "r1", "--format", "json", "--by", "none"}) {
		t.Fatalf("%q %v", got, err)
	}
	for _, bad := range []struct {
		id string
		r  ExportRequest
	}{
		{"-x", ExportRequest{Format: "json"}},
		{"r1", ExportRequest{}},
		{"r1", ExportRequest{Format: "csv"}},
		{"r1", ExportRequest{Format: "json", Group: "other"}},
		{"r1", ExportRequest{Format: "json", Page: -3}},
		{"r1", ExportRequest{Format: "json", Page: 1000000}},
		{"r1", ExportRequest{Format: "json", By: "year"}},
	} {
		if _, err := ExportArgs(bad.id, bad.r); err == nil {
			t.Errorf("%+v accepted", bad)
		}
	}
}

func TestExportRoute(t *testing.T) {
	s, rec := testServer(t, map[string]string{"export": `{"ok": true, "files": ["/x/r1.json"], "count": 2}`})
	h, host := s.Handler(), "127.0.0.1:8787"
	hdr := map[string]string{tokenHeader: s.Token}
	w := do(h, "POST", "/api/export/r1", host, hdr, `{"format":"json","by":"group"}`)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"count": 2`) {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"export", "r1", "--format", "json", "--by", "group"}) {
		t.Fatalf("%q", last)
	}
	n := len(rec.calls)
	for _, body := range []string{`{}`, `{"format":"xml"}`, `not json`, `{"format":"json","group":"x"}`} {
		if w := do(h, "POST", "/api/export/r1", host, hdr, body); w.Code != 400 {
			t.Errorf("%q: %d", body, w.Code)
		}
	}
	if w := do(h, "POST", "/api/export/-x", host, hdr, `{"format":"json"}`); w.Code != 400 {
		t.Errorf("bad id: %d", w.Code)
	}
	if len(rec.calls) != n {
		t.Errorf("a bad request reached the manager")
	}
	s2, _ := testServer(t, map[string]string{"export": `{"ok": false, "why": "the run is still running"}`})
	if w := do(s2.Handler(), "POST", "/api/export/r1", host, map[string]string{tokenHeader: s2.Token}, `{"format":"json"}`); w.Code != 409 {
		t.Errorf("refusal: %d", w.Code)
	}
}
