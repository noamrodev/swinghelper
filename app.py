"""
Trading Data Center - local control center for the momentum swing-trading coach.

Pure standard-library web app. Launch with:  python app.py
Then open http://localhost:8765 (it also opens automatically).

Serves the single-page UI in web/ and a small JSON API. Structured data lives in
data/*.json (source of truth); watchlist.md and journal/trades.md are regenerated
from JSON on every write so the Claude coach sees current data in chat.
"""
import json
import os
import shutil
import threading
import time
import base64
import re
import webbrowser
import mimetypes
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, quote

import scanner
import universe

BASE = Path(__file__).resolve().parent
SEED = BASE / "data"                       # shared files baked into the image / repo
# DATA is where live data is read/written. Locally it's just data/. On a host with a
# persistent disk, set DATA_DIR to the mounted volume so journals survive redeploys;
# shared seed files are copied in on first boot (see seed_shared()).
DATA = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else SEED
WEB = BASE / "web"
UPLOADS = DATA / "uploads"

# Shared, market-wide data (one copy for everyone). SETTINGS_F is the owner's config,
# used only as a fallback for shared jobs (scan/universe params); per-user settings live
# in the workspace dir (see settings_f()).
SETTINGS_F = DATA / "settings.json"
SCREENERS_F = DATA / "screeners.json"
SUGGEST_F = DATA / "suggestions.json"
SECTORS_F = DATA / "sectors.json"
THEMES_F = DATA / "themes.json"
SECTOR_HEAT_F = DATA / "sector_heat.json"
NEWS_F = DATA / "news.json"
MARKET_F = DATA / "market.json"
UNIVERSE_F = DATA / "universe.json"
SUSPICIOUS_F = DATA / "suspicious.json"
PREMARKET_F = DATA / "premarket.json"

DOCS = {
    "qullamaggie": BASE / "strategy" / "qullamaggie.md",
    "martin-luk": BASE / "strategy" / "martin-luk.md",
    "my-rules": BASE / "strategy" / "my-rules.md",
    "pullback-avwap": BASE / "strategy" / "pullbacks-avwap.md",
    "lessons": BASE / "journal" / "lessons.md",
}

# --------------------------------------------------------------------------- #
# Multi-user workspaces.
#   * Local (HOSTED unset): everything stays in data/ exactly as before — your real
#     account, journal and watchlist are untouched and the app behaves identically.
#   * Hosted (HOSTED=1): each browser sends an X-Workspace id; that user's private
#     files live in data/users/<id>/. Market-wide data (suggestions, screeners,
#     universe, news, heat, regime, cache, docs) stays SHARED in data/.
# --------------------------------------------------------------------------- #
HOSTED = os.environ.get("HOSTED") == "1"
USERS_DIR = DATA / "users"               # per-user workspaces (on the persistent volume)
TEMPLATE_DIR = SEED / "template"         # baked blank-workspace template (always present)
_ctx = threading.local()


def udir():
    """The current request's data dir: a per-user folder when hosted, else data/."""
    return getattr(_ctx, "udir", DATA)


def settings_f():   return udir() / "settings.json"     # per-user account/risk
def trades_f():     return udir() / "trades.json"       # per-user journal
def watchlist_f():  return udir() / "watchlist.json"    # per-user watchlist
def status_f():     return udir() / "status.json"       # per-user approve/reject/take overlay
def uploads_dir():  return udir() / "uploads"           # per-user screenshots


_TEMPLATE_DEFAULTS = {
    "settings.json": {"account_size": None, "risk_pct": 1.0, "max_position_pct": 15},
    "trades.json": [],
    "watchlist.json": [],
    "status.json": {},
}


def _safe_wsid(raw):
    return re.sub(r"[^A-Za-z0-9_-]", "", raw or "")[:64]


def bootstrap_workspace(ud):
    """Create a fresh private workspace from the template (or sane empty defaults)."""
    ud.mkdir(parents=True, exist_ok=True)
    (ud / "uploads").mkdir(exist_ok=True)
    for name, default in _TEMPLATE_DEFAULTS.items():
        tgt = ud / name
        if tgt.exists():
            continue
        src = TEMPLATE_DIR / name
        if src.exists():
            shutil.copyfile(src, tgt)
        else:
            tgt.write_text(json.dumps(default, indent=2), encoding="utf-8")


# Shared, market-wide files that define the universe the scanner runs on. On a fresh
# persistent disk these are copied from the baked-in SEED so the app works immediately.
_SHARED_SEED = ["screeners.json", "universe.json", "themes.json", "sectors.json",
                "market.json", "sector_heat.json", "news.json", "suspicious.json",
                "premarket.json", "suggestions.json"]


def seed_shared():
    """Copy baked shared files onto a freshly-mounted data volume (no-op when DATA == SEED)."""
    if DATA.resolve() == SEED.resolve():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    for name in _SHARED_SEED:
        src, tgt = SEED / name, DATA / name
        if src.exists() and not tgt.exists():
            shutil.copyfile(src, tgt)


def set_workspace(handler):
    """Resolve the per-request workspace from the X-Workspace header (hosted only)."""
    if not HOSTED:
        _ctx.udir = DATA
        return
    wsid = _safe_wsid(handler.headers.get("X-Workspace", "")) or "default"
    ud = USERS_DIR / wsid
    if not ud.exists():
        bootstrap_workspace(ud)
    _ctx.udir = ud


PORT = int(os.environ.get("PORT", "8765"))
SCAN = {"running": False, "done": 0, "total": 0, "current": "",
        "screener_id": None, "finished_at": None}
SECTORH = {"running": False, "done": 0, "total": 0, "current": ""}
REFRESH = {"running": False, "stage": "", "done": 0, "total": 4}
UNIVERSE = {"running": False, "stage": "", "done": 0, "total": 0,
            "built_at": None, "kept": None, "passed": None, "total_syms": None}
_scan_lock = threading.Lock()
_sector_lock = threading.Lock()
_refresh_lock = threading.Lock()
_universe_lock = threading.Lock()


def run_sector_heat():
    themes = read_json(THEMES_F, {})
    SECTORH.update(running=True, done=0, total=sum(len(v) for v in themes.values()) or 1, current="")
    done, rows = 0, []
    for name, tickers in themes.items():
        SECTORH.update(current=name)
        try:
            m = scanner.sector_metrics(tickers)
        except Exception:
            m = None
        done += len(tickers)
        SECTORH.update(done=done)
        if m:
            rows.append({"sector": name, **m})
    rows.sort(key=lambda r: r["score"], reverse=True)
    n = len(rows)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["tier"] = "Hot" if r["rank"] <= max(1, n // 3) else ("Warm" if r["rank"] <= 2 * n // 3 else "Cool")
    write_json(SECTOR_HEAT_F, {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "sectors": rows})
    SECTORH.update(running=False, current="")


def reverse_themes():
    rev = {}
    for name, ts in read_json(THEMES_F, {}).items():
        for t in ts:
            rev[t] = name
    return rev


# --------------------------------------------------------------------------- #
# News (free Google News RSS — no API key)
# --------------------------------------------------------------------------- #
NEWS = {"running": False, "done": 0, "total": 0, "current": ""}
_news_lock = threading.Lock()
SUSPECT = {"running": False, "done": 0, "total": 0, "current": ""}
_suspect_lock = threading.Lock()
PREMKT = {"running": False, "done": 0, "total": 0, "current": ""}
_premkt_lock = threading.Lock()


def fetch_rss(query, n=8):
    url = ("https://news.google.com/rss/search?q=" + quote(query) +
           "&hl=en-US&gl=US&ceid=US:en")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return []
    out = []
    for it in root.findall(".//item")[:n]:
        title = (it.findtext("title") or "").strip()
        src = ""
        if " - " in title:
            title, src = title.rsplit(" - ", 1)
        out.append({"title": title.strip(), "source": src.strip(),
                    "published": it.findtext("pubDate") or "", "link": it.findtext("link") or ""})
    return out


# catalyst keywords — only market-moving headlines pass the filter
GOOD_KW = ["soar", "surge", "jump", "rally", "spike", "wins", "awarded", "contract", "deal",
           "approval", "approve", "beats", "upgrade", "partnership", "acquire", "acquisition",
           "merger", "breakthrough", "buyout", "order", "secures", "lands", "invests", "investment",
           "funding", "stake", "backs", "selected", "chosen", "bet", "billion deal"]
BAD_KW = ["plunge", "plummet", "crash", "tumble", "slump", "lawsuit", "sued", "probe",
          "investigation", "recall", "halt", "ban", "sanction", "downgrade", "warning",
          "cuts guidance", "misses", "bankruptcy", "accident", "explosion", "war", "strike",
          "fraud", "delist", "selloff", "slashes"]
EXTRA_KW = ["tariff", "fda", "executive order", "trump", "pentagon", "government",
            "defense contract", "subpoena", "antitrust", "buyback"]

THEME_KEYWORDS = {
    "Quantum": ["quantum"],
    "Space": ["space", "satellite", "rocket", "spacex", "aerospace", "launch"],
    "Defence & Drones": ["defense", "defence", "drone", "pentagon", "military", "weapon", "missile"],
    "Crypto": ["crypto", "bitcoin", "ethereum", "blockchain", "stablecoin"],
    "Solar": ["solar"],
    "Rare Earth": ["rare earth", "rare-earth", "critical mineral"],
    "Lithium Miners": ["lithium"],
    "Aluminum": ["aluminum", "aluminium"],
    "Gas/Oil": ["oil", "crude", "opec", "natural gas", "drilling"],
    "Marine Shipping": ["shipping", "tanker", "freight", "shipping rates"],
    "China": ["china", "chinese", "beijing"],
    "AI/Data Center": ["data center", "ai chip", "artificial intelligence"],
    "Software/Cloud/Cyber": ["cybersecurity", "cyberattack", "ransomware"],
}


def _classify(title):
    t = (title or "").lower()
    good = any(k in t for k in GOOD_KW)
    bad = any(k in t for k in BAD_KW)
    important = good or bad or any(k in t for k in EXTRA_KW)
    sentiment = "bad" if bad else ("good" if good else "neutral")
    return important, sentiment


def _epoch(it):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(it["published"]).timestamp()
    except Exception:
        return 0


def _recent_important(items, days=7, keep=8):
    cut = time.time() - days * 86400
    out = []
    for it in items:
        ep = _epoch(it)
        if ep < cut:
            continue
        imp, sent = _classify(it["title"])
        if not imp:
            continue
        it["sentiment"] = sent
        it["_ep"] = ep
        out.append(it)
    out.sort(key=lambda x: x["_ep"], reverse=True)   # newest first
    return out[:keep]


def run_news_refresh():
    NEWS.update(running=True, done=0, current="headlines")
    raw_trump = fetch_rss("Trump stocks OR tariffs OR contract when:7d", 25)
    raw_market = fetch_rss("stock soars OR plunges OR contract OR deal when:5d", 25)
    sections = [
        {"name": "🇺🇸 Trump & policy", "items": _recent_important(raw_trump)},
        {"name": "📰 Market catalysts", "items": _recent_important(raw_market)},
    ]
    hot = [s for s in read_json(SECTOR_HEAT_F, {}).get("sectors", [])
           if s.get("tier") == "Hot" or s.get("trend") == "Rising"][:3]
    pool = list(raw_trump) + list(raw_market)
    for s in hot:
        items = fetch_rss(s["sector"] + " stocks when:7d", 15)
        pool += items
        sections.append({"name": "🔥 " + s["sector"], "items": _recent_important(items, keep=6)})

    # sector-level catalysts: match recent important headlines to themes by keyword
    theme_news = {}
    pool_imp = _recent_important(pool, keep=200)
    for th, kws in THEME_KEYWORDS.items():
        for it in pool_imp:                              # pool_imp already newest-first
            tl = it["title"].lower()
            if any(k in tl for k in kws):
                theme_news[th] = {"title": it["title"], "link": it["link"],
                                  "published": it["published"], "sentiment": it["sentiment"]}
                break

    # per-ticker: only keep a ticker that has a recent IMPORTANT headline.
    # News is shared market data, so it draws from the shared scan's top names (not any
    # one user's journal — open positions are private and per-user).
    tickers = [i["ticker"] for i in read_json(SUGGEST_F, {}).get("items", [])[:16]]
    seen, tn = [], {}
    uniq = [t for t in tickers if not (t in seen or seen.append(t))]
    NEWS.update(total=len(uniq))
    for n, tk in enumerate(uniq, 1):
        NEWS.update(current=tk, done=n)
        ri = _recent_important(fetch_rss(tk + " stock when:7d", 6), keep=1)
        if ri:
            it = ri[0]
            tn[tk] = {"title": it["title"], "link": it["link"], "published": it["published"],
                      "sentiment": it["sentiment"], "trump": "trump" in it["title"].lower()}
    # ---- actionable alerts: distill the BIG catalysts into BUY / AVOID directives ----
    themes_map = read_json(THEMES_F, {})
    HARD = ["contract", "deal", "wins", "awarded", "approval", "soar", "surge", "plunge",
            "explosion", "war", "ban", "fda", "acquire", "merger", "recall", "invests",
            "funding", "selected", "darpa", "pentagon", "billion", "stake", "bet"]
    alerts = []
    for th, info in theme_news.items():
        if not any(k in info["title"].lower() for k in HARD):
            continue
        d = info["sentiment"]
        alerts.append({"dir": "buy" if d == "good" else "avoid" if d == "bad" else "watch",
                       "scope": "sector", "title": th, "headline": info["title"],
                       "link": info["link"], "published": info.get("published", ""),
                       "tickers": themes_map.get(th, [])[:6]})
    for tk, info in tn.items():
        if info["sentiment"] in ("good", "bad") and any(k in info["title"].lower() for k in HARD):
            alerts.append({"dir": "buy" if info["sentiment"] == "good" else "avoid",
                           "scope": "stock", "title": tk, "headline": info["title"],
                           "link": info["link"], "published": info.get("published", ""),
                           "tickers": [tk]})
    alerts.sort(key=lambda a: 0 if a["dir"] == "buy" else 1 if a["dir"] == "watch" else 2)
    alerts = alerts[:8]
    write_json(NEWS_F, {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "sections": sections,
                        "ticker_news": tn, "theme_news": theme_news, "alerts": alerts})
    NEWS.update(running=False, current="")


def run_suspicious():
    """Scan the universe for end-of-day / after-hours buy & sell anomalies (insider-style)."""
    tickers = []
    screeners = read_json(SCREENERS_F, [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    if default:
        tickers = default.get("tickers", [])
    SUSPECT.update(running=True, done=0, total=len(tickers) or 1, current="")

    def prog(done, total, t):
        SUSPECT.update(done=done, total=total, current=t)
    try:
        out = scanner.scan_suspicious(tickers, prog)
        write_json(SUSPICIOUS_F, out)
    except Exception as e:
        SUSPECT.update(current="error: " + str(e))
    SUSPECT.update(running=False, current="")


def run_premarket():
    """Scan the universe for notable pre-market gaps (run during pre-market hours)."""
    tickers = []
    screeners = read_json(SCREENERS_F, [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    if default:
        tickers = default.get("tickers", [])
    PREMKT.update(running=True, done=0, total=len(tickers) or 1, current="")

    def prog(done, total, t):
        PREMKT.update(done=done, total=total, current=t)
    try:
        out = scanner.scan_premarket(tickers, prog)
        write_json(PREMARKET_F, out)
    except Exception as e:
        PREMKT.update(current="error: " + str(e))
    PREMKT.update(running=False, current="")


def run_market_regime():
    """Classify SPX / QQQ / IWM into a blended market posture; store for the dashboard + grade."""
    try:
        reg = scanner.market_regime()
        if reg:
            write_json(MARKET_F, reg)
    except Exception:
        pass


def run_build_universe():
    """Assemble the full tradeable US universe, coarse-filter to the Market Leaders criteria,
    and write the ticker list into the default screener so the scan runs on the real universe."""
    settings = read_json(SETTINGS_F, {})
    UNIVERSE.update(running=True, stage="Fetching US symbols…", done=0, total=0)

    def prog(stage, total, done):
        UNIVERSE.update(stage="Reading market caps…" if stage == "quotes" else "Fetching US symbols…",
                        done=done, total=total)
    try:
        u = universe.build_universe(
            exchanges=settings.get("universe_exchanges", "all"),
            size=settings.get("universe_size", 800),
            min_price=settings.get("universe_min_price", 10),
            min_mktcap_m=settings.get("universe_min_mktcap_m", 300),
            min_dollar_vol_m=settings.get("universe_min_dollar_vol_m", 10),
            progress=prog,
        )
        if u.get("tickers"):
            write_json(UNIVERSE_F, u)
            screeners = read_json(SCREENERS_F, [])
            tgt = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
            if tgt is None:
                tgt = {"id": "market-leaders", "name": "Market Leaders", "is_default": True}
                screeners.append(tgt)
            tgt["tickers"] = u["tickers"]
            tgt["auto"] = True
            write_json(SCREENERS_F, screeners)
        UNIVERSE.update(running=False, stage="Done", built_at=u.get("built_at"),
                        kept=u.get("kept"), passed=u.get("passed_filter"),
                        total_syms=u.get("universe_total"))
    except Exception as e:
        UNIVERSE.update(running=False, stage="error: " + str(e))


def run_refresh_all():
    """The 'new day' button: refresh the EXISTING universe — regime, rescan setups/ratings,
    sector heat, news. Does NOT rebuild the universe (that's the manual 'Rebuild universe'
    button); New day just re-reads prices and recomputes for the names we already track."""
    REFRESH.update(running=True, stage="Reading the market (SPX/QQQ/IWM)…", done=0, total=4)
    try:
        run_market_regime()
        REFRESH.update(stage="Updating setups & ratings…", done=1)
        screeners = read_json(SCREENERS_F, [])
        default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
        if default:
            run_scan(default["id"])
        REFRESH.update(stage="Computing sector heat…", done=2)
        run_sector_heat()
        REFRESH.update(stage="Pulling fresh news…", done=3)
        run_news_refresh()
    except Exception as e:
        REFRESH.update(stage="error: " + str(e))
    REFRESH.update(running=False, stage="Done", done=4)


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2), encoding="utf-8")


def now_date():
    return time.strftime("%Y-%m-%d")


def days_until(date_str):
    """Whole calendar days from today to a 'YYYY-MM-DD' string (negative if past), or None."""
    if not date_str:
        return None
    try:
        from datetime import date
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Position sizing (recomputed live from current settings)
# --------------------------------------------------------------------------- #
def apply_sizing(item, settings):
    """Shares for `risk_pct` risk, capped by max-position size and buying power."""
    risk_ps = item.get("risk_ps") or 0
    entry = item.get("entry") or 0
    acct = settings.get("account_size")
    risk_pct = settings.get("risk_pct", 1.0)
    maxpos_pct = settings.get("max_position_pct", 15)
    item["capped"] = False
    item["cap_reason"] = None
    if acct and risk_ps > 0 and entry > 0:
        risk_shares = int((acct * risk_pct / 100) // risk_ps)
        maxpos_shares = int((acct * maxpos_pct / 100) // entry)
        afford = int(acct // entry)
        shares = max(0, min(risk_shares, maxpos_shares, afford))
        item["shares"] = shares
        if shares < risk_shares:
            item["capped"] = True
            item["cap_reason"] = "max position size" if maxpos_shares <= afford else "buying power"
        item["dollar_risk"] = round(shares * risk_ps, 2)
        item["risk_pct_actual"] = round(shares * risk_ps / acct * 100, 2)
        item["cost"] = round(shares * entry, 2)
        item["pct_acct"] = round(shares * entry / acct * 100, 1)
    else:
        item["shares"] = item["dollar_risk"] = item["risk_pct_actual"] = None
        item["cost"] = item["pct_acct"] = None
    item["shares_per_10k"] = int((10000 * risk_pct / 100) // risk_ps) if risk_ps > 0 else 0
    return item


def _mean(xs, default=0.0):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else default


def position_coach(t, bars, settings, news_map):
    """Suggest the next action on an OPEN position from profit (R), extension above the 9/21
    EMA, an upcoming earnings print, and news. Mirrors my-rules: the default exit is a daily
    close under the 9 EMA; trim into strength / before binary events; raise the stop once +1R."""
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    if len(c) < 50:
        return None
    last = c[-1]
    e9, e21, s50 = scanner._ema(c, 9), scanner._ema(c, 21), scanner._sma(c, 50)
    adr = _mean([(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0], 1.0) or 1.0
    entry = t.get("entry") or t.get("planned_entry")
    stop = t.get("stop")
    risk = (entry - stop) if (entry and stop and entry > stop) else None
    r_mult = ((last - entry) / risk) if (risk and entry) else None
    ext9 = (last / e9 - 1) * 100
    ext9_adr = ext9 / adr
    under_9 = last < e9
    # earnings + news context
    e = None
    try:
        e = scanner.get_earnings(t["ticker"])
    except Exception:
        e = None
    edays = days_until(e["date"]) if e else None
    earn_soon = edays is not None and 0 <= edays <= 7
    nm = news_map.get(t.get("ticker"))
    bad_news = bool(nm and nm.get("sentiment") == "bad")

    reasons = []
    rtxt = (f"+{r_mult:.1f}R" if (r_mult is not None and r_mult >= 0)
            else (f"{r_mult:.1f}R" if r_mult is not None else "—"))
    # priority ladder: stop → 9-EMA exit → earnings → trim extended → breakeven → news → hold
    if stop and last < stop:
        action, tone = "EXIT", "danger"
        reasons.append(f"price ${round(last,2)} is below your stop ${stop} — you should already be out")
    elif under_9:
        action, tone = "EXIT", "danger"
        reasons.append(f"closed under the 9 EMA (${round(e9,2)}) — your default trailing exit")
    elif earn_soon and r_mult is not None and r_mult >= 0.5:
        action, tone = "TRIM", "warn"
        reasons.append(f"earnings in {edays}d — trim to lock {rtxt} before the binary print")
    elif earn_soon:
        action, tone = "WATCH", "warn"
        reasons.append(f"earnings in {edays}d with no real cushion ({rtxt}) — consider closing to skip the gamble")
    elif r_mult is not None and r_mult >= 3 and ext9_adr > 2.2:
        action, tone = "TRIM", "warn"
        reasons.append(f"{rtxt} and {ext9_adr:.1f}× ADR above the 9 EMA — trim into strength, trail the 10/20-MA")
    elif r_mult is not None and r_mult >= 1 and stop and entry and stop < entry:
        action, tone = "RAISE STOP", "good"
        reasons.append(f"{rtxt} locked-in zone — raise the stop to breakeven (${entry}) so the trade can't turn red")
    elif bad_news:
        action, tone = "WATCH", "warn"
        reasons.append("a negative headline is out — watch the 9-EMA close")
    else:
        action, tone = "HOLD", "good"
        reasons.append(f"trend intact above the 9 EMA ({rtxt}) — hold; exit on a daily close under it")
    # optional add-on note (never the primary action; respects total-risk rules)
    if action in ("HOLD", "RAISE STOP") and ext9_adr < 1.0 and last > e21 > s50 \
            and r_mult is not None and 0 <= r_mult < 2 and not earn_soon:
        reasons.append("near rising support & not extended — could add on a push to new highs (optional, keep total risk within rules)")
    if e and edays is not None and not earn_soon and 0 <= edays <= 21:
        reasons.append(f"earnings {e['date']} ({edays}d out){' · est.' if e.get('estimate') else ''}")

    return {"action": action, "tone": tone, "reasons": reasons,
            "r_mult": round(r_mult, 2) if r_mult is not None else None,
            "ext9": round(ext9, 1), "ext9_adr": round(ext9_adr, 1),
            "ext21": round((last / e21 - 1) * 100, 1), "under_9ema": under_9,
            "earnings_days": edays, "earnings_date": e["date"] if e else None,
            "earnings_estimate": bool(e.get("estimate")) if e else False,
            "last": round(last, 2)}


def enrich_trades(trades):
    """Add current price + P&L to each trade, and a coaching action for open positions."""
    settings = read_json(settings_f(), {})
    news_map = read_json(NEWS_F, {}).get("ticker_news", {})
    for t in trades:
        e, sh = t.get("entry"), t.get("shares")
        t["last"] = t["pnl"] = t["pnl_pct"] = None
        t["coach"] = None
        if t.get("status") == "open" and t.get("ticker"):
            bars = scanner.get_bars(t["ticker"])
            last = bars[-1]["close"] if bars else None
            t["last"] = last
            if e and sh and last:
                t["pnl"] = round((last - e) * sh, 2)
                t["pnl_pct"] = round((last / e - 1) * 100, 2)
            else:
                t["pnl"] = t["pnl_pct"] = None
            if bars:
                try:
                    t["coach"] = position_coach(t, bars, settings, news_map)
                except Exception:
                    t["coach"] = None
        elif t.get("status") == "closed":
            x = t.get("exit")
            if e and sh and x:
                t["pnl"] = round((x - e) * sh, 2)
                t["pnl_pct"] = round((x / e - 1) * 100, 2)
            else:
                t["pnl"] = t["pnl_pct"] = None
    return trades


def attach_sectors(items):
    smap = read_json(SECTORS_F, {})
    for it in items:
        it["sector"] = smap.get(it["ticker"], "Other")


def compute_hot_sectors(items):
    """Hot = sectors whose members lead on 1-month performance (top third)."""
    from collections import defaultdict
    g = defaultdict(list)
    for it in items:
        g[it.get("sector", "Other")].append(it.get("p1m", 0))
    means = {s: sum(v) / len(v) for s, v in g.items() if len(v) >= 2 and s != "Other"}
    if not means:
        return []
    ranked = sorted(means.items(), key=lambda x: x[1], reverse=True)
    cut = max(1, len(ranked) // 3)
    return [s for s, _ in ranked[:cut]]


def grade_suggestions(items, settings):
    """Enrich scan items with sizing, theme/heat, news, earnings proximity, group leadership,
    and the composite 0-100 rating + letter grade. Mutates items in place and returns them
    sorted best-first. Canonical rubric: strategy/scoring.md (keep weights in sync)."""
    rev = reverse_themes()
    heat = {h["sector"]: h for h in read_json(SECTOR_HEAT_F, {}).get("sectors", [])}
    news_data = read_json(NEWS_F, {})
    news_map = news_data.get("ticker_news", {})
    theme_news = news_data.get("theme_news", {})
    for it in items:
        apply_sizing(it, settings)
        it["worth_waiting"] = it.get("setup_type") in ("Deep Pullback", "Consolidation")
        # earnings proximity — a binary print within ~1 week is a reason to skip a fresh entry
        ed = days_until(it.get("earnings_date"))
        it["earnings_days"] = ed
        it["earnings_soon"] = ed is not None and 0 <= ed <= 7
        it["earnings_near"] = ed is not None and 8 <= ed <= 14
        th = rev.get(it["ticker"])
        it["theme"] = th
        hr = heat.get(th)
        if hr:
            it["theme_trend"] = hr.get("trend")
            it["theme_streak"] = hr.get("streak")
            it["theme_tier"] = hr.get("tier")
            it["theme_perf_1mo"] = hr.get("perf_1mo")
            it["theme_hot"] = hr.get("tier") == "Hot" or hr.get("trend") == "Rising"
        else:
            it["theme_trend"] = it["theme_tier"] = None
            it["theme_hot"] = False
        nm = news_map.get(it["ticker"])
        tn = theme_news.get(th)
        if nm:
            it["news_headline"] = nm["title"]; it["news_link"] = nm["link"]
            it["news_dir"] = nm.get("sentiment"); it["news_trump"] = bool(nm.get("trump")); it["news_scope"] = "stock"
        elif tn:
            it["news_headline"] = tn["title"]; it["news_link"] = tn["link"]
            it["news_dir"] = tn.get("sentiment"); it["news_trump"] = "trump" in tn["title"].lower(); it["news_scope"] = "sector"
        else:
            it["news_headline"] = it["news_link"] = it["news_dir"] = it["news_scope"] = None
            it["news_trump"] = False
        it["news_flag"] = bool(nm or tn or it.get("recent_gap", 0) >= 12)
    # ---- leader-in-group: within each theme, rank names by relative strength so the
    # strongest stock of a hot group gets a 🥇 mark (like the Sector Heat awards) ----
    groups = {}
    for it in items:
        th = it.get("theme")
        if th:
            groups.setdefault(th, []).append(it)
    for members in groups.values():
        # leadership = relative strength + liquidity (an illiquid spike isn't a leader)
        members.sort(key=lambda x: (0.6 * x.get("rs_score", 0) + 0.4 * x.get("liq_score", 0),
                                    x.get("score", 0)), reverse=True)
        n = len(members)
        for rank, it in enumerate(members, 1):
            it["group_size"] = n
            it["group_rank"] = rank
            it["group_leader"] = rank == 1 and n >= 2

    # ---- composite grade: rate every name best->worst using ALL the data ----
    bysetup = compute_stats().get("by_setup", {})
    posture = read_json(MARKET_F, {}).get("posture", 55)               # 0-100 market regime
    pullback_setups = ("Pullback", "Pullback @ AVWAP",
                       "AVWAP reclaim (ATH)", "AVWAP reclaim (earnings)")

    def _rating(it):
        setup = max(0, min(100, (it.get("score", 0) - 4) / 16 * 100))   # technical setup quality
        rs = it.get("rs_score", 50)                                     # relative strength
        # market regime: breakouts/EPs are demoted harder than pullbacks in weak tape
        regime = posture if it.get("setup_type") in pullback_setups else (
            posture if posture >= 55 else posture * 0.6)
        entry_loc = it.get("entry_quality", 60)                         # don't-chase / tight-stop
        liq = it.get("liq_score", 50)                                    # liquidity -> institutional interest
        tr, tier = it.get("theme_trend"), it.get("theme_tier")
        # backtest: a RISING sector beat a backward-looking "Hot" tier (Hot is often already extended),
        # so Rising now outranks Hot.
        sector = 100 if tr == "Rising" else (85 if tier == "Hot" else 25 if tr == "Slowing"
                                             else 12 if tr == "Falling" else 55)
        # actionable now still helps, but only a little — the backtest showed buying in-zone NOW
        # underperformed waiting for the pullback to the line, so don't over-reward chasing.
        timing = 75 if it.get("buyable_now") else 55
        nd = it.get("news_dir")
        news = 100 if nd == "good" else (8 if nd == "bad" else (75 if it.get("news_flag") else 55))
        r = (0.28 * setup + 0.14 * rs + 0.14 * regime + 0.14 * entry_loc
             + 0.08 * liq + 0.10 * sector + 0.06 * timing + 0.06 * news)
        hist = bysetup.get(it.get("setup_type"))                        # learns from realized results
        if hist and hist.get("n", 0) >= 5:
            r += max(-8, min(8, hist.get("avg_r", 0) * 3))
        # earnings overhang: a print inside a week is a hard demote (don't open binary risk);
        # 8-14 days out is a lighter caution.
        if it.get("earnings_soon"):
            r -= 18
        elif it.get("earnings_near"):
            r -= 6
        # distribution / climax-reversal day: cap the grade regardless of how strong RS/sector
        # look — a heavy-volume rejection off the highs is a "wait", never a "get in now". (RS and
        # a hot sector are exactly what make a blow-off look buyable, so the cap overrides them.)
        if it.get("distribution_today"):
            r = min(r, 62)                                  # cap at C — don't buy the reversal
        # REGIME GATE (backtest's biggest lesson): A/A+ taken with blended posture < ~65 LOST money
        # (the 55-69 "constructive but not strong" band was −0.5R). Below 65 we cap at B (still
        # visible, not "get in now"); a weak/correcting tape (<50) caps at C.
        if posture < 50:
            r = min(r, 52)
        elif posture < 65:
            r = min(r, 72)
        return round(max(0, min(99, r)))

    def _grade(r):
        return "A+" if r >= 82 else "A" if r >= 73 else "B" if r >= 63 else "C" if r >= 52 else "D"

    for it in items:
        it["rating"] = _rating(it)
        it["grade"] = _grade(it["rating"])
    return sorted(items, key=lambda x: x.get("rating", 0), reverse=True)


# --------------------------------------------------------------------------- #
# Markdown regeneration (so the chat-side coach sees current data)
# --------------------------------------------------------------------------- #
def regen_watchlist():
    if HOSTED:   # shared .md is only for the local Claude coach; skip in multi-user mode
        return
    rows = read_json(watchlist_f(), [])
    lines = ["# Watchlist", "",
             "Auto-generated from the Data Center. Edit in the GUI or here; the GUI is the source of truth.",
             "",
             "| Ticker | Why | Level | Setup | Catalyst |",
             "|--------|-----|-------|-------|----------|"]
    for r in rows:
        lines.append(f"| {r.get('ticker','')} | {r.get('why','')} | {r.get('level','')} "
                     f"| {r.get('setup','')} | {r.get('catalyst','')} |")
    (BASE / "watchlist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def regen_trades_md():
    if HOSTED:   # shared .md is only for the local Claude coach; skip in multi-user mode
        return
    trades = read_json(trades_f(), [])
    lines = ["# Trade journal", "",
             "Auto-generated from the Data Center (newest first).", ""]
    for t in sorted(trades, key=lambda x: x.get("taken_at", ""), reverse=True):
        r = t.get("result_r")
        res = f"{r:+.2f}R" if isinstance(r, (int, float)) else "-"
        lines += [
            f"### {t.get('taken_at','')} - {t.get('ticker','')} ({t.get('setup_type','')}) [{t.get('status','')}]",
            f"- Plan: entry {t.get('planned_entry') or t.get('entry')} / stop {t.get('stop')} / target {t.get('target')}",
            f"- Filled: entry {t.get('entry')} / shares {t.get('shares')}",
            f"- Result: {res}  exit {t.get('exit')}",
            f"- Rules followed: {t.get('rules_followed')}",
            f"- Notes: {t.get('notes','')}",
        ]
        if t.get("lesson"):
            lines.append(f"- Lesson: {t['lesson']}")
        lines.append("")
    (BASE / "journal" / "trades.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_rules_account(acct):
    if HOSTED:   # my-rules.md is shared; per-user account size must not rewrite it
        return
    p = DOCS["my-rules"]
    try:
        txt = p.read_text(encoding="utf-8")
        val = f"${acct:,.0f}" if acct else "_not set_"
        txt = re.sub(r"(- \*\*Account size:\*\*).*", rf"\1 {val}", txt, count=1)
        p.write_text(txt, encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Background scan
# --------------------------------------------------------------------------- #
def run_scan(screener_id):
    global SCAN
    screeners = read_json(SCREENERS_F, [])
    sc = next((s for s in screeners if s["id"] == screener_id), None)
    if not sc:
        SCAN.update(running=False)
        return
    tickers = sc["tickers"]
    settings = read_json(SETTINGS_F, {})
    SCAN.update(running=True, done=0, total=len(tickers), current="",
                screener_id=screener_id, finished_at=None)

    def prog(done, total, t):
        SCAN.update(done=done, total=total, current=t)

    out = scanner.scan(tickers, settings, prog)
    prev = {i["ticker"]: i for i in read_json(SUGGEST_F, {}).get("items", [])}
    items = []
    for r in out["results"]:
        old = prev.get(r["ticker"], {})
        r["status"] = old.get("status", "pending")
        r["catalyst"] = old.get("catalyst", "")
        items.append(r)
    attach_sectors(items)
    hot = compute_hot_sectors(items)
    for it in items:
        it["sector_hot"] = it["sector"] in hot
        if it["sector_hot"]:
            it["score"] = round(it["score"] + 1.5, 1)
    items.sort(key=lambda r: r["score"], reverse=True)
    # earnings dates for the most actionable names (top of the list). Cached daily, so
    # repeat scans are cheap; we cap the count to keep the scan fast and Yahoo-friendly.
    SCAN.update(current="earnings dates…")
    for it in items[:70]:
        try:
            e = scanner.get_earnings(it["ticker"])
        except Exception:
            e = None
        if e:
            it["earnings_date"] = e["date"]
            it["earnings_estimate"] = e.get("estimate", False)
    write_json(SUGGEST_F, {"scanned_at": out["scanned_at"], "screener_id": screener_id,
                           "screener_name": sc["name"], "failed": out["failed"],
                           "hot_sectors": hot, "items": items})
    SCAN.update(running=False, finished_at=out["scanned_at"], current="")


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def compute_stats():
    trades = read_json(trades_f(), [])
    closed = [t for t in trades if t.get("status") == "closed" and isinstance(t.get("result_r"), (int, float))]
    out = {"closed": len(closed), "open": len([t for t in trades if t.get("status") == "open"]),
           "win_rate": None, "avg_r": None, "expectancy": None, "by_setup": {}}
    if closed:
        rs = [t["result_r"] for t in closed]
        wins = [x for x in rs if x > 0]
        out["win_rate"] = round(100 * len(wins) / len(rs), 1)
        out["avg_r"] = round(sum(rs) / len(rs), 2)
        out["expectancy"] = out["avg_r"]
        for stp in set(t.get("setup_type", "?") for t in closed):
            grp = [t["result_r"] for t in closed if t.get("setup_type") == stp]
            w = [x for x in grp if x > 0]
            out["by_setup"][stp] = {"n": len(grp),
                                    "win_rate": round(100 * len(w) / len(grp), 1),
                                    "avg_r": round(sum(grp) / len(grp), 2)}
    return out


def _top_lessons(n=3):
    """First few real lessons from lessons.md (skips the empty placeholder)."""
    try:
        txt = DOCS["lessons"].read_text(encoding="utf-8")
    except Exception:
        return []
    out = []
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("- ") and "empty for now" not in line.lower():
            out.append(re.sub(r"\*\*", "", line[2:]).strip())
    return out[:n]


def _sug_compact(s):
    return {"ticker": s["ticker"], "grade": s.get("grade"), "setup_type": s.get("setup_type"),
            "theme": s.get("theme"), "entry": s.get("entry"), "entry_type": s.get("entry_type"),
            "zone_bottom": s.get("zone_bottom"), "zone_top": s.get("zone_top"),
            "close": s.get("close"), "earnings_days": s.get("earnings_days"),
            "rating": s.get("rating"), "why": s.get("why")}


# --------------------------------------------------------------------------- #
# Daily Gameplan — one synthesized plan from positions + cash + regime + setups + news
# --------------------------------------------------------------------------- #
def compute_gameplan():
    settings = read_json(settings_f(), {})
    acct = settings.get("account_size")
    trades = enrich_trades(read_json(trades_f(), []))
    open_pos = [t for t in trades if t.get("status") == "open"]
    held = {t.get("ticker") for t in open_pos}

    market = read_json(MARKET_F, {})
    posture = market.get("posture", 55)
    label = market.get("label", "")
    indexes = market.get("indexes", [])
    stretched = [i["name"] for i in indexes if i.get("stretched_50")]

    sug_items = read_json(SUGGEST_F, {}).get("items", [])
    graded = grade_suggestions(sug_items, settings) if sug_items else []
    good_grades = ("A+", "A")
    buy_now = [_sug_compact(s) for s in graded
               if s.get("buyable_now") and s.get("grade") in good_grades
               and not s.get("earnings_soon") and s["ticker"] not in held][:5]
    watch = [_sug_compact(s) for s in graded
             if not s.get("buyable_now") and s.get("grade") in good_grades
             and not s.get("earnings_soon") and s["ticker"] not in held][:5]
    avoid = [{"ticker": s["ticker"], "reason": f"earnings in {s.get('earnings_days')}d — skip new entries"}
             for s in graded[:40] if s.get("earnings_soon")][:5]

    # exposure / free cash
    cost = sum((t.get("entry") or 0) * (t.get("shares") or 0) for t in open_pos)
    open_risk = 0.0
    for t in open_pos:
        e, stp, sh = t.get("entry"), t.get("stop"), t.get("shares")
        if e and stp and sh and stp < e:
            open_risk += (e - stp) * sh
    exposure = {"account": acct, "positions": len(open_pos),
                "invested": round(cost, 2),
                "invested_pct": round(cost / acct * 100, 1) if acct else None,
                "free_cash": round(acct - cost, 2) if acct else None,
                "open_risk": round(open_risk, 2),
                "open_risk_pct": round(open_risk / acct * 100, 2) if acct else None}

    # stance from the tape
    if posture >= 70:
        stance = "Press — healthy uptrend, full size on A+ setups"
    elif posture >= 55:
        stance = "Selective — constructive tape, pick your spots"
    elif posture >= 45:
        stance = "Cautious — mixed tape; half size or wait for clean buys"
    else:
        stance = "Defense — weak tape, protect capital, mostly cash"
    if stretched:
        stance += f" · {', '.join(stretched)} extended above the 50-MA — don't chase, let setups pull in"

    manage = []
    for t in open_pos:
        co = t.get("coach") or {}
        manage.append({"ticker": t["ticker"], "action": co.get("action", "HOLD"),
                       "tone": co.get("tone", "good"),
                       "reason": (co.get("reasons") or [""])[0], "pnl_pct": t.get("pnl_pct")})
    todo = [m for m in manage if m["action"] in ("EXIT", "TRIM", "RAISE STOP")]

    # bottom line — honest, "do nothing" is allowed
    if not open_pos and not buy_now:
        if watch:
            bottom = ("No positions and nothing in a buy zone yet — the plan is patience. "
                      "Watching: " + ", ".join(s["ticker"] for s in watch) + ".")
        else:
            bottom = "No positions, no buyable A/A+ setups, tape " + (label or "unclear") + " — doing nothing is the right move today."
    elif todo:
        acts = ", ".join(f"{m['ticker']} ({m['action']})" for m in todo)
        tail = (" Then consider " + ", ".join(s["ticker"] for s in buy_now) + "."
                if buy_now else " No new entries needed.")
        bottom = f"Handle your positions first: {acts}." + tail
    elif buy_now:
        bottom = "Positions are fine to hold. Best buyable now: " + \
                 ", ".join(f"{s['ticker']} ({s['grade']})" for s in buy_now) + "."
    else:
        bottom = "Hold what you've got; nothing new is in its buy zone. Be patient."

    return {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "date": now_date(),
            "posture": posture, "label": label, "stance": stance, "stretched": stretched,
            "exposure": exposure, "manage": manage, "buy_now": buy_now, "watch": watch,
            "avoid": avoid, "alerts": read_json(NEWS_F, {}).get("alerts", [])[:4],
            "lessons": _top_lessons(3), "bottom_line": bottom}


# --------------------------------------------------------------------------- #
# Prediction — a probabilistic forward read from all the data we have (NOT advice)
# --------------------------------------------------------------------------- #
def compute_prediction():
    market = read_json(MARKET_F, {})
    posture = market.get("posture", 55)
    label = market.get("label", "")
    indexes = market.get("indexes", [])
    stretched = [i["name"] for i in indexes if i.get("stretched_50")]

    heat = read_json(SECTOR_HEAT_F, {}).get("sectors", [])
    rising = [s["sector"] for s in heat if s.get("trend") == "Rising"]
    slowing = [s["sector"] for s in heat if s.get("trend") == "Slowing"]
    falling = [s["sector"] for s in heat if s.get("trend") == "Falling"]
    breadth = round(_mean([s.get("breadth", 50) for s in heat])) if heat else None

    news = read_json(NEWS_F, {})
    alerts = news.get("alerts", [])
    a_good = len([a for a in alerts if a.get("dir") == "buy"])
    a_bad = len([a for a in alerts if a.get("dir") == "avoid"])
    tn = news.get("ticker_news", {})
    t_good = len([1 for v in tn.values() if v.get("sentiment") == "good"])
    t_bad = len([1 for v in tn.values() if v.get("sentiment") == "bad"])

    susp = read_json(SUSPICIOUS_F, {})
    buys, sells = len(susp.get("buying", [])), len(susp.get("selling", []))
    pm = read_json(PREMARKET_F, {}).get("movers", [])
    pm_up = len([m for m in pm if m.get("gap", 0) >= 0])
    pm_dn = len(pm) - pm_up

    drivers = []
    score = (posture - 55) / 10.0
    drivers.append({"text": f"Market regime: {label or 'n/a'} (posture {posture}/100)",
                    "dir": "pos" if posture >= 60 else "neg" if posture < 45 else "neutral"})
    if breadth is not None:
        score += (breadth - 50) / 15.0
        drivers.append({"text": f"Sector breadth {breadth}% of names above their 20-day MA",
                        "dir": "pos" if breadth >= 55 else "neg" if breadth < 40 else "neutral"})
    score += (len(rising) - len(slowing) - 2 * len(falling)) * 0.12
    if rising:
        drivers.append({"text": f"Money rotating INTO: {', '.join(rising[:5])}", "dir": "pos"})
    if slowing or falling:
        drivers.append({"text": f"Cooling / rolling over: {', '.join((falling + slowing)[:5])}",
                        "dir": "neg"})
    score += (a_good - a_bad) * 0.4 + (t_good - t_bad) * 0.12
    if alerts or tn:
        drivers.append({"text": f"News tone: {a_good + t_good} positive vs {a_bad + t_bad} negative catalysts",
                        "dir": "pos" if (a_good + t_good) > (a_bad + t_bad) else "neg" if (a_bad + t_bad) > (a_good + t_good) else "neutral"})
    if buys or sells:
        score += (buys - sells) * 0.04
        drivers.append({"text": f"End-of-day footprint: {buys} unusual-buying vs {sells} unusual-selling names",
                        "dir": "pos" if buys > sells else "neg" if sells > buys else "neutral"})
    if pm:
        drivers.append({"text": f"Pre-market: {pm_up} gapping up vs {pm_dn} down",
                        "dir": "pos" if pm_up > pm_dn else "neg" if pm_dn > pm_up else "neutral"})
    if len(stretched) >= 2:
        score -= 1.5
        drivers.append({"text": f"{', '.join(stretched)} stretched above the 50-MA — pullback/digestion risk",
                        "dir": "neg"})

    if score >= 2:
        lean = "Bullish"
    elif score >= 0.7:
        lean = "Constructive"
    elif score > -0.7:
        lean = "Neutral / chop"
    elif score > -2:
        lean = "Cautious"
    else:
        lean = "Risk-off"
    confidence = "moderate" if abs(score) >= 2 and len(drivers) >= 4 else "low"

    parts = [f"The tape reads <b>{label or 'unclear'}</b> (posture {posture}/100)."]
    if stretched:
        parts.append(f"{', '.join(stretched)} sit stretched above the 50-MA, so indices are vulnerable to a near-term pullback or sideways digestion rather than a clean leg up.")
    elif posture >= 65:
        parts.append("Trend and breadth are healthy — dips are likely buyable while leaders hold their lines.")
    if rising:
        parts.append(f"Leadership is rotating into {', '.join(rising[:4])}; that's where fresh setups should cluster.")
    if falling or slowing:
        parts.append(f"Avoid fading strength into the cooling groups ({', '.join((falling + slowing)[:4])}).")
    if (a_bad + t_bad) > (a_good + t_good):
        parts.append("Headline tone skews negative — keep size honest.")
    outlook = " ".join(parts)

    return {"computed_at": time.strftime("%Y-%m-%d %H:%M"),
            "lean": lean, "confidence": confidence, "score": round(score, 2),
            "outlook": outlook, "drivers": drivers,
            "rising": rising[:8], "slowing": slowing[:8], "falling": falling[:8],
            "posture": posture, "label": label, "breadth": breadth,
            "note": "Probabilistic read from the data on hand — not a prediction you should trade blindly. The market does what it wants."}


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- low-level senders ----
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, relpath):
        # serve files from web/ and data/uploads/
        relpath = unquote(relpath).lstrip("/")
        if relpath == "" or relpath == "index.html":
            target = WEB / "index.html"
        elif relpath.startswith("uploads/"):           # hosted: per-user screenshots
            target = uploads_dir() / relpath[len("uploads/"):]
        elif relpath.startswith("data/uploads/"):       # local: shared uploads dir
            target = BASE / relpath
        else:
            target = WEB / relpath
        target = target.resolve()
        # keep per-user uploads sandboxed to their own folder
        if relpath.startswith("uploads/") and not str(target).startswith(str(uploads_dir().resolve())):
            self._json({"error": "not found"}, 404)
            return
        if not target.exists() or not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._bytes(target.read_bytes(), ctype)

    # ---- routing ----
    def do_GET(self):
        set_workspace(self)
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._static(path)
            return
        parts = [p for p in path.split("/") if p]            # e.g. ['api','chart','SNDK']
        route = parts[1] if len(parts) > 1 else ""
        settings = read_json(settings_f(), {})

        if route == "settings":
            self._json(settings)
        elif route == "env":
            # lets the frontend hide write-heavy pages (journal/strategy/watchlist) on the hosted
            # free service, where per-user data doesn't persist across the dyno sleeping.
            self._json({"hosted": HOSTED})
        elif route == "screeners":
            self._json(read_json(SCREENERS_F, []))
        elif route == "suggestions":
            s = read_json(SUGGEST_F, {"items": []})
            # Suggestions are a SHARED market scan; approve/reject/take is a per-user
            # overlay so each friend keeps their own marks without touching others'.
            ov = read_json(status_f(), {})
            for it in s.get("items", []):
                o = ov.get(it["ticker"])
                if o:
                    it["status"] = o.get("status", it.get("status", "pending"))
                    if o.get("reject_reason") is not None:
                        it["reject_reason"] = o["reject_reason"]
                    if o.get("catalyst") is not None:
                        it["catalyst"] = o["catalyst"]
            s["items"] = grade_suggestions(s.get("items", []), settings)
            self._json(s)
        elif route == "market":
            self._json(read_json(MARKET_F, {}))
        elif route == "universe":
            u = read_json(UNIVERSE_F, {})
            u["status"] = UNIVERSE
            self._json(u)
        elif route == "suspicious":
            s = read_json(SUSPICIOUS_F, {"buying": [], "selling": []})
            s["status"] = SUSPECT
            self._json(s)
        elif route == "premarket":
            pm = read_json(PREMARKET_F, {"movers": []})
            rev = reverse_themes()
            heat = {h["sector"]: h for h in read_json(SECTOR_HEAT_F, {}).get("sectors", [])}
            news_map = read_json(NEWS_F, {}).get("ticker_news", {})
            sug = {i["ticker"]: i for i in read_json(SUGGEST_F, {}).get("items", [])}
            for m in pm.get("movers", []):
                th = rev.get(m["ticker"])
                m["theme"] = th
                hr = heat.get(th)
                if hr:
                    m["theme_trend"] = hr.get("trend")
                    m["theme_tier"] = hr.get("tier")
                    m["theme_hot"] = hr.get("tier") == "Hot" or hr.get("trend") == "Rising"
                nm = news_map.get(m["ticker"])
                if nm:
                    m["news_headline"] = nm["title"]
                    m["news_link"] = nm["link"]
                    m["news_dir"] = nm.get("sentiment")
                    m["news_trump"] = bool(nm.get("trump"))
                si = sug.get(m["ticker"])
                if si:
                    m["setup_type"] = si.get("setup_type")
                    m["rs_pct"] = si.get("rs_pct")
                    m["worth_waiting"] = si.get("setup_type") in ("Deep Pullback", "Consolidation")
            pm["status"] = PREMKT
            self._json(pm)
        elif route == "watchlist":
            self._json(read_json(watchlist_f(), []))
        elif route == "trades":
            self._json(enrich_trades(read_json(trades_f(), [])))
        elif route == "stats":
            self._json(compute_stats())
        elif route == "scan" and len(parts) > 2 and parts[2] == "status":
            self._json(SCAN)
        elif route == "chart" and len(parts) > 2:
            t = parts[2].upper()
            bars = scanner.get_bars(t)
            channel = scanner.regression_channel(bars) if bars else None
            earn = None
            try:
                e = scanner.get_earnings(t)
                if e:
                    earn = {**e, "days": days_until(e["date"])}
            except Exception:
                earn = None
            self._json({"ticker": t, "bars": bars or [], "channel": channel, "earnings": earn})
        elif route == "gameplan":
            self._json(compute_gameplan())
        elif route == "prediction":
            self._json(compute_prediction())
        elif route == "analyze" and len(parts) > 2:
            t = parts[2].upper()
            bars = scanner.get_bars(t)
            if not bars:
                self._json({"error": "no data"}, 404); return
            a = scanner.analyze(t, bars, settings)
            apply_sizing(a, settings)
            a["sector"] = read_json(SECTORS_F, {}).get(t, "Other")
            a["sector_hot"] = a["sector"] in read_json(SUGGEST_F, {}).get("hot_sectors", [])
            self._json({"analysis": a, "bars": bars})
        elif route == "sector-heat" and len(parts) > 2 and parts[2] == "status":
            self._json(SECTORH)
        elif route == "sector-heat":
            self._json(read_json(SECTOR_HEAT_F, {"computed_at": None, "sectors": []}))
        elif route == "news" and len(parts) > 2 and parts[2] == "status":
            self._json(NEWS)
        elif route == "news":
            self._json(read_json(NEWS_F, {"computed_at": None, "sections": [], "ticker_news": {}}))
        elif route == "themes":
            self._json(read_json(THEMES_F, {}))
        elif route == "refresh-all":
            self._json(REFRESH)
        elif route == "docs" and len(parts) > 2:
            name = parts[2]
            p = DOCS.get(name)
            if not p:
                self._json({"error": "unknown doc"}, 404)
            else:
                self._json({"name": name, "content": p.read_text(encoding="utf-8") if p.exists() else ""})
        else:
            self._json({"error": "unknown route"}, 404)

    def do_POST(self):
        set_workspace(self)
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        route = parts[1] if len(parts) > 1 else ""
        body = self._body()

        if HOSTED and route == "screeners":
            # screeners (incl. the auto-built universe) are shared / owner-managed
            self._json({"ok": False, "error": "screeners are shared in hosted mode"}, 403); return
        if route == "screeners":
            screeners = read_json(SCREENERS_F, [])
            raw = body.get("tickers", "")
            tickers = [t.strip().upper() for t in re.split(r"[\s,;]+", raw) if t.strip()]
            sid = re.sub(r"[^a-z0-9]+", "-", body.get("name", "screener").lower()).strip("-") or f"s{int(time.time())}"
            base_sid, n = sid, 2
            while any(s["id"] == sid for s in screeners):
                sid = f"{base_sid}-{n}"; n += 1
            screeners.append({"id": sid, "name": body.get("name", "Screener"),
                              "is_default": False, "tickers": tickers})
            write_json(SCREENERS_F, screeners)
            self._json({"ok": True, "id": sid})

        elif route == "scan" and len(parts) > 2:
            sid = parts[2]
            with _scan_lock:
                if SCAN["running"]:
                    self._json({"ok": False, "error": "scan already running"}, 409); return
                threading.Thread(target=run_scan, args=(sid,), daemon=True).start()
            self._json({"ok": True})

        elif route == "suggestions" and len(parts) > 3:
            ticker, action = parts[2].upper(), parts[3]
            s = read_json(SUGGEST_F, {"items": []})
            it = next((i for i in s["items"] if i["ticker"] == ticker), None)
            if not it:
                self._json({"error": "not found"}, 404); return
            # status lives in a per-user overlay (status.json) so it never mutates the
            # shared scan everyone reads.
            ov = read_json(status_f(), {})
            cur = ov.get(ticker, {})
            if action == "approve":
                cur["status"] = "approved"
            elif action == "reject":
                cur["status"] = "rejected"
                cur["reject_reason"] = body.get("reason", "")
            elif action == "catalyst":
                cur["catalyst"] = body.get("catalyst", "")
            elif action == "take":
                cur["status"] = "taken"
                self._create_trade(it, body)
            ov[ticker] = cur
            write_json(status_f(), ov)
            self._json({"ok": True})

        elif route == "trades" and len(parts) == 2:
            self._add_trade(body); self._json({"ok": True})

        elif route == "trades" and len(parts) > 3 and parts[3] == "close":
            self._close_trade(parts[2], body); self._json({"ok": True})

        elif route == "upload":
            self._json(self._save_upload(body))

        elif route == "sector-heat" and len(parts) > 2 and parts[2] == "refresh":
            with _sector_lock:
                if SECTORH["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_sector_heat, daemon=True).start()
            self._json({"ok": True})

        elif route == "news" and len(parts) > 2 and parts[2] == "refresh":
            with _news_lock:
                if NEWS["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_news_refresh, daemon=True).start()
            self._json({"ok": True})

        elif route == "universe" and len(parts) > 2 and parts[2] == "build":
            with _universe_lock:
                if UNIVERSE["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_build_universe, daemon=True).start()
            self._json({"ok": True})

        elif route == "suspicious" and len(parts) > 2 and parts[2] == "scan":
            with _suspect_lock:
                if SUSPECT["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_suspicious, daemon=True).start()
            self._json({"ok": True})

        elif route == "premarket" and len(parts) > 2 and parts[2] == "scan":
            with _premkt_lock:
                if PREMKT["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_premarket, daemon=True).start()
            self._json({"ok": True})

        elif route == "refresh-all":
            with _refresh_lock:
                if REFRESH["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                threading.Thread(target=run_refresh_all, daemon=True).start()
            self._json({"ok": True})

        else:
            self._json({"error": "unknown route"}, 404)

    def do_PUT(self):
        set_workspace(self)
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        route = parts[1] if len(parts) > 1 else ""
        body = self._body()

        if route == "settings":
            s = read_json(settings_f(), {})
            if "account_size" in body:
                s["account_size"] = body["account_size"] or None
                update_rules_account(s["account_size"])
            if "risk_pct" in body:
                s["risk_pct"] = body["risk_pct"]
            if "max_position_pct" in body:
                s["max_position_pct"] = body["max_position_pct"]
            write_json(settings_f(), s)
            self._json({"ok": True, "settings": s})
        elif route == "watchlist":
            write_json(watchlist_f(), body.get("rows", []))
            regen_watchlist()
            self._json({"ok": True})
        elif route == "docs" and len(parts) > 2:
            if HOSTED:   # strategy docs are shared & read-only for friends
                self._json({"ok": False, "error": "docs are read-only in hosted mode"}, 403); return
            p = DOCS.get(parts[2])
            if not p:
                self._json({"error": "unknown doc"}, 404); return
            p.write_text(body.get("content", ""), encoding="utf-8")
            self._json({"ok": True})
        elif route == "trades" and len(parts) > 2:
            trades = read_json(trades_f(), [])
            t = next((x for x in trades if x["id"] == parts[2]), None)
            if not t:
                self._json({"error": "not found"}, 404); return
            for k in ("setup_type", "entry", "stop", "target", "shares", "notes", "status"):
                if k in body:
                    t[k] = body[k]
            write_json(trades_f(), trades)
            regen_trades_md()
            self._json({"ok": True})
        else:
            self._json({"error": "unknown route"}, 404)

    def do_DELETE(self):
        set_workspace(self)
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        if HOSTED and parts[1:2] == ["screeners"]:
            self._json({"ok": False, "error": "screeners are shared in hosted mode"}, 403); return
        if parts[1:2] == ["screeners"] and len(parts) > 2:
            screeners = [s for s in read_json(SCREENERS_F, []) if s["id"] != parts[2]]
            write_json(SCREENERS_F, screeners)
            self._json({"ok": True})
        else:
            self._json({"error": "unknown route"}, 404)

    # ---- trade helpers ----
    def _create_trade(self, sug, body):
        trades = read_json(trades_f(), [])
        entry = body.get("entry") or sug.get("entry")
        trades.append({
            "id": f"{sug['ticker']}-{int(time.time())}",
            "ticker": sug["ticker"], "setup_type": sug.get("setup_type", "Breakout"),
            "status": "open", "planned_entry": sug.get("entry"), "entry": entry,
            "stop": body.get("stop") or sug.get("stop"),
            "target": body.get("target") or sug.get("target"),
            "shares": body.get("shares"), "taken_at": now_date(),
            "exit": None, "result_r": None, "result_pct": None, "rules_followed": None,
            "notes": body.get("notes", ""), "lesson": None, "screenshots": [],
        })
        write_json(trades_f(), trades)
        regen_trades_md()

    def _add_trade(self, body):
        trades = read_json(trades_f(), [])
        body.setdefault("id", f"{body.get('ticker','T')}-{int(time.time())}")
        body.setdefault("status", "open")
        body.setdefault("taken_at", now_date())
        body.setdefault("screenshots", [])
        trades.append(body)
        write_json(trades_f(), trades)
        regen_trades_md()

    def _close_trade(self, tid, body):
        trades = read_json(trades_f(), [])
        t = next((x for x in trades if x["id"] == tid), None)
        if not t:
            return
        t["status"] = "closed"
        t["exit"] = body.get("exit")
        t["result_r"] = body.get("result_r")
        t["result_pct"] = body.get("result_pct")
        t["rules_followed"] = body.get("rules_followed")
        # realized P&L flows into the account balance
        e, sh, x = t.get("entry"), t.get("shares"), body.get("exit")
        if e and sh and x:
            pnl = (x - e) * sh
            t["realized_pnl"] = round(pnl, 2)
            st = read_json(settings_f(), {})
            if st.get("account_size"):
                st["account_size"] = round(st["account_size"] + pnl, 2)
                write_json(settings_f(), st)
                update_rules_account(st["account_size"])
        if body.get("notes"):
            t["notes"] = body["notes"]
        if body.get("lesson"):
            t["lesson"] = body["lesson"]
            self._append_lesson(t["ticker"], body["lesson"])
        write_json(trades_f(), trades)
        regen_trades_md()

    def _append_lesson(self, ticker, lesson):
        if HOSTED:   # lessons.md is shared; a friend's lesson stays on their own trade record
            return
        p = DOCS["lessons"]
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            txt = "# Lessons\n"
        line = f"- **{lesson}** (from {ticker}, {now_date()})"
        if "_(empty for now" in txt:
            txt = txt.replace("_(empty for now — lessons appear here as I log trades)_", line)
        else:
            txt = txt.rstrip() + "\n" + line + "\n"
        p.write_text(txt, encoding="utf-8")

    def _save_upload(self, body):
        up = uploads_dir()
        up.mkdir(parents=True, exist_ok=True)
        data = body.get("data", "")
        if "," in data:
            data = data.split(",", 1)[1]
        fname = re.sub(r"[^A-Za-z0-9._-]", "_", body.get("filename", f"shot-{int(time.time())}.png"))
        fname = f"{int(time.time())}-{fname}"
        try:
            (up / fname).write_bytes(base64.b64decode(data))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        # hosted uploads are served per-user from /uploads/<file>; locally from /data/uploads/<file>
        rel = f"uploads/{fname}" if HOSTED else f"data/uploads/{fname}"
        tid = body.get("trade_id")
        if tid:
            trades = read_json(trades_f(), [])
            t = next((x for x in trades if x["id"] == tid), None)
            if t:
                t.setdefault("screenshots", []).append(rel)
                write_json(trades_f(), trades)
                regen_trades_md()
        return {"ok": True, "path": rel}


class _Server(ThreadingHTTPServer):
    # locally: detect a 2nd launch instead of double-binding. hosted: containers restart,
    # so allow the port to be reused immediately.
    allow_reuse_address = HOSTED


def _shared_refresh_loop():
    """Hosted only: keep the SHARED market data (regime, scan, sector heat, news) fresh
    so friends never have to trigger a refresh or wait on a cold scan. Runs once at boot,
    then roughly once a day."""
    while True:
        try:
            run_refresh_all()
        except Exception:
            pass
        time.sleep(24 * 3600)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if HOSTED:
        seed_shared()
        USERS_DIR.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=_shared_refresh_loop, daemon=True).start()
        host = "0.0.0.0"
        print(f"Trading Data Center (hosted) listening on {host}:{PORT}")
        try:
            _Server((host, PORT), Handler).serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    # ---- local single-user mode (unchanged) ----
    UPLOADS.mkdir(parents=True, exist_ok=True)
    regen_watchlist()
    regen_trades_md()
    url = f"http://localhost:{PORT}"
    try:
        srv = _Server(("127.0.0.1", PORT), Handler)
    except OSError:
        # already running — just open the browser to the existing instance
        print(f"Data Center already running — opening {url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return
    print(f"Trading Data Center running at {url}  (close this window or Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
