#!/usr/bin/env python3
"""omapantry backend: app index, launcher and Hyprland usage tracker.

  omapantry.py index            print every launchable app as JSON
  omapantry.py launch <id>      record a launch and start the app
  omapantry.py edit <id> <name> [url]   rename a user launcher / change a webapp URL
  omapantry.py track            long-running: log app windows opened in Hyprland

Usage data is an append-only log at ~/.local/share/omapantry/usage.jsonl
(one {"t": epoch, "id": desktop-id, "src": "menu"|"window"} per line).
Delete that file to reset history. The only other thing written is a user launcher
you explicitly edit; its original is first copied to ~/.local/share/omapantry/backups/.
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
        private_dir(CACHE_DIR)
        with private_open(cache, os.O_WRONLY | os.O_TRUNC, "w") as fh:
            fh.write(json.dumps(out))
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


def private_dir(path):
    """Create `path` owner-only (0700); tighten it if it already exists."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def private_open(path, flags, mode):
    """Open `path` owner-only (0600), tightening an existing file's mode."""
    fd = os.open(path, flags | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode)


def harden():
    """Make all usage data and caches private, before anything is read or logged."""
    for d in (DATA_DIR, CACHE_DIR):
        if d.is_dir():
            private_dir(d)
            for f in d.iterdir():
                if f.is_file() and not f.is_symlink():
                    os.chmod(f, 0o600)


def log_usage(app_id, src):
    private_dir(DATA_DIR)
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
    with private_open(USAGE, os.O_WRONLY | os.O_APPEND, "a") as fh:
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
            # Only plain files in the user's own applications dir may be edited.
            "editable": base == USER_APPS and not path.is_symlink(),
        })
    return apps


# -------------------------------------------------------------------- edit

URL_OK = re.compile(r"^https?://[^\s\"'`$%;|&<>\\]+$")


def edit(app_id, name, url):
    """Rename a user launcher and/or change a webapp's URL. Prints 'ok' or an error."""
    name = name.strip()
    if not name or "\n" in name or "\r" in name:
        return "Name can't be empty"
    target = next((p for i, p, b, _e in scan() if i == app_id and b == USER_APPS), None)
    if target is None or target.is_symlink():
        return "That launcher isn't editable (system app or symlink)"
    text = target.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_entry, out, saw_name = False, [], False
    for line in lines:
        if line.startswith("["):
            in_entry = line.strip() == "[Desktop Entry]"
        if in_entry and line.startswith("Name="):
            line, saw_name = "Name=" + name, True
        elif in_entry and line.startswith("Exec=") and url is not None:
            old = webapp_url(line[5:])
            if not old:
                return "This launcher isn't a webapp, so it has no URL to change"
            if not URL_OK.match(url):
                return "URL must start with http:// or https:// and contain no spaces or quotes"
            line = line.replace(old, url, 1)
        out.append(line)
    if not saw_name:
        return "No Name= line found in the launcher"

    # Keep a copy of the original, then replace the file atomically.
    private_dir(DATA_DIR / "backups")
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"
    with private_open(DATA_DIR / "backups" / f"{target.name}.{stamp}", os.O_WRONLY | os.O_TRUNC, "w") as fh:
        fh.write(text)
    tmp = target.with_name(target.name + ".omapantry-tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    os.chmod(tmp, target.stat().st_mode & 0o777)
    os.replace(tmp, target)
    return "ok"


# ------------------------------------------------------------------ launch

def launch(app_id):
    log_usage(app_id, "menu")
    subprocess.Popen(["setsid", "uwsm-app", "--", "gtk-launch", app_id + ".desktop"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ------------------------------------------------------------------ tracker

# Chromium names an --app window class "<browser>-<host>_<path, / -> _>-<profile>",
# e.g. chrome-mail.google.com__mail_u_1_-Default. The port and fragment are dropped,
# so two webapps can legitimately share one class (all localhost apps do).
CHROMIUM_CLASS = re.compile(
    r"^(?:chrome|chromium|brave(?:-browser)?|google-chrome|msedge|microsoft-edge|helium|vivaldi|opera)"
    r"-(.+)-(?:default|profile[ _]\d+)$")
GENERIC_KEYS = ("sh", "bash", "env", "xdg-terminal-exec", "omarchy-launch-webapp")


def web_key(url):
    u = urlparse(url)
    host = (u.hostname or "").lower()
    return host + "_" + (u.path or "/").replace("/", "_").lower() if host else ""


def script_webapp_url(cmd):
    """A launcher whose Exec is a small user script that ends in omarchy-launch-webapp <url>."""
    exe = shlex.split(re.sub(r"%[a-zA-Z]", "", cmd) or "x")[0] if cmd.strip() else ""
    try:
        p = Path(exe)
        if p.is_file() and p.stat().st_size < 65536 and p.stat().st_uid == os.getuid():
            m = re.search(r"omarchy-launch-webapp\s+[\"']?(https?://[^\s\"']+)", p.read_text(errors="ignore"))
            return m.group(1) if m else ""
    except (OSError, ValueError):
        pass
    return ""


def class_map():
    """window class / web key -> app id; "web:<key>" -> [app ids]; "name:<id>" -> display name."""
    m = {}
    for app_id, _p, _b, e in scan():
        m["name:" + app_id] = e.get("Name", "")
        for key in {app_id.lower(), e.get("StartupWMClass", "").lower(),
                    exec_basename(e["Exec"]).lower()}:
            if key and key not in GENERIC_KEYS:
                m.setdefault(key, app_id)
        url = webapp_url(e["Exec"]) or script_webapp_url(e["Exec"])
        key = web_key(url) if url else ""
        if key:
            m.setdefault("web:" + key, []).append(app_id)
    return m


def match_class(cmap, window_class):
    """-> list of candidate app ids (empty if unknown, several if webapps share a class)."""
    c = window_class.lower()
    if c in cmap and not c.startswith(("web:", "name:")):
        return [cmap[c]]
    m = CHROMIUM_CLASS.match(c)
    if not m:
        return []
    key = m.group(1)
    if "web:" + key in cmap:
        return list(cmap["web:" + key])
    # Tolerate www./subdomain differences: same host, then closest path.
    host, _, path = key.partition("_")
    same = []
    for k, ids in cmap.items():
        if not k.startswith("web:"):
            continue
        h, _, pth = k[4:].partition("_")
        if h == host or h.endswith("." + host) or host.endswith("." + h):
            same.append((pth, ids))
    if not same:
        return []
    best = [x for x in same if path.startswith(x[0])]
    if best:
        top = max(len(x[0]) for x in best)
        return [i for x in best if len(x[0]) == top for i in x[1]]
    return [i for x in same for i in x[1]] if len(same) == 1 else []


def recent_menu_launch(ids, within=30):
    """Most recent menu launch of one of `ids` in the last `within` seconds, else None."""
    now = time.time()
    try:
        with open(USAGE, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 8192))
            lines = fh.read().decode(errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if now - e.get("t", 0) > within:
            break
        if e.get("src") == "menu" and e.get("id") in ids:
            return e["id"]
    return None


def title_pick(cmap, cands, title):
    """Pick the candidate whose distinctive name words appear in the window title."""
    toks = {i: {w for w in re.findall(r"[a-z0-9]{3,}", cmap.get("name:" + i, "").lower())}
            for i in cands}
    common = set.intersection(*toks.values()) if toks else set()
    t = title.lower()
    score = {i: sum(1 for w in ws - common if w in t) for i, ws in toks.items()}
    top = max(score.values(), default=0)
    winners = [i for i, v in score.items() if v == top]
    return winners[0] if top > 0 and len(winners) == 1 else None


def resolve(cmap, cands, title):
    """One app id for a window, or None when it can't be told apart (log nothing, never guess)."""
    if len(cands) == 1:
        return cands[0]
    return recent_menu_launch(set(cands)) or (title_pick(cmap, cands, title) if title else None)


def die_with_parent():
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM
    except Exception:
        pass


def track():
    die_with_parent()
    private_dir(DATA_DIR)
    lock = private_open(DATA_DIR / "tracker.lock", os.O_WRONLY, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another tracker is already running
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(f"{runtime}/hypr/{sig}/.socket2.sock")
    cmap, built, misses = class_map(), time.time(), {}
    pending = {}  # window address -> (candidate ids, deadline): waiting for its title
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.decode(errors="ignore")
            now = time.time()
            for addr in [a for a, (_c, dl) in pending.items() if dl < now]:
                del pending[addr]
            if line.startswith("windowtitlev2>>"):
                addr, _, title = line[len("windowtitlev2>>"):].partition(",")
                if addr in pending:
                    app_id = resolve(cmap, pending[addr][0], title)
                    if app_id:
                        del pending[addr]
                        log_usage(app_id, "window")
                continue
            if not line.startswith("openwindow>>"):
                continue
            parts = line[len("openwindow>>"):].split(",", 3)
            if len(parts) < 3:
                continue
            addr, wclass = parts[0], parts[2]
            title = parts[3] if len(parts) > 3 else ""
            cands = match_class(cmap, wclass)
            if not cands and now - built > 60 and now - misses.get(wclass, 0) > 60:
                misses[wclass] = now
                cmap, built = class_map(), now  # app may have just been installed
                cands = match_class(cmap, wclass)
            if not cands:
                continue
            app_id = resolve(cmap, cands, title)
            if app_id:
                log_usage(app_id, "window")
            else:
                pending[addr] = (cands, now + 20)  # title usually arrives a moment later


def main():
    harden()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "index"
    if cmd == "index":
        json.dump(build_index(), sys.stdout)
    elif cmd == "launch" and len(sys.argv) > 2:
        launch(sys.argv[2])
    elif cmd == "edit" and len(sys.argv) >= 4:
        result = edit(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        print(result)
        sys.exit(0 if result == "ok" else 1)
    elif cmd == "track":
        track()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
