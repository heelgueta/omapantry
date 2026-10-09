# omastart

A tiny start menu for the Omarchy bar. Click the apps icon (right after the Omarchy menu) to browse everything launchable: native apps, webapps and TUIs.

**Views** (Tab / Shift+Tab, or ←/→ when the search box is empty): Recent · Top (most opened) · A–Z · New (install date) · Size · Groups (category) · Unused.
**Filters:** All / Apps / Web / TUI, plus **Mine** (installed by you, your webapps, custom launchers) vs **Omarchy** (shipped with Omarchy or pulled in as dependencies).
**Search** is always live and overrides the view. ↑/↓ move, Enter launches, Esc clears the search and then closes.

Colors, borders, fonts and corner radius all come from the shell's theme tokens, so it follows `omarchy theme set`.

## How it works

- `backend/omastart.py index` scans the desktop entries and asks pacman for owner, install date, size and install reason. A package listed in Omarchy's own package lists, or installed as a dependency, counts as "Omarchy". Webapps are detected from `omarchy-launch-webapp` / `--app=`.
- `backend/omastart.py track` runs as a child of the shell, listens to Hyprland's `openwindow` events and appends `{t, id}` to `~/.local/share/omastart/usage.jsonl`. This is how Recent and Top work for apps started from anywhere. Launching from the menu logs too, with de-duplication. History starts when you install; Linux keeps no earlier record. TUIs started outside the menu can't be told apart (their window class is just `TUI.float`/`TUI.tile`), so they are only counted when launched from omastart.
- `Panel.qml` renders it.

## Install / undo

```
./install.sh               # symlink into ~/.config/omarchy/plugins and add to the bar
./install.sh --no-enable   # symlink only
./uninstall.sh             # remove from bar, unlink, stop tracker (keeps usage history)
./uninstall.sh --purge     # also delete ~/.local/share/omastart and ~/.cache/omastart
```

`install.sh` saves `~/.config/omarchy/shell.json.pre-omastart` the first time it runs. Optional keybinding: `omarchy-shell mnx.omastart toggle`.
