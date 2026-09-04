# Roblox Admin Scraper

A single-threaded, rate-limit-friendly scraper that walks every member of a Roblox
group and finds accounts that hold the **Administrator** badge.

Admins are written to `admins.txt` as `Username | UserId`, one per line, and are
highlighted **green** in the console.

## How it detects admins

Each member's profile is checked against the real badge source:

```
GET https://accountinformation.roblox.com/v1/users/{id}/roblox-badges
```

A user is treated as an administrator if they hold badge `id: 1` named
`Administrator` (a handful of legacy badges are also accepted). This is the same
source the profile page's badge comes from — no guessing.

## Design goals

- **Group-only.** It scans the seed group's members and nothing else — no friends
  lists, no admin-group expansion.
- **Rate-limit free.** Requests are paced (`--delay`, default 2s) so 429s
  should never happen. If a 429 does happen, it retries after a flat **2 seconds**
  (the wait never grows).
- **Never skips anyone.** Rate-limited, blocked, or timed-out IDs are moved to a
  re-check list; after the main sweep the list is re-passed until it's empty. Only
  deleted users (404) are passed over.
- **Resumable.** Progress is checkpointed to `state.json` every N checks
  (`--checkpoint`, default 25) and picked up on the next run. `--fresh` restarts.
- **Live console title.** The window title shows admins found, users checked,
  pending retries, and the current ID being checked.
- **Real-time GitHub sync.** When run inside a clone of this repository, every
  newly found admin is auto-committed and pushed to GitHub within ~5 seconds
  (commit message includes the admin's name). Disabled automatically when no git
  repo is present.

## Requirements

- Python 3.8+
- `pip install -r requirements.txt` (only `requests`)

## Usage

```
python scraper.py                       # scan group 1200769 (Official Group of Roblox)
python scraper.py --start-group 7384468 # scan a different community (repeatable)
python scraper.py --delay 3.0           # slow it down further to avoid rate-limits
python scraper.py --probe 1             # check a single user and exit
python scraper.py --fresh               # ignore saved progress and restart
```

Or double-click `runme.bat`.

## Output

`admins.txt` is appended with each confirmed admin:

```
Roblox | 1
RobloxArenaEvents | 1861517257
```

Names already in `admins.txt` are never duplicated.

## Files

- `scraper.py` — the scraper.
- `admins.txt` — confirmed admins (`Name | Id`), generated.
- `requirements.txt` — dependencies.
- `runme.bat` — Windows launcher for the scraper.
- `state.json` — runtime resume state (not tracked in git).