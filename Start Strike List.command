#!/bin/bash
# macOS launcher: double-click in Finder. First run sets up a private Python environment (~1 min).
cd "$(dirname "$0")"
PY=$(command -v python3)
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Strike List needs Python 3. Install it from python.org, then try again." buttons {"OK"}'
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "First run: setting up (this takes about a minute)…"
  "$PY" -m venv .venv || { echo "Setup failed"; read -n1; exit 1; }
  .venv/bin/pip install -q --upgrade pip
fi
# (re)install packages whenever requirements.txt changes (e.g. after an update)
if [ ! -f .venv/.req-installed ] || ! cmp -s requirements.txt .venv/.req-installed; then
  echo "Installing/updating components…"
  .venv/bin/pip install -q -r requirements.txt && cp requirements.txt .venv/.req-installed || { echo "Setup failed"; read -n1; exit 1; }
fi
nohup .venv/bin/python strikelist.py >/dev/null 2>&1 &
osascript -e 'tell application "Terminal" to close (every window whose name contains "Start Strike List")' >/dev/null 2>&1 &
exit 0
