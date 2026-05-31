# Changelog

## 2026-05-31 (evening) — Hosted launch, pre-market movers, mobile

### Hosting (share with friends)
- **Multi-user "hosted" mode** (`HOSTED=1`): each browser gets its own private workspace
  (`data/users/<id>/`, keyed by an `X-Workspace` id in localStorage — no login). Market-wide data
  (suggestions, screeners, universe, sector heat, news, regime, price cache) is **shared**.
- Local mode (`HOSTED` unset) is unchanged — single-user, all data in `data/`.
- `DATA_DIR` env points data at a mounted disk so journals survive redeploys; shared seed files are
  copied in on first boot. Binds `0.0.0.0`, reads `$PORT`, runs a daily shared-refresh thread.
- Deployable repo `swinghelper/` (Dockerfile, `render.yaml`, `fly.toml`); deployed on Render.
  One-command publish via `make-build.ps1` ("make a new build").

### Pre-market movers (new)
- New **🌅 Pre-market** sub-tab under Screeners. Scans the universe for the biggest pre-market gaps
  vs prior close (Yahoo pre/post bars). Each card shows gap %, price, prior close, sector (🔥 if hot)
  + trend, setup type + RS percentile (from the daily scan), a linked news catalyst (sentiment-colored),
  and pre-market volume. Tap a card for the chart.

### Sector Heat
- **Find-a-stock search** — type a ticker (e.g. CRCL) to see its group, auto-expanded + highlighted
  (authoritative lookup via `themes.json`, exposed at `/api/themes`).
- **Click column headers to sort** (Today/Week/Month/6mo/Streak/Heat); removed the top-right buttons.

### News / Suspicious / Suggestions
- News sorted **newest → oldest** (News tab + dashboard).
- Suspicious activity **sort control** (Volume / Move / Strength), default Volume.
- Suggestions sector filter lists **all sector-heat categories** (not just those in the current scan).

### Charts
- **EMA** and **AVWAP** toggle buttons (on by default) alongside Log / Channel.

### Mobile / responsive
- Full mobile pass: off-canvas **drawer nav + hamburger**, display-zoom auto-capped on phones,
  responsive inputs/modals/grids, and **horizontal scroll on wide data tables** (sector heat,
  suspicious) so columns never smush or get cut off. Standing rule: every change must be
  mobile-friendly (see `PROJECT.md`).

---

## 2026-05-31 — Big upgrade day

A full day of work turning the Data Center from "scans a hand-pasted list of 193 names with a
simple grade" into "scans the whole liquid US market with a context-aware grade, market regime,
sector heat across 38 themes, and proper momentum filters." Here's everything, in plain English.

---

### 🧠 Smarter suggestion grades

- **Wrote down how grading actually works** → [strategy/scoring.md](strategy/scoring.md). It used to
  live only in code; now there's a plain-English rubric we edit first and keep the code in sync with.
- **Relative strength** is now a real factor (15% → 14%): each name's 1M/3M return vs the market, as
  both a percentile-in-universe and outperformance-vs-index. Leaders float to the top.
- **Entry location** is now graded, not pass/fail: buying stretched far above the 10-EMA, or with a
  stop forced near a full 1× ADR, drags the grade down. This is the "don't chase" dimension.
- **Liquidity** added (8%): average daily dollar volume on a log scale. No liquidity = no
  institutions. Shown as a 💧$X/d chip on every card.
- Final weights: setup 28 · RS 14 · regime 14 · entry 14 · liquidity 8 · sector 10 · timing 6 ·
  news 6, plus the ±8 nudge from your own realized trades once you've logged ~5+.

### 📊 Market context (new)

- **Market regime panel** on the dashboard — SPX / QQQ / IWM each classified (Healthy / Recovery /
  Extended / Pullback / Mid- or Deep-correction) with a blended 0–100 "posture" that feeds 15% of
  every grade. Breakouts get demoted harder than pullbacks when the tape is weak.
- **Distance-from-the-50-MA gauge** (your "ATR multiple from 50-MA" idea) on each index — a
  correction-risk meter that flags ⚠ when an index is stretched far above its 50, with a graded
  posture haircut. Also added the **20-EMA** distance alongside it.

### 🎯 Better stops & leaders

- **Reclaimed-swing-high stops** (your NVDA example): the engine now picks the tighter *valid*
  structural stop between the 5-day low and a reclaimed prior swing high (reported "close below").
- **Leader-in-group medal** 🥇 — within each sector, the strongest name gets crowned, ranked by
  **relative strength + liquidity** so a thin stock spiking on low volume doesn't get called the
  leader.

### 🌐 The whole US market, not a pasted list

- **New universe builder** (`universe.py`) — pulls every US-listed stock (~5,100, NASDAQ/NYSE/AMEX),
  filters to your Market Leaders criteria (price > $10, mkt cap > $300M, $vol > $10M), and keeps the
  **top 800 by liquidity**. Fully keyless (NASDAQ symbol directory + Yahoo crumb-authed quotes).
- The old hand-pasted 193-ticker list is gone; "Market Leaders" now auto-populates the real universe.
- **🌐 Rebuild universe** button on the dashboard does this on demand. It does **not** run on "New
  day" — that only refreshes the existing names.

### 📈 Momentum screens (your 1M / 3M / 6M)

- Recreated your three TradingView momentum screens as **filter buttons** on Suggestions.
- To make them real, the price-history fetch was extended from 8 months to **1 year** (so the
  200-MA and 52-week-high are computable; also sharpens the AVWAP-from-ATH).
- **Made them exclusive cohorts** so they actually find *new* movers: **🔥 New (1M)** shows only
  fresh breakouts up >20% this month that aren't already an established 3M/6M trend — it no longer
  shows the old leaders that happen to be up this month.

### 🗂️ Sector Heat — 38 themes now

- Read your carefully-built sector taxonomy and **added 104 momentum-relevant names** from the new
  universe, completing your existing groups (Memory, Photonics, Semi Testing, Precious Metals, …).
- **11 new themes**: Nuclear, Power Producers, Cybersecurity, AI Software/Apps, AI
  Networking/Hardware, Analog/Power Semi, Compute (CPU/GPU), Foundry, Steel, Copper, LNG/Nat Gas.
- Left all the broad-market names (banks, REITs, staples, healthcare, transports) **out on purpose**
  — that's how you built it.
- Ran the heat: 38 sectors now rank by momentum (AI Networking/Hardware came out hottest).

### 🔎 Filters on Suggestions

- **Sector dropdown** (all 38 themes, ordered hottest-first) — e.g. "show me only Nuclear setups."
- **Live match count** + a **✕ clear filters** button.
- All filters — setup type, momentum cohort, sector, status — **stack together** (AND), so you can do
  "new movers in Quantum that are pullbacks" in a few clicks.

### 🛠️ Fixes & docs

- **"New day" no longer rebuilds the universe** — it just refreshes regime, setups/ratings, sector
  heat, and news for the names you already track (4 stages). Rebuilding the 800 is manual only.
- **Front-end style guide** written → [web/STYLE.md](web/STYLE.md), so the dark/glassy look stays
  consistent across sessions.
- Kept [PROJECT.md](PROJECT.md) and [strategy/scoring.md](strategy/scoring.md) up to date with all of
  the above.

### 🎣 Catching strong leaders on the dip (AXTI/VRT fix)

- **New "Deep Pullback" setup** — a strong leader (big run, still above the 200-MA) that's pulled
  back *below* its short EMAs toward the 50 EMA used to get mis-classified as a near-ATH breakout
  (dumb). Now it's caught at the **50 EMA / recent support with a tight stop below the swing** —
  e.g. AXTI now reads entry $100 / stop $93.70 instead of "buy near the $150 ATH," VRT at its 50 EMA.
- On parabolic names the 50 EMA lags far below price, so the entry is **floored at the recent
  pullback low** (where it's actually finding support), not 14% lower.
- **"⏳ Worth waiting" tag + filter** — the patient at-support setups: a strong leader correcting
  *deep* into its 50 EMA (VRT/AXTI/LITE), **or** a strong stock **consolidating** sideways in a tight
  base on the 50 EMA (the SNDK base — buy the dip to the 50 while it waits). NOT shallow pullbacks
  near the highs that are still moving up. Filter button + card badge.
- **Buyable-now buffer** — a name sitting just above its buy zone (IREN at $63.54 vs a $63.02 top)
  now reads "buyable now" instead of "wait" (~0.3× ADR tolerance).
- The stock's **own strength carries the grade** even when its sector is cooling.

### 📐 Charts: log scale + trend channel

- **Logarithmic price scale, ON by default** (with a Log toggle) — essential for $3→$155 names.
- **Trend channel ("tunnel")** — an auto-fit linear-regression channel (fit on log price so it's a
  straight band on the log chart), drawn with a 📐 Channel toggle.

### 🕵️ Suspicious activity (insider-style EOD footprints)

- New **Suspicious Activity** tab under News (📰 News / 🕵️ Suspicious), split **🟢 buying / 🔴 selling**.
- Scans the universe's **intraday 5-min bars including pre/post-market** for an end-of-day or
  **after-hours volume spike (≥4× the day's average 5-min volume) paired with a directional move** —
  the footprint of aggressive buying/selling into the close or after hours.
- Validated on the examples: **CIEN +4.2% on 9.4× vol**, **USAR +3.2% on 7.9×** (buying), **NVDA −1.9%
  on 10.2×**, **INTC −2.3% on 8.7×** (selling) — while boring names (AAPL/KO/PG) correctly flag nothing.
- On-demand **"🔍 Scan now"** button (full 800-name universe, ~8–10 min — your choice); doesn't slow
  down New day. Each row links to the chart.

### ⚙️ Under the hood

- New data files: `data/market.json` (regime), `data/universe.json` (last build).
- New API routes: `/api/market`, `/api/universe` (+ `/api/universe/build`).
- Yahoo data is still keyless; the one new trick is a cookie+crumb session for batch quotes
  (`universe._yahoo_session()`), needed because Yahoo now 401s the plain quote endpoint.
