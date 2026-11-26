#!/bin/sh
# ctl.sh — run one apollo.py command in WSL from Windows (Git Bash) or from inside WSL, so an assistant or a person
# drives Apollo with the same state the window shows. The window polls that state, so what this starts or clears
# shows there within seconds.
#   sh ctl.sh ls --json
#   sh ctl.sh start --flow read --source pollard --pages 20-25
#   sh ctl.sh view <run-id> --tab found --json
#   sh ctl.sh logdb --sql "select count(*) from found"
# HYDRA_CONFIG_DIR points hydra at a scratch config, so the owner's own settings are never written.
DIR=/mnt/e/Documents/development/stable/apollo
PY=/home/manager/.venvs/apollo/bin/python
if [ -d /mnt/e ] && command -v "$PY" >/dev/null 2>&1; then
	cd "$DIR" && exec env HYDRA_CONFIG_DIR=/home/manager/hcfg_apollo "$PY" apollo.py "$@"
fi
MSYS_NO_PATHCONV=1 exec wsl.exe -d Debian --cd "$DIR" -- env HYDRA_CONFIG_DIR=/home/manager/hcfg_apollo "$PY" apollo.py "$@"
