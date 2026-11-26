package main

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"os/exec"
	"sync"
)

// worker is one long-lived `apollo.py serve` for the window's quick, read-only calls: a JSON line out, a JSON line
// back, so a call costs no new wsl.exe, no new Python and no new imports (~0.3 s -> tens of ms). One call at a time.
// A timeout or a broken pipe ends it; the next call starts a new one, and Call falls back to one process per call
// whenever the worker cannot answer.
type worker struct {
	mu   sync.Mutex
	argv []string
	cmd  *exec.Cmd
	in   io.WriteCloser
	out  *bufio.Reader
}

// workerCalls are the manager commands the worker answers: read-only and quick (analyse only reads a source and its own
// cache, and the worker already holds the driver's imports). Starting, stopping and clearing keep their own process.
var workerCalls = map[string]bool{"ls": true, "status": true, "view": true, "configs": true, "recipe": true,
	"prompt": true, "tail": true, "analyse": true, "toolget": true, "toolabout": true, "sourcelist": true, "pagetext": true, "collections": true, "entitytrace": true,
	"proofreadget": true, "catalogueget": true, "catalogueexport": true, "history": true, "logdb": true, "choices": true, "pageview": true, "settings": true}

func (w *worker) start() error {
	cmd := exec.Command(w.argv[0], w.argv[1:]...)
	hideWindow(cmd)
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	w.cmd, w.in, w.out = cmd, in, bufio.NewReaderSize(out, 1<<20)
	return nil
}

func (w *worker) stop() {
	if w.cmd != nil && w.cmd.Process != nil {
		w.in.Close()
		w.cmd.Process.Kill()
		w.cmd.Wait()
	}
	w.cmd, w.in, w.out = nil, nil, nil
}

// call sends one request and waits for its answer until ctx ends.
func (w *worker) call(ctx context.Context, args []string) ([]byte, int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.cmd == nil {
		if err := w.start(); err != nil {
			return nil, 0, err
		}
	}
	req, _ := json.Marshal(map[string][]string{"args": args})
	type result struct {
		line []byte
		err  error
	}
	ch := make(chan result, 1)
	in, out := w.in, w.out
	go func() {
		if _, err := in.Write(append(req, '\n')); err != nil {
			ch <- result{nil, err}
			return
		}
		line, err := out.ReadBytes('\n')
		ch <- result{line, err}
	}()
	select {
	case r := <-ch:
		if r.err != nil {
			w.stop()
			return nil, 0, r.err
		}
		var reply struct {
			Code int    `json:"code"`
			Out  string `json:"out"`
		}
		if err := json.Unmarshal(r.line, &reply); err != nil {
			w.stop()
			return nil, 0, err
		}
		return []byte(reply.Out), reply.Code, nil
	case <-ctx.Done():
		w.stop()
		return nil, 0, ctx.Err()
	}
}
