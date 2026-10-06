"""File tags for code editors: find the open workspace folder, index its files, swap spoken file names for paths. No win32."""
import json, os, re, subprocess, time
from pathlib import Path
from urllib.parse import unquote, urlparse

EDITORS = {"code.exe": "Code", "cursor.exe": "Cursor", "windsurf.exe": "Windsurf"}  # exe -> %APPDATA% folder
CHAT_HINT = re.compile(r"\b(?:chat|composer|copilot|prompt|ask)\b", re.I)  # whole words: "flask_app.py" and "Tasks" are not chat
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target", ".mypy_cache", ".pytest_cache"}
MAX_FILES, TTL, WALK_BUDGET_S = 5000, 60, 1.5
_cache = {}  # root -> (time, index)


def title_folder(title):
    """Workspace folder name from an editor window title ('file - folder - Visual Studio Code', em dashes too), or None."""
    parts = re.split(r"\s+[\u2014\u2013-]\s+", title.strip())
    if len(parts) >= 3:
        name = parts[-2]
    elif len(parts) == 2 and not re.search(r"\.\w{1,6}$", parts[0]):
        name = parts[0]  # 'folder - App' (no file open)
    else:
        return None
    name = re.sub(r"\s*\[[^\]]*\]$", "", name)  # [WSL: Ubuntu-24.04], [SSH: host]
    return re.sub(r"\s*\(Workspace\)$", "", name).strip() or None


def uri_to_path(uri):
    """Folder URI from storage.json -> path string (forward slashes): file:///c%3A/x -> c:/x; WSL remote -> //wsl.localhost/<distro>/x."""
    u = urlparse(uri)
    path = unquote(u.path)
    if u.scheme == "file":
        return path[1:] if re.match(r"/[A-Za-z]:", path) else path
    host = unquote(u.netloc)
    if u.scheme == "vscode-remote" and host.lower().startswith("wsl+"):
        return f"//wsl.localhost/{host[4:]}{path}"
    return None


def storage_folders(data):
    """Folder paths an editor has open, from its parsed globalStorage/storage.json."""
    ws, uris = data.get("windowsState", {}), []
    uris.append(ws.get("lastActiveWindow", {}).get("folder"))
    uris += [w.get("folder") for w in ws.get("openedWindows", [])]
    uris += [f.get("folderUri") for f in data.get("backupWorkspaces", {}).get("folders", [])]
    return list(dict.fromkeys(p for p in (uri_to_path(u) for u in uris if u) if p))


def roots_for(app, title, cfg, appdata=None):
    """Workspace root folders for an editor window: the storage.json folder whose basename matches the title's folder,
    plus explicit [code] roots. [] for non-editors."""
    if app not in EDITORS:
        return []
    roots = []
    name = title_folder(title)
    base = appdata or os.environ.get("APPDATA", "")
    try:
        data = json.loads((Path(base) / EDITORS[app] / "User" / "globalStorage" / "storage.json").read_text(encoding="utf-8"))
        roots = [Path(p) for p in storage_folders(data) if name and p.rstrip("/").rsplit("/", 1)[-1].lower() == name.lower()]
    except (OSError, ValueError):
        pass
    return roots + [Path(r) for r in cfg.get("code", {}).get("roots", []) if r]


def build_index(files):
    """{basename lower: [relative paths with forward slashes]}."""
    idx = {}
    for f in files:
        f = f.replace("\\", "/")
        idx.setdefault(f.rsplit("/", 1)[-1].lower(), []).append(f)
    return idx


def _git_files(root):
    try:
        r = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(root), "ls-files"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.splitlines() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _walk_files(root):
    out, end = [], time.monotonic() + WALK_BUDGET_S
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
        if len(out) >= MAX_FILES or time.monotonic() > end:
            break
    return out[:MAX_FILES]


def index(root):
    """File index for one root: `git ls-files` when it is a repo, else a capped walk. Cached 60 s per root."""
    hit = _cache.get(str(root))
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    idx = build_index(_git_files(root) or _walk_files(root))
    _cache[str(root)] = (time.monotonic(), idx)
    return idx


def index_roots(roots):
    """Merged index of several roots (a basename found in two roots becomes ambiguous)."""
    merged = {}
    for r in roots:
        for k, v in index(r).items():
            merged.setdefault(k, []).extend(v)
    return merged


def tag_style(mode, control_name):
    """"path" | "at" | None: [code] file_tags "off" | "chat" (AI chat inputs only, '@path') | "always" (path in the editor, @path in chat)."""
    chat = bool(CHAT_HINT.search(control_name or ""))
    if mode == "chat":
        return "at" if chat else None
    return ("at" if chat else "path") if mode == "always" else None


_TOKEN = re.compile(r"(`?)(?<![\w/\\@.-])([\w.-]+\.[A-Za-z]\w{0,5})(?![\w/\\-])\1")


def tag_files(text, idx, style):
    """Replace a filename token that matches exactly ONE indexed file with its relative path (`path` or @path style).
    Ambiguous, unknown or already-pathed names stay as spoken."""
    def sub(m):
        hits = idx.get(m.group(2).lower(), [])
        if len(hits) != 1:
            return m.group(0)
        return f"@{hits[0]}" if style == "at" else f"`{hits[0]}`"
    return _TOKEN.sub(sub, text)
