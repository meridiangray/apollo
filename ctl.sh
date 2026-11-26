#!/bin/sh
# ctl.sh — run one py/apollo.py command with the same state the window shows; what it starts or clears shows there
# within seconds. Works from Linux, macOS and WSL, and from Windows (Git Bash), where it runs itself again inside WSL.
#   sh ctl.sh ls --json
#   sh ctl.sh start --flow read --source pollard --pages 20-25
#   sh ctl.sh view <run-id> --tab found --json
#   sh ctl.sh logdb --sql "select count(*) from found"
# Settings (environment):
#   APOLLO_PYTHON     the python that runs Apollo (default: ~/.venvs/apollo/bin/python, else python3)
#   APOLLO_DISTRO     the WSL distro, from Windows (default: Debian)
#   HYDRA_CONFIG_DIR  passed on to hydra when set (from Windows too)
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
case "$(uname -s)" in
MINGW* | MSYS* | CYGWIN*)
	[ -n "${HYDRA_CONFIG_DIR:-}" ] && export WSLENV="${WSLENV:+$WSLENV:}HYDRA_CONFIG_DIR/p"
	MSYS_NO_PATHCONV=1 exec wsl.exe -d "${APOLLO_DISTRO:-Debian}" --cd "$(cd "$DIR" && pwd -W)" -- sh ./ctl.sh "$@"
	;;
esac
PY=${APOLLO_PYTHON:-$HOME/.venvs/apollo/bin/python}
[ -x "$PY" ] || PY=python3
cd "$DIR"
exec "$PY" py/apollo.py "$@"
