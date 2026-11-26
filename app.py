#!/usr/bin/env python3
"""app.py: the Apollo window. Left, every run Apollo knows; right, the selected run (panel.py's tabs).

    ~/.venvs/apollo/bin/python /mnt/e/Documents/development/stable/apollo/app.py

Plain tkinter, refreshed twice a second from the runs' files and apollo/state/. Start… picks a page-flow config (.yaml)
or a brief (.md) and starts it through apollo.py (Dry is ticked by default: no model calls); Stop signals the selected
run's own process group; Open folder prints the run's folder. Closing the window never stops a run: each run's wrapper
leads its own session, and every run can be watched again from a new window or `apollo.py ls`.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import apollo  # noqa: E402
import runfiles  # noqa: E402

COLUMNS = [("name", "Run", 230), ("kind", "Kind", 90), ("model", "Model", 130), ("status", "Status", 80),
           ("progress", "Progress", 120), ("calls", "Calls", 55), ("cost", "Cost", 150), ("elapsed", "Elapsed", 70)]


# ---- reading (no GUI: tested) ---------------------------------------------------------------------------------------

class Reader:
    """Every run's summary, with one RunFiles per run kept between reads so each read takes only what is new."""

    def __init__(self):
        self.files: dict[str, runfiles.RunFiles] = {}

    def files_of(self, rec: dict) -> runfiles.RunFiles:
        f = self.files.get(rec["id"])
        if f is None:
            f = self.files[rec["id"]] = runfiles.RunFiles(rec["run_folder"], rec["kind"], rec.get("archive"))
        return f

    def read(self) -> list[dict]:
        apollo.reap()
        out = []
        for rec in apollo.records():
            try:
                out.append(apollo.summary(rec, self.files_of(rec)))
            except Exception as e:                            # noqa: BLE001 - a half-written file: shown, next time
                out.append({"id": rec["id"], "name": rec.get("name"), "kind": rec["kind"], "model": rec.get("model"),
                            "status": "?", "detail": str(e), "progress": {"fraction": None}, "calls": {"hydra": 0},
                            "cost": {}, "elapsed_s": None})
        return out


def row_values(s: dict) -> tuple:
    """A runs-list row: name, kind, model, status, a text bar, hydra calls, cost or budget, elapsed."""
    c = s.get("cost") or {}
    name = s.get("name") or s["id"]
    if s.get("dry"):
        name += " (dry)"
    return (name, s["kind"], s.get("model") or "", s["status"], runfiles.bar_text((s.get("progress") or {}).get("fraction")),
            (s.get("calls") or {}).get("hydra", 0), runfiles.cost_text(c, c.get("budget_usd")),
            runfiles.elapsed_text(s.get("elapsed_s")))


# ---- the window -----------------------------------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        import panel
        self.root, self.reader, self.summaries, self.selected = root, Reader(), {}, None
        root.title("Apollo")
        root.geometry("1360x780")
        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = tk.Frame(panes)
        bar = tk.Frame(left)
        bar.pack(fill="x", padx=8, pady=8)
        tk.Button(bar, text="Start…", command=self.start).pack(side="left")
        tk.Button(bar, text="Stop", command=self.stop).pack(side="left", padx=6)
        tk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left")
        self.message = tk.StringVar(value="")
        tk.Label(left, textvariable=self.message, anchor="w", fg="#555").pack(fill="x", padx=8)
        self.tree = ttk.Treeview(left, columns=[c for c, _, _ in COLUMNS], show="headings", selectmode="browse")
        for c, title, w in COLUMNS:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="e" if c in ("calls", "elapsed") else "w")
        self.tree.tag_configure("failed", foreground="#b3261e")
        self.tree.tag_configure("died", foreground="#b3261e")
        self.tree.tag_configure("done", foreground="#2e7d4f")
        self.tree.tag_configure("stopped", foreground="#8a5a00")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        panes.add(left, weight=3)
        self.panel = panel.RunPanel(panes)
        panes.add(self.panel, weight=4)
        root.bind("<Escape>", lambda e: self.tree.selection_set(()))
        self.poll()

    def poll(self):
        try:
            self.refresh()
        except Exception as e:                                # noqa: BLE001 - the window keeps going
            self.message.set(f"refresh: {e}")
        self.root.after(500, self.poll)

    def refresh(self):
        rows = self.reader.read()
        have = set(self.tree.get_children())
        for s in rows:
            self.summaries[s["id"]] = s
            vals = row_values(s)
            if s["id"] in have:
                if tuple(str(v) for v in self.tree.item(s["id"], "values")) != tuple(str(v) for v in vals):
                    self.tree.item(s["id"], values=vals, tags=(s["status"],))
            else:
                self.tree.insert("", "end", iid=s["id"], values=vals, tags=(s["status"],))
        for gone in have - {s["id"] for s in rows}:
            self.tree.delete(gone)
        if self.selected:
            self.panel.refresh(self.summaries.get(self.selected))

    def on_select(self, _=None):
        sel = self.tree.selection()
        rid = sel[0] if sel else None
        if rid == self.selected:
            return
        self.selected = rid
        rec = apollo.load(rid) if rid else None
        self.panel.show(self.reader.files_of(rec) if rec else None)
        if rid:
            self.panel.refresh(self.summaries.get(rid))

    # ---- buttons ----
    def start(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="A page-flow config or a brief", initialdir=str(apollo.TESTING / "pageflow" / "runs"),
            filetypes=[("Run config or brief", "*.yaml *.yml *.md"), ("All files", "*")])
        if path:
            StartDialog(self.root, Path(path), self.started)

    def started(self, rec: dict):
        self.message.set(f"started {rec['id']} (pid {rec['pid']})")
        self.refresh()
        self.tree.selection_set(rec["id"])

    def stop(self):
        rid = self.selected
        if not rid:
            return
        if not messagebox.askyesno("Stop", f"Stop {rid}?", parent=self.root):
            return
        out = apollo.stop(rid)
        self.message.set(out.get("how") if out["ok"] else f"not stopped: {out['why']}")

    def open_folder(self):
        s = self.summaries.get(self.selected or "")
        if s:
            print(s["run_folder"], flush=True)                # no Windows calls: the path, to copy
            self.message.set(s["run_folder"])
            self.root.clipboard_clear()
            self.root.clipboard_append(s["run_folder"])


class StartDialog(tk.Toplevel):
    """Name, model, pages, budget, Dry (ticked) and Fast, for one start."""

    def __init__(self, master, path: Path, done):
        super().__init__(master)
        self.path, self.done = path, done
        self.title(f"Start {path.name}")
        self.transient(master)
        kind = apollo.kind_of(path)
        self.vars = {k: tk.StringVar() for k in ("name", "model", "pages", "budget")}
        self.dry, self.fast = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        tk.Label(self, text=f"{kind}: {path}", anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        hints = {"model": "page model" if kind == "pageflow" else apollo.SESSION_MODEL,
                 "pages": "A-B" if kind == "pageflow" else "-",
                 "budget": "Haiku calls, or none" if kind == "pageflow" else "US dollars, or none"}
        for i, k in enumerate(("name", "model", "pages", "budget"), 1):
            tk.Label(self, text=k.capitalize()).grid(row=i, column=0, sticky="w", padx=8)
            e = tk.Entry(self, textvariable=self.vars[k], width=40)
            e.grid(row=i, column=1, sticky="we", padx=8, pady=2)
            if k in hints:
                tk.Label(self, text=hints[k], fg="#888").grid(row=i, column=2, sticky="w", padx=(0, 8))
        tk.Checkbutton(self, text="Dry (canned answers, no model calls)", variable=self.dry).grid(
            row=5, column=1, sticky="w", padx=8)
        tk.Checkbutton(self, text="Fast (WSL disk; pending the owner's OK)", variable=self.fast).grid(
            row=6, column=1, sticky="w", padx=8)
        b = tk.Frame(self)
        b.grid(row=7, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        tk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        tk.Button(b, text="Start", command=self.go).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())

    def go(self):
        v = {k: (x.get().strip() or None) for k, x in self.vars.items()}
        if not self.dry.get() and not messagebox.askyesno(
                "Live run", "Not dry: this run calls models and costs money. Start it?", parent=self):
            return
        try:
            rec = apollo.start(self.path, v["name"], v["model"], self.dry.get(), self.fast.get(), v["pages"], v["budget"])
        except (ValueError, OSError) as e:
            messagebox.showerror("Not started", str(e), parent=self)
            return
        self.destroy()
        self.done(rec)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
