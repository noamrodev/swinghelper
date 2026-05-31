# Deploying the Trading Data Center for friends

This puts the app online at one URL. Each friend's browser silently gets its **own
private workspace** (settings, journal, watchlist, approve/reject marks). The heavy
market data — the graded suggestions, universe, sector heat, news, regime, and price
cache — is **shared** and refreshed automatically once a day, so nobody waits on a scan.

> **Your local copy is untouched.** Run `run.ps1` as always — with `HOSTED` unset the app
> behaves exactly as before, reading/writing your real `data/*.json`. A full backup was
> taken at `..\claude-BACKUP-2026-05-31\`.

---

## What ships vs. what stays private

`.gitignore` / `.dockerignore` keep your personal files OFF the server:
- **Never shipped:** `data/settings.json`, `data/trades.json`, `data/watchlist.json`,
  `data/suggestions.json`, `data/status.json`, `data/uploads/`, `data/users/`, `data/cache/`.
- **Shipped (shared, not personal):** `data/screeners.json`, `data/universe.json`,
  `data/themes.json`, `data/sectors.json` — these define the universe the scanner runs on.

Friends get a blank workspace from `data/template/` on first visit.

---

## Modes (environment variables)

| Var        | Local (you)        | Hosted (friends)            |
|------------|--------------------|-----------------------------|
| `HOSTED`   | unset              | `1`                         |
| `PORT`     | 8765 (default)     | set by the host             |
| `DATA_DIR` | unset (→ `data/`)  | the persistent disk (`/data`)|

`HOSTED=1` → per-browser workspaces, binds `0.0.0.0`, no auto-open browser, starts the
daily shared-refresh loop, and makes the strategy docs + screeners read-only for friends.

---

## Easiest from the GitHub website: Render (no CLI)

This repo includes `render.yaml`, so Render sets everything up for you:

1. Push this repo to GitHub (`noamrodev/swinghelper`).
2. [render.com](https://render.com) → **New ➜ Blueprint** → connect the repo.
3. Render reads `render.yaml`: a Docker web service with `HOSTED=1`, `DATA_DIR=/data`, and a
   1 GB persistent disk. Confirm and create.
4. When the build finishes, open the `https://swinghelper.onrender.com`-style URL. First boot
   runs a cold scan (~5–9 min) to warm the cache; after that it's instant for everyone.
5. **Share that URL with your friends** — each gets a private workspace automatically.

**Updating everyone:** `git push` → `autoDeploy` redeploys, persistent disk keeps journals.
Needs the **Starter** plan (~$7/mo); the free plan has no disk and sleeps (journals would vanish).

---

## Alternative: Fly.io (CLI, cheap, persistent disk, free HTTPS)

1. Install flyctl and sign in: `fly auth login`
2. From this folder: `fly launch --no-deploy`
   - Pick a unique app name + a region. Decline Postgres/Redis.
   - It may rewrite `fly.toml`; make sure it keeps the `[env]`, `[http_service]`, and
     `[mounts]` sections shown in the committed `fly.toml`.
3. Create the persistent volume (same region you chose):
   `fly volumes create tdc_data --size 1 --region iad`
4. Deploy: `fly deploy`
5. Open the URL fly prints (e.g. `https://your-app.fly.dev`). First boot runs a cold scan
   (~5–9 min) to build the shared cache; after that it's warm and instant for everyone.
6. **Share that URL with your 2–5 friends.** Each gets their own private workspace
   automatically — no login.

### Updating everyone
Make changes locally, then `fly deploy`. One command updates all friends. The persistent
volume means their journals/settings survive the redeploy.

---

## Test it locally before deploying (Docker)

```powershell
docker build -t tdc .
docker run --rm -p 8080:8080 -e PORT=8080 -e HOSTED=1 -v ${PWD}\_voldata:/data -e DATA_DIR=/data tdc
```
Open http://localhost:8080 in two different browsers (or one normal + one incognito) and
confirm each has a separate, empty journal. The `_voldata` folder mimics the persistent disk.

---

## Honest limits (trusted friends, not the public)

- **No login.** Privacy = an unguessable workspace id stored in each browser. Fine for a
  handful of trusted friends; do **not** post the URL publicly (anyone could spin up
  workspaces and trigger scans against Yahoo). For public use you'd add real accounts.
- If a friend clears their browser storage they get a fresh (empty) workspace; their old
  one still exists on the server under its id but is no longer linked to their browser.
- Other hosts (Render/Railway/a small VPS) work too — just set the same env vars and mount
  a persistent disk at `DATA_DIR`. Avoid free tiers that sleep and wipe disk (journals vanish).
