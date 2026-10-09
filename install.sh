#!/usr/bin/env bash
# Link omapantry into the Omarchy shell and (by default) put it on the bar.
#   ./install.sh               link + enable, placed right after the Omarchy menu
#   ./install.sh --no-enable   link only; enable later with: omarchy plugin enable heelgueta.omapantry
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
id="heelgueta.omapantry"
dest="$HOME/.config/omarchy/plugins/$id"
cfg="$HOME/.config/omarchy/shell.json"

if [[ -e $dest && ! -L $dest ]]; then
  echo "$dest exists and is not a symlink; refusing to touch it" >&2; exit 1
fi

# One-time safety copy of the bar config, so uninstall can always restore it.
[[ -f $cfg && ! -f $cfg.pre-omapantry ]] && cp "$cfg" "$cfg.pre-omapantry"

ln -sfn "$repo" "$dest"
omarchy-shell shell rescanPlugins >/dev/null
echo "linked $dest -> $repo"

if [[ ${1:-} != "--no-enable" ]]; then
  omarchy plugin enable "$id" --section left --after omarchy.menu
  echo "enabled; click the apps icon on the bar"
fi
