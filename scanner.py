"""
Scanner engine for the Trading Data Center.

Fetches daily price data (Yahoo public chart API), computes momentum metrics in the
Qullamaggie / Martin-Luk style, classifies the setup, and returns structured trades
with entry / stop / invalidation / position size.

Setup preference (per the trader): Pullbacks and AVWAP reclaims (from all-time-high
or the last earnings gap) are favored over plain breakouts.

Standard library only.
"""
import json
import math
import os
import urllib.request
import urllib.parse
import http.cookiejar
import time
import threading
import statistics as st
from pathlib import Path
from datetime import datetime, timezone

import rubric

BASE = Path(__file__).resolve().parent
# Cache lives under DATA_DIR when set (so it persists on a host's volume), else data/.
CACHE = (Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else BASE / "data") / "cache"

# how strongly each setup type is preferred (added to the score).
# Pullbacks/AVWAP get a slight edge, but breakouts & EPs still rank competitively.
# Weights reflect the 2026-06-01 backtest: AVWAP-anchored pullbacks & Consolidations were the
# profitable setups; the generic "Pullback" (not at AVWAP) was the weakest — so it's nudged down.
PREF = {
    "Pullback @ AVWAP": 4,
    "Pullback": 2,
    "AVWAP reclaim (ATH)": 3,
    "AVWAP reclaim (earnings)": 2.5,
    "Episodic Pivot": 2,
    "Breakout": 1.5,
    "Deep Pullback": 3,
    "Consolidation": 4,
}

# Benchmarks for market regime + relative strength (equal-blend, per user choice).
INDEXES = [("SPX", "^GSPC"), ("QQQ", "QQQ"), ("IWM", "IWM")]

# Per-market configuration (US default + Israel / Tel Aviv Stock Exchange). Verified on
# Yahoo: TASE bars resolve via the .TA suffix and the indexes ^TA125.TA / TA35.TA exist.
# TASE trades Sunday-Thursday (closed Fri/Sat) and quotes prices in agorot (1/100 ILS) —
# all strategy math is price-relative so that's display/sizing-unit only. `tz_offset` is
# hours from UTC for the local session clock; `trading_days` are Python weekday() numbers
# (Mon=0 .. Sun=6). `uni` holds the universe liquidity filter thresholds for that market.
MARKETS = {
    "us": {"indexes": [("SPX", "^GSPC"), ("QQQ", "QQQ"), ("IWM", "IWM")], "ref": "SPY",
           "open": 9.5, "close": 16.0, "tz_offset": -4, "trading_days": (0, 1, 2, 3, 4),
           "currency": "USD", "currency_sym": "$",
           "uni": {"min_price": 10, "min_mktcap_m": 300, "min_dollar_vol_m": 10, "size": 800}},
    "il": {"indexes": [("TA125", "^TA125.TA"), ("TA35", "TA35.TA")], "ref": "^TA125.TA",
           "open": 9.9, "close": 17.25, "tz_offset": 3, "trading_days": (6, 0, 1, 2, 3),
           "currency": "ILS", "currency_sym": "₪",
           # NB: TASE quotes prices in AGOROT (1/100 ₪), so min_price + min_dollar_vol_m are in
           # agorot units (min_dollar_vol_m=500 -> 500M agorot = ₪5M/day turnover). marketCap is in ₪.
           # LIQUIDITY = ₪ TURNOVER, NOT share count: agorot pricing makes blue-chips (Elbit ~80k
           # shares but ₪190M/day) look thin by shares, so we gate on price×volume, never on raw
           # shares. ₪5M/day drops the untradeable long tail (~221 -> ~130 names) while keeping every
           # liquid leader. TASE has ~500 listed equities; size is generous, not a true cap.
           "uni": {"min_price": 50, "min_mktcap_m": 200, "min_dollar_vol_m": 500, "size": 300}},
}


def mcfg(market="us"):
    return MARKETS.get(market, MARKETS["us"])


# --------------------------------------------------------------------------- #
# Data fetching (with on-disk daily cache)
# --------------------------------------------------------------------------- #
def _normalize_unit_jumps(bars):
    """TASE tickers occasionally carry a ~100x unit discontinuity in Yahoo's daily series (an
    agorot↔shekel switch mid-history) — far larger than any real one-day move, and it corrupts
    every multi-day return (a sector reading +2000%/mo). Detect a ~100x (or ~1/100) step between
    ADJACENT closes and rescale the earlier side so the whole series is continuous in the MOST
    RECENT unit (what live quotes + the chart already use). No-op when there's no such step, so
    it's safe to run on every series."""
    n = len(bars)
    if n < 2:
        return bars
    c = [b["close"] for b in bars]
    f = [1.0] * n
    for i in range(n - 2, -1, -1):
        if c[i] <= 0:
            f[i] = f[i + 1]
            continue
        rr = c[i + 1] / c[i]
        if rr > 30:            # next bar ~100x bigger → earlier bars are in the smaller unit
            f[i] = f[i + 1] * 100
        elif rr < 1 / 30:      # next bar ~100x smaller → earlier bars are in the bigger unit
            f[i] = f[i + 1] / 100
        else:
            f[i] = f[i + 1]
    if all(x == 1.0 for x in f):
        return bars
    for i, b in enumerate(bars):
        if f[i] != 1.0:
            for k in ("open", "high", "low", "close"):
                b[k] = round(b[k] * f[i], 2)
    return bars


def _fetch_raw(sym):
    sym = sym.strip().upper()
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/"
               f"{sym}?interval=1d&range=1y")          # 1y so 200-MA + 52W-high are computable
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            res = d["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            vol = q.get("volume") or [None] * len(ts)
            bars = []
            for i in range(len(ts)):
                o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], vol[i]
                if None in (o, h, l, c):
                    continue
                bars.append({
                    "time": datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": round(o, 2), "high": round(h, 2),
                    "low": round(l, 2), "close": round(c, 2),
                    "volume": int(v) if v else 0,
                })
            if len(bars) > 60:
                if sym.endswith(".TA"):          # TASE: heal any agorot↔shekel unit step
                    bars = _normalize_unit_jumps(bars)
                return bars
        except Exception:
            time.sleep(0.4)
    return None


# Did the LAST get_bars call hit the network (cache miss)?  Thread-local so a concurrent HTTP
# request can't flip a global out from under a running scan (which reads it to decide throttling).
_FETCH_STATE = threading.local()

# In-process bar cache: sym -> (mtime, bars).  A warm disk cache still costs a stat + full JSON
# parse on every get_bars call; during an 800-name scan that is 800 re-parses.  We keep the parsed
# bars in memory keyed by the file's mtime, so a hit skips the read+parse entirely.  Naturally
# bounded by the universe size (~800-3000 syms); hard-capped below as a leak backstop.
_BARS_MEM = {}
_BARS_MEM_MAX = 3000


def did_fetch():
    """True if the most recent get_bars() call on THIS thread hit the network."""
    return getattr(_FETCH_STATE, "did_fetch", False)


def get_bars(sym, max_age_hours=12):
    _FETCH_STATE.did_fetch = False
    sym = sym.strip().upper()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{sym}.json"
    if f.exists():
        try:
            mtime = f.stat().st_mtime
            age = (time.time() - mtime) / 3600
            if age < max_age_hours:               # fresh enough to use the disk cache
                hit = _BARS_MEM.get(sym)
                if hit and hit[0] == mtime:        # in-memory hit — skip the read + JSON parse
                    return hit[1]
                obj = json.loads(f.read_text())
                bars = obj.get("bars")
                if bars:
                    _BARS_MEM[sym] = (mtime, bars)
                    return bars
        except Exception:
            pass
    bars = _fetch_raw(sym)
    _FETCH_STATE.did_fetch = True
    if bars:
        try:
            f.write_text(json.dumps({"sym": sym, "bars": bars}))
            if len(_BARS_MEM) > _BARS_MEM_MAX:     # leak backstop across universe changes
                _BARS_MEM.clear()
            _BARS_MEM[sym] = (f.stat().st_mtime, bars)
        except Exception:
            pass
    return bars


# --------------------------------------------------------------------------- #
# Earnings dates (Yahoo quoteSummary — needs a cookie + crumb, cached daily)
# --------------------------------------------------------------------------- #
# Yahoo's quoteSummary endpoint requires an auth crumb tied to a session cookie.
# We grab both once and reuse them; the calendarEvents module then gives the NEXT
# (confirmed or estimated) earnings date — used to warn off setups/positions with a
# binary print coming up. Degrades gracefully to None on any failure (the UI just
# hides the warning), so a Yahoo change can never break the scan.
_YF = {"opener": None, "crumb": None}


def _yahoo_session():
    if _YF["opener"] and _YF["crumb"]:
        return _YF["opener"], _YF["crumb"]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    try:                                    # seed the consent/session cookie
        op.open("https://fc.yahoo.com", timeout=10)
    except Exception:
        pass                                # a 404 here is fine — the cookie still sets
    try:
        with op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10) as r:
            crumb = r.read().decode().strip()
    except Exception:
        return None, None
    if not crumb or "<" in crumb or len(crumb) > 32:
        return None, None
    _YF["opener"], _YF["crumb"] = op, crumb
    return op, crumb


def _fetch_earnings(sym):
    op, crumb = _yahoo_session()
    if not op:
        return None
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
           f"?modules=calendarEvents&crumb={urllib.parse.quote(crumb)}")
    try:
        with op.open(url, timeout=12) as r:
            d = json.load(r)
        earn = (d["quoteSummary"]["result"][0].get("calendarEvents", {}) or {}).get("earnings", {}) or {}
        dates = earn.get("earningsDate") or []
        ts = dates[0].get("raw") if dates else None
        if not ts:
            return None
        return {"date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                "ts": ts, "estimate": bool(earn.get("isEarningsDateEstimate"))}
    except Exception:
        _YF["opener"] = _YF["crumb"] = None   # crumb may have expired — re-auth next call
        return None


_QUOTE_CACHE = {}                       # sym -> {price, prev_close, change_pct, market_state, _t}
_QUOTE_HEALTH = {"last_ok": None, "last_err": None}   # Yahoo price-feed heartbeat (for the /api/health vital signs)


def quote_health():
    """Yahoo price-feed health: when we last got a good quote response, and the last error. The /api/health
    endpoint turns this into the green/red 'Yahoo connected' dot in the app + website vital-signs indicator."""
    return dict(_QUOTE_HEALTH)


def fetch_quotes(symbols, max_age=30):
    """Batched live quotes from Yahoo's v7/quote (cookie+crumb), ~30s cached so rapid polls /
    multiple users don't hammer Yahoo. Returns {SYM: {price, prev_close, change_pct, market_state}}.
    During pre/post-market the active session price is used so the page shows what's moving now."""
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    now = time.time()
    out, missing = {}, []
    for s in syms:
        c = _QUOTE_CACHE.get(s)
        if c and now - c["_t"] < max_age:
            out[s] = c
        else:
            missing.append(s)
    if missing:
        op, crumb = _yahoo_session()
        if op and crumb:
            for i in range(0, len(missing), 50):
                chunk = missing[i:i + 50]
                url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                       + urllib.parse.quote(",".join(chunk)) + "&crumb=" + urllib.parse.quote(crumb))
                try:
                    with op.open(url, timeout=12) as r:
                        d = json.load(r)
                    for q in d.get("quoteResponse", {}).get("result", []):
                        sym = q.get("symbol")
                        if not sym:
                            continue
                        reg_price = q.get("regularMarketPrice")
                        reg_chg = q.get("regularMarketChangePercent", 0)
                        ms = q.get("marketState")
                        # extended-hours (pre/after) print kept SEPARATE from the regular-session
                        # price so the UI can split "today's P&L" (regular) from pre/after-hours P&L.
                        ext_price = ext_chg = None
                        if ms in ("PRE", "PREPRE") and q.get("preMarketPrice"):
                            ext_price, ext_chg = q["preMarketPrice"], q.get("preMarketChangePercent")
                        elif ms in ("POST", "POSTPOST", "CLOSED") and q.get("postMarketPrice"):
                            ext_price, ext_chg = q["postMarketPrice"], q.get("postMarketChangePercent")
                        # `price`/`change_pct` stay the "live" values (extended when in pre/post) so
                        # existing live position P&L and chart overlays are unchanged.
                        price = ext_price if ext_price is not None else reg_price
                        chg = ext_chg if ext_chg is not None else reg_chg
                        rec = {"price": round(price, 2) if price is not None else None,
                               "reg_price": round(reg_price, 2) if reg_price is not None else None,
                               "ext_price": round(ext_price, 2) if ext_price is not None else None,
                               "ext_change_pct": round(ext_chg, 2) if ext_chg is not None else None,
                               "prev_close": q.get("regularMarketPreviousClose"),
                               "change_pct": round(chg, 2) if chg is not None else None,
                               # today's regular-session range — feeds the intraday rotation entry
                               # (buy the reclaim of the prior-day high, stop at the day's low)
                               "day_high": q.get("regularMarketDayHigh"),
                               "day_low": q.get("regularMarketDayLow"),
                               "day_open": q.get("regularMarketOpen"),
                               "market_state": ms, "_t": now}
                        _QUOTE_CACHE[sym] = rec
                        out[sym] = rec
                    _QUOTE_HEALTH["last_ok"] = now           # a good Yahoo response landed
                except Exception:
                    _QUOTE_HEALTH["last_err"] = now
                    _YF["opener"] = _YF["crumb"] = None     # crumb may have expired
    return out


def get_earnings(sym, max_age_hours=24):
    """Next earnings date for `sym` ({date, ts, estimate}) or None. Disk-cached daily."""
    sym = sym.strip().upper()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{sym}.earn.json"
    if f.exists():
        try:
            age = (time.time() - f.stat().st_mtime) / 3600
            obj = json.loads(f.read_text())
            # cache hits last a day; a cached miss (None) is only trusted ~6h so a transient
            # Yahoo hiccup doesn't suppress the date all day.
            if obj.get("earnings") is not None and age < max_age_hours:
                return obj["earnings"]
            if obj.get("earnings") is None and age < 6:
                return None
        except Exception:
            pass
    e = _fetch_earnings(sym)
    try:
        f.write_text(json.dumps({"sym": sym, "earnings": e}))
    except Exception:
        pass
    return e


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def _sma(a, n):
    return sum(a[-n:]) / n


def _ema(a, n):
    k = 2 / (n + 1)
    e = a[0]
    for x in a[1:]:
        e = x * k + e * (1 - k)
    return e


def _avwap(bars, anchor):
    num = den = 0.0
    for b in bars[anchor:]:
        tp = (b["high"] + b["low"] + b["close"]) / 3
        num += tp * b["volume"]
        den += b["volume"]
    return num / den if den else bars[-1]["close"]


def _earnings_anchor(bars, lookback=75):
    """Proxy for the last earnings day: the biggest gap-up in recent history."""
    best_i, best_gap = None, 0.0
    start = max(1, len(bars) - lookback)
    for i in range(start, len(bars)):
        prev_c = bars[i - 1]["close"]
        if prev_c <= 0:
            continue
        gap = (bars[i]["open"] / prev_c - 1) * 100
        if gap > best_gap:
            best_gap, best_i = gap, i
    return (best_i, best_gap) if best_gap >= 6 else (None, best_gap)


def _swing_highs(h, left=3, right=3, lookback=60):
    """Pivot highs: bars whose high is the local max within a +/- window.
    Returns [(index, price)] over the last `lookback` bars. Used to find a prior
    swing high that price has reclaimed (resistance-turned-support) for stop placement."""
    n = len(h)
    start = max(left, n - lookback)
    out = []
    for i in range(start, n - right):
        if h[i] == max(h[i - left:i + right + 1]):
            out.append((i, h[i]))
    return out


def _pullback_leg_pivot(h, l, close, adr):
    """[PART B — dev/breakout, 2026-06-08]  The breakout '2nd entry' pivot for a PATIENT setup.

    The trader's 'buy the right side' rule: 1st entry = the pullback/reclaim AT the line (EMA/AVWAP/
    pivot); if missed, the 2nd entry = the BREAKOUT back THROUGH that pivot — the swing high the stock
    PULLED BACK FROM. The live alt used max(h[-2:]) (the prior-day high), which after a multi-week base
    sits far above the EMAs ('too far'). Instead anchor at the NEARER pivot:

      Definition (no lookahead — all bars are <= today):
        1. Find the pullback LOW = the lowest low since the most-recent confirmed swing high
           (the leg the stock just pulled back on).
        2. Among confirmed swing highs (_swing_highs, left/right=3), keep those that are
             - ABOVE current price (a genuine upside trigger, price has pulled back below them),
             - the swing high that bounds the pullback leg (index <= the pullback-low index, i.e. the
               level price fell FROM), and
             - within a sane band above price (<= ~6x ADR) so we don't grab an ancient unrelated high.
        3. Take the LOWEST such qualifying pivot = the NEAREST overhead level = where it's basing
           under. That is the realistic 2nd-entry trigger.

      Returns the pivot price, or None (caller falls back to max(h[-2:]) — the old behaviour)."""
    sh = _swing_highs(h)
    if not sh or close <= 0:
        return None
    adr_px = close * adr / 100 or 0.01
    last_sh_i = sh[-1][0]
    # pullback low = lowest low from the last swing high forward to now (the leg just pulled back)
    seg = l[last_sh_i:]
    if not seg:
        return None
    pb_low = min(seg)
    pb_low_i = last_sh_i + seg.index(pb_low)
    # candidate pivots: confirmed swing highs ABOVE price that the pullback fell from, near enough
    band_top = close + 6.0 * adr_px
    cands = [p for (i, p) in sh
             if p > close + 0.1 * adr_px        # a real upside trigger (not basically at price)
             and i <= pb_low_i                  # the level price pulled back FROM (bounds the leg)
             and p <= band_top]                 # not an ancient, unrelated high
    if not cands:
        return None
    return min(cands)                            # the NEAREST overhead pivot


def _breakout_pivot(o, h, l, c, adr_px, lookback=10, min_base=4):
    """The breakout TRIGGER = the high of the CURRENT contraction, de-wicked (user 2026-06-09, INTC).

    The old `max(high[-10:])` had two failure modes (INTC: pivot 126.64, ~15% above price, from a
    May-29 distribution bar that closed −12 below its high):
      1. a single REJECTION/distribution bar's spike (or its body, on a gap-up-and-fade) became the
         pivot — a stale level price already FAILED at, not the edge of the base it's coiling in now;
      2. a fixed 10-bar window reached back INTO the prior leg's distribution after the stock re-based
         lower, so the trigger sat far above the real, recent base (INTC's June base topped ~113).

    Fix — "de-wick + recent base":
      * RECENT BASE: start the window AFTER the most recent SHARP leg-down close (> ~1.3× ADR down day
        = the move that ended the prior shelf), so we measure the coil the stock is in NOW. Bounded to
        [min_base, lookback] bars so a brand-new break still leaves a real base to read.
      * DE-WICK: a bar that closed in the bottom third of its range FAILED at the highs — it
        contributes its CLOSE, not its spike high. A clean bar contributes its real high.
    Returns the de-wicked recent-base high (the actionable breakout trigger)."""
    n = len(h)
    if n == 0:
        return 0.0
    lb = min(lookback, n)
    start = n - lb
    for i in range(n - 1, n - lb, -1):                  # walk back; stop at the last sharp down-close
        if (c[i - 1] - c[i]) > 1.3 * adr_px:
            start = i
            break
    start = max(0, min(start, n - min_base))            # always keep ≥ min_base bars of base
    eff = []
    for i in range(start, n):
        rng = h[i] - l[i]
        rejected = rng > 0 and (c[i] - l[i]) < 0.34 * rng   # closed in the bottom third = failed at highs
        eff.append(c[i] if rejected else h[i])
    return max(eff) if eff else h[-1]


# --------------------------------------------------------------------------- #
# Entry-plan builders. A suggestion can carry up to two entry options (a buy-stop
# BREAKOUT above a pivot, and/or a buy-the-dip PULLBACK to support below price).
# Each returns a self-contained plan dict (entry/stop/zone/sizing-ready) or None
# when the option doesn't make sense — so we "show two only where it makes sense."
# --------------------------------------------------------------------------- #
def _breakout_stop(entry, day_low, adr, pivot=None):
    """A breakout's stop anchors JUST UNDER THE BREAKOUT PIVOT (the level being broken), NOT the
    day's low.  [PART A — dev/breakout, 2026-06-08]

    OLD behaviour (live): stop = today's (breakout-day) low. That works when the breakout fires near
    the highs, but when the pivot sits well ABOVE current price (price has pulled back below it), the
    day-low is far below the entry → a misleadingly huge risk (the LUNR case: entry $32.69, day-low
    stop $29.37 = ~10% = well over 1x ADR). The risk should be entry − (just under the pivot), tight,
    regardless of how far below current price the day's low happens to be.

    NEW behaviour:
      * Anchor under the pivot: `pivot × (1 − buffer)`, buffer ≈ 0.25x ADR (a structural cushion).
      * CLAMP the stop distance to [0.3x ADR, 1.0x ADR] below the entry — a hard ≤1x ADR cap
        (my-rules: 'stop never wider than 1x ADR'), and a 0.3x floor so noise can't wick it out.
      * Use the day's LOW only when it is the TIGHTER VALID structure — i.e. the day-low sits ABOVE
        the just-under-pivot anchor but still below the entry (the clean-breakout-near-the-highs case,
        which stays ~unchanged: there the day-low is close to the pivot anyway).
      * Falls back to the old 1x-ADR-below-trigger only when no pivot is supplied (back-compat).

    Returns (stop, basis)."""
    e_adr = entry * adr / 100 or 0.01
    max_floor = entry - 1.0 * e_adr           # never risk more than 1x ADR (the hard cap)
    tight_floor = entry - 0.3 * e_adr         # never tighter than 0.3x ADR (RKLB noise lesson)
    # PREFERENCE: the day's low is the real, lived structure — KEEP it whenever it's a usable stop
    # INSIDE 1x ADR (this is the clean-breakout-near-the-highs case → byte-for-byte the old behaviour).
    if day_low and 0 < day_low < entry and day_low >= max_floor:
        stop = min(day_low, tight_floor)      # but never tighter than 0.3x ADR
        return round(stop, 2), "today's low (breakout-day low)"
    # Otherwise the day low is too far below the entry (price has pulled back below the pivot — the
    # LUNR case) → anchor JUST UNDER THE PIVOT, clamped into [0.3x, 1.0x] ADR.
    if pivot and 0 < pivot <= entry:
        piv_stop = pivot * (1 - 0.25 * adr / 100)     # just under the pivot (0.25x ADR cushion)
        piv_stop = max(piv_stop, max_floor)           # respect the 1x ADR cap
        piv_stop = min(piv_stop, tight_floor)         # respect the 0.3x ADR tightness floor
        return round(piv_stop, 2), "just under the breakout pivot"
    # no pivot supplied and no usable day low -> the old 1x-ADR-below-trigger fallback
    return round(max_floor, 2), "1x ADR below the trigger"


def _breakout_plan(pivot, close, adr, day_low=None, note="buy-stop above the pivot high"):
    """Buy-STOP above an upside pivot, stop at today's low. The pivot must sit at/above the
    current price (a genuine upside trigger), else there's nothing to break out over."""
    if not pivot or pivot <= 0 or pivot < close:
        return None
    entry = round(pivot, 2)
    e_adr = entry * adr / 100 or 0.01
    stop, basis = _breakout_stop(entry, day_low, adr, pivot=pivot)   # PART A: anchor under the pivot
    risk_ps = round(entry - stop, 2)
    if risk_ps <= 0:
        return None
    zone_bottom = round(entry, 2)
    zone_top = round(entry + 0.5 * e_adr, 2)
    buf = 0.3 * e_adr
    return {
        "kind": "breakout", "entry_type": "stop", "entry": entry, "stop": stop,
        "stop_basis": basis, "inval": stop,
        "risk_ps": risk_ps, "target": round(entry + 2 * risk_ps, 2),
        "zone_bottom": zone_bottom, "zone_top": zone_top,
        "buyable_now": (zone_bottom - buf) <= close <= (zone_top + buf),
        "entry_note": note, "trigger_note": f"break above ${entry} to trigger",
    }


def _pullback_plan(close, adr, supports, recent_low, sh_prices):
    """Buy-LIMIT into the nearest meaningful support BELOW price. `supports` is a list
    of (label, price); we pick the highest one that's at least ~0.4x ADR under the close
    (so it's a real, distinct dip — not basically today's price). Stop anchors to the
    tighter of the 5-day structure low / a reclaimed swing high, clamped 0.45-1.2x ADR.
    Returns None when no support sits meaningfully below price (don't force a 2nd option)."""
    adr_px = close * adr / 100 or 0.01
    below = [(lbl, v) for lbl, v in supports if v and v <= close - 0.4 * adr_px]
    if not below:
        return None
    lbl, sup = max(below, key=lambda x: x[1])          # nearest support beneath price
    entry = round(sup, 2)
    e_adr = entry * adr / 100 or 0.01
    wide_floor = entry - 1.2 * e_adr
    tight_cap = entry - 0.45 * e_adr
    struct = recent_low - 0.15 * e_adr
    reclaimed = [p for p in sh_prices if p < entry - 0.02 * e_adr]
    sh_stop = (max(reclaimed) - 0.10 * e_adr) if reclaimed else None
    cands = []
    if struct >= wide_floor:
        cands.append((struct, "5-day structure low"))
    if sh_stop is not None and sh_stop >= wide_floor:
        cands.append((sh_stop, f"reclaimed swing high ${round(max(reclaimed), 2)} (close below)"))
    if cands:
        raw, basis = max(cands, key=lambda x: x[0])
    else:
        raw, basis = max(wide_floor, struct), "1.2x ADR limit"
    stop = round(min(raw, tight_cap), 2)
    risk_ps = round(entry - stop, 2)
    if risk_ps <= 0:
        return None
    zone_top = round(entry + 0.6 * e_adr, 2)
    zone_bottom = round(entry - 0.25 * e_adr, 2)
    buf = 0.3 * e_adr
    return {
        "kind": "pullback", "entry_type": "limit", "entry": entry, "stop": stop,
        "stop_basis": basis, "inval": round(recent_low, 2),
        "risk_ps": risk_ps, "target": round(entry + 2 * risk_ps, 2),
        "zone_bottom": zone_bottom, "zone_top": zone_top,
        "buyable_now": (zone_bottom - buf) <= close <= (zone_top + buf),
        "entry_note": f"buy the pullback into the {lbl} (limit, below ${round(close, 2)})",
        "trigger_note": f"wait for the pullback to ${entry}",
    }


def _respected_level(h, l, level, tol, min_touches=2, lookback=120):
    """Has the market RESPECTED this price — is it real S/R, or a mid-air number (the TSEM case)? Counts
    DISTINCT prior bars (within `lookback`, excluding the most recent ~2 = the current dip) whose high or
    low came within `tol` of the level. >= min_touches separated touches = a level buyers/sellers have
    defended before. Used so a pullback 'buy zone' anchors to a level the market respects, not a freefall
    recent low that price is slicing through right now."""
    if not level or level <= 0:
        return False
    n = len(l)
    start = max(0, n - lookback)
    touches, last_i = 0, -10
    for i in range(start, n - 2):                 # exclude the most recent ~2 bars (the live dip)
        if abs(l[i] - level) <= tol or abs(h[i] - level) <= tol:
            if i - last_i >= 2:                   # distinct touches, not one cluster
                touches += 1
                last_i = i
    return touches >= min_touches


def _respected_bounce(o, h, l, c, support, adr_px):
    """EOD daily proxy of the user's pullback rule (2026-06-05, the TSEM case): a pullback is buyable
    ONLY when price has pulled back TO support, RESPECTED it (didn't close decisively below it), and is
    JUMPING off it — NOT while it's still falling through the zone in mid-air. True when, in the last ~3
    bars, a low REACHED the support, the latest close is not >1% below it, AND today is a bounce: a GREEN
    reclaim candle closing at/above support (the same-day 'spin' proxy) OR a green bar taking out the
    prior bar's high after support held yesterday (the next-day jump). Backtest-validated (research_bounce):
    vs the passive limit fill it ~halves trades, lifts win rate, and ~doubles avg winsorized R."""
    if not support or support <= 0 or len(c) < 3:
        return False
    tol = max(0.003 * support, 0.15 * (adr_px or 0))
    if min(l[-3:]) > support + tol:                       # never reached support in the last ~3 bars
        return False
    if c[-1] < support * 0.99:                            # closed >1% under support = it broke = a knife
        return False
    green = c[-1] > o[-1]
    reclaim = green and c[-1] >= support - tol            # same-day green reclaim off support (spin proxy)
    nextday_jump = green and h[-1] > h[-2] and l[-2] <= support + tol   # held yesterday, breaks its high
    return bool(reclaim or nextday_jump)


def _vcp(bars, lookback=60):
    """Volatility Contraction Pattern (Minervini) — APPROXIMATE detector. A VCP is a series of
    successive pullbacks of DECREASING depth (the price coiling tighter) on DRYING volume, near the
    top of a base — the 'line of least resistance' before a breakout. Exact thresholds aren't pinned
    in primary sources (see strategy/minervini.md open questions), so these are defensible
    community-standard params, tunable: >=2 contractions, each <=0.8x the prior, last one tight
    (<=12%), price within ~8% of the base high, recent volume below the prior base's volume.
    Returns {vcp, contractions, depth_last, vol_dry, pivot}."""
    n = len(bars)
    if n < 30:
        return {"vcp": False, "contractions": 0}
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]
    v = [b["volume"] for b in bars]
    L = R = 3
    raw = [i for i in range(max(L, n - lookback), n - R) if h[i] == max(h[i - L:i + R + 1])]
    # collapse plateaus / adjacent equal-high runs (flat or illiquid names spam "pivots") — keep the
    # highest pivot in each cluster, require pivots spaced >= 4 bars apart.
    piv = []
    for i in raw:
        if piv and i - piv[-1] < 4:
            if h[i] >= h[piv[-1]]:
                piv[-1] = i
        else:
            piv.append(i)
    if len(piv) < 2:
        return {"vcp": False, "contractions": 0}
    depths = []                                            # peak-to-trough drawdown of each leg
    for k in range(len(piv)):
        start = piv[k]
        end = piv[k + 1] if k + 1 < len(piv) else n - 1
        if end <= start:
            continue
        ph = h[start]
        trough = min(l[start:end + 1])
        if ph > 0:
            depths.append((ph - trough) / ph * 100)
    if not (2 <= len(depths) <= 6):                        # a real VCP is a handful of legs, not noise
        return {"vcp": False, "contractions": len(depths)}
    tightening = all(depths[i] <= depths[i - 1] * 0.8 for i in range(1, len(depths)))
    deep_enough = max(depths) >= 8.0                       # at least one real ~8%+ contraction (not flatline)
    last_tight = 1.0 <= depths[-1] <= 12.0                 # final leg tight but real (excludes ~0% flatlines)
    base_high = max(h[-lookback:])
    near = base_high > 0 and (base_high / c[-1] - 1) * 100 <= 8.0
    vol_dry = (st.mean(v[-10:]) < st.mean(v[-30:-10])) if n >= 30 and st.mean(v[-30:-10]) > 0 else False
    vcp = tightening and deep_enough and last_tight and near
    return {"vcp": bool(vcp), "contractions": len(depths),
            "depth_last": round(depths[-1], 1), "vol_dry": bool(vol_dry), "pivot": round(base_high, 2)}


def regression_channel(bars, max_lookback=120, min_len=30):
    """Linear-regression channel on LOG price, ANCHORED to the most recent major low so it fits
    the current trend leg (a fixed window mis-fits V-shaped moves and the bands blow out). Bands
    are an envelope that touches the extreme deviations (capped at ~2.4 sd so a single spike can't
    distort it), so the 'tunnel' hugs the price like a hand-drawn channel. LOG fit => straight on
    a log chart."""
    n = len(bars)
    if n < min_len:
        return None
    window = bars[-min(max_lookback, n):]
    mw = len(window)
    lows = [b["low"] for b in window]
    lo_i = lows.index(min(lows))
    start = max(0, min(lo_i, mw - min_len))               # anchor at the leg low, keep >= min_len bars
    seg = [b for b in window[start:] if b["close"] > 0]
    m = len(seg)
    if m < min_len:
        return None
    xs = list(range(m))
    ys = [math.log(b["close"]) for b in seg]
    mx = sum(xs) / m
    my = sum(ys) / m
    denom = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(m)) / denom
    intercept = my - slope * mx
    resid = [ys[i] - (slope * xs[i] + intercept) for i in range(m)]
    sd = (sum(r * r for r in resid) / m) ** 0.5 or 1e-9
    up = min(max(resid), 2.0 * sd)                         # envelope, but capped so spikes don't distort
    dn = max(min(resid), -2.0 * sd)
    upper, mid, lower = [], [], []
    for i in range(m):
        base = intercept + slope * xs[i]
        t = seg[i]["time"]
        mid.append({"time": t, "value": round(math.exp(base), 2)})
        upper.append({"time": t, "value": round(math.exp(base + up), 2)})
        lower.append({"time": t, "value": round(math.exp(base + dn), 2)})
    return {"upper": upper, "mid": mid, "lower": lower}


def _pivots(vals, kind="high", left=2, right=2):
    """Local pivot indices: where vals[i] is the max (kind='high') / min ('low') in a ±window."""
    n = len(vals)
    out = []
    for i in range(left, n - right):
        win = vals[i - left:i + right + 1]
        if (kind == "high" and vals[i] == max(win)) or (kind == "low" and vals[i] == min(win)):
            out.append(i)
    return out


def _fit_line(idxs, vals):
    """Least-squares (slope, intercept) for points (idx, vals[idx])."""
    m = len(idxs)
    if m < 2:
        return 0.0, (vals[idxs[0]] if idxs else 0.0)
    mx = sum(idxs) / m
    my = sum(vals[i] for i in idxs) / m
    den = sum((x - mx) ** 2 for x in idxs) or 1
    sl = sum((idxs[k] - mx) * (vals[idxs[k]] - my) for k in range(m)) / den
    return sl, my - sl * mx


def detect_pattern(bars, lookback=45, min_cons=6):
    """Detect the ONE most-relevant consolidation pattern after a recent pole — a flag / pennant / wedge —
    for the chart overlay (replaces the generic channel when found). A pole = a real up-move (≥12%) into a
    recent high; the consolidation is the base/pullback after it, bounded by an upper trendline (through the
    pivot highs) and a lower trendline (through the pivot lows). Classified by the two slopes + convergence:
    parallel & down = Bull Flag, converging symmetric = Pennant, converging both-down = Falling Wedge, etc.
    Returns {kind,label,bull,upper:[2pts],lower:[2pts],pole:[2pts],pole_gain} or None."""
    n = len(bars)
    if n < min_cons + 6:
        return None
    win = bars[-min(lookback, n):]
    m = len(win)
    h = [b["high"] for b in win]
    l = [b["low"] for b in win]
    c = [b["close"] for b in win]
    hi_i = max(range(m), key=lambda i: h[i])                 # pole top = highest high in the window
    if (m - hi_i) < min_cons or hi_i < 2:                    # need a base after the high AND a pole before it
        return None
    lo_before = min(range(0, hi_i + 1), key=lambda i: l[i])
    pole_gain = (h[hi_i] / l[lo_before] - 1) * 100 if l[lo_before] > 0 else 0
    if pole_gain < 12:                                        # not a real flagpole — fall back to the channel
        return None
    # Fit the envelope on the consolidation EXCLUDING the latest bar, so we can test whether the CURRENT
    # candle has BROKEN the pattern (a broken pattern is no use). Upper = trend of the highs shifted UP to
    # touch the highest high (a real ceiling); lower = trend of the lows shifted DOWN to the lowest low.
    cidx = list(range(hi_i, m))
    fit_idx = cidx[:-1] if len(cidx) > min_cons else cidx
    if len(fit_idx) < 3:
        return None
    su, iu = _fit_line(fit_idx, h)
    sl, il = _fit_line(fit_idx, l)
    iu += max(h[i] - (su * i + iu) for i in fit_idx)         # shift ceiling up to the extreme high
    il += min(l[i] - (sl * i + il) for i in fit_idx)         # shift floor down to the extreme low
    x0, x1 = hi_i, m - 1
    up0, up1 = su * x0 + iu, su * x1 + iu
    lo0, lo1 = sl * x0 + il, sl * x1 + il
    # VALIDITY — is the pattern still intact? If the latest CLOSE has cleared the ceiling (breakout fired)
    # or lost the floor (pattern failed), the consolidation is over → no use drawing it (fall back to channel).
    last = c[-1]
    if last > up1 * 1.005 or last < lo1 * 0.985:
        return None
    rng0, rng1 = max(up0 - lo0, 1e-9), max(up1 - lo1, 1e-9)
    conv = rng1 / rng0
    mid = c[hi_i] or 1
    su_pct, sl_pct = su / mid * 100, sl / mid * 100          # slopes in %/bar
    converging = conv < 0.66                                  # range narrowed ≥1/3 toward the apex
    if converging:
        if su_pct < -0.03 and sl_pct > 0.03:
            kind, label, bull = "pennant", "Bull Pennant", True
        elif su_pct <= 0 and sl_pct <= 0:
            kind, label, bull = "falling_wedge", "Falling Wedge", True
        elif su_pct >= 0 and sl_pct >= 0:
            kind, label, bull = "rising_wedge", "Rising Wedge", False
        else:
            kind, label, bull = "pennant", "Pennant", True
    else:
        if su_pct < -0.05:
            kind, label, bull = "bull_flag", "Bull Flag", True
        elif su_pct > 0.05:
            kind, label, bull = "rising_channel", "Rising Channel", True
        else:
            kind, label, bull = "flag", "Tight Flag", True

    def line(v0, v1):
        return [{"time": win[x0]["time"], "value": round(v0, 2)},
                {"time": win[x1]["time"], "value": round(v1, 2)}]
    return {"kind": kind, "label": label, "bull": bull,
            "upper": line(up0, up1), "lower": line(lo0, lo1),
            "pole": [{"time": win[lo_before]["time"], "value": round(l[lo_before], 2)},
                     {"time": win[hi_i]["time"], "value": round(h[hi_i], 2)}],
            "pole_gain": round(pole_gain, 1)}


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def analyze(sym, bars, settings=None, forming_last=False, force_no_deep=False):
    # force_no_deep=True disables the "Deep Pullback" classification path entirely, so the name falls
    # through to whatever it naturally classifies as (plain Pullback / Breakout / etc.). The post-loop
    # (scan -> _attach_rs ranking) sets this and RE-CALLS analyze for any provisional Deep Pullback that
    # FAILS the leadership bar (rs_pct>=70 OR trend_template), once the true cross-universe percentile
    # exists. Default False => single-pass behavior is byte-for-byte unchanged. (2026-06-09 leader gate.)
    # forming_last=True means bars[-1] is TODAY'S STILL-FORMING daily bar (its close = the live price,
    # not a settled close). The caller sets it only during a live session (see _session_today_if_open).
    # It changes ONE thing: the respected-bounce gate below evaluates SETTLED structure (drops the
    # forming bar) so a name that CLOSED below the 50 yesterday can't flip "buyable now" on an
    # unsettled intraday pop — the live confirmation engine owns the real reclaim+spin call. Default
    # False ⇒ EOD/backtest/golden behavior is byte-for-byte unchanged.
    settings = settings or {}
    # Drop corrupt bars (a stray 0.0 OHLC from a Yahoo glitch). One such bar poisons every
    # min-low / ratio downstream (div-by-zero) and would silently drop the whole ticker from the
    # scan via the caller's try/except. Filtering keeps the name in the scan instead.
    bars = [b for b in bars if b.get("low", 0) > 0 and b.get("high", 0) > 0 and b.get("close", 0) > 0]
    if len(bars) < 60:
        raise ValueError("insufficient valid bars")
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    v = [b["volume"] for b in bars]
    o = [b["open"] for b in bars]
    close = c[-1]

    _adr_rng = [(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0]
    adr = st.mean(_adr_rng) if _adr_rng else 0   # guard: all-zero/bad lows -> empty list would crash st.mean
    adr_safe = adr or 1
    s10, s20, s50 = _sma(c, 10), _sma(c, 20), _sma(c, 50)
    s150 = _sma(c, 150) if len(c) >= 150 else None
    s200 = _sma(c, 200) if len(c) >= 200 else None
    e10, e20, e50 = _ema(c, 10), _ema(c, 20), _ema(c, 50)
    e9, e21 = _ema(c, 9), _ema(c, 21)        # the trader reads 9/21/50 — used for the break-above-EMA entry
    above = int(close > s10) + int(close > s20) + int(close > s50)
    ext10 = (close / e10 - 1) * 100
    p1m = (close / c[-21] - 1) * 100 if len(c) >= 21 else 0
    p3m = (close / c[-63] - 1) * 100 if len(c) >= 63 else 0
    p6m = (close / c[-126] - 1) * 100 if len(c) >= 126 else 0
    volc = (st.mean(v[-5:]) / st.mean(v[-25:-5])) if len(v) >= 25 and st.mean(v[-25:-5]) > 0 else 0

    # ----- liquidity (avg daily dollar volume) -> a 0-100 score on a log scale -----
    # No liquidity = no institutions. ~$10M/d scores low, ~$1B/d ~90, $5B+/d = 100.
    dv = st.mean([c[k] * v[k] for k in range(-20, 0)]) if len(c) >= 20 else c[-1] * v[-1]
    dollar_vol = round(dv)
    liq_score = round(max(0.0, min(100.0, (math.log10(dv) - 6.7) / 2.5 * 100))) if dv > 0 else 0

    # ----- 52-week-high distance + MA stack (for the 1M/3M/6M momentum screens) -----
    hi_52w = max(h)
    lo_52w = min(l)
    pull_from_52w = round((hi_52w / close - 1) * 100, 1)
    above_50 = close > s50
    above_200 = s200 is not None and close > s200
    ma_stack = s200 is not None and s50 > s200            # 50-MA over 200-MA (golden-cross trend)
    near_high = pull_from_52w <= 20

    # ----- Minervini Trend Template (Stage-2 leader filter; the RS criterion is added later in
    # _attach_rs once the universe-relative RS rating exists). Verified criteria (refuted variants
    # excluded): price > 50/150/200 SMAs; 50>150>200 alignment; 200-SMA rising ~1mo; >=30% above the
    # 52w low; within 25% of the 52w high. -----
    tt_flags, tt_pass = {}, False
    if s150 is not None and s200 is not None and lo_52w > 0:
        s200_prev = _sma(c[:-22], 200) if len(c) >= 222 else None      # 200-SMA ~1 month ago
        tt_flags = {
            "above50": close > s50, "above150": close > s150, "above200": close > s200,
            "stack": s50 > s150 > s200,
            "ma200_up": s200_prev is not None and s200 > s200_prev,
            "above_low": close >= lo_52w * 1.30,
            "near_high": pull_from_52w <= 25,
        }
        tt_pass = all(tt_flags.values())
    tt_count_price = sum(1 for v in tt_flags.values() if v)            # 0-7 (RS adds an 8th later)
    vcp = _vcp(bars)
    screen_1m = p1m >= 20 and above_50 and near_high
    screen_3m = p3m >= 25 and above_50 and above_200 and ma_stack and near_high and adr >= 3.5
    screen_6m = p6m >= 30 and above_50 and above_200 and ma_stack and near_high

    # recent swing / pullback geometry
    hi40 = max(h[-40:]) if len(h) >= 40 else max(h)
    pull_from_high = (hi40 / close - 1) * 100
    dist_e10 = (close / e10 - 1) * 100
    dist_e20 = (close / e20 - 1) * 100
    near_line, near_dist = ("10EMA", abs(dist_e10)) if abs(dist_e10) <= abs(dist_e20) else ("20EMA", abs(dist_e20))

    # anchored VWAPs
    ath_anchor = max(range(len(h)), key=lambda i: h[i])
    avwap_ath = _avwap(bars, ath_anchor)
    dist_avwap_ath = (close / avwap_ath - 1) * 100
    earn_i, earn_gap = _earnings_anchor(bars)
    avwap_earn = _avwap(bars, earn_i) if earn_i is not None else None
    dist_avwap_earn = (close / avwap_earn - 1) * 100 if avwap_earn else None

    # breakout geometry (for fallback)
    base_h = max(h[-10:])
    base_l = min(l[-10:])
    tight = (base_h / base_l - 1) * 100 if base_l > 0 else 0   # guard: a 0-low bad bar would divide-by-zero
    dist_hi = (base_h / close - 1) * 100 if close > 0 else 0
    max_day = max(((c[k] / c[k - 1] - 1) * 100) for k in range(-10, 0)) if len(c) > 11 else 0
    # recent catalyst/news move: biggest gap or 1-day jump in the last 5 sessions
    if len(c) > 6:
        gap5 = max((o[k] / c[k - 1] - 1) * 100 for k in range(-5, 0))
        move5 = max((c[k] / c[k - 1] - 1) * 100 for k in range(-5, 0))
        recent_gap = round(max(gap5, move5), 1)
        gap_up = round(gap5, 1)                  # the TRUE open gap (catalyst tell) — not an intraday run
    else:
        recent_gap = 0.0
        gap_up = 0.0
    news_move = recent_gap >= 8

    uptrend = close > s50 and above >= 2
    not_extended = ext10 < 1.6 * adr

    is_pullback = (uptrend and 3 <= pull_from_high <= 40
                   and near_dist <= 1.8 * adr and not_extended)
    is_avwap_ath = (uptrend and 0 <= dist_avwap_ath <= 1.5 * adr and pull_from_high >= 3)
    is_avwap_earn = (uptrend and avwap_earn is not None
                     and 0 <= dist_avwap_earn <= 1.5 * adr and pull_from_high >= 3)
    # Deep pullback: a STRONG leader (big run, still above the 200-MA) that has pulled back
    # BELOW its short EMAs toward the 50 EMA. Normal pullback logic drops these (uptrend test
    # needs price above the short MAs), so they used to fall through to a near-ATH breakout.
    # The stock's own strength is the edge here even if the sector is cooling.
    above_200_now = s200 is not None and close > s200
    strong_leader = above_200_now and (p6m >= 30 or p3m >= 30)
    # Deep pullback = a strong leader pulled back TO the 50 EMA (price below its short EMAs, AT the 50).
    # Anchor on PROXIMITY to the 50, not a fixed pull% cap — explosive leaders (AXTI ran +650% in 6mo)
    # pull back >50% off the high and still just be at the 50 EMA. (user 2026-06-05, the AXTI case.)
    # "Near the 50" must be genuinely CLOSE to the 50 — capped ABSOLUTELY (~9%) so a high-ADR name (AAOI,
    # ADR 15%) at +13% (up near the 9/21) isn't mislabeled a deep-at-the-50 pullback (user 2026-06-05).
    _dp_band = min(max(0.06, 0.8 * adr / 100), 0.09)
    near_50 = bool(e50) and (e50 * (1 - _dp_band) <= close <= e50 * (1 + _dp_band))
    is_deep_pullback = (not force_no_deep and strong_leader and close < e10 and close < e20
                        and pull_from_high >= 5 and near_50)
    # Consolidation: a strong stock going SIDEWAYS in a tight base while riding the 50 EMA
    # (the SNDK base — buy the dips to the 50 while it "waits"). A patient, worth-waiting setup.
    rng15 = (max(h[-15:]) / min(l[-15:]) - 1) * 100 if len(h) >= 15 else 100.0
    net15 = abs(c[-1] / c[-16] - 1) * 100 if len(c) >= 16 else 100.0   # net move = how SIDEWAYS it is
    # EMAs CONVERGED — a real tight base has the 9/21 BUNCHED near the 50; if they're FANNED far above
    # it, the stock dropped fast = a DEEP pullback, not a consolidation (the AXTI tell: 9/21 ~17% over
    # the 50). This is the single discriminator that separates the two patient setups.
    ema_fan = ((max(e9, e21) / e50 - 1) * 100) if e50 else 99.0
    is_consolidation = (above_200_now and (p3m >= 15 or p6m >= 25) and close > s50
                        and rng15 <= 4.0 * adr            # tight base (range)
                        and ema_fan <= max(8.0, 0.6 * adr)   # 9/21 bunched near the 50 (not a deep drop)
                        and net15 <= 0.4 * rng15          # genuinely sideways (not a directional pullback)
                        and -max(8.0, 0.5 * adr) <= ext10 <= 1.5 * adr)   # near the 10, not knifed below it

    if is_consolidation:                                  # tight sideways base takes priority
        setup_type = "Consolidation"
    elif is_pullback and (is_avwap_ath or is_avwap_earn):
        setup_type = "Pullback @ AVWAP"
    elif is_pullback:
        setup_type = "Pullback"
    elif is_avwap_ath:
        setup_type = "AVWAP reclaim (ATH)"
    elif is_avwap_earn:
        setup_type = "AVWAP reclaim (earnings)"
    elif is_deep_pullback:
        setup_type = "Deep Pullback"
    elif gap_up >= 8 and dist_hi <= 1.5 * adr:
        # Episodic Pivot = a TRUE open gap on a catalyst (not just any big intraday run into
        # the highs — that's a Breakout). The "+ fresh news" half is confirmed in the app layer
        # (grade_suggestions), which has the news feed; a gap with no material catalyst gets
        # relabeled Breakout there. Same buy-stop mechanics either way.
        setup_type = "Episodic Pivot"
    else:
        setup_type = "Breakout"

    deep = setup_type == "Deep Pullback"
    consol = setup_type == "Consolidation"
    extended = ext10 > 2.2 * adr
    pullback_like = setup_type in ("Pullback", "Pullback @ AVWAP",
                                   "AVWAP reclaim (ATH)", "AVWAP reclaim (earnings)")

    # ----- volume character: who is in control of the recent action? -----
    # Read volume on up-closes vs down-closes over the last ~10 sessions, plus whether the
    # most recent 3 days are expanding vs the 10-day norm. On a PULLBACK, heavy/rising volume
    # on the down days = distribution (the pullback may not be over). On an ADVANCE, heavy/
    # rising volume on the up days = accumulation (momentum still with us). Drying-up volume
    # into a pullback is the classic healthy contraction.
    vol_signal = vol_note = None
    vol_adj = 0.0
    nv = min(10, len(c) - 1)
    if nv >= 4:
        up_v = [v[k] for k in range(-nv, 0) if c[k] >= c[k - 1]]
        dn_v = [v[k] for k in range(-nv, 0) if c[k] < c[k - 1]]
        uv = st.mean(up_v) if up_v else 0.0
        dv = st.mean(dn_v) if dn_v else 0.0
        base_v = st.mean(v[-nv:]) or 1
        expanding = st.mean(v[-3:]) / base_v          # >1 = recent volume picking up
        if pullback_like or deep or consol:
            if dv > uv * 1.3 and expanding >= 1.1:
                vol_signal, vol_adj = "distribution", -2.0
                vol_note = "selling volume rising on the dip — pullback may not be over"
            elif dv and uv and dv < uv * 0.85:
                vol_signal, vol_adj = "dry", 0.0     # NO score edge — backtest: dry −0.09R vs none +0.52R
                vol_note = "pullback on drying volume — sellers exhausting"   # kept as context, not a grade input
        else:                                          # breakout / EP — an advance
            if uv > dv * 1.3 and expanding >= 1.1:
                vol_signal, vol_adj = "accumulation", 2.0
                vol_note = "rising volume on up days — buyers in control"
            elif uv and dv and uv < dv * 0.85:
                vol_signal, vol_adj = "weak", -1.5
                vol_note = "advance on light volume — momentum thin"

    # climax / reversal bar: TODAY is a heavy-volume rejection off a recent high — the crowd
    # distributing into strength after a run. This is the ASTS tell: a setup that just printed a
    # big red bar on the highest volume around isn't a "buy now," no matter how strong the name.
    climax_rev = False
    if len(c) >= 21:
        rng = h[-1] - l[-1]
        close_pos = (c[-1] - l[-1]) / rng if rng > 0 else 0.5
        avg20 = st.mean(v[-20:]) or 1
        rejected = (max(h[-10:]) / c[-1] - 1) * 100 >= 0.5 * adr_safe   # pulled back off a recent high
        climax_rev = (c[-1] < o[-1] and close_pos < 0.45
                      and v[-1] >= 1.3 * avg20 and rejected)
    distribution_today = climax_rev or vol_signal == "distribution"
    if climax_rev:
        vol_signal = "distribution"
        vol_adj = min(vol_adj, -2.5)
        vol_note = "heavy-volume reversal off the highs — let it settle, don't buy the drop"

    # ----- score -----
    score = above * 1.5 + PREF.get(setup_type, 0)
    if adr >= 4:
        score += 2
    if adr >= 7:
        score += 1
    if deep:
        score += 4                                         # a strong-leader deep pullback is quality
        score += max(0, 2 - abs(close / e50 - 1) * 100 / adr_safe)   # close to the 50 EMA
        score += min(2, max(p3m, p6m) / 50)                # don't forget the run / performance
    elif consol:
        score += 3                                         # tight base riding the 50 = quality wait
        score += max(0, 2 - rng15 / adr_safe)              # the tighter the base, the better
        score += min(2, max(p3m, p6m) / 50)
    elif pullback_like:
        score += max(0, 3 - near_dist / adr_safe)          # tight to the line/VWAP
    else:
        score += max(0, 4 - dist_hi / adr_safe)            # near the breakout trigger
        score += max(0, 4 - tight / adr_safe)              # tight base
    if extended:
        score -= 4
    if ext10 < -1 and not deep and not consol:             # below the 10-EMA IS the deep pullback / base
        score -= 2
    # (removed the flat +1 "dry-up" bonus for volc<0.85 — the 228-trade backtest found volume dry-up
    #  carried no edge, even slightly negative: dry −0.09R vs none +0.52R. Re-validate via forward test.)
    score += vol_adj                                       # volume character (accumulation/distribution — penalties kept)

    # ----- trade levels -----
    swing_low = min(l[-7:])
    if deep:
        # Catch the strong leader at the 50 EMA — but never DEEPER than the recent pullback low.
        # On parabolic names the 50 EMA lags far below price; the stock is really finding support
        # at the swing low, so we floor the entry there. Buy zone can sit below current price
        # (wait for the dip) or above (a reclaim). Stop tight, just under the swing low.
        swing = min(l[-10:])
        # Anchor the buy zone to REAL support (user 2026-06-05, TSEM): floor the entry at the recent swing
        # ONLY if the market has RESPECTED that level (≥2 prior touches); otherwise the swing is a freefall
        # low in mid-air and the real support is the 50 EMA (the leader's deep-pullback line) — use that.
        _tol = 0.008 * close          # respected-level band ~0.8% (tight S/R, not a wide-ADR smear)
        if swing >= e50 and _respected_level(h[:-1], l[:-1], swing, _tol):
            entry = round(swing, 2)
        else:
            entry = round(e50, 2)
        if close < entry:
            entry_type = "stop"
            entry_note = "buy on a reclaim toward the 50 EMA (strong leader — worth waiting)"
        else:
            entry_type = "limit"
            entry_note = "buy the dip into the 50 EMA / recent support (worth waiting)"
        # Stop sits JUST UNDER the support being held (user 2026-06-05, AXTI): a deep pullback is bought
        # because support held, so the stop belongs just below it — capped ~3% so a high-ADR leader (AXTI
        # ADR 15%+) doesn't get a 6% stop. Floored ~1.2% so a low-ADR name isn't wicked out. (Exit proper
        # is a daily CLOSE under the 50 — this is the risk basis / invalidation.)
        adr_px = entry * adr / 100
        buf = min(max(0.5 * adr_px, 0.012 * entry), 0.03 * entry)
        stop = round(entry - buf, 2)
        inval = round(entry, 2)
        stop_basis = ("just under the 50 EMA (close below)" if entry <= e50 * 1.005
                      else f"just under the pullback swing ${round(entry, 2)} (close below)")
    elif consol:
        # Buy the dip to the 50 EMA inside the base (floored at the base low). Stop below the base.
        base_low = min(l[-15:])
        # Anchor to REAL support (TSEM rule): floor at the base low only if the market has RESPECTED it
        # (≥2 touches = a true base edge); otherwise use the 50 EMA, not a mid-air recent low.
        _tol = 0.008 * close          # respected-level band ~0.8% (tight S/R, not a wide-ADR smear)
        if base_low >= e50 and _respected_level(h[:-1], l[:-1], base_low, _tol):
            entry = round(base_low, 2)
        else:
            entry = round(e50, 2)
        if close < entry:
            entry_type = "stop"
            entry_note = "buy the reclaim of the 50 EMA in the base (worth waiting)"
        else:
            entry_type = "limit"
            entry_note = "buy the dip to the 50 EMA inside the consolidation (worth waiting)"
        # Stop just under the support held (same as deep pullback — capped ~3% for high-ADR names).
        adr_px = entry * adr / 100
        buf = min(max(0.5 * adr_px, 0.012 * entry), 0.03 * entry)
        stop = round(entry - buf, 2)
        inval = round(entry, 2)
        stop_basis = ("just under the 50 EMA (close below)" if entry <= e50 * 1.005
                      else f"just under the consolidation low ${round(entry, 2)} (close below)")
    elif pullback_like:
        # Entry = the rising support the stock is pulling back INTO. If price is still
        # above a support line, the entry sits BELOW current price (a limit buy on a
        # further dip). Only if price already reached/undercut support do we use a
        # reclaim (buy-stop above the prior-day high).
        cands = [("10-EMA", e10), ("20-EMA", e20), ("50-MA", s50)]
        if "AVWAP" in setup_type:
            cands.append(("AVWAP (ATH)", avwap_ath))
        if avwap_earn:
            cands.append(("AVWAP (earnings)", avwap_earn))
        below = [(lbl, v) for lbl, v in cands if v and v < close]
        if below:
            lbl, sup = max(below, key=lambda x: x[1])      # nearest support beneath price
            entry = round(sup, 2)
            entry_type = "limit"
            entry_note = f"buy the pullback into the {lbl} (limit, below current ${round(close,2)})"
        else:
            entry = round(max(h[-2:]), 2)                  # already at/under support -> reclaim
            entry_type = "stop"
            entry_note = "buy on reclaim of the prior-day high"
        # Stop anchors to STRUCTURE. Two candidates, pick the TIGHTER valid one
        # (better R-location): (1) just under the recent higher-low the pullback is
        # holding, or (2) just below a reclaimed prior swing high (resistance-turned-
        # support — the NVDA ~$194 / ASTS $104.98 line), reported "close below".
        # Clamp width 0.35-1.2x ADR (never risk > 1.2 ADR, never tighter than noise).
        recent_low = min(l[-5:])
        adr_px = entry * adr / 100
        # Clamp width 0.45–1.2x ADR, but cap ABSOLUTELY (user 2026-06-05) so a high-ADR name (AXTI/TSEM,
        # ADR 15%+) doesn't get a 7–18% "structure" stop — keep it just under support like the rest.
        wide_floor = entry - min(1.2 * adr_px, 0.05 * entry)
        tight_cap = entry - min(0.45 * adr_px, 0.025 * entry)   # ≥0.45 ADR but never wider than ~2.5%
        struct = recent_low - 0.15 * adr_px
        reclaimed = [p for _, p in _swing_highs(h) if p < entry - 0.02 * adr_px]
        sh_stop = (max(reclaimed) - 0.10 * adr_px) if reclaimed else None
        cands = []
        if struct >= wide_floor:
            cands.append((struct, "5-day structure low"))
        if sh_stop is not None and sh_stop >= wide_floor:
            cands.append((sh_stop, f"reclaimed swing high ${round(max(reclaimed), 2)} (close below)"))
        if cands:
            raw, stop_basis = max(cands, key=lambda x: x[0])   # tighter (higher) valid stop
        else:
            raw, stop_basis = max(wide_floor, struct), "1.2x ADR limit"
        stop = round(min(raw, tight_cap), 2)
        inval = round(recent_low, 2)
    else:
        # buy-stop above the RECENT, de-wicked consolidation high — not a stale spike/distribution wick
        # from the prior leg (user 2026-06-09, the INTC case: old max(high[-10:]) = 126.64 from a May-29
        # fade; the real June base tops ~113).
        bo_piv = _breakout_pivot(o, h, l, c, close * adr / 100)
        if bo_piv <= close:                                # already at the top of the de-wicked base →
            bo_piv = max(h[-1], close)                     # the real upside trigger is today's high
        entry = round(bo_piv, 2)
        # PART A: anchor the stop under the pivot, clamped <=1x ADR — not the day low.
        stop, stop_basis = _breakout_stop(entry, l[-1], adr, pivot=bo_piv)
        inval = round(base_l, 2)
        entry_type = "stop"
        entry_note = "buy-stop above the consolidation high"
    risk_ps = round(entry - stop, 2)
    target = round(entry + 2 * risk_ps, 2)
    # entry-location quality (0-100): penalize buying stretched far above the 10-EMA, far above the
    # 50-EMA/BASE, and a stop forced near the full 1x-ADR limit. A valid setup bought right on the
    # rising line with a tight stop scores ~100; the same setup chased 2x ADR up scores low.
    stretch_x = max(0.0, ext10 / adr_safe)
    stretch_pen = min(50.0, stretch_x * 25.0)
    # distance above the 50-EMA (the base) in ADR units — on a parabolic name the 10-EMA is itself far
    # above the 50, so a chase sitting ON its 10-EMA used to score high; this term closes that blind spot.
    ext50_eq = ((close / e50 - 1) * 100 / adr_safe) if e50 else 0.0
    stretch50_pen = min(40.0, max(0.0, ext50_eq - 1.0) * 12.0)
    one_adr_px = entry * adr / 100 or 1
    width_pen = min(40.0, max(0.0, risk_ps / one_adr_px - 0.4) * 60.0)
    entry_quality = round(max(0.0, 100.0 - stretch_pen - stretch50_pen - width_pen))
    # buy zone = a band around the entry (you don't need an exact tick). Being inside it = buyable now.
    adr_px = entry * adr / 100
    if entry_type == "limit":
        # a TIGHT band that hugs support — cap the ADR-scaled width (user 2026-06-05): on a high-ADR name
        # (AXTI/TSEM, ADR 15%+) a 0.6×ADR band is ~9%, smearing the "buy zone" far above the 50 EMA. Cap
        # it at ~2.5% up / 2% down so the zone actually sits AROUND the support level.
        zone_top = round(entry + min(0.6 * adr_px, 0.025 * entry), 2)
        zone_bottom = round(entry - min(0.25 * adr_px, 0.02 * entry), 2)
    else:
        zone_top = round(entry + min(0.5 * adr_px, 0.02 * entry), 2)
        zone_bottom = round(entry, 2)
    buf = 0.3 * adr_px                                    # a little tolerance — just-above the zone still counts
    buyable_now = (zone_bottom - buf) <= close <= (zone_top + buf)
    # don't call it "buyable now" if today is a distribution/reversal bar or the stock is
    # stretched — wait for it to come back to the line instead of buying into the move.
    if distribution_today or extended:
        buyable_now = False
    # RESPECTED-SUPPORT BOUNCE (user 2026-06-05, the TSEM case): a dip-to-support buy is "buyable now"
    # ONLY once price has pulled back TO the support, RESPECTED it, and JUMPED off it — never while it's
    # still knifing down through the zone in mid-air. Being inside the band = ARMED (watch), not a buy.
    # Backtest-validated (tools/research_bounce.py): vs the passive limit this ~halves trades, lifts the
    # win rate, and ~doubles avg winsorized R — it discards exactly the falling-knife fills.
    # FORMING-BAR FIX (user 2026-06-08, the AXTI-at-the-open case): mid-session bars[-1] is today's
    # FORMING bar — its close = the live price. _respected_bounce is an EOD daily proxy; fed a forming
    # bar, a green intraday pop would "un-break" a 50 EMA that yesterday's SETTLED close decisively
    # broke, flipping "buyable now" on with NO confirmation. So when forming_last, evaluate the gate on
    # the SETTLED series only (drop the forming bar): buyable reflects settled structure, and the live
    # confirmation engine (50-reclaim + spin + buyers, on completed candles) owns the real intraday buy.
    _ro, _rh, _rl, _rc = ((o[:-1], h[:-1], l[:-1], c[:-1]) if (forming_last and len(c) >= 4)
                          else (o, h, l, c))
    if entry_type == "limit" and not _respected_bounce(_ro, _rh, _rl, _rc, entry, adr_px):
        buyable_now = False
    # "worth waiting" = patient entries you watch and buy at support: a STRONG leader correcting
    # DEEP into the 50 EMA (VRT/AXTI/LITE), or a strong stock CONSOLIDATING sideways on the 50
    # (the SNDK base). NOT shallow pullbacks near the highs that are still moving up.
    worth_waiting = deep or consol
    # CHASE GUARD (the NBIS case): a momentum/breakout name parabolic-extended far above the 50 EMA is
    # NOT "buyable now" even if the close happens to land in the zone — buying here is chasing a
    # vertical move. Measured as distance above the 50 EMA in ADR units. Patient dip-buy setups
    # (deep pullback / consolidation) are exempt — they buy INTO the 50/support, not extended above it.
    ext50_adr = ((close / e50 - 1) * 100 / adr_safe) if e50 else 0.0
    parabolic = ext50_adr >= rubric.CHASE_HARD_ADR   # ≥4x ADR above the 50 = a chase (my-rules); also hard-caps the grade at C
    if parabolic and not worth_waiting:
        buyable_now = False

    # ----- entry options (1-2 plans) -----
    # entries[0] mirrors the primary computed above (so grading / sizing / forward-test / coach
    # are unchanged); entries[1] is the alternative, shown only when it's a real, distinct option:
    #  - a BREAKOUT/EP also offers the PULLBACK (buy the dip to the nearest support below price)
    #  - a patient dip-buy (Pullback / Deep Pullback / Consolidation / AVWAP) also offers the
    #    BREAKOUT of the prior-day high (buy the strength instead of waiting on support)
    breakout_setup = setup_type in ("Breakout", "Episodic Pivot")
    primary = {
        "kind": "breakout" if breakout_setup else "pullback",
        "entry_type": entry_type, "entry": entry, "stop": stop, "stop_basis": stop_basis,
        "inval": inval, "risk_ps": risk_ps, "target": target,
        "zone_bottom": zone_bottom, "zone_top": zone_top, "buyable_now": buyable_now,
        "entry_note": entry_note,
        "trigger_note": (f"break above ${entry} to trigger" if entry_type == "stop"
                         else f"wait for the pullback to ${entry}"),
    }
    sh_prices = [p for _, p in _swing_highs(h)]
    if breakout_setup:                                   # alt = wait for the pullback to support
        supports = [("breakout level", p) for p in sh_prices]
        supports += [("10-EMA", e10), ("20-EMA", e20), ("50-MA", s50), ("AVWAP (ATH)", avwap_ath)]
        if avwap_earn:
            supports.append(("AVWAP (earnings)", avwap_earn))
        alt = _pullback_plan(close, adr, supports, min(l[-5:]), sh_prices)
    else:                                                # pullbacks AND patient setups: the secondary is the
        # BREAKOUT '2nd entry' (the trader's 'buy the right side'). PART B: for a PATIENT setup whose
        # pullback already happened (Deep Pullback / Pullback / Pullback @ AVWAP / AVWAP reclaims /
        # Consolidation), anchor the breakout at the NEARER PIVOT — the swing high the stock pulled back
        # FROM / is basing under — NOT the far prior-day high. The stop then sits just under THAT pivot
        # (Part A). Falls back to the prior-day high when no defensible nearer pivot exists.
        patient = setup_type in ("Deep Pullback", "Pullback", "Pullback @ AVWAP",
                                 "AVWAP reclaim (ATH)", "AVWAP reclaim (earnings)", "Consolidation")
        pdh = max(h[-2:])
        piv = _pullback_leg_pivot(h, l, close, adr) if patient else None
        # Only use the nearer pivot when it is genuinely NEARER than the prior-day high — never make the
        # 2nd-entry trigger FARTHER. (AAOI case: price has already reclaimed its last swing high, so the
        # only overhead pivot is higher than the prior-day high → fall back to the prior-day high.)
        if piv and piv <= pdh + 0.1 * (close * adr / 100 or 0.01):
            alt = _breakout_plan(piv, close, adr, day_low=l[-1],
                                 note="buy-stop above the pullback-leg pivot (2nd entry)")
        else:
            alt = _breakout_plan(max(h[-2:]), close, adr, day_low=l[-1],
                                 note="buy-stop above the prior-day high")
    entries = [primary]
    if alt and abs(alt["entry"] - primary["entry"]) >= 0.4 * (entry * adr / 100 or 0.01):
        if distribution_today or extended or (parabolic and not worth_waiting):
            alt["buyable_now"] = False                   # never flag an alt buyable into a hot/extended move
        entries.append(alt)
    # per-ENTRY grade factors so each option is graded on ITS OWN merit (the app grades per entry):
    # entry location vs the 10/50-EMA + stop width AT that entry, plus whether it's exempt from the
    # chase cap. A buy-the-dip pullback and a chase-the-breakout buy-stop on the same name grade apart.
    adr_px_now = close * adr / 100 or 0.01
    for e in entries:
        ep, sp = e["entry"], e["stop"]
        # STALE pullback: a "wait for the dip" limit that price has RUN far above (> STALE_PULLBACK_ADR
        # above its zone top). The dip won't realistically come (the parabolic INOD/DOCN case: a +150%
        # name 4.8x ADR over the 50, its pullback entry ~30% below). It's no longer a buy-into-support —
        # grade it as the chase it now is (at today's price, no exemption) and flag it so the unreachable
        # A+ can't carry the headline grade. If price later does correct to the 50, it re-classifies fresh.
        zt = e.get("zone_top") or ep
        stale = bool(e["entry_type"] == "limit" and not e.get("buyable_now")
                     and (close - zt) / adr_px_now > rubric.STALE_PULLBACK_ADR)
        e["stale"] = stale
        # grade at the price you'd actually PAY: a buy-stop fills at its (higher) trigger; a pullback
        # that's BUYABLE NOW (or has run away → stale) fills at the current price, not the lower limit —
        # so a "buy the dip" that's already extended (APLD at 48 vs its 46 limit) still grades as extended.
        gp = max(ep, close) if (e["entry_type"] == "limit" and (e.get("buyable_now") or stale)) else ep
        ex10 = (gp / e10 - 1) * 100 / adr_safe if e10 else 0.0
        ex50 = (gp / e50 - 1) * 100 / adr_safe if e50 else 0.0
        spen = min(50.0, max(0.0, ex10) * 25.0)
        s50pen = min(40.0, max(0.0, ex50 - 1.0) * 12.0)
        adr_px_e = ep * adr / 100 or 1
        wpen = min(40.0, max(0.0, (ep - sp) / adr_px_e - 0.4) * 60.0)
        e["ext50_adr"] = round(ex50, 1)
        e["entry_quality"] = round(max(0.0, 100.0 - spen - s50pen - wpen))
        # the pullback option of a patient setup (deep pullback / consolidation) buys INTO support with a
        # tight stop -> exempt from the chase cap; breakout options, other pullbacks, and run-away (stale)
        # pullbacks never are.
        e["chase_exempt"] = bool(worth_waiting and e["kind"] == "pullback" and not stale)

    # ----- sizing -----
    acct = settings.get("account_size")
    risk_pct = settings.get("risk_pct", 1.0)
    shares = dollar_risk = None
    if acct and risk_ps > 0:
        shares = int((acct * risk_pct / 100) // risk_ps)
        dollar_risk = round(shares * risk_ps, 2)
    shares_per_10k = int((10000 * risk_pct / 100) // risk_ps) if risk_ps > 0 else 0

    # ----- why -----
    why = ["Above 10/20/50 MA" if above == 3 else f"Above {above}/3 MAs", f"+{p1m:.0f}% 1m"]
    if setup_type == "Deep Pullback":
        why.append(f"strong leader (+{max(p3m, p6m):.0f}%/{'6m' if p6m >= p3m else '3m'}) "
                   f"pulled back {pull_from_high:.0f}% to the 50 EMA")
    elif setup_type == "Consolidation":
        why.append(f"tight base ({rng15 / adr_safe:.1f}x ADR) riding the 50 EMA, "
                   f"strong (+{max(p3m, p6m):.0f}%)")
    elif setup_type == "Pullback":
        why.append(f"pulled back {pull_from_high:.0f}% to {near_line}")
    elif setup_type == "Pullback @ AVWAP":
        why.append(f"pullback to {near_line} + holding AVWAP")
    elif setup_type == "AVWAP reclaim (ATH)":
        why.append(f"holding AVWAP from ATH ({dist_avwap_ath:+.1f}%)")
    elif setup_type == "AVWAP reclaim (earnings)":
        why.append(f"holding AVWAP from earnings gap (+{earn_gap:.0f}%)")
    elif setup_type == "Breakout":
        # distance to the ACTUAL (de-wicked, recent-base) trigger, not the stale base-high dist_hi
        _trig = (entry / close - 1) * 100 if close > 0 else 0
        why.append(f"base {tight / adr_safe:.1f}x ADR, {_trig:.1f}% to trigger")
    else:
        _trig = (entry / close - 1) * 100 if close > 0 else 0
        why.append(f"gap {max_day:.0f}%, {_trig:.1f}% to trigger")
    if volc and volc < 0.85:
        why.append(f"vol drying {volc:.2f}")
    if vol_note:
        why.append(("🟢 " if vol_adj > 0 else "🔴 ") + vol_note)
    if extended:
        why.append("EXTENDED - don't chase")

    return {
        "ticker": sym.upper(), "setup_type": setup_type, "close": round(close, 2),
        "adr": round(adr, 1), "above": above, "ext10": round(ext10, 1),
        "pull_from_high": round(pull_from_high, 1), "near_line": near_line,
        "avwap_ath": round(avwap_ath, 2), "avwap_earn": round(avwap_earn, 2) if avwap_earn else None,
        "dist_avwap_ath": round(dist_avwap_ath, 1),
        "tight_x": round(tight / adr_safe, 1), "dist_hi": round(dist_hi, 1),
        "p1m": round(p1m, 1), "p3m": round(p3m, 1), "p6m": round(p6m, 1), "volc": round(volc, 2),
        "dollar_vol": dollar_vol, "liq_score": liq_score,
        "avg_vol": round(st.mean(v[-20:])) if len(v) >= 20 else (round(st.mean(v)) if v else 0),
        "pull_from_52w": pull_from_52w, "above_50": above_50, "above_200": above_200,
        "ma_stack": ma_stack, "screen_1m": screen_1m, "screen_3m": screen_3m, "screen_6m": screen_6m,
        "tt_pass_price": tt_pass, "tt_count_price": tt_count_price, "tt_flags": tt_flags,
        "vcp": vcp["vcp"], "vcp_contractions": vcp["contractions"],
        "vcp_depth_last": vcp.get("depth_last"), "vcp_pivot": vcp.get("pivot"),
        "score": round(score, 1), "entry": entry, "stop": stop, "inval": inval,
        "risk_ps": risk_ps, "target": target, "shares": shares,
        "shares_per_10k": shares_per_10k, "dollar_risk": dollar_risk,
        "extended": extended, "why": " - ".join(why),
        "entry_type": entry_type, "entry_note": entry_note, "stop_basis": stop_basis,
        "entry_quality": entry_quality, "worth_waiting": worth_waiting,
        "zone_top": zone_top, "zone_bottom": zone_bottom, "buyable_now": buyable_now,
        "parabolic": parabolic, "ext50_adr": round(ext50_adr, 1),
        "entries": entries, "gap_up": gap_up,
        # inputs for the date-correct "prior-day high" (the level a live rotation entry reclaims).
        # During a live session the cached daily series' last bar is TODAY, so the prior day is
        # prev_high; the app (grade_suggestions) decides via _session_date() and sets prior_high.
        "last_bar_date": bars[-1].get("time"), "last_high": round(h[-1], 2),
        "prev_high": round(h[-2], 2) if len(h) >= 2 else round(h[-1], 2),
        # current EMAs (the 50 is the deep-pullback line — a reclaim of it is itself a confirmation)
        "ema10": round(e10, 2), "ema20": round(e20, 2), "ema50": round(e50, 2),
        "ema9": round(e9, 2), "ema21": round(e21, 2),       # 9/21 to match the trader's chart (entry logic)
        "recent_gap": recent_gap, "news_move": news_move,
        "vol_signal": vol_signal, "vol_note": vol_note,
        "distribution_today": distribution_today,
    }


def analyze_at(sym, date, settings=None, market="us"):
    """Run analyze() on daily bars SLICED to a past date (a trade's entry date) so a setup can be
    graded AS OF when it was taken. Also attaches an RS-outperformance proxy vs the equal-blend
    benchmark indexes sliced to the same date. Price-based only — market regime / sector heat / news
    can't be reconstructed for a past date. Returns the analyze dict (+ rs_score, asof) or None."""
    bars = get_bars(sym)
    if not bars:
        return None
    sl = [b for b in bars if b["time"] <= date]            # bars up to and incl. the entry date
    if len(sl) < 60:
        return None
    try:
        a = analyze(sym, sl, settings)
    except Exception:
        return None
    b1, b3 = [], []
    for _, isym in mcfg(market)["indexes"]:
        ib = get_bars(isym)
        isl = [b for b in ib if b["time"] <= date] if ib else None
        if not isl or len(isl) < 63:
            continue
        ic = [b["close"] for b in isl]
        b1.append((ic[-1] / ic[-21] - 1) * 100)
        b3.append((ic[-1] / ic[-63] - 1) * 100)
    bb1 = st.mean(b1) if b1 else 0.0
    bb3 = st.mean(b3) if b3 else 0.0
    outperf = 0.5 * (a.get("p1m", 0) - bb1) + 0.5 * (a.get("p3m", 0) - bb3)
    a["rs_score"] = round(max(0.0, min(100.0, 50.0 + outperf * 2.0)))
    a["asof"] = sl[-1]["time"]
    return a


def _fetch_intraday(sym, rng="2d"):
    """5-minute bars incl. pre/post-market for the last ~2 sessions. Returns (bars, gmtoffset)."""
    sym = sym.strip().upper()
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?interval=5m&range={rng}&includePrePost=true")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            res = d["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            gmt = res["meta"].get("gmtoffset", -14400)
            bars = []
            for i in range(len(ts)):
                o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
                if c is None:
                    continue
                bars.append({"t": ts[i], "o": o or c, "c": c, "v": v or 0})
            if len(bars) > 20:
                return bars, gmt
        except Exception:
            time.sleep(0.3)
    return None, None


_INTRADAY5 = {}     # sym -> {"t": cached_epoch, "bars": [today's regular-session 5-min OHLCV]}


def get_5m_today(sym, max_age=120):
    """Today's REGULAR-session (09:30–16:00 ET) 5-minute OHLCV bars, cached ~2 min in memory. Powers the
    live confirmation engine's opening-range-high detection. Light: called only for the armed shortlist.
    Returns [{et,'HH:MM', hour, open, high, low, close, volume}] (oldest→newest) or None."""
    sym = sym.strip().upper()
    now = time.time()
    c = _INTRADAY5.get(sym)
    if c and now - c["t"] < max_age:
        return c["bars"]
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=1d")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.load(r)
            res = d["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            gmt = res["meta"].get("gmtoffset", -14400)
            vol = q.get("volume") or [0] * len(ts)
            bars = []
            for i in range(len(ts)):
                o, h, l, cl = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (h, l, cl):
                    continue
                secs = (ts[i] + gmt) % 86400
                hour = secs / 3600
                if not (9.5 <= hour < 16.0):                 # regular cash session only
                    continue
                bars.append({"et": f"{int(secs // 3600):02d}:{int((secs % 3600) // 60):02d}",
                             "hour": round(hour, 3), "open": round(o or cl, 2), "high": round(h, 2),
                             "low": round(l, 2), "close": round(cl, 2), "volume": int(vol[i] or 0)})
            if bars:
                _INTRADAY5[sym] = {"t": now, "bars": bars}
                return bars
        except Exception:
            time.sleep(0.2)
    return c["bars"] if c else None


def orh_confirm(bars5, buf_pct=0.0, adr_pct=0.0):
    """The verified Qulla/Luk intraday trigger: given today's regular-session 5-min bars, the setup is
    CONFIRMED when price TAKES OUT the opening-range high (the high of the first 5-min candle). Stop = the
    low of day (running min). `buf_pct` requires price to push a small % ABOVE the ORH (not merely tag it),
    so a wick that pokes the level and reverses ('hit in the nose') doesn't trigger a false confirm.
    Returns {orh, level, lod, confirmed, extended, trig_et, bars} or None (too few bars yet)."""
    if not bars5:
        return None
    orh = bars5[0]["high"]                                    # opening-range high (first 5-min candle)
    lod = min(b["low"] for b in bars5)                        # low of day = the structural stop
    level = round(orh * (1 + (buf_pct or 0) / 100), 2)        # must clear the ORH by the buffer to count
    # A 5-min candle must CLOSE above the level — not just wick through it. A candle whose HIGH pierced the
    # resistance but CLOSED back below it got 'hit in the nose' (rejected) and does NOT count (user's rule).
    # Exclude the LAST bar — Yahoo's last 5-min bar is the still-FORMING candle (its close = the live price),
    # so we only count COMPLETED candles that closed above. The forming candle is used for 'holding' below.
    closed = bars5[1:-1] if len(bars5) > 2 else []
    broke = next((b["et"] for b in closed if b["close"] >= level), None)
    last = bars5[-1]["close"]
    # DON'T CHASE: if price has already run more than ~0.7× ADR ABOVE the ORH, the entry is stale — buying
    # here means a day-low stop far wider than 1× ADR (the VIAV case: ORH $49.86, price $51.4, already +3%).
    band = orh * (1 + 0.7 * (adr_pct or 0) / 100) if adr_pct else None
    extended = bool(band and last > band)
    holding = (last >= orh) and not extended                 # current price still above the ORH, not run away
    return {"orh": orh, "level": level, "lod": lod, "extended": extended,
            "confirmed": (broke is not None) and holding,
            "broke": broke is not None, "holding": holding, "trig_et": broke, "bars": len(bars5)}


def breakout_confirm(bars5, level, adr_pct=0.0):
    """Confirmed when intraday price BREAKS ABOVE `level` (clearing overhead resistance — e.g. the EMA
    cluster) and is HOLDING: a 5-min high took out the level and the latest close is back above it but hasn't
    run away past ~0.7× ADR (don't chase). A poke that fades back below = not confirmed (re-arms). Returns
    {level, lod, broke, holding, extended, confirmed, last, bars} or None."""
    if not bars5 or not level:
        return None
    lod = min(b["low"] for b in bars5)
    # a COMPLETED 5-min candle must CLOSE above the resistance (not just wick through). Exclude the last bar
    # (the still-forming candle) so it can't confirm mid-candle before the real close.
    closed = bars5[:-1] if len(bars5) > 1 else []
    broke = any(b["close"] >= level for b in closed)
    last = bars5[-1]["close"]                                 # forming candle = current price (for 'holding')
    band = level * (1 + 0.7 * (adr_pct or 0) / 100)           # past this it already ran — don't chase
    holding = level <= last <= max(band, level * 1.002)
    return {"level": round(level, 2), "lod": lod, "broke": broke, "holding": holding,
            "extended": last > band, "confirmed": broke and holding, "last": round(last, 2), "bars": len(bars5)}


def buyers_confirm(bars5, adr_pct=0.0):
    """Buyers-stepping-in gate for the live confirmation (the user's AAOI rule, 2026-06-05): a single
    5-min candle poking back above a level in a name that's been SOLD HARD all day is a falling knife,
    NOT a reclaim — wait to SEE buyers take control, exactly like the Spinning screener does. Given
    today's regular-session 5-min bars, require the recent tape to show real buying: the 5-min 9 EMA
    turning up (or higher-lows) AND at least 2 recent GREEN closes back above that 9 EMA, with price
    holding the line now. Returns (ok: bool, why: str). Lenient EARLY (too few bars) so it never blocks
    a clean opening-range break — the level-break check still governs there."""
    if not bars5 or len(bars5) < 6:
        return True, ""                                  # too early to read the tape — don't block
    c = [b["close"] for b in bars5]
    o = [b["open"] for b in bars5]
    n = len(c)
    k = 2 / 10                                           # 9-period EMA on the 5-min closes (the chart's 9 EMA)
    ma = [c[0]]
    for x in c[1:]:
        ma.append(x * k + ma[-1] * (1 - k))
    green = [c[i] > o[i] for i in range(n)]
    above = [c[i] >= ma[i] for i in range(n)]
    # 2+ recent GREEN candles that CLOSED above the 5-min 9 EMA = the reclaim is real (the spin rule).
    # Count COMPLETED candles only (exclude the still-forming last bar).
    green_above = sum(1 for i in range(max(0, n - 9), n - 1) if green[i] and above[i])
    ma_up = ma[-1] >= ma[-4] if n >= 4 else True         # the 5-min 9 EMA is curling up (buyers winning)
    recent = c[-7:]
    higher_lows = len(recent) >= 6 and min(recent[-3:]) >= min(recent[:-3])   # not making fresh lows
    holding = above[-1] or above[-2]                     # price is holding the 5-min 9 EMA right now
    turning = ma_up or higher_lows
    if green_above >= 2 and turning and holding:
        return True, ""
    if green_above < 2:
        return False, "no buyers yet — only one candle back above the line (need 2 green closes above the 5-min 9 EMA)"
    if not turning:
        return False, "still being sold — the 5-min 9 EMA is falling / making lower lows; wait for buyers to turn it up"
    return False, "not holding the reclaim — wait for it to hold above the 5-min 9 EMA"


def ep_volume_ok(bars5, avg_daily_vol):
    """EP volume gate (verified: volume is 'the #1 thing' for an episodic pivot). Confirmed when today's
    cumulative volume is already running at a big fraction of the stock's avg DAILY volume — i.e. massive
    open participation. Qualitative by design (the research REFUTED any hardcoded multiple). Returns
    (ok: bool, ratio: float|None). If we lack avg volume, don't block (ok=True, ratio=None)."""
    if not bars5 or not avg_daily_vol:
        return True, None
    cum = sum(b.get("volume", 0) for b in bars5)
    ratio = cum / avg_daily_vol if avg_daily_vol else None
    return (ratio is not None and ratio >= 0.7), ratio       # ≥0.7× ADV already traded = massive for an EP


def eod_signal(sym):
    """Detect unusual end-of-day / after-hours activity: a 5-min volume spike well above the
    session's norm paired with a directional move — a sign of aggressive buying or selling
    (often insider/institutional) into the close or after hours."""
    bars, gmt = _fetch_intraday(sym)
    if not bars:
        return None
    last_t = bars[-1]["t"]
    day = [b for b in bars if last_t - b["t"] <= 16 * 3600]      # one session incl pre/post
    if len(day) < 20:
        return None

    def ethr(t):
        return ((t + gmt) % 86400) / 3600                       # hour-of-day in exchange tz

    reg = [b for b in day if 9.5 <= ethr(b["t"]) < 16.0]
    post = [b for b in day if ethr(b["t"]) >= 16.0]             # after-hours (post-close)
    vols = [b["v"] for b in reg if b["v"] > 0]
    if len(vols) < 8:
        return None
    avg5 = st.mean(vols)
    if avg5 <= 0:
        return None
    eod = (reg[-12:] if reg else day[-12:]) + post              # closing hour + after-hours
    if not eod:
        return None
    spike = max(eod, key=lambda b: b["v"])
    spike_ratio = spike["v"] / avg5
    w_open = eod[0]["o"]
    w_close = eod[-1]["c"]
    move = (w_close / w_open - 1) * 100 if w_open else 0.0
    if spike_ratio < 4.0 or abs(move) < 1.0:                    # not unusual enough
        return None
    return {
        "ticker": sym.upper(), "signal": "buying" if move > 0 else "selling",
        "move": round(move, 1), "vol_mult": round(spike_ratio, 1),
        "after_hours": ethr(spike["t"]) >= 16.0, "price": round(w_close, 2),
        "strength": round(spike_ratio * abs(move), 1),
    }


def scan_suspicious(tickers, progress=None):
    """Scan tickers for end-of-day buy/sell anomalies; return ranked buying & selling lists."""
    buys, sells = [], []
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        if progress:
            progress(i, total, t)
        try:
            sig = eod_signal(t)
        except Exception:
            sig = None
        if sig:
            (buys if sig["signal"] == "buying" else sells).append(sig)
        time.sleep(0.03)
    buys.sort(key=lambda x: -x["strength"])
    sells.sort(key=lambda x: -x["strength"])
    return {"buying": buys[:50], "selling": sells[:50], "scanned": total,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def _premarket_one(sym):
    """Most recent pre-market (4:00–9:30 ET) move vs the prior regular-session close.
    Returns the gap %, last pre-market price, and pre-market volume — or None if there's
    no pre-market activity (e.g. run outside pre-market hours) or the move is tiny."""
    bars, gmt = _fetch_intraday(sym, rng="2d")
    if not bars:
        return None

    def ethr(t):
        return ((t + gmt) % 86400) / 3600          # hour-of-day in exchange tz

    def eday(t):
        return (t + gmt) // 86400                   # day index in exchange tz

    last_day = eday(bars[-1]["t"])
    pre = [b for b in bars if eday(b["t"]) == last_day and 4.0 <= ethr(b["t"]) < 9.5]
    if not pre:
        return None
    # prior regular-session close: last regular bar before today's pre-market window
    reg_prior = [b for b in bars if b["t"] < pre[0]["t"] and 9.5 <= ethr(b["t"]) < 16.0]
    if not reg_prior:
        return None
    prev_close = reg_prior[-1]["c"]
    last = pre[-1]["c"]
    if not prev_close or not last:
        return None
    gap = (last / prev_close - 1) * 100
    if abs(gap) < 2.0:                              # not a notable gap
        return None
    pre_vol = sum(b["v"] for b in pre)
    return {"ticker": sym.upper(), "gap": round(gap, 1), "price": round(last, 2),
            "prev_close": round(prev_close, 2), "pre_vol": int(pre_vol),
            "dir": "up" if gap >= 0 else "down"}


def scan_premarket(tickers, progress=None):
    """Scan tickers for notable pre-market gaps; return movers ranked by gap (gainers first)."""
    movers = []
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        if progress:
            progress(i, total, t)
        try:
            m = _premarket_one(t)
        except Exception:
            m = None
        if m:
            movers.append(m)
        time.sleep(0.03)
    movers.sort(key=lambda x: -x["gap"])            # biggest gainers at the top
    return {"movers": movers[:60], "scanned": total,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


# --------------------------------------------------------------------------- #
# Spinning stocks (intraday reversal / rotation candidates)
# --------------------------------------------------------------------------- #
def _higher_lows(closes, start_i):
    """Higher-lows structure on the 5-min recovery leg (closes only — intraday bars carry no
    high/low). True when the recent swing low sits ABOVE the prior one: a rising 5-min uptrend that
    is likely to keep going even if the latest candle just poked under the 10 EMA. This is why we
    DON'T drop a name the instant one bar dips below the line."""
    seg = closes[max(0, start_i):]
    if len(seg) < 6:
        return False
    piv = [seg[i] for i in range(1, len(seg) - 1) if seg[i] < seg[i - 1] and seg[i] <= seg[i + 1]]
    if len(piv) >= 2 and piv[-1] > piv[-2]:          # last pivot low above the prior one
        return True
    return min(seg[-3:]) > min(seg[-7:-3])           # fallback: a fresh rolling higher low


# A "spin": a stock that got BEATEN DOWN (a real flush off a recent high), then starts to
# ROTATE BACK UP — on the 5-min chart a green candle reclaims a turning-up 10-period EMA, ideally
# with buyers' volume rising on the turn. The ASTS gold-standard: flushed ~$113 -> ~$101, then
# green candles reclaim the 10 EMA at ~$103 as the line flattens and curls up. We rank candidates
# by how much rotation potential is LEFT (early reclaim off a big flush, near the line, buyers
# stepping in) — not how far they've already bounced. Thresholds tuned on live data (ASTS/RKLB/MARA
# rank top; too-shallow, already-recovered, and not-yet-turning names are rejected).
def spin_signal(sym, adr=None):
    """5-min intraday reversal detector. Returns a ranked spin dict or None (gates not met)."""
    bars, gmt = _fetch_intraday(sym, rng="2d")
    if not bars:
        return None
    while bars and bars[-1]["v"] == 0:               # drop the in-progress (no-volume) bar(s)
        bars = bars[:-1]
    if len(bars) < 20:
        return None
    adr = adr or 4.0

    def ethr(t):
        return ((t + gmt) % 86400) / 3600            # hour-of-day in exchange tz
    def eday(t):
        return (t + gmt) // 86400                    # day index in exchange tz

    days = sorted({eday(b["t"]) for b in bars})
    today = days[-1]
    reg = [b for b in bars if eday(b["t"]) == today and 9.5 <= ethr(b["t"]) < 16.0]
    if len(reg) < 12 and len(days) > 1:              # too early in today's session -> use prior day
        today = days[-2]
        reg = [b for b in bars if eday(b["t"]) == today and 9.5 <= ethr(b["t"]) < 16.0]
    if len(reg) < 12:
        return None
    prior = [b for b in bars if eday(b["t"]) < today and 9.5 <= ethr(b["t"]) < 16.0]
    prior_close = prior[-1]["c"] if prior else reg[0]["o"]

    c = [b["c"] for b in reg]
    o = [b["o"] for b in reg]
    v = [b["v"] for b in reg]
    n = len(c)
    # 9-period EMA on the 5-min closes (the smooth turn line — matches the chart's 9 EMA)
    k = 2 / 10
    ma = [c[0]]
    for x in c[1:]:
        ma.append(x * k + ma[-1] * (1 - k))

    ref_high = max(prior_close, max(c))              # beaten-down reference (catches gap-downs)
    hi_i = max(range(n), key=lambda i: c[i])
    trough = min(min(c[hi_i:]), min(c))
    trough_i = min(range(n), key=lambda i: c[i])
    drop = (ref_high - trough) / ref_high * 100 if ref_high > 0 else 0.0
    last = c[-1]
    dist_ma = (last / ma[-1] - 1) * 100 if ma[-1] > 0 else 0.0
    off_low = (last / trough - 1) * 100 if trough > 0 else 0.0
    above = [c[i] > ma[i] for i in range(n)]
    green = [c[i] > o[i] for i in range(n)]
    ma_up = ma[-1] >= ma[-4]
    n_above = 0                                      # consecutive bars closing above the MA
    for i in range(n - 1, -1, -1):
        if above[i]:
            n_above += 1
        else:
            break
    # rising volume on the turn: buyers (up-bar vol) vs sellers (down-bar vol) over the recovery
    # leg only (since the trough) — keeps the early-session flush out of the comparison.
    leg = list(range(max(trough_i, n - 12), n))
    up_v = [v[i] for i in leg if green[i]]
    dn_v = [v[i] for i in leg if not green[i]]
    mu, md = (st.mean(up_v) if up_v else 0.0), (st.mean(dn_v) if dn_v else 0.0)
    vol_ratio = (mu / md) if md > 0 else (1.5 if mu > 0 else 1.0)

    higher_lows = _higher_lows(c, trough_i)
    recently_above = any(above[-3:])                 # reclaimed within the last ~15 min
    tol = min(1.2, max(0.4, 0.2 * adr))              # how far under the 9 EMA still counts as "a little"
    # CONFIRMATION: at least 2 closed green candles have closed above the 9 EMA in the recent window.
    # A single candle tagging the line isn't enough — wait for the reclaim to confirm with 2 green
    # closes above it before the name counts as a spin.
    green_above = sum(1 for i in range(max(0, n - 8), n) if green[i] and above[i])
    confirmed = green_above >= 2

    # ----- gates (all must pass) -----
    beaten = drop >= max(4.0, 0.8 * adr)             # a real flush, scaled to volatility
    # Holding = still above the 9 EMA now, OR (after confirming) only a LITTLE under it while still
    # making higher lows — a shallow dip in a rising 5-min structure isn't dropped, it can keep going.
    holding = above[-1] or (recently_above and higher_lows and dist_ma >= -tol)
    reclaimed = confirmed and holding
    turning = (ma_up or higher_lows) and off_low >= 0.5   # MA curling up OR higher-lows, off the low
    not_late = n_above <= 8 and dist_ma <= 2.2       # the spin hasn't already run away
    if not (beaten and reclaimed and turning and not_late):
        return None

    # ----- potential score (0-100): how much rotation is LEFT -----
    drop_s = min(1.0, drop / 12.0)                              # snapback fuel
    fresh_s = max(0.0, 1.0 - max(0, n_above - 1) / 7.0)         # earlier reclaim = more upside left
    vol_s = max(0.0, min(1.0, (vol_ratio - 0.8) / 1.0))        # buyers > sellers on the turn
    green_s = 0.6 * (1.0 if green[-1] else 0.0) + 0.4 * (1.0 if last >= max(c[-3:]) else 0.0)
    prox_s = max(0.0, 1.0 - abs(dist_ma) / 2.0)                # right at the line = best (under or over)
    offlow_s = 1.0 - min(1.0, abs(off_low - 3.0) / 6.0)        # sweet spot ~1-6% off the low
    struct_s = 1.0 if higher_lows else 0.0                      # rising 5-min structure = stays in
    score = round(100 * (0.24 * drop_s + 0.16 * fresh_s + 0.14 * vol_s + 0.14 * green_s +
                         0.12 * prox_s + 0.08 * offlow_s + 0.12 * struct_s))
    return {
        "ticker": sym.upper(), "score": score, "drop": round(drop, 1),
        "off_low": round(off_low, 1), "dist_ma": round(dist_ma, 2), "n_above": n_above,
        "vol_ratio": round(vol_ratio, 1), "green_last": green[-1], "ma_up": ma_up,
        "higher_lows": higher_lows, "green_above": green_above,
        "price": round(last, 2), "ma": round(ma[-1], 2),
        "trough": round(trough, 2), "fresh": n_above <= 3,
    }


def _daily_adr(sym):
    """Daily ADR% from cached daily bars (cheap — the main scan already cached them)."""
    bars = get_bars(sym)
    if not bars or len(bars) < 21:
        return 4.0
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    return st.mean([(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0]) or 4.0


def scan_spinning(tickers, min_down=-2.5, progress=None):
    """Find spinning stocks. Smart pre-filter: pull live quotes, keep only names DOWN on the day
    (the beaten-down universe), then fetch 5-min bars for just those — fast and on-target. Returns
    spins ranked by potential (highest first)."""
    quotes = fetch_quotes(tickers)
    cand = [s.strip().upper() for s in tickers
            if (quotes.get(s.strip().upper(), {}).get("change_pct") or 0) <= min_down]
    spins = []
    total = len(cand)
    for i, t in enumerate(cand, 1):
        if progress:
            progress(i, total, t)
        try:
            sig = spin_signal(t, _daily_adr(t))
        except Exception:
            sig = None
        if sig:
            spins.append(sig)
        time.sleep(0.03)
    spins.sort(key=lambda x: -x["score"])
    return {"spins": spins[:60], "scanned": len(tickers), "candidates": total,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def sector_metrics(tickers):
    """Composite momentum metrics for a group of tickers (a sector/theme),
    plus per-member detail (sorted hottest-first) for the expandable view."""
    arrays = []
    members = []
    for t in tickers:
        bars = get_bars(t)
        if not bars or len(bars) < 25:
            continue
        c = [b["close"] for b in bars]
        arrays.append(c)

        def mp(k):
            return round((c[-1] / c[-1 - k] - 1) * 100, 2) if len(c) > k else 0.0
        members.append({"ticker": t, "close": round(c[-1], 2), "perf_1d": mp(1),
                        "perf_1w": mp(5), "perf_1mo": mp(21), "above20": c[-1] > _sma(c, 20)})
    if not arrays:
        return None

    def perf(k):
        vals = [(c[-1] / c[-1 - k] - 1) * 100 for c in arrays if len(c) > k]
        return round(st.mean(vals), 2) if vals else 0.0

    breadth = round(100 * sum(1 for c in arrays if c[-1] > _sma(c, 20)) / len(arrays))
    w = min(70, min(len(c) for c in arrays))
    comp = [st.mean([c[i] / c[-w] for c in arrays]) for i in range(-w, 0)]
    k = 2 / 11
    e = comp[0]
    emas = []
    for x in comp:
        e = x * k + e * (1 - k)
        emas.append(e)
    streak = 0
    for i in range(len(comp) - 1, -1, -1):
        if comp[i] >= emas[i]:
            streak += 1
        else:
            break
    p1, p5, p21, p63, p126 = perf(1), perf(5), perf(21), perf(63), perf(126)
    # momentum state: is the move accelerating (Rising) or losing steam (Slowing)?
    mo_pace, wk_pace = p21 / 21, p5 / 5
    if p5 <= -3:
        trend = "Falling"
    elif p5 >= 2 and wk_pace >= mo_pace:
        trend = "Rising"
    elif p21 >= 3 and wk_pace < mo_pace * 0.5:
        trend = "Slowing"
    else:
        trend = "Steady"
    fresh = trend == "Rising" and streak <= 5          # newly sparking up
    score = round(p5 * 0.4 + p21 * 0.3 + p1 * 0.2 + (breadth - 50) * 0.05, 2)
    members.sort(key=lambda m: m["perf_1w"], reverse=True)
    return {"count": len(arrays), "perf_1d": p1, "perf_1w": p5, "perf_1mo": p21,
            "perf_3mo": p63, "perf_6mo": p126, "breadth": breadth, "streak": streak,
            "trend": trend, "fresh": fresh, "score": score, "members": members}


# --------------------------------------------------------------------------- #
# Market regime (SPX / QQQ / IWM) + relative-strength benchmark
# --------------------------------------------------------------------------- #
def _regime_one(name, bars):
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    close = c[-1]
    adr = st.mean([(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0]) or 1
    e10, e20, s50 = _ema(c, 10), _ema(c, 20), _sma(c, 50)
    above = int(close > e10) + int(close > e20) + int(close > s50)
    ext10 = (close / e10 - 1) * 100
    hi = max(h[-50:]) if len(h) >= 50 else max(h)
    off_high = (hi / close - 1) * 100
    dipped = min(l[-6:]) < e20                              # recently traded below the 20-EMA
    # Distance from the 50-MA as a multiple of daily range — the correction-risk gauge.
    # (% gain above the 50 divided by ADR%; a high multiple marks possible highs before a
    # pullback. Normalizing by ADR self-adjusts for each index's volatility.)
    gain_50 = (close / s50 - 1) * 100
    atr_mult_50 = round(gain_50 / adr, 1)
    gain_20 = (close / e20 - 1) * 100                       # shorter-term extension gauge
    atr_mult_20 = round(gain_20 / adr, 1)
    very_stretched = atr_mult_50 >= 4.5                     # far above the 50 -> chase risk
    # Early turn / "bottoming": still BELOW the 50-MA (not a confirmed bull move yet), but the first
    # leg up is CONFIRMED — price reclaimed both short EMAs with the 10 stacked over the 20, AND a
    # higher low is in place (structure broke the downtrend). We do NOT anticipate bottoms: a bear
    # bounce that merely pokes the 10-EMA without a higher low stays "correction". Graded 48 — just
    # UNDER the weak-tape line (REGIME_WEAK 50) on purpose: it lifts the best patient leaders from C
    # toward B as the turn starts, but A/A+ stay locked until price reclaims the 50 into "Recovery"
    # (80). Don't be the first one in — warm up on the confirmed turn, press once it's above the 50.
    turning = (len(l) >= 20 and close > e10 > e20 and close > e20
               and min(l[-10:]) > min(l[-20:-10]))
    # base state from trend position
    if close < s50 and turning:
        state, posture = "Bottoming / turning up", 48
    elif close < s50 and off_high >= 8:
        state, posture = "Deep correction", 15
    elif close < s50:
        state, posture = "Mid-correction", 25
    elif above == 3 and ext10 > 2.2 * adr:
        state, posture = "Extended", 55
    elif above < 2:                                        # above 50 but below the 10 & 20
        state, posture = "Pullback", 45
    elif close > e20 and dipped:
        state, posture = "Recovery", 80
    else:
        state, posture = "Healthy uptrend", 85
    # correction-risk haircut: the further above the 50 (in ADR units), the lower the upside.
    # Graded, so a mildly-stretched index is barely dinged while a very stretched one is capped.
    if very_stretched:
        posture = min(posture, round(max(48, 78 - (atr_mult_50 - 4.5) * 6)))
        if state in ("Healthy uptrend", "Recovery"):
            state = "Extended"
    p1m = (close / c[-21] - 1) * 100 if len(c) >= 21 else 0.0
    p3m = (close / c[-63] - 1) * 100 if len(c) >= 63 else 0.0
    return {"name": name, "state": state, "posture": posture, "close": round(close, 2),
            "ext10": round(ext10, 1), "off_high": round(off_high, 1), "above": above,
            "adr": round(adr, 2), "p1m": round(p1m, 1), "p3m": round(p3m, 1),
            "gain_50": round(gain_50, 1), "atr_mult_50": atr_mult_50,
            "gain_20": round(gain_20, 1), "atr_mult_20": atr_mult_20,
            "stretched_50": very_stretched}


def _rsi(closes, n=14):
    """Wilder-simple RSI on the close series (0-100). None if too little data."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def fear_greed(market="us", tickers=None):
    """A 0-100 market-sentiment gauge (0 = extreme fear, 100 = extreme greed), blended from up to
    five components built ENTIRELY from data we already fetch — CNN-Fear&Greed style:
      • extension — how far the indexes sit above their 50-MA in ADR units (stretched = greed)
      • rsi       — average index RSI-14 (overbought = greed)
      • breadth   — % of the universe above its own 50-MA (participation)
      • highs_lows— share of the universe near its 52-wk high vs near its low
      • vix       — inverse 1-yr percentile of VIX (low vol / complacency = greed; US only)
    Each component is 0-100; the score is their mean. Returns None if nothing is computable.
    Used to NUDGE the regime posture (greed/froth trims it — see market_regime)."""
    comps = {}
    idxbars = [b for _, s in mcfg(market)["indexes"] for b in [get_bars(s)] if b and len(b) >= 60]
    if idxbars:
        exts, rsis = [], []
        for b in idxbars:
            c = [x["close"] for x in b]
            try:
                exts.append(_regime_one("idx", b)["atr_mult_50"])
            except Exception:
                pass
            r = _rsi(c)
            if r is not None:
                rsis.append(r)
        if exts:
            comps["extension"] = max(0, min(100, 50 + st.mean(exts) * 5))   # 0 ADR=50, +10 ADR=100
        if rsis:
            comps["rsi"] = max(0, min(100, st.mean(rsis)))
    if tickers:
        above = tot = hi = lo = 0
        for t in tickers:
            b = get_bars(t)
            if not b or len(b) < 60:
                continue
            c = [x["close"] for x in b]
            tot += 1
            if c[-1] > _sma(c, 50):
                above += 1
            if c[-1] >= max(x["high"] for x in b) * 0.98:
                hi += 1
            if c[-1] <= min(x["low"] for x in b) * 1.02:
                lo += 1
        if tot:
            comps["breadth"] = round(100 * above / tot)
        if hi + lo:
            comps["highs_lows"] = round(100 * hi / (hi + lo))
    if market == "us":                                                      # ^VIX is a US gauge only
        vix = get_bars("^VIX")
        if vix and len(vix) >= 60:
            vc = [b["close"] for b in vix]
            pct = 100 * sum(1 for x in vc if x <= vc[-1]) / len(vc)
            comps["vix"] = round(100 - pct)                                 # low VIX percentile = greed
    if not comps:
        return None
    score = round(st.mean(list(comps.values())))
    label = ("Extreme Fear" if score < 20 else "Fear" if score < 40 else
             "Neutral" if score <= 55 else "Greed" if score <= 75 else "Extreme Greed")
    return {"score": score, "label": label, "components": {k: round(v) for k, v in comps.items()}}


def vix_trend():
    """VELOCITY/context read of the VIX from daily ^VIX closes — the 'are we starting to panic? is the
    panic fading?' signal (the rate of change, not the level). Returns a dict (level, 1d/5d/7d % change,
    20-MA + vs-MA, a 5-state label, a ~24-bar sparkline, and an as-of date) or None if VIX bars are
    unavailable. US gauge only. The bands/thresholds are FIXED market-structure priors (VIX 15/20/30,
    ±12% velocity), declared a-priori — NOT fitted to our data. DELIBERATELY SEPARATE from fear_greed()
    and from the posture nudge: it NEVER enters grading — it feeds only the displayed regime, the
    prediction narrative, and defend mode (the 'firewall' so VIX velocity can't leak into grades)."""
    vix = get_bars("^VIX")
    if not vix or len(vix) < 8:
        return None
    vc = [b["close"] for b in vix]
    level = vc[-1]
    chg_1d = (level / vc[-2] - 1) * 100 if vc[-2] else 0.0
    chg_5d = (level / vc[-6] - 1) * 100 if len(vc) >= 6 and vc[-6] else 0.0
    chg_7d = (level / vc[-8] - 1) * 100 if vc[-8] else 0.0
    ma20 = _sma(vc, 20) if len(vc) >= 20 else st.mean(vc)
    vs_ma20 = (level / ma20 - 1) * 100 if ma20 else 0.0
    # 6-state classifier, anchored to vs_ma20 (VIX relative to its OWN 20-day mean) — NOT absolute level,
    # because the validated macro research found VIX LEVEL is noise. Fixes the bug where a VIX that spiked
    # (+40% Fri to 21.51) then drained one day to 19.74 was labelled 'calm' — it fell under the old hard
    # `level >= 20` gate on 'rising' yet was +23% over 5d / +14.8% above its mean (2026-06-08, trader-found).
    # Priority: spiking -> elevated-falling -> rising -> elevated -> falling -> calm.
    # 'elevated-falling' BEFORE 'rising': a high-but-DRAINING VIX travels down, so the trailing-5d spike must
    # not read as 'rising'. 'rising' requires chg_1d >= -5 so a 5d spike that is NOW unwinding (-8% today)
    # falls through to 'elevated' rather than falsely implying fear is still building.
    if chg_1d >= 20 or chg_5d >= 30:
        state = "spiking"
    elif vs_ma20 >= 10 and chg_5d <= -12:
        state = "elevated-falling"
    elif (chg_5d >= 12 or vs_ma20 >= 15) and chg_1d >= -5:
        state = "rising"
    elif vs_ma20 >= 10:
        state = "elevated"
    elif chg_5d <= -12:
        state = "falling"
    else:
        state = "calm"
    return {"level": round(level, 2), "change_1d_pct": round(chg_1d, 1),
            "change_5d_pct": round(chg_5d, 1), "change_7d_pct": round(chg_7d, 1),
            "ma20": round(ma20, 2), "vs_ma20_pct": round(vs_ma20, 1),
            "state": state, "spark": [round(x, 2) for x in vc[-24:]],
            "as_of": vix[-1].get("time", "")}


def _posture_label(p):
    return ("Risk-on - uptrend" if p >= 80 else "Constructive" if p >= 60 else
            "Mixed / pullback" if p >= 45 else "Caution - correction" if p >= 25 else
            "Risk-off - deep correction")


def market_regime(market="us", tickers=None):
    """Classify the benchmark indexes -> blended posture (0-100), then NUDGE it with the Fear &
    Greed gauge: a frothy/greedy tape (indexes stretched above the 50, overbought RSI, low VIX,
    narrowing breadth) TRIMS the posture (correction risk) so grades cool when the market is
    extended. Greed-only by design — fear never *adds* posture (the price-based states already
    handle weakness; a fear boost would reward buying a falling knife). `tickers` = the universe,
    for the breadth + highs/lows components (omitted if not supplied)."""
    idx = []
    for name, sym in mcfg(market)["indexes"]:
        bars = get_bars(sym)
        if bars and len(bars) >= 60:
            try:
                idx.append(_regime_one(name, bars))
            except Exception:
                pass
    if not idx:
        return None
    posture_raw = round(st.mean([i["posture"] for i in idx]))
    fg = fear_greed(market, tickers)
    nudge = max(-12, -round(max(0, fg["score"] - 55) * 0.35)) if fg else 0
    posture = max(0, min(100, posture_raw + nudge))
    out = {"computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "posture": posture, "posture_raw": posture_raw,
           "label": _posture_label(posture), "indexes": idx}
    if fg:
        out["fear_greed"] = {**fg, "posture_nudge": nudge}
    if market == "us":                       # VIX velocity read — US only; sibling of fear_greed, NOT
        vt = vix_trend()                     # folded into posture/nudge → it can never touch grades (firewall)
        if vt:
            out["vix_trend"] = vt
    return out


def _benchmark_returns(market="us"):
    """Equal-blend 1m / 3m return of the market's benchmark indexes, for RS outperformance."""
    r1, r3 = [], []
    for _, sym in mcfg(market)["indexes"]:
        bars = get_bars(sym)
        if not bars:
            continue
        c = [b["close"] for b in bars]
        if len(c) > 21:
            r1.append((c[-1] / c[-21] - 1) * 100)
        if len(c) > 63:
            r3.append((c[-1] / c[-63] - 1) * 100)
    return (st.mean(r1) if r1 else 0.0), (st.mean(r3) if r3 else 0.0)


# Fields carried over from a force_no_deep RE-ANALYZE (the leadership-gate reclassification, 2026-06-09).
# We re-run analyze() so the new setup_type's OWN entry/stop/score logic produces these — no duplication.
# Sizing fields (shares/risk) are re-derived too; if the caller passes settings they match live, else the
# re-analyze uses {} (sizing differs but setup_type/score/levels — what the gate is about — are correct).
_RECLASS_FIELDS = (
    "setup_type", "score", "entry", "stop", "inval", "risk_ps", "target", "shares",
    "shares_per_10k", "dollar_risk", "entry_type", "entry_note", "stop_basis",
    "entry_quality", "worth_waiting", "zone_top", "zone_bottom", "buyable_now",
    "extended", "why", "entries",
)


def _rs_score_term(rs_pct, trend_template):
    """FIX 2 (2026-06-09): structural credit the raw `score` was MISSING — it had ZERO relative-strength
    or trend-template term, so a clean RS-97/TT-pass leader (CIFR) got no credit while a deep-pullback
    non-leader collected +4/proximity/perf bonuses. Quality was inverted.
      RS leg: top-decile RS scaled, (rs_pct-50)/15 capped at +3 (50th pct -> 0, ~95th -> +3).
      TT leg: +1.5 for a full Trend-Template pass (the 7 price/MA criteria + RS>=70).
    CIFR (RS97/TT) gains ~+3.1 + 1.5 = +4.6; NXT (RS53/TT-fail) gains ~+0.2 + 0 = ~0. Cross-universe
    percentile is the trader's intended 'RS', so this lives in the post-loop where rs_pct is final."""
    rs_leg = max(0.0, min(3.0, (rs_pct - 50.0) / 15.0))
    tt_leg = 1.5 if trend_template else 0.0
    return rs_leg + tt_leg


def _attach_rs(results, market="us", benchmark=None, bars_map=None, settings=None):
    """Relative strength = 0.5 * percentile-in-universe + 0.5 * outperformance-vs-index.
    `benchmark=(b1,b3)` overrides the index 1m/3m returns with AS-OF values — the blind backtest MUST
    pass this (computed from index bars sliced to date D), else the outperformance leg subtracts today's
    index return from a past date (a lookahead that mis-scores RS / grade). Live callers pass None.
    `bars_map` = {ticker: bars} enables the 2026-06-09 LEADERSHIP GATE: a provisional 'Deep Pullback'
    that fails (rs_pct>=70 OR trend_template) is RE-ANALYZED with force_no_deep=True so it falls to its
    natural classification (losing the +4 / patient-A path it didn't earn). Without bars_map the gate is
    SKIPPED (RS score term still applies) — pass it from live/scan so the reclassification takes effect."""
    if not results:
        return
    b1, b3 = benchmark if benchmark is not None else _benchmark_returns(market)
    blended = [0.5 * r.get("p1m", 0) + 0.5 * r.get("p3m", 0) for r in results]
    order = sorted(range(len(results)), key=lambda i: blended[i])
    n = len(results)
    pct = [0.0] * n
    for rank, i in enumerate(order):
        pct[i] = 100.0 * rank / (n - 1) if n > 1 else 100.0
    for i, r in enumerate(results):
        outperf = 0.5 * (r.get("p1m", 0) - b1) + 0.5 * (r.get("p3m", 0) - b3)
        op_score = max(0.0, min(100.0, 50.0 + outperf * 2.0))
        r["rs_outperf"] = round(outperf, 1)
        r["rs_pct"] = round(pct[i])
        r["rs_score"] = round(0.5 * pct[i] + 0.5 * op_score)
        # finalize the Minervini Trend Template: the 7 price/MA criteria (from analyze) + RS rating >=70
        rs_ok = r["rs_pct"] >= 70
        r["trend_template"] = bool(r.get("tt_pass_price")) and rs_ok
        r["tt_count"] = r.get("tt_count_price", 0) + (1 if rs_ok else 0)   # out of 8
        r["tt_rs_ok"] = rs_ok

    # ---- FIX 1: leadership gate on Deep Pullback (2026-06-09, trader-approved) ----
    # The in-analyze `strong_leader` gate keys on ABSOLUTE gain only (p6m/p3m>=30) — no relative
    # strength, no trend-template. So choppy non-leaders (NXT RS60, MRNA RS9, SATS RS41...) collected
    # the deep-pullback +4 and patient A-path they hadn't earned. Require a REAL leader IN ADDITION:
    # rs_pct>=70 OR trend_template. Failers are re-analyzed with force_no_deep so the SAME code path
    # produces their natural setup_type + matching entry/stop/score (no duplicated logic).
    if bars_map:
        for r in results:
            if r.get("setup_type") != "Deep Pullback":
                continue
            if (r.get("rs_pct", 0) >= 70) or r.get("trend_template"):
                continue                                   # a genuine leader — keep Deep Pullback
            bars = bars_map.get(r["ticker"])
            if not bars:
                continue                                   # can't re-derive cleanly — leave as-is (caveat)
            try:
                re_a = analyze(r["ticker"], bars, settings or {}, force_no_deep=True)
            except Exception:
                continue
            r["reclassified_from"] = "Deep Pullback"        # audit trail of the deliberate move
            for k in _RECLASS_FIELDS:
                if k in re_a:
                    r[k] = re_a[k]

    # ---- FIX 2: add the RS + trend-template credit to the raw score (post-loop: rs_pct is final) ----
    # IDEMPOTENT (Burry 2026-06-09): stash the pre-credit base ONCE and always recompute from it, so a
    # second pass through _attach_rs (re-grade / retry / test) can't stack the term (+4.5) twice.
    for r in results:
        base = r.get("score_base")
        if base is None:
            base = r.get("score", 0)
            r["score_base"] = base
        r["score"] = round(base + _rs_score_term(r.get("rs_pct", 0), r.get("trend_template")), 1)


def detect_groups(tickers, lookback=15, min_move=8.0, corr_thr=0.86, max_cand=120, progress=None):
    """Find EMERGING groups: strong recent movers whose DAILY returns are highly correlated move
    together — often a fresh theme before it's an official sector. Take the recent leaders, z-score
    their last `lookback` daily returns, link any pair with correlation >= corr_thr, and return the
    connected components (size 3-20). Pure stdlib; bars are cached so this is cheap."""
    data = []
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        if progress:
            progress(i, total, t)
        bars = get_bars(t)
        if not bars or len(bars) < lookback + 6:
            continue
        c = [b["close"] for b in bars]
        if c[-6] <= 0 or c[-1] <= 0:
            continue
        p1w = (c[-1] / c[-6] - 1) * 100
        p1m = (c[-1] / c[-21] - 1) * 100 if len(c) > 21 else p1w
        rets = [(c[k] / c[k - 1] - 1) for k in range(-lookback, 0)]
        data.append({"t": t, "p1w": p1w, "p1m": p1m, "rets": rets})
    cand = [d for d in data if d["p1w"] >= min_move or d["p1m"] >= 20.0]
    cand.sort(key=lambda d: max(d["p1w"], d["p1m"] / 2), reverse=True)
    cand = cand[:max_cand]
    n = len(cand)
    if n < 3:
        return []
    for d in cand:                                            # z-score so mean(zi*zj) == Pearson r
        r = d["rets"]; m = sum(r) / len(r)
        sd = (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5 or 1e-9
        d["z"] = [(x - m) / sd for x in r]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    L = len(cand[0]["z"])
    for i in range(n):
        zi = cand[i]["z"]
        for j in range(i + 1, n):
            zj = cand[j]["z"]
            corr = sum(zi[k] * zj[k] for k in range(L)) / L
            if corr >= corr_thr:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comp = {}
    for i in range(n):
        comp.setdefault(find(i), []).append(i)
    groups = []
    for idxs in comp.values():
        if not (3 <= len(idxs) <= 20):
            continue
        members = [{"ticker": cand[i]["t"], "perf_1w": round(cand[i]["p1w"], 1),
                    "perf_1mo": round(cand[i]["p1m"], 1)} for i in idxs]
        members.sort(key=lambda m: m["perf_1w"], reverse=True)
        groups.append({"members": members, "size": len(members),
                       "avg_1w": round(sum(m["perf_1w"] for m in members) / len(members), 1)})
    groups.sort(key=lambda g: g["avg_1w"], reverse=True)
    return groups


def scan(tickers, settings=None, progress=None, max_age=12, market="us", forming_date=None):
    # forming_date = today's market-local date string while a session is IN PROGRESS (else None). A
    # ticker whose freshest daily bar IS that date has a still-forming last bar, so analyze() runs the
    # respected-bounce gate on settled structure only (see analyze's forming_last). Per-ticker so a
    # stale/halted name (last bar != today) is unaffected. None ⇒ all bars treated as settled (EOD).
    results, fails = [], []
    bars_map = {}                   # ticker -> bars, so the leadership-gate reclassification can re-analyze
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        bars = get_bars(t, max_age_hours=max_age)
        if progress:
            progress(i, total, t)
        fetched = did_fetch()
        if not bars:
            fails.append(t)
            if fetched:
                time.sleep(0.05)
            continue
        try:
            _forming = bool(forming_date and bars[-1].get("time") == forming_date)
            results.append(analyze(t, bars, settings, forming_last=_forming))
            bars_map[t.upper()] = bars
        except Exception:
            fails.append(t)
        if fetched:                 # only throttle when we actually hit Yahoo (cache miss)
            time.sleep(0.05)
    _attach_rs(results, market, bars_map=bars_map, settings=settings)
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"results": results, "failed": fails,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


if __name__ == "__main__":
    import sys
    test = sys.argv[1:] or ["DOCN", "INOD", "MSFT", "ONDS", "FIVN", "RNG", "NBIS", "AAON"]
    out = scan(test, settings={"account_size": 20217.62, "risk_pct": 1.0})
    print(f"scanned={len(out['results'])} failed={out['failed']}\n")
    for r in out["results"]:
        print(f"{r['ticker']:6} {r['setup_type']:22} sc={r['score']:5} "
              f"entry={r['entry']:>9} stop={r['stop']:>9} sh={r['shares']} | {r['why']}")
