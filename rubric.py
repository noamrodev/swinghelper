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
CAP_PLAIN_WEAK     = 52   # plain pullback / other in weak tape → max C
CAP_PLAIN_MIXED    = 78   # plain pullback / other in mixed tape → allow A, not A+

EARN_SOON_PEN = 18      # earnings within ~a week → hard demote
EARN_NEAR_PEN = 6       # earnings ~8-14 days out → lighter caution

HIST_NUDGE_MAX = 8      # ± realized-results nudge cap (|avg_R × 3| clamped)
HIST_NUDGE_K   = 3      # realized avg-R → rating-point multiplier
HIST_MIN_N     = 5      # min CLOSED trades per setup before the nudge arms


# --------------------------------------------------------------------------- #
# Position-coach thresholds — shared by the backend coach (app.position_coach) and the
# live frontend recompute (web/app.js, served via /coach_config). The branch ORDER lives
# in each (Python at scan time, JS live/premarket-aware); these NUMBERS are single-sourced.
# --------------------------------------------------------------------------- #
COACH_PARABOLIC_ADR = 4.0   # ≥ this many ADR above the 9-EMA = a parabolic blow-off → TRIM
COACH_RAISE_R       = 1.0   # at ≥ this R, raise the stop to breakeven
COACH_EARN_SOON_D   = 7     # earnings within this many days = a binary event → WATCH
TRAIL_EMA           = 20    # the DEFAULT trailing-exit EMA — exit on a daily CLOSE under it. Max-R intraday
                            # backtest (2026-06-03, tools/sim_intraday.py): the 20-EMA trail beat the 9-EMA
                            # +65.8R vs +53.6R over the month by letting winners run ~2× further. (Was 9.)
TRAIL_EMA_PATIENT   = 50    # the LONG-HOLD trail for the patient deep-leader setups (Deep Pullback / Consolidation)

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
