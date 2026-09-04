import argparse
import json
import os
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMINS_FILE = os.path.join(BASE_DIR, "admins.txt")
LOG_FILE = os.path.join(BASE_DIR, "scraper_log.txt")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.roblox.com/",
}

ROBLOX_BADGES_URL = "https://accountinformation.roblox.com/v1/users/{}/roblox-badges"
USER_INFO_URL = "https://users.roblox.com/v1/users/{}"
MEMBERS_URL = "https://groups.roblox.com/v1/groups/{}/users"

ADMIN_BADGE_IDS = {1, 71824357}
ADMIN_BADGE_NAME = "administrator"

COLOR_NORMAL = 7
COLOR_GREEN = 10
COLOR_YELLOW = 14
COLOR_RED = 12

session = requests.Session()
session.headers.update(HEADERS)

state = {
    "admins": {},
    "known_admin_names": set(),
    "visited_users": set(),
    "pending": [],
    "seed_groups": [],
    "seed_users": [],
    "processed": 0,
}

delay = 2.0
checkpoint_every = 25


def set_title(text):
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(str(text))
        except Exception:
            pass


def print_console(msg, color=COLOR_NORMAL):
    if sys.platform == "win32":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            ctypes.windll.kernel32.SetConsoleTextAttribute(handle, color)
            print(msg, flush=True)
            ctypes.windll.kernel32.SetConsoleTextAttribute(handle, COLOR_NORMAL)
            return
        except Exception:
            pass
    print(msg, flush=True)


def log(msg, color=COLOR_NORMAL):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    print_console(line, color)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def http_request(method, url, params=None, timeout=20):
    """Return (response, err). On a 429 it retries every 2s (flat, never grows);
    if it still cannot get through it hands back to the caller, which re-queues
    the id and keeps moving down the list."""
    err = "error"
    for attempt in range(1, 4):
        try:
            r = session.request(method, url, params=params, headers=dict(HEADERS), timeout=timeout)
            if r.status_code == 429:
                err = "rate"
                log(f"Rate limited (429) on {url}; retrying in 2s", COLOR_YELLOW)
                time.sleep(2)
                continue
            if r.status_code == 404:
                return None, "not_found"
            if r.status_code in (401, 403):
                err = "blocked"
                log(f"Blocked ({r.status_code}) on {url}; retrying in 2s", COLOR_YELLOW)
                time.sleep(2)
                continue
            r.raise_for_status()
            return r, None
        except requests.RequestException as e:
            log(f"Request error for {url}: {e}", COLOR_YELLOW)
            if attempt < 3:
                time.sleep(2)
    return None, err


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state["admins"] = {str(k): v for k, v in (data.get("admins") or {}).items()}
        state["known_admin_names"] = set(data.get("known_admin_names") or [])
        state["visited_users"] = set(int(x) for x in (data.get("visited_users") or []))
        state["pending"] = [int(x) for x in (data.get("pending") or [])]
        state["seed_groups"] = [int(x) for x in (data.get("seed_groups") or [])]
        state["seed_users"] = [int(x) for x in (data.get("seed_users") or [])]
        state["processed"] = int(data.get("processed") or 0)
        log(
            f"Resumed from {STATE_FILE}: {len(state['admins'])} admins, "
            f"{len(state['visited_users'])} checked, {len(state['pending'])} pending retries"
        )
    except Exception as e:
        log(f"Could not load state file ({e}); starting fresh.")


def save_state():
    data = {
        "admins": state["admins"],
        "known_admin_names": sorted(state["known_admin_names"]),
        "visited_users": sorted(state["visited_users"]),
        "pending": state["pending"],
        "seed_groups": state["seed_groups"],
        "seed_users": state["seed_users"],
        "processed": state["processed"],
    }
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log(f"Could not save state: {e}")


def load_existing_admins():
    if not os.path.exists(ADMINS_FILE):
        return
    with open(ADMINS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if not name:
                continue
            if " | " in name:
                name = name.split(" | ", 1)[0].strip()
            if name:
                state["known_admin_names"].add(name.casefold())


def resolve_usernames(names):
    """Best-effort username -> user id resolution via users.roblox.com (public)."""
    resolved = {}
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        try:
            r = session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": chunk},
                headers=dict(HEADERS),
                timeout=20,
            )
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    uname = (item.get("requestedUsername") or "").casefold()
                    if uname and item.get("id"):
                        resolved[uname] = int(item["id"])
        except (requests.RequestException, ValueError):
            pass
    return resolved


def migrate_admins():
    """Rewrite any legacy name-only lines in admins.txt as 'Name | id'."""
    if not os.path.exists(ADMINS_FILE):
        return
    with open(ADMINS_FILE, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if not lines:
        return
    if all(" | " in ln for ln in lines):
        return

    name_to_id = {}
    for uid, uname in state["admins"].items():
        if uname:
            name_to_id.setdefault(uname.casefold(), int(uid))

    new_lines = []
    legacy = []
    for ln in lines:
        if " | " in ln:
            new_lines.append(ln)
            continue
        uid = name_to_id.get(ln.casefold())
        if uid is not None:
            new_lines.append(f"{ln} | {uid}")
        else:
            legacy.append(ln)

    if legacy:
        resolved = resolve_usernames(legacy)
        for name in legacy:
            uid = resolved.get(name.casefold())
            if uid is not None:
                new_lines.append(f"{name} | {uid}")
            else:
                new_lines.append(name)

    tmp = ADMINS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(new_lines) + "\n")
        os.replace(tmp, ADMINS_FILE)
        migrated = sum(1 for ln in new_lines if " | " in ln)
        log(f"admins.txt now in '<Name> | <id>' format ({migrated} entries with ids)")
    except OSError as e:
        log(f"Could not migrate {ADMINS_FILE}: {e}")


def fetch_roblox_badges(user_id):
    """Return (badges, err). err is None on a completed request, else failure kind."""
    r, err = http_request("GET", ROBLOX_BADGES_URL.format(user_id))
    if r is None:
        return [], err
    try:
        data = r.json()
    except ValueError:
        return [], "error"
    if not isinstance(data, list):
        return [], "error"
    return data, None


def lookup_username(user_id):
    """Return (username_or_None, err). err is None on a completed request, else failure kind."""
    r, err = http_request("GET", USER_INFO_URL.format(user_id))
    if r is None:
        return None, err
    try:
        data = r.json()
    except ValueError:
        return None, "error"
    return data.get("name"), None


def fetch_members(group_id):
    """Return (member_ids, err). err is None on a completed fetch, else failure kind."""
    users = []
    cursor = None
    while True:
        params = {"limit": "100", "sortOrder": "Asc"}
        if cursor:
            params["cursor"] = cursor
        r, err = http_request("GET", MEMBERS_URL.format(group_id), params=params)
        if r is None:
            return users, err
        try:
            data = r.json()
        except ValueError:
            return users, "error"
        for item in data.get("data", []):
            u = item.get("user") or {}
            uid = u.get("id") or u.get("userId")
            if uid:
                users.append(int(uid))
        cursor = data.get("nextPageCursor")
        if not cursor:
            return users, None
        time.sleep(delay / 2.0)


def has_admin_badge(badges):
    for badge in badges:
        if not isinstance(badge, dict):
            continue
        badge_id = badge.get("id")
        name = (badge.get("name") or "").strip().lower()
        if badge_id in ADMIN_BADGE_IDS and name == ADMIN_BADGE_NAME:
            return True
        if "administrator" in name:
            return True
    return False


def add_admin(user_id, username):
    if not username:
        log(f"[SKIP] id {user_id} has the badge but no verified username; not written", COLOR_YELLOW)
        return
    key = username.casefold()
    if key in state["known_admin_names"]:
        return
    state["admins"][str(user_id)] = username
    state["known_admin_names"].add(key)
    try:
        with open(ADMINS_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{username} | {user_id}\n")
    except OSError as e:
        log(f"Could not write to {ADMINS_FILE}: {e}", COLOR_RED)
        return
    log(f"[ADMIN] {username} | {user_id} -> {ADMINS_FILE}", COLOR_GREEN)


def check_user(user_id):
    """Check one member for the Administrator badge. Returns 'ok', 'admin',
    'not_found', or a failure kind ('rate'/'blocked'/'error')."""
    badges, err = fetch_roblox_badges(user_id)
    if err is not None:
        return err
    if not has_admin_badge(badges):
        return "ok"
    username, uerr = lookup_username(user_id)
    if uerr is not None:
        return uerr
    add_admin(user_id, username)
    return "admin"


def run():
    members = []
    seen = set()
    for gid in state["seed_groups"]:
        while True:
            ids, err = fetch_members(gid)
            if err is None:
                break
            log(f"Could not fetch members of group {gid} ({err}); retrying in 2s", COLOR_YELLOW)
            time.sleep(2)
        log(f"Group {gid}: {len(ids)} members loaded")
        for u in ids:
            if u not in seen:
                seen.add(u)
                members.append(u)
    for uid in state["seed_users"]:
        if uid not in seen:
            seen.add(uid)
            members.append(uid)

    pending = list(state["pending"])
    pending_set = set(pending)
    retry_kinds = ("rate", "blocked", "error")

    def update_title(uid):
        set_title(
            f"Roblox Admin Scraper | Admins: {len(state['admins'])} | "
            f"Checked: {len(state['visited_users'])}/{len(members) or '?'} | "
            f"Pending: {len(pending)} | Checking: id {uid}"
        )

    def handle(user_id):
        nonlocal pending, pending_set
        if user_id in state["visited_users"] or user_id in pending_set:
            return
        update_title(user_id)
        result = check_user(user_id)
        if result in retry_kinds:
            pending_set.add(user_id)
            pending.append(user_id)
            log(f"[RETRY] id {user_id} ({result}) - added to the re-check list, moving on", COLOR_YELLOW)
        elif result == "not_found":
            state["visited_users"].add(user_id)
            log(f"[SKIP] id {user_id} no longer exists (404) - cannot be checked", COLOR_YELLOW)
        else:
            state["visited_users"].add(user_id)
        state["processed"] += 1
        if checkpoint_every and state["processed"] % checkpoint_every == 0:
            save_state()
        time.sleep(delay)

    for uid in members:
        handle(uid)

    while pending:
        remaining = []
        remaining_set = set()
        for uid in pending:
            if uid in state["visited_users"]:
                continue
            update_title(uid)
            result = check_user(uid)
            if result in retry_kinds:
                remaining_set.add(uid)
                remaining.append(uid)
                log(f"[RETRY] id {uid} still {result} - will be checked again", COLOR_YELLOW)
            elif result == "not_found":
                state["visited_users"].add(uid)
            else:
                state["visited_users"].add(uid)
            state["processed"] += 1
            if checkpoint_every and state["processed"] % checkpoint_every == 0:
                save_state()
            time.sleep(delay)
        if remaining:
            log(f"{len(remaining)} users still blocked; taking another pass", COLOR_YELLOW)
        pending = remaining
        pending_set = remaining_set

    save_state()
    set_title(f"Roblox Admin Scraper | Done | Admins: {len(state['admins'])}")
    log(
        f"Finished. Processed {state['processed']}; {len(state['admins'])} admins found.",
        COLOR_GREEN,
    )


def probe_user(user_id):
    badges, _ = fetch_roblox_badges(user_id)
    is_admin = has_admin_badge(badges)
    names = [b.get("name") for b in badges if isinstance(b, dict)]
    username, _ = lookup_username(user_id)
    color = COLOR_GREEN if is_admin else COLOR_NORMAL
    log(
        f"Probe {user_id}: username={username} | Administrator badge={is_admin} "
        f"| {len(badges)} badges: {names}",
        color,
    )


def main():
    global delay, checkpoint_every
    ap = argparse.ArgumentParser(
        description=(
            "Single-threaded, group-only Roblox admin finder. Slowly walks every "
            "member of the seed group(s), checks each profile's Administrator badge "
            "(accountinformation.roblox.com/v1/users/{id}/roblox-badges), writes "
            "admins to admins.txt as '<Name> | <id>' (green [ADMIN] lines). Paced so "
            "rate-limits should not happen; if one does it retries in 2s and re-queues "
            "the id - nobody is skipped. Resumes from state.json."
        )
    )
    ap.add_argument("--start-group", type=int, action="append", default=[], metavar="ID",
                    help="Community id to scan (repeatable). Default: 1200769")
    ap.add_argument("--start-user", type=int, action="append", default=[], metavar="ID",
                    help="Extra user id to scan (repeatable). Default: none")
    ap.add_argument("--delay", type=float, default=2.0, metavar="SEC",
                    help="Seconds between checks (higher = fewer rate-limits). Default: 2.0")
    ap.add_argument("--checkpoint", type=int, default=25, metavar="N",
                    help="Save progress every N users. Default: 25")
    ap.add_argument("--probe", type=int, metavar="USER_ID",
                    help="Check a single user id for the Administrator badge and exit")
    ap.add_argument("--fresh", action="store_true",
                    help="Ignore saved state.json and restart from the seeds")
    args = ap.parse_args()

    if args.probe:
        probe_user(args.probe)
        return

    delay = args.delay
    checkpoint_every = args.checkpoint

    if args.fresh and os.path.exists(STATE_FILE):
        log("--fresh used; previous progress in state.json will be ignored.")
        state["admins"] = {}
        state["known_admin_names"] = set()
        state["visited_users"] = set()
        state["pending"] = []
        state["seed_groups"] = []
        state["seed_users"] = []
        state["processed"] = 0
    else:
        load_state()

    load_existing_admins()
    migrate_admins()

    if args.start_group:
        state["seed_groups"] = list(dict.fromkeys(args.start_group))
    elif not state["seed_groups"]:
        state["seed_groups"] = [1200769]
    if args.start_user:
        state["seed_users"] = list(dict.fromkeys(args.start_user))

    log(
        f"Starting group-only scan: groups={state['seed_groups']}, "
        f"delay={delay}s, {len(state['admins'])} known admins, "
        f"{len(state['visited_users'])} already checked"
    )
    run()


if __name__ == "__main__":
    main()