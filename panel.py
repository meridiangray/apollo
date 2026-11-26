"""panel.py: one run's view in tkinter, the only window code: the Apollo app's right side, and the single-run window
`python pageflow/progress.py <run>/pageflow` opens (progress.window).

Head (fraction and pass, a bar, the counts, the page strip), then tabs: Run (the Trace lines, newest first, repeats
folded), Found (one line per record, by page), Questions (the `ask_person` cards), Calls (hydra calls, the slowest
marked), Report (report.md as text). Everything is read from the run's files (runfiles.RunFiles); nothing here starts,
stops or writes anything, so closing a window never stops a run.
"""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import runfiles
from runfiles import CATEGORIES, STATUS_COLOURS

MONO = ("TkFixedFont", 9)
SLOW_EVERY = 4.0                                             # seconds between reads of the archive's files


class RunPanel(tk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.files: runfiles.RunFiles | None = None
        self.head, self.sub = tk.StringVar(value=""), tk.StringVar(value="")
        tk.Label(self, textvariable=self.head, font=("TkDefaultFont", 13, "bold"), anchor="w").pack(fill="x", padx=8,
                                                                                                    pady=(8, 2))
        self.bar = ttk.Progressbar(self, maximum=1000)
        self.bar.pack(fill="x", padx=8, pady=4)
        tk.Label(self, textvariable=self.sub, anchor="w", fg="#555").pack(fill="x", padx=8)
        self.strip = tk.Canvas(self, height=26, highlightthickness=0)
        self.strip.pack(fill="x", padx=8, pady=(6, 2))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(6, 8))

        self.run_box = self._textbox("Run")
        found = tk.Frame(self.tabs)
        top = tk.Frame(found)
        top.pack(fill="x")
        self.found_count = tk.StringVar(value="")
        self.show_quotes = tk.BooleanVar(value=False)        # quotes hidden by default: one line per record
        tk.Label(top, textvariable=self.found_count, anchor="w", fg="#555").pack(side="left")
        tk.Checkbutton(top, text="Show quotes", variable=self.show_quotes,
                       command=lambda: self._draw_found()).pack(side="right")
        self.found_box = self._textbox(None, found)
        self.tabs.add(found, text="Found")
        self.q_box = self._textbox("Questions")
        calls = tk.Frame(self.tabs)
        self.calls_head = tk.StringVar(value="")
        tk.Label(calls, textvariable=self.calls_head, anchor="w", fg="#555").pack(fill="x")
        self.calls_box = self._textbox(None, calls)
        self.tabs.add(calls, text="Calls")
        self.report_box = self._textbox("Report", wrap="word")

        self.run_box.tag_configure("fail", foreground="#b3261e")
        self.run_box.tag_configure("phase", font=(MONO[0], MONO[1], "bold"))
        self.run_box.tag_configure("time", foreground="#888")
        for cat, colour in {c: a for c, a in CATEGORIES.values()}.items():
            self.found_box.tag_configure(cat, foreground=colour, font=(MONO[0], MONO[1], "bold"))
        self.found_box.tag_configure("quote", foreground="#5b4a8a", font=(MONO[0], MONO[1], "italic"))
        self.found_box.tag_configure("dashed", foreground="#999")
        self.found_box.tag_configure("where", foreground="#888")
        self.q_box.tag_configure("open", foreground="#8a5a00", font=(MONO[0], MONO[1], "bold"))
        self.q_box.tag_configure("answered", foreground="#2e7d4f")
        self.q_box.tag_configure("dismissed", foreground="#999")
        self.q_box.tag_configure("quote", foreground="#5b4a8a", font=(MONO[0], MONO[1], "italic"))
        self.q_box.tag_configure("dim", foreground="#666")
        self.calls_box.tag_configure("slow", background="#f3e3b5", font=(MONO[0], MONO[1], "bold"))
        self.calls_box.tag_configure("err", foreground="#b3261e")
        self.report_box.tag_configure("h1", font=("TkDefaultFont", 13, "bold"))
        self.report_box.tag_configure("h2", font=("TkDefaultFont", 11, "bold"), spacing1=8)
        self.report_box.tag_configure("h3", font=("TkDefaultFont", 10, "bold"), spacing1=4)
        self.report_box.tag_configure("table", font=MONO)
        self._reset()

    def _textbox(self, title, parent=None, wrap="none"):
        frame = tk.Frame(parent or self.tabs)
        t = tk.Text(frame, font=MONO, state="disabled", wrap=wrap)
        sb = ttk.Scrollbar(frame, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        if parent is not None:
            frame.pack(fill="both", expand=True)
        if title:
            self.tabs.add(frame, text=title)
        return t

    def _reset(self):
        self.seen = {"trace": -1, "found": -1, "calls": None, "questions": None, "report": None, "slow_at": 0.0,
                     "calls_at": 0.0}

    # ---- what is shown ----
    def show(self, files: runfiles.RunFiles | None) -> None:
        """Show another run (None: nothing)."""
        self.files = files
        self._reset()
        for box in (self.run_box, self.found_box, self.q_box, self.calls_box, self.report_box):
            _fill(box, [])
        self.head.set("")
        self.sub.set("")
        self.found_count.set("")
        self.calls_head.set("")
        self.bar["value"] = 0
        self.strip.delete("all")

    def refresh(self, summary: dict | None = None) -> None:
        """Read what changed and redraw it. `summary`: Apollo's view of the run (status, kind), when there is one."""
        f = self.files
        if f is None:
            return
        self._head(f.progress(), summary)
        rows = f.trace()
        if len(rows) != self.seen["trace"]:
            self.seen["trace"] = len(rows)
            _fill(self.run_box, [[(at + "  ", "time"), (text, tag)] for at, text, tag in runfiles.run_lines(rows)])
        found = f.found()
        if len(found) != self.seen["found"]:
            self.seen["found"] = len(found)
            self._draw_found()
        calls = None
        if f.kind == "pageflow" or time.time() - self.seen["calls_at"] >= 2:     # a session's: the archive's logs
            self.seen["calls_at"] = time.time()
            calls = f.calls()
        if calls is not None:
            sig = (len(calls), sum(c.get("ms") or 0 for c in calls), sum(c.get("ok") is False for c in calls))
            if sig != self.seen["calls"]:
                self.seen["calls"] = sig
                self.calls_head.set(runfiles.calls_head(calls))
                lines = []
                for c in runfiles.call_rows(calls):
                    tag = "err" if c.get("ok") is False else "slow" if c["slow"] else ""
                    lines.append([(runfiles.call_line(c), tag)])
                _fill(self.calls_box, lines)
        if time.time() - self.seen["slow_at"] >= SLOW_EVERY:  # the archive's files and the report: now and then
            self.seen["slow_at"] = time.time()
            qs = f.questions()
            sig = [(q["id"], runfiles.question_status(q)) for q in qs]
            if sig != self.seen["questions"]:
                self.seen["questions"] = sig
                self.tabs.tab(2, text=f"Questions ({sum(s == 'open' for _, s in sig)})" if sig else "Questions")
                _fill(self.q_box, [[x] for x in runfiles.question_lines(qs)])
            rep = f.report()
            if rep != self.seen["report"]:
                self.seen["report"] = rep
                _fill(self.report_box, [[x] for x in runfiles.report_lines(rep)], keep_view=True)

    def _head(self, p: dict | None, s: dict | None) -> None:
        if p:
            self.bar["value"] = int(p.get("fraction", 0) * 1000)
            status = (s or {}).get("status") or p.get("status")
            self.head.set(f"{p.get('fraction', 0):.0%}  {p.get('label', '')}" +
                          ("" if status in ("running", None) else f"  ({status})"))
            pages = p.get("pages") or {}
            content = [v for v in pages.values() if v in ("todo", "doing", "done", "escalated", "gave-up")]
            done = sum(v in ("done", "escalated", "gave-up") for v in content)
            m, sec = divmod(int(p.get("elapsed") or 0), 60)
            self.sub.set((f"{p['done']} of {p['total']} in this pass · " if p.get("total") else "")
                         + (f"pages {done} of {len(content)} through · " if content else "") + f"{m}:{sec:02d} elapsed")
            self._strip(pages)
        elif s:
            fr = (s.get("progress") or {}).get("fraction")
            self.bar["value"] = int((fr or 0) * 1000)
            self.head.set(f"{s['name']}  ({s['status']})")
            c = s.get("cost") or {}
            self.sub.set(" · ".join(x for x in (f"{s['calls']['hydra']} calls",
                                                 runfiles.cost_text(c, c.get("budget_usd")),
                                                 f"{c['turns']} turns" if c.get("turns") else "",
                                                 runfiles.elapsed_text(s.get("elapsed_s")) + " elapsed") if x))

    def _strip(self, pages: dict) -> None:
        self.strip.delete("all")
        shown = [(int(k), v) for k, v in pages.items() if STATUS_COLOURS.get(v)]
        if not shown:
            return
        w = max(self.strip.winfo_width(), 300)
        cell = max(3, min(22, (w - 4) // len(shown)))
        for i, (n, st) in enumerate(shown):
            x = 2 + i * cell
            self.strip.create_rectangle(x, 2, x + cell - 2, 22, fill=STATUS_COLOURS[st], outline="")
            if cell >= 18:
                self.strip.create_text(x + cell / 2 - 1, 12, text=str(n % 100), font=("TkDefaultFont", 7),
                                       fill="white" if st in ("done", "redacted", "escalated") else "#333")

    def _draw_found(self) -> None:
        if self.files is None:
            return
        lines, count = runfiles.found_lines(self.files.found(), self.show_quotes.get())
        _fill(self.found_box, lines, keep_view=True)
        self.found_count.set(count)


def _fill(box: tk.Text, lines: list, keep_view: bool = False) -> None:
    """Replace a read-only Text's content with lines of (text, tag) pieces."""
    y = box.yview()[0]
    box.configure(state="normal")
    box.delete("1.0", "end")
    for pieces in lines:
        for text, tag in pieces:
            box.insert("end", text, tag or ())
        box.insert("end", "\n")
    box.configure(state="disabled")
    if keep_view:
        box.yview_moveto(y)


def single_window(folder) -> None:
    """The one-run window: `folder` is a run's `pageflow/` folder (or the run folder itself)."""
    folder = Path(folder)
    run = folder.parent if folder.name == "pageflow" else folder
    kind = "pageflow" if (run / "pageflow").is_dir() or not (run / "transcript.jsonl").exists() else "mcp-session"
    root = tk.Tk()
    root.title(f"pageflow · {run.name}")
    root.geometry("760x600")
    panel = RunPanel(root)
    panel.pack(fill="both", expand=True)
    panel.show(runfiles.RunFiles(run, kind))

    def poll():
        try:
            panel.refresh()
        except Exception:                                    # noqa: BLE001 - a half-written file: next time
            pass
        root.after(500, poll)
    poll()
    root.mainloop()
