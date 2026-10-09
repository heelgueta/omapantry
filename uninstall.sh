#!/usr/bin/env bash
# Remove omastart from the bar and unlink it. The repo itself is left alone.
#   ./uninstall.sh           keep your usage history (~/.local/share/omastart)
#   ./uninstall.sh --purge   also delete usage history and caches
set -euo pipefail
id="mnx.omastart"
dest="$HOME/.config/omarchy/plugins/$id"

omarchy plugin disable "$id" 2>/dev/null || true
if [[ -L $dest ]]; then rm "$dest"; echo "unlinked $dest"; fi
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
pkill -f '[o]mastart.py track' 2>/dev/null || true

if [[ ${1:-} == "--purge" ]]; then
  rm -rf "$HOME/.local/share/omastart" "$HOME/.cache/omastart"
  echo "purged usage history and caches"
fi
echo "done. Bar config backup from before install: ~/.config/omarchy/shell.json.pre-omastart"
