"""runlog.py: the DuckDB log of everything Apollo runs, one file, `state/apollo.duckdb`.

A run is files (runfiles.py); this keeps them as tables a person can query. Nothing here is read back by a run: the
files stay the truth, the log is a copy that can be rebuilt (`apollo.py logdb --rebuild`).

Who touches the file: DuckDB allows one read-write process or several read-only ones, never both. So every writer (the
manager that owns a live run, a tool's process, a Proofread job, `clear`, `--rebuild`) opens the file, writes in one
short transaction and closes it again, retrying a few seconds when another writer is in the way; every reader opens it
read-only for one query (`sql`), retrying the same way. A write that cannot get in is skipped, never an error for the
run: the next ingest catches up, because each file is read from where the last ingest stopped.

Tables (all keyed by `run_id` where a run owns them):

    runs         id, name, flow, kind, source, archive, pages, dry, model, status, detail, started, ended, exit_code, config_yaml
    events       run_id, seq, clock, t, stage, verb, object, descriptor, who, kind, page, n, detail   (the trace and driver.log)
    model_calls  run_id, seq, origin, source, clock, model, label, page, ms, ok, tokens, cost_usd, prompt, prompt_chars,
                 prompt_sha, answer, why     (origin `run`, `proofread` or `catalogue`; the page flow keeps a prompt by hash only)
    hydra_calls  run_id, seq, clock, tool, args, result, ms, ok, error
    found        run_id, seq, page, schema, thing, quote, record_id, state
    pages        run_id, page, status, records, quotes, places, calls, seconds, problems
    questions    run_id, id, status, kind, page, question, quote, records, answer
    tool_runs    id, tool, key, source, archive, pages, status, started, ended, done, total, error, model, calls, detail
    collections  run_id, archive, name, added
    ingest_state run_id, file, bytes, lines   (where the last ingest stopped in each file)
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import runfiles

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
MAX_ROWS = 1000                                          # rows a query returns
LOCK_WAIT = 20.0                                         # seconds a writer waits for another writer
READ_WAIT = 4.0
_LOCAL = threading.Lock()                                # one writer per process

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS runs(id VARCHAR, name VARCHAR, flow VARCHAR, kind VARCHAR, source VARCHAR, archive VARCHAR, "
    "pages VARCHAR, dry BOOLEAN, model VARCHAR, status VARCHAR, detail VARCHAR, started TIMESTAMP, ended TIMESTAMP, "
    "exit_code INTEGER, config_yaml VARCHAR)",
    "CREATE TABLE IF NOT EXISTS events(run_id VARCHAR, seq INTEGER, clock VARCHAR, t DOUBLE, stage VARCHAR, verb VARCHAR, "
    "object VARCHAR, descriptor VARCHAR, who VARCHAR, kind VARCHAR, page INTEGER, n INTEGER, detail VARCHAR)",
    "CREATE TABLE IF NOT EXISTS model_calls(run_id VARCHAR, seq INTEGER, origin VARCHAR, source VARCHAR, clock VARCHAR, "
    "model VARCHAR, label VARCHAR, page INTEGER, ms INTEGER, ok BOOLEAN, tokens INTEGER, cost_usd DOUBLE, prompt VARCHAR, "
    "prompt_chars INTEGER, prompt_sha VARCHAR, answer VARCHAR, why VARCHAR)",
    "CREATE TABLE IF NOT EXISTS hydra_calls(run_id VARCHAR, seq INTEGER, clock VARCHAR, tool VARCHAR, args VARCHAR, "
    "result VARCHAR, ms INTEGER, ok BOOLEAN, error VARCHAR)",
    "CREATE TABLE IF NOT EXISTS found(run_id VARCHAR, seq INTEGER, page INTEGER, schema VARCHAR, thing VARCHAR, "
    "quote VARCHAR, record_id VARCHAR, state VARCHAR)",
    "CREATE TABLE IF NOT EXISTS pages(run_id VARCHAR, page INTEGER, status VARCHAR, records INTEGER, quotes INTEGER, "
    "places INTEGER, calls INTEGER, seconds DOUBLE, problems VARCHAR)",
    "CREATE TABLE IF NOT EXISTS questions(run_id VARCHAR, id VARCHAR, status VARCHAR, kind VARCHAR, page INTEGER, "
    "question VARCHAR, quote VARCHAR, records VARCHAR, answer VARCHAR)",
    "CREATE TABLE IF NOT EXISTS tool_runs(id VARCHAR, tool VARCHAR, key VARCHAR, source VARCHAR, archive VARCHAR, "
    "pages VARCHAR, status VARCHAR, started TIMESTAMP, ended TIMESTAMP, done INTEGER, total INTEGER, error VARCHAR, "
    "model VARCHAR, calls INTEGER, detail VARCHAR)",
    "CREATE TABLE IF NOT EXISTS collections(run_id VARCHAR, archive VARCHAR, name VARCHAR, added TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS ingest_state(run_id VARCHAR, file VARCHAR, bytes BIGINT, lines INTEGER)",
]
RUN_TABLES = ("runs", "events", "model_calls", "hydra_calls", "found", "pages", "questions", "collections", "ingest_state")
TABLES = RUN_TABLES + ("tool_runs",)


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def db_path() -> Path:
    return state_dir() / "apollo.duckdb"


# ---- opening -------------------------------------------------------------------------------------------------------

def _open(read_only: bool, wait: float):
    import duckdb
    end = time.time() + wait
    while True:
        try:
            if read_only:
                # no file reads or writes through SQL: a query sees the log and nothing else on the disk
                return duckdb.connect(str(db_path()), read_only=True, config={"enable_external_access": False})
            return duckdb.connect(str(db_path()))
        except duckdb.IOException:
            if time.time() > end:
                raise
            time.sleep(0.15)


@contextlib.contextmanager
def writer(wait: float = LOCK_WAIT):
    """The log open for writing, one transaction; closed again on leaving. Raises duckdb.IOException when another
    process holds it for longer than `wait`."""
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL:
        con = _open(False, wait)
        try:
            for stmt in SCHEMA:
                con.execute(stmt)
            con.execute("BEGIN")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()


def _quiet(fn, *a, **kw):
    """Run a log write that must never fail what called it: returns its result, or None when the log was busy or broke."""
    try:
        return fn(*a, **kw)
    except Exception:                                    # noqa: BLE001 - the files stay the truth; the next ingest catches up
        return None


# ---- reading a file from where the last ingest stopped -----------------------------------------------------------------

def _new_lines(con, rid: str, path: Path, table: str | None = None, where: str = "TRUE") -> tuple[list[tuple[int, str]], int, int]:
    """([(seq, line)], bytes, lines): the whole lines added to `path` since the last ingest. A file that shrank was
    replaced: its rows in `table` (those `where` holds for) go and it is read from the start."""
    key = str(path.name if path.parent.name != "pageflow" else f"pageflow/{path.name}")
    got = con.execute("SELECT bytes, lines FROM ingest_state WHERE run_id=? AND file=?", [rid, key]).fetchall()
    pos, lines = (got[0][0], got[0][1]) if got else (0, 0)
    try:
        size = path.stat().st_size
    except OSError:
        return [], pos, lines
    if size < pos:
        if table:
            con.execute(f"DELETE FROM {table} WHERE run_id=? AND ({where})", [rid])
        pos, lines = 0, 0
    if size == pos:
        return [], pos, lines
    with path.open("rb") as f:
        f.seek(pos)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], pos, lines
    out = [(lines + i, ln.decode("utf-8", errors="replace")) for i, ln in enumerate(data[:end + 1].splitlines())]
    return out, pos + end + 1, lines + len(out)


def _mark(con, rid: str, path: Path, pos: int, lines: int) -> None:
    key = str(path.name if path.parent.name != "pageflow" else f"pageflow/{path.name}")
    con.execute("DELETE FROM ingest_state WHERE run_id=? AND file=?", [rid, key])
    con.execute("INSERT INTO ingest_state VALUES (?,?,?,?)", [rid, key, pos, lines])


def _rows(lines: list[tuple[int, str]]) -> list[tuple[int, dict]]:
    out = []
    for seq, ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append((seq, row))
    return out


def _int(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _ts(v):
    """An ISO string or epoch seconds as a datetime (None when it is neither)."""
    if v in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(v))
    except (TypeError, ValueError, OSError):
        pass
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _text(v, limit: int | None = None):
    if v is None:
        return None
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if limit is None or len(s) <= limit else s[:limit]


def _page_of(label) -> int | None:
    m = re.search(r"\bp\.?\s?(\d+)\b", str(label or ""))
    return int(m.group(1)) if m else None


# ---- ingest one run ------------------------------------------------------------------------------------------------

def _config_text(folder: Path) -> str | None:
    for name in ("run-config.yaml", "fleet-config.yaml"):
        try:
            return (folder / name).read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _pages_arg(folder: Path) -> str | None:
    try:
        import yaml
        cfg = yaml.safe_load(_config_text(folder) or "") or {}
    except Exception:                                    # noqa: BLE001
        return None
    p = cfg.get("pages") if isinstance(cfg, dict) else None
    return f"{p['from']}-{p['to']}" if isinstance(p, dict) and p.get("from") and p.get("to") else None


def ingest_run(rec: dict, status: dict | None = None, final: bool = False) -> dict:
    """Bring the run's rows up to date from its files. `status`: {status, detail, ended, exit_code} when it is known (the
    manager knows it; `running` otherwise). Idempotent: files are read from where the last ingest stopped, the run's
    pages, questions, collections and row in `runs` are written again whole. Returns the rows added per table."""
    rid = rec["id"]
    folder = Path(rec["run_folder"])
    pf = folder / "pageflow"
    status = status or {}
    files = runfiles.RunFiles(folder, rec["kind"], rec.get("archive"))
    added = {}
    with writer() as con:
        if rec["kind"] == "pageflow":
            lines, pos, n = _new_lines(con, rid, pf / "trace.jsonl", "events", "stage<>'log'")
            ev = [(s, r) for s, r in _rows(lines)]
            for seq, r in ev:
                con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [rid, seq, r.get("at"), _float(r.get("t")), r.get("stage"), r.get("verb"), _text(r.get("object")),
                             r.get("descriptor"), r.get("who"), r.get("kind"), _int(r.get("page")), _int(r.get("n")),
                             _text(r.get("detail"))])
            _mark(con, rid, pf / "trace.jsonl", pos, n)
            added["events"] = len(ev)
            log, pos, n = _new_lines(con, rid, folder / "driver.log", "events", "stage='log'")
            for seq, ln in log:
                if ln.strip():
                    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [rid, 1_000_000 + seq, None, None, "log", "Logged", ln.strip()[:2000], "Driver log", "driver",
                                 "log", None, None, None])
            _mark(con, rid, folder / "driver.log", pos, n)

            lines, pos, n = _new_lines(con, rid, pf / "found.jsonl", "found")
            fr = _rows(lines)
            for seq, r in fr:
                con.execute("INSERT INTO found VALUES (?,?,?,?,?,?,?,?)",
                            [rid, seq, _int(r.get("page")), r.get("schema"), _text(r.get("thing")), _text(r.get("quote")),
                             _text(r.get("id") or r.get("record_id") or r.get("rid")), r.get("state")])
            _mark(con, rid, pf / "found.jsonl", pos, n)
            added["found"] = len(fr)

            lines, pos, n = _new_lines(con, rid, pf / "hydra.jsonl", "hydra_calls")
            hr = _rows(lines)
            for seq, r in hr:
                ok = r.get("ok", True)
                con.execute("INSERT INTO hydra_calls VALUES (?,?,?,?,?,?,?,?,?)",
                            [rid, seq, r.get("at"), r.get("tool"), _text(r.get("args") or {}), _text(r.get("text")),
                             _int(r.get("ms")), bool(ok), None if ok else _text(r.get("text"), 2000)])
            _mark(con, rid, pf / "hydra.jsonl", pos, n)
            added["hydra_calls"] = len(hr)

            lines, pos, n = _new_lines(con, rid, pf / "models.jsonl", "model_calls", "origin='run'")
            mr = _rows(lines)
            for seq, r in mr:
                con.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [rid, seq, "run", rec.get("source"), r.get("at"), r.get("model"), r.get("label"),
                             _int(r.get("page")) or _page_of(r.get("label")), _int(r.get("ms")), bool(r.get("ok", True)),
                             _int(r.get("tokens")), _float(r.get("cost_usd")), _text(r.get("prompt")),
                             _int(r.get("prompt_chars")), r.get("prompt_sha"), _text(r.get("answer")), _text(r.get("why"), 2000)])
            _mark(con, rid, pf / "models.jsonl", pos, n)
            added["model_calls"] = len(mr)

            progress, state = files.progress(), files.state()
            found_all = files.found()
            con.execute("DELETE FROM pages WHERE run_id=?", [rid])
            for h in runfiles.heat(progress, state, found_all):
                con.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?)",
                            [rid, h["page"], h["status"], h["records"], h["quotes"], h["places"], h["calls"], h["seconds"],
                             json.dumps(h["problems"]) if h["problems"] else None])
        else:                                            # a session: its transcript, read whole when it grew
            tf = folder / "transcript.jsonl"
            size = tf.stat().st_size if tf.exists() else 0
            got = con.execute("SELECT bytes FROM ingest_state WHERE run_id=? AND file='transcript.jsonl'", [rid]).fetchall()
            if size and (not got or got[0][0] != size):
                s = runfiles.Session()
                s.feed(runfiles.Tail(tf).all())
                con.execute("DELETE FROM events WHERE run_id=?", [rid])
                con.execute("DELETE FROM hydra_calls WHERE run_id=?", [rid])
                for i, r in enumerate(s.rows):
                    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [rid, i, r.get("at"), _float(r.get("t")), r.get("stage"), r.get("verb"), _text(r.get("object")),
                                 r.get("descriptor"), r.get("who"), r.get("kind"), None, None, None])
                for i, c in enumerate(s.calls):
                    con.execute("INSERT INTO hydra_calls VALUES (?,?,?,?,?,?,?,?,?)",
                                [rid, i, c.get("at"), c.get("tool"), None, None, _int(c.get("ms")), c.get("ok") is not False,
                                 _text(c.get("error"), 2000)])
                con.execute("DELETE FROM ingest_state WHERE run_id=? AND file='transcript.jsonl'", [rid])
                con.execute("INSERT INTO ingest_state VALUES (?,?,?,?)", [rid, "transcript.jsonl", size, len(s.rows)])
                added["events"], added["hydra_calls"] = len(s.rows), len(s.calls)
        qs = files.questions()
        con.execute("DELETE FROM questions WHERE run_id=?", [rid])
        for q in qs:
            a = q.get("answer") if isinstance(q.get("answer"), dict) else ({"text": q["answer"]} if q.get("answer") else None)
            con.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)",
                        [rid, str(q["id"]), runfiles.question_status(q), q.get("kind"), _int(q.get("page")),
                         _text(q.get("question")), _text(q.get("quote")), json.dumps([str(x) for x in q.get("records") or []]),
                         _text(a)])
        con.execute("DELETE FROM collections WHERE run_id=?", [rid])
        try:
            import archives
            for t in archives._read_tracking():
                if t.get("run") == rid:
                    for name in t.get("collections") or []:
                        con.execute("INSERT INTO collections VALUES (?,?,?,?)", [rid, t.get("archive"), name, _ts(t.get("at"))])
        except Exception:                                # noqa: BLE001 - tracking is a help
            pass
        con.execute("DELETE FROM runs WHERE id=?", [rid])
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [rid, rec.get("name"), rec.get("flow") or ("read" if rec.get("recipe") else None), rec["kind"],
                     rec.get("source"), rec.get("archive") or files.archive(), _pages_arg(folder), bool(rec.get("dry")),
                     rec.get("model"), status.get("status") or "running", status.get("detail"), _ts(rec.get("started")),
                     _ts(status.get("ended")), _int(status.get("exit_code")), _config_text(folder)])
    return added


# ---- tool runs (state/tools/<key>/<tool>.json) and Proofread ---------------------------------------------------------------

_BIG = ("pages", "items")


def _tool_row(tool: str, key: str, rec: dict) -> list | None:
    started = _ts(rec.get("started"))
    if started is None and rec.get("ended") is None:
        return None
    ident = f"{tool}:{key}:{int(started.timestamp()) if started else int(rec.get('ended') or 0)}"
    detail = {k: v for k, v in rec.items() if k not in _BIG + ("tool", "key", "status", "started", "ended", "done", "total",
                                                               "error", "source", "archive", "pages_arg", "pid", "child", "model", "calls")}
    for k in _BIG:
        if isinstance(rec.get(k), list):
            detail[f"{k}_count"] = len(rec[k])
    return [ident, tool, key, rec.get("source"), rec.get("archive"), rec.get("pages_arg"), rec.get("status"), started,
            _ts(rec.get("ended")), _int(rec.get("done")), _int(rec.get("total")), _text(rec.get("error"), 2000),
            rec.get("model"), _int(rec.get("calls")), json.dumps(detail, ensure_ascii=False, default=str)]


def log_tool_run(tool: str, key: str, rec: dict) -> None:
    """One tool's result (OCR, Text, Check, Build, Proofread, Ready) as a row of `tool_runs`, written again if it changes."""
    row = _tool_row(tool, key, rec)
    if row is None:
        return
    with writer() as con:
        con.execute("DELETE FROM tool_runs WHERE id=?", [row[0]])
        con.execute("INSERT INTO tool_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def ingest_tools() -> int:
    """Every `state/tools/<key>/<tool>.json` as a `tool_runs` row; the count."""
    root = state_dir() / "tools"
    n = 0
    rows = []
    for f in sorted(root.glob("*/*.json")) if root.is_dir() else []:
        if f.parent.name.startswith("."):
            continue
        rec = runfiles.read_json(f)
        if isinstance(rec, dict):
            row = _tool_row(f.stem, f.parent.name, rec)
            if row:
                rows.append(row)
    if rows:
        with writer() as con:
            for row in rows:
                con.execute("DELETE FROM tool_runs WHERE id=?", [row[0]])
                con.execute("INSERT INTO tool_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                n += 1
    return n


def log_model_call(source: str, row: dict, prompt: str | None = None, run_id: str = "", origin: str = "proofread") -> None:
    """A model call made outside a run (Proofread): one `model_calls` row with its prompt and raw answer."""
    with writer() as con:
        seq = con.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM model_calls WHERE run_id=? AND origin=?", [run_id, origin]).fetchall()[0][0]
        con.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [run_id, seq, origin, source, row.get("at") or time.strftime("%H:%M:%S"), row.get("model"),
                     row.get("label"), _int(row.get("page")) or _page_of(row.get("label")), _int(row.get("ms")),
                     bool(row.get("ok", True)), _int(row.get("tokens")), _float(row.get("cost_usd")),
                     prompt if prompt is not None else _text(row.get("prompt")), _int(row.get("prompt_chars")),
                     row.get("prompt_sha"), _text(row.get("answer")), _text(row.get("why"), 2000)])


# ---- clear and rebuild -----------------------------------------------------------------------------------------------

def delete_run(rid: str) -> None:
    """Everything the log holds of one run."""
    if not db_path().exists():
        return
    with writer() as con:
        for t in RUN_TABLES:
            col = "id" if t == "runs" else "run_id"
            con.execute(f"DELETE FROM {t} WHERE {col}=?", [rid])


def rebuild(recs: list[dict], status_of) -> dict:
    """Empty the log's run tables and ingest every run in `recs` (the registry's records; `status_of(rec)` gives each its
    status row), then the tool runs. Returns {runs, tool_runs}."""
    with writer() as con:
        for t in TABLES:
            con.execute(f"DELETE FROM {t}")
    done = 0
    for rec in recs:
        if Path(rec.get("run_folder") or "").is_dir():
            ingest_run(rec, status_of(rec))
            done += 1
    return {"runs": done, "tool_runs": ingest_tools()}


# ---- reading: SELECT and WITH only -------------------------------------------------------------------------------------

class Refused(ValueError):
    pass


def check_select(sql: str) -> str:
    """The query as one statement, or Refused. Only SELECT and WITH (a WITH may not lead to anything else)."""
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", str(sql or ""), flags=re.S).strip().rstrip(";").strip()
    if not s:
        raise Refused("a SELECT or WITH query")
    if ";" in re.sub(r"'(?:[^']|'')*'|\"[^\"]*\"", "", s):
        raise Refused("one statement at a time")
    if not re.match(r"(?i)(select|with)\b", s):
        raise Refused("only SELECT and WITH queries")
    if re.search(r"(?i)\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|install|load|pragma|set|call)\b"
                 r"(?=\s)", re.sub(r"'(?:[^']|'')*'|\"[^\"]*\"", "''", s)):
        raise Refused("only SELECT and WITH queries")
    return s


def sql(query: str, limit: int = MAX_ROWS) -> dict:
    """Run a SELECT or WITH on the log, read-only: {ok, columns, rows, truncated, ms}, or {ok: false, why}."""
    try:
        s = check_select(query)
    except Refused as e:
        return {"ok": False, "why": str(e)}
    if not db_path().exists():
        _quiet(_ensure)
    t = time.time()
    try:
        con = _open(True, READ_WAIT)
    except Exception as e:                               # noqa: BLE001 - say why
        return {"ok": False, "why": f"the log is busy or missing: {type(e).__name__}"}
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description or []]
        got = cur.fetchmany(limit + 1)
    except Exception as e:                               # noqa: BLE001 - a bad query is an answer
        return {"ok": False, "why": str(e).splitlines()[0][:300]}
    finally:
        con.close()
    return {"ok": True, "columns": cols, "rows": [[_cell(v) for v in r] for r in got[:limit]], "truncated": len(got) > limit,
            "ms": int((time.time() - t) * 1000)}


def _ensure() -> None:
    with writer():
        pass


def _cell(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def decode_sql(b64: str) -> str:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8")
