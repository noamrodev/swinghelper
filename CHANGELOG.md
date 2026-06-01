# Changelog

## 2026-06-01 (Initial-stop / risk basis — R survives a breakeven raise)
- Trades now carry **`initial_stop`** (the stop taken at entry) separate from the editable live
  **`stop`**. Raising the stop to breakeven no longer destroys the trade's R: **all R is measured off
  `initial_stop`** — `position_coach` r_mult, the new unrealized **`r_open`** in `enrich_trades`, and
  **`result_r` on close (now computed server-side**, not trusted from the client). Set at creation,
  the original is captured the first time the stop is edited, and older trades default it to the
  current stop on read. UI shows "(init $X)" beside the live stop, the live R on journal cards, and a
  live R preview in the close modal — all off the initial stop. Fixes MSFT/DOCN (live stop = breakeven)
  which were showing broken/zero R; they now read +1.3R / +1.1R. Unblocks the ±8 win/loss-by-setup
  learning loop for trades managed with a trailed stop.

## 2026-06-01 (Entry grade on trades — grade your own setups)
- Every trade now carries the **system grade of its setup as of the entry date**, derived
  automatically from `taken_at` — no manual entry. `scanner.analyze_at(sym, date)` slices the cached
  daily bars to the entry date, runs `analyze()`, and attaches an RS-outperformance proxy from the
  indexes sliced to the same date; `app.entry_grade_for()` rates it on the **reconstructable price
  factors** (setup quality, entry location, relative strength, liquidity) with market
  regime/sector/news held **neutral** (they can't be time-traveled) — same weights + letter
  thresholds as the live grade (`_grade_letter`, shared now).
- `enrich_trades` adds `entry_grade` / `entry_rating` / `graded_setup` / `low_grade` (<B) to every
  trade. Shown as a colored **grade badge** on dashboard Open Positions and both Journal lists, with a
  dashboard header summary ("your entries avg C (62) · 5 below B"). The engine's own setup read is in
  the tooltip when it differs from what was logged (e.g. DOCN logged AVWAP → engine saw Consolidation/A).
- Purpose: a mirror on the trader's **own** entries (taking C/D setups is a fair, self-sourced lesson),
  separate from the system's picks. *Limitation: price-based only — historical regime/sector/news
  aren't reconstructed, so the entry grade isn't identical to a live grade on the same name.*

## 2026-06-01 (Spinning: 9 EMA + 2-green confirmation)
- **Switched the spin line from the 10 EMA to the 9 EMA** (5-min) to match the chart.
- **Confirmation:** a name now needs **2 closed green candles above the 9 EMA** (recent window) before
  it counts as a spin — a single candle tagging the line no longer qualifies. The higher-lows
  tolerance still applies *after* confirmation, so a confirmed name isn't dropped on one dip under the
  line. Verified live: every returned spin has `green_above ≥ 2` (e.g. NXPI/HOOD confirmed then sitting
  right on the line). UI relabeled 10EMA→9EMA throughout.

## 2026-06-01 (Spinning stocks screener)

### 🔄 Spinning stocks (Screeners → 🔄 Spinning)
- New intraday-reversal screener: **beaten-down stocks starting to rotate back up.** On the 5-min
  chart, a green candle reclaims a turning-up 10 EMA off a real flush — ranked by how much snap-back
  potential is *left* (early reclaim, near the line, buyers stepping in), not how far it's already
  bounced. The ASTS example (flushed ~$113→$101, reclaiming the 10 EMA at ~$103) is the gold standard.
- `scanner.spin_signal()` (5-min detector) + `scanner.scan_spinning()` with a **smart live-quote
  pre-filter**: pulls quotes for the ~800 universe, keeps only names *down on the day* (the beaten-down
  set), then fetches 5-min bars for just those (~50s, ~140 candidates instead of 800).
- **Gates** (tuned on live data): real flush (`drop ≥ max(4%, 0.8×ADR)`), reclaimed the 10 EMA
  (fresh cross or ≤6 bars above), MA turning up + off the low, and not-already-run-away (≤8 bars
  above / ≤2.2% over the line). **Potential score** blends drop depth, freshness, up/down volume on
  the turn, reclaim-candle strength, proximity to the line, and a sweet-spot off-low.
- **Higher-lows fix (less aggressive):** a single 5-min candle dipping a little under the 10 EMA no
  longer drops a name — if it reclaimed recently and is still making **higher lows** (rising 5-min
  structure, `scanner._higher_lows()`), it stays on the list within a small tolerance under the line
  (`min(1.2%, 0.2×ADR)`). Higher-lows structure is now a scored term and shown as a 📈 badge; the
  "vs 10EMA" chip handles the small negative. (e.g. ENPH was kept at −0.11% under the line.)
- **Leader / rising-sector** toggle filters + a ranking boost (+8 RS-leader, +6 rising sector); each
  card shows RS, setup, theme, drop%, off-low, Δ-to-10EMA, volume, news, and a stop idea (intraday
  low). `GET /api/spinning` (+ enrichment/boost) and `POST /api/spinning/scan`; **auto-refreshes
  every ~3 min** while the tab is open during market hours. Verified live (UMAC/LUNR/RKLB/ASTS led a
  space/drone rotation day), clean console, mobile-safe at 375px.

## 2026-06-01 (perf + groups polish)

### Performance (no behavior change)
- **Scans ~10× faster.** The scan slept `0.05s` per ticker unconditionally (~40s of pure sleep over
  800 names) even when every bar was cached. `get_bars` now flags whether it hit the network
  (`_DID_FETCH`); the scan only throttles on actual fetches. **Cached full scan: ~44s → ~4s** — makes
  "New day", the 30-min auto-rescan, and group detection near-instant.
- **Lighter DOM.** The Suggestions grid rendered all ~700 cards (a ~7MB DOM that slowed rendering and
  the live merge). Now caps at the **top 120** by rating (accurate "showing top 120 of N · show all"),
  so the page and live updates are snappier.
- Backed up the full project to `backups/<timestamp>/` before the pass.

### Detect-new-groups polish
- Each group now **names its common thread** (preferred: the specific theme; fallback: a broad sector),
  and groups with **no shared thread** (no tag shared by a majority of members) are **hidden** — no more
  "Emerging / cross-sector" blobs. 🆕 marks a thread that isn't one of the 38 fixed themes. *(Note:
  `sectors.json` is sparse, so some real clusters can't be named and are conservatively hidden.)*

## 2026-06-01 (latest) — Detect new groups, live pre-market, P&L baseline

### #6 Detect new groups (Screeners → 🧭 New groups)
- `scanner.detect_groups()` finds **emerging groups**: takes the recent strong movers, z-scores their
  last 15 daily returns, links any pair with correlation ≥ 0.86, and returns the connected components
  (size 3–20). `GET /api/groups` + `POST /api/groups/detect` (background job); app tags each group's
  dominant sector and flags 🆕 when it spans multiple/none of the 38 fixed themes (a genuinely new
  group). New Screeners tab renders each cluster's members + avg 1-week move. (Found e.g. TEAM/NOW/
  WDAY/IOT and QBTS/RGTI/INFQ moving together.)

### #5 phase 2 — live pre-market movers
- On the Pre-market tab during the PRE session: listed movers' price/gap update live from quotes
  (pre-market overlay), and a full re-scan auto-fires every ~8 min to catch new gappers. Green
  "live · auto-updating" badge.

### Today's P&L baseline fix
- Positions **entered today** now use your **entry** as the day baseline (you only owned it from the
  fill), not yesterday's close — so a name you bought today shows only the gain since your fill.
  (MP bought today: +$20, not the phantom +$127.)

## 2026-06-01 (late) — Live updates + backtest validation

### Live updates during market hours (roadmap #5, v1)
- **Batched live quotes** — `scanner.fetch_quotes()` pulls Yahoo `v7/quote` (cookie+crumb), 50 symbols
  per call, 30s shared cache, pre/post-market price overlay. Degrades gracefully.
- **`GET /api/live?symbols=`** — returns live prices + `market_state` + a **live blended posture**
  (regime recomputed with live index prices).
- **Frontend live poller** — every ~45s while the market's open (5min when closed), gated by market
  state. Merges live price into: open positions (live P&L/R + **hit-target / at-stop / now-under-9-EMA**
  flags), suggestions (live price → live "buyable now" / stopped), and the market-regime card. A
  **"● LIVE · OPEN · 8s"** header badge shows state + age with a pause toggle.
- Scope: positions + suggestions + regime. Phase 2 (queued): live Sector Heat + pre-market.

### Live refinements (same day)
- **Live coach** — the position action now recomputes from the LIVE price (was stale EOD → e.g. CRWV
  showed +5% *and* "EXIT below stop"). Intraday under the 9-EMA is now **WATCH** ("exit only if it
  CLOSES under it"), not a hard EXIT.
- **Setup-aware exits** — Deep Pullback / Consolidation are bought at the 50-EMA / in a base, so they
  sit under the 9-EMA by design. Those setups now trail the **50-EMA** (not the 9), and a fresh entry
  (≤2 sessions) holds to its **stop** instead of getting shaken out. Coach carries `e50`/`patient`/`young`;
  patient match is case-insensitive.
- **Live re-rank** — suggestions that pull into their buy zone float to the top as prices move.
- **Auto-rescan** — optional (default on), every 30 min while the market's open, with **fresh bars**
  (`scan ?fresh=1` → `max_age=0`) so new setups/grades appear intraday. Toggle on the Suggestions tab.
- **Today's P&L** tile on the dashboard (live mark-to-market vs prior close).
- Server now sends `Cache-Control: no-cache` on the app shell/scripts so a rebuild never looks
  "stuck" behind a stale cached `app.js`.

### Live coach correctness (round 2)
- **"Armed" trailing exit** — the 9/50-EMA close-exit only applies once the position has *closed
  above* its line since entry. Buying a dip BELOW the line is no longer treated as an exit; until it
  reclaims the line, only the hard stop exits. (Generalizes the deep-pullback case to every setup.)
- **Breakeven/raised stops fixed** — a stop at/above entry used to make risk ≈ 0, which broke the
  coach (it fell back to a stale "EXIT below stop"). R is now recovered from the 2R target, and a
  breakeven+ stop reads as a *locked-in* exit, not a panic. (Your CRWV: now HOLD, not EXIT.)
- **Live no longer reverts on save** — `loadTrades()`/`loadSuggestions()` re-apply the live merge,
  so saving a journal entry / editing a stop doesn't flip the page back to stale EOD data for ~45s.
- **Live charts** — the chart modal now fetches fresh bars (today's forming candle shows, `/chart`
  uses a 15-min cache) and the last candle **moves with the live price** each tick while open.
- **Live Sector Heat** — `GET /api/sector-heat/live` recomputes each sector's & member's TODAY %
  and the heat score/rank from live quotes (multi-day trend/streak kept from the EOD compute).
  Polls ~60s while you're on the Sector Heat tab; green "live" badge shows the time. Read-only —
  never overwrites the stored EOD heat.

### Backtest validation of the grader
- Built `backtest.py` (replay grade as-of past dates → simulate forward with real exits). Found the
  A/A+ grade is positive but context-dependent; shipped six tuning changes (regime gate, Rising>Hot,
  PREF, anti-chase, wider stop, trim dry-vol) → `scoring.md` v4.2.
- **Out-of-sample check** (fresh dates): edge holds but in-sample was ~3× optimistic (≈+0.26R/36%
  generalizable). **Forward/paper test** wired (`/api/forward`, Stats tab) — logs live A/A+ daily,
  scores as they mature; true OOS, no survivorship bias.
- Hosted: hid Journal / Watchlist / Strategy (no persistence on the free host) via `/api/env`.

## 2026-06-01 — Earnings, volume, position coach, daily gameplan, prediction

Built roadmap items 4 → 3 → 2 → 1 → 7 in one session.

### Earnings dates (new data source)
- `scanner.get_earnings()` pulls the next earnings date from Yahoo `quoteSummary/calendarEvents`
  via a cached cookie+crumb session. Disk-cached daily; degrades to `None` on any failure so a Yahoo
  change can never break the scan. Returns `{date, ts, estimate}` (`estimate` = Yahoo's est. flag).

### #4 — Upcoming-earnings warning in Suggestions
- The scan fetches earnings for the top ~70 names; `grade_suggestions()` computes
  `earnings_days`/`earnings_soon`/`earnings_near` and **demotes the grade −18 (≤7d) / −6 (8–14d)**.
- Suggestion cards show a 🗓 earnings badge (red ≤7d, amber ≤14d) + a ⚠️ "probably skip" banner.

### #3 — Earnings + volume on charts, volume into the setup score
- Chart modal: a **volume pane** (green/red histogram, Vol toggle) + a next-earnings chip in the header.
- `scanner.analyze` adds a **volume-character signal** (`±2` on the raw score): rising/heavy down-day
  volume on a pullback = distribution (flag, penalize); drying volume = healthy; rising up-day volume
  on an advance = accumulation (favor); thin advance = penalize. Shown in the "why" line. Rubric v4.

### #2 — Suggested position actions (Dashboard)
- `position_coach()` rates every open position: **EXIT** (closed under the 9-EMA / below stop),
  **TRIM** (extended +3R or earnings imminent with profit), **RAISE STOP** (+1R → breakeven),
  **WATCH** (bad news / earnings with no cushion), **HOLD**, plus an optional **ADD** note. Each open
  position now shows an action pill + the reasons (profit R, extension vs 9-EMA in ADR units, earnings days).

### #1 — Daily Gameplan (Dashboard, top card)
- `GET /api/gameplan` synthesizes regime, positions + their actions, exposure (invested % / free cash /
  open risk %), buyable A/A+ setups, earnings/news avoids, and top lessons into one prioritized plan
  with an honest bottom line — "do nothing today" is a valid plan.

### #7 — Prediction (News → 🔮 Prediction tab)
- `GET /api/prediction` blends market regime, sector rising/slowing/falling, breadth, news tone,
  end-of-day buy/sell footprint and pre-market skew into a **lean** (Bullish → Risk-off) + confidence
  + a driver list. Framed as probabilistic, not advice.

### Distribution-day fix + two new Suggestions filters (same day)
- **Distribution / climax-reversal day** (`scanner.analyze`): a heavy-volume rejection off a recent
  high (or the "distribution" volume signal) now sets `distribution_today`, **forces
  `buyable_now=false`**, and **caps the composite grade at C** in `grade_suggestions` — overriding RS
  and a hot sector. Fixes the case where ASTS graded A "buyable now" the same scan it flagged "pullback
  may not be over." A "let it settle, don't buy the drop" banner shows on the card. (Extended >2.2×
  ADR names are no longer flagged buyable-now either.) Verified: ASTS A→**C**, top 8 now clean.
- **Two Suggestions filters** next to ⏳ Worth waiting: **🏆 Market leaders** (RS percentile ≥ 85, ~top
  15%) and **🚀 Rising sector** (`theme_trend == Rising`). Verified counts: 124 / 129 of 796.
- Re-scanned the full 800-name universe so earnings dates + the new flags are live (70 earnings dates,
  3 earnings-soon demoted, 175 distribution-day flags). Rubric → `scoring.md` v4.1.

### Internals
- Extracted the suggestion grading out of the HTTP handler into a reusable `grade_suggestions()`
  module function (used by both `/suggestions` and `/gameplan`). Added `days_until()` + `datetime` import.
- Verified live: gameplan, coach, prediction, volume pane, earnings chip; clean console; no 375px overflow.

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
