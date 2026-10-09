#!/usr/bin/env python3
"""omapantry backend: app index, launcher and Hyprland usage tracker.

  omapantry.py index            print every launchable app as JSON
  omapantry.py launch <id>      record a launch and start the app
  omapantry.py track            long-running: log app windows opened in Hyprland

Usage data is an append-only log at ~/.local/share/omapantry/usage.jsonl
(one {"t": epoch, "id": desktop-id, "src": "menu"|"window"} per line).
Delete that file to reset history. Nothing else is written outside this repo.
"""
import configparser
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
DATA_DIR = HOME / ".local/share/omapantry"
CACHE_DIR = HOME / ".cache/omapantry"
USAGE = DATA_DIR / "usage.jsonl"
OMARCHY_PATH = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))
DEDUP_SECONDS = 8

USER_APPS = HOME / ".local/share/applications"

CATEGORY_MAP = [  # first match wins; (freedesktop category, friendly name)
    ("Development", "Develop"), ("IDE", "Develop"), ("TextEditor", "Edit"),
    ("AudioVideo", "Media"), ("Audio", "Media"), ("Video", "Media"),
    ("Graphics", "Graphics"), ("Network", "Internet"), ("WebBrowser", "Internet"),
    ("Office", "Office"), ("Game", "Games"), ("Education", "Learn"),
    ("Science", "Science"), ("Settings", "Settings"), ("System", "System"),
    ("Utility", "Utilities"),
]


# ---------------------------------------------------------------- helpers

def data_dirs():
    dirs = [USER_APPS]
    xdg = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(d) / "applications" for d in xdg.split(":") if d]
    dirs += [HOME / ".local/share/flatpak/exports/share/applications",
             Path("/var/lib/flatpak/exports/share/applications")]
    seen, out = set(), []
    for d in dirs:
        if str(d) not in seen and d.is_dir():
            seen.add(str(d))
            out.append(d)
    return out


def parse_desktop(path):
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
        return cp["Desktop Entry"] if cp.has_section("Desktop Entry") else None
    except Exception:
        return None


def desktop_id(base, path):
    return str(path.relative_to(base))[:-len(".desktop")].replace("/", "-")


def exec_basename(cmd):
    try:
        parts = shlex.split(re.sub(r"%[a-zA-Z]", "", cmd))
    except ValueError:
        parts = cmd.split()
    for p in parts:
        if "=" in p and not p.startswith("/"):
            continue  # env assignment
        if p in ("env", "uwsm-app", "--", "setsid", "gtk-launch"):
            continue
        return os.path.basename(p)
    return ""


def webapp_url(cmd):
    m = re.search(r"omarchy-launch-webapp\s+(\S+)", cmd) or re.search(r"--app=(\S+)", cmd)
    return m.group(1).strip("'\"") if m else ""


def classify(entry, cmd):
    if webapp_url(cmd):
        return "web"
    if "xdg-terminal-exec" in cmd or entry.get("Terminal", "").lower() == "true":
        return "tui"
    return "app"


def friendly_category(cats):
    names = [c for c in cats.split(";") if c]
    for key, label in CATEGORY_MAP:
        if key in names:
            return label
    return "Other"


# ------------------------------------------------------------ icon lookup

ICON_DIRS = None


def icon_index():
    """name -> best file path. Cached; rebuilt when a name is missing."""
    cache = CACHE_DIR / "icons.json"
    try:
        return json.loads(cache.read_text())
    except Exception:
        pass
    roots = [HOME / ".icons", HOME / ".local/share/icons"]
    xdg = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots += [Path(d) / "icons" for d in xdg.split(":") if d]
    best = {}  # name -> (score, path)
    for root in roots:
        if not root.is_dir():
            continue
        # Only the theme actually in use for Omarchy matters most, but any theme
        # is better than a blank tile. Prefer svg, then big pngs, and hicolor.
        for dirpath, _dirs, files in os.walk(root):
            if "/apps" not in dirpath and "/devices" not in dirpath:
                continue
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext not in (".svg", ".png"):
                    continue
                m = re.search(r"/(\d+)x\d+", dirpath)
                size = int(m.group(1)) if m else 512
                score = (ext == ".svg") * 10000 + min(size, 256) + ("hicolor" in dirpath) * 5
                if stem not in best or score > best[stem][0]:
                    best[stem] = (score, os.path.join(dirpath, f))
    pix = Path("/usr/share/pixmaps")
    if pix.is_dir():
        for f in pix.iterdir():
            if f.suffix in (".svg", ".png"):
                best.setdefault(f.stem, (1, str(f)))
    out = {k: v[1] for k, v in best.items()}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return out


def resolve_icon(name, index):
    if not name:
        return ""
    if name.startswith("/"):
        return name if os.path.exists(name) else ""
    return index.get(name, "")


# ---------------------------------------------------------------- packages

def omarchy_packages():
    pk = set()
    for f in ("omarchy-base.packages", "omarchy-other.packages"):
        try:
            for line in (OMARCHY_PATH / "install" / f).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pk.add(line)
        except OSError:
            pass
    return pk


def package_owners(paths):
    """desktop file path -> package name (one pacman call)."""
    owners = {}
    if not paths:
        return owners
    try:
        out = subprocess.run(["pacman", "-Qo", "--"] + paths, capture_output=True,
                             text=True, timeout=20, env={**os.environ, "LC_ALL": "C"}).stdout
    except Exception:
        return owners
    for line in out.splitlines():
        m = re.match(r"(.+) is owned by (\S+) ", line)
        if m:
            owners[m.group(1)] = m.group(2)
    return owners


def package_info(pkgs):
    """pkg -> (install epoch, installed bytes, explicitly installed?)."""
    info = {}
    if not pkgs:
        return info
    try:
        out = subprocess.run(["pacman", "-Qi"] + sorted(pkgs), capture_output=True, text=True,
                             timeout=20, env={**os.environ, "LC_ALL": "C"}).stdout
    except Exception:
        return info
    units = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}
    for block in out.split("\n\n"):
        name = size = when = None
        explicit = False
        for line in block.splitlines():
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if key == "Name":
                name = val
            elif key == "Installed Size":
                n, _, u = val.partition(" ")
                size = int(float(n) * units.get(u, 1))
            elif key == "Install Reason":
                explicit = val.startswith("Explicitly")
            elif key == "Install Date":
                try:
                    when = int(time.mktime(time.strptime(val, "%a %d %b %Y %I:%M:%S %p %Z")))
                except ValueError:
                    try:
                        when = int(subprocess.run(["date", "-d", val, "+%s"], capture_output=True,
                                                  text=True).stdout.strip())
                    except Exception:
                        when = 0
        if name:
            info[name] = (when or 0, size or 0, explicit)
    return info


# ------------------------------------------------------------------- usage

def read_usage():
    stats = {}
    try:
        with open(USAGE) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                s = stats.setdefault(e["id"], {"count": 0, "last": 0})
                s["count"] += 1
                s["last"] = max(s["last"], e["t"])
    except OSError:
        pass
    return stats


def log_usage(app_id, src):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    # Menu launch followed by the window appearing is one use, not two.
    try:
        with open(USAGE, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4096))
            for line in reversed(fh.read().decode(errors="ignore").splitlines()):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("id") == app_id and now - e["t"] < DEDUP_SECONDS:
                    return
                if now - e["t"] > DEDUP_SECONDS:
                    break
    except OSError:
        pass
    with open(USAGE, "a") as fh:
        fh.write(json.dumps({"t": now, "id": app_id, "src": src}) + "\n")


# ------------------------------------------------------------------- index

def scan():
    entries, seen = [], set()
    desktop = {s for s in (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").split(":") if s}
    for base in data_dirs():
        for path in sorted(base.rglob("*.desktop")):
            app_id = desktop_id(base, path)
            if app_id in seen:
                continue
            e = parse_desktop(path)
            if not e or e.get("Type", "Application") != "Application":
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            only = {s for s in e.get("OnlyShowIn", "").split(";") if s}
            if only and desktop and not (only & desktop):
                continue
            if not e.get("Exec") or not e.get("Name"):
                continue
            seen.add(app_id)
            entries.append((app_id, path, base, e))
    return entries


def build_index():
    entries = scan()
    stats = read_usage()
    icons = icon_index()
    if any(e.get("Icon") and not e["Icon"].startswith("/") and e["Icon"] not in icons
           for _i, _p, _b, e in entries):
        try:
            (CACHE_DIR / "icons.json").unlink()
        except OSError:
            pass
        icons = icon_index()

    # A copy in ~/.local/share/applications that overrides a packaged entry
    # (Omarchy does this for Foot, Image Viewer...) belongs to that package.
    def packaged_path(app_id, path, base):
        if base == USER_APPS:
            twin = Path("/usr/share/applications") / (app_id + ".desktop")
            return twin if twin.exists() else None
        return None if "flatpak" in str(base) else path

    lookup = {app_id: packaged_path(app_id, p, b) for app_id, p, b, _e in entries}
    owners = package_owners([str(p) for p in lookup.values() if p])
    info = package_info(set(owners.values()))
    default_pkgs = omarchy_packages()

    apps = []
    for app_id, path, base, e in entries:
        cmd = e["Exec"]
        kind = classify(e, cmd)
        url = webapp_url(cmd)
        pkg = owners.get(str(lookup[app_id]), "") if lookup[app_id] else ""
        flatpak = "flatpak" in str(base)
        mtime = int(path.stat().st_mtime)
        if pkg:
            installed, size, explicit = info.get(pkg, (mtime, 0, False))
            origin = "yours" if explicit and pkg not in default_pkgs else "omarchy"
            source = pkg
        elif flatpak:
            installed, size, origin, source = mtime, 0, "yours", "flatpak"
        else:
            installed, size = mtime, 0
            origin = "yours"
            source = "webapp" if url else "custom launcher"
        u = stats.get(app_id, {"count": 0, "last": 0})
        apps.append({
            "id": app_id,
            "name": e["Name"],
            "sub": e.get("GenericName") or e.get("Comment") or "",
            "kind": kind,
            "category": "Web apps" if kind == "web" else friendly_category(e.get("Categories", "")),
            "origin": origin,
            "source": source,
            "url": url,
            "icon": resolve_icon(e.get("Icon", ""), icons),
            "installed": installed,
            "size": size,
            "count": u["count"],
            "last": u["last"],
            "wmclass": e.get("StartupWMClass", ""),
            "exec": exec_basename(cmd),
            "path": str(path),
        })
    return apps


# ------------------------------------------------------------------ launch

def launch(app_id):
    log_usage(app_id, "menu")
    subprocess.Popen(["setsid", "uwsm-app", "--", "gtk-launch", app_id + ".desktop"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ------------------------------------------------------------------ tracker

def class_map():
    m = {}
    for app_id, _p, _b, e in scan():
        for key in {app_id.lower(), e.get("StartupWMClass", "").lower(),
                    exec_basename(e["Exec"]).lower()}:
            if key and key not in ("sh", "bash", "env", "xdg-terminal-exec", "omarchy-launch-webapp"):
                m.setdefault(key, app_id)
        url = webapp_url(e["Exec"])
        if url:
            host = urlparse(url).netloc
            if host:
                m["web:" + host.lower()] = app_id
    return m


def match_class(cmap, window_class):
    c = window_class.lower()
    if c in cmap:
        return cmap[c]
    # Chromium --app windows: chrome-<host>__<path>-Default (or brave-/chromium-)
    m = re.match(r"^(?:chrome|chromium|brave|google-chrome|msedge|helium|vivaldi)-(.+?)__", c)
    if m:
        host = m.group(1)
        for key, val in cmap.items():
            if key.startswith("web:") and (key[4:] == host or key[4:].endswith("." + host)
                                           or host.endswith(key[4:])):
                return val
    return None


def die_with_parent():
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM
    except Exception:
        pass


def track():
    die_with_parent()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(DATA_DIR / "tracker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another tracker is already running
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(f"{runtime}/hypr/{sig}/.socket2.sock")
    cmap, built, misses = class_map(), time.time(), {}
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.decode(errors="ignore")
            if not line.startswith("openwindow>>"):
                continue
            parts = line[len("openwindow>>"):].split(",", 3)
            if len(parts) < 3:
                continue
            wclass = parts[2]
            app_id = match_class(cmap, wclass)
            if not app_id and time.time() - built > 60 and time.time() - misses.get(wclass, 0) > 60:
                misses[wclass] = time.time()
                cmap, built = class_map(), time.time()  # app may have just been installed
                app_id = match_class(cmap, wclass)
            if app_id:
                log_usage(app_id, "window")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "index"
    if cmd == "index":
        json.dump(build_index(), sys.stdout)
    elif cmd == "launch" and len(sys.argv) > 2:
        launch(sys.argv[2])
    elif cmd == "track":
        track()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
