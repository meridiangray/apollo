# Apollo guide: run and watch a small set of hydra-mcp runs

You supervise a few runs on this WSL Linux machine through one command, `py/apollo.py`. You start runs, watch them, stop
the ones that go wrong, and report. You don't do the runs' work yourself.

## Use

Double-click **`bin/Apollo.exe`**. It opens Apollo's own window (WebView2, no browser). It needs
WSL Debian with the Apollo venv (`~/.venvs/apollo`). A second launch brings the open window to the front. Closing the window quits Apollo;
runs keep going. The log is `%TEMP%\apollo.log`. If the WebView2 runtime is missing, a message box says where to get
it. Linux and macOS builds (`bin/apollo`, `bin/apollo-darwin-*`) serve the page and open the browser.

`Apollo.exe` looks for the manager (`py/apollo.py`) beside itself first, then at `..\py\apollo.py` (it lives in `bin/`, one folder below).

## Build (from WSL)

    make

It vets (Linux and Windows), tests, and builds `bin/Apollo.exe` into `bin/` (git ignores the folder); `make all` builds `bin/apollo` (Linux) and `bin/apollo-darwin-*` too, and `make help` lists every target. While Apollo is open the new Windows build waits as `bin/Apollo.exe.new`. The Go libraries it needs (go-webview2,
go-winloader, x/sys) are pinned in `go/go.mod`/`go.sum` and downloaded by the build (nothing is vendored). Versions and licences are in `go/THIRD_PARTY.md`. The icon and
version information are `go/rsrc_windows_amd64.syso`, made by `go/icon/` (`cd go/icon && go run . -syso
../rsrc_windows_amd64.syso`).

## The machine

- **Paths:** use `/mnt/e/...` only. Apollo lives in `/mnt/e/Documents/development/stable/apollo/`.
- **Python:** run Apollo as `~/.venvs/apollo/bin/python /mnt/e/.../apollo/py/apollo.py <command>` (the Apollo venv: hydra's packages
  plus duckdb; Apollo.exe starts the manager with it). The page-flow driver and `hydra-ctl` still run on `~/.venvs/hydra`. Below, `F` stands for
  that whole command.
- **Never** run `python -` or a bare REPL (one once wrote 61 GB). Run Python only as a `.py` file, `-m` or `-c`.
- **Never** call a Windows `.exe` or start the hydra desktop app.

## The window

The top bar has Home, **New run**, **Sources** and **Collections**, then the settings, light/dark and quit icons.

- **New run** opens Configure. A **Flow** select picks what the run does ("Read the source", "Fill stubs", "Place names",
  "Enrich"), then Source, Pages, Archive and Runner. The Tools (Analyse, OCR, Text) are buttons; with one chosen the go
  button becomes its verb, and picking the Flow select again returns to Run. Under Limits, **Cost cap** is the run's
  dollar limit (default $1 up to 11 pages, else about $0.045 a page rounded up to $0.50, at most $5). A page flow is
  composed from the `full` recipe plus the window's fields: the pages, `budget_usd`, and the lag-check (the lag recipe's
  `lag` settings and tools). The recipes stay in `pageflow/recipes/`.
- **Archive** is a select: **Draft** (the default; the run adds its collections, named `<source prefix>-<name>`, to
  `archives/draft/`), **New** (a fresh archive for the run, page flows only) or an existing run archive.
- **The right rail** has tabs. In Configure: Flow and Text. In a run: Flow, Text and Setup. In Configure each Flow step is
  clickable and opens its settings (model, budget, an on/off switch for Hard pages, Lag check and Rate, its prompt) under
  the buttons. Text shows the source page by page (pdftotext, or OCR where the page has none); a cell of the Pages bar or the
  run's heatmap opens that page. The tab is remembered per view.
- **Proofread** (the pencil beside the Text tab's page controls; PDF sources only): Haiku reads the page image (`pdftoppm -r 150`)
  beside the page's text and writes the text again, in reading order, nothing summarised or invented. A redaction is marked
  `[REDACTED]` (hydra's mark, from the triage and the source profile) and an unreadable word `~` (hydra's mark for lost letters,
  `records/reading.py`). Click for this page; shift-click or the small arrow for pages A-B (at most 20). The button spins while
  the job runs; then the page shows the old text against the new, changes marked in the theme's colours, with **Accept** and
  **Reject**. An accepted page is kept in `state/proofread/<source>/p<N>.json`, shown with a small "Proofread" mark (and Revert),
  and comes before OCR and the text layer in `pagetext`. The source file is never changed. The button is off, with a `?` tip, when
  `claude` is not installed in WSL; a text source has no Proofread. Calls are counted on the diff; the cost shows only with Show
  cost on. Each call is logged (`model_calls`, and `tool_runs` for the job).
- **Data** (a run's last tab): five saved queries (Slowest pages, Calls by model, Records by type, Failed calls, Tables) and a free
  SELECT box over the log below; the result is a table.
- **Sources**: drop PDF, TXT, MD, HTML or DOCX files on the view (or Choose files, up to 500 MB). Each is taken in by
  `sourcedrop.py` and listed with a title (edit it in place), format, pages or parts, a text-layer badge, size, a Context
  select (it is highlighted until one is chosen: the source cannot run without it), a page range, a brief, and Remove.
  The sources that ship with the page flow are listed below, muted. The Source select in Configure lists both.
- **Collections**: an archive select (Draft first), then each collection with its records, counts by type, the sources
  it cites, the run that added it and when. A row unfolds its records (200, then Show more).
- Cost is hidden by default: the cog's **Show cost** switch brings the dollar figures back. Calls always show.

**The Draft archive** is made empty at Apollo's start (`hydra-ctl init`, off the request path) if `archives/draft/` is
missing. **Builds are automatic**: opening an archive in Collections or Ready builds it if it is stale, and a run that
wrote to an archive builds it once when it ends. `state/collections.json` records which run added which collections.

## The commands

| Command | Does |
|---|---|
| `F start --recipe R --source S [--pages A-B] [--name N] [--model M] [--dry] [--budget B] [--json]` | Starts a page-flow run: recipe x source, composed at start |
| `F start <config.yaml or brief.md> [--name N] [--model M] [--dry] [--pages A-B] [--budget B] [--json]` | Starts a run in the background and returns at once with its `id` |
| `F start --flow read --source S [--pages A-B] [--budget B] [--lag] [--without rate,escalate] [--archive draft\|new\|PATH]` | The window's Read the source: the `full` recipe plus these fields |
| `F start --flow stubs\|places\|enrich --archive draft\|PATH` | A workflow session on an archive |
| `F sourcelist`, `sourceadd PATH`, `sourceupdate SLUG --fields JSON`, `sourceremove SLUG` | Dropped sources (`sourceadd` deletes the file if it sits in `sources/incoming/`) |
| `F pagetext SOURCE PAGE` | One page of a source's text |
| `F collections ARCHIVE [--collection NAME --offset N]` | An archive's collections, or one collection's records |
| `F draft [--reset]` | The Draft archive: make it if missing; `--reset` deletes it and makes it empty again |
| `F logdb --sql "SELECT ..."` | A query on the DuckDB log, read-only; refuses anything but SELECT and WITH. `{ok, columns, rows, truncated}` |
| `F logdb --rebuild` | Empties the log and ingests every run folder still on disk, and the tool runs |
| `F proofreadstart --source S --pages N\|A-B` | Proofread PDF pages (at most 20) in a background process; returns at once |
| `F proofreadget --source S [--page N]` | The job, the pages waiting for Accept or Reject, the accepted pages (with `--page`: that page's old and new text and diff) |
| `F proofreadact --source S --page N --action accept\|reject\|revert` | Decide a page's Proofread |
| `F configs [--json]` | Lists `{recipes, sources, briefs}` that can be started (`--legacy`: the old flat list) |
| `F ls [--json]` | Lists every run Apollo knows, oldest first |
| `F status <id> [--json]` | Shows one run in full: progress, calls, cost, tasks, questions, report |
| `F stop <id> [--json]` | Sends SIGTERM to that run's own process group; it ends `stopped` |
| `F tail <id> [-n 20] [--json]` | Prints the run's last trace lines, oldest first |

**What a run is:**
- A **page-flow run** is a **recipe** (the flow, `pageflow/recipes/`) on a **source** (one document, `pageflow/sources/`):
  `start --recipe smoke --source pollard --pages 20-22`. Apollo composes them (`pageflow/compose.py`), scales the
  budgets to the pages, and writes the effective config into the run folder (`fleet-config.yaml`; the driver adds
  `run-config.yaml`). A **`.yaml`** given to `start` is an already-composed config, run as it is. The driver works it page by page, with Haiku and
  Sonnet called through `claude -p` without tools.
  - `--model` sets the page model.
  - `--pages 20-30` limits the pages.
  - `--budget N` caps the run's cost at US$N: it finishes the page in hand, then closes and writes the report; `--budget none` lifts the cap. A page over its own call or time cap is given up (`gave-up`) and the run goes on.
- A **`.md`** is a brief for an **MCP agent session**: one headless `claude -p` agent with hydra's tools (the Linux
  MCP client config), the brief on stdin.
  - `--model` sets the agent's model (default `claude-sonnet-5-5`).
  - `--budget 5` caps the session at US$5 (`--max-budget-usd`).
  - `--archive PATH` names the archive, if the brief has one.
  - A brief may hold `{archive}`, `{source}` and `{run_folder}`: filled from `--archive`, `--source S` (that source's file) and
    `--rate RUN` (the run it rates: its folder and archive); a placeholder with no value is left as written.

**Where a run goes:**
- **Live runs:** `hydra-mcp-testing/runs/<id>/` and `archives/<id>/`.
- **`--dry`** runs call no model and cost nothing:
  - A page-flow run gets canned answers and real hydra, writing a throwaway archive under `apollo/dry/`.
  - A session replays a canned transcript.
  - Use `--dry` to learn the commands.
- **`--fast`** works on WSL's own disk and copies the run back when it ends. **Don't use it:** it waits for the owner's
  OK.

`id` accepts any unique part of a run's id.

## The log

`state/apollo.duckdb` keeps a copy of everything the runs wrote, as tables (`runlog.py`). The files stay the truth: delete the
database and `F logdb --rebuild` makes it again from the run folders on disk. One writer at a time: a run's own wrapper ingests it
every few seconds while it runs and once when it ends (each file is read from where the last ingest stopped, so ingesting twice
changes nothing); a tool's process, a Proofread job, `ready` and `clear` write for themselves. Each writer opens the file, writes
in one short transaction and closes it (a busy file is waited for, then skipped until the next ingest); readers (`logdb --sql`,
the window's Data tab) open it read-only for one query and cannot read files. `clear` deletes the run's rows too.

| Table | Holds |
|---|---|
| `runs` | id, name, flow, kind, source, archive, pages, dry, model, status, detail, started, ended, exit_code, config_yaml |
| `events` | the trace rows and the driver's log lines: run_id, seq, clock, stage, verb, object, descriptor, who, kind, page, detail |
| `model_calls` | every model call: prompt (Proofread keeps it in full; a run's by length and hash), raw answer, tokens, cost_usd, ms, page, model, ok, why; `origin` is `run` or `proofread` |
| `hydra_calls` | tool, args, result, ms, ok, error |
| `found` | the records found: page, schema, thing, quote, record_id, state |
| `pages` | per page: status, records, quotes, places, calls, seconds, problems |
| `questions` | the `ask_person` cards: status, kind, page, question, quote, answer |
| `tool_runs` | OCR, Text, Check, Build, Proofread and Ready results: tool, key, source, archive, pages, status, started, ended, done, total, error, detail |
| `collections` | which run added which collection to which archive (from `state/collections.json`) |
| `ingest_state` | where the last ingest stopped in each file |

## The JSON (`ls --json` rows; `status --json` adds the last six)

| Field | Meaning |
|---|---|
| `id`, `name`, `kind` | The run; `kind` is `pageflow` or `mcp-session` |
| `model` | The page model, or the agent's model |
| `status` | `running`, `stopping`, `done`, `stopped`, `failed`, or `died` (its process is gone with no exit record) |
| `detail` | The run's own word for how it ended (`done`, `stopped-budget`, `preflight-failed`, …) |
| `alive`, `pid` | Whether its process is running, and that process's id |
| `dry`, `fast` | How it was started |
| `started`, `ended`, `elapsed_s` | Times (ISO; seconds) |
| `exit_code` | The run's exit code once it ends |
| `run_folder`, `archive`, `config` | Where it writes, its archive, and what it was started from |
| `progress.fraction` | 0 to 1 (page flow: its passes; session: task steps ticked), or null |
| `progress.stage`, `progress.label`, `progress.done`, `progress.total` | The page flow's current pass and its count |
| `progress.pages` | Page-flow pages by status: `{"done": 3, "todo": 2}` |
| `calls.hydra`, `calls.errors` | hydra calls so far, and how many failed |
| `calls.model` | Page flow: model calls so far |
| `cost.usd` | Money spent so far (a session's is known only when it ends) |
| `cost.haiku`, `cost.sonnet` | Page flow: `[used, cap]`, where a `null` cap is no limit |
| `cost.budget_usd` | The run's dollar cap |
| `cost.turns` | A session's turns |
| `last` | The newest trace line |
| `report` | The path of `report.md` once it exists |
| `copied` | Where a `--fast` run was copied back to |
| `questions` | `ask_person` cards: `{"open": n, "answered": n, "dismissed": n}` |
| `tasks` | Each hydra task: `id`, `title`, `status`, `steps_done` of `steps_total`, and its `last` log line |
| `budgets` | The page flow's budgets |
| `command` | What was run |
| `slowest_calls` | The five slowest hydra calls |
| `errors` | The last ten failed calls, with their error |

Fields keep their meaning; new fields may be added (`schema` is the shape's version).

## MCP

`py/mcp_server.py` lets any Claude Code session start and watch runs as tools. It calls `apollo.py` as a subprocess and
returns its JSON, so what it starts shows in the window within seconds. Register it once, on Windows:

    claude mcp add apollo -- wsl.exe -d Debian -- /home/manager/.venvs/apollo/bin/python /mnt/e/Documents/development/stable/apollo/py/mcp_server.py

Tools: `runs`, `run` (status and one tab, cut when large), `sources`, `ready`, `start` and `clear` (each previews first;
`confirm=true` only after the person agrees), `stop`, and `query` (SELECT and WITH on the log). A failure comes back as
`{error, why, do}`. Archive and entity work stays in hydra's MCP; Proofread, OCR and sources stay in the window.

## The rules

1. **Stop only your own runs:** use `F stop <id>` on the runs you started in this session, and on no others. Never use
   `kill`, `pkill` or `killall`, and never signal any process yourself.
2. **Budgets:** start every live run with the budget you were given. Never raise one. A run that reaches its budget
   stops itself (`stopped`, detail `stopped-budget`); report it, and don't restart it.
3. **Live runs cost money:** start one only when your brief says so, and only the number it names. Otherwise use
   `--dry`.
4. **Never touch `hydra-git/archive-demos/`:** don't read from it into a run, and don't write to it.
5. **Write nowhere** but your own notes file, if your brief names one. The runs write their own folders.
6. **Watching:**
   - Check `F ls --json` every few minutes, not in a tight loop.
   - A run that shows no new `last` line for 30 minutes while `running` is stuck. Stop it and report it.
7. **One run per archive:** never start two runs on the same archive.

## How to report

End with:
1. **A table of your runs:** id, kind, model, status (and detail), elapsed, hydra calls, errors, cost, open
   questions.
2. **For each run that didn't end `done`:** its last five `tail` lines and what you think stopped it.
3. **For each finished run:** the path of its `report.md`, and the first lines of its counts.
4. **What you stopped,** and why.

Report what happened. "Not done", with the reason, beats a claim the files don't back.

## History, Catalogue and the themed icon

- **History** (top bar): **Action History** lists every action newest first, read from the log: runs started, stopped and cleared; the
  tools (Analyse, OCR, Text, Ready, Proofread, Catalogue, Wikipedia add); source add, remove, hide and unhide; and the toasts. Columns:
  when, what, source and pages, model mark, result, duration. Chips filter by kind; a row opens its run, or its source in the Sources
  card. `F history [--kind run|tool|source|toast]` gives the same rows as JSON. A run's own trace (`events`) is not listed: it is the
  run's page, not an action.
- **Sources** is one list: dropped sources and the page flow's built-in ones, the built-ins marked only by a small **Built in** badge
  and read only. Every row selects its source into the card. The **W** button at the right of the header is **Hide Wikipedia** (off by
  default, remembered): it hides sources whose publisher is Wikipedia or whose address is wikipedia.org. `F sourcehide SLUG` and
  `F sourceunhide SLUG` hide or show a built-in source (`state/hidden-sources.json`; the page flow's profile is never touched).
- **Catalogue** (the Sources card, and a tool in Configure): an agent reads the source's first pages (a PDF: the title page and front
  matter, at most 4 page images from `pdftoppm`; a text source: its opening text) and writes a library catalogue record: title and
  statement of responsibility (245), author (110/100), imprint (264), extent (300), notes (500), access (506), identifiers (024/035,
  typed, e.g. "MORI DocID 1346933"), language (546), a one-paragraph summary (520), proposed subjects and names (600/610/650/651).
  Every field cites its page; what the pages do not state stays empty. A record waits for **Accept** or **Reject** beside a **check**
  against what Apollo holds (title, publisher, pages, language, date). Accepted, it is kept in `state/catalogue/<slug>.json`
  (record, citations, model, time), shown under the card's fields with a Catalogue mark, and exported as MARCXML or CSL-JSON. Nothing
  is written into the page flow's profiles; subjects and names are proposals, never proof of identity. Each call is logged
  (`model_calls`, origin `catalogue`; `tool_runs`). Commands: `F cataloguestart --source S [--model haiku|sonnet|opus]`,
  `catalogueget`, `catalogueact --action accept|reject|revert`, `catalogueexport --format marcxml|csl`.
- **Tool Agent** select (Configure's Tools, and beside the Catalogue button and Proofread's pencil): Haiku (the default), Sonnet or
  Opus, with the model marks. It is shown only for a tool that launches an agent; Proofread takes it too (`proofreadstart --model`).
- **The window icon follows the theme.** The page posts its colours with every theme or mode change; Apollo.exe draws the icon again
  (`go/appicon`: the rounded square in the theme's primary colour, the orbit in its accent, the moon in the background or ink colour,
  whichever stands out) at 16, 32 and 48 px and sets it as the window's small and big icon. The exe's file icon (`go/icon`) stays as
  built, from the same drawing. To check it, switch the theme or light/dark in Apollo and look at the taskbar and the title bar.
- **The scroll line:** the foot of the top bar is a thin line that fills as the panel being read scrolls; the panels' scrollbars are
  hidden, and the line shows only while a panel overflows.
