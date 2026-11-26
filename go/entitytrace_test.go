package main

import (
	"reflect"
	"testing"
)

func TestEntityTraceGoesToTheManager(t *testing.T) {
	s, rec := testServer(t, map[string]string{"entitytrace": `{"ok": true, "lines": []}`})
	h, host := s.Handler(), "127.0.0.1:8787"
	if w := do(h, "GET", "/api/entitytrace?archive=/a/b&record=p-1", host, nil, ""); w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	if last := rec.calls[len(rec.calls)-1]; !reflect.DeepEqual(last[2:], []string{"entitytrace", "--archive", "/a/b", "--json", "--record=p-1"}) {
		t.Fatalf("%q", last)
	}
	if w := do(h, "GET", "/api/entitytrace?archive=/a/b&source=minutes&title=The+Minutes", host, nil, ""); w.Code != 200 {
		t.Errorf("source: %d", w.Code)
	}
	for _, bad := range []string{"archive=--x&record=a", "archive=/a/b", "archive=/a/b&source=../x", "archive=/a/b&record=a%0Ab"} {
		if w := do(h, "GET", "/api/entitytrace?"+bad, host, nil, ""); w.Code != 400 {
			t.Errorf("%s: %d", bad, w.Code)
		}
	}
}
