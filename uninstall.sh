#!/usr/bin/env bash
# Remove omapantry from the bar and unlink it. The repo itself is left alone.
#   ./uninstall.sh           keep your usage history (~/.local/share/omapantry)
#   ./uninstall.sh --purge   also delete usage history and caches
set -euo pipefail
id="heelgueta.omapantry"
dest="$HOME/.config/omarchy/plugins/$id"

omarchy plugin disable "$id" 2>/dev/null || true
if [[ -L $dest ]]; then rm "$dest"; echo "unlinked $dest"; fi
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
pkill -f '[o]mastart.py track' 2>/dev/null || true

if [[ ${1:-} == "--purge" ]]; then
  rm -rf "$HOME/.local/share/omapantry" "$HOME/.cache/omapantry"
  echo "purged usage history and caches"
fi
echo "done. Bar config backup from before install: ~/.config/omarchy/shell.json.pre-omapantry"
