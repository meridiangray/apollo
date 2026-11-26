package main

import (
	"net/http"
	"strings"
	"time"
)

// entitytrace is one entity's compact Trace (?record=ID, or ?name=N&schema=S for an entity no record holds yet), or the
// entities a source gave (?source=SLUG&title=T), both read by the manager from the log, the run folders and the archive's history.
func (s *Server) entitytrace(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	a := strings.TrimSpace(q.Get("archive"))
	if a == "" || strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
		fail(w, http.StatusBadRequest, "pick an archive")
		return
	}
	args := []string{"entitytrace", "--archive", managerPath(s.GOOS, a), "--json"}
	for _, k := range []string{"record", "name", "schema", "source", "title"} {
		v := q.Get(k)
		if v == "" {
			continue
		}
		if strings.ContainsAny(v, "\x00\r\n") || len(v) > 400 {
			fail(w, http.StatusBadRequest, k+": plain text")
			return
		}
		if k == "source" && !reSourceSlug.MatchString(v) {
			fail(w, http.StatusBadRequest, "a source name")
			return
		}
		args = append(args, "--"+k+"="+v)
	}
	if q.Get("record") == "" && q.Get("name") == "" && q.Get("source") == "" {
		fail(w, http.StatusBadRequest, "a record, a name or a source")
		return
	}
	raw, err := s.call(r, 8*time.Second, args...)
	passJSON(w, raw, err, http.StatusConflict)
}
