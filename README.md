# SwingHelper — momentum swing-trading Data Center

A self-hosted web app for Qullamaggie/Martin-Luk style momentum swing trading: graded
A+→D setup suggestions across ~800 liquid US stocks, market regime, sector heat, news +
suspicious-activity scans, charts, a position calculator, and a trade journal with stats.

Pure Python standard library (no dependencies) + a single-page front-end. Free data
sources (Yahoo Finance, Google News) — no API keys.

## Two ways to run

**Locally (single user):**
```powershell
python app.py        # or run.ps1 on Windows
```
Opens http://localhost:8765. All data lives in `data/`.

**Hosted (share with friends — each gets a private workspace):**
Set `HOSTED=1`. Each browser silently gets its own isolated workspace (settings, journal,
watchlist, approve/reject marks) keyed by a random id in `localStorage` — no login. The
heavy market data (suggestions, universe, sector heat, news, regime, price cache) is
**shared** and auto-refreshed daily, so nobody waits on a scan.

👉 **See [DEPLOY.md](DEPLOY.md) for one-click-ish deploy steps** (Render from GitHub, or Fly.io).

## What's in the repo

| Path | What |
|------|------|
| `app.py` | Web server + JSON API (stdlib `http.server`) |
| `scanner.py` | Setup engine — fetch, indicators, grading |
| `universe.py` | Builds the ~800-name tradeable US universe |
| `web/` | Single-page UI (Alpine + Tailwind + Lightweight Charts, all CDN) |
| `strategy/` | The playbooks (Qullamaggie, Martin-Luk, my-rules, scoring rubric) |
| `data/` | Shared seed data (screeners/universe/themes) + a blank workspace `template/` |
| `Dockerfile`, `fly.toml` | Container + Fly.io config |

Personal data (account size, trades, watchlist, uploads, per-user workspaces, price
cache) is git-ignored and never committed — see `.gitignore`.

## Honest limits

No login: privacy is an unguessable per-browser id, which is right for a handful of
trusted friends, not for posting the URL publicly. **Not financial advice.**
