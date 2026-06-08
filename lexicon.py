"""The Lexicon — Setup & Signal Context Layer (Phase 1: detect + display only).

A leaf module: imports NOTHING from app/scanner/rubric and reads NO files. Each detector is a
pure function of an already-GRADED suggestion item dict (the dict `grade_suggestions` produced) and
returns whether its tag fired. `detect_all(item)` runs the Phase-1 registry and returns the fired
tags as display badges. It NEVER mutates the item and NEVER influences a grade — the only consumer
is the UI (and, later, the journal). See `strategy/lexicon.md` for the full design + guardrails.

Phase 1 ships ONLY tags that are honestly computable from fields already on the item dict (no
lookahead, no new feeds). EMA/VWAP "reclaim" tags need a prior-bar-vs-line field that analyze() does
not yet emit — deferred to Phase 1b. True ATH (vs our 52-week window) is Phase 1b too.

A tag's grade weight stays ZERO until Phase 3 (a blind, no-curve-fit backtest proves edge + Burry
verifies). Until then `role`/`conviction` only drive how the UI sorts/caps the badges.
"""

# Role legend: S=setup · T=trigger/confirmation · C=context · X=trap/failure(long side).


def _num(v):
    """Coerce to float or return None — detectors must never raise on a missing/None/odd field."""
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# --- Phase-1 detectors -------------------------------------------------------
# Each takes the graded item dict, returns (fired: bool, meta: dict). Keep them O(1) and total.

def _d_stage2(it):
    # Minervini Stage-2: the 8-point trend template passes (internally requires ~200 bars, so this
    # also guards short-history/IPO names for free).
    return (bool(it.get("trend_template")), {})


def _d_rs_leader(it):
    rs = _num(it.get("rs_pct"))
    return (rs is not None and rs >= 90, {"rs_pct": rs})


def _d_52wh_bo(it):
    # Within 1.5% of the 52-week high AND a confirmed uptrend (trend_template gates short history).
    p = _num(it.get("pull_from_52w"))
    return (p is not None and p <= 1.5 and bool(it.get("trend_template")), {"pull_from_52w": p})


def _d_vdu(it):
    # Volume dry-up: recent volume well under its baseline (volc = recent/older vol ratio).
    vc = _num(it.get("volc"))
    return (vc is not None and vc < 0.65, {"volc": vc})


def _d_vcp(it):
    # Volatility Contraction Pattern — reuses the engine's own _vcp() result. UI dedups against the
    # existing dedicated VCP badge.
    return (bool(it.get("vcp")), {"contractions": it.get("vcp_contractions")})


def _d_ep(it):
    # Episodic Pivot — a true catalyst gap. grade_suggestions already relabels a gap WITHOUT good news
    # to "Breakout", so setup_type=="Episodic Pivot" here means a real EP.
    return (it.get("setup_type") == "Episodic Pivot", {})


def _d_bgu(it):
    # Buyable Gap-Up: a real opening gap. (Catalyst/price context — NOT a fundamentals filter.)
    g = _num(it.get("gap_up"))
    return (g is not None and g >= 4.0, {"gap_up": g})


def _d_parabolic(it):
    # Parabolic extension — a CAUTION context (trim/▲risk), not a buy signal.
    return (bool(it.get("parabolic")), {"ext50_adr": it.get("ext50_adr")})


def _d_tightness(it):
    # Tight base: range over the lookback is a small multiple of ADR (coiled before a move).
    tx = _num(it.get("tight_x"))
    return (tx is not None and tx <= 1.5, {"tight_x": tx})


def _d_yh_reclaim(it):
    # Trading back above yesterday's high WITHOUT a gap (distinguishes from BGU). prior_high is the
    # date-correct prior-session high that grade_suggestions sets.
    ph = _num(it.get("prior_high"))
    cl = _num(it.get("close"))
    g = _num(it.get("gap_up")) or 0.0
    return (ph is not None and cl is not None and cl > ph and g < 2.0, {"prior_high": ph})


# --- registry ----------------------------------------------------------------
# tag, name, role, conviction (0-1, drives UI sort/cap only), detector, define
REGISTRY = [
    ("EP",         "Episodic Pivot",          "S", 0.95, _d_ep,
     "A true catalyst gap that changes the story — the highest-conviction momentum entry."),
    ("52WH_BO",    "52-Week High Breakout",   "S", 0.90, _d_52wh_bo,
     "Within ~1.5% of the 52-week high in a confirmed uptrend — little overhead supply."),
    ("STAGE2",     "Stage-2 Uptrend",         "C", 0.85, _d_stage2,
     "Passes Minervini's 8-point trend template — a confirmed Stage-2 advance."),
    ("VCP",        "Volatility Contraction",  "S", 0.80, _d_vcp,
     "Minervini VCP: tightening contractions into a pivot, volume drying up."),
    ("RS_LEADER",  "Relative-Strength Leader","C", 0.75, _d_rs_leader,
     "Top-decile relative strength (RS ≥ 90) — a leader, not a laggard."),
    ("BGU",        "Buyable Gap-Up",          "S", 0.70, _d_bgu,
     "A real opening gap up (≥4%) — momentum ignition off a catalyst."),
    ("YH_RECLAIM", "Above Yesterday's High",  "T", 0.65, _d_yh_reclaim,
     "Trading back above the prior session's high (not via a gap) — an intraday trigger."),
    ("TIGHTNESS",  "Tight Base",              "C", 0.60, _d_tightness,
     "Price coiled tight (range ≤ 1.5× ADR) — primed to expand."),
    ("VDU",        "Volume Dry-Up",           "C", 0.55, _d_vdu,
     "Recent volume well below its baseline — sellers exhausted before a move."),
    ("PARABOLIC",  "Parabolic Extension",     "C", 0.50, _d_parabolic,
     "Extended far above the 50-EMA — a CAUTION: think trim / tighter risk, not a fresh buy."),
]

# Each registry tag is Phase-1 status "detected" (displays) — NONE are "grade-validated" (none weigh
# on the grade). Promotion happens one tag at a time through the Phase-3 backtest gate.


# --- Phase 2: the confirmation menu (tags drive the confirm engine) --------------------------------
# Per setup type, the ORDERED list of role-T trigger tags that can confirm the entry. This is the
# Lexicon vocabulary for "what confirms this setup" — pure DATA; `compute_now` maps each tag to its
# existing trigger function (ORH_BREAK→orh_confirm, YH_RECLAIM→breakout_confirm(prior_high),
# RECLAIM_50→the 50-reclaim path, HOD_BREAK→the today's-high lift). Only WIRED tags are listed here
# (ORB15 / PMH_BREAK / DVWAP are Phase 2b — not yet wired, so not shown, to stay honest).
SETUP_CONFIRM_MENU = {
    "Breakout":                 ["ORH_BREAK", "HOD_BREAK"],
    "Consolidation":            ["ORH_BREAK", "HOD_BREAK"],
    "Episodic Pivot":           ["ORH_BREAK", "HOD_BREAK"],
    "Pullback":                 ["YH_RECLAIM", "ORH_BREAK"],
    "Pullback @ AVWAP":         ["YH_RECLAIM", "ORH_BREAK"],
    "AVWAP reclaim (ATH)":      ["YH_RECLAIM", "ORH_BREAK"],
    "AVWAP reclaim (earnings)": ["YH_RECLAIM", "ORH_BREAK"],
    "Deep Pullback":            ["RECLAIM_50"],
}
_DEFAULT_CONFIRM_MENU = ["ORH_BREAK"]

# Human label per trigger tag — for the armed card's "waiting for …" text.
CONFIRM_TAG_LABEL = {
    "ORH_BREAK":  "opening-range-high break",
    "HOD_BREAK":  "today's-high break",
    "YH_RECLAIM": "reclaim of yesterday's high",
    "RECLAIM_50": "reclaim of the 50 EMA",
    "EMA_RECLAIM": "reclaim above the EMA cluster",
}


def get_confirm_menu(setup_type):
    """The ordered confirmation menu (trigger tags) for a setup type — the Lexicon vocabulary for
    'what confirms this entry'. Data only; the engine maps each tag to its existing trigger fn."""
    return SETUP_CONFIRM_MENU.get((setup_type or "").strip(), list(_DEFAULT_CONFIRM_MENU))


def confirm_menu_text(setup_type):
    """Readable 'waiting for: X or Y' phrase from the menu — for the armed card."""
    menu = get_confirm_menu(setup_type)
    labels = [CONFIRM_TAG_LABEL.get(t, t) for t in menu]
    return " or ".join(labels)


def detect_all(item):
    """Run the Phase-1 registry on a graded item dict. Returns a list of fired tag dicts:
    {tag, role, name, define, conviction, meta}, sorted by conviction (highest first).
    Pure: never mutates `item`, never raises (a misbehaving detector is skipped)."""
    if not isinstance(item, dict):
        return []
    out = []
    for tag, name, role, conviction, fn, define in REGISTRY:
        try:
            fired, meta = fn(item)
        except Exception:
            fired, meta = False, {}
        if fired:
            out.append({"tag": tag, "role": role, "name": name, "define": define,
                        "conviction": conviction, "meta": meta or {}})
    out.sort(key=lambda t: t["conviction"], reverse=True)
    return out
