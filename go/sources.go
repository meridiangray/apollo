package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// The Sources view: files dropped on the window are received here, put in apollo/sources/incoming/, and taken in by the
// manager (`sourceadd`, which deletes the incoming copy). Also the sources' list and edits, a page of a source's text,
// and an archive's collections: each a thin pass over one manager command.

const maxUpload = 500 << 20 // bytes in one upload

var uploadFormats = map[string]bool{".pdf": true, ".txt": true, ".md": true, ".html": true, ".htm": true, ".docx": true,
	".jpg": true, ".jpeg": true, ".png": true, ".tif": true, ".tiff": true, ".webp": true, ".gif": true,
	".mp4": true, ".mov": true, ".webm": true, ".mkv": true, ".mp3": true, ".wav": true, ".m4a": true, ".flac": true}

var (
	reSourceSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,59}$`)
	reUnsafeName = regexp.MustCompile(`[^A-Za-z0-9 ._()\-]`)
	rePageNo     = regexp.MustCompile(`^\d{1,6}$`)
	reCollName   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$`) // a hydra collection name
)

// incomingDir is apollo/sources/incoming beside the manager, as this OS shows it.
func (s *Server) incomingDir() string {
	if v := os.Getenv("APOLLO_SOURCES"); v != "" && s.GOOS != "windows" { // the manager's own override (tests, a second Apollo)
		return filepath.Join(v, "incoming")
	}
	return ToHost(s.GOOS, path.Dir(s.Backend.Manager)+"/sources/incoming")
}

// safeName is an uploaded file's name kept to plain characters, with its extension.
func safeName(name string) string {
	base := filepath.Base(strings.ReplaceAll(name, `\`, "/"))
	base = strings.TrimSpace(reUnsafeName.ReplaceAllString(base, "_"))
	if base == "" || base == "." || base == ".." {
		return "source"
	}
	if len(base) > 120 {
		ext := filepath.Ext(base)
		base = base[:120-len(ext)] + ext
	}
	return base
}

// UploadResult is one file's outcome: taken in as a source, or refused with the reason.
type UploadResult struct {
	Name  string   `json:"name"`
	OK    bool     `json:"ok"`
	Slug  string   `json:"slug,omitempty"`
	Needs []string `json:"needs,omitempty"`
	Why   string   `json:"why,omitempty"`
}

func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUpload)
	mr, err := r.MultipartReader()
	if err != nil {
		fail(w, http.StatusBadRequest, "send the files as a multipart form")
		return
	}
	root := s.incomingDir()
	var results []UploadResult
	var saved []string
	cleanup := func() {
		for _, d := range saved {
			os.RemoveAll(d)
		}
	}
	type pending struct{ name, file string }
	var todo []pending
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			cleanup()
			var tooBig *http.MaxBytesError
			if errors.As(err, &tooBig) {
				fail(w, http.StatusRequestEntityTooLarge, "Files over 500 MB are refused.")
			} else {
				fail(w, http.StatusBadRequest, "the upload could not be read: "+err.Error())
			}
			return
		}
		if part.FileName() == "" {
			continue
		}
		name := safeName(part.FileName())
		if !uploadFormats[strings.ToLower(filepath.Ext(name))] {
			results = append(results, UploadResult{Name: name, Why: "Apollo keeps PDF, TXT, MD, HTML and DOCX files, images, video and audio."})
			io.Copy(io.Discard, part)
			continue
		}
		var b [8]byte
		rand.Read(b[:])
		dir := filepath.Join(root, hex.EncodeToString(b[:]))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			cleanup()
			fail(w, http.StatusInternalServerError, "cannot make the incoming folder: "+err.Error())
			return
		}
		saved = append(saved, dir)
		f, err := os.Create(filepath.Join(dir, name))
		if err == nil {
			_, err = io.Copy(f, part)
			f.Close()
		}
		if err != nil {
			cleanup()
			var tooBig *http.MaxBytesError
			if errors.As(err, &tooBig) {
				fail(w, http.StatusRequestEntityTooLarge, "Files over 500 MB are refused.")
			} else {
				fail(w, http.StatusInternalServerError, "cannot save "+name+": "+err.Error())
			}
			return
		}
		todo = append(todo, pending{name, filepath.Join(dir, name)})
	}
	for _, p := range todo {
		raw, err := s.Backend.Call(r.Context(), 10*time.Minute, "sourceadd", ToWSL(p.file))
		res := UploadResult{Name: p.name}
		var got struct {
			OK    bool     `json:"ok"`
			Slug  string   `json:"slug"`
			Needs []string `json:"needs"`
			Why   string   `json:"why"`
		}
		switch {
		case err != nil:
			res.Why = err.Error()
		case json.Unmarshal(raw, &got) != nil:
			res.Why = "no answer from the manager"
		case !got.OK:
			res.Why = got.Why
		default:
			res.OK, res.Slug, res.Needs = true, got.Slug, got.Needs
		}
		results = append(results, res)
	}
	cleanup() // the manager deletes what it took; this clears anything it could not reach
	s.cache.clear()
	writeJSON(w, http.StatusOK, map[string]any{"results": results})
}

// sourceFields are the only fields the page may change on a dropped source.
type sourceFields struct {
	Title   *string `json:"title,omitempty"`
	Context *string `json:"context,omitempty"`
	Brief   *string `json:"brief,omitempty"`
	Pages   *string `json:"pages,omitempty"`
	// the facts the card edits in place
	Publisher *string `json:"publisher,omitempty"`
	Language  *string `json:"language,omitempty"`
	URL       *string `json:"url,omitempty"`
	Retrieved *string `json:"retrieved,omitempty"`
}

func (s *Server) sourceUpdate(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	var f sourceFields
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<17))
	if err == nil {
		dec := json.NewDecoder(strings.NewReader(string(body)))
		dec.DisallowUnknownFields()
		err = dec.Decode(&f)
	}
	if !reSourceSlug.MatchString(slug) || err != nil {
		fail(w, http.StatusBadRequest, "a dropped source and a JSON body: title, context, brief or pages")
		return
	}
	fields, _ := json.Marshal(f)
	raw, err := s.Backend.Call(r.Context(), 60*time.Second, "sourceupdate", slug, "--fields", string(fields))
	s.cache.clear()
	s.outcome(w, raw, err)
}

func (s *Server) sourceRemove(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	if !reSourceSlug.MatchString(slug) {
		fail(w, http.StatusBadRequest, "a dropped source")
		return
	}
	raw, err := s.Backend.Call(r.Context(), 60*time.Second, "sourceremove", slug)
	s.cache.clear()
	s.outcome(w, raw, err)
}

// passJSON writes the manager's one JSON answer, or its refusal ({ok: false, why}) as an error with `refused` as the status.
func passJSON(w http.ResponseWriter, raw []byte, err error, refused int) {
	if err != nil {
		fail(w, http.StatusBadGateway, err.Error())
		return
	}
	var probe struct {
		OK  *bool  `json:"ok"`
		Why string `json:"why"`
	}
	if json.Unmarshal(raw, &probe) != nil {
		fail(w, http.StatusBadGateway, "the manager's answer is not JSON")
		return
	}
	if probe.OK != nil && !*probe.OK {
		fail(w, refused, probe.Why)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Write(raw)
}

func (s *Server) sources(w http.ResponseWriter, r *http.Request) {
	raw, err := s.call(r, 700*time.Millisecond, "sourcelist")
	passJSON(w, raw, err, http.StatusConflict)
}

// pagetext is one page of a source's text (a part, for a text source).
func (s *Server) pagetext(w http.ResponseWriter, r *http.Request) {
	src, page := r.URL.Query().Get("source"), r.URL.Query().Get("page")
	if !reSlug.MatchString(src) || !rePageNo.MatchString(page) {
		fail(w, http.StatusBadRequest, "a source name and a page number")
		return
	}
	raw, err := s.call(r, 5*time.Second, "pagetext", src, page)
	passJSON(w, raw, err, http.StatusNotFound)
}

// collections are an archive's collections and groups, or (?collection=NAME, ?group=KEY) one collection's or one group's
// records from ?offset=.
func (s *Server) collections(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	a := strings.TrimSpace(q.Get("archive"))
	if a == "" || strings.HasPrefix(a, "-") || strings.ContainsAny(a, "\x00\r\n") {
		fail(w, http.StatusBadRequest, "pick an archive")
		return
	}
	args := []string{"collections", managerPath(s.GOOS, a)}
	c, flag := q.Get("collection"), "--collection"
	if g := q.Get("group"); g != "" {
		c, flag = g, "--group"
	}
	if c != "" {
		if strings.HasPrefix(c, "-") || strings.ContainsAny(c, "\x00\r\n/\\") {
			fail(w, http.StatusBadRequest, "a collection name")
			return
		}
		args = append(args, flag, c)
		if o := q.Get("offset"); o != "" {
			if !rePageNo.MatchString(o) {
				fail(w, http.StatusBadRequest, "offset: a whole number")
				return
			}
			args = append(args, "--offset", o)
		}
	}
	raw, err := s.call(r, 700*time.Millisecond, args...)
	passJSON(w, raw, err, http.StatusConflict)
}

// collectionGroup makes a Complex Collection ({archive, title, collections}): a hydra collection group of collections from
// several sources. hydra checks it, rebuilds the archive and takes one history step, so it may take a while.
func (s *Server) collectionGroup(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Archive     string   `json:"archive"`
		Title       string   `json:"title"`
		Collections []string `json:"collections"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	a, t := strings.TrimSpace(req.Archive), strings.TrimSpace(req.Title)
	ok := err == nil && a != "" && !strings.HasPrefix(a, "-") && !strings.ContainsAny(a, "\x00\r\n") &&
		t != "" && !strings.ContainsAny(t, "\x00\r\n") && len(req.Collections) >= 2
	for _, c := range req.Collections {
		ok = ok && reCollName.MatchString(c)
	}
	if !ok {
		fail(w, http.StatusBadRequest, "a JSON body: archive, title, collections (two or more)")
		return
	}
	s.groupMu.Lock()
	defer s.groupMu.Unlock()
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 5*time.Minute, "collectiongroup", managerPath(s.GOOS, a),
		"--title="+t, "--collections="+strings.Join(req.Collections, ","))
	s.cache.clear()
	passJSON(w, raw, err, http.StatusConflict)
}

// sourceThumb is the first page of a dropped PDF as a small PNG, made by the manager (pdftoppm) and kept under state/thumbs.
func (s *Server) sourceThumb(w http.ResponseWriter, r *http.Request) {
	slug := r.URL.Query().Get("slug")
	if !reSourceSlug.MatchString(slug) {
		fail(w, http.StatusBadRequest, "a dropped source")
		return
	}
	raw, err := s.call(r, 5*time.Second, "sourcethumb", slug)
	var got struct {
		OK   bool   `json:"ok"`
		Path string `json:"path"`
		Why  string `json:"why"`
	}
	if err != nil || json.Unmarshal(raw, &got) != nil || !got.OK || got.Path == "" {
		http.NotFound(w, r)
		return
	}
	ct := mime.TypeByExtension(strings.ToLower(filepath.Ext(got.Path))) // a PDF's page is a PNG; a photo is its own file
	if !strings.HasPrefix(ct, "image/") {
		ct = "image/png"
	}
	w.Header().Set("Content-Type", ct)
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeFile(w, r, ToHost(s.GOOS, got.Path))
}

// wikipedia takes one article in as a text source: the single network call Apollo makes for a source, and only when the
// person presses Add. The manager fetches it (hydra's own extraction) and refuses in plain words.
func (s *Server) wikipedia(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Lang  string `json:"lang"`
		Query string `json:"query"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<14))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	q := strings.TrimSpace(req.Query)
	lang := strings.ToLower(strings.TrimSpace(req.Lang))
	if lang == "" {
		lang = "en"
	}
	if err != nil || q == "" || len(q) > 400 || strings.HasPrefix(q, "-") || strings.ContainsAny(q, "\x00\r\n") || !reLangWiki.MatchString(lang) {
		fail(w, http.StatusBadRequest, "a language code and an article title or address")
		return
	}
	raw, err := s.Backend.Call(r.Context(), 90*time.Second, "wikipedia", "--lang", lang, "--query", q)
	s.cache.clear()
	s.outcome(w, raw, err)
}

var reLangWiki = regexp.MustCompile(`^[a-z]{2,3}(-[a-z]+)?$`)

// notice logs a toast the window showed (kind error, warn or info) in the DuckDB log, through the manager.
func (s *Server) notice(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Level string `json:"level"`
		Text  string `json:"text"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<13))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	t := strings.TrimSpace(req.Text)
	if err != nil || t == "" || len(t) > 500 || strings.HasPrefix(t, "-") || strings.ContainsAny(t, "\x00") || (req.Level != "error" && req.Level != "warn" && req.Level != "info") {
		fail(w, http.StatusBadRequest, "a level (error, warn, info) and a line of text")
		return
	}
	raw, err := s.Backend.Call(r.Context(), 20*time.Second, "notice", "--level", req.Level, "--text", t)
	s.outcome(w, raw, err)
}

// collectionGroupEdit renames a Complex Collection ({archive, key, title}) or, with no title, ungroups it ({archive, key}). Only
// the grouping changes, never a collection or a record, so it needs no confirmation: the History is the undo.
func (s *Server) collectionGroupEdit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Archive string  `json:"archive"`
		Key     string  `json:"key"`
		Title   *string `json:"title"`
		// Collections: the group's new members (two or more), instead of a title
		Collections []string `json:"collections"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<16))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	a, k := strings.TrimSpace(req.Archive), strings.TrimSpace(req.Key)
	ok := err == nil && a != "" && !strings.HasPrefix(a, "-") && !strings.ContainsAny(a, "\x00\r\n") && reCollName.MatchString(k)
	args := []string{"--key=" + k}
	if ok && req.Title != nil {
		t := strings.TrimSpace(*req.Title)
		ok = t != "" && !strings.ContainsAny(t, "\x00\r\n")
		args = append(args, "--title="+t)
	}
	if ok && len(req.Collections) > 0 {
		ok = req.Title == nil && len(req.Collections) >= 2
		for _, c := range req.Collections {
			ok = ok && reCollName.MatchString(c)
		}
		args = append(args, "--collections="+strings.Join(req.Collections, ","))
	}
	if !ok {
		fail(w, http.StatusBadRequest, "a JSON body: archive, key, and a title to rename or collections to hold (none to ungroup)")
		return
	}
	s.groupMu.Lock()
	defer s.groupMu.Unlock()
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 5*time.Minute, append([]string{"collectiongroupedit", managerPath(s.GOOS, a)}, args...)...)
	s.cache.clear()
	passJSON(w, raw, err, http.StatusConflict)
}
