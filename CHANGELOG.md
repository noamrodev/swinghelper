# Changelog

## 2026-06-12 (night) — ran-up ⇒ prefer pullback over breakout + high-ADR near-support cap (LITE/TSEM)

**Trigger:** friends site showed "BUY TSEM A+ Pullback @ AVWAP" (confirmed $266.45, +2.5% above the AVWAP) and
armed "LITE A+ Consolidation — break $932" — both **high-ADR chases that ESCAPED the 0.5× ADR day-run gate**
(LITE +3.0%/9.7% ADR = 0.3× ADR; TSEM +3.1%/8.3% ADR). (Also: the friends site was the OLD build — chase gate +
today's grades aren't live there until the trader pushes; the A+ is stale grading.) Trader: "when a stock already
ran up today, don't force a breakout step — better to arm a pullback (only if good)."

**Feature A — ran-up ⇒ prefer the PULLBACK leg (decision: ≥0.3× ADR).** In `compute_now`'s cands loop: if a name
ran ≥0.3× ADR off yesterday's close AND its setup_type is **Breakout/Consolidation** (the types whose plain
ORH/resistance confirm FORCES a breakout entry regardless of leg — keying on `best.kind` first missed a
Consolidation whose best leg was already the pullback, the LITE bug), switch to the best **pullback** leg and arm
it **WATCH-ONLY** at the support ("ran +X% today — not chasing the breakout; wait for the pullback to $Y and the
reclaim"); if there's **no good pullback leg → DROP it** (no chase, no fabricated pullback). EP exempt. Silent
(armed, never beeps). LITE → watch at $867 (the 50-EMA); AMKR (+7.8%) → watch at $79.23.

**Feature B — 2.5% absolute backstop on the near-support caps (high-ADR).** The 0.5× ADR near-AVWAP / zone-drift
caps are too loose at 8-10% ADR. Now `min(0.5× adr_px, 2.5%)` — only bites names >5% ADR. TSEM reclaim $266.45
(raw_risk 7.69 > 2.5%=$6.66) now **stays armed** for a closer retest; low-ADR reclaims unchanged.

**Build/verify:** quant built both, Burry verified PASS (grades byte-for-byte — scanner SHA256 identical, golden
intact; the hard invariant holds — a ran-up name never arms/fires a breakout; watch-only is silent; Feature B
crossover exactly 5% ADR). **Caught a live-scope bug Burry's replica-tests missed:** quant used a bare `setup_type`
in the cands loop where it's undefined → `UnboundLocalError` crashed every `compute_now` call (/api/now, /api/autopilot
returned closed-connection). Fixed (read `s.get("setup_type")`), restarted, **/api/now serves clean**. Tests:
178 → **192 passing** (+13 ran-up, +1 INOD backstop). Restart done; NOT rebuilt/pushed yet.

## 2026-06-12 (evening) — GLOBAL day-run chase gate + suggestions.json recovery + EARLY test coverage

**1) Data-integrity fix (urgent — the app was BLIND).** `data/suggestions.json` was corrupted: a non-atomic,
non-truncating scan write left one complete JSON document + 631 bytes of leftover tail from a previous longer
write (two scan threads racing — boot scan vs intraday rescan). `read_json` silently returned `{}` → the live
engine saw 0 setups → empty buys/armed. Recovered the first valid document (794 items), backed the corrupt copy
to `backups/2026-06-12_suggestions-corrupt/`, rewrote atomically. **ROOT BUG STILL OPEN:** the scan writer isn't
atomic / isn't serialized — spawned a task to fix `run_scan`'s write (temp-file + `os.replace`, and/or a scan
lock) so it can't re-corrupt.

**2) GLOBAL day-run chase gate (the CIFR case).** The app fired a "🟢 BUY CIFR · A — Pullback @ AVWAP" at $24.95
near the high of a **+10.5% vertical candle** (ADR 10%). The setup PLAN was fine (AVWAP $24.06, tight stop) but
the live confirm fired 3.7% above the AVWAP after the day already ran ~1× ADR — a chase the ADR-scaled near-zone
caps were too loose to catch on a high-ADR name (the open GLXY caveat). Trader's rule: **"if the stock already
rose more than half its daily ATR, it's a chase."** Built (quant) + verified (Burry): `_day_run_is_chase(setup,
cur_px, prior_close, adr)` → a confirmation is a chase when price has risen **>0.5× ADR above YESTERDAY's close**.
Decisions: reference = **prior close** (full daily move), **all setups except Episodic Pivot**, **frozen standing
calls exempt** (gate runs BEFORE the freeze so a chase is NEVER frozen). Wired into all 4 fire paths (deep-pullback
50-reclaim incl. the wall-clear `cand_entry` re-check, AVWAP Path A/B, plain breakout/ORH). Added `prior_close` to
`scanner.analyze` (last settled daily close, no-lookahead). Firewalled from grades (Burry independently re-diffed
the re-baselined `golden_analyze.json`: only `prior_close` added, every grade/score/level byte-for-byte). Live-
verified after restart+rescan: CIFR (+10.5% > 5.0%) now **demotes to armed**; down/flat-day deep pullbacks
(RDW/AAOI/RKLB/LUNR/QBTS) still fire. Tests: `tests/test_day_run_chase.py` (8).

**3) EARLY tier — proven + locked (had ZERO tests).** Trader: "didn't catch any EARLY confirmation today, make sure
it works." Code review: the EARLY path (Deep-Pullback 50-reclaim under an unbroken in-band wall, tight ≤1× ADR stop)
is intact; my chase-gate edits only block it on a genuine chase (correct). It's a narrow confluence, so "none today"
is plausibly just "nothing qualified live," not a bug. Added `tests/test_early_reclaim.py` (6) — a lockstep
replica (mirrors test_avwap_reclaim_gates.py) proving EARLY fires under the confluence and is correctly suppressed
by chase / no-buyers / wall-too-close / no-wall / too-wide-stop. **Suite 164 → 178 passing.** Restart+rescan done
(prior_close populated on all 794); no commit/push/build.

## 2026-06-12 (later) — Breakout Watch CHASE GUARD (distance-to-9-EMA gate)

**Trigger:** the pre-arm lane surfaced SNDK as a "breakout watch" — but SNDK had run +4.65% vertical to $1969 with
its 9-EMA at $1751 (**+12.7% / 1.4× ADR above the 9-EMA**). Trader: "these are terrible breakouts, 100% a chase —
we can NOT allow breakouts where we're so far from the 9-EMA." Correct. The selection gated distance-to-PIVOT
(`dist_hi` ≤ 2%) but never distance-to-the-9-EMA, so a name rubber-banded far above its short MA but near its
high slipped through. (Removing the morning's `parabolic` filter is what let the chasers in.)

**Fix (`app.py` `_breakout_watch_picks`):** added a CHASE GUARD — the breakout entry must sit within **1× ADR of
the rising 9-EMA** (`_BW_MAX_EXT_9EMA_ADR = 1.0`; needs ema9 + adr + a sane entry or it's dropped). A genuine
tight-base breakout fires NEAR the short MA; farther is a chase. Today's effect: of the 8 prior picks, only **GH**
(0.6× ADR above its 9-EMA) survives — SNDK (1.4×), HUM (1.5×), AMAT (1.5×), M (1.6×), LRCX (1.3×) all correctly
rejected. The near-empty lane is the HONEST read of a vertical tape: almost everything ran today → wait for the
pullback to the 9-EMA. Tests: +2 chase-guard cases, fixtures given ema9/adr; suite **164 passing**. Server
restarted (live-verified: API shows GH only, SNDK gone).

## 2026-06-12 — Breakout Watch (pre-arm lane) for coiled leaders in a recovering tape

**Trigger:** trader looking at QQQM reclaiming its 9/21 cluster (~296 line) off a sharp pullback (309→286): "market
is rotating back healthy, we need breakout setups armed in Auto Pilot when it's close — even before it's fully
there. Don't see them yet." Diagnosis (grading tonight's live `suggestions.json`): the tape is constructive
(posture 68, light green) but the **rubric crushes breakout legs to C/D** in a fresh-off-a-pullback tape — SNDK
RS100 0.7% from its pivot graded **C**, OSCR/HUM/LRCX **D**. 437/549 breakouts grade D, only 17 ≥ B. So the
A/A+/B arm gate drops every coiled leader and the panel reads empty exactly when rotation starts. CAR (the lone A)
was all that armed.

**What shipped — a separate, grade-INDEPENDENT "Breakout Watch" pre-arm lane (`app.py`):**
- `_breakout_watch_active(market, posture)` — lane is live ONLY in a RECOVERING tape: posture ≥ 60 AND ≥1 index
  in state Recovery/Healthy uptrend (= reclaimed its 21-EMA). Off in pullback/correction/deep-correction tapes.
- `_breakout_watch_picks(graded, in_cands, skip_buy)` — pure, unit-tested. Up to 8 Breakout setups, selected on
  RAW strength: rs_score ≥ 90, within 0–2% of pivot (`dist_hi`), tight base (`tight_x` ≤ 8), above the 50, not
  extended, not already armed/held. **`parabolic` deliberately NOT filtered** — it's a grade-cap, not a base
  signal; the strongest coiled leaders (SNDK/HUM/LRCX) are all flagged parabolic. Today's picks: SNDK, HUM, SMTC,
  SNX, LRCX, GH, AMAT, M.
- Threaded through the SAME canonical confirm path (appended to `cands` after the 18-cap). While NOT triggered →
  routed to a new `breakout_watch` list (yellow, **silent — no beep**, `grade=None` so it never conflicts with
  the gameplan's one-truth). If a watched name actually breaks out (ORH + buyers-confirm + stop-≤1×ADR) it
  **promotes into `buys`** and beeps like any other setup. FIRE gates byte-for-byte unchanged.

**Decisions made:** (1) trigger = recovering regime (not always-on, not a manual toggle); (2) strict leaders-only
bar (RS≥90, ≤2% from pivot); (3) a SEPARATE watch lane showing RS + %-from-pivot, NOT a forced grade-floor and
NOT a rubric rewrite — keeps grades firewalled. The "why does a tight RS-100 leader grade C?" rubric question was
explicitly deferred as separate (grade change → forward-test gated).

**3 surfaces wired:** Dashboard (index.html) + Auto Pilot (autopilot.html/app.js) via the ux agent — amber, dimmer
than armed, RS badge + "% from pivot", expandable, mobile-safe at 375px, renders nothing when empty; Telegram
morning brief gets a "🟡 Breakout Watch (N)" block. **Caught on live-verify:** the ux agent's editor smart-quoted
its autopilot.html block (curly `‘’` string delimiters across the render() empty-state + watch lane) — a JS syntax
error that would've BLANKED the whole Auto Pilot tab. Fixed all delimiters → straight quotes, `node --check`-clean.
(The "verify UI live, don't trust the agent's claim" rule earned its keep.) Card level falls back to `trigger` when
`break_level` is null (these fire on today's-high breaks). **Burry (qa) verified: PASS** — no grade leak, one-truth holds,
no FIRE loosening, watch lane can't beep, off in deep corrections. Tests: +`tests/test_breakout_watch.py` (5),
suite 157→**162 passing**.

**What's next:** trader must **restart the local server** for `app.py` to go live (no re-scan needed — reads the
existing graded suggestions). NOT built to swinghelper, NOT pushed (git is the trader's). Open: (a) forward-test
whether pre-armed leaders that promote actually pay; (b) the deferred rubric question — is C/D too harsh on a tight
RS-100 leader at its pivot?

## 2026-06-11 (late) — GLXY "BUY B on chop" bug fixed (2 AVWAP gates) + EARLY wired to ALL surfaces

**Trigger:** trader got a "🟢 BUY GLXY · B" Pullback@AVWAP alert on a choppy non-leader (RS 62/63, trend_template
False, Crypto, a year of 46→17→32 round-trips) — right after the coach said GLXY was a C/pass. Burry root-caused
TWO real bugs (the None/C/B across surfaces was mostly expected: grades aren't persisted; forward_log is the
EOD headline-avg at posture 44; armed_history is the intraday per-leg at higher posture).

**Fix 1 — AVWAP patient-B leadership gate (`app.py:3609`):** the Deep-Pullback arm path requires rs_pct≥70 OR
trend_template, but "Pullback @ AVWAP" had NO such gate — any AVWAP B armed in a weak/mixed tape. Now an AVWAP
setup needs the SAME leadership (rs≥70 OR TT) to arm B (the green-tape `_healthy` escape hatch stands). GLXY no
longer arms B; leaders (DOCN RS97/SNDK RS98/INTC RS93/ALAB RS99) still do; ~60 non-leader AVWAP names go quiet
in non-green tape. `worth_waiting` can't bypass it (it's Deep Pullback/Consolidation only → AVWAP always False).

**Fix 2 — Path B post-clear zone-drift gate (`app.py:4161`):** the CLEAR_WALL branch fired the post-clear entry
with no re-check of drift from the AVWAP zone (the 0.5×ADR near-zone cap only ran on the PRE-clear entry) → a
breakout entry under a pullback label (the CRDO/AAOI bug). Now stays ARMED if `_post` > 0.5×ADR above the
pullback `zone_top`; in-zone wall-clears still fire; frozen bypasses. Honest caveat: GLXY's ADR is wide (8.6%),
so Fix 1 is the primary stop for the $31.16 case; Fix 2 catches the more-extended legs — defense-in-depth.

**EARLY tier wired to ALL surfaces (completing the morning's build):** Telegram bot now sends `🟡 EARLY BUY ·
{grade}` + yellow light (vs `🟢 BUY`) for `early` recs (`app.py:6546`); Auto Pilot verdict headline goes yellow
when the lead trigger is early (`app.py:4486`); autopilot.html + app.js nested `planHtml` buy boxes got the
yellow `early` class (the ux agent left both green). Coverage: dashboard/app, autopilot, telegram, in-app feed,
hosted (build-ready — make-build syncs app.py + index/app.js/styles.css/autopilot.html; Telegram stays local).

**Proof:** 157 tests pass (6 new in test_avwap_reclaim_gates.py; the 2 tripwires fail-on-revert), golden grade
snapshot **byte-for-byte** (both fixes are confirmation/arming layer, firewalled). Quant implemented, **Burry
independently verified → SHIP** (6/6 items, no NameError, no over-suppression, no regression). Backup:
`backups/2026-06-11_early-tier/app.py.pre-glxy-fix.bak`. lessons.md + strategy/swing-system.md updated.

**What's next:** **restart** the local server (today's app.py changes: EARLY tier, EARLY Telegram/autopilot
wiring, the 2 GLXY gates — no rescan needed). **Forward-test** EARLY + the GLXY gates before trusting live (no
backtest). Say **"build"** to push EARLY + the GLXY fixes to the friends' site.

## 2026-06-11 — EARLY (semi-confirm) tier for high-ADR deep pullbacks + day's trades logged

**Trades (2026-06-11, all closed → net −$9.23 across 8, scratch in the flip):** CIFR +$32 & +$37 (the day's
edge — the one clean RS leader, paid twice), RDW +$9.27 (2nd go), DOCN +$7.2, RDW −$42 (1st), SNDK −$16.7,
SQQQ −$14 (intentional flip hedge), AXTI −$22. Discipline was good (defended CIFR/DOCN into the flip, hedged,
sized small); leak was over-trading the chop. AXTI re-filed: the **read was right** (caught the 82.31 flush
bottom, reversed ~+4%) — the −$22 was a **noise-tight stop on a ~5%+ ADR bottom-fish**, not the name. New
lesson added (bottom-fish: stop UNDER the flush low + cut size, or buy the reclaim).

**Feature — ADR-aware "EARLY" confirmation tier (trader's idea, the RDW case):** the deep-pullback "clear the
wall" gate (right for breakouts) was **over-suppressing high-ADR deep-pullbacks AT support** — RDW reclaimed
~$15.20 at the 50/support (tight stop ~$14.5) but the prior-day-high wall sat ~5% up, so waiting to clear it
blew the stop past 1× ADR → engine dropped it to "too extended / no call", **losing the good entry**. Fix
(`app.py` `compute_now`, `is_deep` block): when a genuine 50-reclaim (all knife gates passed) has a tight
(≤1× ADR) support stop AND clearing the wall would be a real chase (>1× ADR from the wall), fire an **EARLY**
buy now at the reclaim, tagged `RECLAIM_50_EARLY`, `early=True`, frozen for the day (stays EARLY on re-poll).
Surfaced like a normal buy card but **yellow** (`.plan5 .buy.early` amber box + "Early" pill + "Early entry"
badge) across all web surfaces (index/panel/autopilot → so Data Center + Coach window + hosted all inherit it).

**Proof:** 151 tests pass; grader golden **byte-for-byte** (confirmation-layer, firewalled). Burry verified
6/6: knives structurally can't reach EARLY (it's after reached_50/held_above/turning_up), chase-math None-safe,
frozen re-poll stays yellow, no AVWAP-block regression, no NameError. Yellow rendering verified live via
computed styles (amber border `rgba(255,181,61)` vs green `rgba(34,224,161)`; badge gradient `#ffc843→#ffb020`).
Backup: `backups/2026-06-11_early-tier/app.py.bak`.

**What's next:** **restart** the local server to go live (app.py change; no re-scan needed — `compute_now` is
live). **Forward-test** EARLY before trusting it (not backtested). NOT synced to swinghelper — needs a `build`
to reach the friends' site.

## 2026-06-10 (late⁴) — Tape alerts fully fixed: Guard re-spam + a DECOUPLED redesign (Burry caught a loop)

**Bug chain:** After the Turn-spam fix, the trader still got "⚠️ TAPE GUARD armed" **6× in 2.5h** (every 30
min) on a sustained fade — the Guard was still level-triggered (re-reminder). First fix: Guard edge-triggered +
the Turn-fire clears the Guard key (symmetric). **Burry then caught a worse latent bug:** Guard and Turn are
INDEPENDENT state machines that can BOTH be true in one poll (a V-recovery day — indices red on the SESSION yet
spun back up INTRADAY); the mutual key-popping then re-cleared each other EVERY 30-sec poll = infinite
double-spam (worse than the original).

**Final fix — DECOUPLED edge + debounce:** removed ALL cross-key-popping. Each signal fires ONCE per episode and
re-arms only after it's been OFF continuously for 20 min (`_TAPE_REARM_SEC`); a brief flicker doesn't re-arm.
Independent → a loop is structurally impossible even when both are live. Alert-only; cold-start safe (persisted
ledger). The `tape_turn_state` / `tape_guard_state` machines were unchanged (correct).

**Proof:** 151 tests pass (139 + 12 new, incl. the both-signals-true loop-regression test that fails-old /
passes-new); grader golden byte-for-byte; independent grep confirms zero cross-pops (each key popped only by its
own block). Burry **SHIP**. Backups: `backups/2026-06-10_tapeguard-spam/` + `2026-06-10_tapeturn-spam/`.

**What's next:** **restart** to go live (the running server predates this fix). NOT yet re-synced to swinghelper
— needs a fresh `build` to reach the friends' site.

## 2026-06-10 (late³) — Tape Turn message SPAM fixed (Burry, restart pending)

**Bug (trader, live):** in a choppy red tape the "✅ TAPE TURN — stand-down lifted" alert spammed repeatedly
(Telegram + desktop). The Tape Guard fired once and worked. **Root cause** (`_now_watcher`, app.py ~6419):
the old `elif not tt.get('on'): fired.pop('tapeturn:on')` wiped the Turn's 30-min cooldown the instant the
turn flickered off, so the next `confirmed` poll re-fired as a first-timer — unbounded spam when the tape
oscillates confirmed→off→confirmed every 30–90s. The Guard had no symmetric pop-on-off → it never spammed.

**Fix (Burry):** never pop the Turn key on the off-state (the 30-min cooldown persists through flickers,
mirroring the Guard). The Turn cooldown resets ONLY when a fresh Guard alert fires — a genuine new
Guard→Turn cycle earns one fresh all-clear. Turn now fires once, then quiet ~30 min / until a real new cycle.
The `tape_turn_state` machine itself was confirmed correct (stateless-per-poll by design; the flicker is real
market behaviour, handled at the watcher layer).

**Proof:** 139 tests pass (135 + 4 new spam-regression tests in `tests/test_tape_turn.py`); grader golden
byte-for-byte. Burry **SHIP**. Backup: `backups/2026-06-10_tapeturn-spam/`. Needs a server restart to go live.

## 2026-06-10 (late²) — AVWAP plain-reclaim: two anti-chase gates (BUILT + Burry-verified, restart+rescan pending)

**What was done:** The fallback AVWAP-reclaim path (Path B in `compute_now`) still chased two ways the trader
hit live today:
1. **Too far above the AVWAP (ONTO).** Confirmed $289.75 vs AVWAP $274.95 (~1×ADR up, top of the candle). The
   near-AVWAP entry cap is tightened **1×→0.5×ADR**; farther above → stay ARMED for a closer retest. (Path A's
   EMA-clear cap left at 1×ADR.)
2. **A wall right overhead (INOD).** Confirmed $100.67 with the 9-EMA $103.28 only ~2.6% above = buying into it.
   Path B now runs the entry-anchored highest-overhead-wall gate (9/21 EMA / prior-day high / descending line
   within 0.6×ADR above the *entry*): in-band → require a 5-min CLOSE above the HIGHEST wall, else ARM; and if
   clearing the wall would push the stop (under the AVWAP) past 1×ADR → no clean entry → stay armed.
   `_highest_overhead_wall` gained an `ema_cands` param (proximity-gated overhead EMAs); existing callers
   unchanged (default None).

Built by quant; **135 tests pass** (124 baseline + 11 new gate tests incl. Burry's Gate-2-exclusive test);
grader golden **byte-for-byte** (firewall held). Confirmation-only.

**Decisions:** 0.5×ADR cap chosen by the trader (over 0.25× / 1×). Burry **SHIP** verdict — one misleading test
annotation corrected (the production logic was never in question). Backups: `backups/2026-06-10_scan-age-collision/`
(`app_pre-pathB-gates.py`, `app_pre-avwap-gate.py`).

**What's next:** **restart + rescan** to go live, then **forward-test** — INOD should ARM "clear $103.28", ONTO
should stay ARMED (no $289.75 chase). Lesson recorded in `journal/lessons.md`.

## 2026-06-10 (late) — CRASH FIX: local + hosted both went offline at market open

**What was done:**
1. **Root-caused & fixed the market-open crash.** A second `def _scan_age_min()` (0-arg, used by journal
   logging) had been added LOWER in app.py and silently **shadowed** the original `_scan_age_min(s)`.
   `_data_stale()` calls it only inside `if _us_regular_open()`, so `/api/suggestions` raised `TypeError` on
   every request the instant the US regular session opened → empty reply (curl: "Empty reply from server") →
   the dashboard rendered "offline" with no setups. Hit LOCAL and the hosted Render site at the same moment
   (same code). Fix: merged into one `_scan_age_min(s=None)` (reads suggestions.json when the arg is omitted),
   deleted the duplicate, formatted the journal age as `:.0f`. Verified: py_compile, both call sites, and the
   forced regular-hours `_data_stale` branch — no TypeError. Swept app.py for other duplicate top-level def
   names: none. Backup: `backups/2026-06-10_scan-age-collision/`.
2. **Build synced to swinghelper** (31 tests pass incl. analyze_matches_golden → grades byte-for-byte) — NOT
   committed/pushed. The friends' site stays broken until the trader pushes
   (`git -C swinghelper add -A && commit && push origin main`).
3. **Journal correction.** An SNDK (SanDisk) buy was mis-logged as **SNDX** (Syndax, ~$18); the watcher quoted
   real SNDX at $17.99 against the $1698 entry and **false-auto-closed it as a −20R stop-out**. Corrected to
   SNDK / open / entry 1698 (trader set stop 1658.5); false −$84/−1R removed. trades.json backed up alongside.

**Decisions:** The Telegram bot is confirmed to be a thin layer over `compute_now()` — no separate
setup/confirmation logic, so engine/grade/confirmation changes flow to it automatically; it only needs a
server **restart** to pick up app.py edits. Auto-close-on-stop kept ON at the trader's choice.

**What's next:**
- **TRADER ACTION:** push `swinghelper` to deploy the crash fix to the friends' Render site.
- **OPEN (engine — needs Burry/qa + restart):** *implausible-quote guard* — suppress the stop-out/auto-close
  when the live price is wildly off the entry (e.g. <0.5× or >2×); treat as a data/ticker error and warn
  instead. Would have caught the SNDX/SNDK false stop. Same family as the agorot unit-jump bug.

## 2026-06-10 — SESSION HANDOFF (read first)

**What was done today (all LIVE locally; SYNCED to swinghelper but NOT committed/pushed — git is the trader's):**
1. **Hosted on-demand LIVE scan** — kills the frozen-snapshot problem; the friends' site gates behind a fresh full
   scan (wait panel + progress), never shows stale as live. Free.
2. **🚨 Data-feed fix (HIGH)** — the whole app was a session stale: Yahoo returns the latest CLOSED bar with null/omitted
   OHLC, the fetcher skipped it. Now backfilled from the same free Yahoo `meta` (PATH A null / PATH B omitted),
   provisional-tagged with a 1h TTL. App caught up to June 9. (tests/test_fetch_backfill.py.)
3. **Descending-trendline detector** — anchors at the CURRENT down-leg (not the stale all-time high) + proximate-ceiling
   selection + short-fresh-leg + secondary-peak. BE now = June-2 line → $280.74 (= the breakout), as the trader drew it.
4. **Per-entry confirmation messages at 3-surface parity** — breakout entries name the BREAK ("clear the downtrend
   line"), pullbacks keep "reclaim of the 50 EMA"; the dashboard CARD, AUTO PILOT, and the LIVE-COACH PANEL all render
   the same wording. (panel.html is local-only → not in the build, by design.)
5. **Arm A+/A/B in a healthy (green) tape** — compute_now (~app.py:3585): in a GREEN light, B now arms for ANY setup
   (breakouts/plain pullbacks), not just patient dip-buys; weaker tapes keep the strict gate (caution → patient-only).
   WATCH-only — the reclaim+buyers_confirm+stop-≤1×ADR FIRE gates are unchanged; firewalled from grades (124 tests pass).
   Real-time entry-switching already works (leg-filter arms the best NON-STALE qualifying entry, so a missed/stale
   pullback yields to the breakout when it qualifies ≥B). A C breakout (extended chase, e.g. BE $280) correctly stays
   unarmed ("below B, no"). Verified live in the current red tape: arms 8 A + 10 B patient setups, no breakouts.

**Decisions:** grade firewall held throughout (golden re-baselined ADDITIVELY on the FROZEN fixtures; never run
`tools/make_golden.py` for that — it re-fetches + moves the baseline to live data; re-run analyze on existing fixtures
instead). Confirmation/trendline are firewalled from grades. Full suite 124 passed; multiple Burry SHIP verifications.

**What's next / open:**
- **Push to deploy:** `git -C swinghelper add -A && git -C swinghelper commit -m "…" && git -C swinghelper push origin main` (Render auto-deploys; 403 = the NoamHooked/noamrodev account thing).
- **Refresh** the local Auto Pilot tab + the coach panel window to see the confirm changes (static files; browser refresh).
- **Forward-test** the hosted live-scan timing on the real free dyno (how long the friend waits); trim the universe if too slow.
- Minor: a breakout-PRIMARY name's pullback 2nd-entry shows no confirm row (deliberate — no-confirm > wrong-confirm); could add a per-entry pullback confirm if wanted.
- Pre-today carry-forwards still open: grade-rubric v6 forward-test; NXT/FN/SATS/TCGL items.

## 2026-06-10 — Per-entry confirmation messages: breakout entry names the BREAK, not the pullback's 50-EMA

**Trader (BE):** the BREAKOUT entry's card said "confirms on: reclaim of the 50 EMA" — wrong; a breakout
confirms on the BREAK (clear the downtrend line / prior-day high), the 50-EMA reclaim is the PULLBACK entry's.
Root cause: a dual-setup name (e.g. BE = Deep Pullback + a breakout 2nd-entry) had confirm info only at the
SUGGESTION level, and the card rendered the live rec's confirm for EVERY entry — so the breakout entry inherited
the pullback's `RECLAIM_50`.

**Fix (confirmation-only; grades byte-for-byte).**
- **scanner.py** — after `_res_trend` is computed, attach per-ENTRY confirm fields to every breakout entry:
  `break_level` = the trigger, `res_trendline` = today's line IF it sits within the wall band of the trigger
  (then the break IS the trendline break), `confirm_menu` = `YH_RECLAIM` (prior-day-high note) / `ORH_BREAK`.
  BE breakout now: break_level 280.74, res_trendline 280.74 → **"close above $280.74 — clear the downtrend line"**;
  the pullback entry keeps "reclaim of the 50 EMA". (compute_now's live-rec breakout already names the line via
  the earlier FIX-2; panel/autopilot show the live rec, so they were already correct — the gap was the card's 2nd entry.)
- **web/app.js** — new `entryConfirmRec(s,e)`: a breakout entry (`break_level != null`) uses ITS OWN fields;
  other entries borrow this name's live rec ONLY when it's the same kind (entry_type) — so a breakout-PRIMARY's
  pullback 2nd-entry no longer shows the breakout's "clear the downtrend line" (symmetric bug); on a kind
  mismatch the confirm row is HIDDEN (no confirm > wrong confirm). **web/index.html** — the "confirms on" row
  reads `entryConfirmRec(s,e)` per entry instead of `confRec(s)` for all.
- **Surfaces audited:** pullback-family setups (Deep Pullback/Pullback/Consolidation/AVWAP/Pullback-21) correct
  on BOTH entries; breakout-family breakout entry → downtrend-line/overhead, pullback alt → hidden.
- **3-surface parity (added after the card fix):** Auto Pilot (web/autopilot.html) had NO confirms-on row — added
  `_CONFIRM_LABEL`+`confirmMenuText`+`wallConfirmText` (copied byte-for-byte from app.js) and a "confirms on: …"
  line on each armed row. The Live-Coach PANEL (web/panel.html) only showed the WALL confirm — added
  `confirmMenuTextPanel` so pullback setups (no break_level) now show "reclaim of the 50 EMA" too. Both use the
  SAME logic/wording as the card. VERIFIED live: 18 confirm lines on each, zero console errors. Charts/setup
  details were already shared (same /api/chart + suggestions), so they matched already.
- **VERIFIED live** (rescan 2026-06-10 11:45 UTC): BE breakout alt = "close above $280.74 — clear the downtrend
  line"; CAR (breakout-primary) breakout = "clear the downtrend line", its pullback alt hidden. NB: had to kill
  DUPLICATE server instances — an earlier rescan ran on a stale process before the edit loaded; one clean server now.

**Firewall.** Only 3 additive keys (`break_level`/`res_trendline`/`confirm_menu`) appear on breakout entries —
verified zero shared-field change, grades byte-for-byte (golden re-baselined additively on the FROZEN 250-bar
fixtures; `golden_grade_snapshot` unchanged → `lexicon_tags_do_not_change_grades` green). Full suite 124 passed.
NB: avoid `tools/make_golden.py` for additive re-baselines — it RE-FETCHES + overwrites the fixtures (pulled them
to 251-bar/June-9, polluting the frozen baseline); re-run analyze on the existing fixtures instead. **Not built/pushed.**

## 2026-06-10 — 🚨 DATA-FEED FIX: app was a full session stale (Yahoo null/omit latest bar) — backfill from meta

**Severity: HIGH (every setup was graded on a session-old data).** Trader caught it on BE: the chart candles were
wrong — the last real candle (June 9) was missing for EVERY ticker; the whole app sat on June-8 data.

**Root cause.** `scanner._fetch_raw` skips any daily bar with a null OHLC. Yahoo's free chart API returns the latest
CLOSED session (June 9) with its **historical close = null** (not yet finalized into the array) even though the
session closed — so the app dropped it for all 800 names and froze at June 8. The real numbers were sitting unused in
the same response's `meta` block (regularMarketPrice/DayHigh/DayLow/Volume). FREE — same endpoint, we were just
throwing the meta away. (This also explains the trendline confusion: the breakout "$280.41" was June-5's high, and
the June-2 line read $286/$293 — all because June 9 was absent. With June 9 in, the June-2 line now reads **$280.74 =
yesterday's high = the breakout**, exactly as the trader said.)

**Fix (scanner.py — `_parse_chart` pure helper + `get_bars`; built & verified via two ultracode workflows).**
- **PATH A** — latest bar present but close=null → reconstruct from `meta`.
- **PATH B** — Yahoo OMITS the latest closed session entirely → derive the session date from `meta.regularMarketTime`/
  `currentTradingPeriod` and append it. Both share `_reconstruct_bar_from_meta` (close/high/low/vol from meta; open
  from quote-array open, else prior close, clamped into [low,high]).
- **Guards:** only when `_meta_session_closed` (never a live/forming/pre-market/future bar), strict-greater dedup (no
  double-add), graceful degrade on partial meta (never raises), no lookahead. `.TA` still normalized.
- **Provisional convergence:** meta-built bars tagged `_provisional` (non-breaking key; downstream reads only OHLCV);
  `get_bars` shortens their cache TTL to **1h** so they reconverge to Yahoo's finalized close/volume — without making
  an 800-name scan refetch every name each cache hit.

**Verified.** 2 workflows, 9 agents. Live force-fetch: BE = exact `O261.94 H280.74 L241.92 C259.61 V16166746`; all
tickers advanced 06-08→06-09; SPY (ref index) advancing un-sticks app-wide freshness/EOD gating. Completeness critic
PASS (P1 omitted-variant, P2 provisional TTL, P3 pre-market non-fabrication all closed). Grade firewall intact —
golden byte-for-byte; **suite 124 passed** (+4 backfill tests). Backups in `backups/2026-06-10_backfill-harden/`.
Deployed: local server restarted + full rescan re-grading on June-9 data. **NOT built/pushed** (trader's call).

## 2026-06-10 — Descending-trendline detector: anchor at the CURRENT down-leg (the BE fix)

**Trigger.** Trader (on BE): the chart wasn't drawing the descending resistance, and the engine, when forced to,
anchored it at the **stale all-time high (May 22 $322)** instead of the **start of the current down-leg (June 2 $305)**.
His rule (correct): "it's NOT from the highest point, it's from the most relevant point where the trendline didn't
break yet — June 2." Three quant passes + 2 Burry SHIP verifications, all **confirmation-only, grades byte-for-byte**.

**What was wrong & fixed in `_descending_resistance` (scanner.py):**
1. **Anchor = current down-leg, not global max.** Added current-down-leg anchor candidates (every right-pivot
   dominant lower peak), and made the selection prefer the **most-recent** qualifying anchor. BE: was May22→$293
   (6 touches), now **June2 $305.11 → $286.27 today (4 touches)** — exactly the trader's line.
2. **Secondary-peak anchoring** (two-stage tops) — earlier pass; the global-max line gets broken by a later lower
   peak's bounce, so try lower pivots too.
3. **Proximate-ceiling selection** — pick the nearest OVERHEAD line (the wall price is actually fighting), not the
   most-touched stale one.
4. **Short fresh leg admitted** — a 2-bar leg (June2→June4) is no longer dropped on `TREND_MIN_LEG=3` alone, IF it
   still has the full touches + lower-highs staircase + proximity (leg length was never the real noise guard).
5. **End-anchor margin cap** — new `rubric.TREND_ENDLOWER_CAP_PCT=3.0` (only binds for ADR>6% names like BE; the
   0.5×ADR margin was wrongly demanding a bigger lower-high step than BE's real ~$9.4 on its 8.6% ADR).
6. **Breakout setup now NAMES a coinciding trendline** (app.py compute_now) — "close above $X — clear the downtrend
   line" when a breakout trigger sits under an in-band res_trendline; the line never pushes the trigger up.

**Honest nuance (told the trader):** the steepest CLEAN June-2 line lands ~$286, not his hand-drawn ~$268 — going
lower would cut through the real **June 4 high ($295.69)**, which the strict no-poke guard (rightly) won't do.

**Verified.** Burry SHIP ×2: only `res_trendline` drifted in the golden (NVDA null→line, TSLA/GOOGL→recent anchors,
all grade fields byte-for-byte). No lookahead, no loosened core guards, no clean-uptrend noise. Suite 60/60 (112 full).
**Chart endpoint** already recomputes res_trendline live (app.py ~5389) — so a server **restart** shows BE's new line
immediately; the suggestion's stored value needs a **rescan**. Backups in `backups/2026-06-10_descending-anchor-recency/`.

## 2026-06-10 — Hosted on-demand LIVE scan (kills the frozen-snapshot problem) + synced to swinghelper

**Problem.** The hosted (friends') site served a FROZEN warm snapshot from build time — Render's free plan sleeps
after ~15 min idle and wipes the disk, so `_shared_refresh_loop` deliberately never auto-rescanned (a wake-time
rescan changed the list on every click). Result: friends saw stale/build-time setups labelled "watching live".
Trader: *"we can't allow frozen snapshots… fake data is the worst thing we can show them. they will wait."*

**Fix (HOSTED only; LOCAL completely unchanged; grades firewalled — grader 7/7 byte-for-byte, full gate 31/31).**
On-demand full scan: gate every setup surface behind a fresh scan, never render stale rows as live.
- **Backend (app.py):** new `_data_stale(s)` + `_scan_age_min(s)` + `HOSTED_FRESH_MIN=30` — stale if no items / a
  prior-session scan / (during RTH) >30 min old. Added freshness fields to `/api/suggestions`, `compute_autopilot`
  (`stale`,`scanning`,`scan_done`,`scan_total`,`scanned_at`,`screener_id`,`hosted`) and `compute_health`
  (`hosted`,`scan_running`). Scan POST endpoint now claims `SCAN.running=True` **synchronously** before spawning
  (closed a start-race where the poller saw running=False and thought the scan had finished instantly).
- **Frontend (autopilot.html + app.js + index.html):** on hosted+stale → hide setups, auto-POST `/scan/<sid>?fresh=1`,
  show a wait panel ("Getting today's live setups" + spinner + N/800 progress + current ticker), poll `/scan/status`,
  then render live setups stamped **"LIVE · HH:MM"**. Manual **↺ Re-scan** button when live; honest header (no fake
  "watching live"); 13-min stall→retry. Mobile-friendly. LOCAL path untouched.
- **Architecture note:** `suggest_f()` etc. are SHARED (not per-user) on hosted → ONE scan serves all friends; the
  global SCAN lock serializes correctly (no per-user scan storms).

**Verified LIVE on a throwaway HOSTED instance (port 8766):** fresh load → LIVE stamp + setups + Re-scan; Re-scan →
wait panel with live 0→800 progress (no stale rows) → real scan wrote fresh data (07:44→08:03) → flipped back to
LIVE · new stamp. LOCAL (8765) confirmed unchanged ("● watching live", reload ↻, no gate). **3 bugs found & fixed
during live test:** (1) screener_id not set on the fresh path → Re-scan no-op; (2) scan start-race; (3) the 5-min
auto-reload fired mid-scan (guarded at fire-time now). Test instance stopped; data/suggestions.json restored to pretest.

**Status: BUILT & synced to swinghelper (NOT committed, NOT pushed — git is the trader's).** To publish:
`git -C swinghelper add -A && git -C swinghelper commit -m "…" && git -C swinghelper push origin main` (Render auto-deploys).
**What's next:** push to deploy; forward-test the real free-dyno scan time (~5 min local incl. earnings fetch — confirm
it's tolerable on the throttled dyno); optional `.claude/launch.json` "hosted-test" config left in for future hosted QA.

## 2026-06-10 — SESSION HANDOFF (everything below this date is LIVE; read this first)

**Current state.** Server running (last PID 30524, port 8765), alerts ON (`notify_mode` removed), all of today's
work DEPLOYED via restart + a fresh `market-leaders` rescan. Grader 7/7 byte-for-byte throughout; full suite 106.
NOT committed (git is the trader's) and NOT synced to swinghelper.

**What shipped today (all live, all Burry-verified except the last refinement which is self-verified 106 tests):**
- 3 intraday tape defenses: **over-stretch shield** (regime_signal risk_off, per-index 5/7/7), **Tape Guard**
  (reject+rollover → stand down + move to break-even), **Tape Turn** (flush+spin-back → all-clear), one-banner display.
- Pullback/confirmation engine: **NBIS** pullback-to-21, **LUNR** deep-pullback knife guard, **AXTI** horizontal
  CLEAR_WALL, **descending-trendline gate** (the trendline GOVERNS the confirmation; strict unbroken-line detector;
  card "confirms on" names the downtrend line + clears at the line level).
- Bug fixes: after-close buy-alert spam (compute_now demotes to armed when closed + watcher RTH gate), **scan
  stuck-flag** (run_scan try/finally + 10-min staleness auto-clear), banner double-render, bull-flag overlay
  removed, stance-label doubling, 5 data/risk guards (B3 market-stale / B4 stop-above-price / B5 result_r-None /
  B6 stale-cache fallback / overnight-exposure cap warning).

**Open / what's next (carry forward):**
1. **Forward-test the new defenses live** (the trader's rule: validate with forward data, not backtests): the
   over-stretch 5/7/7 lines, Tape Guard/Turn arm rates, the trendline gate.
2. **Optional Burry re-verify** of the final trendline-GOVERNS change (self-verified: 106 tests + grader 7/7).
3. **Minor:** 25% soft-warn exposure level is computed but only the 30% cap surfaces a stance line (wire it).
4. **Parity gap:** the LIVE `now` item lacks `res_trendline`, so the Live-Coach PANEL's "confirms on" may still
   say the generic label (the DASHBOARD card works — it reads the suggestion's res_trendline). Attach res_trendline
   (or a wall_kind) to the armed rec for full parity.
5. Spawned task chip still open: **unit tests for the pullback/confirmation features** (task_90f15324).
6. Earlier pending (pre-today): grade-rubric v6 forward-test, the NXT/FN/SATS/TCGL items, swinghelper build/push.
7. **A `build` (sync to swinghelper) was NOT done** — none of today's work is on the hosted site.

**Today's trades logged** (data/trades.json + lessons.md): FLNC +71.40 (win, profit-protect), AXTI/INTC/TER −1R
each (tilt — entered into a rejecting tape), plus the trader's separate green day-trades (QQQ short + the turn).
Net of the logged momentum trades ≈ −$108 (−0.5%); sizing was disciplined, the leak was ENTERING in a bad tape.

## 2026-06-10 — Trendline gate (clear the highest wall) + detector strictness (SEDG) + scan stuck-flag fix

**What was done.** Three fixes, Burry-verified SHIP (7/7 grader, 105/105 suite, zero grade regressions), deployed (restart + rescan).
- **Trendline gate — clear the HIGHEST in-band wall** (quant): DOCN (Pullback @ AVWAP) fired UNDER its descending resistance because the AVWAP `CLEAR_9EMA` branch never consulted the trendline/horizontal walls. New `_highest_overhead_wall` unifies the EMA / horizontal / descending-trendline walls within `WALL_NEAR_ADR` (0.6×ADR) and gates on the **highest** of them (you're not above resistance until all of it is cleared). Wired into the AVWAP + 50-reclaim branches; single-wall cases (AXTI) unchanged. The after-hours armed preview surfaces the line (the gate arms without 5-min bars; only firing needs them). Confirmation-side, firewalled. NB: with DOCN's real 8.2% ADR the highest in-band wall is the **$174.74 prior-day high** (above the $172.75 trendline) — both in-band, so it correctly gates the higher.
- **Detector strictness — reject BROKEN lines** (the SEDG case): `_descending_resistance` used the generous TOUCH tolerance (0.5×ADR) for the "high pokes above" check, so candles up to half an ADR above still passed → pierced/broken lines drew. Split it: new `rubric.TREND_BREAK_TOL_ADR=0.12` — a high more than 0.12×ADR above the line = BROKEN → reject. Only a tiny wick may poke; a close above invalidates it. "Perfect resistance that hasn't been broken." Golden re-baselined for the additive `res_trendline` field ONLY (MSFT/GOOGL/META shifted to different valid anchors; grades byte-for-byte).
- **Scan stuck-flag fix** (trader-found): `run_scan` had no `try/finally`, so an error mid-scan (Yahoo hiccup, bad bar) left `SCAN['running']=True` STUCK → "scan already running" forever, needing a restart / the New-Day button. Wrapped in `try/finally` (always resets the flag; `finished_at` only on success) + a `started_at` stamp + a route **staleness auto-clear** (`SCAN_STALE_SEC=600` — a scan claiming to run with no completion >10 min is treated as hung and the new scan takes over). Self-healing.

**Verified.** Burry SHIP; restart (PID 51628) + rescan. Backups: `backups/2026-06-10_trendline-gate/`, `backups/2026-06-10_detector-scanflag/`. Not synced to swinghelper / not committed.

**Refinement (same night) — the descending TRENDLINE GOVERNS, not the highest wall.** Trader: DOCN's confirmation should be the downtrend line ($172.75 = today's high = the last lower-high of the 3-day down-leg), but the "highest wall" rule gated on yesterday's high ($174.74) just above it. `_highest_overhead_wall` now: a descending trendline in-band GOVERNS (it's the drawn resistance the trader watches; a prior-day high above it is part of the same down-leg, not a distinct higher wall); only with NO trendline does it fall back to the highest in-band horizontal/EMA wall (AXTI). Card display fixed: the header "confirms on" line (index.html:539) used the stale `confirmMenuText` (Lynch's earlier fix only touched the expanded body) — now `wallConfirmText(a)`; and `wallConfirmText`/panel mirror name "the downtrend line" when `res_trendline` is present, not "prior-day high." Verified live: DOCN break_level 174.74→**172.75**, confirm names "the descending resistance line." +1 test (line governs over a higher prior-day high) → suite **106**; grader 7/7 firewalled. Restart (PID 30524) + rescan. Web = hard-refresh.

## 2026-06-09 — End-of-day cleanup batch + full deploy (after-close buy spam, draw-filter, bull-flag removal, banner/stance, data bugs)

**What was done.** Final batch of the 2026-06-09 session, all verified and DEPLOYED (server restarted, alerts re-enabled).
- **After-close buy spam (the DOCN bug):** the trader kept getting Telegram BUY signals after the close. Root cause: a buy that confirmed/froze during the session lingered in `compute_now`'s `buys` via the frozen-price reuse, and the `_now_watcher` BUY-alert loop had NO regular-session gate (unlike the EXIT/stop alerts). Two fixes: (1) compute_now demotes any standing buy to `armed` once `not active` (market closed) — "after the close everything shows as armed, not a live call"; (2) the watcher BUY loop now gated `if _us_regular_open()`. Stop-gap during the session = muted alerts via `notify_mode:off` (now removed). Verified post-deploy: after-hours `buys=0`.
- **Resistance-line draw proximity-filter:** the relaxed (v2) descending-trendline detector drew a line on ~58% of charts (only 6.6% gate). `/api/chart` now draws the line only when it's a real overhead wall (above price, within 1.5×ADR). The GATE is unaffected (separate path, own 0.6×ADR check).
- **Bull-flag/pattern overlay REMOVED:** trader found the flag/pennant/wedge + channel overlay noisy and unactionable — removed the chart toolbar button + the render; the descending-resistance line is kept (decoupled from the old toggle).
- **Banner precedence:** Tape Guard + Tape Turn no longer render as two competing banners — one at a time (confirmed Turn hides Guard; a forming Turn folds into the Guard as a sub-line). All 3 surfaces.
- **Stance-label doubling:** "Caution — correction (Caution - correction)" → uses posture, not the redundant label.
- **5 data/risk bugs (data-steward):** B6 get_bars serves stale cache on a fetch failure; B5 degenerate-stop `result_r`→None+`r_unmeasurable` (no fake-0.0R); B4 `_chat_move_stop` refuses a stop ≥ live price; B3 market.json staleness flag (`_market_stale`, computed_at stamp, /api/market flag, gameplan warning); overnight-exposure cap warning (`OVERNIGHT_EXPOSURE_*`, fires >30%).

**Verified.** Combined Burry pass: **SHIP** — test_grader 7/7 byte-for-byte, full suite **93/93**, no grade regression (only the additive `res_trendline`), no after-hours buy leakage, no-lookahead, no JS syntax errors. Deployed: server restarted (PID 29180), `/api/now` healthy, alerts re-enabled. Backups: `backups/2026-06-09_final-cleanup/` (+ open-bugs, trendline-wall, trendline-wall-v2, tape-turn).

**Open / next.** (1) RESCAN needed to populate suggestion-level changes (the NBIS/LUNR/AXTI pullback fixes, over-stretch shield, `res_trendline` in suggestions.json) — the EOD auto-scan or a manual Scan. (2) Minor: wire the 25% soft-warn exposure level into the stance (only the 30% cap warns today) — Burry-flagged, non-blocking. (3) Not synced to swinghelper / not committed.

## 2026-06-09 — Tape Turn (intraday "market spinning back up" all-clear — inverse of Tape Guard)

**What was done.** Trader's idea after watching QQQ flush to ~$282.8 and reclaim its 5-min 9/21 EMAs ("the
market spins back up just like our spinning stocks"). Built the **Tape Turn** — the counterpart to Tape Guard.
`tape_turn_state` + `_index_spin`: arms STANDALONE when ≥TAPE_TURN_N(2) of SPX/QQQ/IWM each flushed off the
session high (≥FLUSH_PCT), bounced off the low (≥BOUNCE_PCT), reclaimed BOTH the 5-min 9 & 21 EMA, and made a
higher low. Two phases by the slowest spinning index's held-bar count: **forming** (<CONFIRM_BARS=3 ≈ 15 min →
green "watch" note, Guard still wins) and **confirmed** (held ≥3 completed bars → `lifts_guard`, so
`_tg_on = guard.on AND NOT tape_turn.lifts_guard` re-enables new buy confirmations even if still red). Stateless
per poll off COMPLETED 5-min bars, so a whippy up-down-up day re-arms Guard / resets Turn cleanly. Break-even
stops STAY (Turn re-enables buys, never un-raises a stop). Thresholds in `rubric.TAPE_TURN_*`; toggle
`tape_turn_enabled`. Green styling on all surfaces (Telegram confirmed-only + 30-min cooldown, desktop, index/
panel/liveCoach/Auto Pilot). ALERT-ONLY, LOCAL-ONLY.

**Decisions made (trader).** Standalone trigger (not only after a Guard); confirm-delayed re-engage (lift only
after the reclaim HOLDS ~15 min — avoids a 1-bar V-spike bull trap).

**Verified.** Quant + 16 tests; **Burry: SHIP** (grades 7/7, no-lookahead/forming-bar excluded, Guard↔Turn
precedence correct, stateless re-arm, HOSTED suppression, alert-only); **risk-auditor: COMPLIANT** on all 6 rules
(alert-only / local-only / break-even integrity / re-engage still passes every entry gate / longs-only / Guard-
Turn coherence). Burry flagged a cosmetic wording bug — a standalone confirmed Turn (today's case: Turn on, Guard
never armed) said "stand-down lifted" when there was none; **fixed** (reworded to "(any tape-guard stand-down is
lifted)" in both stance + Auto Pilot). Also removed a dead duplicate `_ema_series`. Full suite **79/79**.

**Deployed.** Server force-restarted (killed the PID on 8765, relaunched `pythonw app.py --background` — coach_app
has no watchdog so it was relaunched explicitly; tray still tracks it by port). Verified live via `/api/now`: the
Tape Guard message is now the phase-aware "sold off hard — down day" wording (no more "were up" on a red tape),
and Tape Turn is live (was `forming` on the choppy afternoon — lifts the Guard once the reclaim holds). Fixed one
cosmetic glitch found in the live stance — the forming-turn note printed its "Tape Turn forming —" label twice
(stance prepended it AND the reason led with it); now appends just "⚡ " + reason. NOT synced to swinghelper /
not committed. Backups: `backups/2026-06-09_tape-turn/`.

## 2026-06-09 — Tape Guard (intraday "market rejected & rolling over" defense)

**What was done.** After a live tilt session where every buy taken into a rejecting tape failed identically
(AXTI/INTC/TER −1R each + an AMKR alert into the down tape), built the trader's ask: an intraday **Tape Guard**.
LOCAL-ONLY, ALERT-ONLY. Arms during the cash session when **≥2 of SPX/QQQ/IWM are red AND have rolled over**
(were up intraday / made a session high, now faded ≥TAPE_GUARD_FADE_PCT off it — a rejection, not a slow-red
drift). When armed: (1) new BUY confirmations → **watch-only** (removed from `buys`, no beep, "armed — don't
initiate"); (2) **RAISE_BE** alert tells EVERY open position (stop<entry) to move its stop to break-even; (3)
alerts to all local surfaces — Telegram (30-min cooldown), desktop `_now_watcher`, index.html / panel.html /
liveCoach banners, and the **Auto Pilot** warning the trader named. Thresholds single-sourced in
`rubric.TAPE_GUARD_*`; toggle `tape_guard_enabled` (default true). Distinct from EOD defend (flatten-into-close)
and can run alongside it; precedence EXIT > FLATTEN > RAISE_BE.

**Decisions made (trader).** Trigger = rejection+rollover (not any-red); break-even applies to ALL open
positions (no deep-pullback exemption, unlike defend-flatten); new buys go watch-only (not hard-suppressed).

**Verified.** Quant built + 14 new behavioral tests; **Burry (qa): SHIP** (grades 7/7 byte-for-byte, no
lookahead, HOSTED/shared suppression on every surface, defend + over-stretch arm untouched, alert-only confirmed);
**risk-auditor: COMPLIANT** on all 5 locked rules (alert-only / local-only / BE-soundness / defend-coherence /
no sizing regression). Burry caught one non-blocking bug — Auto Pilot used `compute_now(shared=True)` so the
owner's tab could show "BUY confirmed" next to the stand-down banner; **fixed** (downgrade buys + tape-guard
verdict in `compute_autopilot`). Full suite **61/61** after the fix.

**Refinement (same session).** Trader flagged the alert said "were up and have sold off their highs" while QQQ
was already −2%. Diagnosed: NOT a false fire — the indexes genuinely rallied (SPX +1.05% / QQQ +1.34% / IWM
+2.38% intraday highs) then reversed hard, so the arm was correct. Made the message **phase-aware**
(`TAPE_GUARD_DEEP_PCT=−1.0`, single source via a new `headline`): "rejected & rolling over" while still up-ish
near the highs → "sold off hard — down day" once the avg armed index is ≤ −1%. The ARM + protection (stand-down
+ break-even) are UNCHANGED the whole way down — only the wording changes, so it never claims "were up" on a deep-
red tape. All 4 surfaces reuse the one headline. +2 phase tests (suite 63/63).

**What's next.** RESTART server to activate (app.py changed; no rescan — reads live quotes). NOT synced to
swinghelper / not committed. Backup: `backups/2026-06-09_tape-guard/` + `backups/2026-06-09_tape-guard-msg/`.

## 2026-06-09 — Pullback/confirmation engine fixes (NBIS / LUNR / AXTI) + Burry catch

**What was done.** Three related setup-detection/confirmation bugs surfaced from live trader cases, fixed by
the quant, adversarially verified by Burry. (1) **NBIS** — a medium pullback HOLDING the rising 21-EMA fell
through the SMA-based `uptrend` gate (`above>=2`, 10/20/50 SMA) and got mislabeled Breakout with a chase entry
at the prior high. Added a new setup **"Pullback to 21-EMA"** (rising 9>21>50 fan, ≥1×ADR above a rising 50,
holding the 9/21, higher lows), entry anchored to the 9/21 line, trails the 9 (NOT a long-hold). New rubric
consts PB21_MIN_EXT50_ADR/PB21_NEAR_ADR/PB21_PREF. Placed after deep/consol/pullback/avwap, before EP/Breakout
→ can only ever capture a former Breakout/EP. (2) **LUNR** — Deep Pullback bought a falling knife (collapsed
~57% off the peak into the 50, 6 lower highs). Added a knife guard: a deep pullback in a confirmed downtrend
(≥KNIFE_LOWER_HIGHS=3 successive lower highs AND below DECLINING 9/21) stays worth_waiting but not buyable_now
until it RESPECTS the 50. No pull-% cap (would break AXTI-style legit deep pulls), no 50-slope gate (it lags).
(3) **AXTI** — live confirmation fired UNDER the prior-day-high wall ($96.56). Generalized the IREN CLEAR_9EMA
gate to horizontal resistance: `_horizontal_wall_gate` (rubric WALL_NEAR_ADR=0.6) arms (CLEAR_WALL_ARMED) and
fires only on a completed 5-min close above the wall (CLEAR_WALL) when a prior/recent high sits within 0.6×ADR
above a deep-pullback 50-reclaim entry.

**Burry caught a regression (this is why we run him).** The knife guard lacked a `close < e50` check, so it
wrongly suppressed deep-pull names ALREADY ABOVE the 50 (SCOP.TA flipped buyable True→False). A Deep Pullback
entry IS a 50-reclaim — above the 50 there's no knife. Fixed (one conjunct added). Also closed a parity gap
Burry flagged: added "Pullback to 21-EMA" to `_PATIENT_OK` so PB21 names are watched in caution tape like other
pullbacks.

**Decisions made.** Knife discriminator = downtrend STRUCTURE (lower highs + declining short EMAs), explicitly
NOT depth and NOT 50-slope (lags). New PB21 setup trails the 9, debuts conservative.

**Verified.** test_grader 7/7 byte-for-byte; firewall proof (Burry: 1137 names identical setup_type+score, 10
reclassified Breakout→PB21, zero existing pullback/deep/consol/avwap moved); post-fix behavior: SCOP.TA buyable
True (restored), LUNR/AXTI False (knives caught), RIO True (legit deep-pull-that-bounced preserved). Backup:
`backups/2026-06-09_pullback-confirmation-fixes/`.

**What's next.** RESTART server + rescan to go live (scanner.py + app.py changed). NOT synced to swinghelper.
Follow-ups (non-blocking, Burry-flagged): add unit tests for the 3 new features (PB21 classifier, knife guard,
wall gate); `_successive_lower_highs` reads the forming bar (safe direction, minor). lessons.md + swing-system.md
already updated by the quant.

## 2026-06-09 — Over-stretch shield (independent defend arm, per-index ATR-from-50 lines)

**What was done.** Trader spotted on TradingView that "ATR% multiple from the 50-MA" marks index tops — eyeballed
IWM ~5 / SPX ~8 / QQQ ~9.5 as cash-raise warnings, wanted it as a shield arm "like extended". Engine already
computes this exact metric (`_regime_one.atr_mult_50`) but flagged it with ONE flat 4.5 cutoff feeding the
"extended" half of defend. **Measured first** (`dev/atrstretch/measure5y.py`, 4yr daily bars): (1) because
`atr_mult_50` already divides by ADR, the three indexes top at SIMILAR extension (median ~5.5, extreme ~8) — NOT
5/8/9.5; (2) the trader's TV numbers are in true-ATR units that run ~20% below the engine's H/L-ADR, so literal
hard-coding would make SPX/QQQ never fire. His RELATIVE instinct (IWM lower, mega-caps higher) holds; the spread
didn't. **Decisions (asked):** independent trigger · ≥2 of 3 · data-matched thresholds.

**Built.** `rubric.OVERSTRETCH_50 = {SPX 7, QQQ 7, IWM 5}` (engine units, ≈ rarest 6% of days), `OVERSTRETCH_N=2`.
`_regime_one` emits NEW key `overstretched_50` per index (separate from the untouched 4.5 `stretched_50`/posture
haircut). `regime_signal.risk_off` gains a 4th independent arm: ≥2 indexes over their line → risk_off (arms on
green tape too; froth top reverts like a correction). Flows to `defend_state`/stance/all surfaces via the existing
risk_off path — no per-surface wiring. **Firewall-clean:** band/light/posture/grades untouched.

**Decisions made.** Data-matched 5/7/7 over the trader's TV-eyeballed 5/8/9.5 (units + 4yr evidence). Kept it a
SEPARATE signal from `stretched_50` so no grade moves. Independent arm (no weak-tape required).

**Verified.** `py_compile` clean; `test_grader` 7/7 (grades byte-for-byte); functional: 1 idx → no arm, ≥2 →
risk_off + reason "X, Y rubber-banded above the 50-MA"; current live (SPX 3.7/QQQ 4.8/IWM 2.2) all below lines → no
false fire.

**What's next.** RESTART server + refresh regime (rescan) to populate `overstretched_50` into market.json — until
then the arm reads None and can't fire. NOT yet built/synced to swinghelper. Burry re-verify the firewall. Forward-
test the 5/7/7 lines live and tune. (Backup: `backups/2026-06-09_idx-overstretch-shield/`.)

## 2026-06-09 — AVWAP overhead-EMA fire gate (the IREN "clear the 9-EMA" case)

**What was done.** Trader: IREN (Pullback @ AVWAP) — the 9-EMA ($60.64) sits just above the AVWAP support
($58.95), so the plain reclaim+spin fires ~2.5% UNDER the 9-EMA, buying into overhead. Wanted: for AVWAP
setups, fire on a 5-min CLOSE above the overhead 9-EMA ("no retest"), stop STILL under the AVWAP. Quant added
`_avwap_overhead_gate` + a gated branch in `compute_now`'s is_avwap path: when an overhead daily 9/21 EMA sits
within ~0.5×ADR above the AVWAP support, the buy fires on a completed 5-min close above that EMA (buyers_confirm
kept; AVWAP retest/spin bypassed), entry at the clear, stop just under the AVWAP, ≤1×ADR guard. is_deep + the
ungated AVWAP reclaim path byte-for-byte unchanged. New trigger tag CLEAR_9EMA.

**Burry CRITICAL FAIL → fixed.** Quant anchored `oh_ema` to the LIVE price — so the instant price closed above
the EMA and kept rising (the trader's exact "straight up, no retest" case), the gate de-qualified BEFORE the
first fire could freeze, and the name fell through to the reclaim path at a WORSE entry above the EMA. Fix
(Burry's prescription): anchor `oh_ema` to the AVWAP SUPPORT (a stable daily level), not the live price. Now
the gate holds through the close-above and fires correctly. Rewrote the test that pinned the bug
(`test_gate_stays_on_when_price_runs_above_ema`) + added `test_fires_straight_up_no_retest`.

**Result.** IREN: armed below the 9-EMA → fires on a completed 5-min close ≥ ~$60.82 (60.64×1.003), entry at
the clear, stop ~$58.66 (under the AVWAP), risk ~0.4×ADR. Corrected blast radius: **58 AVWAP-family names** have
a near-overhead EMA → new clear-the-EMA mechanic (the buggy live-price anchor under-counted to 17); 56 unchanged
(EMA far / none above support). Suite **45/45** (10 new gate tests). Goldens unaffected (live-path, firewalled
from grades). Backup backups/2026-06-09_avwap-overhead-ema/. Needs restart+rescan + live session to fire.

**Edge-case fix (trader, same day): no chasing a gap.** With the support-anchored gate, a high-ADR name
gapping +5% pre-market above the 9-EMA would still pass the stop-≤1×ADR guard (ADR 9%) and FIRE a chase at the
gap. Added a `reached_ema` gate: a FRESH fire now requires the day-low to have TAGGED the 9-EMA today (ADR-scaled
buffer, same as reached_av/reached_50) — a genuine cross-from-below OR a pullback that RETESTS the EMA, NOT a
gap-and-go. Gap-and-go -> `too_extended`, "waiting for a retest of the 9-EMA"; gap-then-retest-and-bounce ->
fires near the EMA. Strictly TIGHTENING (can only prevent a chase). Tests `test_gap_and_go_does_not_fire` +
`test_gap_then_retest_the_ema_fires`. Suite 47/47.

**Risk/honesty.** Confirmation-engine change (entry timing), not a backtested R-edge — can't be A/B'd in the
daily-bar blind harness. Faithful to the trader's explicit rule, verified mechanically.

## 2026-06-09 — Confirmation WATCH widened to B AVWAP-family setups (the IREN case)

**What was done.** Trader asked why IREN (Pullback @ AVWAP, RS 88, grade B) never fires a buy notification.
Root cause: the live confirmation WATCH gate (`compute_now`, ~line 3330) admitted a B leg only when
`worth_waiting` (= Deep Pullback / Consolidation). AVWAP-family pullbacks aren't worth_waiting, so a B AVWAP
leader was never even armed → no beep. Fix: extended the strong-B exception to the full PATIENT family —
`_patient_b = worth_waiting OR "AVWAP" in setup_type` (mirrors grading's `patient_quality`). Breakouts/EPs +
plain "Pullback" keep the strict A/A+ gate.

**Result.** IREN now admitted to WATCH (was excluded). Blast radius: 18 AVWAP-family names newly watchable,
zero non-patient leakage. 13 are C-HEADLINE names with a B AVWAP *leg* (breakout leg drags the avg) — admitted
on the B leg, consistent with the worth_waiting precedent (keys on leg grade). Surface self-bounded by the
existing `cands[:18]` shortlist cap. **Only widens WATCH, not BUY** — the reclaim + buyers_confirm + stop-≤1×ADR
FIRE gates are untouched; AVWAP routes to the is_avwap reclaim branch as before. Suite 35/35 (watch gate is
live-path, not golden-covered). Backup in backups/2026-06-09_watch-avwap-b/.

**For the trader: IREN's actual buy trigger** = a RECLAIM of the earnings-AVWAP (~$58.95), NOT a breakout:
price tags the line → a 5-min candle CLOSES ≥~$59.13 (0.3% above) → turning up off the lows → buyers_confirm
(≥2 green 5-min closes over a rising 5-min 9-EMA) → stop ~$56 (≤1×ADR under the AVWAP). Beeps once, locks price.
NOTE: IREN grades B; the watch widening is what lets a B AVWAP arm at all. Needs restart+rescan + the live
session to fire.

## 2026-06-09 — Setup score: leadership gate + RS/TT credit (the CIFR-low / NXT-high case)

**What was done.** Trader caught the engine grading a clean leader (CIFR: RS94, TT-pass, riding the 10-EMA)
a C while a choppy non-leader (NXT: RS60, TT-fail, knifed 9% below its 10-EMA) was a B. Two root causes,
both fixed (quant-built, Burry-verified):
1. **`strong_leader` gate was absolute-gain only** (`above_200 and (p6m>=30 or p3m>=30)`) — no relative
   strength, no trend-template. 27–61 of ~45–97 "Deep Pullback" names were non-leaders (MRNA RS9, SATS 41,
   NXT 60…) collecting the +4 deep bonus + the patient A-path. FIX 1: a provisional Deep Pullback now must
   pass **rs_pct≥70 OR trend_template**, else it's re-analyzed `force_no_deep=True` and falls to its natural
   type (loses the premium). Implemented in `scanner._attach_rs` (the post-loop where the cross-universe
   rs_pct is final — the only place both gate inputs are real), via a new `analyze(force_no_deep=…)` param +
   re-call (no logic duplication). `_RECLASS_FIELDS` copies the fallback fields; `reclassified_from` audit tag.
2. **Raw `score` had ZERO RS/TT term** (they only entered at the 14%-weight grade layer). FIX 2:
   `_rs_score_term(rs_pct, tt)` = `(rs_pct-50)/15` capped +3, plus +1.5 for a TT pass. CIFR +4.6; NXT ~0.

**Result.** CIFR: Pullback (correctly not deep), score 10.9 → **15.3, grade C → B** (the trader's call).
NXT: **Deep Pullback → Breakout**, score → ~6, **grade B → D**. Deep Pullback count 45 → 18 (live ~97 → 36);
every kept name is a genuine leader (TSEM 96, AXTI 93, LUNR 92), zero leakage. Grade blast radius: **65
upgrades (all RS 89–99 leaders), ~19–61 downgrades (all reclassified non-leaders) — zero leader downgraded,
zero non-leader upgraded.**

**Burry verdict: CONDITIONAL PASS → both items closed.** (a) Firewall byte-for-byte (analyze default path
unchanged → goldens still valid, NO regen). (b) No stale fields after reclass (worth_waiting/chase_exempt/
why/entries all flip correctly; app.py:1432 double-checks worth_waiting). (c) No lookahead (forward-test +
backtest re-analyze use as-of slices). FIXED: **BUG B (idempotency)** — `_attach_rs` now stashes `score_base`
once and recomputes, so a 2nd pass can't stack the +4.5. CLOSED: **coverage gap** — new `tests/test_leader_
gate.py` (4 tests: RS-term bounds, idempotency, reclass-on-fail, keep-on-pass). Suite **35/35**.

**What's next.** Restart + rescan to go live (suggestions.json is pre-fix). Forward-test the new leader gate.
Open (trader call): NXT/FN landed B→**D** (two grades), not C — defensible (non-leader knifed below 10-EMA →
plain Breakout far from trigger) but the softer lever is the Breakout score path if wanted. rs_pct boundary
names (HBM RS69) sit one point under the gate — live rescan is ground truth.

## 2026-06-09 — Breakout pivot: de-wick + recent base (the INTC case)

**What was done.** Trader flagged INTC's breakout trigger sitting ~15% away ($126.64) when the actionable
base tops ~$113. Root cause: `base_h = max(high[-10:])` in scanner.analyze used the raw intraday high over a
fixed 10-bar window, so (1) a single rejection/distribution bar's spike set the pivot — INTC's May-29 bar
opened 123.8 and CLOSED 114.7 (a fade), yet its 126.64 high became the "consolidation high"; and (2) the
fixed window reached back into the prior leg's distribution after the stock re-based lower.
New `_breakout_pivot(o,h,l,c,adr_px)` helper — "de-wick + recent base" (user's choice):
  * RECENT BASE — start the window AFTER the most recent sharp leg-down close (>1.3× ADR down day), bounded
    [4,10] bars, so it measures the coil the stock is in NOW.
  * DE-WICK — a bar that closed in the bottom third of its range contributes its CLOSE, not its spike high.
Wired into the breakout primary entry only (line ~1160); stop already pivot-anchored (LUNR fix) so risk
stays ≤1× ADR. Guard added: if the de-wicked base high ≤ close, trigger = today's high. "% to trigger" in
the why now reflects the actual entry, not the stale `dist_hi`. Classification inputs (base_h/tight/dist_hi/
rng15/EP gate) deliberately UNTOUCHED → setup types stay byte-for-byte.

**Result.** INTC $126.64 → $113.14 (2.6% to trigger, zone 113–115, on the EMAs — matches the trader's read).
Blast radius across 479 breakout/EP names: **309 entries moved, ALL nearer (downward)**, 170 unchanged. Big
movers (ZS 191→151, MSTR 167→136, RKLB 151→123) are post-crash names whose old pivots were pre-crash spikes —
new pivots are real recent swing highs. 0 entries below close (3 at-close edge cases fixed by the guard).

**Decisions made.** Scoped to the breakout PIVOT only — left base_h/classification/scoring alone so setup
types don't shift (firewall). Did not touch the patient-setup `_pullback_leg_pivot` (already nearest-overhead).

**Burry (qa) verdict: PASS (both changes).** Firewall confirmed: 792/792 cached names — setup_type byte-for-
byte unchanged; 309 breakout entries all moved DOWN (nearer), 0 up, 0 below close. Edge-case audit of
`_breakout_pivot` clean (empty/single/flat/no-sharp-down/min_base clamp all safe). Grader change provably
MONOTONE (0 downgrades) when theme state is held fixed — a naive backup-vs-now diff showed phantom downgrades
only because themes.json was updated today (206 new assignments); not a code effect.

**Golden snapshots REGENERATED** (`make_golden.py` + `make_grade_snapshot.py`, both re-baseline on fresh bars
by design) after the PASS — full suite now **31/31 green**. Backups of the old goldens in the backup dir.

**What's next.** (1) ONE residual Burry flagged: TCGL (halted stock — zero volume, flat bars, ADR 0) yields
entry==stop / risk_ps=0 in the analyze breakout path; harmless (sizing guarded, won't arm) but degenerate —
spawned as a follow-up task (add `if risk_ps<=0: skip breakout entry`, mirroring `_breakout_plan`). (2) Forward-
test the nearer triggers. (3) Live grade-letter impact lands on the next full scan (grade_suggestions runs on
stored items).

## 2026-06-09 — Grade: broad-correction de-bias widened + plain-pullback C-cap relaxed (the CIFR case)

**What was done.** Trader flagged CIFR (RS 94, clean Stage-2 leader, shallow pullback) graded C and traced it
to its sector (theme HPC) tagged "Falling" while the WHOLE tape was pulling back (posture 45). Two real
defects + one intentional loosening, all in `grade_suggestions` (app.py):
1. **broad_correction trigger** now counts **Falling + Slowing**, not Falling-only. Today's tape was 61%
   Falling (under the 65% trip) but 82% Falling+Slowing, only 2/38 sectors Rising — obviously broad, but the
   de-bias never fired. Slowing is the same market-wide cooling.
2. **de-bias scope** extended from patient-at-support setups only → patient setups **OR** a top-decile-RS
   leader (rs_pct ≥ 90). A plain pullback on an RS-94 leader no longer eats the −10pt Falling penalty when
   the tape is the thing falling.
3. **plain-pullback C-cap relaxed**: a top-decile leader (rs_pct ≥ 90) on a plain pullback in a MILD tape
   (posture 40-49) now caps at **B** (was forced to C / CAP_PLAIN_WEAK 52). Real corrections (<40) and
   weaker names still cap at C. Deep-pulls/AVWAP keep their own A-path untouched.

**Result.** CIFR 52 → 61 (still C, now on composite merit — its shallow-pullback *setup-structure* score
43/100 is the only thing under B, NOT force-capped). Blast radius across 795 graded names: **20 upgrades,
0 downgrades** (firewall: a de-bias only lifts). 5 C→B (NXT/FN/CRWV/FLY/SATS), 15 D→C. Burry-verified the
code is provably MONOTONE (0 downgrades) with theme state fixed — see the breakout entry above for the full
qa verdict; SATS/FLY's C→B is partly driven by new themes.json assignments, not the rubric edit alone.

**Decisions made.** Did NOT inflate the setup-score mapping to push CIFR to B — that's curve-fitting one
name (methodology lock). CIFR earning a 61 high-C on a shallow pullback is correct.

**What's next.** OPEN: 3 of the 5 C→B upgrades are weak-RS patient deep-pulls (NXT 60, SATS 41, FN 65) lifted
by the wider broad_correction trigger — decide whether the broad-correction patient path needs an RS floor.
Burry (qa) re-verify the intended deltas. Forward-test the relaxed plain-pullback B in live tape.

## 2026-06-09 — Competition bots: real-time intraday exits (live-exit lag fix)

**What was done.** Fixed the live-path exit lag the trader flagged (ROADMAP item). `run_bots_live` (bots.py)
previously only closed on a live hard-stop hit; a midday +R target blast or time-due exit lagged to the next
EOD pass (unrealistic for a "real-time" leaderboard). Now the live path runs the full price-touch / bar-count
exit set against the live quote: **hard stop** (existing) · **+R target** (fills at the target level, not the
overshoot) · **time_stop** · **rebalance** — with `bars_held` derived from the index session count (gap-aware).
**Decisions.** (A) The EMA/Donchian **trail stays close-confirmed (EOD-only)** — trader's explicit choice;
firing a close-under signal on an intraday tick would whipsaw out a name that dips under the 9 EMA midday but
closes back above (respects the locked 9/50-EMA close-under rule). (B) Fill-day guard kept (no same-bar exit).
Verified in isolation (target/time/stop fire at correct prices; trail + fill-day correctly do NOT exit; live
`competition.json` untouched — temp-file tests). LOCAL-only, firewalled from grades.
**Diagnosis of the "no closed trades" symptom:** NOT a bug and NOT this lag — every bot position had `fill_date`
= today with `bars_held=0`, and the engine correctly never exits on the fill day. First EOD closes expected ~06-09.
**What's next.** Restart the server (bots.py is imported — running process holds the old module). Burry (qa) to
verify the bot equity math. Then watch the Competition → closed-trades table populate live.

## 2026-06-08 — Forming-bar buyable fix (no false BUY at the open) + Telegram/Gameplan armed-list parity

**What was done.** Two bugs the trader caught live (war-pause relief gap; AXTI deep pullback).
- **Forming-bar `buyable_now` fix (engine).** During a live session the daily series' last bar is today's
  STILL-FORMING bar (close = live price). `_respected_bounce` (the falling-knife gate on `buyable_now`)
  keyed on `c[-1]`, so a green intraday pop "un-broke" a 50 EMA that yesterday's SETTLED close decisively
  broke → flashed "BUY NOW" on a knife with zero confirmation (AXTI: closed 89.04 vs 50≈92.15, opens ~94.5).
  Fix: `analyze(..., forming_last=False)` new param; when True the respected-bounce gate runs on the SETTLED
  series only (drops the forming bar) — buyable reflects settled structure, and the live **confirmation
  engine** (50-reclaim + spin + buyers, on completed candles) owns the real intraday call. Threaded via
  `scan(..., forming_date=...)` + new `_session_today_if_open()` (None after close ⇒ EOD unchanged). Wired
  into `run_scan`, `run_intraday_partial`, `/api/analyze`. Per-ticker (stale/halted name → unaffected).
- **Decisions.** (A) knife-below-50 shows ARMED, never auto-buyable; confirmation engine is the single source
  of the BUY. Confirmation engine left untouched (already uses completed 5-min candles + buyers_confirm).
  "Doesn't retest, just runs" = MISSED setup (correct) — breakout alt leg + stale re-base already handle it.
- **Burry (qa): SHIP IT** — grader 7/7 byte-for-byte (buyable_now doesn't feed grade/score; feeds live
  `timing` only, as before), bug reproduced & closed, no false-negative on a settled next-day jump, edge
  cases (len<4, forming_date=None, stale ticker) clean, caller isolation confirmed (backtest/bots/tools/
  analyze_at untouched). **Server restart required.**
- **Telegram + Gameplan armed-list parity.** Trader's morning brief showed 6 of 11 armed (silent `armed[:6]`
  cap dropped VICR/NXT/LITE/APLD/VIAV). Headers now show the true count `(N)` and collapse only a long tail
  to "+N more" (caps: buys 10, armed 15). Matched `compute_gameplan` caps so all 3 surfaces agree (parity rule).
- **ADR-scaled `reached_50` (live confirmation engine).** Trader (VICR/BE) found the deep-pullback "tested the
  50" tolerance was a FLAT 0.5% — far too tight for a high-ADR leader (VICR 8.9% ADR, low 0.85% off the 50, and
  BE 8.5% ADR, low 0.70% off → both wrongly read as "no-man's-land, didn't tag the 50"). Changed
  `reached_50` to `_dlo <= e50*(1+max(0.005, 0.0015*adr_pct))` — i.e. ADR-scaled (0.15×ADR), consistent with
  the EOD `_respected_bounce` buffer; low-ADR names keep the 0.5% floor. Widens "did it visit the zone" ONLY;
  the turning_up + buyers_confirm + stop-≤1×ADR gates still reject a crashing knife and govern the actual fire
  (verified: VICR/BE register reached_50 but stay ARMED while red/at-lows). **Burry: SHIP, 6/6 PASS.** Loaded
  live mid-session at trader's choice. FLAG: `swinghelper/app.py` still has the old `e50*1.005` — sync next build.
- **Watch-stability for patient setups (live confirmation engine).** Trader: top deep-pullback leaders
  (BE/POET/NXT, rank 2/4/9 by score) vanished from the ARMED panel. Cause (verified, NOT the buffer change —
  the buffer runs downstream of the grade gate): the watch shortlist (`cands`) was gated A/A+-leg-only, and a
  leader's A leg FLICKERS to B on live intraday prices (RS dips as a high-beta name sells off). Fix: admit
  B-grade legs to the WATCH when the setup is patient (`worth_waiting` = Deep Pullback / Consolidation) —
  `grade in (A+,A) or (worth_waiting and grade==B)`. WATCH is a lower bar than BUY; the reclaim+buyers_confirm+
  stop-≤1×ADR gates (which never read grade) still govern the FIRE. Breakouts/EPs keep the strict A/A+ gate.
  best-leg sort still picks the dip-buy leg. **Burry: SHIP, 7/7 PASS** (isolated, only-widens, correctly scoped).
  Forward-watch: if beep frequency climbs >1-2/session, add a `rating>=68` floor to the B clause (weak-B tail
  FORM/SEI/etc at 63-67); not shipped now. FLAG: sync swinghelper/app.py for both app.py changes next build.
- **AVWAP reclaim+spin confirmation branch (live engine).** Trader: AVWAP reclaims should fire on the RECLAIM
  of the AVWAP + spin (like the deep-pullback 50-reclaim), not the generic ORH/EMA-cluster break. Added a NEW
  `is_avwap` branch in compute_now mirroring the is_deep logic on the AVWAP support line (= the dip-buy limit
  leg's entry): reached(ADR-scaled buffer)+held_above+turning_up+buyers_confirm+stop-≤1×ADR, stop just under
  the AVWAP, confirm_trigger RECLAIM_AVWAP. Covers "Pullback @ AVWAP" / "AVWAP reclaim (ATH)" / "(earnings)".
  is_deep block left BYTE-FOR-BYTE (guards extended to `is_deep or is_avwap`, False for deep). _avwap_dip=None →
  graceful fallback to the generic path. **Burry: SHIP, 7/7 PASS** (deep byte-for-byte verified, branches mutually
  exclusive, knife-protection holds). Trader chose load-live mid-session. FLAG: sync swinghelper next build.
- **Breakout 2nd-entry + stop-under-pivot — SHIPPED LIVE (grade-affecting; golden re-baselined).** Simons built
  it in isolation (`dev/breakout/`); **Burry SHIP-gate verified** (clean 4-location diff — nothing leaks into
  deep-pullback/AVWAP/pullback/grading; 352 grade shifts ALL **D→C/C→B, ZERO A/A+** in or out; clean breakouts
  unchanged; Part A stop clamped [0.3,1]×ADR; Part B pivot no-lookahead). Applied `dev/breakout/scanner.py` → live
  `scanner.py`; **golden re-baselined FROM EXISTING FIXTURES** (isolates the engine change — diff shows only the
  breakout stop moving day-low → "just under the pivot", risk_ps tightening ~10×, entry_quality rising). test_grader
  **7/7**. **NEEDS RESTART + RESCAN.** Forward-test now runs live per the plan. (`scanner.py.pre-breakout-ship`,
  `golden_analyze.json.pre-breakout-ship` backed up.)
- **VIX classifier fix — SHIPPED LIVE (macro layer, firewalled from grades).** Root cause: the `level >= 20`
  gate dropped an elevated post-spike VIX (19.74, +23% 5d, +14.8% vs MA20, after a +40% Fri spike) to 'calm' — the
  bug behind the "green light, VIX calm" gameplan that contradicted the 09:05 risk-off alert. Fix: 6-state classifier
  anchored to **vs_ma20** (relative, per "level is noise" research), adds an **'elevated'** state; +3 app.py consumers
  updated ('elevated' label/dir/lean-dict — the lean dict was a hard lookup that would KeyError on a new state — and a
  gameplan narrative branch). Grader **7/7 byte-for-byte**; today's VIX now classifies **'elevated'** (was 'calm');
  **defend UNAFFECTED** (reads raw vix numbers, not the state label). Burry formal verify running. Surgical (only the
  vix_trend classifier + the 3 consumers); did NOT wholesale-copy dev/vixfix (it predated the breakout ship). Needs restart.
  **Burry PASS** (grader 7/7, all 3 backend consumers handle 'elevated', classifier correct across 6 cases, defend
  unaffected) — AND caught + fixed a FRONTEND gap I missed: `web/app.js` `vixStateColor/Icon/Label` (~1436-1447) had
  the old 5-state dicts (no 'elevated' → grey/wrong label, not a crash); Burry added 'elevated' (browser-refresh only,
  no restart). **swinghelper KeyError vector for next build:** `swinghelper/app.py` (~3810) hard lean-dict + `swinghelper/web/app.js`
  still 5-state — scanner.py + app.py + web/app.js MUST sync TOGETHER or the hosted site KeyErrors on 'elevated'.
- **Card order now GRADE-first (Live entries / Gameplan / Telegram).** Trader: cards weren't strictly by grade.
  `compute_now` sorted buys/armed by `rating` (number), but grade CAPS (below-200/parabolic/mild-pullback) break
  rating↔grade monotonicity, so a capped-down high-rating name could sit above a true A. Added a STABLE grade-rank
  sort (A+→A→B→C) after the cands loop — rating-order preserved within each grade. Pure display order; grader 7/7,
  no grade/engine change; fixes all 3 surfaces at once. Needs restart.
- **Fixed the "restart but nothing changes" friction (trader pain).** Diagnosed live: assets are ALREADY served
  no-cache; the real causes were (a) reopening the Edge app-mode window doesn't RELOAD the page, and (b) a stale
  process keeping port 8765 so a relaunch can't bind → old code keeps serving. Two fixes: (1) **auto-reload on
  restart** — server `BOOT_ID` (per-process, in /api/health); the client polls it every 20s and `location.reload()`s
  when it changes, so an open window picks up new code on its own. (2) **run.ps1 now kills the stale 8765 owner
  first**, then relaunches (leaves the coach_app.py tray alone). Compile/grader 7/7, JS `node --check` clean.
  Bootstrap: ONE manual Ctrl+Shift+R after the next restart to load the new app.js; auto-reload is automatic after that.
- **Scan freshness — session-aware cache cap (trader-found).** The manual "Scan" button (`runScan`) doesn't send
  `?fresh`, so `run_scan` fell to the 12h cache → could grade on this-morning's/yesterday's bars intraday. (The
  AUTOSCAN was already fine — verified cache ~5–30 min fresh; live triggers use fresh 5-min bars+quotes.) Fix:
  `run_scan` now clamps `max_age ≤ 0.5h` whenever a session is in progress (`_session_today_if_open()`), so NO scan
  path can serve badly-stale bars intraday — reuses the autoscan's recent cache (no extra Yahoo load), refetches
  older. After the close the 12h cache stands (bars settled). Grader 7/7 (uses fixed fixtures, not get_bars). Needs restart.
- **SESSION CLOSE (server restarted on the new code — boot_id 1780944349, VIX reads 'elevated', one server PID
  52416 + coach tray; stale processes killed).** ONE-TIME on the trader's window: `Ctrl+Shift+R` to load the new
  `app.js` (auto-reload + "Took it" removal + grade-first cards); after that, auto-reload is automatic.
- **What's next / open items.**
  - **swinghelper is BEHIND on ALL of today's changes** (scanner.py, app.py, web/app.js) — sync them TOGETHER next
    build or the hosted site KeyErrors on the new 'elevated' VIX state.
  - **Forward-test live** (the real judge): breakout 2nd-entry + stop-under-pivot, VIX 'elevated', RS80→A.
  - **Open ROADMAP:** chart-bug "jumps to dashboard" is hosted-specific (verify on the hosted site — local works on
    every path); competition bots' live-exit only stops out (add trail/target/time intraday); the "DO FIRST" chart
    item is effectively the hosted chart-bug. Forward-watch
  AXTI: stay ARMED, fire only on a real 50-reclaim+spin. **swinghelper is behind on ALL of today's scanner.py +
  app.py changes — sync next build.** Backup: `backups/2026-06-08_respected-bounce-forming-fix/`.

## 2026-06-08 — Grade rubric v6 BUILT (test-clean) → synced to swinghelper, awaiting trader push

**What was done.** Resolved the build-block HONESTLY (no test gutting) and ran `make-build.ps1` → all 31 tests
pass, synced to `swinghelper/` (NOT committed/pushed — trader publishes via the printed git commands; Render
auto-deploys on push). Trader override: ship LITE→A / MNDY→C to the friends' site to match local.
- **The 3 failures were resolved by two principled refinements (new `rubric.py` constants):**
  - `BROAD_CORR_MIN_SECTORS = 8` — `broad_correction` now needs ≥8 sectors, so a 1-sector test heat (or a
    degenerate real heat) can't trip it. Fixes `falling_group_bypass_requires_top_decile_rs`: a SINGLE Falling
    group is no longer a "broad correction", so an RS80 there stays capped (NOT A) — while real-market LITE
    (28/38 Falling) still bypasses. The invariant and the trader's ask are BOTH satisfied; they were never
    actually in conflict (single group ≠ market-wide pull).
  - `CAP_PATIENT_MILD = 74` — patient leader in a MILD pullback (posture 40-49) caps at max A, never A+
    (=81 max-A − the +7 max strength bonus). Fixes the RS96→A+ regressions (A+ reserved for healthy tape).
- **Constants extracted to rubric.py** (single source of truth): `CAP_BELOW_200=62`, `CAP_PATIENT_MILD=74`,
  `BROAD_CORR_MIN_SECTORS=8`.
- **Live + shipped grades:** LITE/APLD/SITM/NXT/VICR/POET/AXTI pullback = A; DXYZ = B (round-trip guard);
  MNDY/HIMS/RDDT/TEAM = C (below-200 floor). Local server restarted → matches the build.
- **Burry (qa) sign-off on the SHIPPED v6: 6/6 PASS** — targets correct, NO A+ in sub-50 tape (0 legs ≥82),
  no junk in A, floor holds, min-sector guard works (1-sector→False/RS80→C, 38-sector→True/RS80→A), monotonic
  vs pre-refinement (0 up; INTC/LUNR/BE A+→A = the intended A+ reservation). Two non-bug notes: (a) the +7 RS
  strength bonus applies AFTER the cap, so effective maxes are 79/81 (still <82 A+ — rule holds; the "max B/A"
  comments describe pre-bonus); (b) a test-harness gotcha (must route via reverse_themes, not item["sector"]).
- **Still recommended:** forward-test the RS80→A rule live over coming sessions. Trader pushes when ready.

## 2026-06-08 — BUILD BLOCKED by test gate (floor+ceiling overturns a forward-tested invariant)

**What happened.** Ran `make-build.ps1` → its grader test gate ABORTED the build (working as designed). The
floor+ceiling batch fails 3 of 7 `tests/test_grader.py` cases. Diagnosed cleanly by swapping each backup:
- **de-bias ONLY (`app.py.pre-batch`): 7/7 PASS** — clean, independently shippable.
- **pre-all-grade (`app.py.pre-debias`): 7/7 PASS.**
- **full batch (floor+ceiling): 4 pass / 3 FAIL.**

**The 3 failures are real design conflicts, not sloppy bugs:**
1. `falling_group_bypass_requires_top_decile_rs` — asserts an **RS80 deep pullback must NOT reach A** (only
   RS≥90 bypasses). But the trader's ask THIS session is **LITE (RS80) → A** — which directly overturns that
   forward-tested invariant. This is the "RS>80" change Jim Chanos/critic flagged earlier; it needs its OWN
   forward-test before the test is rewritten.
2 & 3. `deep_pullback_leader_is_A_in_falling_group` / `..is_patient_not_regime_discounted` — RS96 leader at
   posture 45 now grades **A+(82)**, tests expect **A**. The ceiling fully uncaps the 40-49 band → top-decile
   reaches A+. Open question: should a leader at the 50 in a mild pullback be A or A+? (If A, cap the relaxed
   band at ~81 instead of uncapping.)

**Two real bugs in the batch were already fixed** (live in app.py): floor now fires only on `above_200 is
False` (not missing/None — was wrongly flooring test leaders); ceiling round-trip guard now scoped to the
40-49 band only (was capping healthy-tape ≥50 leaders too).

**Burry (qa) full-batch verdict: 5/5 PASS** — no junk in A (12 A-legs, 0 violators), below-200 floor holds
(0 below-200 names with a B+ leg), all 10 target tickers correct (LITE/APLD/SITM/NXT/VICR=A, DXYZ=B,
MNDY/HIMS/RDDT/TEAM=C), de-bias firewall intact (0 unexplained diffs when broad_correction=False), monotonic
(0 unexpected cross-effects). So the IMPLEMENTATION is verified correct — the only thing standing between this
and shipping is the deliberate RULE decision + forward-test, NOT a code bug. Burry's 2 minor notes: (a) two
below-200 AVWAP names (RDDT/HOOD) get a small WITHIN-C numeric uplift from de-bias+floor interaction (letter
unchanged); (b) AXTI sits right at the 60.8% round-trip boundary (reaches A via strength bonus anyway).

**State left for next session:**
- `app.py` = FULL BATCH, live locally (server running it → trader can still SEE LITE→A / MNDY→C). NOT built,
  NOT shipped, NOT committed. Behavior Burry-verified; build blocked ONLY by the 3 invariant tests.
- **De-bias is clean + Burry-friendly** — buildable on its own if desired (but that reverts LITE→B locally).
- **TODO before this ships:** (a) decide RS80→A and A-vs-A+ rules deliberately; (b) add a min-sector-count
  guard to `broad_correction` so a tiny test heat can't trip it; (c) update the 3 tests to the agreed rules;
  (d) forward-test; (e) Burry sign-off (his full-batch run was in flight). Backups: `2026-06-08_grade-batch/`.

## 2026-06-08 — Grade batch APPLIED + server restart (floor + ceiling)

**What was done** (trader: "restart… i cant see the update"; backups `2026-06-08_grade-batch/app.py.pre-batch`
= de-bias-only, `app.py.staged` = the applied batch)
- **Applied to app.py** the staged grade batch and **restarted the local server** (killed pythonw PID, relaunched
  `app.py --background`). Verified live via `/api/suggestions`.
  - **Floor:** below the 200-day SMA → `r = min(r, 62)` (max C) — after the leadership gate. Stage-2 rule.
  - **Ceiling:** patient cap threshold `posture<50 → <40`, plus a round-trip guard `pull_from_high ≤ 60`
    (else cap at B). Real leaders reach A in a mild pullback; >60% drawdowns can't.
- **Live result:** LITE/APLD/SITM/NXT/VICR pullback legs → **A**; DXYZ held **B** (74% pull); MNDY/HIMS/RDDT/TEAM
  → **C** (below-200). No below-200 name keeps a B+ leg; the only A-legs with deep pulls are RS≥90 elites
  (POET 95/+86%, AXTI 96/+636% — the intended explosive-leader case).
- **LOCAL ONLY — not built/shipped.** Burry re-running on the FULL batch (de-bias + floor + ceiling) as the
  pre-ship gate; forward-test still pending. Old de-bias-only Burry run superseded.
- Headline still = average of legs (so LITE headline reads B though its pullback leg is A) — flagged to trader.

## 2026-06-08 — Show ALL setups (stop hiding decided names) + RGTI/APLD un-flag

**What was done** (trader: "i want to see all setups"; data backup `2026-06-08_status-fix/`)
- **`web/app.js filteredSuggestions`** no longer hides decided names. Was: default queue filtered out
  `status==='taken'` and (without `showPassed`) `status==='rejected'`. Now: every setup stays visible,
  taken (✓) / passed (✗) keep their badge. 13 names were being hidden at the time (8 taken: MXL AEHR LUNR
  CRWV NXT FIX AAOI VIAV; 5 passed: SITM BE VRT CRDO FLNC — incl. the RS-88 SITM leader). Frontend-only →
  browser reload, no server restart. Logic-verified; couldn't live-screenshot (single-instance guard blocks
  attaching the preview to the running app) — trader to confirm on reload.
- **Cleared stale status flags:** RGTI (`taken`, but the trade is closed) and APLD (`rejected`, blank reason)
  removed from status.json so they rejoin the queue.
- **Staged, NOT built:** the patient-cap relaxation (posture<50→<40 for patient setups + ≤60% round-trip
  guard so DXYZ-type −74% names don't reach A). Blast radius measured (LITE/APLD/SITM/NXT → A-leg; DXYZ stays
  B). Held behind forward-test + Burry sign-off per trader — no second unverified rubric change back-to-back.

## 2026-06-08 — Broad-correction de-bias (the APLD/sector double-count)

**What was done** (backup `2026-06-08_gameplan-engine-sync/app.py.pre-debias`; trader-approved)
- **Problem:** in a market-wide pullback (today 28/38 sectors "Falling"), a leader's Falling-sector tag is
  just the market — already in `posture` — yet the rubric penalized it (sector factor 12) AND applied the
  cooling-sector cap on top. Double-counting the same correction buried genuine deep-pullback leaders
  (APLD RS84, VICR RS91/+187%/6m) at C. Investigation started from "why isn't APLD showing" — it was both
  `rejected` in status.json AND grade-capped.
- **Fix (`grade_suggestions`):** compute `broad_correction` once = ≥65% of sectors "Falling". When broad,
  for PATIENT at-support setups only (Deep Pullback / Consolidation / AVWAP): (1) lift the Falling sector
  factor off the floor to NEUTRAL, and (2) let them take the patient cap-bypass branch regardless of the
  RS≥90 gate. **FIREWALL:** when <65% Falling, `broad_correction` is False and both edits are no-ops →
  grades byte-for-byte unchanged.
- **Verified (fresh-subprocess A/B, posture 45):** provably **monotonic — 0 rating decreases, 0 downgrades**;
  **13 letter upgrades** (D→C / C→B), all quality semis/AI-infra leaders (VICR, FORM, FN, AMKR, APP, ONTO,
  CIEN, MTZ, MPWR, CLS, PWR, GEV, CRDO); **no new A/A+** (weak-tape `CAP_PATIENT_WEAK` still blocks A's in a
  correction — they lift to A only once posture ≥ 50). APLD pullback leg C(52)→B(72).
- **Note:** earlier "15 downgrades" reading was a same-process two-module cache artifact, disproved by the
  clean subprocess A/B. Engine change — should still get Burry (qa) sign-off + forward-test before fully
  trusted. Needs server restart.

## 2026-06-08 — Gameplan ↔ Live entries sync (bottom-line "no setups" bug)

**What was done** (backup `2026-06-08_gameplan-engine-sync`)
- **Bug:** the Daily Gameplan read *"No positions, no buyable A/A+ setups… doing nothing is the right move"*
  while the **Live entries** panel showed **6 armed A-grade Deep Pullbacks** (POET/INTC/LUNR/TSEM/RKLB/AXTI).
- **Root cause:** `compute_gameplan` filtered `buy_now`/`watch` on the **headline** grade, but the confirmation
  engine (`compute_now`) arms on the best **A/A+ leg** — so A-grade legs of B-headline names were invisible to
  the gameplan. Two surfaces, two sources of truth → they disagreed.
- **Fix:** `compute_gameplan` now derives `buy_now` (= CONFIRMED buys) and `watch` (= ARMED) from `compute_now()`
  itself — the exact list the Live entries panel renders. Added `_now_compact()` to map an engine rec to the
  gameplan's compact shape. compute_now already excludes held names + earnings and applies the regime gate, so
  the lists match the panel by construction. **Grades untouched** (presentation/synthesis layer only — no
  `grade_suggestions`/`scanner.analyze` change).
- **Verified live:** bottom-line now reads *"No positions and nothing in a buy zone yet — the plan is patience.
  Watching: POET, INTC, LUNR, TSEM, RKLB."*
- **Note:** needs a server restart to take effect (`app.py` change).

## 2026-06-08 — Plan-view v5 (compact, new font) + two render-path fixes + chart-button fix

**What was done** (trader-driven, mockup-approved via `mockups/plan.html`; backups `2026-06-08_pre-fullsite-v4`,
`2026-06-08_planview-v5`)
- **New font site-wide:** Plus Jakarta Sans (body) + Space Grotesk (labels/tickers) + JetBrains Mono (numbers),
  replacing Inter. Font `<link>`s in index/autopilot/panel heads; `--muted`/`--faint` lifted + new `--content`
  for readable context text (the dim-grey "blends into the background" fix, applied globally).
- **Plan view redesigned to v5** (BUY hero → 4-cell levels strip → "Why this setup" metric grid from real
  fields p1m/p6m/pull_from_high/volc → one clean thesis line → 📈 View chart button). Replaces the old flat
  Watch/BUY/Stop/… text dump. `app.py` passes those 4 metric fields onto the `/now` recs (text-only, grades
  verified unchanged).
- **Caught TWO separate plan renderers** (the trader spotted the app's was right-ish but the Live Coach was
  still old): (1) `app.js planHtml` — its "thesis" was dumping the full cryptic `pl.why`; now a clean derived
  one-liner; SIZE falls back to the plan's size text. (2) `panel.html planRows` (Live Coach) — was still the
  old `1·Watch/2·BUY` list; **rewritten to the v5 layout** to match.
- **Chart button no longer dumps you into the dashboard:** root cause was the `?chart=` deep-link (used by
  autopilot + Live Coach) loading the app at the default `view:'dashboard'`. Fix = a **chart-only mode**: when
  `?chart=X` is present, a new `chartOnly` flag hides the sidebar + `<main>` (via `x-show="!chartOnly"`) so ONLY
  the chart modal renders — no dashboard. Closing it reveals the app on **suggestions** (never dashboard).
  Autopilot + coach links open in a new tab (`target="_blank"`); the main app already used an in-place modal.
- Verified: `node --check` clean (app.js + autopilot + panel inline JS), grades firewall + grader suite PASS,
  CSS balanced. **Frontend = hard-refresh** (incl. the Live Coach window, which caches hard); the `app.py`
  metric pass-through wants a **server restart** to light up the WHY grid (degrades to "—" otherwise).

## 2026-06-08 — v4 design language applied to the WHOLE site (every screen, Lynch)

**What was done** (trader loved the v4 cards — "WAY better, that's my style" — wanted EVERY screen to match;
last cohesion sweep was too subtle/invisible, so this round hand-swept bespoke per-screen markup. Backup at
`backups/2026-06-08_pre-fullsite-v4/web/`)
- **Card v4 finalized** (round 2, trader-reviewed via the `mockups/cards.html` TSEM spec): ≥6 tags with
  **🏆 Leader + 🚀 Rising sector pinned first** (gold `.key` chips); **both setups identical** (full Buy/Stop
  stat cards + own status panel) **split by a divider**; **status in its own `.statusbox` panel** (state +
  condition + the Lexicon **"confirms on"** trigger chips — the confirm_menu that had gone missing); cleaned
  the double-hourglass; **bigger type** across the card.
- **Top bar** restyled to v4 (Filters / SIGNALS / SETUP / MOMENTUM / SECTOR chips, scan + auto-rescan controls,
  Hot-sectors strip — spring hover, accent active-state, spacing).
- **Every screen hand-swept to v4** via new shared helpers (`.v4-sub/.v4-eyebrow/.v4-row/.v4-divrow/.v4-table/
  .tabpill/.v4-tile/.v4-banner/.v4-seg/.v4-modal/.ctrl-btn`, all `@media(hover:hover)`-gated + reduced-motion):
  Dashboard (tiles/positions/banners), Learning Hub (tab pills + tables + tiles), News + Screeners + Strategy
  (tab pills, tables, list rows), the **Chart modal** + Take/Close/Position-calc modals (v4 modal surface),
  **panel.html** (Live Coach cells + action rows) and **autopilot.html** (buy/armed cards). HQ + Competition
  were already v4-quality (own dedicated CSS) — left by design.
- **STYLE.md** bumped to **Design language v4** (the law for future work).
- **Verified live** on :8765: `node --check` clean, 692/692 div balance, zero console errors, computed-style
  checks pass, **all 11 views zero horizontal overflow at 375px**. Frontend-only → **hard-refresh shows it,
  no server restart.**

**Open follow-ups (small):** the deep "Stats" forward-test internals still ride v4 *inheritance* rather than
the new table/row helpers (reads fine); and the "≥6 tags" target shows only genuinely-true tags (data-sparse
scans may show 4–5 rather than invent filler — trader to decide if he wants padding).

## 2026-06-07 — Card design language v3 + site-wide polish (Lynch, trader-approved mockup)

**What was done** (trader: "this isn't professional… too much text/clutter, basic font". Picked direction C via rendered mockups; backup at `backups/2026-06-07_pre-ui-redesign/web/`)
- **Designed in the open:** built 3 rendered card directions (`mockups/cards.html`, served via a `mockups`
  launch.json entry + the preview tool) → trader picked **C (modern fintech)**, refined with: 2-setup support,
  **Chart button replaces Watchlist**, verbose info (shares/risk/$/thesis) under a collapsed **"Details"**, a
  **tag expander** (important tags pinned, "＋ all tags" reveals the rest), and tasteful **micro-animations**.
- **STYLE.md "Design language v3" (LOCKED)** — codified the tokens (`--ease` spring, `--faint`), the card/chip/
  tag-expander/stat/status/alt/Details/button recipes, and the animation conventions (hover-grow, card lift,
  `pulse2` for live states, `fade2` for reveals, chevron rotate; honor reduced-motion; never animate layout on
  mobile via `@media (hover:hover)`).
- **Suggestion cards rebuilt** (`web/index.html`) onto v3 with full Alpine fidelity — header (sector/ticker/
  price/grade chip/armed pill), pinned-lead tag expander, `displayEntries` primary (Buy-zone/Stop stat cards +
  pulsing live status) vs compact alt block, Details collapse, Chart·Took it·Pass. All live states preserved
  (confirmed/buyable/armed/taken/stale/defend, confirm-menu, lexicon tags). Fixed a `$…/d/d` double-suffix.
- **Site-wide cohesion** — central v3 component set + global spring hover-polish on `.btn/.fchip/.badge/
  .card-hover/.scale-btn` in `web/styles.css` (reaches Dashboard/HQ/Competition/Journal/Watchlist/LearningHub/
  News/Screeners) + button polish in `panel.html` + `autopilot.html`.
- **Verified live:** 120 cards render, zero JS/Alpine console errors across all 9 views, no horizontal overflow
  at 360px (kept fluid — no fixed card width). **Frontend only → hard-refresh shows it, no server restart.**

**Optional follow-up:** the non-Suggestions screens inherited the motion/button/chip language but still use the
v2 `.card` surface (not the full v3 gradient `.card2`) — a deeper migration of those panels is a larger sweep,
flagged if literal visual parity is wanted.

## 2026-06-07 — Site-wide readability/UX redesign (Lynch) — bigger, higher-contrast type

**What was done** (trader: "I can't read half this stuff — the entire site"; backup at `backups/2026-06-07_pre-ui-redesign/web/`)
- **Central, no per-element hacks** — all fixes in `web/styles.css` so they cascade across every screen
  (Suggestions/Dashboard/HQ/Competition/Journal/Watchlist/Learning Hub/Coach/Autopilot) without editing the
  690-div `index.html`:
  - **Type scale:** root `font-size` 20→22px; pixel-locked Tailwind tiny classes overridden — `text-[8px]`→11,
    `text-[9px]`/`[10px]`→12, `text-[11px]`/`[12px]`→13 (nothing real-content below ~12px); body `line-height` 1.55.
  - **Contrast (the big one):** the ~289 dim greys in index.html — Tailwind `text-slate-400/500/600` — lifted
    centrally via `!important`: 400 `#94a3b8`→`#b8ccd8`, 500 `#64748b`→`#9dafbf`, 600 `#475569`→`#788da3`
    (hierarchy preserved, not flat white). `--muted` also lifted `#93a1b8`→`#a8b8d0`.
  - `panel.html` (Live Coach) + `autopilot.html` font sizes bumped to match.
- **Mobile safe:** app already caps the user zoom to 100% below 760px, so the 22px base + default 1.25× desktop
  zoom never overflows a 375px phone. `STYLE.md` updated with the v2 type-scale + contrast rules (LOCKED).
- **Only CSS/HTML touched** (no Python) → a hard-refresh (Ctrl+Shift+R) shows it; no server restart needed.
  The in-app zoom can now be dialed back toward 100% since contrast no longer depends on it.

## 2026-06-07 — Lexicon Phase 2 SHIPPED: tags drive the confirmation engine (Burry-verified)

**What was done** (the trader's "make tags USEFUL, not just shown" directive)
- **`lexicon.py`** — added `SETUP_CONFIRM_MENU` (per-setup confirmation menu, pure data) + `get_confirm_menu()`
  + `confirm_menu_text()` + `CONFIRM_TAG_LABEL`. The Lexicon vocabulary now defines *what confirms each setup*.
- **`app.py` `compute_now`** (the trade-critical confirmation engine) — additive only:
  - Every armed/buy rec now carries **`confirm_menu`** (what will confirm it) + **`confirm_trigger`** (what fired).
  - New **YH_RECLAIM trigger** (the Martin-Luk prior-day-high reclaim) for the pullback/AVWAP family via the
    existing `scanner.breakout_confirm(prior_high)` — fires only when the ORH path hasn't already confirmed, so
    it can never remove an existing confirmation; the 0.7×ADR guard blocks chasing; zone-drift / ADR-cap /
    buyers-confirm / overhead / frozen-price gates all still apply. Often a tighter, earlier entry than ORH.
  - `confirm_trigger` labels every path: RECLAIM_50 (deep) · YH_RECLAIM · EMA_RECLAIM · HOD_BREAK · ORH_BREAK.
- **UI** — armed cards show "⏳ confirms on: reclaim of yesterday's high or opening-range-high break".
- **Tests** — new `tests/test_lexicon_confirm_menu.py` (menu data + the YH_RECLAIM trigger on synthetic 5-min
  bars: fires / no-fire-on-wick / no-fire-when-extended), added to the build gate. **31 tests pass.**
- **Burry-verified PASS**: additive, no-regression on existing confirmations, grades firewalled, None-safe.

**Decisions made**
- YH_RECLAIM confirms on "whichever fires first" (OR, not AND) — it's the correct Luk trigger and a *tighter*
  (less-chasing) entry, so additive is right. Easy to gate to AND if the trader wants.
- Only wired triggers are listed in the menu (ORH/HOD/YH/RECLAIM_50). ORB15/PMH/DVWAP = Phase 2b (need feeds).

**What's next**
- ⚠️ **Restart the server** to load it. Live-session smoke: confirm a pullback actually fires on YH_RECLAIM
  intraday (built + verified offline; markets were closed today). Then Phase 3 (backtested grade weights).

## 2026-06-07 — Roadmap cleanup + Lexicon re-centered on USEFULNESS (trader direction)

**What was done**
- Trader called out "Phase-1-itis" (shipping a Phase 1 and abandoning the rest) → saved as standing rule
  `finish-features-no-phase1-itis`. Cleaned ROADMAP.md accordingly:
  - **Removed** the whole Chanos-audit post-verdict section (all closed/rejected/shipped): #7 phantom-stops
    "false positive", rejected #1/#4/#5/#6, and the #2 same-name re-entry **chase reminder** (trader: "I don't
    need someone to keep reminding me not to chase"). Also pulled the loss-streak nag + breakout-volume badge.
  - **Removed** shipped B1/B2 from the active bug list (→ CHANGELOG).
  - **Re-centered the Lexicon** entry on the real goal — the engine USING tags (Phase 2 confirmation-engine,
    Phase 3 backtested grade weights), not just display badges. Committed the full arc (M6–M9 → 1b → 2 → 3).
  - **Learning Hub** promoted from "deferred extras / not needed" to ACTIVE "finish phases 1–4 fully".
  - **Kept** (trader's call): Israel market Phase 2 + pre-market movers (both "later").

**Decisions made**
- Lexicon's value is tag-driven confirmation + grading, not decoration. Phases are gates, not stop points.
- No behavioral "chase/streak reminder" features — the trader manages that himself; the $500 cap is the control.

**What's next**
- Build, not defer: make Lexicon tags useful (confirmation engine first) AND finish the Learning Hub (1–4).

## 2026-06-07 — The Lexicon Phase 1 (M1–M5) SHIPPED — context badges, Burry-verified

**What was done** (team design: quant/Lynch/Burry/Graham/Munger + chief; backup at `backups/2026-06-07_pre-lexicon/`)
- **`lexicon.py`** — new leaf module (imports nothing from app/scanner/rubric, reads no files): a tag registry
  + pure detectors + `detect_all(item)`. **10 Phase-1 tags** (all display-only, status `detected`, zero grade
  weight): EP, 52WH_BO, STAGE2, VCP, RS_LEADER, BGU, YH_RECLAIM, TIGHTNESS, VDU, PARABOLIC.
- **Firewall** — wired into `grade_suggestions` as a pure append (one `lexicon_tags` key, AFTER grades finalize;
  reads the graded item dict, can't touch the grade). Proven by `tests/test_lexicon_tags_do_not_change_grades.py`
  (golden grade snapshot via new `tools/make_grade_snapshot.py`) + `tests/test_lexicon_detectors.py` (13 cases).
  Both added to the `make-build.ps1` test gate. Existing 7-test grader suite still passes.
- **UI (M5)** — role-colored badges on the main suggestion card (`web/app.js` `lexVisible/lexStyle/lexLabel`
  + `web/index.html` band): cap 3 + "+N more", tooltip = the tag's define; STAGE2/VCP/EP deduped against the
  existing dedicated badges. Live-data sanity: 233/793 names tagged, avg 1.6, max 5.
- **Burry caught + fixed a CRITICAL build bug:** `make-build.ps1` wasn't copying `lexicon.py` to swinghelper →
  the hosted build would have crashed on `import lexicon`. Fixed.

**Decisions made**
- Detectors read the GRADED ITEM dict (not raw bars), called from `grade_suggestions` (Burry's placement) — keeps
  `scanner.analyze` + its golden untouched and makes grade-safety structural.
- Dropped the EMA/VWAP **reclaim** tags + true **ATH_BO** from Phase 1 → Phase 1b: they can't be detected
  honestly from a single snapshot (no prior-bar-vs-line field; our 1y window ≠ true ATH). Honest beats more.
- Short-history guard rides on `trend_template` (needs 200 bars) since the item carries no bar count.

**What's next**
- ⚠️ **Restart the server** (port 8765) — the serve path re-grades on read (`app.py:4281`), so badges appear on
  restart, no full rescan needed.
- Phase 1 cont. (M6–M9): Live Coach chips, Telegram digest line, Autopilot pills, journal auto-tag.
- Phase 1b: true ATH_BO + reclaim tags. Phase 3 (gated): backtest-validated grade weights — grades frozen till then.

## 2026-06-07 — The Lexicon (Context Layer) spec'd + new feature ideas → roadmap

**What was done**
- Named & documented the trader's idea — **The Lexicon** (`strategy/lexicon.md`): a canonical vocabulary of
  every named setup/trigger/context/trap pattern + a runtime **Context Layer** that detects which apply and
  reuses them across setups, the confirmation engine, the grade, and the journal. Organized the trader's full
  taxonomy (~150 patterns) into 7 categories with a per-entry schema (tag/role/detect/feeds/status) + guardrails
  (grade-firewall, backtest-gated weighting, no double-count, earnings=catalyst-not-fundamentals, long-only,
  no-lookahead) + a 4-phase rollout (detect+display → confirmation menu → backtested grade weights → extend).
- ROADMAP.md now has a **BIG IDEA** pointer to the doc + a **NEW FEATURES** section (chief's net-new ideas:
  "why this grade" explainer, stalk mode, regime-aware playbook, inline setup win%/median-R, one-tap
  suggestion→journal, post-mortem auto-tagger) — the crew had leaned bug-heavy.
- Memory `lexicon-context-layer` added for cross-session continuity.

**Decisions made**
- The Lexicon is firewalled: a tag may DISPLAY immediately but never changes a grade until a blind no-curve-fit
  backtest proves edge + Burry verifies. Catalog ≠ grading.
- Earnings tags (EGU/PEG) are catalyst context, not a fundamentals filter — `no-fundamental-filter` stays in force.

**What's next**
- Phase 1: a `lexicon.py` registry (tag → detector + metadata) for the cheap unambiguous tags, shown as
  informational badges (zero grade change). Trader will extend the vocabulary over time.

## 2026-06-07 — Shipped B1 + B2 from the crew brainstorm (Burry-verified)

**What was done**
- **B1 — forward-sim primary trail 20→9-EMA.** `_sim_forward` & `_trail_results` (`app.py:2052`/`:2118`) set
  `pn = 50 if patient else 9` (was 20); docstrings at `:2095`/`:2369` corrected. "System Edge" now measures the
  9-EMA the user actually trades instead of a 20-EMA trail no one runs. Isolated to the forward-test path — does
  NOT feed grades (the grade nudge reads real closed-trade `result_r`, not the forward sim). r9/r20/r50 all still
  stored, so a future 20-vs-9 backtest is still possible.
- **B2 — realized-results grade nudge MEAN→MEDIAN R.** `compute_stats()` gained a `_median()` helper + a
  `median_r` field per setup; the nudge (`app.py:1522`) now uses `median_r` (avg_r fallback for stale cache).
  Resists fat-tail skew. **The nudge was already ARMED** (not dormant as first thought): Consolidation (n=9) and
  Pullback@AVWAP (n=6) both ≥5 closed. Live grade impact: **Consolidation nudges ~1 pt harder** (−2.05→−3.00,
  median −1.00 vs mean −0.68 — its typical trade is a clean −1R); Pullback@AVWAP ≈ unchanged; all other setups
  dormant (<5) = zero nudge.
- **Burry (qa) verified both** PASS — KeyError-safe, isolated, median correct for odd/even/empty, no double-count,
  impact numbers confirmed against `data/trades.json`. He caught one stale docstring (now fixed).

**Decisions made**
- B1 was a straight alignment to the live 9-EMA rule, not a re-derivation. If a backtest later argues 20 is the
  better trail, that's a separate strategy call (r20 still recorded).
- B2 intentionally moves Consolidation grades down ~1 pt — accepted as the more honest "typical trade" read.

**What's next**
- ⚠️ **Restart the app server** (port 8765) for both changes to take effect — code edits to app.py/rubric.py.
- Remaining NOW items from the brainstorm: B3 market-staleness guard, overnight-exposure warning, same-name
  re-entry warning, loss-streak size banner, breakout volume badge. (Context being cleared — see ROADMAP.md.)

## 2026-06-07 — Chanos studies the masters + full-crew feature brainstorm → roadmap

**What was done**
- **Chanos studied the trading canon** (cheap/haiku research agents): distilled the Market Wizards series,
  Minervini SEPA/VCP/Trend-Template, O'Neil CANSLIM, Darvas Box, and the user's *Principles of Great Traders*
  PDF (76pp, fully extracted) into `strategy/masters/*.md` (4 files, ~1,360 lines). Then delivered **Critique
  Memo #001** (`journal/critic-log.md`) — 7 findings, evidence-cited.
- **Trader's verdict logged** in critic-log: #1 fundamentals REJECTED (→ memory `no-fundamental-filter`),
  #4 9-EMA exit REJECTED (shield mode + pullback regime context Chanos missed), #5 progressive-exposure
  ALREADY DONE ($500 cap = ¼ size), #6 volume MINOR/opt-in, #7 phantom-stops **FALSE POSITIVE** (verified in
  `trades.json`: the 7 `stop==entry` rows are legit breakeven exits, `initial_stop` preserved, R correct).
  Hardened critic.md: always verify against `data/*.json`, never the regenerated views.
- **Full-crew feature brainstorm** (Workflow `crew-feature-brainstorm`, 10 specialists + Munger, 50 ideas):
  scored into ROADMAP.md as a new "Crew Brainstorm" section (bugs + NOW/NEXT/LATER). Chief **code-verified
  the bug claims**: B1 (forward sim trails 20-EMA not 9 — ✅ real, needs quant decision) and B2 (nudge uses
  mean avg_r, arms at 5/setup — ✅ real) confirmed; B3 market-staleness likely; B4 overstated (R not corrupted,
  only a fat-finger upper-bound gap); B5/B6 unverified.

**Decisions made**
- Backup cadence corrected: ONE backup per NEW DAY, not per feature (memory `backup-before-changes` updated).
- Fundamentals/earnings filters are permanently OUT OF SCOPE — trend-following, not value.
- Nothing built this session — roadmap only; trader decides what ships next.

**What's next**
- Highest-conviction builds queued: **B1 trail decision** + **median-R nudge (B2)** + the behavioral guardrails
  (overnight-exposure warning, same-name re-entry, loss-streak sizer). All quant/risk-auditor, mostly S-effort.
- Engine touches (B1/B2) need Burry verification + no grade regression before shipping.

## 2026-06-07 — New crew member: Chanos 🐻, the critic (red-team / skeptic)

**What was done**
- Added an **11th subagent** — `critic` (codename **Jim Chanos** 🐻), a standalone skeptic whose only job is
  to find what we got **wrong**: pressure-tests our setups, the engine, the data, AND the user's live
  decisions against how the greatest traders actually operate (deep-reads *The Market Leaders*, *Principles
  of Great Traders*, Qullamaggie/Livermore/Soros/Druckenmiller/O'Neil, *Market Wizards*, etc.).
- **Evidence-only, no yes-manning, no reflexive contrarianism** — every finding ships with a master's
  principle + citation → our reality (file:line/trade/rule) → cost in R → fix/owner. Steelmans his own thesis
  before raising it; states plainly when we're actually right.
- Files: `.claude/agents/critic.md` (charter); `journal/critic-log.md` (his running memo, newest-on-top).
  Wired into the roster + sprint fan-out in `.claude/agents/README.md`, and into the **HQ tab**
  (`HQ_ROSTER` + squads list in app.py, `hqRooms` order in web/app.js — new "Critic" squad, DiceBear avatar).
  Memory `agent-crew.md` updated 9→11 (also backfilled Shannon).

**Decisions made**
- Left the existing 10 untouched (user's call) — Chanos is additive and independent, mandate = *everything*.
- Model = sonnet (judgment role, like qa/risk-auditor); he does his own web research rather than spawning.

**What's next**
- **Restart the app server** (port 8765) to see Chanos in the HQ tab — code changes need a restart.
- Take him for a test drive: "ask Chanos to critique our pullback grading vs Minervini's *Market Leaders*."

## 2026-06-07 — Doc architecture overhaul: researched, codified, applied (token diet)

**What was done**
- **Researched** proper CLAUDE.md / agent-doc architecture (official Claude Code memory docs + HumanLayer)
  and adopted the **3-layer progressive-disclosure** model: L1 index (always loaded) *orients & routes, never
  documents*; L2 topic docs load on demand; L3 = code/data/CHANGELOG.
- **CLAUDE.md → a 49-line index** (12.2KB → **3.25KB, −73%**). Trading ground rules, sizing formula, and the
  setup output-format template moved OUT to `strategy/my-rules.md` (read before every setup); slash-command
  docs removed; kept only the universal "always" guardrails + Context-Management loop + a router table.
- **MEMORY.md → 36 lines** (7.9KB → **4.1KB**). Added the 95%-confidence rule + Context-Management to CLAUDE.md.
- **PROJECT.md** 60KB → **8.3KB**: deleted the "Recent session history" table (duplicated CHANGELOG), moved the
  66-line feature catalog to new **`FEATURES.md`**, trimmed hosting to a pointer. Kept file-map + locked rules + gotchas.
- **Per-session memory files DELETED** (`session-2026-06-*.md` ×4, `next-task-forward-review.md`,
  `macro-regime-research-2026-06-06.md`) — dated history belongs in CHANGELOG only. Missing content migrated
  first → added `2026-06-06 (optimization + crew-roadmap)` and `2026-06-04 (forward-review + PROFIT GUARD)` entries.
- **ROADMAP.md** 26KB → 15KB; shipped items → `ROADMAP-archive.md`. Memory dir 184KB → **70KB (−62%)**.
- **New crew agent: 🗜️ Shannon = `token-master`** (`.claude/agents/token-master.md`) — owns context/token
  efficiency, separate from the optimizer (runtime). The full doc-architecture **standard is codified inside it**
  so we never re-derive it; memory pointer `doc-memory-architecture.md` + README updated (now 10 specialists).
- **Whole-tree audit** (not just the headline files): swept every doc + memory file against the standard
  and fixed the duplication/conflicts it found — retired the stale `journal/backtest-findings.md` (→ pointer
  to `swing-system.md`), redirected root `DEPLOY.md` → `swinghelper/DEPLOY.md`, de-duped grading (FEATURES →
  pointer to `scoring.md`), fixed the **10%-vs-15% max-position conflict** (10% operative, 15% ceiling) across
  PROJECT/FEATURES, fixed two stale `scoring.md` weight headers (15%→14%), trimmed PROJECT's locked-rules to a
  checklist + pointers, trimmed `lessons.md` + `backtest-validation.md` of stats duplicated in swing-system,
  deleted the superseded `screener-universe.md`, and archived the shipped Competition tab in ROADMAP.
- Backups: `backups/2026-06-07_token-diet/` + `…-2/`. (Risk-auditor PASS on the first batch.)

**Decisions made**
- **Always-loaded tax (CLAUDE.md + MEMORY.md) = 20KB → 7.4KB (−63%)** — paid back every turn.
- One fact, one home, by type: rules→domain file, dated history→CHANGELOG (query by date), features→FEATURES.md,
  plans→ROADMAP.md, lessons→memory topic files. **Prefer pointers to copies.** No per-session `.md` files, ever.
- `token-master` ≠ `optimizer`: context cost vs runtime cost. Standard lives in the agent so it's reusable.

**What's next**
- ⚠️ The `token-master` agent file is saved but the runtime registry loads it **next session** — Shannon can't be
  spawned until then (this round's memory cleanup ran via a general agent).
- Owner to review the 49-line CLAUDE.md before lock-in. No code touched — nothing to restart.

## 2026-06-06 (evening — Telegram upgrade · macro-layer rewire · Today-prediction fix)
- **Telegram bot upgraded (LOCAL-ONLY, free)** — inline action buttons on alerts ([✅ Took it]/[👀 Watch]/[❌ Pass];
  [🔴 Closed it]/[🟡 Raise stop]), a `/` command menu (`setMyCommands`: /setups /positions /pnl /brief /recap /regime
  /defend /size /help), auto **AM gameplan (~9:00 ET) + EOD recap (~16:10 ET) + defend-flip** phone digests (weekday,
  `briefing_enabled` toggle in Settings), and a `/size` 1%-risk sizer (longs-only + ≤1×ADR gated). `handle_callback_query`
  + `tg_send_buttons`/`tg_answer_callback`/`tg_edit_markup`; loop handles `callback_query`. **Took-it reuses the guarded
  `_chat_take`** (asks for your fill / refuses >1×ADR — never books a stale price). Telegram is **local-only forever**;
  its Settings UI is now `x-show="!hosted"` so it never shows on the friends' site.
- **5 latent trade-logging rule-violations fixed** in the chat path (`_chat_take`/`_chat_move_stop`): 1×ADR check on log
  (G1), no stale bar-close as a "fill" (G2), require a stop / no fake 0R (G3), preserve setup_type for the 50-EMA trail
  (G4), refuse a stop that widens risk (G5).
- **Macro layer rewired (blind-research-validated, Burry 7/7, grades byte-for-byte unchanged).** New `regime_signal()` =
  ONE canonical band+light+`risk_off` classifier (collapses the divergent threshold tables in compute_now/gameplan).
  **Defend mode now arms on `(extended AND weak) OR risk_off`** — the data-backed independent path (posture<30 / no index
  above its 50-day / VIX spike) — fixing defend turning OFF in a real correction (a correction bled −225R/−264R on
  breakouts in the study; pullback-at-support stayed flat → patient-50 holds exempt). Phase-0 wiring: `_effective_regime`
  carries `vix_trend` into the live PRE/POST regime (the VIX arm was dead live); gameplan passes `frothy` to defend (tabs
  agree). Research harness: `tools/research_macro.py` → `data/research_macro.json`. Rejected as noise: VIX-level bands,
  breadth-%, 200-MA count.
- **"Today" prediction fixed** — it printed "Likely up" while Overall said Risk-off. Causes fixed in `compute_prediction`
  DAILY block: capped the catalyst tilt (±0.6) so a few headlines can't outvote breadth; anchored the daily lean to
  `regime_signal` (no up-call in risk_off unless indexes are LIVE green); gated stale pre-market/catalyst files behind
  `_us_session_active()` (off-hours falls back to the regime base case). OVERALL state/frothy untouched → defend/grades safe.
- **Standing rules set:** Telegram is local-only/private forever; research runs on a cheap model (haiku). Roadmap task
  added: compact + route the always-read docs (CLAUDE.md/ROADMAP/PROJECT/MEMORY) to cut standing token cost.
- **Synced to the build** (`make-build.ps1`, test-gate 7/7) — NOT pushed (user pushes). ⚠️ **Restart the server** to load
  the Telegram + macro + prediction changes. Friend handout written: `UNIVERSE_AND_DATA_HOWTO.md`.

## 2026-06-06 (optimization + crew-roadmap batch)
- **Tier-1 performance + correctness** — `scanner.get_bars` in-memory bar cache keyed by file mtime (~0.09ms warm vs re-reading 800 JSON files); DST fix (`_et_now` manual EDT/EST offset — box has no tzdata so hardcoded −4 was wrong Nov–Mar); `analyze()` drops 0.0-OHLC glitch bars (was silently div-by-zero + drop); `close_trade` defaults `result_r=0.0` not None; scan/universe-rebuild mutual-exclusion guard (`UNIVERSE["running"]`/`SCAN["running"]`); `compute_stats` 15s TTL cache (was re-reading trades.json on every grade pass); `_FETCH_STATE` thread-local replaces racy global `_DID_FETCH`.
- **Golden-file test harness** — `tests/test_grader.py` + `tests/golden_analyze.json` + `tools/make_golden.py`. 8 frozen tickers byte-compared through `scanner.analyze`. Re-baseline ONLY via `make_golden.py` after a sanctioned change.
- **Money-leak #1 fixed: live-equity sizing** — `compute_equity` values open positions at live `fetch_quotes` price during RTH; `compute_now` sizes BUYs off `_equity_settings()` (real equity), not the static account figure.
- **Money-leak #2 fixed: zone-drift gate (the −8.73R leak)** — in `compute_now` confirmed-breakout path, a buy-at-support setup (Pullback/Pullback@AVWAP/AVWAP-reclaim/Consolidation) whose live price runs >½× ADR above the planned zone stays ARMED but won't freeze a chase entry. Breakout/EP/DeepPullback exempt.
- **Winsorized forward R** — `_agg_rs` adds `avg_r_w`/`total_r_w` capped at +10R (`FWD_WINSOR_CAP`). Surfaced as subnote on Forward-test Avg R.
- **>1× ADR stop flag** — `_adr_violation` helper; `adr_violation` field on item + each leg; ⚠ badge on card leg.
- **RS lookahead fix** — `_attach_rs` accepts optional `benchmark` param; `research_stage1` passes AS-OF index returns. ⚠️ `research_watch.json` needs regen (`python tools/research_stage1.py`) — not yet run.
- **UX** — staleness banner on Suggestions tab when scan date < latest session; gzip on `/api/*` responses >1.4KB (≈9× compression); dashboard top-suggestions strip `flex-wrap` + mobile line-2 for 375px.
- **Learning loop** — nudge audit (Consolidation −2.04, Pullback@AVWAP −2.19 confirmed); fixed "Deep pullback"→"Deep Pullback" typo; added `entry_vs_plan_pct` to closed trades; RKLB lessons added to `journal/lessons.md`.
- NOT shipped: grade-output response cache (conflicts with live-equity sizing), sector-heat members strip, app.py module split (needs golden-file gate first), intraday-equity live quotes (BUG-03).
- Burry-verified (both batches). ⚠️ Restart server + rescan to load. Backups: `backups/20260606-001545-pre-tier1` + `backups/20260606-005302-pre-crew-roadmap`.

## 2026-06-06 (VIX velocity · grader fixes · build gate · Learning Hub)
- **VIX velocity feature** — `scanner.vix_trend()` (a SIBLING of fear_greed, never folded into posture → can't
  touch grades) reads "panic building vs fading" via a 5-state classifier (calm/falling/rising/spiking/
  elevated-falling). UI strip under Fear & Greed; prediction narrative; defend-mode arms on a VIX spike
  (`_vix_spike`, rubric `VIX_SPIKE_*`). Live-verified on Friday's spike (VIX 21.51, +39.7% 1d → "spiking").
- **2 pre-existing GRADER BUGS fixed** (surfaced when posture dropped 60→45): Deep Pullback/Consolidation were
  missing from `pullback_setups` (40% regime-factor cut), and top-decile leaders were double-penalized by the
  "falling group" cap. AXTI restored C→A; 7 armed Deep Pullback leaders back. Firewall proven: VIX never moves grades.
- **Test harness + BUILD GATE** — `tests/test_grader.py` now 7 tests (golden + grader-behavior guards + VIX
  firewall + Learning-Hub smoke); `make-build.ps1` runs them before any sync and ABORTS on failure, plus a
  `strip_sizing` exit-guard so a strip failure can never ship the owner's account size.
- **🧠 Learning Hub (SHIPPED, local-only)** — merged the Armed Log + Stats tabs into ONE collect→compare→learn→
  improve page. New leaf module `learning.py` + unified event store (`learning_events.json`) + append-only audit;
  `compute_learning()` + `GET /api/learning`; one `learninghub` nav tab with a Today's Brief / Edge History toggle,
  a pinned vital-signs strip (incl. **System Edge** = forward winsorized R), a computed **daily lesson**, and the
  ⭐ **execution gap** (chased >5%-above-plan avg −0.73R vs clean −0.62R — the #1 leak, quantified). Old
  armed_history/forward_log WIPED to `.bak` (clean restart); trades.json preserved. Full-team designed; Burry-verified;
  375px-checked. Outcome-scorer / trade-linkage / grade-vs-outcome / histogram = deferred extras in ROADMAP.
- ⚠️ Restart the server to load. Learning Hub + Competition are LOCAL-ONLY (hidden in hosted) — no build needed for them.

## 2026-06-05 (late — deep-pullback engine overhaul + the Agent Crew/HQ)
- **Confirmation engine, deep pullbacks:** the live trigger is now the **50-EMA RECLAIM/BOUNCE + a spin** (price
  tests the 50, turns up off the lows ≥45%, and `buyers_confirm` fires) — replaces the old day-high break that
  fired in no-man's-land above the 50 (the AXTI $104.55 bug). Wording is now accurate: **reclaim** from below the
  50, **bounce** from above. Fixed a regression where any price above the 50 counted as "reclaimed" (false-confirmed
  crashers NXT/VICR/TER). All Burry-verified.
- **buyers_confirm gate** (`scanner.buyers_confirm`) — a level cleared by one candle in a sold-hard name is a knife;
  require 2 green 5-min closes over a turning-up 5-min 9 EMA. **respected-support bounce** (`_respected_bounce`) gates
  pullback `buyable_now`; backtest-validated (`tools/research_bounce.py`).
- **Deep-Pullback vs Consolidation** classifier rewritten (EMA-fan + `_respected_level`); `near_50` capped ~9% so a
  name far above the 50 (AAOI +13%) isn't mislabeled deep. **High-ADR absolute caps** on entry anchor / zone band /
  stops so 15%-ADR names don't get mid-air zones or 6% stops.
- **Caution-band regime gate** — full stand-aside only in a deep correction (posture <30); patient support-buys keep
  arming at 30–45 (matches the validated `proposed_gate`).
- **News classifier fix** — word-START + inflection matching (kills "award"→war, "urban"→ban, "dealer"→deal while
  catching plurals) + routine insider grants → neutral.
- **RS strength bonus** — Deep Pullback/Consolidation legs with RS ≥90 get up to +7 rating so the strongest leaders
  at the 50 reach A and arm (AXTI RS96 → A).
- **The Agent Crew** — 10 reusable subagents (`.claude/agents/`) with investor personas + model tiers, and the
  **🧠 HQ "Agent Office"** tab (animated, plain-English, local-only; `/api/hq`).
- ⚠️ Restart the server + rescan to load. STANDING RULE: engine changes go through a repro + Burry (qa) before shipping.

## 2026-06-04 (forward-review + PROFIT GUARD)
- **Forward-review finding:** the app's offered slate (103 picks, 2026-06-04) = net +0.26R / 66% green; user's 5 CLOSED trades = −2.55R (−0.51R avg). **Gap is 100% execution, not the system** — every red trade was a chase above the planned entry + improvised tight stop (RKLB: planned 113.66 pullback → user entered at 116.58 with a 14¢ stop → shaken for −0.02R; INTC/AEHR: planned entries never filled so losses were self-made).
- **Grade calibration finding:** B avg +0.39R vs C avg +0.24R (B edges C), BUT 3 of 6 big winners were C-grade (CRDO +2.11, SUNB +2.07, AVAV +1.95) → grader under-rates clean RS-strong pullbacks. Calibrate via forward-test loop as data accrues.
- **PROFIT GUARD built** (`GUARD STOP`, `design-rules-sizing-stops.md`) — ATR-aware 1.5× buffer: after a position moves enough, the stop auto-raises to entry + 1.5× ATR so a retracement doesn't give back full profit. Built off user's "stop giving money back at breakeven" request.
- **AAOI validation:** first AAOI failed on OLD logic (−1R); same name on the fixed live engine → ripped to $200+ (+2.58R open). Validates the mid-day rebuild (entry-confirmation-mechanics).
- **Account base set:** $19,477.20; equity = base + realized (−$400.86) + open. `account_size` in settings = fixed base; never overwrite with the live balance.

## 2026-06-03 (Real-time intraday backtest + visual report — proves the confirmation engine works)
- Built `tools/sim_intraday.py`: replays the LIVE coach on **5-minute bars** — watches each A/A+ setup and
  fires the BUY the moment it breaks its trigger (prior-day high; Qulla/Luk's actual entry), stop = day low
  clamped **0.5–1.2× ADR**, then manages forward to a daily 9-EMA-close / stop exit. Answers "how many
  REAL-TIME calls, when, and how did they do" (the EOD `sim_alerts.py` only asked "in-zone at 4pm?"). Last 21
  sessions: **28 calls (~1.5/day, 5× the EOD gate's 6), 54% win, +1.66R avg, +46R total** — Deep Pullback +5.1R
  (100% win), Breakout +11R, AVWAP +1.0R, Consolidation +0.8R.
- Fixed a stop bug mid-build (a 5-min bar low on a gap-up = near-zero risk → R exploded to +186R; now clamped
  ≥0.5× ADR like the live app). Caveats noted: 8/28 still open at the data end, prior-day-high is a unified
  trigger proxy, bull-month/small sample.
- `tools/sim_report.py` → **`data/sim_report.html`**: dark self-contained visual report (lightweight-charts)
  with a candlestick chart PER CALL — the ▲BUY (date/time/price), the red stop line, the ✕ exit.
- **Conclusion: the EOD "in-zone" trigger threw away ~80% of good setups; intraday confirmation is more
  frequent AND profitable → the validated #1 build.** Also added `data/cache_5m/` (5-min bar cache) +
  `sim_alerts.live_rating` (faithful live-grader replica for backtests).
- Live Coach window: `web/manifest.webmanifest` (window-controls-overlay → borderless look) + size 456×884
  (anti-smush) + favicon `coach.ico`.

## 2026-06-03 (Live Coach — DROPPED pywebview for an Edge app-mode window; fixes the freeze/crash for good)
- **The freeze-then-crash was pywebview itself, not frameless.** Capturing coach_app's unbuffered stderr
  showed a 271KB `[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty…`
  → **maximum recursion depth exceeded** — an infinite UI-Automation/accessibility recursion in
  pywebview/WebView2 that crashes the GUI process a few seconds AFTER the page paints (hence "content shows,
  then freezes"). It happens with the FRAMED window too — the earlier frameless revert didn't fix it. The
  diagnostic tell: after launch only the server `pythonw` survived; the coach_app GUI process was gone, and
  `/api/now` was healthy (2.1s).
- **Fix: ditched pywebview entirely.** `coach_app.py` now opens `panel.html` in an **Edge/Chrome app-mode
  window** (`msedge --app=… --user-data-dir=data/coach_window --window-size=420,772`) — chromeless,
  native-feeling, and rock-solid browser tech. The pystray tray + single-instance lock + server desktop
  notifications are unchanged; the tray's "Open Live Coach" now `open_window()`s (focuses the existing window
  via Win32 EnumWindows/SetForegroundWindow by title, else launches). `panel.html` got a
  `<link rel="icon" href="coach.ico">` so the app window + taskbar show the custom icon (served at
  `/coach.ico`, verified 200). Falls back to the default browser if neither Edge nor Chrome is found.
- Verified: coach_app **stays alive 30s+** (was crashing to 0), the Edge app window opens (chromeless),
  server HTTP 200, favicon served. `pywebview` is no longer a runtime dependency of the coach.

## 2026-06-03 (Live Coach — real-app redesign + "WATCH is MY job, not yours")
- **`WATCH` is no longer a user action.** `compute_now` had WATCH in the `todo` ("⚡ do now") list, so the
  coach literally told the user to "watch your position" — which the user (rightly) called dumb: watching is
  the coach's job. WATCH (earnings / bad-news monitoring) now folds into the quiet `holds` list; **only EXIT /
  RAISE STOP / TRIM are user actions.** The "nothing to do" verdicts were rewritten to say **I'm** watching
  ("Nothing for you to do. I'm watching your N position(s) — I'll ping you the moment one needs action").
- **`web/panel.html` fully redesigned** from a plain dark webpage into a real-feeling app, on the MAIN app's
  design tokens (aurora backdrop, glassy sticky header/footer chrome, Inter font): a **stance hero**
  (GO / SELECTIVE / STAND-ASIDE accent + the one-line verdict as the centerpiece), bold colour-coded
  **⚡ Act-now** cards (EXIT/RAISE-STOP/TRIM), a confident **🟢 Buy** card (grade pill + entry/stop/size grid +
  confirm note), a collapsible **👁️ I'm on it** section for held positions you DON'T need to touch, a clean
  **alerts feed** with relative timestamps, and a calm **All-clear** empty state for the common no-trade day.
  Same data contract (`/api/now`, `/api/notifications`). The native window's look needs the user's eyes —
  pywebview can't be tool-screenshotted.
- Verified against live data: a WATCH-only tape → `todo:(none)`, holds `[HOLD,WATCH]`, "Nothing for you to do."
  **Restart the Coach (tray → Quit, relaunch) to load it (app.py + panel.html changed).**

## 2026-06-03 (Live Coach notifier — a beep now means ACT; killed the random/heartbeat/startup beeps)
- **Root cause of "random beeps" + "watch your positions isn't a notification":** the background notifier
  (`_now_watcher`, app.py) toasted **and beeped** on (1) every app startup, (2) a 5-min *heartbeat* in the
  default `all` mode, and (3) **any change to the verdict TEXT** — which drifts intraday (posture label,
  position count, "frothy/weak/nothing's confirmed") with no action behind it. State was in-memory only, so
  a crash/restart re-beeped everything. A `WATCH`/`HOLD`/"Sit tight" verdict therefore beeped despite having
  nothing to do — exactly the user's complaint.
- **Rebuilt around one rule: a beep means ACT.** Sound + toast fire ONLY for discrete *actionable* alerts —
  the **single best confirmed BUY**, or **EXIT / RAISE STOP / TRIM** on a held position. `WATCH`, `HOLD`,
  stance drift, "sit tight" and heartbeats are now **silent** (panel + tray colour only, never a beep).
  EXIT/BUY = urgent triple-beep; RAISE STOP/TRIM = single chime.
- **Per-alert dedupe + cooldowns, persisted** to `data/notify_state.json` so nothing double-fires (EXIT
  re-reminds ≥90 min, TRIM ≥3 h, RAISE STOP/BUY ≥6 h). **The first loop after any (re)launch is always
  silent** — the startup beep is gone for good.
- **`compute_now` now surfaces the SINGLE best buy** (was up to 2): one decisive call, not a menu.
- **DST-correct market-hours gating** (`_et_now` via `zoneinfo`, fixed-offset fallback) replaces the old
  hard-coded −4 that drifted on DST-changeover days.
- Verified against live data: a `LITE → WATCH` / no-buy tape now yields **"WOULD BEEP: nothing"** while still
  showing LITE in the panel. **Restart the Coach (tray → Quit, relaunch) to load it (app.py changed).**

## 2026-06-02 (Chart UX — white price mark + declutter)
- **Current price now marked in WHITE** on the chart, not the green/red candle color (confusing next to the
  green buy zone / red stop). Candlestick `lastValueVisible:false`; a neutral white price line is drawn in the
  live (`obj`) view and tracks the live tick (`updateChartLive` updates `chartModal._priceLine`). (`web/app.js`)
- **Chart draws ONLY the selected setup's levels.** Removed the faint "other setup" overlay (zone/stop drawn
  faded) — it cluttered/overlapped, especially with the confirm + pullback levels close together. The
  Pullback/Confirm buttons still switch which setup is shown. Legend updated. (`web/app.js`, `web/index.html`)

## 2026-06-02 (Confirmation entry = nearest resistance + breakout-catch + day-low stop)
- **Confirmation is now a real per-setup ALT ENTRY** (`kind:"confirm"`), graded + forward-tested like any
  entry — replaces the earlier card-level "safer entry" box (which was per-TICKER, not per-setup). For
  `worth_waiting` setups (Deep Pullback / Consolidation), `scanner.analyze` adds it as the **nearest real
  resistance buyers must reclaim overhead**: the closest above price among the **9/21/50 EMAs** (computed to
  match the chart's 9/21/50 lines), the **prior-day high**, and recent **swing-high pivots** — 1× ADR stop.
  Don't wait for a far high; breaking the closest level is enough on a strong leader. (e.g. INTC → *reclaim the
  21 EMA ~109.7*, not the 113.3 prior-day high). When price is below the 50, the deep-pullback PRIMARY entry
  already IS the 50 reclaim. `confirm_kind ∈ {9ema,21ema,50ema,high,resistance}`. `ema10/20/50` exposed on the
  result. Card renders a **🔔 Confirm** badge per option. (`scanner.py`, `web/`)
- **LIVE breakout-catch for deep pullbacks** (`breakoutCatchFor`/`_applyCatch`, sibling to `rotationFor`): when
  the market's OPEN and a patient name has already broken out above its deep dip zone, a dip to the 50 EMA is
  unlikely — so the displayed pullback is swapped for a **shallow catch to the nearest rising EMA (10/20)** with
  a ≤1× ADR stop (🎯 *breakout catch* badge). EOD it reverts to the deep zone. (`web/app.js`, `web/index.html`)
- **Breakout-catch grade fixed:** it was inheriting the deep-pullback's A. Now graded off the **confirm/breakout
  option + a notch** (`_plusGrade` → e.g. **B+**) — better than chasing the confirmed breakout, but not the
  deep pullback's A. `gradeColor` reads the first letter so `B+`/`C+` color correctly. (`web/app.js`)
- **Confirm entry stop → the DAY'S LOW (live)** (`_confirmDayStop`): a confirmation is a breakout buy-stop, so
  once it triggers the real invalidation is today's low (like the rotation), not a fixed 1× ADR stop. Tightens to
  the day low (floored at 0.3× ADR; never wider than 1× ADR), recomputes risk/shares, labels **"Stop (day low)"**,
  and the chart stop line tracks it live. (e.g. INOD confirm risk 15.07→7.45/sh). (`web/app.js`, `web/index.html`)
- `entryHint`, the confirm/catch/rotation notes, and all `$` glyphs are **currency-aware** (`cur` → ₪ on IL).

## 2026-06-02 (Israeli / TASE market — local-only)
- **Added a second market (Israel / Tel Aviv Stock Exchange) with a top-right 🇺🇸/🇮🇱 toggle.** Each market is
  fully separated: own dashboard, account, positions, P&L, suggestions, and forward-test data. US is byte-for-byte
  unchanged; IL namespaces every data file under `data/il/`.
- **Backend (`app.py`):** mirrors the per-user workspace pattern — a thread-local `_ctx.market` set per request
  from an **`X-Market`** header (`set_workspace`); `_mns()` namespaces shared files and `_ud()` per-user files;
  the 16 shared-file constants became **per-market getter functions** (`suggest_f()`, `forward_f()`, `pnl_f()`,
  …); `write_json` makes parent dirs; lazy IL workspace bootstrap. **Gotcha fixed:** `_ctx` does NOT propagate to
  worker threads, so `_spawn()` re-applies market+udir into every scan/build/forward worker (else IL jobs would
  write into US files). Session/close gating (`_market_closed`/`_after_close_today`/`_session_date`/
  `_next_session_date`) is market-aware. Forward-test + P&L are per-market; `_run_forward_eod_all` loops US+IL
  (local-only).
- **Config (`scanner.py` `MARKETS`/`mcfg`):** benchmarks (US SPX/QQQ/IWM; IL `^TA125.TA` + `TA35.TA`), session
  hours, **trading days (IL = Sun–Thu)**, tz, currency, and universe thresholds (IL price/dollar-vol in **agorot**,
  marketCap in ₪). `scan`/`analyze_at`/`market_regime`/`_attach_rs` thread a `market` arg.
- **IL universe (`universe.py`, `data/il_symbols.json`):** TASE has no free symbol directory like NASDAQ-Trader,
  so the list is **auto-harvested from Yahoo's screener** (`exchange=TLV`, quoteType=EQUITY → 820, bonds/T-bills/
  series stripped → **499 candidate equities**); `build_universe(market='il', symbols=…)` re-quotes + liquidity-
  filters them (same pipeline as US) → **~222 kept**. TASE has ~500 listed equities total — no 800-name liquid set
  exists like the US; 222 ≈ the whole investable market.
- **Frontend (`web/`):** US/IL pill (mobile-safe), `currentMarket()`, `X-Market` on every request, `reloadAll()`
  swaps the whole app, and a `cur` getter renders **₪ vs $** (TASE quotes in agorot — strategy math is ratio-based
  so unaffected, display only).
- **Local-only:** the toggle is gated `x-show="!hosted"` and `init()` forces `market='us'` when hosted, so the
  hosted swinghelper site stays **US-only** (IL ships inert in the code; `data/il/` + `il_symbols.json` aren't
  copied by the build). Verified end-to-end: data isolation, IL scan (220 graded setups), regime, forward, mobile.

## 2026-06-02 (Forward-test: snapshot top 50, not 10)
- `FORWARD_TOP_N` **10 → 50** (`app.py`). The best setups are "worth waiting" and rarely trigger same-day, so the
  old top-10 was mostly un-entered names — a wider net captures the lower-ranked names that *do* have an entry
  today, so the forward test keeps collecting real data. Pulls from the full `suggestions.json` (not the
  dashboard's top-10), local-only, gated to post-close. Applies to both markets.

## 2026-06-02 (Grade is PER ENTRY OPTION, not per ticker)
- User: "grade should be PER setup — INOD pullback can be A but the breakout is DEF NOT." The grade was
  computed once per ticker from the primary's factors; now **each entry option is graded on its own merit**.
- `scanner.analyze` computes per-entry **`ext50_adr` + `entry_quality` + `chase_exempt`** on every `entries[]`
  plan — graded at the price you'd actually PAY (a buy-stop at its higher trigger; a BUYABLE-NOW pullback at
  the current price, not its lower limit — so APLD buyable at 48 grades as extended, not at its 46 limit).
- `_rating(it, unit)` (app.py) accepts per-entry inputs; the loop grades each option and sets the ticker
  headline grade = **max of the options** (best available setup); each option carries its own `grade`/`rating`.
- Card shows a **colored grade letter per option row**. Result: **INOD pullback A+ / breakout C**; DOCN
  pullback A+ / breakout D; ONDS pullback A / breakout C; DXYZ pullback A / breakout B (breakout only 1.7×
  ADR extended → still B, not blanket). **Restart python + re-scan.**

## 2026-06-02 (Grade rubric v5 — chases out of B, A reachable for the best)
- **Problem:** posture-58 tape capped every grade at B → 0 A's, 98 B's, ~half of them CHASES (e.g. SEDG
  +85%/1m, 4.1× ADR above the 50-EMA, graded B/72). "If chases get B, B is meaningless." Diagnosed via a
  3-agent deep-research pass over the rubric + the 228-trade backtest + the 9-trade journal (CRWV waited→+3R
  vs INOD chased→+0.9R; AVWAP +0.58R / Consolidation +0.41R / worth-waiting +0.36R work in any tape;
  breakouts/EPs fail in weak tape).
- **Fix (canonical in `strategy/scoring.md` v5, mirrored in `app.py _rating()` + `scanner.py analyze()`):**
  1. **Extension/chase penalty** on `ext50_adr` (distance above the 50-EMA in ADR) — graded demote
     `−(ext50_adr−2.5)×8`, **cap at B once ≥2.5×**, **HARD-CAP at C once ≥4×** (parabolic threshold 4.5→4.0,
     now caps the GRADE not just `buyable_now`). Gradient: <2.5× A-eligible / 2.5–4× B / ≥4× C. `worth_waiting`
     dip-buys exempt (deep pullback buys AT the 50; tight Consolidation has a tight base/stop). The 2.5× B-cap
     came from the **APLD** case (Pullback @ AVWAP, ext 2.7, +36%/1m — shallow pullback in an extended move,
     was A → now B).
  2. **`entry_quality` also penalizes distance from the 50-EMA/base** (`stretch50_pen`), not just the 10-EMA
     — closes the "10-EMA is itself parabolic" blind spot (SEDG entry_quality 42→7).
  3. **Timing rewards WAITING** (in-zone bonus only for non-extended names).
  4. **Regime gate is setup-aware** — breakouts/EPs stay capped at B below posture 65; patient
     worth_waiting/AVWAP setups with `ext50_adr<3` can reach **A even in mixed tape** (user choice: "let the
     best reach A now").
- **Result on the 796 (posture 58):** A=12, B=47 (was 98), C=167, D=570. SEDG→C; no ≥4×-ADR name above C;
  the A's are clean pullback/consolidation/AVWAP at support (incl. DOCN, the user's model trade). Magnitudes
  are a starting calibration — tune via the forward-test loop. **Restart python + re-scan.**

## 2026-06-02 (Dual entries, live rotation, forward-test fixes — session)

### Dual entry options + EP relabel (`scanner.py`, `app.py`, `web/`)
- Every suggestion now carries an **`entries` list** (1–2 plans): a **Breakout** (buy-stop above a pivot) and/or
  a **Pullback** (buy the dip to the best support below price). `entries[0]` mirrors the legacy primary so
  grade/sizing/forward/coach are unchanged. Helpers `_breakout_plan`/`_pullback_plan`; each plan typed +
  phrased (**"break above $X"** vs **"wait for the pullback to $Y"** — never "pull back" for a buy-stop).
  Patient setups also offer "break above the prior-day high"; shown only when distinct (≥0.4×ADR). Cards
  render both options with per-option sizing; chart has a **▲ Breakout / ⏳ Pullback** switch (other drawn faint).
- **Episodic Pivot now requires a TRUE open gap** (`gap_up`≥8, gated in scanner) **AND a fresh good-news
  catalyst** (confirmed in `grade_suggestions`); a multi-day run into a base high = **Breakout** (fixes the
  long-flagged ONDS/INOD relabel).
- Coach docs (`CLAUDE.md`, `find-setups`) updated with the EP rule + dual-option phrasing.

### Suggestions sort: grade dominates (`web/app.js`)
- `sugRank`/`gradeBand`: order is **grade band → buyable-now → rating**, so a buyable **C never outranks a B**
  (the "press show-all to find hidden B's" bug). Buyable-now still floats up, but only *within* a grade band.

### Live intraday ROTATION pullback (`scanner.py`, `app.py`, `web/`)
- During REGULAR hours, when a non-patient name has broken out and is **pulling back to its prior-day high**,
  the Pullback option becomes a live rotation: **buy the reclaim of the prior-day high, stop at TODAY's low**
  (both update each tick; ⚠ flag if risk > 1×ADR). `fetch_quotes` now returns `day_high/low/open`; `analyze`
  exposes `last_bar_date/last_high/prev_high`; `grade_suggestions` computes a date-correct `prior_high` via
  `_session_date()`; frontend `rotationFor`/`displayEntries`; chart tracks the rotation stop to the live low.

### EOD jobs are POST-CLOSE only — calendar/forward no longer corrupted mid-session (`app.py`)
- `record_daily_pnl` + the forward snapshot capture were firing on **every launch / 30-min heartbeat / scan**
  (not gated), so dev restarts wrote a stale mid-session value into today's P&L cell and re-froze the next
  snapshot from re-scanned data. New `_after_close_today()` (weekday, ET≥16:00) gates both — never pre-market/
  mid-session. Snapshots now carry `logged_at` + `frozen_at_close_of`. Cleaned the bogus 06-02 P&L cell + the
  premature 06-03 snapshot (backed up first).

### Forward-test chart + readout (`app.py`, `web/`)
- Forward-pick chart shows the **FROZEN snapshot levels** (not recomputed), a ▲ **signal marker at the freeze
  bar**, and a 🔬 **Snapshot badge**. **🔬 Frozen ⇄ 📈 Live** toggle: Live drops the setup and compares the
  **Entrance to current price** (the idea's progress). **No target line.** Metric is **% made, not R**
  (`fwdPct`/`fwdAvgPct`); day header shows "+X% avg". Levels drawn as **rays from the signal forward** so
  pre-freeze candles don't look like fills. Status labels: "filled · open" / "stopped out" / **"no fill yet"** /
  **"never triggered"**. Every pick carries **`progress_pct`** (entry → latest close) so a name that ran without
  giving its dip still shows the move (un-filled shown muted).
- **Snapshot date = the session you TRADE the picks; sim fills from that day inclusive.** The 06-02 snapshot
  held 06-01's traded picks (RGTI/CIFR filled on 06-01) — relabeled **06-02 → 06-01** so the fills show, and
  created today's **06-02** snapshot. (Open follow-up: track the breakout leg in the forward test too — on
  up-days the pullback leg misses moves a breakout would catch.)

## 2026-06-02 (Prediction now reads pre/after-hours SECTOR movement)
- User: the prediction's rotation (Into/Out of) is the EOD multi-day trend and ignored premarket — e.g.
  Photonics/Optics was popping pre-market two days running but still showed as "cooling."
- Added `_premarket_sector_moves()`: averages each sector's members' **`ext_change_pct`** (the true
  extended-hours move vs the regular close, ≥2 members printing) → up/down sector movers. `compute_prediction`
  adds a **🌙 {Pre-market|After-hours} sector moves** driver, nudges the score, mentions the leaders in the
  outlook, and returns `pm_sectors`. Prediction UI shows a dedicated **"🌙 Pre-market now:"** chip row
  (green up / red down) under Into/Out, with a "moves often fade at the open" caveat — kept SEPARATE from
  the multi-day trend so it's a heads-up, not a trend call.
- **Important:** used `ext_change_pct`, NOT `live_sector_heat`'s `perf_1d` — during PRE `perf_1d`'s
  `prev_close` is 2 days back, so it doubled in yesterday's session (showed AI-Networking +13%; the real
  premarket avg is ~+8%). Verified in PRE: Photonics/Optics now surfaces at +3.2% (n=19) as a leader.
  **Restart python + refresh.**

## 2026-06-02 ("Detect new groups" actually finds NEW ones)
- Bug: it was showing clusters whose common thread is an **existing theme** (e.g. INFQ/QBTS/RGTI → all
  Quantum), defeating the purpose. (The "Other" the user saw was the legacy *sector* column; the *theme*
  map has all three in Quantum.) `run_detect_groups` kept theme-dominated clusters with `novel=False`.
- Fix: a cluster that is **entirely an existing theme is now dropped**. An existing-theme cluster is kept
  ONLY when **new names are joining it** (members not in that theme) — surfaced as `joining` (candidate
  additions), e.g. "🆕 INFQ → Quantum". Genuinely new clusters (thread not a known theme) stay as
  `novel` 🆕 groups. Frontend: joining badge + amber ring + a "candidate addition" note + a 🆕 tag on the
  new member; help text rewritten. Verified: the Quantum cluster now drops (0 groups → honest empty
  state); injected joining/novel samples render correctly. **Restart python + refresh.**

## 2026-06-02 (System is now pre/after-hours aware — regime, gameplan, prediction)
- **% color:** position P&L % was grey — now inherits the row's green/red (up/down). (index.html)
- **Market regime shows pre/after:** `live_posture` now returns per-index `ext_pct` + `market_state`/
  `extended`; the regime card shows a 🌙 chip per index (QQQ/IWM; the cash index ^GSPC has no premarket
  print) and a "🌙 PRE/AFTER" header badge. The posture/states already re-blended from live prices.
- **Gameplan & Prediction take pre/after into account:** new `_effective_regime()` — during pre/after
  hours it re-blends SPX/QQQ/IWM from extended-hours prices (`live_posture`); otherwise the stored daily
  regime. Both `compute_gameplan` and `compute_prediction` use it, so posture/stance/lean reflect what's
  moving NOW. Gameplan stance appends a "🌙 Pre-market: … this read uses live prices" note + a header
  badge (`regime_live`/`market_state`); prediction adds a 🌙 extended-hours index-move driver that biases
  the lean. Frontend re-fetches gameplan (+prediction if open) every ~3 min during extended hours.
  Verified in PRE: gameplan `regime_live:true` posture 58 (live), prediction uses it, regime card 🌙 chips.

## 2026-06-02 (Trim strategy: parabolic-only — no more quick trims)
- User: "I do not trim my positions so quickly. I only trim if a stock went parabolic or close to it,
  the EMAs are VERY far from it — like ARM or DELL." The coach trimmed too eagerly (TRIM at `r≥3 &&
  ext9_adr>2.2`, and pre-earnings at `r≥0.5`).
- **Change (both `position_coach` in app.py and `liveCoach` in app.js, kept in sync):** TRIM now fires
  **only on a genuine parabolic blow-off — `ext9_adr ≥ 4.0` (price ≥4× ADR above the 9 EMA, miles above
  the 21/50) with `r ≥ 1`**. Ordinary strength now stays HOLD/RAISE-STOP and rides the 9-EMA trail.
  **Earnings → WATCH** ("binary event; hold through or reduce, your call"), no longer an auto-TRIM.
  `liveCoach`'s ext9_adr is live, so the parabolic trim is premarket-aware.
- Verified: parabolic (4.5× ADR) → TRIM; strong-but-not-parabolic (1× ADR) → RAISE STOP; the old eager
  case (2.5× ADR, +R) → now holds. Playbook updated in `strategy/my-rules.md`. **Restart python + refresh.**

## 2026-06-02 (Premarket P&L semantics fixed + per-position pre/after change)
- **Bug:** during PRE, "Today's P&L" showed a non-zero number = **yesterday's** full-day move. Cause:
  before today's open, Yahoo's `regularMarketPrice` is still yesterday's close and `prev_close` is the
  day before, so `reg_price − prev_close` = yesterday's move. Today's regular session hasn't happened.
  **Fix:** `dailyPnl` returns null during PRE/PREPRE → tile shows "—"; the only live number premarket is
  the **🌙 Pre-market P&L** sub-line (extended price − yesterday's close). Verified: PRE → Today "—",
  Pre-market +$87.
- **Per-position pre/after-hours change** (user ask): each position row in the Gameplan now shows a
  **🌙 chip** with its extended-hours % (and $ impact in the tooltip + expanded line). `tickLive`
  attaches `t._extPct`/`t._extImpact` from the quote's `ext_price`/`reg_price`/`ext_change_pct`.
  Verified live: ONDS −1.71% (−$46), CRWV +2.71% (+$61), etc. Frontend only — **refresh**.

## 2026-06-02 (Dashboard: positions merged into Gameplan, regime color fix, major-news banner, premarket coach)
Four user-requested dashboard improvements (verified live in PRE, desktop + 375px, no console errors):
- **Manage positions ⨉ Open positions merged.** The Gameplan now has one full-width **"Your positions"**
  section (driven by `openTrades`): each row shows the live coach action + ticker + P&L/R + reason, with a
  **"more ▾"** toggle that expands to the full open-position detail (setup/grade badges, entry/stop/risk-
  basis/shares, hit-target, all coach reasons, Chart/Edit/Close). The standalone "Open positions" card was
  removed (redundant). Per-row expand state = `expandedPos` (keyed by trade id).
- **Regime color bug fixed.** An "Extended" index showed **green** because it was colored by raw posture
  (55 → lime band) while the emoji said 🟠. New `stateColor(state)` colors each index by its STATE
  (Extended → amber), matching the emoji. IWM/SPX/QQQ now read amber when extended.
- **Major market news banner.** A deliberately HIGH-bar macro detector (`MACRO_PATTERNS` + `_detect_macro`
  in app.py, fed by a dedicated macro RSS query) surfaces ONLY regime-changers — war/military, Fed-chair
  change, emergency Fed move, election/president shock, market crash/halt, debt/fiscal shock, national
  crisis. Routine Fed speeches, "price war", single-stock moves, and everyday tariff headlines are
  rejected (verified). Shows as a prominent red/amber banner at the top of the dashboard; empty on a
  normal day (`news.macro`). Populates on the next **New day** / news refresh.
- **Premarket-aware position coach.** The merged section uses the **live** coach (`t._liveCoach||t.coach`),
  which recomputes off the live (pre/after-hours) price. Verified: in premarket, INOD flipped from the
  static **HOLD** (last close) to **RAISE STOP** because the premarket spike pushed it past +1R.
  **Restart python** (app.py changed) **+ refresh** (app.js/index.html changed).

## 2026-06-02 (Professional dashboard redesign — two-column, decluttered)
- User feedback: the dashboard was a long single stack of heavy cards — "too cluttered, not easy on the
  eyes." Reorganized into a clean, scannable layout (verified desktop + 375px mobile, no console errors,
  no horizontal overflow):
  - **Toolbar** slimmed (data-as-of + Rebuild universe + New day); the verbose universe-coverage line is
    now the Rebuild button's tooltip instead of a standalone gray paragraph.
  - **KPI strip** at the top — `.stat` tiles: **Equity** (new), Today's P&L (with the 🌙 pre/after-hours
    move folded in as a sub-line so the strip is always a clean 6), Open P&L, Realized, Win rate, Avg R.
  - **Two-column work area** (`lg:grid-cols-12`): main (8 cols) = Gameplan → Open positions → Top
    suggestions; right rail (4 cols) = compact Market regime → vertical Catalysts list → compact Position
    calculator. Collapses to one column on mobile (main above rail).
  - **Declutter:** explanatory footnotes (regime "equal-blend…", gameplan "synthesized…") moved to
    tooltips; market-regime condensed from 3 big multi-chip cards to one compact row per index
    (state · 50-MA ext · off-high · 1m); catalysts changed from a horizontal scroll strip to a tidy
    vertical list (ticker + headline + date).
  - No data/logic changes — pure layout; all existing Alpine bindings/getters reused. **Refresh the
    browser** (frontend only).

## 2026-06-02 (Chase guard held on live ticks + pre/after-hours P&L split out)
- **Bug 1 — extended name showing "🟢 BUYABLE NOW":** NBIS (5.6× ADR above the 50 EMA, AVWAP-reclaim,
  not a patient setup) showed BUYABLE NOW *and* the ⚠️ "extended — chasing" warning at once. The scanner
  correctly set `buyable_now=False` (chase guard), but the frontend live tick (`tickLive`, app.js:298)
  recomputed `buyable_now` from **live price vs zone only**, wiping the guard on every poll. Root trigger:
  the "in-zone" price ($271.25) was a **pre-market** print. **Fix:** the live recompute now mirrors the
  scanner guard — `parabolic && !worth_waiting`, `distribution_today`, or `extended` ⇒ not buyable, even
  if price sits in the zone. Hardened `inZone()`'s fallback the same way.
- **Bug 2 — pre/after-hours leaking into "Today's P&L":** during PRE/POST, `fetch_quotes` collapsed
  `price` to the extended-hours print, and `dailyPnl` used it — so a pre-market gap (e.g. MRVL reg 219 →
  pre 276) counted as today's regular-session P&L. **Fix:** `scanner.fetch_quotes` now returns
  `reg_price` / `ext_price` / `ext_change_pct` separately (keeps `price` as the live value for position
  P&L/charts); `/api/live` forwards them. Frontend: **Today's P&L = regular-session move only**
  (`reg_price` − base), plus a new **Pre-market / After-hours P&L** tile (extended price − regular close)
  that shows only during extended hours. Verified live in PRE: NBIS reg 264.51 / pre 272.91 split cleanly.
  **Restart python** (scanner.py + app.py changed) **then refresh** (app.js + index.html changed).

## 2026-06-02 (News catalysts surface ANY universe mover, not just the top-16 suggestions)
- **Bug the user hit:** MRVL got a big catalyst ("Marvell stock soars — Nvidia CEO calls it the next
  trillion-dollar company") but it never showed in **catalysts**. Root cause: the per-ticker news pool
  (`ticker_news` → the catalyst table + 🚀BUY/🛑AVOID alerts) was built ONLY from the top-16 graded
  suggestions, so a fresh mover that wasn't already a setup was invisible (it sat in the raw feed only).
- **Fix:** after building the feed, `run_news_refresh` now **promotes big catalysts on ANY universe
  name** by resolving each material headline back to a ticker — by company name (`Marvell Technology`→
  MRVL) or an explicit ticker token (`HPE stock soars`). New: `universe.fetch_symbol_names()` +
  `clean_company_name()` (keyless NASDAQ directory → `data/symbol_names.json`, cached ~monthly),
  `app._symbol_names()` + `_build_news_resolver()`.
- **Precision guards** (so it surfaces the SUBJECT, not every mentioned name): a name only counts when
  it's immediately followed by stock/shares/possessive or a price-action verb (kills "Truist cuts…",
  "…Morgan Stanley sees", "Price **Target**", "(NASDAQ:…"); restricted to the tradeable universe;
  generic first-words/tokens blocklisted; **mixed-sentiment roundups skipped** ("…Rally; Credo Plunges").
- Alerts now **buy-first then recency-sorted** (cap 8→10) so a fresh mover outranks week-old news.
  Verified: MRVL is the #1 🚀BUY catalyst + shows in the catalyst table ("not in current setups",
  clickable to chart); promoted set was clean (MRVL/HPE/S/SMCI). Frontend needed no changes.
  **Restart the python process** (app.py changed), then **Refresh news / New day**.

## 2026-06-02 (Live re-rank RESTORED — reconciliation roadmapped)
- Reverted the previous change: the dashboard's **live re-rank is back** (buyable-now floats to the top,
  updates live) — removing it made the best setups lag, which the user (rightly) rejected. The
  dashboard "Top suggestions" is intentionally a LIVE view and will differ intraday from the frozen
  forward snapshot. **Reconciling the two properly is now an OPEN BUG / roadmap item** (PROJECT.md →
  "Next highest-value" item 0): root cause is that top picks all tie at **rating 72** (regime-gate cap),
  so any re-rank reshuffles; fix by breaking the tie with raw score / widening the grade and labeling
  the snapshot (static) vs the dashboard (live) — without removing the live re-rank.

## (superseded) 2026-06-02 (Dashboard top picks == forward snapshot)
- [reverted — see above] Had removed the intraday reorder to force a match; that caused lag.

## 2026-06-02 (Forward log keyed by the TRADE day, not the signal day)
- Snapshots now key by **`_next_session_date()`** (the upcoming session you'd ACT on the picks), not the
  signal session. So picks captured at tonight's close show up labeled **tomorrow** ("your watchlist
  FOR 06-02"), matching how a trader thinks. `_sim_forward` measures from that trade day onward (`>=`),
  which also avoids the signal-day instant-loss bug. `run_forward_eod` captures the next-session
  watchlist from the current top suggestions (no auto-scan). Existing 06-01 snapshot re-keyed → 06-02.
  Verified: forward shows **2026-06-02 · 10 awaiting** (DOCN/INOD/APP/ONDS/AEHR…); scores after 06-02 trades.

## 2026-06-02 (Forward sim fix #2 — measure AFTER the signal day only)
- **Root bug:** the suggested entry is a buy-stop set just above the signal day's range — which equals
  that day's HIGH (ONDS entry 13.91 = 06-01 high; INOD 117.19 = 06-01 high). The sim was measuring the
  signal day ITSELF, so it "triggered at the high and closed lower" = a fake −R (ONDS −0.33R) and a
  misleading −1R aggregate. **Fix:** `_sim_forward` now looks ONLY at bars *after* the signal day — you
  act on the trigger the next session and the result is measured from there. With no post-signal bar
  yet → status **"awaiting"** (no fake R). Each pick's tooltip says exactly what it's waiting for
  ("would enter on the break above $X next session"). Day summary shows "N awaiting next session".

## 2026-06-02 (Forward sim fix — honor the entry TRIGGER)
- **Bug:** the sim entered every pick at the day's OPEN, ignoring the setup's entry trigger — so a
  breakout buy-stop that never broke out (INOD, entry $117.19, closed $114.40) was wrongly "entered"
  and an intraday wick to the stop was logged as a **false −1R** (which was dragging the matured
  aggregate to −1R). **Fix:** `_sim_forward` now waits for the trigger — buy-stop fills only when a
  bar's HIGH reaches entry; limit/pullback fills when a bar's LOW reaches it. Never triggered → **"no
  fill"** (not a loss). On the fill day a stop counts only if the bar CLOSES below it (an intraday wick
  that closes strong isn't a false stop-out). Result: INOD now reads **open −0.13R** (triggered, slightly
  red), pullbacks that didn't fill show "no fill", and the bogus −1R is gone.

## 2026-06-02 (Forward picks → chart + personal P&L calendar)
- **Each forward pick is now clickable to its chart** with the SNAPSHOT's entry/stop drawn — so you can
  see exactly which stop the forward-sim used (it uses the *suggested* stop, not your personal one;
  e.g. INOD "stop" exit was the suggested level, not where your stop sat).
- **Personal P&L calendar (Stats).** `record_daily_pnl()` saves the account's daily equity + **day P&L**
  (= today's equity − the last recorded day's; realized + open) into `pnl_calendar.json`, updated every
  EOD cycle (local only). New month-grid calendar colors each weekday green/red by its P&L with a
  month total. `GET /api/pnl-calendar`.

## 2026-06-02 (Forward = same-day open→close + news connected to setups)
- **Forward picks now score from their SIGNAL DAY** (enter at that day's OPEN, track open→close then
  forward), instead of waiting for the next session. So the day's top picks show their result at that
  day's own close — verified: 06-01's picks now read DOCN +0.4R, ONDS +0.3R, INOD −1.0R, avg +0.19R,
  70% win, with a real lesson. `run_forward_eod()` no longer gates on market-closed — it **scores
  continuously and captures each new US session's picks** (so tomorrow's set is added at the next open).
- **News tab = ONE connected table.** Removed the spammy "Actionable now" chip cards. The primary view
  is now **Catalysts → setups**: each stock with a news catalyst joined to its grade/setup/why and
  **what to do** (🟢 buy zone / ⏳ wait), sorted actionable-first then newest. Broad macro/sector
  headlines moved to a secondary **Market headlines** feed below. `catalystTable` computed (news ⋈ suggestions).

## 2026-06-02 (News feed cleanup + prediction-news + gameplan clarity)
- **News tab rebuilt as ONE clean feed.** Replaced the scattered category cards with a single
  **deduped, newest-first "Latest catalysts" feed** (material-only) with **relative timestamps**
  ("2h ago"), sentiment icons, source, and a Trump tag. Backend builds `news.feed` (deduped from the
  already-sorted `pool_imp`, ≤24); frontend `ago()` helper. The "Tickers worth watching" sidebar stays.
- **Prediction now names the actual catalysts** (not just a +/- count) and weights news a bit more —
  a "Catalysts: 🚀 …" driver lists the top material headlines feeding the lean.
- **Daily Gameplan clarity:** the stance is now a prominent posture-tinted **banner** (headline +
  bottom line), and Manage / New-entries / Avoid / Remember are separated into distinct panels.

## 2026-06-02 (Forward log: keyed by US session + autonomous updating)
- **Bug fix — key by the US trading-session date, not the local clock.** `now_date()` is the local
  date, which rolls over before the US close (e.g. Israel midnight = 17:00 ET, prior session). The
  forward log now keys off `_session_date()` (SPY's latest daily bar), so days aren't mislabeled and
  no phantom "next day" is created before the US session trades.
- **Autonomous EOD updating.** `run_forward_eod()` (on launch + every 30 min, local only, while the
  market is CLOSED): `_refresh_forward_bars()` pulls the latest session's bars (SPY-probed, only for
  picks that are behind) so **prior days' picks score forward automatically as each session closes**;
  and it **captures the latest completed session's top picks** if not yet logged (auto re-rating the
  universe first if the current scan isn't from that session). Results now update themselves at each
  close with no clicks.

## 2026-06-02 (Forward test: per-day results + daily lesson)
- The **Stats → 🔬 Forward test** card now shows a **day-by-day breakdown** (collapsible per date) of
  the top picks logged that day, each with its forward R + status (matured / open / no-entry) and
  Trend-Template/VCP badges — plus an **auto-generated lesson per day** (`_day_lesson()`): avg R,
  best/worst pick, and which trait carried the edge (Trend Template / VCP / buyable-now / setup type).
  `score_forward()` now returns `by_day`. Results accrue as forward sessions arrive (enters the session
  after the signal; "matures" ~7 sessions).

## 2026-06-01 (Dashboard reorder + auto daily forward-data)
- **Dashboard reordered by priority** (flex `order`, no risky block moves): refresh → coverage →
  **Market regime → Daily Gameplan → Today's P&L → Open positions → catalysts → Top suggestions →
  Position calculator (now last)**. Fixes the calculator/tool sitting above your positions & ideas.
- **Auto daily forward-data (local only).** `log_forward_picks()` now snapshots the **top 10 by rating
  (ANY grade** — some days have no A/A+) instead of only A/A+, with `trend_template`/`vcp` flags.
  `run_forward_eod()` + a background heartbeat (`_forward_eod_loop`, started in local `main()`)
  **auto-snapshot once per day when the market is closed** (`_market_closed()` ET check), gated to
  today's scan + not-already-captured. Scored over the following days by the existing `score_forward()`
  / Stats "forward" tab — a growing dataset to learn which setups actually work. Never runs hosted.

## 2026-06-01 (Suggestions UX + auto-equity + chase guard)
- **One watchlist button.** Removed Approve/Reject (and the pending/approved status filter) from
  Suggestions — replaced with a single **+ Watchlist** button (`addSugToWatch`/`onWatch`); the status
  badge now shows only "✓ taken". "Took it" (log a trade) stays.
- **Auto account equity.** The typed-in account size is now the **base**; the app derives live
  **equity = base + realized + open P&L** (`compute_equity()`), uses it for sizing (`_equity_settings()`),
  and shows it in the sidebar ("$21,284.96 (+$1,067 P&L)"). The close handler no longer mutates the
  base — realized is computed from closed trades, so the account "updates itself."
- **Chase guard (NBIS fix).** A momentum/breakout name **parabolic-extended ≥4.5× ADR above the 50 EMA**
  is no longer flagged "buyable now" even if the close lands in the zone — that's chasing a vertical
  move. Card shows "⚠️ Extended ~X× ADR above the 50 EMA — chasing; wait for a pullback." Patient
  dip-buy setups (deep pullback / consolidation) are exempt. Verified: NBIS (5.6× ADR) → not buyable.

## 2026-06-01 (Minervini Trend Template + VCP + redesigned filter bar)
- **New strategy: Mark Minervini** (`strategy/minervini.md`, from verified deep research) as the
  "worth watching" **eligibility gate** — complements (doesn't replace) Qulla/Luk.
- **Trend Template** (`scanner`): `trend_template` boolean + `tt_count` (n/8) — price > 50/150/200
  SMAs, 50>150>200 stacked, 200-SMA rising, ≥30% above the 52w low, within 25% of the 52w high, RS
  rating ≥70 (the RS criterion finalized in `_attach_rs`). 137/796 pass — a clean Stage-2 leader
  universe (DOCN/ONDS/AEHR/INTC… 8/8). Refuted variants deliberately NOT encoded.
- **VCP detector** (`scanner._vcp`, approximate): `vcp` + `vcp_contractions` — successive shallower
  contractions on drying volume near the base high; hardened against flatline/illiquid false
  positives (plateau-collapse + 2–6 contraction cap + a real ≥8% leg required).
- **Suggestions filter bar redesigned**: 4 stacked rows → one grouped, collapsible bar (`.fchip`),
  with the top signals always visible + a "⚙ Filters (N)" toggle + match count + clear. **New filters:
  Trend Template, VCP, News catalyst, Buyable-now**; ✓ Trend Template / 🌀 VCP badges on cards. Mobile-safe.
- Wired `minervini.md` into the in-app Strategy tab (`DOCS`, `docTabs`) and the build (`make-build.ps1`).

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
