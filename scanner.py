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
import statistics as st
from pathlib import Path
from datetime import datetime, timezone

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


# --------------------------------------------------------------------------- #
# Data fetching (with on-disk daily cache)
# --------------------------------------------------------------------------- #
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
                return bars
        except Exception:
            time.sleep(0.4)
    return None


_DID_FETCH = False              # set by get_bars: True when the last call hit the network (cache miss)


def get_bars(sym, max_age_hours=12):
    global _DID_FETCH
    _DID_FETCH = False
    sym = sym.strip().upper()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{sym}.json"
    if f.exists():
        try:
            age = (time.time() - f.stat().st_mtime) / 3600
            obj = json.loads(f.read_text())
            if age < max_age_hours and obj.get("bars"):
                return obj["bars"]
        except Exception:
            pass
    bars = _fetch_raw(sym)
    _DID_FETCH = True
    if bars:
        try:
            f.write_text(json.dumps({"sym": sym, "bars": bars}))
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
                except Exception:
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


# --------------------------------------------------------------------------- #
# Entry-plan builders. A suggestion can carry up to two entry options (a buy-stop
# BREAKOUT above a pivot, and/or a buy-the-dip PULLBACK to support below price).
# Each returns a self-contained plan dict (entry/stop/zone/sizing-ready) or None
# when the option doesn't make sense — so we "show two only where it makes sense."
# --------------------------------------------------------------------------- #
def _breakout_plan(pivot, close, adr, note="buy-stop above the pivot high"):
    """Buy-STOP above an upside pivot, 1x-ADR stop. The pivot must sit at/above the
    current price (a genuine upside trigger), else there's nothing to break out over."""
    if not pivot or pivot <= 0 or pivot < close:
        return None
    entry = round(pivot, 2)
    e_adr = entry * adr / 100 or 0.01
    stop = round(entry * (1 - adr / 100), 2)
    risk_ps = round(entry - stop, 2)
    if risk_ps <= 0:
        return None
    zone_bottom = round(entry, 2)
    zone_top = round(entry + 0.5 * e_adr, 2)
    buf = 0.3 * e_adr
    return {
        "kind": "breakout", "entry_type": "stop", "entry": entry, "stop": stop,
        "stop_basis": "1x ADR below the trigger", "inval": stop,
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


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def analyze(sym, bars, settings=None):
    settings = settings or {}
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    v = [b["volume"] for b in bars]
    o = [b["open"] for b in bars]
    close = c[-1]

    adr = st.mean([(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0])
    adr_safe = adr or 1
    s10, s20, s50 = _sma(c, 10), _sma(c, 20), _sma(c, 50)
    s150 = _sma(c, 150) if len(c) >= 150 else None
    s200 = _sma(c, 200) if len(c) >= 200 else None
    e10, e20, e50 = _ema(c, 10), _ema(c, 20), _ema(c, 50)
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
    tight = (base_h / base_l - 1) * 100
    dist_hi = (base_h / close - 1) * 100
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
    is_deep_pullback = (strong_leader and close < e10 and close < e20
                        and 5 <= pull_from_high <= 50)
    # Consolidation: a strong stock going SIDEWAYS in a tight base while riding the 50 EMA
    # (the SNDK base — buy the dips to the 50 while it "waits"). A patient, worth-waiting setup.
    rng15 = (max(h[-15:]) / min(l[-15:]) - 1) * 100 if len(h) >= 15 else 100.0
    net15 = abs(c[-1] / c[-16] - 1) * 100 if len(c) >= 16 else 100.0   # net move = how SIDEWAYS it is
    is_consolidation = (above_200_now and (p3m >= 15 or p6m >= 25) and close > s50
                        and rng15 <= 4.0 * adr            # tight base
                        and net15 <= 0.4 * rng15          # genuinely sideways (not a directional pullback)
                        and ext10 <= 1.5 * adr)           # not extended above the 10

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
                vol_signal, vol_adj = "dry", 0.5     # trimmed from 1.5 — didn't add edge in the backtest
                vol_note = "pullback on drying volume — sellers exhausting"
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
    if volc and volc < 0.85:
        score += 1
    score += vol_adj                                       # volume character (accumulation/distribution)

    # ----- trade levels -----
    swing_low = min(l[-7:])
    if deep:
        # Catch the strong leader at the 50 EMA — but never DEEPER than the recent pullback low.
        # On parabolic names the 50 EMA lags far below price; the stock is really finding support
        # at the swing low, so we floor the entry there. Buy zone can sit below current price
        # (wait for the dip) or above (a reclaim). Stop tight, just under the swing low.
        swing = min(l[-10:])
        entry = round(max(e50, swing), 2)
        if close < entry:
            entry_type = "stop"
            entry_note = "buy on a reclaim toward the 50 EMA (strong leader — worth waiting)"
        else:
            entry_type = "limit"
            entry_note = "buy the dip into the 50 EMA / recent support (worth waiting)"
        adr_px = entry * adr / 100
        raw = min(swing - 0.10 * adr_px, entry - 0.40 * adr_px)   # tight, just under the swing
        raw = max(raw, entry - 1.5 * adr_px)                       # but never absurdly wide
        stop = round(raw, 2)
        inval = round(swing, 2)
        stop_basis = f"below the pullback swing low ${round(swing, 2)} (close below)"
    elif consol:
        # Buy the dip to the 50 EMA inside the base (floored at the base low). Stop below the base.
        base_low = min(l[-15:])
        entry = round(max(e50, base_low), 2)
        if close < entry:
            entry_type = "stop"
            entry_note = "buy the reclaim of the 50 EMA in the base (worth waiting)"
        else:
            entry_type = "limit"
            entry_note = "buy the dip to the 50 EMA inside the consolidation (worth waiting)"
        adr_px = entry * adr / 100
        raw = min(base_low - 0.10 * adr_px, entry - 0.40 * adr_px)
        raw = max(raw, entry - 1.5 * adr_px)
        stop = round(raw, 2)
        inval = round(base_low, 2)
        stop_basis = f"below the consolidation low ${round(base_low, 2)} (close below)"
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
        wide_floor = entry - 1.2 * adr_px
        tight_cap = entry - 0.45 * adr_px        # min 0.45x ADR (backtest: 0.35 got wicked out by noise)
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
        entry = round(base_h, 2)                           # buy-stop above the consolidation high
        stop = round(entry * (1 - adr / 100), 2)
        inval = round(base_l, 2)
        entry_type = "stop"
        entry_note = "buy-stop above the consolidation high"
        stop_basis = "1x ADR below the trigger"
    risk_ps = round(entry - stop, 2)
    target = round(entry + 2 * risk_ps, 2)
    # entry-location quality (0-100): penalize buying stretched far above the 10-EMA and
    # penalize a stop forced near the full 1x-ADR limit. A valid setup bought right on the
    # rising line with a tight stop scores ~100; the same setup chased 2x ADR up scores low.
    stretch_x = max(0.0, ext10 / adr_safe)
    stretch_pen = min(50.0, stretch_x * 25.0)
    one_adr_px = entry * adr / 100 or 1
    width_pen = min(40.0, max(0.0, risk_ps / one_adr_px - 0.4) * 60.0)
    entry_quality = round(max(0.0, 100.0 - stretch_pen - width_pen))
    # buy zone = a band around the entry (you don't need an exact tick). Being inside it = buyable now.
    adr_px = entry * adr / 100
    if entry_type == "limit":
        zone_top = round(entry + 0.6 * adr_px, 2)        # a bit above the support still buys the pullback
        zone_bottom = round(entry - 0.25 * adr_px, 2)
    else:
        zone_top = round(entry + 0.5 * adr_px, 2)
        zone_bottom = round(entry, 2)
    buf = 0.3 * adr_px                                    # a little tolerance — just-above the zone still counts
    buyable_now = (zone_bottom - buf) <= close <= (zone_top + buf)
    # don't call it "buyable now" if today is a distribution/reversal bar or the stock is
    # stretched — wait for it to come back to the line instead of buying into the move.
    if distribution_today or extended:
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
    parabolic = ext50_adr >= 4.5
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
    else:                                                # alt = break above the prior-day high
        alt = _breakout_plan(max(h[-2:]), close, adr, "buy-stop above the prior-day high")
    entries = [primary]
    if alt and abs(alt["entry"] - primary["entry"]) >= 0.4 * (entry * adr / 100 or 0.01):
        if distribution_today or extended or (parabolic and not worth_waiting):
            alt["buyable_now"] = False                   # never flag an alt buyable into a hot/extended move
        entries.append(alt)

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
        why.append(f"base {tight / adr_safe:.1f}x ADR, {dist_hi:.1f}% to trigger")
    else:
        why.append(f"gap {max_day:.0f}%, {dist_hi:.1f}% to trigger")
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
        "recent_gap": recent_gap, "news_move": news_move,
        "vol_signal": vol_signal, "vol_note": vol_note,
        "distribution_today": distribution_today,
    }


def analyze_at(sym, date, settings=None):
    """Run analyze() on daily bars SLICED to a past date (a trade's entry date) so a setup can be
    graded AS OF when it was taken. Also attaches an RS-outperformance proxy vs the equal-blend
    SPX/QQQ/IWM sliced to the same date. Price-based only — market regime / sector heat / news
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
    for _, isym in INDEXES:
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
    # base state from trend position
    if close < s50 and off_high >= 8:
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


def market_regime():
    """Classify SPX / QQQ / IWM and return a blended market posture (0-100)."""
    idx = []
    for name, sym in INDEXES:
        bars = get_bars(sym)
        if bars and len(bars) >= 60:
            try:
                idx.append(_regime_one(name, bars))
            except Exception:
                pass
    if not idx:
        return None
    avg = st.mean([i["posture"] for i in idx])
    if avg >= 80:
        label = "Risk-on - uptrend"
    elif avg >= 60:
        label = "Constructive"
    elif avg >= 45:
        label = "Mixed / pullback"
    elif avg >= 25:
        label = "Caution - correction"
    else:
        label = "Risk-off - deep correction"
    return {"computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "posture": round(avg), "label": label, "indexes": idx}


def _benchmark_returns():
    """Equal-blend 1m / 3m return of SPX+QQQ+IWM, for relative-strength outperformance."""
    r1, r3 = [], []
    for _, sym in INDEXES:
        bars = get_bars(sym)
        if not bars:
            continue
        c = [b["close"] for b in bars]
        if len(c) > 21:
            r1.append((c[-1] / c[-21] - 1) * 100)
        if len(c) > 63:
            r3.append((c[-1] / c[-63] - 1) * 100)
    return (st.mean(r1) if r1 else 0.0), (st.mean(r3) if r3 else 0.0)


def _attach_rs(results):
    """Relative strength = 0.5 * percentile-in-universe + 0.5 * outperformance-vs-index."""
    if not results:
        return
    b1, b3 = _benchmark_returns()
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


def scan(tickers, settings=None, progress=None, max_age=12):
    results, fails = [], []
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        bars = get_bars(t, max_age_hours=max_age)
        if progress:
            progress(i, total, t)
        fetched = _DID_FETCH
        if not bars:
            fails.append(t)
            if fetched:
                time.sleep(0.05)
            continue
        try:
            results.append(analyze(t, bars, settings))
        except Exception:
            fails.append(t)
        if fetched:                 # only throttle when we actually hit Yahoo (cache miss)
            time.sleep(0.05)
    _attach_rs(results)
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
