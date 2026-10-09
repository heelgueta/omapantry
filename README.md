# omapantry

A tiny start menu for the Omarchy bar. Click the apps icon (right after the Omarchy menu) to browse everything launchable: native apps, webapps and TUIs.

**Views** (Tab / Shift+Tab, or ←/→ when the search box is empty): Recent · Top (most opened) · A–Z · New (install date) · Size · Groups (category) · Unused.
**Filters:** All / Apps / Web / TUI, plus **Mine** (installed by you, your webapps, custom launchers) vs **Omarchy** (shipped with Omarchy or pulled in as dependencies).
**Search** is always live and overrides the view. ↑/↓ move, Enter launches, Esc clears the search and then closes.

Colors, borders, fonts and corner radius all come from the shell's theme tokens, so it follows `omarchy theme set`.

## Install

```bash
omarchy plugin add https://github.com/heelgueta/omapantry.git --enable
```

Or review it first: `omarchy plugin add <url>` clones it disabled, then `omarchy plugin enable heelgueta.omapantry`.
Update with `omarchy plugin update heelgueta.omapantry`; remove with `omarchy plugin remove heelgueta.omapantry`. Optional keybinding: `omarchy-shell heelgueta.omapantry toggle`.

## Requirements

Omarchy with Hyprland, Python 3 (standard library only), and tools Omarchy already ships: `pacman`
(read-only queries), `gtk-launch`, `setsid`, `xdg-terminal-exec`, `omarchy-launch-webapp`.
No sudo, no install hooks, no network access.

## Privacy

Everything stays on your machine. The tracker only records which app launched and when
(`~/.local/share/omapantry/usage.jsonl`); nothing is sent anywhere. `./uninstall.sh --purge` deletes it.

## How it works

- `backend/omapantry.py index` scans the desktop entries and asks pacman for owner, install date, size and install reason. A package listed in Omarchy's own package lists, or installed as a dependency, counts as "Omarchy". Webapps are detected from `omarchy-launch-webapp` / `--app=`.
- `backend/omapantry.py track` runs as a child of the shell, listens to Hyprland's `openwindow` events and appends `{t, id}` to `~/.local/share/omapantry/usage.jsonl`. This is how Recent and Top work for apps started from anywhere. Launching from the menu logs too, with de-duplication. History starts when you install; Linux keeps no earlier record. TUIs started outside the menu can't be told apart (their window class is just `TUI.float`/`TUI.tile`), so they are only counted when launched from omapantry.
- `Panel.qml` renders it.

## Install from a checkout / undo

```
./install.sh               # symlink into ~/.config/omarchy/plugins and add to the bar
./install.sh --no-enable   # symlink only
./uninstall.sh             # remove from bar, unlink, stop tracker (keeps usage history)
./uninstall.sh --purge     # also delete ~/.local/share/omapantry and ~/.cache/omapantry
```

`install.sh` saves `~/.config/omarchy/shell.json.pre-omapantry` the first time it runs. Optional keybinding: `omarchy-shell heelgueta.omapantry toggle`.
