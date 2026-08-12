#!/usr/bin/env python3
"""
ROM Picker
==========
Serves a web UI to browse games inside a zip and export selected ones.

Usage:
    python3 rom_picker.py [ZIP_PATH] [PORT]

Defaults:
    ZIP_PATH  = ./Roms and games.zip   (put this script next to the zip)
    PORT      = 8000

Then open http://localhost:8000  (or http://<server-ip>:8000 from another machine)
Exported files land in ./output/<platform>/
"""

import sys
import os
import json
import zipfile
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler


# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_ZIP  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Roms and games.zip")
ZIP_PATH     = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ZIP
PORT         = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

SKIP_EXTS    = {".png", ".jpg", ".jpeg", ".xml", ".txt", ".dat", ".cfg",
                ".srm", ".sav", ".db", ".json", ".nfo"}
SKIP_DIRS    = {"Imgs", "Thumbs"}

PLATFORM_NAMES = {
    "ARCADE":        "Arcade",
    "ATARI":         "Atari 2600",
    "COLECO":        "ColecoVision",
    "COMMODORE":     "Commodore 64",
    "CPS3":          "CPS3 (SF3 / JoJo)",
    "DOS":           "DOS",
    "FC":            "NES / Famicom",
    "FDS":           "Famicom Disk System",
    "FIFTYTWOHUNDRED": "Atari 5200",
    "GB":            "Game Boy",
    "GBA":           "Game Boy Advance",
    "GBC":           "Game Boy Color",
    "GG":            "Game Gear",
    "GW":            "Game & Watch",
    "LYNX":          "Atari Lynx",
    "MD":            "Mega Drive / 32X",
    "MS":            "Master System",
    "NDS":           "Nintendo DS",
    "NEOGEO":        "Neo Geo",
    "NGP":           "Neo Geo Pocket",
    "PCE":           "PC Engine / TurboGrafx",
    "PCECD":         "PC Engine CD",
    "PICO":          "Pico-8",
    "PORTS":         "Ports",
    "PS":            "PlayStation",
    "SATELLAVIEW":   "Satellaview",
    "SCUMMVM":       "ScummVM Adventures",
    "SEVENTYEIGHTHUNDRED": "Atari 7800",
    "SFC":           "Super Nintendo",
    "VECTREX":       "Vectrex",
    "ZXS":           "ZX Spectrum",
}

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".rom_cache.json")
GAMES_TXT    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games.txt")
_games_cache = None
_cache_lock  = threading.Lock()

def _scan_zip():
    """Scan the zip and return a list of game dicts. Slow — call once."""
    print("Scanning zip… (this takes a moment, will be cached after)")
    games = []
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        seen_dirs = set()
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue
            parts = name.split("/")
            if len(parts) < 3:
                continue
            if parts[0] != "Roms":
                continue
            platform = parts[1]
            if any(d in SKIP_DIRS for d in parts):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXTS:
                continue
            basename = parts[-1]
            if basename.startswith("webcache") or basename == "miyoogamelist.xml":
                continue

            if platform == "SCUMMVM" and len(parts) >= 4:
                game_dir = parts[2]
                dir_key  = f"SCUMMVM::{game_dir}"
                if dir_key not in seen_dirs:
                    seen_dirs.add(dir_key)
                    games.append({
                        "platform": platform,
                        "name":     game_dir,
                        "path":     f"Roms/SCUMMVM/{game_dir}/",
                        "is_dir":   True,
                        "size":     0,
                    })
                continue

            display_name = os.path.splitext(basename)[0]
            games.append({
                "platform": platform,
                "name":     display_name,
                "path":     name,
                "is_dir":   False,
                "size":     info.file_size,
            })

    # Fill ScummVM sizes
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        for g in games:
            if g["is_dir"]:
                prefix = g["path"]
                g["size"] = sum(i.file_size for i in zf.infolist()
                                if i.filename.startswith(prefix))

    games.sort(key=lambda g: (g["platform"], g["name"].lower()))
    return games


def load_games(force_refresh=False):
    global _games_cache
    with _cache_lock:
        if _games_cache is not None and not force_refresh:
            return _games_cache

        # Try disk cache first (unless refreshing)
        if not force_refresh and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Validate cache is for the same zip (check mtime)
                zip_mtime = os.path.getmtime(ZIP_PATH)
                if data.get("zip_mtime") == zip_mtime:
                    print("Loaded game list from cache.")
                    _games_cache = data["games"]
                    return _games_cache
                else:
                    print("Zip has changed — rescanning…")
            except Exception as e:
                print(f"Cache read failed ({e}), rescanning…")

        # Full scan
        games = _scan_zip()

        # Save to disk cache
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "zip_mtime": os.path.getmtime(ZIP_PATH),
                    "games":     games,
                }, f)
            print(f"Cache saved to {CACHE_FILE}")
        except Exception as e:
            print(f"Warning: could not save cache ({e})")

        # Write human-readable games.txt
        try:
            current_platform = None
            with open(GAMES_TXT, "w", encoding="utf-8") as f:
                f.write(f"ROM List — {len(games)} games\n")
                f.write("=" * 60 + "\n\n")
                for g in games:
                    if g["platform"] != current_platform:
                        current_platform = g["platform"]
                        plat_label = PLATFORM_NAMES.get(current_platform, current_platform)
                        f.write(f"\n[{plat_label}]\n")
                    size_str = ""
                    if g["size"] > 0:
                        mb = g["size"] / 1_048_576
                        size_str = f"  ({mb:.0f} MB)" if mb >= 1 else f"  ({g['size'] // 1024} KB)"
                    f.write(f"  {g['name']}{size_str}\n")
            print(f"Game list written to {GAMES_TXT}")
        except Exception as e:
            print(f"Warning: could not write games.txt ({e})")

        _games_cache = games
        return games


def get_image(platform, name):
    """Return PNG bytes for a game's screenshot from the zip, or None."""
    # Image path convention: Roms/<PLATFORM>/Imgs/<name>.png
    img_path = f"Roms/{platform}/Imgs/{name}.png"
    try:
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            return zf.read(img_path)
    except KeyError:
        return None


def export_games(paths):
    """Extract a list of zip paths (or dir prefixes) into output/."""
    results = []
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        all_names = set(zf.namelist())

        for path in paths:
            if path.endswith("/"):
                # Directory (ScummVM): extract all files under prefix
                platform  = path.split("/")[1]
                game_name = path.rstrip("/").split("/")[-1]
                out_dir   = os.path.join(OUTPUT_DIR, platform, game_name)
                os.makedirs(out_dir, exist_ok=True)
                members   = [n for n in all_names if n.startswith(path) and not n.endswith("/")]
                if not members:
                    results.append({"path": path, "ok": False, "msg": "Not found in zip"})
                    continue
                for member in members:
                    rel    = member[len(path):]
                    target = os.path.join(out_dir, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    data   = zf.read(member)
                    with open(target, "wb") as f:
                        f.write(data)
                results.append({"path": path, "ok": True,
                                 "msg": f"{len(members)} files → output/{platform}/{game_name}/"})
            else:
                if path not in all_names:
                    results.append({"path": path, "ok": False, "msg": "Not found in zip"})
                    continue
                platform = path.split("/")[1]
                out_dir  = os.path.join(OUTPUT_DIR, platform)
                os.makedirs(out_dir, exist_ok=True)
                filename = os.path.basename(path)
                target   = os.path.join(out_dir, filename)
                data     = zf.read(path)
                with open(target, "wb") as f:
                    f.write(data)
                size_mb  = len(data) / 1_048_576

                results.append({"path": path, "ok": True,
                                 "msg": f"{size_mb:.1f} MB → output/{platform}/{filename}"})
    return results


# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ROM Picker</title>
<style>
  :root {
    --bg:      #f2efe4;
    --panel:   #fffdf6;
    --ink:     #1c1c1c;
    --muted:   #6b665c;
    --line:    #1c1c1c;
    --red:     #ff4d4d;
    --blue:    #3b6cff;
    --yellow:  #ffcd3c;
    --green:   #1fa463;
    --shadow:  3px 3px 0 var(--line);
    --shadow-sm: 2px 2px 0 var(--line);
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "SF Pro Rounded", "Segoe UI", system-ui, sans-serif;
         background: var(--bg); color: var(--ink);
         display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
  ::selection { background: var(--yellow); color: var(--ink); }

  /* ── Top bar ── */
  header { display: flex; align-items: center; gap: 12px; padding: 12px 16px;
           background: var(--panel); border-bottom: 3px solid var(--line); flex-shrink: 0; }
  header h1 { font-size: 18px; font-weight: 800; color: var(--ink); letter-spacing: .01em;
              white-space: nowrap; margin-right: auto; }
  #export-btn { padding: 8px 20px; border-radius: 8px; border: 2px solid var(--line);
                background: var(--red); color: #fff; font-size: 14px; font-weight: 800;
                cursor: pointer; white-space: nowrap; box-shadow: var(--shadow-sm);
                transition: transform .08s, box-shadow .08s; }
  #export-btn:disabled { background: #e5e1d3; color: #a29c8c; border-color: #cfc9b8;
                          box-shadow: none; cursor: default; }
  #export-btn:hover:not(:disabled) { transform: translate(-1px,-1px);
                                      box-shadow: 4px 4px 0 var(--line); }
  #export-btn:active:not(:disabled) { transform: translate(1px,1px); box-shadow: none; }
  #sel-count { font-size: 13px; color: var(--muted); font-weight: 700; white-space: nowrap;
               text-align: right; }
  #sel-size  { font-size: 12px; color: var(--blue); font-weight: 700; white-space: nowrap;
               text-align: right; min-width: 70px; }
  #refresh-btn { padding: 6px 12px; border-radius: 8px; border: 2px solid var(--line);
                 background: var(--panel); color: var(--ink); font-size: 12px; font-weight: 700;
                 cursor: pointer; white-space: nowrap; }
  #refresh-btn:hover { background: var(--yellow); }
  #refresh-btn:disabled { opacity: 0.5; cursor: default; background: var(--panel); }

  /* ── Platform tabs + filter ── */
  #filter-row { display: flex; align-items: center; gap: 10px; padding: 10px 16px;
                background: var(--bg); border-bottom: 2px solid var(--line); flex-shrink: 0; }
  #tabs { flex: 1; min-width: 0; display: flex; flex-wrap: nowrap; overflow-x: auto; gap: 6px;
          scrollbar-width: thin; }
  .tab { padding: 6px 14px; border-radius: 20px; border: 2px solid var(--line);
         background: var(--panel); color: var(--ink); font-size: 12px; font-weight: 700;
         cursor: pointer; white-space: nowrap; transition: transform .08s; }
  .tab:hover { transform: translateY(-1px); }
  .tab.active { background: var(--yellow); }
  #filter { flex-shrink: 0; width: 200px; padding: 6px 10px; border-radius: 8px;
            border: 2px solid var(--line); background: var(--panel); color: var(--ink);
            font-size: 12px; font-weight: 600; }
  #filter::placeholder { color: #9a9484; }
  #filter:focus { outline: none; border-color: var(--blue); }

  /* ── Main layout ── */
  main { display: flex; flex: 1; overflow: hidden; }

  /* ── Game list ── */
  #list-wrap { flex: 1; overflow-y: auto; padding: 12px 16px; }
  #game-count { font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 8px;
                text-transform: uppercase; letter-spacing: .04em; }
  .game-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
              border-radius: 8px; border: 2px solid transparent; cursor: pointer;
              transition: background .1s, border-color .1s; }
  .game-row:hover { background: var(--panel); border-color: var(--line); }
  .game-row.selected { background: #fff4cf; border-color: var(--line); }
  .game-row input[type=checkbox] { accent-color: var(--red); width: 16px; height: 16px;
                                    flex-shrink: 0; cursor: pointer; }
  .game-name { flex: 1; font-size: 13px; color: var(--ink); font-weight: 500; }
  .game-row.selected .game-name { font-weight: 700; }
  .game-plat { font-size: 11px; color: var(--muted); min-width: 90px; text-align: right; }
  .game-size { font-size: 11px; color: #9a9484; min-width: 52px; text-align: right; }
  /* ── Screenshot preview panel ── */
  #preview { width: 480px; border-left: 3px solid var(--line); flex-direction: column;
             flex-shrink: 0; background: var(--panel); overflow-y: auto; display: none; }
  #preview.visible { display: flex; }
  #preview-top { display: flex; justify-content: space-between; align-items: center;
                 padding: 12px 18px 8px; flex-shrink: 0; }
  #preview-top-title { font-size: 11px; font-weight: 800; color: var(--muted);
                       text-transform: uppercase; letter-spacing: .08em; }
  #preview-close { background: none; border: none; color: var(--muted); font-size: 22px;
                   cursor: pointer; line-height: 1; padding: 0; }
  #preview-close:hover { color: var(--ink); }
  #preview-img-wrap { width: 100%; background: var(--ink); flex-shrink: 0;
                      border-top: 2px solid var(--line); border-bottom: 2px solid var(--line); }
  #preview-img { width: 100%; display: block; image-rendering: pixelated;
                 object-fit: contain; }
  #preview-no-img { color: #4a4a4a; font-size: 14px; text-align: center;
                    padding: 120px 20px; }
  #preview-info { padding: 20px 20px 10px; flex-shrink: 0; }
  #preview-name { font-size: 20px; font-weight: 800; color: var(--ink); line-height: 1.25;
                  margin-bottom: 6px; }
  #preview-plat { font-size: 13px; color: var(--muted); font-weight: 600; }
  #preview-size { font-size: 13px; color: var(--blue); font-weight: 700; margin-top: 4px; }
  #preview-sel-btn { margin: 12px 20px 20px; padding: 13px; border-radius: 10px;
                     border: 2px solid var(--line); background: var(--red);
                     color: #fff; font-size: 15px; font-weight: 800;
                     cursor: pointer; width: calc(100% - 40px); box-shadow: var(--shadow-sm); }
  #preview-sel-btn:hover:not(.selected) { transform: translate(-1px,-1px);
                                           box-shadow: 4px 4px 0 var(--line); }
  #preview-sel-btn.selected { background: var(--yellow); color: var(--ink); }

  /* ── Sidebar (selected) ── */
  #sidebar { width: 260px; border-left: 3px solid var(--line); display: flex;
             flex-direction: column; overflow: hidden; flex-shrink: 0; background: var(--panel); }
  #sidebar-header { padding: 10px 14px 8px; border-bottom: 2px solid var(--line); flex-shrink: 0; }
  #sidebar-header h2 { font-size: 13px; font-weight: 800; color: var(--ink);
                        text-transform: uppercase; letter-spacing: .03em; }
  #sidebar-total { font-size: 11px; color: var(--blue); font-weight: 700; margin-top: 3px; }
  #selected-list { flex: 1; overflow-y: auto; padding: 8px 14px; }
  .sel-item { display: flex; justify-content: space-between; align-items: flex-start;
              gap: 6px; padding: 6px 0; border-bottom: 1px solid #e5e1d3; }
  .sel-item-name { font-size: 12px; color: var(--ink); font-weight: 600; line-height: 1.3; }
  .sel-item-meta { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .sel-remove { background: none; border: none; color: var(--muted); cursor: pointer;
                font-size: 16px; line-height: 1; padding: 0 2px; flex-shrink: 0; }
  .sel-remove:hover { color: var(--red); }
  #clear-btn { margin: 8px 14px; padding: 6px; border-radius: 8px; border: 2px solid var(--line);
               background: var(--panel); color: var(--ink); font-size: 12px; font-weight: 700;
               cursor: pointer; }
  #clear-btn:hover { background: var(--red); color: #fff; }

  /* ── Toast ── */
  #toast { position: fixed; bottom: 46px; left: 50%; transform: translateX(-50%);
           background: var(--panel); border: 2px solid var(--line); color: var(--ink);
           padding: 10px 20px; border-radius: 10px; font-size: 13px; font-weight: 600;
           box-shadow: var(--shadow); opacity: 0; transition: opacity .3s;
           pointer-events: none; z-index: 99; max-width: 500px; text-align: center;
           white-space: pre-line; }
  #toast.show { opacity: 1; }
  #toast.error { background: #ffe1e1; border-color: var(--red); }

  /* ── Export modal ── */
  #modal-overlay { display: none; position: fixed; inset: 0; background: rgba(28,28,28,.55);
                   z-index: 50; align-items: center; justify-content: center; }
  #modal-overlay.show { display: flex; }
  #modal { background: var(--panel); border: 3px solid var(--line); border-radius: 12px;
           padding: 20px; width: 480px; max-height: 70vh; display: flex;
           flex-direction: column; gap: 12px; box-shadow: 6px 6px 0 var(--line); }
  #modal h2 { font-size: 15px; color: var(--ink); font-weight: 800; }
  #modal-log { flex: 1; overflow-y: auto; font-size: 12px; font-family: ui-monospace, monospace;
               background: var(--bg); border: 2px solid var(--line); border-radius: 8px;
               padding: 10px; color: var(--ink); min-height: 120px; }
  #modal-log .ok   { color: var(--green); font-weight: 600; }
  #modal-log .fail { color: var(--red); font-weight: 600; }
  #modal-close { padding: 7px 18px; border-radius: 8px; border: 2px solid var(--line);
                 background: var(--panel); color: var(--ink); font-weight: 700;
                 cursor: pointer; align-self: flex-end; }
  #modal-close:hover { background: var(--yellow); }

  #empty { color: var(--muted); font-size: 14px; text-align: center; margin-top: 60px; }

  /* ── AI search bar ── */
  #ai-search-bar { display: flex; align-items: center; gap: 10px; padding: 10px 16px;
                    background: var(--blue); border-bottom: 3px solid var(--line);
                    flex-shrink: 0; }
  #ai-load-btn { padding: 8px 14px; border-radius: 8px; border: 2px solid var(--line);
                 background: var(--panel); color: var(--ink); font-size: 13px; font-weight: 800;
                 cursor: pointer; white-space: nowrap; box-shadow: var(--shadow-sm); }
  #ai-load-btn:hover:not(:disabled) { transform: translate(-1px,-1px);
                                       box-shadow: 3px 3px 0 var(--line); }
  #ai-load-btn:disabled { opacity: .75; cursor: default; }
  #ai-load-btn.ready { background: var(--green); color: #fff; }
  #search { flex: 1; padding: 9px 12px; border-radius: 8px; border: 2px solid var(--line);
            background: var(--panel); color: var(--ink); font-size: 14px; font-weight: 500;
            box-shadow: var(--shadow-sm); }
  #search::placeholder { color: #7a7a7a; }
  #search:focus { outline: none; border-color: var(--yellow); }
  #search:disabled { opacity: .55; cursor: not-allowed; background: #e4eaff; box-shadow: none; }
  #ai-ask-btn { padding: 9px 16px; border-radius: 8px; border: 2px solid var(--line);
                background: var(--yellow); color: var(--ink); font-size: 13px; font-weight: 800;
                cursor: pointer; white-space: nowrap; box-shadow: var(--shadow-sm); }
  #ai-ask-btn:hover:not(:disabled) { transform: translate(-1px,-1px);
                                      box-shadow: 3px 3px 0 var(--line); }
  #ai-ask-btn:disabled { background: #dfe3f5; color: #9099c2; border-color: #c2c8e6;
                          box-shadow: none; cursor: default; }

  /* ── Credits ── */
  #credits { flex-shrink: 0; text-align: center; padding: 6px 12px; font-size: 11px;
             font-weight: 600; color: var(--muted); background: var(--panel);
             border-top: 1px solid #e5e1d3; }
  #credits a { color: var(--blue); text-decoration: none; font-weight: 700; }
  #credits a:hover { text-decoration: underline; }
</style>
</head>
<body>

<header>
  <h1>🕹️ ROM PICKER</h1>
  <span id="sel-count">0 selected</span>
  <span id="sel-size"></span>
  <button id="refresh-btn" title="Re-scan zip and rebuild cache">⟳ Refresh zip</button>
  <button id="export-btn" disabled>Export →</button>
</header>

<div id="ai-search-bar">
  <button id="ai-load-btn">🤖 Load AI model</button>
  <input id="search" type="search" placeholder="Load the AI model to ask it something, e.g. &quot;select all fighting games&quot;" autocomplete="off" disabled>
  <button id="ai-ask-btn" disabled>Ask AI →</button>
</div>

<div id="filter-row">
  <div id="tabs"></div>
  <input id="filter" type="search" placeholder="Filter visible games…" autocomplete="off">
</div>

<main>
  <div id="list-wrap">
    <div id="game-count"></div>
    <div id="game-list"></div>
    <div id="empty" style="display:none">No games found.</div>
  </div>
  <!-- Screenshot preview panel -->
  <div id="preview">
    <div id="preview-top">
      <span id="preview-top-title">Preview</span>
      <button id="preview-close">×</button>
    </div>
    <div id="preview-img-wrap">
      <img id="preview-img" src="" alt="" style="display:none">
      <div id="preview-no-img">No screenshot</div>
    </div>
    <div id="preview-info">
      <div id="preview-name"></div>
      <div id="preview-plat"></div>
      <div id="preview-size"></div>
    </div>
    <button id="preview-sel-btn">+ Add to selection</button>
  </div>

  <aside id="sidebar">
    <div id="sidebar-header">
      <h2>Selected games</h2>
      <div id="sidebar-total"></div>
    </div>
    <div id="selected-list"></div>
    <button id="clear-btn">Clear all</button>
  </aside>
</main>


<div id="toast"></div>

<div id="modal-overlay">
  <div id="modal">
    <h2>Exporting…</h2>
    <div id="modal-log"></div>
    <button id="modal-close">Close</button>
  </div>
</div>

<div id="credits">🕹️ Coded by Claude · check more stuff at <a href="https://jon.io" target="_blank" rel="noopener">jon.io</a></div>

<script>

let allGames = [];
let selected = new Map(); // path -> game obj
let activePlatform = 'ALL';
let searchTerm = '';

const $ = id => document.getElementById(id);

// ── Fetch game list ────────────────────────────────────────────────────────
async function init() {
  $('game-count').textContent = 'Loading game list…';
  const res  = await fetch('/api/games');
  allGames   = await res.json();
  buildTabs();
  render();
}

// ── Tabs ───────────────────────────────────────────────────────────────────
function buildTabs() {
  const platforms = ['ALL', ...new Set(allGames.map(g => g.platform))];
  const counts    = {};
  allGames.forEach(g => { counts[g.platform] = (counts[g.platform] || 0) + 1; });
  const container = $('tabs');
  container.innerHTML = '';
  platforms.forEach(p => {
    const btn = document.createElement('button');
    btn.className = 'tab' + (p === 'ALL' ? ' active' : '');
    btn.dataset.p  = p;
    const label = p === 'ALL'
      ? `All (${allGames.length})`
      : `${PLAT[p] || p} (${counts[p] || 0})`;
    btn.textContent = label;
    btn.onclick = () => {
      activePlatform = p;
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      render();
    };
    container.appendChild(btn);
  });
}

// ── Render game list ───────────────────────────────────────────────────────
function render() {
  const term    = searchTerm.toLowerCase();
  const visible = allGames.filter(g => {
    const matchPlat = activePlatform === 'ALL' || g.platform === activePlatform;
    const matchName = !term || g.name.toLowerCase().includes(term);
    return matchPlat && matchName;
  });

  $('game-count').textContent = `${visible.length} game${visible.length !== 1 ? 's' : ''}`;
  $('empty').style.display    = visible.length ? 'none' : 'block';

  const frag = document.createDocumentFragment();
  visible.forEach(g => {
    const row  = document.createElement('div');
    row.className = 'game-row' + (selected.has(g.path) ? ' selected' : '');
    row.dataset.path = g.path;

    const cb   = document.createElement('input');
    cb.type    = 'checkbox';
    cb.checked = selected.has(g.path);
    cb.tabIndex = -1;

    const name = document.createElement('span');
    name.className   = 'game-name';
    name.textContent = g.name;

    const plat = document.createElement('span');
    plat.className   = 'game-plat';
    plat.textContent = activePlatform === 'ALL' ? (PLAT[g.platform] || g.platform) : '';

    const size = document.createElement('span');
    size.className   = 'game-size';
    size.textContent = g.size > 0 ? fmtSize(g.size) : '';

    cb.onclick = (e) => { e.stopPropagation(); toggleGame(g, row, cb); };

    row.append(cb, name, plat, size);
    row.onclick = () => showPreview(g);
    frag.appendChild(row);
  });

  const list = $('game-list');
  list.innerHTML = '';
  list.appendChild(frag);
}

function toggleGame(g, row, cb) {
  if (selected.has(g.path)) {
    selected.delete(g.path);
    row.classList.remove('selected');
    cb.checked = false;
  } else {
    selected.set(g.path, g);
    row.classList.add('selected');
    cb.checked = true;
    showPreview(g);  // auto-show screenshot on select
  }
  updateSidebar();
}

// ── Sidebar + total size ───────────────────────────────────────────────────
function updateSidebar() {
  const count    = selected.size;
  const totalBytes = [...selected.values()].reduce((s, g) => s + (g.size || 0), 0);

  $('sel-count').textContent = count ? `${count} selected` : '0 selected';
  $('sel-size').textContent  = count ? fmtSize(totalBytes) : '';
  $('export-btn').disabled   = count === 0;

  // Sidebar total
  $('sidebar-total').textContent = count
    ? `${count} game${count !== 1 ? 's' : ''} · ${fmtSize(totalBytes)}`
    : '';

  const list = $('selected-list');
  list.innerHTML = '';
  selected.forEach((g, path) => {
    const item  = document.createElement('div');
    item.className = 'sel-item';

    const info  = document.createElement('div');
    const nspan = document.createElement('div');
    nspan.className   = 'sel-item-name';
    nspan.textContent = g.name;
    const pspan = document.createElement('div');
    pspan.className   = 'sel-item-meta';
    pspan.textContent = (PLAT[g.platform] || g.platform) + (g.size ? '  ' + fmtSize(g.size) : '');
    info.append(nspan, pspan);

    const rm  = document.createElement('button');
    rm.className   = 'sel-remove';
    rm.textContent = '×';
    rm.onclick = () => {
      selected.delete(path);
      updateSidebar();
      const row = document.querySelector(`.game-row[data-path="${CSS.escape(path)}"]`);
      if (row) { row.classList.remove('selected'); row.querySelector('input').checked = false; }
      if (previewGame && previewGame.path === path) updatePreviewSelBtn();
    };

    item.append(info, rm);
    list.appendChild(item);
  });
}

// ── Export ─────────────────────────────────────────────────────────────────
$('export-btn').onclick = async () => {
  if (!selected.size) return;
  const paths = [...selected.keys()];
  $('modal-overlay').classList.add('show');
  $('modal-log').innerHTML = `<div>Exporting ${paths.length} game(s)…</div>`;
  $('modal-close').disabled = true;

  const res  = await fetch('/api/export', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ paths }),
  });
  const data = await res.json();

  const log  = $('modal-log');
  log.innerHTML = '';
  data.results.forEach(r => {
    const line = document.createElement('div');
    line.className   = r.ok ? 'ok' : 'fail';
    line.textContent = (r.ok ? '✓ ' : '✗ ') + r.msg;
    log.appendChild(line);
  });
  const ok  = data.results.filter(r => r.ok).length;
  const bad = data.results.length - ok;
  const sum = document.createElement('div');
  sum.style.marginTop  = '8px';
  sum.style.color      = '#6b665c';
  sum.style.fontWeight = '700';
  sum.textContent = `Done — ${ok} exported, ${bad} failed. Files are in ./output/`;
  log.appendChild(sum);
  $('modal-close').disabled = false;
};

$('modal-close').onclick  = () => $('modal-overlay').classList.remove('show');
$('clear-btn').onclick    = () => { selected.clear(); updateSidebar(); render(); };

// ── Refresh zip ────────────────────────────────────────────────────────────
$('refresh-btn').onclick = async () => {
  $('refresh-btn').disabled = true;
  $('refresh-btn').textContent = '⟳ Scanning…';
  $('game-count').textContent  = 'Re-scanning zip…';
  try {
    const res  = await fetch('/api/refresh');
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    // Reload game list
    const res2 = await fetch('/api/games');
    allGames   = await res2.json();
    buildTabs();
    render();
    showToast(`Refreshed — ${data.count} games found`, '', 3000);
  } catch (e) {
    showToast('Refresh failed: ' + e.message, 'error', 4000);
  } finally {
    $('refresh-btn').disabled    = false;
    $('refresh-btn').textContent = '⟳ Refresh zip';
  }
};

// ── Filter ─────────────────────────────────────────────────────────────────
$('filter').addEventListener('input', e => {
  searchTerm = e.target.value;
  render();
});

// ── Screenshot preview ─────────────────────────────────────────────────────
let previewGame = null;

function showPreview(g) {
  previewGame = g;
  $('preview').classList.add('visible');

  $('preview-name').textContent = g.name;
  $('preview-plat').textContent = PLAT[g.platform] || g.platform;
  $('preview-size').textContent = g.size > 0 ? fmtSize(g.size) : '';

  const img   = $('preview-img');
  const noImg = $('preview-no-img');

  img.style.display   = 'none';
  noImg.style.display = 'block';
  img.src = '';

  updatePreviewSelBtn();

  // The image in the zip is always named after the display name for most platforms.
  // For NEOGEO/ARCADE the img filename matches the rom basename (without extension).
  // We try display name first; on error try the rom basename.
  const displayName = g.name;
  const romBasename = g.path.split('/').pop().replace(/\.[^.]+$/, '');

  function tryLoad(name, fallback) {
    const url = `/api/img?platform=${encodeURIComponent(g.platform)}&name=${encodeURIComponent(name)}`;
    img.onload  = () => { img.style.display = 'block'; noImg.style.display = 'none'; };
    img.onerror = () => {
      if (fallback && name !== fallback) {
        tryLoad(fallback, null);
      } else {
        img.style.display = 'none';
        noImg.style.display = 'block';
      }
    };
    img.src = url;
  }

  tryLoad(displayName, romBasename !== displayName ? romBasename : null);
}

function updatePreviewSelBtn() {
  if (!previewGame) return;
  const selBtn = $('preview-sel-btn');
  const isSel  = selected.has(previewGame.path);
  selBtn.textContent = isSel ? '✓ In selection' : '+ Add to selection';
  selBtn.className   = 'preview-sel-btn' + (isSel ? ' selected' : '');
}

$('preview-close').onclick = () => { $('preview').classList.remove('visible'); previewGame = null; };

$('preview-sel-btn').onclick = () => {
  if (!previewGame) return;
  if (selected.has(previewGame.path)) {
    selected.delete(previewGame.path);
  } else {
    selected.set(previewGame.path, previewGame);
  }
  updatePreviewSelBtn();
  updateSidebar();
  render();
};

// ── Toast ──────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = '', duration = 3000) {
  const t = $('toast');
  t.textContent = msg;
  t.className   = 'show' + (type ? ' ' + type : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = '', duration);
}

// ── AI assistant (local, WebLLM) ──────────────────────────────────────────
let aiEngine  = null;
let aiLoading = false;
let aiAsking  = false;
const AI_MODEL      = 'Phi-3.5-mini-instruct-q4f16_1-MLC';
const AI_BATCH_SIZE = 200; // games per model call, keeps prompts within context window

async function loadAI() {
  if (aiEngine || aiLoading) return;
  if (!window.isSecureContext) {
    showToast(
      'WebGPU needs a secure context. Plain http:// to a LAN IP doesn\'t qualify.\n' +
      'Open this page via http://localhost:PORT on this machine, or on Chrome add this ' +
      'origin to chrome://flags/#unsafely-treat-insecure-origin-as-secure and relaunch.',
      'error', 8000
    );
    return;
  }
  if (!navigator.gpu) {
    showToast('WebGPU not supported in this browser. Try Chrome 113+ or Edge 113+.', 'error', 5000);
    return;
  }
  aiLoading = true;
  const btn = $('ai-load-btn');
  btn.disabled = true;
  btn.textContent = 'Loading engine…';
  try {
    const webllm = await import('https://esm.run/@mlc-ai/web-llm');
    aiEngine = await webllm.CreateMLCEngine(AI_MODEL, {
      initProgressCallback: (p) => {
        btn.textContent = p.text || `Loading… ${Math.round((p.progress || 0) * 100)}%`;
      },
    });
    btn.textContent = '✓ AI model ready';
    btn.classList.add('ready');
    $('search').disabled     = false;
    $('ai-ask-btn').disabled = false;
    showToast('AI model loaded — type a request in the search bar, then hit Ask AI.', '', 4000);
  } catch (e) {
    showToast('Failed to load AI model: ' + e.message, 'error', 5000);
    btn.textContent = '🤖 Load AI model';
    btn.disabled = false;
  } finally {
    aiLoading = false;
  }
}

async function askAI() {
  const query = $('search').value.trim();
  if (!query || !aiEngine || aiAsking) return;

  // Scope to the active platform tab — much faster than scanning every game.
  const pool = activePlatform === 'ALL'
    ? allGames
    : allGames.filter(g => g.platform === activePlatform);

  aiAsking = true;
  const askBtn = $('ai-ask-btn');
  const input  = $('search');
  askBtn.disabled = true;
  input.disabled  = true;

  const batches = [];
  for (let i = 0; i < pool.length; i += AI_BATCH_SIZE) {
    batches.push(pool.slice(i, i + AI_BATCH_SIZE));
  }

  const matchedPaths = new Set();
  let errCount = 0;

  try {
    for (let bi = 0; bi < batches.length; bi++) {
      askBtn.textContent = `Thinking… (${bi + 1}/${batches.length})`;
      const batch    = batches[bi];
      const listText = batch.map(g => `${g.name} [${g.platform}]`).join('\n');
      const messages = [
        { role: 'system', content:
          'You select video games from a list based on a user request. ' +
          'You are given a list of "Game Name [PLATFORM]" lines, one per game, and a request. ' +
          'Reply with ONLY a JSON array of the exact game names (without the platform tag) from ' +
          'THIS list that match the request. If none match, reply with []. ' +
          'No explanation, no markdown — just the JSON array.' },
        { role: 'user', content: `Request: ${query}\n\nGames:\n${listText}` },
      ];
      try {
        const reply = await aiEngine.chat.completions.create({ messages, temperature: 0.2 });
        const text  = reply.choices[0].message.content.trim();
        const m     = text.match(/\[[\s\S]*\]/);
        const names = m ? JSON.parse(m[0]) : [];
        names.forEach(n => {
          const hit = batch.find(g => g.name === n);
          if (hit) matchedPaths.add(hit.path);
        });
      } catch (e) {
        errCount++;
      }
    }
  } finally {
    matchedPaths.forEach(path => {
      const g = allGames.find(x => x.path === path);
      if (g && !selected.has(path)) selected.set(path, g);
    });
    updateSidebar();

    askBtn.disabled    = false;
    askBtn.textContent = 'Ask AI →';
    input.disabled     = false;
    input.value        = '';
    aiAsking = false;

    const msg = matchedPaths.size
      ? `AI selected ${matchedPaths.size} game(s)${errCount ? ` (${errCount} batch(es) failed)` : ''}`
      : `AI found no matches${errCount ? ` (${errCount} batch(es) failed)` : ''}`;
    showToast(msg, matchedPaths.size ? '' : 'error', 4000);
  }
}

$('ai-load-btn').onclick = loadAI;
$('ai-ask-btn').onclick  = askAI;
$('search').addEventListener('keydown', e => { if (e.key === 'Enter' && aiEngine) askAI(); });

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024)            return bytes + ' B';
  if (bytes < 1048576)         return Math.round(bytes / 1024) + ' KB';
  if (bytes < 1073741824)      return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}

const PLAT = __PLATFORM_NAMES__;

init();
</script>
</body>
</html>
"""


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Quiet mode — only print errors
        if int(args[1]) >= 400:
            print(f"[{args[1]}] {args[0]}")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/" or path == "/index.html":
            # Inject platform names into the HTML
            pnames_js = json.dumps(PLATFORM_NAMES)
            page = HTML.replace("__PLATFORM_NAMES__", pnames_js).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(page))
            self.end_headers()
            self.wfile.write(page)

        elif path == "/api/games":
            try:
                games = load_games()
                self.send_json(games)
            except FileNotFoundError:
                self.send_json({"error": f"Zip not found: {ZIP_PATH}"}, 500)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/refresh":
            try:
                games = load_games(force_refresh=True)
                self.send_json({"count": len(games)})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/img":
            qs       = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            platform = qs.get("platform", [""])[0]
            name     = qs.get("name", [""])[0]
            if not platform or not name:
                self.send_response(400); self.end_headers(); return
            data = get_image(platform, name)
            if data is None:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/export":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            data   = json.loads(body)
            paths  = data.get("paths", [])
            try:
                results = export_games(paths)
                self.send_json({"results": results})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(ZIP_PATH):
        print(f"ERROR: Zip file not found at: {ZIP_PATH}")
        print("Either put this script in the same folder as the zip,")
        print("or run:  python3 rom_picker.py /path/to/Roms\\ and\\ games.zip")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"ROM Picker")
    print(f"  Zip:    {ZIP_PATH}")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  URL:    http://localhost:{PORT}")
    print(f"  (Ctrl+C to stop)\n")
    print(f"  First load scans the zip — may take a few seconds…")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
