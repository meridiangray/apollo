package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

// Chat: the bubble's conversation (chat.js, chat.py). POST /api/chat {messages, model, context, session} starts one reply in its
// own manager process and answers at once {ok, id, session}; GET /api/chat/{id} is that reply: {status: running|done|failed,
// text, error}. The page polls it, as it polls a Catalogue job. The page keeps the conversation and sends it whole.

const (
	chatMaxText    = 4000  // characters kept of one message (chat.py MAX_TEXT)
	chatMaxMsgs    = 50    // messages sent, newest kept
	chatMaxPayload = 18000 // bytes of JSON on the command line before base64 (Windows caps a command line at 32767 characters)
)

var (
	reChatSession = regexp.MustCompile(`^\d{8}T\d{6}Z-[0-9a-f]{6}$`)
	reChatJob     = regexp.MustCompile(`^\d{8}T\d{6}Z-[0-9a-f]{6}-\d{1,4}$`)
	chatViews     = map[string]bool{"runs": true, "configure": true, "sources": true, "collections": true, "history": true}
)

func init() { workerCalls["chatget"] = true } // a file read: the long-lived manager answers it

type ChatMessage struct {
	Role string `json:"role"`
	Text string `json:"text"`
}

type ChatContext struct {
	View    string `json:"view,omitempty"`
	Run     string `json:"run,omitempty"`
	Source  string `json:"source,omitempty"`
	Archive string `json:"archive,omitempty"`
}

type ChatRequest struct {
	Session  string        `json:"session,omitempty"`
	Model    string        `json:"model"`
	Messages []ChatMessage `json:"messages"`
	Context  ChatContext   `json:"context"`
}

// clip keeps the first n characters of s (whole runes).
func clip(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n])
}

// ChatArgs checks a request and builds `chatstart --req64 <base64 JSON>`: messages clipped and, from the oldest, dropped until the
// JSON fits the command line; the context's odd values dropped. The manager checks everything again.
func ChatArgs(r ChatRequest) ([]string, error) {
	if r.Model == "" {
		r.Model = "sonnet"
	}
	if !reTier.MatchString(r.Model) {
		return nil, errString("model: haiku, sonnet or opus")
	}
	if r.Session != "" && !reChatSession.MatchString(r.Session) {
		return nil, errString("not a chat session")
	}
	var msgs []ChatMessage
	for _, m := range r.Messages {
		if m.Role != "user" && m.Role != "assistant" {
			return nil, errString("each message: role user or assistant, and text")
		}
		if t := strings.TrimSpace(m.Text); t != "" {
			msgs = append(msgs, ChatMessage{m.Role, clip(t, chatMaxText)})
		}
	}
	if len(msgs) == 0 || msgs[len(msgs)-1].Role != "user" {
		return nil, errString("the last message is the person's")
	}
	if len(msgs) > chatMaxMsgs {
		msgs = msgs[len(msgs)-chatMaxMsgs:]
	}
	c := r.Context
	if !chatViews[c.View] {
		c.View = ""
	}
	if c.Run != "" && !validID(c.Run) {
		c.Run = ""
	}
	if c.Source != "" && !reSlug.MatchString(c.Source) {
		c.Source = ""
	}
	if len(c.Archive) > 500 || strings.ContainsFunc(c.Archive, func(r rune) bool { return r < 32 }) {
		c.Archive = ""
	}
	for {
		b, err := json.Marshal(ChatRequest{Session: r.Session, Model: r.Model, Messages: msgs, Context: c})
		if err != nil {
			return nil, err
		}
		if len(b) <= chatMaxPayload {
			return []string{"chatstart", "--req64", base64.RawURLEncoding.EncodeToString(b)}, nil
		}
		if len(msgs) == 1 { // one message too long for the line: keep its end-trimmed start
			msgs[0].Text = clip(msgs[0].Text, utf8.RuneCountInString(msgs[0].Text)*3/4)
			continue
		}
		msgs = msgs[1:]
		for len(msgs) > 1 && msgs[0].Role != "user" { // the thread starts with the person
			msgs = msgs[1:]
		}
	}
}

func (s *Server) chatStart(w http.ResponseWriter, r *http.Request) {
	var req ChatRequest
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		fail(w, http.StatusBadRequest, "a JSON body: messages, model, context")
		return
	}
	args, err := ChatArgs(req)
	if err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 60*time.Second, args...)
	passJSON(w, raw, err, http.StatusConflict)
}

func (s *Server) chatGet(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !reChatJob.MatchString(id) {
		fail(w, http.StatusBadRequest, "not a chat reply")
		return
	}
	raw, err := s.Backend.Call(context.WithoutCancel(r.Context()), 10*time.Second, "chatget", "--id", id)
	passJSON(w, raw, err, http.StatusNotFound)
}

// chatRoutes adds the chat's page files and its API to the app's routes.
func (s *Server) chatRoutes(mux *http.ServeMux, files http.Handler) {
	mux.Handle("GET /chat.js", files)
	mux.Handle("GET /chat.css", files)
	mux.HandleFunc("POST /api/chat", s.chatStart)
	mux.HandleFunc("GET /api/chat/{id}", s.chatGet)
}
