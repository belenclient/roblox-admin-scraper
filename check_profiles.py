"""Drives the user's Chrome (one tab) through every profile in admins.txt.

Each line of admins.txt is 'Name | Id'. The profile URL is
https://www.roblox.com/users/{id}/profile — the script navigates the SAME tab
to each one, waits for the page to fully load, then moves to the next.

Progress is written to checker_progress.txt after every profile, so stopping or
closing Chrome mid-way and re-running check_profiles.bat resumes exactly where
it left off. New admins appended to admins.txt are picked up automatically.

Usage:
    python check_profiles.py            # resume (or start at 0)
    python check_profiles.py 0          # force start over from the beginning
    python check_profiles.py 42         # start at a specific entry
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMINS_FILE = os.path.join(BASE_DIR, "admins.txt")
PROGRESS_FILE = os.path.join(BASE_DIR, "checker_progress.txt")
LOG_FILE = os.path.join(BASE_DIR, "checker_log.txt")
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")

PORT = 9222
DEBUG_URL = "http://127.0.0.1:{}".format(PORT)
LOAD_TIMEOUT = 60
HOLD_AFTER_LOAD = 2.5

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def request_json(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def chrome_exe():
    paths = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        p = os.path.join(os.environ.get(env, ""), "Google\\Chrome\\Application\\chrome.exe")
        if p:
            paths.append(p)
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        paths.append(os.path.join(la, "Google\\Chrome\\Application\\chrome.exe"))
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def port_up(timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        try:
            request_json(DEBUG_URL + "/json/version")
            return True
        except Exception:
            time.sleep(0.5)
    return False


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.seq = 0

    def send(self, method, params=None):
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def wait_event(self, method, timeout):
        self.ws.settimeout(max(timeout, 1))
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("method") == method:
                    return True
        except Exception:
            return False


def ensure_chrome():
    if port_up(3):
        log("Chrome is already running with the debug port; attaching to it.")
        return
    exe = chrome_exe()
    if not exe:
        log("ERROR: Google Chrome not found on this machine.")
        sys.exit(1)
    log("Launching Chrome (your default profile) with the debug port...")
    subprocess.Popen([exe, "--remote-debugging-port={}".format(PORT), "about:blank"])
    if port_up(12):
        log("Attached to Chrome (default profile).")
        return
    log("Default profile was already in use (no debug port). Starting a dedicated Chrome profile instead...")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    subprocess.Popen(
        [exe, "--user-data-dir={}".format(PROFILE_DIR),
         "--remote-debugging-port={}".format(PORT), "about:blank"]
    )
    if not port_up(12):
        log("ERROR: could not attach to Chrome. Close all Chrome windows and re-run.")
        sys.exit(1)
    log("Attached to Chrome (dedicated profile: {})".format(PROFILE_DIR))


def give_me_a_page():
    tabs = request_json(DEBUG_URL + "/json/list")
    page = next(
        (t for t in tabs if t.get("type") == "page" and t.get("url", "").startswith("about:blank")),
        None,
    )
    if page is None:
        page = next((t for t in tabs if t.get("type") == "page"), None)
    if page is None:
        page = request_json(DEBUG_URL + "/json/new?about:blank", method="PUT")
    return page["webSocketDebuggerUrl"]


def load_entries():
    entries = []
    with open(ADMINS_FILE, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            name, _, uid = ln.rpartition(" | ")
            name = name.strip()
            uid = uid.strip()
            if not uid.isdigit():
                log("Skipping line without an id: {}".format(ln))
                continue
            entries.append((name, int(uid)))
    return entries


def main():
    entries = load_entries()
    if not entries:
        log("No profiles in admins.txt to visit.")
        return

    start = 0
    if os.path.exists(PROGRESS_FILE):
        try:
            start = int(open(PROGRESS_FILE, "r").read().strip() or 0)
        except Exception:
            start = 0
    if len(sys.argv) > 1:
        try:
            start = max(0, int(sys.argv[1]))
        except ValueError:
            pass
    if start < 0:
        start = 0
    if start >= len(entries):
        log(
            "Saved progress says everything is done; nothing new yet. Any admins "
            "appended to admins.txt later will be picked up on the next run. "
            "Pass 0 to force a fresh pass: python check_profiles.py 0"
        )
        start = len(entries)

    ensure_chrome()
    cdp = CDP(give_me_a_page())
    cdp.send("Page.enable")

    log(
        "Checking {} profiles, starting at entry #{}.".format(
            len(entries), start + 1 if start < len(entries) else "end"
        )
    )

    for i in range(start, len(entries)):
        name, uid = entries[i]
        url = "https://www.roblox.com/users/{}/profile".format(uid)
        log("[{}/{}] Loading {} | {}".format(i + 1, len(entries), name, url))
        try:
            cdp.send("Page.navigate", {"url": url})
        except Exception as e:
            log("  navigation error: {} - moving on".format(e))
            time.sleep(HOLD_AFTER_LOAD)
            _save_progress(i + 1)
            continue
        if not cdp.wait_event("Page.loadEventFired", LOAD_TIMEOUT):
            log("  load timed out for {}; retrying once".format(name))
            try:
                cdp.send("Page.reload", {"ignoreCache": True})
                if not cdp.wait_event("Page.loadEventFired", LOAD_TIMEOUT):
                    log("  still not fully loaded for {}; moving on anyway".format(name))
            except Exception as e:
                log("  reload error: {}".format(e))
        time.sleep(HOLD_AFTER_LOAD)
        _save_progress(i + 1)

    log("Finished viewing all {} profiles. Re-run the bat file to resume later.".format(len(entries)))


def _save_progress(index):
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            f.write(str(index))
    except OSError as e:
        log("  could not save progress: {}".format(e))


if __name__ == "__main__":
    main()