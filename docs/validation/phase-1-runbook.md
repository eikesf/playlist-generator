# Phase 1 — Live validation runbook (owner, on the Mac)

Phase 1 was built and tested offline (CI, fake Apple API). These steps check it on the real Mac and account. Every step is read-only for Apple Music, except the optional `doctor --write`, which creates and deletes one test playlist, as before.

Paste outputs back to Claude after masking anything that looks like a token. The commands never print tokens.

## 1. Update

```bash
cd ~/Personal_DEV_Projects/playlist-generator
git checkout develop && git pull
uv tool install --force --reinstall --no-cache .
```

## 2. Keychain and status

```bash
apple-music-mcp status
```

Expected:
- `stored_in` shows `macOS Keychain (apple-music-mcp) + …/credentials.json`.
- `media_user_token` is masked, not `<missing>`.
- macOS may ask once to allow Keychain access. Choose **Always Allow**.

Check that the file no longer holds the token (prints only True/False, never the value):

```bash
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.config/apple-music-mcp/credentials.json')));print('token in file:', bool(d.get('media_user_token')))"
```

Expected: `token in file: False`.

## 3. Login expiry (optional, recommended)

Running `login` again is the way to record the cookie's real expiry date. Safari shows it in the *Expires* column next to `media-user-token`. Until then, the expiry is estimated as 180 days after the next login, or reported as `null` in `status`.

```bash
apple-music-mcp login      # paste the token (hidden), then type the expiry date, e.g. 2027-05-04
```

## 4. Configuration file

```bash
cp .env.example ~/.config/apple-music-mcp/.env
open -e ~/.config/apple-music-mcp/.env
```

Fill in:
- **`NTFY_TOPIC`**: install the ntfy app on the iPhone and subscribe to a long random topic. Generate one with `python3 -c "import secrets;print('amm-'+secrets.token_urlsafe(16))"`.
- **`HEALTHCHECK_URL`**: create a free healthchecks.io account and a check with a 10-minute period and 15 minutes of grace. Copy its ping URL.
- **`HEALTHCHECKS_API_KEY`** (optional): a read-write key from the healthchecks.io project settings. Only `pause` uses it.
- **`BACKUP_DIR`**: leave empty to use `iCloud Drive/apple-music-mcp-backups`.

## 5. Run each job once

```bash
apple-music-mcp run doctor
apple-music-mcp run snapshot
apple-music-mcp run recent_played
apple-music-mcp run backup
apple-music-mcp run heartbeat
```

Expected:
- **doctor**: `ok` or `warn`, plus an ntfy alert only if something failed. The AppleScript line now ends with `persistent IDs: yes`.
- **snapshot**: `rows_written` equals the number of library tracks the first time, and about 0 if run again.
- **recent_played**: `items_returned` around 50 and `new_items` around 50 the first time.
- **backup**: a `.db.gz` file in the iCloud folder.
- **heartbeat**: `pinged: True`, and the healthchecks.io check turns green.

## 6. Schedule

```bash
apple-music-mcp schedule install
launchctl list | grep apple-music-mcp
```

Expected: 5 jobs. Logs are in `~/Library/Logs/apple-music-mcp/`.

## 7. Pause mode

```bash
apple-music-mcp pause --until 2026-11-07
apple-music-mcp run snapshot          # expected: skipped_paused
apple-music-mcp resume
```

With `HEALTHCHECKS_API_KEY`, the check shows "paused" on healthchecks.io after `pause` and resumes after `resume`. This API call has not been verified live yet.

## 8. Alerts

Quit Claude Desktop (⌘Q) and run `apple-music-mcp run heartbeat`. healthchecks.io should show a failure with "Claude Desktop is not running". Reopen Claude Desktop afterwards.

## Exit criteria

All jobs ran once, with results recorded in the validation log (`docs/audit.md` §7), and `schedule install` loaded 5 agents. Open questions are listed in the Phase 1 summary.
