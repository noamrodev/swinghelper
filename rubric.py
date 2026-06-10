"""
Rubric — the ONE place for the grade weights, grade-letter cutoffs, and rating caps.

Before this module the composite weights lived in THREE places (`app._rating`,
`app.entry_grade_for`, `backtest.bt_rating`) and the grade-letter cutoffs in two
(`app._grade_letter`, `backtest.grade_of`), kept in sync by hand — a tweak in one but
not another was a silent grading bug. They now live here; every site imports them.

Leaf module: imports nothing from app/scanner/backtest (so scanner→app→backtest can all
import it without a cycle). Canonical prose mirror: strategy/scoring.md — keep in sync.
"""

# --------------------------------------------------------------------------- #
# Composite grade weights — must sum to 1.0. (strategy/scoring.md documents these.)
# --------------------------------------------------------------------------- #
W_SETUP  = 0.28   # technical setup quality (from the scanner's raw score)
W_RS     = 0.14   # relative strength
W_REGIME = 0.14   # market regime (posture), setup-aware
W_ENTRY  = 0.14   # entry location (don't-chase / tight stop)
W_LIQ    = 0.08   # liquidity → institutional interest
W_SECTOR = 0.10   # sector / theme heat
W_TIMING = 0.06   # timing (reward waiting at support)
W_NEWS   = 0.06   # news direction

NEUTRAL = 55      # regime/sector/timing/news held neutral when unknown (e.g. a past date)


def composite(setup, rs, regime, entry_loc, liq, sector, timing, news):
    """The weighted sum of the 8 factors (each 0-100) → a raw rating before any caps."""
    return (W_SETUP * setup + W_RS * rs + W_REGIME * regime + W_ENTRY * entry_loc
            + W_LIQ * liq + W_SECTOR * sector + W_TIMING * timing + W_NEWS * news)


def setup_score(raw):
    """Map the scanner's raw technical score to the 0-100 setup-quality factor."""
    return max(0, min(100, (raw - 4) / 16 * 100))


def grade_letter(r):
    """Rating (0-99) → letter grade. The ONE definition of the cutoffs."""
    return "A+" if r >= 82 else "A" if r >= 73 else "B" if r >= 63 else "C" if r >= 52 else "D"


# --------------------------------------------------------------------------- #
# Cap thresholds — named so a calibration tweak can't silently drift between sites.
# The cap VALUES line up with the grade-letter cutoffs (52=C top of D/C, 62/72≈B, 78≈A).
# --------------------------------------------------------------------------- #
CHASE_SOFT_ADR = 2.5    # extension above the 50-EMA where the graded chase penalty starts
CHASE_HARD_ADR = 4.0    # parabolic — a hard chase (also the scanner's `parabolic` flag)
CHASE_PEN_K    = 8.0    # graded penalty per ADR above CHASE_SOFT_ADR

# ----- Pullback to the rising 9/21 EMA (the NBIS case, 2026-06-09) ---------- #
# The middle-ground continuation setup between a shallow Pullback (still above the short SMAs) and a
# Deep Pullback (all the way down to the 50): price has dipped BELOW the short SMAs to the rising 9/21
# EMA, still WELL above a rising 50, with intact higher lows. The classifier's SMA-based `uptrend`
# gate (above>=2) drops these (NBIS at the 21-EMA read above=1) so they used to fall through to a
# near-prior-high Breakout = a chase. These caps bound WHEN that pattern is real (vs a marginal-above
# -50 breakout name): it must sit at least PB21_MIN_EXT50_ADR above the 50 (a genuine extended-above
# -50 leader, not a name basing right on its 50) and within PB21_NEAR_ADR of a rising 9 or 21 EMA.
PB21_MIN_EXT50_ADR = 1.0   # price must be >= this many ADR above the rising 50 (NBIS@218 = 1.8) — separates a
                           # true pullback-to-the-21 from a breakout name sitting right on its 50 (GOOGL/NVDA/TSLA ~0.2)
PB21_NEAR_ADR      = 1.0   # price must be within this many ADR of a rising 9 OR 21 EMA (holding the line)
PB21_PREF          = 2.5   # setup-preference weight — a strong continuation, comparable to Pullback/Consolidation,
                           # but NOT above an AVWAP-anchored pullback (4). Single-sourced into scanner.PREF.

# ----- Deep Pullback knife-catch guard (the LUNR case, 2026-06-09) ---------- #
# A Deep Pullback is disqualified (worth_waiting, NOT buyable_now) when the structure is a CONFIRMED
# DOWNTREND slicing the 50, not a leader HOLDING it: >= KNIFE_LOWER_HIGHS successive lower swing highs
# AND price below DECLINING short (9/21) EMAs. We do NOT reintroduce a pull-% cap (breaks AXTI-style
# legit deep pulls) and do NOT gate on 50-EMA slope (it LAGS — LUNR's 50 was still rising while price
# crashed through it). The short-EMA slope + lower-highs read the live waterfall the 50 can't.
KNIFE_LOWER_HIGHS = 3      # >= this many successive lower swing highs = a confirmed down-leg (LUNR had 6)

# ----- Horizontal-resistance "clear the wall" confirmation gate (the AXTI case, 2026-06-09) -------- #
# Generalizes the AVWAP-family overhead-EMA gate (the IREN CLEAR_9EMA fix) to HORIZONTAL resistance —
# a prior-day high / nearest recent swing high / round level sitting just above the live trigger. When
# such a wall sits within WALL_NEAR_ADR ABOVE the candidate entry, the confirmation must wait for a
# COMPLETED 5-min CLOSE above the wall — not a reclaim/spin BENEATH it (AXTI: a 50-reclaim near $92 fired
# while the prior-day high $96.56 capped it; price poked $96.67, failed, reversed to $91.82 = a false
# breakout bought into resistance). Only the deep-pullback 50-reclaim and AVWAP-reclaim paths consult it;
# a clear path with no overhead wall is unchanged.
WALL_NEAR_ADR = 0.6        # a horizontal wall within this many ADR above the entry = must CLOSE above it first
                           # (matches _overhead_res's 0.6×ADR "near resistance" band)

# ----- DESCENDING-trendline "clear the wall" confirmation gate (the DOCN case, 2026-06-09) --------- #
# The SLOPED sibling of the horizontal wall + the AVWAP overhead-EMA gates. A genuine descending
# resistance line (lower highs off a recent peak) caps a name even when no flat level or EMA is near
# (DOCN: ~$169 sitting just UNDER a line drawn off the ~$184 June-4 peak — a buy fired straight into it).
#
# INTENT (user clarified 2026-06-09 v2): this is CONFIRMATION-ONLY, NOT a grade/quality signal — it must
# NEVER lower the grade or hide a setup (FIREWALLED; rubric untouched). The trader DOES want the younger
# lines (DOCN's ~3-day line off the $184 peak with lower highs ~$180.5/$174.7) drawn + gating the
# confirmation (wait for a 5-min close above), even though they are young: a young line firing a WAIT is
# LOW-HARM (worst case a brief false wait, never a loss, never a grade hit). So we relax the strict
# multi-week definition to ALSO detect a younger 2-anchor line. The key NOISE-GUARD is PROXIMITY: the GATE
# only activates when today's line sits within WALL_NEAR_ADR (0.6x ADR) ABOVE the entry, so a far/loose
# line can NEVER silently gate a clean setup — we lean on that, not on a strict line definition.
#
# A valid line still must be a REAL lower-highs sequence (anti-noise, NO arbitrary 2-point line): a recent
# PEAK (a genuine local high), a genuinely DESCENDING slope, >= TREND_MIN_LOWER_HIGHS distinct lower highs
# stepping DOWN after the peak, >= TREND_MIN_TOUCHES distinct touches of the line, and NO decisive close
# above it; fit on RECENT bars only (<= today, no lookahead). Both the YOUNG 2-anchor line (short leg) and
# the STRICT multi-week channel (e.g. MRAM) qualify. When today's line value sits within WALL_NEAR_ADR
# ABOVE the candidate entry, the confirmation arms (CLEAR_TREND_ARMED) and fires only on a COMPLETED 5-min
# CLOSE above the line (CLEAR_TREND) — never a reclaim/spin beneath it.
TREND_MIN_TOUCHES = 2      # >= this many distinct touches within tolerance = a real (not arbitrary) line
                           # (the peak anchor + >= 1 lower high counts; relaxed from 3 for the young DOCN line)
TREND_MIN_LOWER_HIGHS = 2  # >= this many distinct lower highs stepping DOWN after the peak — the real
                           # "lower-highs sequence" noise guard (DOCN: 180.5, 174.7 below the 184 peak)
TREND_LOOKBACK    = 60     # fit the line on at most this many recent daily bars (the current down-channel)
TREND_MIN_DESC_ADR = 0.04  # min DOWNWARD slope, in ADR units PER BAR, for the line to count as DESCENDING
                           # (a near-flat line is a horizontal wall's job, not this gate)
TREND_TOUCH_TOL_ADR = 0.5  # a daily high within this many ADR BELOW the line = a "touch" (respect tolerance)
TREND_BREAK_TOL_ADR = 0.12 # ...but a high more than this many ADR ABOVE the line = the line is BROKEN → reject it
                           # (user 2026-06-10, the SEDG case: at 0.5×ADR slack candles sat clearly above the line and
                           # it still passed — "that isn't resistance, it's been broken"). A valid descending line is
                           # PERFECT/unbroken resistance: only a tiny wick (~0.12×ADR) may poke above; a close above
                           # (high necessarily above too) invalidates it. Tight break-tol, generous touch-tol.
TREND_MIN_LEG  = 3         # the end-anchor must be at least this many bars after the peak (relaxed 15->3 so a
                           # young line like DOCN's June-4 peak + 3 lower-high bars qualifies; the proximity
                           # gate, not the leg length, is the noise guard — see WALL_NEAR_ADR above)
TREND_MIN_DROP_ADR = 0.5   # the line must have DROPPED at least this many ADR peak->today (relaxed 3.0->0.5 so
                           # a young line off a recent peak qualifies; DOCN dropped ~0.85 ADR in 3 bars). Still
                           # rejects a near-flat mean-revert drift (which is the horizontal wall's job anyway).

# A "wait for the pullback" limit is STALE once price has run this many ADR above its buy-zone top —
# a real pullback would have to retrace more than this just to reach the zone, so the limit won't
# realistically fill (the parabolic INOD/DOCN case). A stale leg is graded as the chase it now is and
# can't carry the ticker's headline grade.
STALE_PULLBACK_ADR = 1.0

CAP_PARABOLIC = 52      # parabolic chase (ext ≥ HARD) → max C
CAP_EXTENDED  = 72      # extended SOFT–HARD ADR above the 50 → max B (the APLD case)
CAP_DISTRIB   = 62      # distribution / climax-reversal day → max C (the ASTS case)

REGIME_WEAK  = 50       # posture below this = weak tape
REGIME_MIXED = 65       # posture below this = mixed tape
REGIME_SOFT  = 55       # below this, non-pullback regime factor is discounted (×0.6)
REGIME_DISCOUNT = 0.6   # the discount applied to the regime factor in soft tape

CAP_BREAKOUT_WEAK  = 52   # breakout/EP in weak tape (posture < WEAK) → max C
CAP_BREAKOUT_MIXED = 72   # breakout/EP in mixed tape (posture < MIXED) → max B
CAP_PATIENT_WEAK   = 72   # best patient at-support setup in weak tape → max B
CAP_PATIENT_MILD   = 74   # patient LEADER in a MILD pullback (posture 40-49) → max A, never A+ (=81 max-A
                          # minus the +7 max strength bonus, so even an RS99 leader can't reach A+ in a sub-50
                          # tape; A+ is reserved for a healthy tape, posture ≥ REGIME_WEAK). The LITE case.
CAP_PLAIN_WEAK     = 52   # plain pullback / other in weak tape → max C
CAP_PLAIN_MIXED    = 78   # plain pullback / other in mixed tape → allow A, not A+
CAP_BELOW_200      = 62   # below the 200-day SMA = not Stage 2 → max C (the MNDY case)
BROAD_CORR_MIN_SECTORS = 8  # broad_correction needs a real market's worth of sectors falling, not a tiny
                            # (e.g. single-group) heat — guards the de-bias from a degenerate sector table
# LEADERSHIP GATE (user 2026-06-05, the TER case): A/A+ is reserved for LEADERS. A name that is neither a
# strong-RS leader NOR a confirmed Stage-2 trend-template uptrend caps at B — a tidy AVWAP/pullback on a
# choppy non-leader (TER: RS 58, fails the trend template, months of chop) is a B at best, never an A.
LEADER_RS     = 70        # rs_pct (percentile) at/above this = leader-grade strength (Minervini RS-rating bar)
CAP_NONLEADER = 72        # not a leader (RS < LEADER_RS and not trend-template) → max B

EARN_SOON_PEN = 18      # earnings within ~a week → hard demote
EARN_NEAR_PEN = 6       # earnings ~8-14 days out → lighter caution

# Overnight exposure warning / cap (my-rules.md: ~30% overnight)
OVERNIGHT_EXPOSURE_WARN = 25   # warn when invested_pct >= this (alert-only, no auto-trim)
OVERNIGHT_EXPOSURE_CAP  = 30   # cap when invested_pct >= this (alert-only, no auto-trim)

# Market regime freshness (B3): a computed_at_ts older than this is clearly a prior session
MARKET_STALE_SEC = 6 * 3600   # 6 hours

HIST_NUDGE_MAX = 8      # ± realized-results nudge cap (|median_R × 3| clamped)
HIST_NUDGE_K   = 3      # realized MEDIAN-R → rating-point multiplier (B2 2026-06-07: median, not mean)
HIST_MIN_N     = 5      # min CLOSED trades per setup before the nudge arms


# --------------------------------------------------------------------------- #
# Position-coach thresholds — shared by the backend coach (app.position_coach) and the
# live frontend recompute (web/app.js, served via /coach_config). The branch ORDER lives
# in each (Python at scan time, JS live/premarket-aware); these NUMBERS are single-sourced.
# --------------------------------------------------------------------------- #
COACH_PARABOLIC_ADR = 4.0   # ≥ this many ADR above the 9-EMA = a parabolic blow-off → TRIM
COACH_RAISE_R       = 1.0   # at ≥ this R, raise the stop to breakeven
COACH_EARN_SOON_D   = 7     # earnings within this many days = a binary event → WATCH
TRAIL_EMA           = 9     # the DEFAULT trailing-exit EMA — exit on a daily CLOSE under it. The user's core
                            # method (Qulla 9-EMA trail, CLAUDE.md ground rule), reaffirmed 2026-06-05: "the rest
                            # is using the 9 ema." (A 2026-06-03 backtest had set this to 20 for max-R — the looser
                            # trail let DOCN run +21R vs +11R — but the user prefers the tighter 9-EMA discipline.)
TRAIL_EMA_PATIENT   = 50    # the LONG-HOLD trail — ONLY for a market leader bought DEEP at the 50 EMA (Deep
                            # Pullback, like CIEN). NOT consolidations bought up near the 9/21 (user 2026-06-05:
                            # "the only long positions is when i buy a market leader on the 50 emas, the rest is 9").

# ----- Defend mode (extended + weak tape → flatten momentum into the close) -- #
# The user's ask (2026-06-05): when the market is BOTH extended AND showing weakness RIGHT NOW (the
# classic "good day yesterday, red premarket, it gives the money back" tape), don't hold momentum
# trades overnight — flatten them into the close and go to cash. ALERT-only (the app never sells for
# you). Patient 50-EMA holds (Deep Pullback only — a leader bought DEEP at the 50) are EXEMPT; a Consolidation
# bought near the 9/21 trails the 9 and is NOT exempt (user 2026-06-05).
# New entries are NOT paused (the user trades daily); the rule is purely "don't carry momentum overnight".
DEFEND_FG          = 72     # Fear&Greed at/above this alone counts as "extended" (frothy/greedy)
DEFEND_STRETCHED_N = 2      # this many of the 3 indexes stretched above the 50-MA also = "extended"
DEFEND_RED_PCT     = -0.15  # an index counts as "red on the session" at/below this % (ext-hours or intraday)
DEFEND_WEAK_RED_N  = 2      # need this many of the 3 indexes red → the tape is "weak right now"
DEFEND_WEAK_AVG    = -0.20  # ...AND the average index session move must be at/below this (kills flicker)
DEFEND_FLATTEN_ET  = 15.0   # the FLATTEN reminder fires after this ET hour — 15:00 = ~1 HOUR before the 16:00
                            # close (user wants the heads-up an hour out, BEFORE the bell, while it can still be
                            # acted on in the regular session). Before the window it's a quiet "plan to flatten".
# Per-index "rubber-banded above the 50-MA" lines — an INDEPENDENT shield (risk-off) arm (user 2026-06-09).
# Value = atr_mult_50 = (%gain above the 50-MA) ÷ ADR%, in the ENGINE's units (ADR = mean H/L range, runs
# ~20% above TradingView's true-ATR, so these sit ABOVE the TV reading). Calibrated on 4y of daily bars
# (dev/atrstretch/measure5y.py): each line ≈ the rarest ~6% of that index's days, where real tops cluster.
# ADR-normalization already divides out volatility, so the three indexes top at SIMILAR extension (~5–8);
# small-cap IWM gets stretched far less often, hence its lower line. SEPARATE from stretched_50 (the flat
# 4.5 chase/posture flag) on purpose — this drives SHIELD only and never touches a grade.
OVERSTRETCH_50         = {"SPX": 7.0, "QQQ": 7.0, "IWM": 5.0}
OVERSTRETCH_50_DEFAULT = 7.0
OVERSTRETCH_N          = 2   # this many of the 3 indexes over their line → independent shield (risk-off) arm
# A sharp VIX move = market stress → contributes to the "weak right now" leg of defend mode (NOT the
# "extended" leg). All three guard together so a vol blip off a low base can't arm it: VIX must be ABOVE
# the absolute floor AND have spiked 1-day OR run up over ~7 days. Fixed market-structure priors.
VIX_SPIKE_ABS_MIN  = 18.0   # VIX must be at/above this absolute level for any spike gate to count
VIX_SPIKE_1D_PCT   = 15.0   # ...and a 1-day rise at/above this % = an acute panic spike
VIX_TREND_7D_PCT   = 30.0   # ...or a 7-day rise at/above this % = sustained, building fear

# ----- Tape Guard (intraday "the market rejected and is rolling over") ------- #
# The user's rule (2026-06-09, the AXTI/INTC/TER + AMKR session): "nothing is confirmed when the market
# is going down." Every buy taken into a REJECTING tape failed identically. Tape Guard is an INTRADAY,
# ALERT-ONLY defense that arms when the indexes were UP / made an intraday high and have ROLLED OVER (a
# rejection), NOT a quiet slow-red drift. Distinct from defend mode (which flattens momentum INTO THE
# CLOSE on an extended+weak OR risk-off tape): Tape Guard fires DURING the session the moment the tape
# turns down, and (1) downgrades NEW buy confirmations to watch-only, (2) recommends moving ALL open
# positions to break-even, (3) alerts every local surface. Tape Guard and defend can be ON together.
# Single-sourced here; LOCAL-ONLY + ALERT-ONLY (the app never moves a stop or sells). No lookahead — the
# fade reads the realized session high (day_high) vs the live price, all values <= now.
TAPE_GUARD_RED_PCT   = -0.10   # an index counts as "red on the session" at/below this % (session move).
                               # Slightly looser than DEFEND_RED_PCT (-0.15) because the FADE test below is
                               # the real teeth — a name can be only mildly red but have rolled hard off its high.
TAPE_GUARD_RED_N     = 2       # need this many of the 3 indexes (SPX/QQQ/IWM) red AND faded → arm
TAPE_GUARD_FADE_PCT  = 0.40    # "rolled over" = the live price has faded at least this % BELOW the session high
                               # (day_high). A quiet slow-red drift that never popped barely fades off its high;
                               # a rejection (up, made a high, sold off) shows a real gap from the high to here.
TAPE_GUARD_UP_PCT    = 0.10    # ...AND the session high must have been at least this % ABOVE the prior close —
                               # i.e. the index actually WAS up / made an intraday high before rolling over (the
                               # "rejection" the user means, not a gap-down that just bled lower all day).
TAPE_GUARD_DEEP_PCT  = -1.0    # PHASE split (user 2026-06-09): once the avg of the armed indexes is at/below this,
                               # the rejection has become a full DOWN DAY — the message switches from "rejected &
                               # rolling over" (still up-ish near the highs) to "sold off hard — down day" so it
                               # never claims "were up" on a deeply-red tape. The ARM + protection are unchanged
                               # (stand-down + break-even stay ON the whole way down) — only the wording changes.

# ----- Tape Turn (intraday "the market flushed and is spinning back up") ----- #
# The user's rule (2026-06-09): the INVERSE of Tape Guard. "You can see QQQ spinning, just like our
# spinning stocks." When the market FLUSHES (sells off, makes an intraday low) and then SPINS BACK UP —
# bounces off that low and reclaims its 5-min 9/21 EMAs, making a higher low — the rejection is OVER and
# the stand-down should LIFT so the normal confirmation engine can surface buys again. ALERT-ONLY: Tape
# Turn never buys; it only RE-ENABLES new buy confirmations to surface + beep normally, and alerts every
# local surface. It does NOT un-raise any stop (break-even stays — that's just good risk). STANDALONE — it
# arms on its own (does NOT require a prior Tape Guard); when both Guard's roll-over and Turn's confirmed
# reclaim are read the same poll, a CONFIRMED Turn overrides Guard's buy-suppression (the all-clear). No
# lookahead — derived statelessly each poll from COMPLETED 5-min bars only (the still-forming last candle is
# the live price, never counted as held). Mirrors the STOCK spin (_respected_bounce / buyers_confirm) on the
# indices. Two phases: "forming" (spun, not held yet → still watch-only) vs "confirmed" (held the reclaim
# for >= TAPE_TURN_CONFIRM_BARS completed bars with a higher low → lifts the stand-down).
TAPE_TURN_N            = 2     # need this many of the 3 indexes (SPX/QQQ/IWM) flushed + spun back up → arm
TAPE_TURN_FLUSH_PCT    = 0.30  # the index must have FLUSHED — made an intraday low at least this % BELOW its
                               # session high (a real selloff off the high, mirroring the Guard's fade test);
                               # a quiet grind that never sold off has nothing to "spin back" from
TAPE_TURN_BOUNCE_PCT   = 0.20  # ...and have BOUNCED at least this % back UP off that intraday low (the spin —
                               # price lifting off the flush low, not still pinned to it)
TAPE_TURN_CONFIRM_BARS = 3     # the reclaim must HOLD for this many COMPLETED 5-min bars (~15 min) closing
                               # above BOTH the 5-min 9 and 21 EMA, with a higher low → "confirmed" (lift the
                               # stand-down). Fewer held bars (>=1) = "forming" (spun but not held → stay watch-only).
                               # A 1-bar V-spike that pokes above and falls back never reaches this → never confirms.

# ----- Profit guard (lock in real money, don't choke on noise) -------------- #
# The user's ask (2026-06-04): "I'm tired of giving my money back at breakeven. Let me KEEP some.
# Raise the stop to a level with resistance/structure and ENOUGH distance from price — but only
# where it makes sense; don't force me out." The guard only fires when a support level exists that
# locks in >= GUARD_MIN_LOCK dollars AND sits >= GUARD_BUFFER_ADR ADR below the live price.
GUARD_MIN_LOCK    = 40.0    # the SMALL $ floor a guard stop must bank to be worth suggesting ("take some money");
                            # the structure picked banks as much as the position allows, often well past it
GUARD_BUFFER_ADR  = 1.5     # the guard stop must sit at least this many ADR below the live price. The exit is a
                            # daily CLOSE, so the stop must clear a NORMAL pullback (≈1–2 day-ranges), not a fraction
                            # of one — else a single ordinary red day wicks it. On a hot, high-ATR vertical (INOD
                            # ≈13% ADR) nothing is far enough above entry to guard yet → correctly no guard ("not the time").
GUARD_STEP_DOLLARS = 25.0   # only re-suggest a guard if it banks at least this many $ MORE than the current stop
                            # already locks (anti-nag: don't push you to nudge a profitable stop for trivial gain)


def coach_config():
    """The coach threshold numbers, as a dict for the frontend to read (one source of truth)."""
    return {"parabolic_adr": COACH_PARABOLIC_ADR, "raise_r": COACH_RAISE_R,
            "earn_soon_days": COACH_EARN_SOON_D,
            "trail_ema": TRAIL_EMA, "trail_ema_patient": TRAIL_EMA_PATIENT,
            "guard_min_lock": GUARD_MIN_LOCK, "guard_buffer_adr": GUARD_BUFFER_ADR,
            "guard_step_dollars": GUARD_STEP_DOLLARS}
