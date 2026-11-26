# Apollo

A desktop app for starting and watching [hydra](https://github.com/meridiangray/open-hydra) runs. Hydra's page flow reads a source page by page and records what it states; Hydra's MCP agent sessions work through an archive. Apollo starts those runs, tracks progress, calls and cost as they go, and stops a run that goes wrong. It keeps a queryable log of everything the runs wrote.

The window is Go (a WebView2 window on Windows, the browser on Linux and macOS); the run manager is Python (`py/apollo.py`) running under WSL.

## Requirements

- WSL with Debian, and a Python venv at `~/.venvs/apollo` holding hydra's packages and `duckdb`
- [hydra](https://github.com/meridiangray/open-hydra) and its page flow beside this folder
- The `claude` CLI in WSL, for live runs and the agent tools
- On Windows, the WebView2 runtime
- Go, to build

## Build

```
make
```

`make` vets, tests and builds the binary for the machine you're on, into `bin/`: `bin/Apollo.exe` from WSL (the Windows window), `bin/apollo` on Linux, `bin/apollo-darwin-*` on a Mac. `make help` lists the other targets: `windows`, `linux`, `macos`, `all`, `check`, `clean`. `GO=/path/to/go make` picks a Go that isn't on the PATH. Go fetches the pinned libraries listed in `go/go.mod`; their versions and licences are in [go/THIRD_PARTY.md](go/THIRD_PARTY.md).

## Run

Double-click `bin/Apollo.exe`. Closing the window quits Apollo; runs keep going. The same manager works from the command line:

```
~/.venvs/apollo/bin/python py/apollo.py start --recipe smoke --source pollard --pages 20-22 --dry
~/.venvs/apollo/bin/python py/apollo.py ls
```

`--dry` runs call no model and cost nothing. To start and watch runs from a Claude Code session, register `py/mcp_server.py`:

```
claude mcp add apollo -- wsl.exe -d Debian -- /home/USER/.venvs/apollo/bin/python /path/to/apollo/py/mcp_server.py
```

[docs/guide.md](docs/guide.md) covers the window, every command, the log's tables, the JSON fields, and the rules for an agent supervising runs.

## Tests

```
~/.venvs/apollo/bin/python -m pytest tests
```

## Licence

[GNU Affero General Public License v3.0](LICENSE).
