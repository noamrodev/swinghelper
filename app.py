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
import sys
import subprocess
import webbrowser
import mimetypes
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, quote, parse_qs

import scanner
import universe
import rubric

BASE = Path(__file__).resolve().parent
SEED = BASE / "data"                       # shared files baked into the image / repo
# DATA is where live data is read/written. Locally it's just data/. On a host with a
# persistent disk, set DATA_DIR to the mounted volume so journals survive redeploys;
# shared seed files are copied in on first boot (see seed_shared()).
DATA = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else SEED
WEB = BASE / "web"
UPLOADS = DATA / "uploads"

# Shared, market-wide data files are now resolved per-market via getters defined just below
# the workspace helpers (suggest_f(), market_f(), ...). US keeps the original data/ paths;
# the Israeli market (market()=="il") namespaces every file under data/il/. See _mns().

DOCS = {
    "qullamaggie": BASE / "strategy" / "qullamaggie.md",
    "martin-luk": BASE / "strategy" / "martin-luk.md",
    "minervini": BASE / "strategy" / "minervini.md",
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


# --------------------------------------------------------------------------- #
# Market dimension (US default + Israel/TASE). Mirrors the per-user workspace
# pattern: a thread-local `market` set per request (X-Market header) and per
# worker thread. US keeps the original data/ paths untouched; IL namespaces every
# data file under an il/ subfolder, giving a fully separate dashboard, account,
# positions, suggestions and forward-test log. NOTE: _ctx is request/worker-thread
# local — worker threads must set _ctx.market explicitly (it is NOT inherited).
# --------------------------------------------------------------------------- #
def market():
    return getattr(_ctx, "market", "us")


def _safe_market(raw):
    m = (raw or "us").lower()
    return m if m in ("us", "il") else "us"


def _mns(p):
    """Market-namespace a shared data path: US unchanged; IL -> <dir>/il/<name>."""
    return p if market() == "us" else p.parent / "il" / p.name


def udir():
    """The current request's data dir: a per-user folder when hosted, else data/."""
    return getattr(_ctx, "udir", DATA)


def _ud():
    """Per-user data dir for the current market (US unchanged; IL under il/)."""
    return udir() if market() == "us" else udir() / "il"


def settings_f():   return _ud() / "settings.json"      # per-user account/risk
def trades_f():     return _ud() / "trades.json"        # per-user journal
def watchlist_f():  return _ud() / "watchlist.json"     # per-user watchlist
def status_f():     return _ud() / "status.json"        # per-user approve/reject/take overlay
def uploads_dir():  return _ud() / "uploads"            # per-user screenshots


# Shared, market-wide data files (per-market). settings_owner_f() is the owner's config,
# a fallback for shared jobs (scan/universe params); per-user settings live in settings_f().
def settings_owner_f():  return _mns(DATA / "settings.json")
def screeners_f():       return _mns(DATA / "screeners.json")
def suggest_f():         return _mns(DATA / "suggestions.json")
def sectors_f():         return _mns(DATA / "sectors.json")
def themes_f():          return _mns(DATA / "themes.json")
def sector_heat_f():     return _mns(DATA / "sector_heat.json")
def news_f():            return _mns(DATA / "news.json")
def market_f():          return _mns(DATA / "market.json")
def universe_f():        return _mns(DATA / "universe.json")
def symnames_f():        return _mns(DATA / "symbol_names.json")  # {ticker: name} cache (news resolver)
def suspicious_f():      return _mns(DATA / "suspicious.json")
def premarket_f():       return _mns(DATA / "premarket.json")
def spinning_f():        return _mns(DATA / "spinning.json")      # last spinning (intraday reversal) scan
def forward_f():         return _mns(DATA / "forward_log.json")   # forward/paper-test snapshots
def pnl_f():             return _mns(DATA / "pnl_calendar.json")  # per-day equity + day P&L calendar
def groups_f():          return _mns(DATA / "groups.json")        # detected emerging groups


_TEMPLATE_DEFAULTS = {
    "settings.json": {"account_size": None, "risk_pct": 1.0, "max_position_pct": 15, "size_factor": 1.0},
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
    """Resolve per-request market (X-Market) + workspace (X-Workspace, hosted only)."""
    _ctx.market = _safe_market(handler.headers.get("X-Market", "us"))
    if not HOSTED:
        _ctx.udir = DATA
    else:
        wsid = _safe_wsid(handler.headers.get("X-Workspace", "")) or "default"
        ud = USERS_DIR / wsid
        if not ud.exists():
            bootstrap_workspace(ud)
        _ctx.udir = ud
    if _ctx.market != "us":                 # lazy-create the per-market per-user workspace
        d = _ud()
        if not d.exists():
            bootstrap_workspace(d)


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


def _spawn(target, *args, **kwargs):
    """Start a daemon worker that inherits the current request's market + workspace.
    The thread-local _ctx is NOT inherited by new threads, so capture both here and
    re-set them inside the worker — otherwise IL jobs would write into the US files."""
    mkt, ud = market(), udir()

    def _run():
        _ctx.market, _ctx.udir = mkt, ud
        target(*args, **kwargs)

    threading.Thread(target=_run, daemon=True).start()


def run_sector_heat():
    themes = read_json(themes_f(), {})
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
    write_json(sector_heat_f(), {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "sectors": rows})
    SECTORH.update(running=False, current="")


def reverse_themes():
    rev = {}
    for name, ts in read_json(themes_f(), {}).items():
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
SPIN = {"running": False, "done": 0, "total": 0, "current": ""}
_spin_lock = threading.Lock()
GROUPS = {"running": False, "done": 0, "total": 0, "current": ""}
_groups_lock = threading.Lock()


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

# MAJOR MARKET NEWS — a deliberately HIGH bar: only regime-changing, whole-market events
# (war, a new Fed chair, an emergency rate move, an election/president shock, a crash/halt, a
# debt/fiscal shock, a national crisis). NOT everyday tariff/analyst/single-stock noise. Each is a
# precise pattern so routine headlines ("Fed chair speaks", "price war") don't trip it.
MACRO_PATTERNS = [
    (re.compile(r"\b(declares war|at war with|war breaks out|invasion of|invades|missile strike|air ?strikes?|nuclear (strike|war|attack)|act of war)\b", re.I), "⚔️ War / military escalation"),
    (re.compile(r"\b((new|next|incoming) fed chair|fed chair (resign|step(s|ping) down|nominat|replaced|fired|out\b)|powell (resign|step(s|ping) down|fired|ousted|replaced|out\b))", re.I), "🏛️ Fed leadership change"),
    (re.compile(r"\b(emergency rate (cut|hike)|surprise rate (cut|hike)|inter-?meeting (cut|hike)|fed (cuts|hikes|slashes) rates by|(75|100) ?(bps|basis points))\b", re.I), "🏛️ Major Fed move"),
    (re.compile(r"\b(wins the (presidency|election)|elected president|president-?elect|resigns as president|forced out as president|impeached)\b", re.I), "🗳️ Election / presidency shock"),
    (re.compile(r"\b(market crash|circuit breaker|trading halted|black monday|flash crash|stocks? plunge \d\d%|biggest (drop|plunge) since)\b", re.I), "📉 Market crash / halt"),
    (re.compile(r"\b(u\.?s\.? (debt )?default|debt default|government shutdown|credit rating downgrade|u\.?s\.? downgraded|sovereign default)\b", re.I), "🏦 Debt / fiscal shock"),
    (re.compile(r"\b(global pandemic|pandemic declared|national state of emergency|terror(ist)? attack)\b", re.I), "🚨 National crisis"),
]


def _detect_macro(items, days=3):
    """Scan headlines for a TRULY market-moving macro event (see MACRO_PATTERNS). Returns the most
    recent few, deduped — the dashboard shows these as a prominent banner. Empty almost every day."""
    cut = time.time() - days * 86400
    out, seen = [], set()
    for it in items:
        ep = _epoch(it)
        if ep < cut:
            continue
        title = it.get("title", "") or ""
        for pat, label in MACRO_PATTERNS:
            if pat.search(title):
                key = "".join(ch for ch in title.lower() if ch.isalnum())[:50]
                if key in seen:
                    break
                seen.add(key)
                out.append({"label": label, "title": title, "link": it.get("link", ""),
                            "published": it.get("published", ""), "source": it.get("source", ""), "_ep": ep})
                break
    out.sort(key=lambda m: m["_ep"], reverse=True)
    return out[:3]


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


# news headline -> ticker resolution -------------------------------------------------------- #
# Tokens that look like tickers but are almost always English/jargon in a headline, OR
# first words of company names too generic to match on alone.
_GENERIC_TOKENS = {"AI", "CEO", "CFO", "COO", "ETF", "ETFS", "IPO", "USA", "US", "UK", "EU",
                   "GDP", "FED", "SEC", "FDA", "ON", "OR", "AND", "FOR", "THE", "NEW", "NOW",
                   "ALL", "ARE", "IT", "BE", "GO", "SO", "BY", "AT", "TO", "OF", "IN", "AS",
                   "NO", "UP", "Q1", "Q2", "Q3", "Q4", "FY", "DEAL", "WAR", "BUY", "SELL"}
_GENERIC_NAME_WORDS = {"open", "block", "global", "american", "national", "first", "general",
                       "capital", "group", "energy", "power", "data", "cloud", "digital",
                       "tech", "health", "financial", "international", "united", "advanced",
                       "applied", "core", "next", "prime", "smart", "super", "value", "world"}
_TICKER_TOKEN = re.compile(r"\b[A-Z]{1,5}\b")
# words that, right after a company name, mark it as the headline's SUBJECT (a stock that moved)
_SUBJ_NEXT = {"stock", "stocks", "shares", "share", "s", "stocks", "rises", "rose", "rallies",
              "rallied", "rally", "jumps", "jumped", "soars", "soared", "surges", "surged",
              "plunges", "plunged", "tumbles", "tumbled", "slides", "slid", "slumps", "slumped",
              "drops", "dropped", "falls", "fell", "sinks", "sank", "pops", "popped", "spikes",
              "spiked", "rockets", "rocketed", "climbs", "climbed", "gains", "gained", "crashes",
              "crashed", "dips", "dipped", "skyrockets", "skyrocketed", "edges", "edged"}


def _symbol_names():
    """{ticker: company name}, cached to data/symbol_names.json and refreshed ~monthly from the
    keyless NASDAQ directory. Degrades to whatever's cached (or {}) if the fetch fails."""
    cached = read_json(symnames_f(), None)
    if isinstance(cached, dict) and cached.get("names"):
        try:
            if time.time() - float(cached.get("_ep", 0)) < 30 * 86400:
                return cached["names"]
        except Exception:
            pass
    try:
        names = universe.fetch_symbol_names()
        if names:
            write_json(symnames_f(), {"_ep": time.time(),
                                    "built_at": time.strftime("%Y-%m-%d %H:%M"), "names": names})
            return names
    except Exception:
        pass
    return cached["names"] if isinstance(cached, dict) and cached.get("names") else {}


def _build_news_resolver():
    """Return fn(headline)->[tickers]: maps a material headline to the universe ticker(s) it's
    about, by company name ('Marvell Technology'->MRVL) or an explicit ticker token ('HPE stock').
    Restricted to the current universe so only tradeable names surface. This is what lets a fresh
    catalyst on a name that ISN'T yet a graded suggestion still show up in catalysts."""
    screeners = read_json(screeners_f(), [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    uni_set = set(default.get("tickers", [])) if default else set()
    names = _symbol_names()
    phrases = []                                   # (lower company name, ticker) — multi-word only
    first_count, first_map = {}, {}                # distinctive first word -> ticker (if unique)
    for tk in uni_set:
        nm = names.get(tk)
        if not nm:
            continue
        low = nm.lower()
        if " " in low and len(low) >= 5:
            phrases.append((low, tk))
        ft = low.split()[0]
        if len(ft) >= 5 and ft not in _GENERIC_NAME_WORDS:
            first_count[ft] = first_count.get(ft, 0) + 1
            first_map[ft] = tk
    firsts = {ft: tk for ft, tk in first_map.items() if first_count[ft] == 1}
    phrases = [(ph.split(), tk) for ph, tk in sorted(phrases, key=lambda p: -len(p[0]))]

    def resolve(title):
        if not title:
            return []
        toks = re.findall(r"[a-z0-9&]+", title.lower())
        hits = []
        # A company name only counts when it's the SUBJECT — i.e. immediately followed by
        # stock/shares/possessive or a price-action verb ("Marvell stock soars", "HPE jumps").
        # This rejects names that are merely mentioned: analyst firms ("Truist cuts…",
        # "…Morgan Stanley sees"), comparisons, and generic words ("Price Target", "(NASDAQ:…").
        def subject(i_after):
            return i_after < len(toks) and toks[i_after] in _SUBJ_NEXT
        for ws, tk in phrases:                     # 1) full multi-word company name as subject
            n = len(ws)
            for i in range(len(toks) - n + 1):
                if toks[i:i + n] == ws and subject(i + n) and tk not in hits:
                    hits.append(tk)
                    break
        for i, w in enumerate(toks):               # 2) distinctive single-word name as subject
            if w in firsts and firsts[w] not in hits and subject(i + 1):
                hits.append(firsts[w])
        for m in _TICKER_TOKEN.finditer(title):    # 3) explicit ticker token ('HPE stock soars')
            tok = m.group(0)
            if tok not in uni_set or tok in _GENERIC_TOKENS or tok in hits:
                continue
            tail = title[m.end():m.end() + 8].lower()
            if len(tok) >= 4 or tail.lstrip().startswith(("stock", "shares")) or ("(" + tok + ")") in title:
                hits.append(tok)
        return hits[:2]

    return resolve


def run_news_refresh():
    NEWS.update(running=True, done=0, current="headlines")
    raw_trump = fetch_rss("Trump stocks OR tariffs OR contract when:7d", 25)
    raw_market = fetch_rss("stock soars OR plunges OR contract OR deal when:5d", 25)
    # dedicated query for whole-market, regime-changing events (war / Fed chair / crash / election);
    # the catalyst queries above wouldn't surface these. Filtered hard by MACRO_PATTERNS below.
    raw_macro = fetch_rss('"stock market" OR "wall street" OR "federal reserve" OR economy when:3d', 30)
    sections = [
        {"name": "🇺🇸 Trump & policy", "items": _recent_important(raw_trump)},
        {"name": "📰 Market catalysts", "items": _recent_important(raw_market)},
    ]
    hot = [s for s in read_json(sector_heat_f(), {}).get("sectors", [])
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
    tickers = [i["ticker"] for i in read_json(suggest_f(), {}).get("items", [])[:16]]
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
    HARD = ["contract", "deal", "wins", "awarded", "approval", "soar", "surge", "plunge",
            "explosion", "war", "ban", "fda", "acquire", "merger", "recall", "invests",
            "funding", "selected", "darpa", "pentagon", "billion", "stake", "bet"]
    # ---- promote BIG catalysts on ANY universe name, not just the top-16 suggestions ----
    # The material feed already caught the headline (e.g. MRVL soaring); resolve it back to a
    # tradeable ticker so a fresh mover surfaces in catalysts even before it's a graded setup.
    resolve = _build_news_resolver()
    promoted = 0
    for it in pool_imp:                                  # newest-first, already material
        if promoted >= 14:
            break
        sent = it.get("sentiment")
        tl = it["title"].lower()
        if sent not in ("good", "bad") or not any(k in tl for k in HARD):
            continue
        if any(g in tl for g in GOOD_KW) and any(b in tl for b in BAD_KW):
            continue                                     # mixed up/down → a roundup, not one catalyst
        for tk in resolve(it["title"]):
            if tk in tn:
                continue
            tn[tk] = {"title": it["title"], "link": it["link"], "published": it.get("published", ""),
                      "sentiment": sent, "trump": "trump" in tl, "from_feed": True}
            promoted += 1
    # ---- actionable alerts: distill the BIG catalysts into BUY / AVOID directives ----
    themes_map = read_json(themes_f(), {})
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
    # buy first, then by recency — a fresh big mover (e.g. MRVL today) outranks week-old news.
    alerts.sort(key=lambda a: (0 if a["dir"] == "buy" else 1 if a["dir"] == "watch" else 2,
                               -_epoch(a)))
    alerts = alerts[:10]
    # ---- unified feed: ONE deduped, newest-first stream of the material headlines (cleaner than the
    # scattered category cards). pool_imp is already important-only + newest-first.
    feed, fseen = [], set()
    for it in pool_imp:
        key = "".join(ch for ch in (it["title"] or "").lower() if ch.isalnum())[:60]
        if not key or key in fseen:
            continue
        fseen.add(key)
        feed.append({"title": it["title"], "link": it["link"], "source": it.get("source", ""),
                     "published": it["published"], "ep": it.get("_ep", 0), "sentiment": it["sentiment"],
                     "trump": "trump" in it["title"].lower()})
        if len(feed) >= 24:
            break
    # major market news — the high-bar macro banner (war / Fed chair / crash / election shock).
    # Scans the dedicated macro query + the existing pool; empty on a normal day.
    macro = _detect_macro(list(raw_macro) + pool)
    write_json(news_f(), {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "sections": sections,
                        "ticker_news": tn, "theme_news": theme_news, "alerts": alerts,
                        "feed": feed, "macro": macro})
    NEWS.update(running=False, current="")


def run_suspicious():
    """Scan the universe for end-of-day / after-hours buy & sell anomalies (insider-style)."""
    tickers = []
    screeners = read_json(screeners_f(), [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    if default:
        tickers = default.get("tickers", [])
    SUSPECT.update(running=True, done=0, total=len(tickers) or 1, current="")

    def prog(done, total, t):
        SUSPECT.update(done=done, total=total, current=t)
    try:
        out = scanner.scan_suspicious(tickers, prog)
        write_json(suspicious_f(), out)
    except Exception as e:
        SUSPECT.update(current="error: " + str(e))
    SUSPECT.update(running=False, current="")


def run_premarket():
    """Scan the universe for notable pre-market gaps (run during pre-market hours)."""
    tickers = []
    screeners = read_json(screeners_f(), [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    if default:
        tickers = default.get("tickers", [])
    PREMKT.update(running=True, done=0, total=len(tickers) or 1, current="")

    def prog(done, total, t):
        PREMKT.update(done=done, total=total, current=t)
    try:
        out = scanner.scan_premarket(tickers, prog)
        write_json(premarket_f(), out)
    except Exception as e:
        PREMKT.update(current="error: " + str(e))
    PREMKT.update(running=False, current="")


def run_spinning():
    """Scan the universe for 'spinning' stocks — beaten-down names reclaiming the 5-min 10 EMA
    (intraday reversal candidates). Smart-prefiltered to names down on the day inside the scanner."""
    tickers = []
    screeners = read_json(screeners_f(), [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    if default:
        tickers = default.get("tickers", [])
    SPIN.update(running=True, done=0, total=len(tickers) or 1, current="quotes…")

    def prog(done, total, t):
        SPIN.update(done=done, total=total, current=t)
    try:
        out = scanner.scan_spinning(tickers, progress=prog)
        write_json(spinning_f(), out)
    except Exception as e:
        SPIN.update(current="error: " + str(e))
    SPIN.update(running=False, current="")


def run_detect_groups():
    """Detect emerging groups (correlated recent movers) over the default screener universe."""
    screeners = read_json(screeners_f(), [])
    default = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
    tickers = default.get("tickers", []) if default else []
    GROUPS.update(running=True, done=0, total=len(tickers) or 1, current="")

    def prog(done, total, t):
        GROUPS.update(done=done, total=total, current=t)
    try:
        groups = scanner.detect_groups(tickers, progress=prog)
        smap = read_json(sectors_f(), {})
        rev = reverse_themes()
        theme_keys = set(read_json(themes_f(), {}).keys())
        kept = []
        for g in groups:
            theme_tally, sector_tally = {}, {}
            for m in g["members"]:
                sec = smap.get(m["ticker"], "Other"); m["sector"] = sec
                th = rev.get(m["ticker"]); m["theme"] = th
                if th:
                    theme_tally[th] = theme_tally.get(th, 0) + 1
                if sec and sec != "Other":
                    sector_tally[sec] = sector_tally.get(sec, 0) + 1
            need = max(2, g["size"] // 2 + 1)          # a real majority must share the thread
            t_best = max(theme_tally.items(), key=lambda x: x[1], default=(None, 0))
            s_best = max(sector_tally.items(), key=lambda x: x[1], default=(None, 0))
            # The point of THIS tab is to find NEW groups — not to re-show themes we already track.
            if t_best[1] >= need:                       # the cluster is an EXISTING theme…
                theme = t_best[0]
                joining = [m["ticker"] for m in g["members"] if rev.get(m["ticker"]) != theme]
                if not joining:
                    continue                             # …entirely a known theme → skip, nothing new
                g["common"], g["common_count"] = theme, t_best[1]   # …but NEW names are joining it
                g["novel"], g["joining"] = False, joining           # surface only the new members
            elif s_best[1] >= need and s_best[0] not in theme_keys:
                g["common"], g["common_count"] = s_best[0], s_best[1]   # genuinely new cluster
                g["novel"], g["joining"] = True, []
            else:
                continue                                 # an existing sector/theme or no thread → skip
            kept.append(g)
        write_json(groups_f(), {"computed_at": time.strftime("%Y-%m-%d %H:%M"), "groups": kept})
    except Exception as e:
        GROUPS.update(current="error: " + str(e))
    GROUPS.update(running=False, current="")


def run_market_regime():
    """Classify the market's benchmark indexes into a blended posture; store for dashboard + grade.
    Passes the universe so the Fear & Greed gauge can compute breadth + 52wk highs/lows."""
    try:
        tickers = read_json(universe_f(), {}).get("tickers", [])
        reg = scanner.market_regime(market(), tickers=tickers)
        if reg:
            write_json(market_f(), reg)
    except Exception:
        pass


def run_build_universe():
    """Assemble the market's tradeable universe, coarse-filter to the Market Leaders criteria,
    and write the ticker list into the default screener so the scan runs on the real universe.
    US: the full NASDAQ/NYSE/AMEX directory. IL: the curated TASE seed list (data/il_symbols.json),
    re-quoted + liquidity-filtered the same way (no auto-directory exists for Tel Aviv)."""
    mkt = market()
    settings = read_json(settings_owner_f(), {})
    uni = scanner.mcfg(mkt)["uni"]
    il_syms = None
    label = "Fetching US symbols…"
    if mkt == "il":
        il_syms = read_json(DATA / "il_symbols.json", {}).get("symbols", [])
        label = "Reading TASE symbols…"
    UNIVERSE.update(running=True, stage=label, done=0, total=0)

    def prog(stage, total, done):
        UNIVERSE.update(stage="Reading market caps…" if stage == "quotes" else label,
                        done=done, total=total)
    try:
        u = universe.build_universe(
            exchanges=settings.get("universe_exchanges", "all"),
            size=settings.get("universe_size", uni["size"]),
            min_price=settings.get("universe_min_price", uni["min_price"]),
            min_mktcap_m=settings.get("universe_min_mktcap_m", uni["min_mktcap_m"]),
            min_dollar_vol_m=settings.get("universe_min_dollar_vol_m", uni["min_dollar_vol_m"]),
            progress=prog,
            symbols=il_syms,
        )
        if u.get("tickers"):
            write_json(universe_f(), u)
            screeners = read_json(screeners_f(), [])
            tgt = next((s for s in screeners if s.get("is_default")), screeners[0] if screeners else None)
            if tgt is None:
                tgt = {"id": "market-leaders", "name": "Market Leaders", "is_default": True}
                screeners.append(tgt)
            tgt["tickers"] = u["tickers"]
            tgt["auto"] = True
            write_json(screeners_f(), screeners)
        UNIVERSE.update(running=False, stage="Done", built_at=u.get("built_at"),
                        kept=u.get("kept"), passed=u.get("passed_filter"),
                        total_syms=u.get("universe_total"))
    except Exception as e:
        UNIVERSE.update(running=False, stage="error: " + str(e))


def run_refresh_all():
    """The 'new day' button: refresh the EXISTING universe — regime, rescan setups/ratings,
    sector heat, news. Does NOT rebuild the universe (that's the manual 'Rebuild universe'
    button); New day just re-reads prices and recomputes for the names we already track."""
    _idxnames = "/".join(n for n, _ in scanner.mcfg(market())["indexes"])
    REFRESH.update(running=True, stage=f"Reading the market ({_idxnames})…", done=0, total=4)
    try:
        run_market_regime()
        REFRESH.update(stage="Updating setups & ratings…", done=1)
        screeners = read_json(screeners_f(), [])
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
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)   # IL namespace dir may not exist yet
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


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
    # TEST SIZE: while validating the new system live we trade at a fraction of full size (user set 1/3,
    # 2026-06-03). size_factor scales the FINAL share count (so it cuts both risk AND position to that
    # fraction). R-based learning is unaffected (R is stop-relative); this only de-risks real dollars.
    size_factor = settings.get("size_factor", 1.0) or 1.0
    max_dollars = settings.get("max_position_dollars")    # hard $ cap per position (e.g. 500); None/0 = off
    item["capped"] = False
    item["cap_reason"] = None
    if acct and risk_ps > 0 and entry > 0:
        risk_shares = int((acct * risk_pct / 100) // risk_ps)
        maxpos_shares = int((acct * maxpos_pct / 100) // entry)
        afford = int(acct // entry)
        full_shares = max(0, min(risk_shares, maxpos_shares, afford))
        shares = int(full_shares * size_factor)          # legacy test-size fraction (1.0 when unused)
        # HARD DOLLAR CAP (user de-risking while learning): never deploy more than max_dollars of capital
        # per position. If one share already costs more than the cap, allow exactly 1 share (if affordable).
        if max_dollars and entry > 0:
            dcap = int(max_dollars // entry)
            if dcap < 1:
                dcap = 1 if entry <= acct else 0
            shares = min(shares, dcap) if shares else dcap
            if dcap < full_shares:
                item["capped"] = True
                item["cap_reason"] = f"${int(max_dollars)} position cap"
        item["shares"] = shares
        item["full_shares"] = full_shares                # what full size would be (for the UI label)
        item["size_factor"] = size_factor
        if full_shares < risk_shares and not item["capped"]:
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


def compute_equity():
    """Live account equity = base (the size you input) + realized (closed P&L) + open (unrealized
    P&L). The base is never auto-mutated — equity is always derived, so the account 'updates itself'."""
    st = read_json(settings_f(), {})
    base = st.get("account_size") or 0
    trades = read_json(trades_f(), [])
    realized = open_pnl = 0.0
    for t in trades:
        e, sh = t.get("entry"), t.get("shares")
        if not (e and sh):
            continue
        if t.get("status") == "closed" and t.get("exit"):
            realized += (t["exit"] - e) * sh
        elif t.get("status") == "open" and t.get("ticker"):
            try:
                bars = scanner.get_bars(t["ticker"])
                if bars:
                    open_pnl += (bars[-1]["close"] - e) * sh
            except Exception:
                pass
    return {"base": round(base, 2), "realized": round(realized, 2),
            "open": round(open_pnl, 2), "equity": round(base + realized + open_pnl, 2)}


def _equity_at(session, refresh=True):
    """Account equity at a session's REGULAR close — each open position valued at the FINALIZED daily-bar
    close for that date (never a mid-session cached value or an after-hours print). Deterministic, so the
    P&L calendar cell doesn't drift with WHEN it's recorded (the old bug: a cached mid-session 'close'
    inflated the baseline → the next day's day_pnl came out too low). `session` = 'YYYY-MM-DD'."""
    st = read_json(settings_f(), {})
    base = st.get("account_size") or 0
    trades = read_json(trades_f(), [])
    realized = open_pnl = 0.0
    for t in trades:
        e, sh = t.get("entry"), t.get("shares")
        if not (e and sh):
            continue
        if t.get("status") == "closed" and t.get("exit"):
            realized += (t["exit"] - e) * sh
            continue
        if t.get("status") != "open" or not t.get("ticker"):
            continue
        if t.get("taken_at") and t["taken_at"] > session:        # position not opened yet on this session
            continue
        try:
            bars = scanner.get_bars(t["ticker"], max_age_hours=0 if refresh else 12)
            bar = next((b for b in reversed(bars) if b.get("time") and b["time"] <= session), None)
            if bar:
                open_pnl += (bar["close"] - e) * sh
        except Exception:
            pass
    return {"base": round(base, 2), "realized": round(realized, 2),
            "open": round(open_pnl, 2), "equity": round(base + realized + open_pnl, 2)}


# The IRREPLACEABLE files — your account, journal and forward test. Everything else (suggestions,
# universe, news, cache) is regenerable market data and is deliberately NOT backed up.
_BACKUP_FILES = ["settings.json", "trades.json", "watchlist.json", "status.json",
                 "forward_log.json", "pnl_calendar.json"]


def backup_data():
    """Copy only the irreplaceable personal data (account, trades, watchlist, forward log, P&L — for
    BOTH markets and every user dir — plus the journal prose) to a timestamped backups/<ts>/ folder, so
    one bad write never loses the journal. Local-only (hosted has no durable disk anyway)."""
    if HOSTED:
        return {"ok": False, "error": "backup is local-only"}
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = BASE / "backups" / ts
    roots = [DATA, DATA / "il"]                                # us + il namespaces
    users = DATA / "users"
    if users.exists():
        for d in users.iterdir():
            if d.is_dir():
                roots += [d, d / "il"]
    n = 0
    try:
        for root in roots:
            for name in _BACKUP_FILES:
                p = root / name
                if p.exists():
                    rel = p.relative_to(DATA)
                    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, dst / rel)
                    n += 1
        for jp in (BASE / "journal" / "lessons.md", BASE / "journal" / "trades.md"):
            if jp.exists():
                (dst / "journal").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(jp, dst / "journal" / jp.name)
                n += 1
    except Exception as e:
        return {"ok": False, "error": str(e), "files": n}
    return {"ok": True, "path": str(dst), "files": n, "at": ts}


def rebuild_pnl_calendar():
    """Recompute EVERY recorded session's equity + day_pnl with `_equity_at` (the deterministic
    finalized-close method) so the whole calendar is consistent — fixes any cells captured with the old
    snapshot method. day_pnl = this session's equity − the prior recorded session's (recomputed) equity."""
    cal = read_json(pnl_f(), {})
    prev_eq = None
    for session in sorted(cal):
        eq = _equity_at(session)
        base_for_first = eq["base"]
        ref = prev_eq if prev_eq is not None else base_for_first
        cal[session] = {"equity": eq["equity"], "open": eq["open"], "realized": eq["realized"],
                        "day_pnl": round(eq["equity"] - ref, 2)}
        prev_eq = eq["equity"]
    write_json(pnl_f(), cal)
    return cal


def _equity_settings():
    """Settings with account_size swapped for live equity — used for sizing/grading so position size
    reflects current equity, not a stale typed-in number."""
    st = read_json(settings_f(), {})
    eq = compute_equity()["equity"]
    return {**st, "account_size": eq} if eq else st


def _mean(xs, default=0.0):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else default


def _swing_lows(l, left=3, right=3, lookback=60):
    """Pivot lows: bars whose low is the local min within a +/- window, over the last `lookback`
    bars. These are the support shelves a protective stop should sit just under (mirror of
    scanner._swing_highs)."""
    n = len(l)
    start = max(left, n - lookback)
    out = []
    for i in range(start, n - right):
        if l[i] == min(l[i - left:i + right + 1]):
            out.append((i, l[i]))
    return out


def profit_guard(bars, entry, stop, shares, adr, emas, settings):
    """Find a structural profit-LOCK stop, or return None.

    The user's rule (2026-06-04): "I keep giving money back at breakeven. Let me KEEP some —
    raise the stop somewhere there's structure AND enough distance from the price — but only
    where it makes sense; don't force me out." So a guard is offered ONLY when a real support
    level exists that simultaneously:
      (a) locks in >= guard_min_lock dollars of open gain if it's hit,     (keep money)
      (b) sits >= guard_buffer_adr ADR BELOW the live price (def 1.5×),     (clears a normal pullback close)
      (c) is at real structure — a swing low, a reclaimed level, or an EMA, (a level the market respects)
      (d) is meaningfully above the current stop.                          (a real raise, not micro-nagging)
    If no level clears all four, return None — the position is left alone (no choke). Never lowers a stop.
    """
    if not (entry and shares and bars):
        return None
    min_lock = float(settings.get("guard_min_lock", rubric.GUARD_MIN_LOCK))
    buffer_adr = float(settings.get("guard_buffer_adr", rubric.GUARD_BUFFER_ADR))
    last = bars[-1]["close"]
    adr_px = max(last * (adr or 0) / 100.0, 0.01)
    cur_stop = stop if stop is not None else (entry - 1)            # treat "no stop" as below entry
    # the band a guard stop may live in: high enough to bank at least min_lock $, low enough to keep
    # room. min_lock is a small floor ("at least take SOME money") — the structure picked below banks
    # as much as the position allows (often well past it), so a big winner naturally locks $100+, while
    # a small/volatile position simply doesn't qualify (you can't guard money you don't have).
    floor_profit = entry + min_lock / shares                       # below this it doesn't lock enough $ to bother
    ceil_room = last - buffer_adr * adr_px                          # above this there isn't enough breathing room
    if ceil_room <= floor_profit:
        return None                                                # can't bank min_lock AND keep room → leave it alone
    # gather structural support candidates that sit in [.., ceil_room] and above the current stop.
    l = [b["low"] for b in bars]
    h = [b["high"] for b in bars]
    e9, e21, e50 = emas
    cands = []                                                     # (price, label)
    for _i, lo in _swing_lows(l):
        cands.append((lo, "swing low"))
    for _i, hi in scanner._swing_highs(h):                         # reclaimed swing high = resistance-turned-support
        if hi < last:
            cands.append((hi, "reclaimed level"))
    for px, lab in ((e9, "9 EMA"), (e21, "21 EMA"), (e50, "50 EMA")):
        if px:
            cands.append((px, lab))
    eligible = [(px, lab) for px, lab in cands if cur_stop < px <= ceil_room]
    if not eligible:
        return None
    # pick the HIGHEST eligible structure — ceil_room already guarantees the buffer, so highest
    # banks the most profit while still keeping the required room below price.
    struct, label = max(eligible, key=lambda x: x[0])
    tick = 0.08 * adr_px                                           # set the stop a hair UNDER the level (closing basis)
    guard = round(max(struct - tick, floor_profit), 2)            # never drop below the min-lock floor
    if guard < floor_profit or guard <= cur_stop:
        return None
    lock = round((guard - entry) * shares)
    if lock < min_lock:                                           # final guard: the tick mustn't erase the lock
        return None
    return {"guard_stop": guard, "lock": lock, "structure": round(struct, 2),
            "structure_label": label, "room_pct": round((last - guard) / last * 100, 2),
            "room_adr": round((last - guard) / adr_px, 2), "min_lock": round(min_lock)}


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
    e9, e21, e50 = scanner._ema(c, 9), scanner._ema(c, 21), scanner._ema(c, 50)
    adr = _mean([(h[k] / l[k] - 1) * 100 for k in range(-20, 0) if l[k] > 0], 1.0) or 1.0
    entry = t.get("entry") or t.get("planned_entry")
    stop = t.get("stop")
    target = t.get("target")
    # 1R = the ORIGINAL risk taken at entry. Use initial_stop (the stop you took, never overwritten);
    # fall back to the live stop, then to the 2R target if the stop was raised to breakeven.
    istop = t.get("initial_stop") if t.get("initial_stop") is not None else stop
    risk = (entry - istop) if (entry and istop and entry > istop) else None
    risk_r = risk or (((target - entry) / 2) if (target and entry and target > entry) else None)
    r_mult = ((last - entry) / risk_r) if (risk_r and entry) else None
    breakeven_plus = bool(entry and stop and stop >= entry)   # stop locked at/above entry = house money
    ext9 = (last / e9 - 1) * 100
    ext9_adr = ext9 / adr
    under_9 = last < e9
    # ----- which line is the trailing exit for THIS setup? -----
    # Deep Pullback / Consolidation are bought AT the 50 EMA / inside a base — by design they sit
    # BELOW the 9 EMA at entry, so "under the 9 EMA" is normal and NOT an exit. Their invalidation
    # is a close under the 50 EMA (or the stop). Momentum/breakout/pullback setups trail the 9 EMA.
    setup = (t.get("setup_type") or "").strip().lower()
    patient = setup in ("deep pullback", "consolidation")
    trail_n = rubric.TRAIL_EMA_PATIENT if patient else rubric.TRAIL_EMA   # default trail 20-EMA (max-R); 50 for patient
    trail_label = f"{trail_n} EMA"

    def _ema_ser(arr, n):
        k = 2 / (n + 1); ev = arr[0]; out = [ev]
        for x in arr[1:]:
            ev = x * k + ev * (1 - k); out.append(ev)
        return out
    trail_ser = _ema_ser(c, trail_n)
    trail = trail_ser[-1]
    under_trail = last < trail
    ti = None                                            # entry index (first session on/after entry)
    if t.get("taken_at"):
        ti = next((i for i, b in enumerate(bars) if b["time"] >= t["taken_at"]), None)
    if ti is None:
        ti = max(0, len(c) - 10)
    # "armed": has the position CLOSED above its trailing line since you got in? The 9/50-EMA
    # close-exit only applies once you're riding ABOVE the line. If you bought a dip BELOW it, it's
    # NOT an exit until price reclaims the line and then closes back under it — the hard stop is the
    # only exit until then.
    armed = any(c[j] >= trail_ser[j] for j in range(ti, len(c)))
    # earnings + news context
    e = None
    try:
        e = scanner.get_earnings(t["ticker"])
    except Exception:
        e = None
    edays = days_until(e["date"]) if e else None
    earn_soon = edays is not None and 0 <= edays <= rubric.COACH_EARN_SOON_D
    nm = news_map.get(t.get("ticker"))
    bad_news = bool(nm and nm.get("sentiment") == "bad")

    # ----- profit guard: a structural stop that BANKS real money with room (only where it makes sense) -----
    adr_px = max(last * adr / 100.0, 0.01)
    guard = None
    try:
        guard = profit_guard(bars, entry, stop, t.get("shares"), adr, (e9, e21, e50), settings)
    except Exception:
        guard = None
    step_dollars = float(settings.get("guard_step_dollars", rubric.GUARD_STEP_DOLLARS))
    # fire if the guard banks at least step_dollars MORE than the current stop already locks (a stop at/below
    # entry locks $0, so the FIRST guard always clears this — it only suppresses nudging an already-profitable stop).
    _cur_lock = max(0.0, ((stop - entry) * t.get("shares", 0))) if (stop and entry and t.get("shares")) else 0.0
    guard_ready = bool(guard and (guard["lock"] - _cur_lock) >= step_dollars)

    reasons = []
    stop_hit = bool(stop and last < stop)                 # live price has traded through the stop
    rtxt = (f"+{r_mult:.1f}R" if (r_mult is not None and r_mult >= 0)
            else (f"{r_mult:.1f}R" if r_mult is not None else "—"))
    # priority ladder: stop → trail-EMA close → PARABOLIC trim → earnings (watch) → breakeven → news → hold
    # TRIM only on a genuine parabolic blow-off (price VERY far above the EMAs, the ARM/DELL case),
    # NOT on ordinary strength — the user does not trim quickly. PARABOLIC = ≥4× ADR above the 9 EMA.
    if stop and last < stop:
        if breakeven_plus:
            action, tone = "EXIT", "warn"
            reasons.append(f"stop ${stop} is your locked-in (breakeven+) exit — {rtxt}, take it if it closes here")
        else:
            action, tone = "EXIT", "danger"
            reasons.append(f"price ${round(last,2)} is below your stop ${stop} — you should already be out")
    elif under_trail and not armed:
        action, tone = "HOLD", "good"
        reasons.append(f"below the {trail_label} (${round(trail,2)}) but it hasn't reclaimed the line since entry "
                       f"— not an exit yet; your stop (${stop}) is the only exit until it closes back above it")
    elif under_trail:
        action, tone = "EXIT", "danger"
        reasons.append(f"closed back under the {trail_label} (${round(trail,2)}) after riding above it — your trailing exit")
    elif patient and under_9:
        action, tone = "HOLD", "good"
        reasons.append(f"under the 9 EMA but holding the 50 EMA (${round(e50,2)}) — that's the deep-pullback/base "
                       f"plan; exit only on a close under the 50")
    elif r_mult is not None and r_mult >= rubric.COACH_RAISE_R and ext9_adr >= rubric.COACH_PARABOLIC_ADR:
        action, tone = "TRIM", "warn"
        reasons.append(f"parabolic — {ext9_adr:.1f}× ADR above the 9 EMA (far above 9/21/50), {rtxt} — trim "
                       f"into the spike & trail the rest (the ARM/DELL blow-off case)")
    elif earn_soon:
        action, tone = "WATCH", "warn"
        reasons.append(f"earnings in {edays}d — binary event ({rtxt}); hold through or reduce, your call "
                       f"(no auto-trim on strength)")
    elif guard_ready:
        # Bank real money: raise the stop to a STRUCTURAL level that locks in >= $X with room below
        # price — not a tight breakeven that gets wicked (the 5-breakevens-at-once pain). Still a daily
        # CLOSE-under-it exit, so it doesn't force you out on an intraday poke.
        action, tone = "GUARD STOP", "good"
        g = guard
        already = "raise" if (stop and stop >= entry) else "lock it in: raise"
        reasons.append(f"{rtxt} — {already} your stop to ${g['guard_stop']} (just under the {g['structure_label']} "
                       f"${g['structure']}). That banks ${g['lock']} if it pulls back, and it sits {g['room_adr']:.1f}× ADR "
                       f"/ {g['room_pct']:.1f}% under ${round(last,2)} so normal wiggle won't hit it — exit on a daily close below it.")
    elif r_mult is not None and r_mult >= rubric.COACH_RAISE_R and stop and entry and stop < entry:
        action, tone = "RAISE STOP", "good"
        # Trail the stop just UNDER the trailing EMA (the real exit line), not a fixed breakeven. The
        # RGTI lesson: snapping to breakeven right where the 9 EMA sits gets you wicked out on noise —
        # if the EMA has risen up near your entry, give it room to the line (risk a little) and exit
        # on a daily CLOSE under it instead. Falls back to breakeven when the EMA is still below your stop.
        adr_px = last * adr / 100 or 0.01
        ema_stop = round(trail - 0.10 * adr_px, 2)        # a hair under the 9/50-EMA (closing basis)
        if ema_stop > stop:
            qual = ("locks in above breakeven" if ema_stop >= entry
                    else "risk a little to the line, not a tight breakeven that gets wicked")
            reasons.append(f"{rtxt} — trail the stop to just under the {trail_label} (${ema_stop}) — "
                           f"{qual}; exit on a daily close under the line")
        else:
            reasons.append(f"{rtxt} locked-in zone — raise the stop to breakeven (${entry}) so the trade "
                           f"can't turn red (the {trail_label} ${round(trail,2)} is still below your stop)")
    elif bad_news:
        action, tone = "WATCH", "warn"
        reasons.append(f"a negative headline is out — watch the {trail_label} close")
    else:
        action, tone = "HOLD", "good"
        reasons.append(f"trend intact above the {trail_label} ({rtxt}) — hold; exit on a daily close under it")
    # optional add-on note (never the primary action; respects total-risk rules)
    if action in ("HOLD", "RAISE STOP") and ext9_adr < 1.0 and last > e21 > e50 \
            and r_mult is not None and 0 <= r_mult < 2 and not earn_soon:
        reasons.append("near rising support & not extended — could add on a push to new highs (optional, keep total risk within rules)")
    if e and edays is not None and not earn_soon and 0 <= edays <= 21:
        reasons.append(f"earnings {e['date']} ({edays}d out){' · est.' if e.get('estimate') else ''}")

    return {"action": action, "tone": tone, "reasons": reasons, "stop_hit": stop_hit, "e9": round(e9, 2),
            "e21": round(e21, 2), "guard": guard, "adr": round(adr, 2),
            "e50": round(e50, 2), "trail": round(trail, 2), "trail_n": trail_n,
            "trail_label": trail_label, "patient": patient, "armed": armed,
            "r_mult": round(r_mult, 2) if r_mult is not None else None,
            "ext9": round(ext9, 1), "ext9_adr": round(ext9_adr, 1),
            "ext21": round((last / e21 - 1) * 100, 1), "under_9ema": under_9,
            "earnings_days": edays, "earnings_date": e["date"] if e else None,
            "earnings_estimate": bool(e.get("estimate")) if e else False,
            "last": round(last, 2)}


def _grade_letter(r):
    return rubric.grade_letter(r)


def entry_grade_for(ticker, date, settings):
    """Grade a setup AS OF its entry date — the reconstructable, price-based factors: setup quality,
    entry location (chased vs tight), relative strength, liquidity. Market regime / sector heat / news
    can't be reconstructed for a past date, so they're held NEUTRAL (55). Same weights + letter
    thresholds as the live grade (strategy/scoring.md) so the letter is comparable. This is the mirror
    on the trader's OWN entries — taking C/D setups is a fair lesson, separate from the system's calls.
    Returns {rating, grade, setup_type, entry_quality, ext10, asof, note} or None."""
    a = scanner.analyze_at(ticker, date, settings, market())
    if not a:
        return None
    setup = rubric.setup_score(a.get("score", 0))
    rs = a.get("rs_score", 50)
    entry_loc = a.get("entry_quality", 60)
    liq = a.get("liq_score", 50)
    n = rubric.NEUTRAL                                      # regime/sector/timing/news unknown for a past date
    r = rubric.composite(setup, rs, n, entry_loc, liq, n, n, n)
    if a.get("extended"):
        r -= 8
    if a.get("distribution_today"):
        r = min(r, rubric.CAP_DISTRIB)
    r = round(max(0, min(99, r)))
    return {"rating": r, "grade": _grade_letter(r), "setup_type": a.get("setup_type"),
            "entry_quality": a.get("entry_quality"), "ext10": a.get("ext10"), "asof": a.get("asof"),
            "note": "setup + entry location + relative strength + liquidity at entry; market context neutral"}


def enrich_trades(trades):
    """Add current price + P&L to each trade, a coaching action for open positions, and the
    system grade of the setup as of the entry date (so the trader can see if they're taking weak setups)."""
    settings = read_json(settings_f(), {})
    news_map = read_json(news_f(), {}).get("ticker_news", {})
    for t in trades:
        e, sh = t.get("entry"), t.get("shares")
        t["last"] = t["pnl"] = t["pnl_pct"] = None
        t["coach"] = None
        # the risk basis is the INITIAL stop (the one taken at entry); default to the live stop for
        # older trades that predate the field. R is always measured off this, never the raised stop.
        if t.get("initial_stop") is None:
            t["initial_stop"] = t.get("stop")
        istop = t.get("initial_stop")
        t["risk_ps"] = round(e - istop, 4) if (e and istop is not None and e > istop) else None
        t["r_open"] = None
        # grade the setup as of the entry date (price-based; market context neutral)
        t["entry_grade"] = t["entry_rating"] = t["graded_setup"] = t["grade_note"] = None
        t["low_grade"] = False
        if t.get("ticker") and t.get("taken_at"):
            try:
                eg = entry_grade_for(t["ticker"], t["taken_at"], settings)
            except Exception:
                eg = None
            if eg:
                t["entry_grade"] = eg["grade"]
                t["entry_rating"] = eg["rating"]
                t["graded_setup"] = eg["setup_type"]
                t["grade_note"] = eg["note"]
                t["low_grade"] = eg["rating"] < 63          # below B = a setup worth questioning
        if t.get("status") == "open" and t.get("ticker"):
            bars = scanner.get_bars(t["ticker"])
            last = bars[-1]["close"] if bars else None
            t["last"] = last
            if e and sh and last:
                t["pnl"] = round((last - e) * sh, 2)
                t["pnl_pct"] = round((last / e - 1) * 100, 2)
            else:
                t["pnl"] = t["pnl_pct"] = None
            if t.get("risk_ps") and last:                 # unrealized R off the initial stop
                t["r_open"] = round((last - e) / t["risk_ps"], 2)
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
    smap = read_json(sectors_f(), {})
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


def grade_suggestions(items, settings, posture=None, heat=None):
    """Enrich scan items with sizing, theme/heat, news, earnings proximity, group leadership,
    and the composite 0-100 rating + letter grade. Mutates items in place and returns them
    sorted best-first. Canonical rubric: strategy/scoring.md (keep weights in sync).
    `posture`/`heat` override the live market+sector context with AS-OF values (for the forward
    backfill of a missed past day); when None they read the current files (the normal live path)."""
    rev = reverse_themes()
    heat = heat if heat is not None else {h["sector"]: h for h in read_json(sector_heat_f(), {}).get("sectors", [])}
    news_data = read_json(news_f(), {})
    news_map = news_data.get("ticker_news", {})
    theme_news = news_data.get("theme_news", {})
    sd = _session_date()                                  # the live/most-recent session date (SPY-derived)
    for it in items:
        apply_sizing(it, settings)
        it["worth_waiting"] = it.get("setup_type") in ("Deep Pullback", "Consolidation")
        # date-correct "prior-day high" the live rotation entry reclaims: if the scan's last daily
        # bar IS the current session (today's forming bar), the prior day is prev_high; otherwise
        # the last bar is already a completed prior session and IS the prior-day high.
        it["prior_high"] = (it.get("prev_high") if it.get("last_bar_date") == sd
                            else it.get("last_high"))
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
        # EP = a TRUE gap (gated in scanner) AND a fresh material catalyst. With the gap but no
        # good-news catalyst attached, it's just a base breakout — relabel so card/coach/grade agree.
        if it.get("setup_type") == "Episodic Pivot" and it.get("news_dir") != "good":
            it["setup_type"] = "Breakout"
        # size every entry option (each plan carries its own entry/risk_ps), not just the primary
        for e in it.get("entries", []):
            apply_sizing(e, settings)
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
    posture = posture if posture is not None else read_json(market_f(), {}).get("posture", 55)  # 0-100 regime (or as-of)
    pullback_setups = ("Pullback", "Pullback @ AVWAP",
                       "AVWAP reclaim (ATH)", "AVWAP reclaim (earnings)")

    def _rating(it, unit=None):
        # `unit` = a single entry option's per-entry inputs (setup_type / ext50_adr / entry_quality /
        # buyable_now / chase_exempt). None → grade the ticker off its primary fields. The grade is
        # PER setup: a name's pullback option and breakout option grade apart (INOD pullback can be A
        # while its breakout — a buy-stop above an extended high — is a chase, C/D).
        u = unit or {}
        st = u.get("setup_type") or it.get("setup_type") or ""
        ext = u.get("ext50_adr")
        ext = (it.get("ext50_adr", 0) or 0) if ext is None else ext     # extension above the 50-EMA in ADR (chase measure)
        ww = u.get("chase_exempt")
        ww = bool(it.get("worth_waiting")) if ww is None else bool(ww)   # patient dip-buy — buys INTO the 50 (exempt from chase pen)
        patient_quality = ww or ("AVWAP" in st)                         # setups the backtest showed work in ANY tape
        buyable = u.get("buyable_now", it.get("buyable_now"))
        setup = rubric.setup_score(it.get("score", 0))                  # technical setup quality
        rs = it.get("rs_score", 50)                                     # relative strength
        # market regime: breakouts/EPs are demoted harder than pullbacks in weak tape
        regime = posture if st in pullback_setups else (
            posture if posture >= rubric.REGIME_SOFT else posture * rubric.REGIME_DISCOUNT)
        entry_loc = u.get("entry_quality")
        entry_loc = it.get("entry_quality", 60) if entry_loc is None else entry_loc   # don't-chase / tight-stop
        liq = it.get("liq_score", 50)                                    # liquidity -> institutional interest
        tr, tier = it.get("theme_trend"), it.get("theme_tier")
        # backtest: a RISING sector beat a backward-looking "Hot" tier (Hot is often already extended),
        # so Rising now outranks Hot.
        sector = 100 if tr == "Rising" else (85 if tier == "Hot" else 25 if tr == "Slowing"
                                             else 12 if tr == "Falling" else 55)
        # timing rewards WAITING (backtest: not-buyable +0.36R beat buyable-now +0.17R): the in-zone
        # bonus only goes to patient setups OR a non-extended name — never an extended in-zone chase.
        timing = 75 if (ww or (buyable and ext < 2)) else 55
        nd = it.get("news_dir")
        news = 100 if nd == "good" else (8 if nd == "bad" else (75 if it.get("news_flag") else 55))
        r = rubric.composite(setup, rs, regime, entry_loc, liq, sector, timing, news)
        hist = bysetup.get(st)                                          # learns from realized results
        if hist and hist.get("n", 0) >= rubric.HIST_MIN_N:
            r += max(-rubric.HIST_NUDGE_MAX, min(rubric.HIST_NUDGE_MAX,
                                                 hist.get("avg_r", 0) * rubric.HIST_NUDGE_K))
        # earnings overhang: a print inside a week is a hard demote (don't open binary risk);
        # 8-14 days out is a lighter caution.
        if it.get("earnings_soon"):
            r -= rubric.EARN_SOON_PEN
        elif it.get("earnings_near"):
            r -= rubric.EARN_NEAR_PEN
        # EXTENSION / CHASE penalty (v5): a momentum name far above its BASE (the 50-EMA) already made the
        # money — buying it is a chase (SEDG: +85%/1m, 4.1x ADR over the 50, was grading B). Graded demote
        # above 2.5x ADR, HARD-CAP at C once parabolic (>=4x). worth_waiting dip-buys buy INTO the 50 -> exempt.
        if not ww:
            if ext > rubric.CHASE_SOFT_ADR:
                r -= (ext - rubric.CHASE_SOFT_ADR) * rubric.CHASE_PEN_K
            if ext >= rubric.CHASE_HARD_ADR:
                r = min(r, rubric.CAP_PARABOLIC)            # parabolic chase -> max C (overrides RS/sector)
            elif ext >= rubric.CHASE_SOFT_ADR:
                r = min(r, rubric.CAP_EXTENDED)             # extended 2.5-4x ADR above the 50 -> max B, not A
                                                           # (the APLD case: shallow pullback in an extended move)
        # distribution / climax-reversal day: cap at C regardless of how strong RS/sector look (the ASTS case).
        if it.get("distribution_today"):
            r = min(r, rubric.CAP_DISTRIB)
        # REGIME GATE (v5 — setup-aware): the backtest's losers in weak tape were breakouts/EPs; AVWAP/
        # Consolidation/worth-waiting worked in any tape. So breakouts/EPs stay capped below posture 65,
        # but the BEST patient at-support setups can reach A even in a mixed tape (user choice).
        if st in ("Breakout", "Episodic Pivot"):
            if posture < rubric.REGIME_WEAK:
                r = min(r, rubric.CAP_BREAKOUT_WEAK)
            elif posture < rubric.REGIME_MIXED:
                r = min(r, rubric.CAP_BREAKOUT_MIXED)       # breakouts fail in weak tape -> max B
        elif patient_quality and ext < rubric.CHASE_SOFT_ADR and tr != "Falling":
            if posture < rubric.REGIME_WEAK:
                r = min(r, rubric.CAP_PATIENT_WEAK)         # deep correction: even the best -> max B, not A
            # posture >= 50: no cap -> A/A+ reachable for the best patient setups (near their base)
        else:                                               # plain pullbacks & everything else
            if posture < rubric.REGIME_WEAK:
                r = min(r, rubric.CAP_PLAIN_WEAK)
            elif posture < rubric.REGIME_MIXED:
                r = min(r, rubric.CAP_PLAIN_MIXED)          # allow A, not A+
        return round(max(0, min(99, r)))

    patient_or_pullback = pullback_setups + ("Deep Pullback", "Consolidation")
    for it in items:
        entries = it.get("entries") or []
        for e in entries:
            bk = e.get("kind") == "breakout"
            # per-entry effective setup type for the gate: a breakout option grades as a Breakout;
            # a pullback option keeps the ticker's pullback family, or "Pullback" if the ticker is a breakout.
            if bk:
                unit_setup = "Breakout"
            elif (it.get("setup_type") or "") in patient_or_pullback:
                unit_setup = it.get("setup_type")
            else:
                unit_setup = "Pullback"
            e["rating"] = _rating(it, {"setup_type": unit_setup, "ext50_adr": e.get("ext50_adr"),
                                       "entry_quality": e.get("entry_quality"),
                                       "buyable_now": e.get("buyable_now"),
                                       "chase_exempt": e.get("chase_exempt")})
            e["grade"] = _grade_letter(e["rating"])
        # the ticker's headline grade = the AVERAGE of its actionable options, so one great-but-unlikely
        # entry can't carry the whole card (the FLNC case: a chase breakout + a 19%-below pullback that
        # grades A shouldn't sit at the top on the pullback alone — the realistic blend is mid). A STALE
        # leg (price ran far above its zone — that dip won't come) is excluded entirely; it can't even be
        # averaged in. With one actionable leg the headline is just that leg's grade.
        ratable = [e for e in entries if not e.get("stale")] or entries
        ratings = [e["rating"] for e in ratable]
        it["rating"] = round(sum(ratings) / len(ratings)) if ratings else _rating(it)
        it["grade"] = _grade_letter(it["rating"])
    # break rating ties with the raw setup `score` so the order is STABLE + meaningful (many names tie at
    # rating 72 when a weak-tape regime gate caps grades at B; without a tiebreak the list reshuffles).
    return sorted(items, key=lambda x: (x.get("rating", 0), x.get("score", 0)), reverse=True)


# --------------------------------------------------------------------------- #
# Forward (paper) test — snapshot the live A/A+ picks each scan, score them as they
# mature. This is the honest out-of-sample check: the REAL current universe, no
# survivorship bias, and it seeds the learning loop. Shared market data (owner settings).
# --------------------------------------------------------------------------- #
def _ema_series(closes, n):
    k = 2 / (n + 1)
    e = closes[0]
    out = [e]
    for x in closes[1:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


FORWARD_MAX_N = 400                     # safety cap: log ALL real setups (grade C+) each day, up to this
                                       # many (user wants the full daily set to learn from — not a top-N).
                                       # 400 is a sane ceiling so one snapshot can't blow up the log.
FILL_WINDOW_SESSIONS = 6               # a setup has this many sessions to trigger ("take it"), else dropped
                                       # as a no-fill — matches the blind backtest's actionable window.


def _forward_pick_row(s):
    """One logged forward pick — the frozen plan (both entry legs) + the rich tags we learn from later.
    Shared by the live snapshot (log_forward_picks) and the missed-day backfill so they're identical."""
    return {"ticker": s["ticker"], "grade": s["grade"], "rating": s["rating"],
            "setup_type": s.get("setup_type"), "entry": s.get("entry"), "stop": s.get("stop"),
            "target": s.get("target"), "entry_type": s.get("entry_type"),
            # BOTH entry legs (pullback + breakout) so the forward test can score whichever actually
            # filled — a name that never dipped but broke out and ran is no longer logged as "no-fill".
            "entries": [{"kind": e.get("kind"), "entry_type": e.get("entry_type"),
                         "entry": e.get("entry"), "stop": e.get("stop"), "target": e.get("target")}
                        for e in (s.get("entries") or []) if e.get("entry") and e.get("stop")],
            "buyable_now": bool(s.get("buyable_now")), "trend_template": bool(s.get("trend_template")),
            "vcp": bool(s.get("vcp")), "theme": s.get("theme"),
            "theme_trend": s.get("theme_trend"), "close_at_signal": s.get("close"),
            # rich tags for the learning loop — slice forward R by any of these later
            "rs_score": s.get("rs_score"), "ext50_adr": s.get("ext50_adr"),
            "liq_score": s.get("liq_score"), "tier": s.get("theme_tier"), "adr": s.get("adr"),
            "prior_high": s.get("prior_high") or s.get("last_high")}


def log_forward_picks(date_key=None):
    """Snapshot today's TOP suggestions as a STATIC record (the top-N SET by grade), so we can score how
    they perform over the following days. Decoupled from the dashboard's live display order: the dashboard
    floats buyable-now names up intraday (`sugRank`), but the snapshot captures by rating then raw score —
    a stable set that doesn't depend on the transient buyable-now state at capture. Keyed by the session
    that just CLOSED; one snapshot per date; never overwrites."""
    items = read_json(suggest_f(), {}).get("items", [])
    if not items:
        return
    day = date_key or _next_session_date()                # label by the session you ACT on these (next session
                                                          # after the close they were snapshotted at)
    log = read_json(forward_f(), {"snapshots": {}})
    if day in log.get("snapshots", {}):                 # already captured this day — keep the first
        return
    graded = grade_suggestions(items, _equity_settings())
    # Log ALL real setups (grade C and up = a genuine setup), richly tagged — the forward test is a full
    # daily data-collector to learn from, not just the best picks (user 2026-06-03: "all the setups, not
    # just the best — we might find stuff we didn't think of"). Sorted by rating then raw score for a
    # stable order; a high safety cap guards against a pathological snapshot.
    graded = [s for s in graded if s.get("grade") in ("A+", "A", "B", "C")]
    graded = sorted(graded, key=lambda s: (s.get("rating", 0), s.get("score", 0)),
                    reverse=True)[:FORWARD_MAX_N]
    picks = [_forward_pick_row(s) for s in graded]
    log.setdefault("snapshots", {})[day] = {
        "posture": read_json(market_f(), {}).get("posture"), "picks": picks,
        "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),  # provenance: when frozen
        "frozen_at_close_of": _session_date()}                                    # the session whose close this is
    write_json(forward_f(), log)


def _market_closed(mk=None):
    """Rough market-closed check in local exchange time: a non-trading day, or outside the session
    window. Good enough to gate a once-a-day end-of-day snapshot. Market-aware: US is Mon-Fri
    9:30-16:00 ET; IL (TASE) is Sun-Thu ~9:54-17:15 IST (see scanner.MARKETS)."""
    cfg = scanner.mcfg(mk or market())
    loc = datetime.now(timezone.utc) + timedelta(hours=cfg["tz_offset"])
    if loc.weekday() not in cfg["trading_days"]:
        return True
    hm = loc.hour + loc.minute / 60.0
    return hm < cfg["open"] or hm >= cfg["close"]


def _after_close_today(mk=None):
    """True ONLY after today's regular-session close (a trading day, local time >= close) — NOT pre-market.
    The end-of-day jobs (day-P&L finalize + forward snapshot) must run only once the session has
    actually CLOSED. `_market_closed()` alone is wrong for this: it's also true PRE-market, and
    pre-market the forming daily bar rolls `_session_date()` ahead to today, so the jobs would
    finalize today's P&L and freeze the NEXT session's snapshot before the session even traded
    (the mid-session/premature-capture bug)."""
    cfg = scanner.mcfg(mk or market())
    loc = datetime.now(timezone.utc) + timedelta(hours=cfg["tz_offset"])
    return loc.weekday() in cfg["trading_days"] and (loc.hour + loc.minute / 60.0) >= cfg["close"]


def _session_date(mk=None):
    """The latest COMPLETED trading-session date (from the market's reference index's most recent daily
    bar) — NOT the local calendar date (which rolls over while the exchange is still on the prior session)."""
    cfg = scanner.mcfg(mk or market())
    try:
        b = scanner.get_bars(cfg["ref"])
        return b[-1]["time"] if b else now_date()
    except Exception:
        return now_date()


def _next_session_date(mk=None):
    """The NEXT trading session after the latest completed one — i.e. the day you'd ACT on picks
    captured at the last close. Forward snapshots are LABELED by this (the trade day), so the picks you
    snapshot at tonight's close show up under the next session, awaiting until it trades. Skips
    non-trading days for the market (holidays not handled — close enough)."""
    cfg = scanner.mcfg(mk or market())
    try:
        d = datetime.strptime(_session_date(mk), "%Y-%m-%d") + timedelta(days=1)
        while d.weekday() not in cfg["trading_days"]:     # skip the market's weekend
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return _session_date(mk)


def _refresh_forward_bars():
    """Pull the latest session's bars for the logged picks so score_forward() can advance them.
    Probes the market's reference index fresh (1 call) to learn the latest session, then
    force-refreshes only picks that are BEHIND it — so it does nothing (beyond the probe) until a
    genuinely new session prints."""
    refsym = scanner.mcfg(market())["ref"]
    try:
        ref = scanner.get_bars(refsym, max_age_hours=0)
    except Exception:
        ref = scanner.get_bars(refsym)
    sess = ref[-1]["time"] if ref else None
    if not sess:
        return
    log = read_json(forward_f(), {"snapshots": {}})
    tks = {p["ticker"] for s in log.get("snapshots", {}).values() for p in s.get("picks", [])}
    for t in tks:
        try:
            b = scanner.get_bars(t)
            if not b or b[-1]["time"] < sess:             # behind the latest session -> pull fresh
                scanner.get_bars(t, max_age_hours=0)
        except Exception:
            pass


def run_forward_eod():
    """LOCAL-ONLY autonomous job (runs on launch + every ~30 min). Every run it:
      1) refreshes the logged picks' bars so EVERY snapshot's status SCORES/UPDATES continuously
         (this is the "update how each setup did, each market close" part),
      2) once the market is CLOSED, captures the day's top dashboard setups as a frozen snapshot
         labeled by the NEXT session (the day you'd act on them) — one per day, never overwriting.
    Capture is gated to market-closed so we snapshot the CLOSING picture, not a mid-session reshuffle.
    Never runs hosted."""
    if HOSTED:
        return
    _refresh_forward_bars()                                # pull latest bars so EVERY snapshot's status re-scores
    if not _after_close_today():
        return                                             # day-P&L + snapshot are END-OF-DAY only — never
                                                           # pre-market or mid-session (would write a stale/
                                                           # premature value, the bug the user hit)
    record_daily_pnl(_session_date())                      # finalize the just-closed session's day P&L
    trade_day = _next_session_date()                       # the upcoming session these picks are FOR (the label)
    log = read_json(forward_f(), {"snapshots": {}})
    if trade_day in log.get("snapshots", {}):
        return                                             # already captured once for that session
    if SCAN.get("running"):
        return                                             # a scan is mid-flight; its post-close finish snapshots
    # AUTO FRESH SCAN at the close: recompute the whole universe so tomorrow's setups are a clean
    # post-close picture (not a stale intraday scan). run_scan writes suggestions AND, because it's after
    # the close, freezes the forward snapshot itself — so these fresh setups ARE the forward-test record
    # and the dashboard's "best setups for tomorrow." Runs once/day (the snapshot gate above skips later ticks).
    screeners = read_json(screeners_f(), [])
    sc = next((s for s in screeners if s.get("is_default")), None) or (screeners[0] if screeners else None)
    if sc:
        run_scan(sc["id"], max_age=0)                      # fresh bars; writes suggestions + the post-close snapshot
    elif read_json(suggest_f(), {}).get("items"):
        log_forward_picks(trade_day)                       # no screener configured — snapshot whatever's there


def record_daily_pnl(session):
    """Record the day's account equity + day P&L into the personal calendar, keyed by the just-closed
    session. day_pnl = today's total equity − the last recorded prior day's equity (realized + unrealized
    move). Called ONLY post-close (gated by `_after_close_today()` in run_forward_eod) so each cell is a
    finalized close value — the in-progress day is shown live by the "Today's P&L" tile, not here.
    Local-only."""
    if HOSTED:
        return
    eq = _equity_at(session)                                   # finalized regular close, not a live/after-hours snapshot
    cal = read_json(pnl_f(), {})
    prior = [cal[k]["equity"] for k in sorted(cal) if k < session and isinstance(cal.get(k), dict)]
    prev_eq = prior[-1] if prior else eq["base"]
    cal[session] = {"equity": eq["equity"], "open": eq["open"], "realized": eq["realized"],
                    "day_pnl": round(eq["equity"] - prev_eq, 2)}
    write_json(pnl_f(), cal)


def _run_forward_eod_all():
    """Run the EOD forward job for EVERY market (US + IL), each with its own _ctx.market so the
    snapshots/P&L land in that market's namespace and the close-gating uses its trading calendar."""
    for mk in ("us", "il"):
        _ctx.market = mk
        try:
            run_forward_eod()
        except Exception:
            pass
    _ctx.market = "us"


def _next_session_date_after(D):
    """The next trading session AFTER a given past date D (the act-day label its close's picks carry)."""
    cfg = scanner.mcfg(market())
    try:
        ref = scanner.get_bars(cfg["ref"])
        cal = [b["time"] for b in ref] if ref else []
        if D in cal and cal.index(D) + 1 < len(cal):
            return cal[cal.index(D) + 1]
        d = datetime.strptime(D, "%Y-%m-%d") + timedelta(days=1)
        while d.weekday() not in cfg["trading_days"]:
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return D


def _posture_asof_local(upto_date):
    """Blended market posture AS-OF a past session (only bars <= upto_date) — for the forward backfill."""
    postures = []
    for name, sym in scanner.mcfg(market())["indexes"]:
        try:
            b = scanner.get_bars(sym)
            sl = [x for x in b if x["time"] <= upto_date] if b else []
            if len(sl) >= 60:
                postures.append(scanner._regime_one(name, sl)["posture"])
        except Exception:
            pass
    return round(sum(postures) / len(postures)) if postures else 55


def backfill_forward(max_days=10, throttle=True):
    """Reconstruct snapshots for trading sessions we MISSED (app not open at the close), so the forward
    calendar has no holes. Faithful AS-OF: grades each name on bars <= that session with the as-of
    posture (sector/news held neutral — not stored historically, same as the backtest methodology).
    Never overwrites; tags `backfilled`; skips the current upcoming label (the live EOD job owns that).
    Local-only. Returns the number of days added.

    GENTLE BY DESIGN (after a freeze on 2026-06-03): it yields the CPU frequently (sleeps between names
    and days) so a multi-day catch-up never pegs the machine. `max_days` bounds it to the realistic
    "missed a few days" case; one snapshot per day persists immediately so progress is never lost."""
    if HOSTED:
        return 0
    log = read_json(forward_f(), {"snapshots": {}})
    snaps = log.setdefault("snapshots", {})
    cfg = scanner.mcfg(market())
    ref = scanner.get_bars(cfg["ref"])
    if not ref:
        return 0
    sessions = [b["time"] for b in ref][-(max_days + 2):]
    current_label = _next_session_date()                       # the live job owns this upcoming day
    start = log.get("start")                                   # never reconstruct BEFORE the forward start
    todo = [(D, _next_session_date_after(D)) for D in sessions]
    todo = [(D, lab) for (D, lab) in todo
            if lab not in snaps and lab < current_label and (not start or lab >= start)]
    if not todo:
        return 0                                               # no holes (or all before start) — skip
    screeners = read_json(screeners_f(), [])
    sc = next((s for s in screeners if s.get("is_default")), None) or (screeners[0] if screeners else None)
    if not sc:
        return 0
    tickers = [t.upper() for t in sc.get("tickers", [])]
    bars_by = {}                                               # load each ticker's bars ONCE
    for t in tickers:
        b = scanner.get_bars(t)
        if b and len(b) >= 200:
            bars_by[t] = b
        if throttle and len(bars_by) % 200 == 0:
            time.sleep(0.05)
    settings = _equity_settings()
    added = 0
    for D, label in todo:
        posture = _posture_asof_local(D)
        items = []
        for i, (t, b) in enumerate(bars_by.items()):
            sl = [x for x in b if x["time"] <= D]
            if len(sl) < 200:
                continue
            try:
                items.append(scanner.analyze(t, sl, {}))
            except Exception:
                pass
            if throttle and i % 120 == 0:
                time.sleep(0.03)                               # yield the CPU — never peg it
        if not items:
            continue
        scanner._attach_rs(items)
        graded = grade_suggestions(items, settings, posture=posture, heat={})   # sector neutral (not stored)
        graded = [s for s in graded if s.get("grade") in ("A+", "A", "B", "C")]
        graded = sorted(graded, key=lambda s: (s.get("rating", 0), s.get("score", 0)),
                        reverse=True)[:FORWARD_MAX_N]
        snaps[label] = {"posture": posture, "picks": [_forward_pick_row(s) for s in graded],
                        "backfilled": True, "frozen_at_close_of": D,
                        "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        write_json(forward_f(), log)                           # persist each day as we go (resumable)
        added += 1
        if throttle:
            time.sleep(1.5)                                    # breathe between days
    return added


def _maybe_weekly_rebuild():
    """Auto-rebuild the universe ~weekly so the tradeable list stays current (new leaders in, faded
    names out) — which also keeps the forward test survivorship-honest. Rebuilds if it's been >=7 days
    since the last build. Local-only; runs in the background (the build is multi-minute)."""
    if HOSTED:
        return
    uni = read_json(universe_f(), {})
    built = (uni.get("built_at") or "")[:10]
    try:
        stale = (not built) or (datetime.now(timezone.utc) - datetime.strptime(built, "%Y-%m-%d")
                                ).days >= 7
    except Exception:
        stale = True
    if stale and not UNIVERSE.get("running"):
        try:
            run_build_universe()
        except Exception:
            pass


_LAUNCH_CATCHUP_DONE = False


def _launch_catchup():
    """Deferred, GENTLE launch catch-up (its own low-key thread): wait for the system to settle after
    login, backfill missed sessions (throttled), then — STAGGERED, never at the same time — a weekly
    universe rebuild if due. Split out + spaced after a 2026-06-03 freeze caused by running both at once."""
    try:
        time.sleep(120)                       # let the post-login storm (Chrome, services, …) settle first
        try:
            n = backfill_forward()
            if n:
                print(f"[forward] backfilled {n} missed session(s)")
        except Exception as e:
            print(f"[forward] backfill skipped: {e}")
        time.sleep(60)                        # stagger — never run the rebuild concurrently with backfill
        try:
            _maybe_weekly_rebuild()
        except Exception:
            pass
    except Exception:
        pass


def _forward_eod_loop():
    """Local background heartbeat: check every ~30 min whether an EOD snapshot is due (per market).
    The heavy launch catch-up (backfill + weekly rebuild) runs ONCE in its own deferred thread."""
    global _LAUNCH_CATCHUP_DONE
    if not _LAUNCH_CATCHUP_DONE:
        _LAUNCH_CATCHUP_DONE = True
        threading.Thread(target=_launch_catchup, daemon=True).start()
    while True:
        _run_forward_eod_all()
        time.sleep(1800)


def _sim_forward(bars, trade_date, entry, stop, entry_type, setup_type=None):
    """`trade_date` is the session these picks are FOR (the day you'd act on the trigger — the snapshot
    is labeled by it). Looks at bars from trade_date onward (so it never fills on the prior close where
    the setup was identified — a buy-stop at that day's high would be a fake instant loss; real bug,
    fixed), waits for the entry trigger (buy-stop: a high >= entry; limit: a low <= entry), fills there,
    then exits on a daily close < a TRAILING EMA or a stop hit.

    Records R under THREE trailing exits (9 / 20 / 50 EMA) from the SAME fill, so the forward test keeps
    measuring which exit is best instead of locking one in (user 2026-06-03: gather everything to learn).
    The PRIMARY R (the validated config-E rule) trails the **20 EMA** — or the **50 EMA** for the patient
    LONG-HOLD setups (Deep Pullback / Consolidation), a strong leader caught deep at the 50 held until it
    loses the 50 (mirrors the live coach + my-rules; the LITE case).
    The trade day hasn't printed yet → 'awaiting'. Trigger never hit → 'no-fill'.
    Returns {R, r9, r20, r50, matured, exit, status, fi, fill_date, exit_date, hold, primary_exit_n}."""
    if not entry or not stop or entry <= stop:
        return None
    risk = entry - stop
    closes = [b["close"] for b in bars]
    patient = (setup_type or "").strip().lower() in ("deep pullback", "consolidation")
    after = [i for i, b in enumerate(bars) if b["time"] >= trade_date]   # the trade day onward
    if not after:
        return {"R": None, "r9": None, "r20": None, "r50": None, "matured": False,
                "exit": None, "status": "awaiting", "fi": None}
    start, end = after[0], min(len(bars) - 1, after[0] + 60)
    is_limit = (entry_type == "limit")
    # A setup is only ACTIONABLE for a few days — a real call would be dropped if it doesn't trigger
    # within FILL_WINDOW sessions (matches the blind backtest's 6-session window). Searching the whole
    # 60-bar hold for a fill counted stale weeks-later triggers as "taken" — wrong. So cap the fill search.
    fill_last = min(end, start + FILL_WINDOW_SESSIONS - 1)
    fi = None                                              # fill index = first post-signal bar that triggers
    for j in range(start, fill_last + 1):
        if (bars[j]["low"] <= entry) if is_limit else (bars[j]["high"] >= entry):
            fi = j
            break
    if fi is None:
        # no-fill once the whole window has had a chance to print; else still 'awaiting'
        window_elapsed = (fill_last - start + 1) >= FILL_WINDOW_SESSIONS
        return {"R": None, "r9": None, "r20": None, "r50": None, "matured": False, "exit": None,
                "status": "no-fill" if window_elapsed else "awaiting", "fi": None}
    emas = {n: _ema_series(closes, n) for n in (9, 20, 50)}
    results = {}
    for n in (9, 20, 50):
        trail = emas[n]
        rr, reason, matured, exj = None, "open", (end - fi) >= 5, end
        for j in range(fi, end + 1):
            b = bars[j]
            if b["low"] <= stop:                          # hard stop (model gap-throughs at the open)
                px = b["open"] if b["open"] < stop else stop
                rr, reason, matured, exj = round((px - entry) / risk, 2), "stop", True, j
                break
            if b["close"] < trail[j] and j > fi:          # trailing-EMA exit only AFTER the fill day
                rr, reason, matured, exj = round((b["close"] - entry) / risk, 2), f"{n}ema", True, j
                break
        if rr is None:
            rr = round((bars[end]["close"] - entry) / risk, 2)
        results[n] = {"R": rr, "exit": reason, "matured": matured, "exj": exj}
    pn = 50 if patient else 20                             # config-E primary trail
    pr = results[pn]
    return {"R": pr["R"], "exit": pr["exit"], "matured": pr["matured"],
            "status": "matured" if pr["matured"] else "open",
            "r9": results[9]["R"], "r20": results[20]["R"], "r50": results[50]["R"],
            "fi": fi, "fill_date": bars[fi]["time"], "exit_date": bars[pr["exj"]]["time"],
            "hold": pr["exj"] - fi, "primary_exit_n": pn}


def _forward_plans(pick):
    """The entry legs to simulate for a forward pick: the stored `entries` (pullback + breakout, the
    same both-leg plan shown on the dashboard) if present, else the single primary entry — so snapshots
    frozen before both-leg tracking still score exactly as before."""
    plans = [e for e in (pick.get("entries") or []) if e and e.get("entry") and e.get("stop")]
    if not plans:
        plans = [{"kind": "breakout" if pick.get("entry_type") == "stop" else "pullback",
                  "entry_type": pick.get("entry_type"), "entry": pick.get("entry"),
                  "stop": pick.get("stop")}]
    return plans


def _sim_forward_best(bars, trade_date, pick):
    """Simulate EVERY entry leg and return (plan, result) for the one that actually FILLED — so a name
    that never gave its pullback but broke out and ran is scored on the breakout leg instead of being
    recorded as 'no-fill' (the forward test used to undercount up-day winners). If several legs filled,
    take the one that triggered FIRST (the entry you'd realistically have been in); ties favor the
    primary leg. If none filled, return the primary leg's result (awaiting / no-fill)."""
    sims = []
    for pl in _forward_plans(pick):
        r = _sim_forward(bars, trade_date, pl.get("entry"), pl.get("stop"),
                         pl.get("entry_type"), pick.get("setup_type"))
        if r:
            sims.append((pl, r))
    if not sims:
        return None, None
    filled = [(pl, r) for pl, r in sims if r.get("fi") is not None]
    if filled:
        return min(filled, key=lambda x: x[1]["fi"])      # earliest fill; primary wins a tie (stable order)
    return sims[0]                                          # nothing filled → primary's awaiting/no-fill


def _trail_results(bars, fi, entry, stop, patient):
    """Manage a filled trade forward from bar `fi`: exit on a daily close < the trailing EMA or the stop.
    Returns R under all three trails (9/20/50); the PRIMARY R trails the 20-EMA (50 for patient leaders)."""
    risk = entry - stop
    if risk <= 0:
        return None
    closes = [b["close"] for b in bars]
    emas = {n: _ema_series(closes, n) for n in (9, 20, 50)}
    end = min(len(bars) - 1, fi + 60)
    results = {}
    for n in (9, 20, 50):
        trail = emas[n]
        rr, reason, matured, exj = None, "open", (end - fi) >= 5, end
        for j in range(fi, end + 1):
            b = bars[j]
            if b["low"] <= stop:
                px = b["open"] if b["open"] < stop else stop
                rr, reason, matured, exj = round((px - entry) / risk, 2), "stop", True, j
                break
            if b["close"] < trail[j] and j > fi:
                rr, reason, matured, exj = round((b["close"] - entry) / risk, 2), f"{n}ema", True, j
                break
        if rr is None:
            rr = round((bars[end]["close"] - entry) / risk, 2)
        results[n] = {"R": rr, "exit": reason, "matured": matured, "exj": exj}
    pn = 50 if patient else 20
    pr = results[pn]
    return {"R": pr["R"], "exit": pr["exit"], "matured": pr["matured"],
            "status": "matured" if pr["matured"] else "open",
            "r9": results[9]["R"], "r20": results[20]["R"], "r50": results[50]["R"],
            "fi": fi, "entry": round(entry, 2), "stop": round(stop, 2),
            "fill_date": bars[fi]["time"], "exit_date": bars[pr["exj"]]["time"], "hold": pr["exj"] - fi}


def _clamp_stop(entry, lod, adr_pct):
    """Day-low stop, risk clamped 0.3–1× ADR (the live ≤1× rule)."""
    raw = entry - lod
    adr_px = (entry * adr_pct / 100) if adr_pct else None
    risk = min(max(raw, 0.3 * adr_px), 1.0 * adr_px) if adr_px else raw
    return entry - risk if risk > 0 else None


def _sim_confirmation(bars, trade_date, adr_pct, setup_type=None):
    """The CONFIRMATION entry — the daily proxy for the live 5-min opening-range-high break: buy when a
    session at/after trade_date TAKES OUT the prior session's high (rotation up); stop = that day's low
    (≤1× ADR). This is what the live coach does. None within FILL_WINDOW = no confirmation = no trade."""
    after = [i for i, b in enumerate(bars) if b["time"] >= trade_date]
    if not after or after[0] == 0:
        return {"R": None, "r9": None, "r20": None, "r50": None, "matured": False, "exit": None, "status": "awaiting", "fi": None}
    start = after[0]
    trigger = bars[start - 1]["high"]                       # prior-day high = the setup-day high
    fill_last = min(len(bars) - 1, start + FILL_WINDOW_SESSIONS - 1)
    fi = next((j for j in range(start, fill_last + 1) if bars[j]["high"] >= trigger), None)
    if fi is None:
        elapsed = (fill_last - start + 1) >= FILL_WINDOW_SESSIONS
        return {"R": None, "r9": None, "r20": None, "r50": None, "matured": False, "exit": None,
                "status": "no-fill" if elapsed else "awaiting", "fi": None}
    entry = bars[fi]["open"] if bars[fi]["open"] >= trigger else trigger    # gap-through fills at the open
    stop = _clamp_stop(entry, bars[fi]["low"], adr_pct)
    patient = (setup_type or "").strip().lower() in ("deep pullback", "consolidation")
    r = _trail_results(bars, fi, entry, stop, patient) if stop else None
    return r or {"R": None, "r9": None, "r20": None, "r50": None, "matured": False, "exit": None, "status": "no-fill", "fi": None}


def _sim_touch(bars, trade_date, limit, adr_pct, setup_type=None):
    """The TOUCH entry (comparison) — buy the dip: fill when a session trades DOWN to the pullback limit
    (low ≤ limit), stop = that day's low (≤1× ADR). Recorded alongside the confirmation so LIVE forward
    data decides which entry actually wins (no dogma, no survivorship-biased backtest)."""
    if not limit or limit <= 0:
        return None
    after = [i for i, b in enumerate(bars) if b["time"] >= trade_date]
    if not after:
        return {"R": None, "matured": False, "status": "awaiting", "fi": None}
    start = after[0]
    fill_last = min(len(bars) - 1, start + FILL_WINDOW_SESSIONS - 1)
    fi = next((j for j in range(start, fill_last + 1) if bars[j]["low"] <= limit), None)
    if fi is None:
        elapsed = (fill_last - start + 1) >= FILL_WINDOW_SESSIONS
        return {"R": None, "matured": False, "status": "no-fill" if elapsed else "awaiting", "fi": None}
    entry = min(limit, bars[fi]["open"])                    # gap-down fills at the open
    stop = _clamp_stop(entry, bars[fi]["low"], adr_pct)
    patient = (setup_type or "").strip().lower() in ("deep pullback", "consolidation")
    r = _trail_results(bars, fi, entry, stop, patient) if stop else None
    return r or {"R": None, "matured": False, "status": "no-fill", "fi": None}


def _day_lesson(picks):
    """A short, data-driven takeaway for ONE day's top picks: how they did + which trait carried an
    edge (Trend Template / VCP / buyable-now / setup type). Honest when too few have matured."""
    scored = [p for p in picks if p.get("R") is not None]
    if len(scored) < 3:
        awaiting = sum(1 for p in picks if p.get("fstatus") == "awaiting")
        if awaiting:
            return ("The top setups to act on this session, frozen from the prior close. Each fills on its "
                    "trigger as the session trades, then we score it (9-EMA close / stop); the status updates "
                    "every market close until it matures. Awaiting this session's data.")
        return "Too early — most of these setups haven't filled/matured yet. Status updates each market close."
    rs = [p["R"] for p in scored]
    avg = sum(rs) / len(rs)
    best = max(scored, key=lambda p: p["R"])
    worst = min(scored, key=lambda p: p["R"])
    head = (f"Avg {avg:+.1f}R across {len(scored)} scored. "
            f"Best {best['ticker']} {best['R']:+.1f}R ({best.get('setup_type')}), "
            f"worst {worst['ticker']} {worst['R']:+.1f}R.")
    notes = []
    for key, label in [("trend_template", "✓ Trend-Template"), ("vcp", "🌀 VCP"), ("buyable_now", "🟢 Buyable-now")]:
        a = [p["R"] for p in scored if p.get(key)]
        b = [p["R"] for p in scored if not p.get(key)]
        if len(a) >= 2 and len(b) >= 2:
            da, db = sum(a) / len(a), sum(b) / len(b)
            if abs(da - db) >= 0.4:
                notes.append(f"{label} {da:+.1f}R vs {db:+.1f}R without — "
                             f"{'added edge' if da > db else 'HURT here'}.")
    # which setup type led
    bytype = {}
    for p in scored:
        bytype.setdefault(p.get("setup_type") or "?", []).append(p["R"])
    if len(bytype) >= 2:
        ranked = sorted(((sum(v) / len(v), k, len(v)) for k, v in bytype.items()), reverse=True)
        top, bot = ranked[0], ranked[-1]
        if top[0] - bot[0] >= 0.5:
            notes.append(f"{top[1]} led ({top[0]:+.1f}R), {bot[1]} lagged ({bot[0]:+.1f}R).")
    return head + (" " + " ".join(notes[:2]) if notes else "")


def _agg_rs(rows, rkey="R"):
    """Aggregate a set of evaluated picks on an R key (R / r9 / r20 / r50)."""
    rs = [t[rkey] for t in rows if t.get(rkey) is not None]
    if not rs:
        return None
    wins = [x for x in rs if x > 0]
    return {"n": len(rs), "avg_r": round(sum(rs) / len(rs), 2),
            "win_rate": round(100 * len(wins) / len(rs)),
            "pct_gt1R": round(100 * sum(1 for x in rs if x >= 1) / len(rs)),
            "total_r": round(sum(rs), 1)}


def _dedupe_forward(rows):
    """One position per name at a time. Sort matured fills by fill date; keep a fill only if we're not
    already in that name (its fill is on/after the prior kept trade's exit). Collapses the same real trade
    appearing across consecutive daily snapshots so the aggregate isn't inflated."""
    out, held = [], {}                                     # held[ticker] = exit_date of the last kept trade
    for t in sorted(rows, key=lambda x: (x.get("fill_date") or x.get("date") or "", x.get("ticker") or "")):
        tk = t.get("ticker")
        fd = t.get("fill_date") or t.get("date")
        if tk in held and fd and held[tk] and fd < held[tk]:
            continue                                       # still in a prior position on this name — skip dup
        held[tk] = t.get("exit_date") or fd
        out.append(t)
    return out


def _fwd_rs_bucket(v):
    v = v or 0
    return "RS 90+" if v >= 90 else ("RS 75-89" if v >= 75 else "RS <75")


def _fwd_regime_bucket(p):
    p = 55 if p is None else p
    return "bull (55+)" if p >= 55 else ("soft (45-54)" if p >= 45 else "correction (<45)")


def _forward_eval_pick(p, date, posture, bars_cache):
    """Run ONE logged pick forward (whichever entry leg fills), enriched with the multi-exit R + tags."""
    t = p["ticker"]
    if t not in bars_cache:
        bars_cache[t] = scanner.get_bars(t)
    bars = bars_cache[t]
    adr_pct = p.get("adr") or 0
    r = _sim_confirmation(bars, date, adr_pct, p.get("setup_type")) if bars else None
    # TOUCH entry recorded ALONGSIDE (the buy-the-dip alternative) so live data picks the winner. The limit
    # = the pullback leg's entry (the support zone); pure breakouts have no touch leg → None.
    touch_limit = next((leg.get("entry") for leg in (p.get("entries") or [])
                        if leg.get("entry_type") == "limit" and leg.get("entry")), None)
    if touch_limit is None and p.get("entry_type") == "limit":
        touch_limit = p.get("entry")
    tr = _sim_touch(bars, date, touch_limit, adr_pct, p.get("setup_type")) if (bars and touch_limit) else None
    status = r["status"] if r else "no-data"
    filled = bool(r and r.get("fi") is not None)
    out = {**p, "posture": posture}
    if filled:                                            # the CONFIRMATION fill drives R/exit/levels
        out.update({"entry": r.get("entry"), "stop": r.get("stop"),
                    "entry_type": "confirmation", "filled_kind": "confirmation"})
    # the two entries, recorded side by side (primary-exit R) for the live confirmation-vs-touch comparison
    out["conf_r"] = r.get("R") if r else None
    out["conf_matured"] = bool(r and r.get("matured"))
    out["touch_r"] = tr.get("R") if tr else None
    out["touch_matured"] = bool(tr and tr.get("matured"))
    out["touch_status"] = tr.get("status") if tr else None
    cur = bars[-1]["close"] if bars else None
    entry = out.get("entry")
    progress = round((cur - entry) / entry * 100, 1) if (cur and entry) else None
    out.update({"R": r["R"] if r else None,
                "r9": r.get("r9") if r else None, "r20": r.get("r20") if r else None,
                "r50": r.get("r50") if r else None,
                "matured": bool(r and r["matured"]), "exit": r["exit"] if r else None,
                "fstatus": status, "cur": cur, "progress_pct": progress,
                "fill_date": r.get("fill_date") if r else None,
                "exit_date": r.get("exit_date") if r else None, "hold": r.get("hold") if r else None})
    return out


def _forward_dimensions(rows):
    """Break evaluated picks down by setup / grade / regime / RS / entry-leg, plus an exit-rule
    comparison (9 vs 20 vs 50 EMA on the SAME matured trades) — the 'learn from everything' analytics."""
    m = [r for r in rows if r.get("R") is not None]

    def grp(keyfn):
        d = {}
        for r in m:
            k = keyfn(r)
            if k is None:
                continue
            d.setdefault(k, []).append(r)
        return {k: a for k, v in d.items() if (a := _agg_rs(v))}

    exits = {}
    for n, key in ((9, "r9"), (20, "r20"), (50, "r50")):
        a = _agg_rs(m, key)
        if a:
            exits[f"{n}-EMA"] = a
    return {"by_setup": grp(lambda r: r.get("setup_type")),
            "by_grade": grp(lambda r: r.get("grade")),
            "by_regime": grp(lambda r: _fwd_regime_bucket(r.get("posture"))),
            "by_rs": grp(lambda r: _fwd_rs_bucket(r.get("rs_score"))),
            "by_entry": grp(lambda r: r.get("filled_kind")),
            "by_exit": exits}


def _forward_day_report(date, snap, bars_cache):
    """Full report for ONE logged day: every tracked setup's forward result + a day summary +
    per-day dimension breakdowns + a plain-English lesson."""
    posture = snap.get("posture")
    picks_out = [_forward_eval_pick(p, date, posture, bars_cache) for p in snap.get("picks", [])]
    day_rs = [x["R"] for x in picks_out if x["R"] is not None]
    wins = [x for x in day_rs if x > 0]
    day_sum = {"n_setups": len(picks_out), "n_scored": len(day_rs),
               "avg_r": round(sum(day_rs) / len(day_rs), 2) if day_rs else None,
               "win_rate": round(100 * len(wins) / len(day_rs)) if day_rs else None,
               "total_r": round(sum(day_rs), 1) if day_rs else None,
               "matured": sum(1 for x in picks_out if x["matured"]),
               "open": sum(1 for x in picks_out if x["fstatus"] == "open"),
               "awaiting": sum(1 for x in picks_out if x["fstatus"] == "awaiting"),
               "no_fill": sum(1 for x in picks_out if x["fstatus"] == "no-fill")}
    return {"date": date, "posture": posture, "regime": _fwd_regime_bucket(posture),
            "picks": sorted(picks_out, key=lambda x: -(x.get("rating") or 0)),
            "summary": day_sum, "dims": _forward_dimensions(picks_out),
            "lesson": _day_lesson(picks_out)}


def forward_day(date):
    """One day's full forward report (for the calendar drill-down). Fresh bars cache per call."""
    snaps = read_json(forward_f(), {"snapshots": {}}).get("snapshots", {})
    if date not in snaps:
        return {"date": date, "error": "no snapshot for that day", "picks": []}
    return _forward_day_report(date, snaps[date], {})


def score_forward(include_picks=False):
    """Overview across ALL logged days: per-day summaries (the calendar grid), an overall aggregate, and
    the global learning dimensions. Each pick is simulated forward (fill on its trigger, exit on a daily
    close < the trailing EMA — primary 20-EMA, 50 for patient leaders — or a stop). `include_picks` adds
    every day's full pick list (heavy); the calendar uses the lightweight default + /api/forward/day."""
    log = read_json(forward_f(), {"snapshots": {}})
    snaps = log.get("snapshots", {})
    bars_cache = {}
    by_day, all_matured, compare_pool = [], [], []
    pending = 0
    for date in sorted(snaps, reverse=True):
        rep = _forward_day_report(date, snaps[date], bars_cache)
        for p in rep["picks"]:
            if p.get("R") is not None and p.get("matured"):
                all_matured.append({**p, "date": date})
            elif p.get("fstatus") == "open":
                pending += 1
            compare_pool.append({**p, "date": date})        # for the confirmation-vs-touch entry comparison
        if not include_picks:
            rep = {k: v for k, v in rep.items() if k not in ("picks", "dims")}  # light for the grid
        by_day.append(rep)
    # CONFIRMATION vs TOUCH — both entries recorded per setup; live data decides which wins (same basis).
    conf_rows = _dedupe_forward([{**p, "R": p.get("conf_r")} for p in compare_pool if p.get("conf_matured")])
    touch_rows = _dedupe_forward([{**p, "R": p.get("touch_r")} for p in compare_pool if p.get("touch_matured")])
    entry_compare = {"confirmation": _agg_rs(conf_rows), "touch": _agg_rs(touch_rows)}
    # DEDUPE one position per name at a time: the same name is snapshotted every day it stays a setup, so
    # one real fill can appear across several overlapping snapshots. Walk fills chronologically and skip a
    # name while we're already "in" it (its fill is before the prior trade's exit) — mirrors the backtest's
    # open_until, so the aggregate counts each actual trade once.
    all_matured = _dedupe_forward(all_matured)
    agg = _agg_rs(all_matured)
    if agg:
        agg["win_rate"] = round(agg["win_rate"], 1)
    dims = _forward_dimensions(all_matured)
    days = len(snaps)
    total_logged = sum(len(s.get("picks", [])) for s in snaps.values())
    recent = sorted(all_matured, key=lambda x: x["date"], reverse=True)[:20]
    return {"days_logged": days, "total_picks": total_logged, "matured": len(all_matured),
            "pending": pending, "aggregate": agg, "recent": recent, "by_day": by_day,
            "by_grade": dims["by_grade"], "by_setup": dims["by_setup"], "dims": dims,
            "entry_compare": entry_compare,
            "first_date": min(snaps) if snaps else None, "last_date": max(snaps) if snaps else None}


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
            f"- Result: {res}  exit {t.get('exit')}" + (f" on {t['exit_at']}" if t.get("exit_at") else ""),
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
def run_scan(screener_id, max_age=12):
    global SCAN
    screeners = read_json(screeners_f(), [])
    sc = next((s for s in screeners if s["id"] == screener_id), None)
    if not sc:
        SCAN.update(running=False)
        return
    tickers = sc["tickers"]
    settings = read_json(settings_owner_f(), {})
    SCAN.update(running=True, done=0, total=len(tickers), current="",
                screener_id=screener_id, finished_at=None)

    def prog(done, total, t):
        SCAN.update(done=done, total=total, current=t)

    out = scanner.scan(tickers, settings, prog, max_age=max_age, market=market())
    prev = {i["ticker"]: i for i in read_json(suggest_f(), {}).get("items", [])}
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
    write_json(suggest_f(), {"scanned_at": out["scanned_at"], "screener_id": screener_id,
                           "screener_name": sc["name"], "failed": out["failed"],
                           "hot_sectors": hot, "items": items})
    try:
        if _after_close_today():      # freeze the forward snapshot ONLY from a post-close scan (the
            log_forward_picks()       # CLOSING picture) — never from a mid-session/pre-market re-scan,
                                      # which would regenerate the frozen record with intraday data
    except Exception:
        pass
    SCAN.update(running=False, finished_at=out["scanned_at"], current="")


def run_intraday_partial(n=220, max_age=0.05):
    """Fast intraday refresh: re-scan only the TOP-N graded names (the actionable pool) with FRESH bars and
    merge them back into suggestions.json. A full 800 re-scan is sequential (~4 min), too slow to run every
    few minutes; the top names are where new A/A+ setups actually come from, so refreshing just those (~1 min)
    keeps the live confirmation engine current at a 5-min cadence. The periodic loop still runs an occasional
    FULL scan to discover names from the deeper pool. SCAN's running flag is managed by the caller."""
    cur = read_json(suggest_f(), {"items": []})
    items = cur.get("items", [])
    if not items:
        return
    top = [it["ticker"] for it in sorted(items, key=lambda r: r.get("score", 0), reverse=True)[:n]]
    settings = read_json(settings_owner_f(), {})
    out = scanner.scan(top, settings, None, max_age=max_age, market=market())
    fresh = {r["ticker"]: r for r in out["results"]}
    merged = []
    for it in items:
        r = fresh.get(it["ticker"])
        if r:
            r["status"] = it.get("status", "pending")        # preserve the user's overlay marks
            r["catalyst"] = it.get("catalyst", "")
            merged.append(r)
        else:
            merged.append(it)                                # outside the refreshed set → keep as-is
    attach_sectors(merged)
    hot = compute_hot_sectors(merged)
    for it in merged:
        it["sector_hot"] = it.get("sector") in hot
    for r in fresh.values():                                 # re-apply the hot-sector score bump to refreshed names
        if r.get("sector_hot"):
            r["score"] = round(r.get("score", 0) + 1.5, 1)
    merged.sort(key=lambda r: r.get("score", 0), reverse=True)
    cur["items"] = merged
    cur["scanned_at"] = out["scanned_at"]
    write_json(suggest_f(), cur)


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
    # learning-loop activation: the ±8 realized-results nudge fires per setup at ≥5 CLOSED trades. Surface
    # how close each setup is so the user knows the grader is data-waiting, not broken.
    out["activation"] = {stp: {"closed": v["n"], "active": v["n"] >= 5, "to_go": max(0, 5 - v["n"])}
                         for stp, v in out["by_setup"].items()}
    out["activation_threshold"] = 5
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
    entries = s.get("entries") or []
    return {"ticker": s["ticker"], "grade": s.get("grade"), "setup_type": s.get("setup_type"),
            "theme": s.get("theme"), "entry": s.get("entry"), "entry_type": s.get("entry_type"),
            "trigger_note": (entries[0].get("trigger_note") if entries else None),
            "entries": [{"kind": e.get("kind"), "entry_type": e.get("entry_type"),
                         "entry": e.get("entry"), "trigger_note": e.get("trigger_note")}
                        for e in entries],
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

    market = _effective_regime()
    posture = market.get("posture", 55)
    label = market.get("label", "")
    indexes = market.get("indexes", [])
    stretched = [i["name"] for i in indexes if i.get("stretched_50")]
    regime_live = bool(market.get("live"))

    sug_items = read_json(suggest_f(), {}).get("items", [])
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
    if regime_live:                       # pre/after-hours — call out the indexes that are moving now
        movers = sorted([i for i in indexes if i.get("ext_pct") is not None],
                        key=lambda i: -abs(i["ext_pct"]))
        big = [f"{i['name']} {i['ext_pct']:+.1f}%" for i in movers if abs(i["ext_pct"]) >= 0.3][:3]
        if big:
            ms = market.get("market_state")
            when = "Pre-market" if ms in ("PRE", "PREPRE") else "After-hours"
            stance += f" · 🌙 {when}: {', '.join(big)} — this regime read uses the live extended-hours prices"

    manage = []
    for t in open_pos:
        co = t.get("coach") or {}
        manage.append({"ticker": t["ticker"], "action": co.get("action", "HOLD"),
                       "tone": co.get("tone", "good"),
                       "reason": (co.get("reasons") or [""])[0], "pnl_pct": t.get("pnl_pct")})
    todo = [m for m in manage if m["action"] in ("EXIT", "TRIM", "RAISE STOP", "GUARD STOP")]

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
            "regime_live": regime_live, "market_state": market.get("market_state"),
            "exposure": exposure, "manage": manage, "buy_now": buy_now, "watch": watch,
            "avoid": avoid, "alerts": read_json(news_f(), {}).get("alerts", [])[:4],
            "lessons": _top_lessons(3), "bottom_line": bottom}


# --------------------------------------------------------------------------- #
# "Now" — the minimalist, decisive view: one screen that says what to DO right now.
# Synthesizes regime + Fear&Greed + prediction + open-position coach + graded setups into a
# stance light, the position actions that NEED doing, and the single best CONFIRMED buy (or an
# honest "sit tight"). The whole point is to remove decision fatigue — fewer choices, one call.
# --------------------------------------------------------------------------- #
def _plan_for(setup_type, zone, orh, stop, sized, triggered, too_extended):
    """The per-SETUP plan (the 📋). The intraday TIMING trigger is the same across Qulla's setups (the
    opening-range-high break — that's his actual method, verified), but the LEVEL you're breaking, the
    stop, the volume gate and the trail DIFFER by setup — so each plan reads specifically, not generically."""
    st = (setup_type or "").strip()
    z = f"${zone}" if zone else "the support zone"
    orh_txt = f"${orh}" if orh else "the 5-min opening-range high"
    size_txt = (f"{sized.get('shares')} sh · ≈{sized.get('risk_pct_actual')}% risk (1/3 test size)"
                if sized.get("shares") else "Sized live on the break.")
    stopd = f"${stop}" if stop else None

    def buy(confirmed_line, wait_line):
        if triggered:
            return confirmed_line
        if too_extended:
            return "Broke the level, but the day-low stop is wider than 1× ADR — too extended, skipping."
        return wait_line

    if st == "Episodic Pivot":
        return {"watch": "Fresh-news gap-up. VOLUME is the #1 thing — needs ≈ a full day's volume in the first 15–20 min.",
                "trigger": buy(f"CONFIRMED — took out the opening-range high {orh_txt} ON massive volume.",
                               "BUY the 5-min opening-range-high break — ONLY with massive volume (≈ ADV in the first 15–20 min). No volume = no trade."),
                "stop": (f"{stopd} — day low." if triggered else "Day low at the break (≤1–1.5× ADR)."),
                "size": size_txt, "target": "Trim ~2R into strength, trail the 20-EMA.",
                "invalidate": "Loses the day low → out. If the volume never shows, there's no trade."}
    if st == "Breakout":
        return {"watch": f"Tight consolidation — the pivot (base high) is ~{z}. Don't pre-buy inside the base.",
                "trigger": buy(f"CONFIRMED — broke the pivot, took out the opening-range high {orh_txt}.",
                               "BUY the break of the pivot — confirmed by the 5-min opening-range-high break (range expansion filters false breaks)."),
                "stop": (f"{stopd} — day low." if triggered else "Day low at the break (≤1× ADR; if wider, skip)."),
                "size": size_txt, "target": "Trim ~2R, trail the 20-EMA.",
                "invalidate": "Falls back through the day low after entry — winning horses don't back up — out."}
    if st == "Consolidation":
        return {"watch": f"Strong stock basing on the 50 EMA. Wait for the break UP through the overhead EMAs — don't buy under them.",
                "trigger": buy("CONFIRMED — broke ABOVE the overhead EMAs (cleared the resistance), holding above.",
                               "BUY the break THROUGH the overhead EMAs (clear the 9/21 cluster) — stop just under them, room above. Don't buy below the EMAs."),
                "stop": (f"{stopd} — just under the EMA cluster you cleared." if triggered else "Just under the EMA cluster (tight, ≤1× ADR)."),
                "size": size_txt, "target": "Patient hold — trail the 50-EMA (exit on a close under the 50, not the 9).",
                "invalidate": "Falls back under the cluster you cleared — the break failed — out."}
    if st == "Deep Pullback":
        return {"watch": f"Strong ~6-month leader that pulled to the 50. Buy the BREAK back up through the 9/21 EMAs — not a reclaim below them.",
                "trigger": buy("CONFIRMED — broke ABOVE the overhead EMAs (cleared the 9/21), holding above.",
                               "BUY the break THROUGH the 9/21 EMAs (clear the overhead cluster) — tight stop just under, clear room above. Going back DOWN to the 50 = buyers lost power, not an entry."),
                "stop": (f"{stopd} — just under the EMA cluster you cleared." if triggered else "Just under the 9/21 cluster (tight, ≤1× ADR)."),
                "size": size_txt, "target": "Patient hold — trail the 50-EMA (exit only on a close under the 50).",
                "invalidate": "Falls back under the cluster you cleared — the break failed — out."}
    if "AVWAP" in st:
        anchor = "ATH" if "ATH" in st else ("earnings gap" if "earn" in st.lower() else "anchor")
        return {"watch": f"Pullback to the AVWAP — the institutional cost basis from the {anchor} (~{z}). Let it hold the line.",
                "trigger": buy(f"CONFIRMED — reclaimed the AVWAP, took out the opening-range high {orh_txt}.",
                               "BUY the RECLAIM — price holds back above the AVWAP and takes out the 5-min opening-range high (buyers defending the cost basis)."),
                "stop": (f"{stopd} — below the AVWAP / day low." if triggered else "Below the AVWAP / day low (≤1× ADR)."),
                "size": size_txt, "target": "Trim ~2R, trail the 20-EMA.",
                "invalidate": "Loses the AVWAP on a closing basis — out."}
    # default = Pullback to the 10/20-day EMA near the highs
    return {"watch": f"Pullback to the rising 10/20-EMA (~{z}) — WATCH here; let it hold, don't buy the falling touch.",
            "trigger": buy(f"CONFIRMED — rotated up, took out the opening-range high {orh_txt}.",
                           "BUY the rotation UP — when it reclaims and takes out the prior-day / 5-min opening-range high off the EMA (buyers stepping back in)."),
            "stop": (f"{stopd} — below the pullback low / day low." if triggered else "Below the pullback low / day low (≤1× ADR)."),
            "size": size_txt, "target": "Trim ~2R, trail the 20-EMA.",
            "invalidate": "Falls back through the day low after entry — winning horses don't back up — out."}


_NOW_CACHE = {}    # per-market 12s cache: the panel polls /api/now every 20s + the dashboard ~45s; without
                   # this, grade_suggestions(~800) re-ran on every poll (a recurring CPU spike). Quotes are
                   # already 30s-cached, so a 12s result cache costs no freshness but kills the repeated grind.


def _overhead_res(entry, s, adr_pct):
    """Nearest resistance just ABOVE the entry — a faster moving average you'd buy straight into (10/20/50
    EMA) or a recent swing high. Returns (res|None, tight). `res` only when a level sits within 0.6× ADR
    overhead; `tight` = within 0.25× ADR (right on top → not a clean entry). Measured in ADR so it scales
    with the stock's volatility. (The user's BE case: reclaimed the 50 EMA but the 10 EMA was ~1.5% above.)"""
    adr_px = (entry * adr_pct / 100) if (entry and adr_pct) else None
    if not adr_px or not entry:
        return None, False
    cands = [("9 EMA", s.get("ema9") or s.get("ema10")), ("21 EMA", s.get("ema21") or s.get("ema20")),
             ("50 EMA", s.get("ema50")), ("recent high", s.get("prior_high") or s.get("prev_high"))]
    above = [(lab, lv) for lab, lv in cands if lv and lv > entry * 1.0015]   # strictly overhead only
    if not above:
        return None, False
    lab, lv = min(above, key=lambda x: x[1])                  # the NEAREST level above the entry
    dist_adr = (lv - entry) / adr_px
    if dist_adr > 0.6:
        return None, False                                   # enough room to the next level — not a concern
    res = {"label": lab, "level": round(lv, 2), "dist_pct": round((lv / entry - 1) * 100, 2),
           "dist_adr": round(dist_adr, 2)}
    return res, dist_adr <= 0.25


def _best_stop(entry, s, b5, adr_pct, sbuf):
    """Find the TIGHTEST valid stop below the entry — the nearest REAL structure (a 9/21/50 EMA, the recent
    5-min swing low, or the day low) that sits at least ~0.3× ADR below (so normal noise doesn't stop you out)
    and no more than 1× ADR below. Minimizes risk while staying at real support — the user's 'find the best
    stop that risks as little as possible, not always the bottom of day'. Returns (stop, label, raw_level) or
    (None, None, None) if nothing valid (caller falls back / flags too-extended)."""
    if not entry or not adr_pct:
        return None, None, None
    adr_px = entry * adr_pct / 100
    floor_d, cap_d = 0.30 * adr_px, 1.0 * adr_px
    cands = [("9 EMA", s.get("ema9") or s.get("ema10")),
             ("21 EMA", s.get("ema21") or s.get("ema20")), ("50 EMA", s.get("ema50"))]
    if b5:
        cands.append(("recent 5-min low", min(b["low"] for b in b5[-6:])))
        cands.append(("day low", min(b["low"] for b in b5)))
    in_range = [(lab, lv) for lab, lv in cands if lv and floor_d <= (entry - lv) <= cap_d]
    if in_range:
        lab, lv = max(in_range, key=lambda x: x[1])          # the HIGHEST (tightest) valid support
    else:                                                    # nothing in the sweet spot → widest support ≤1× ADR
        within = [(lab, lv) for lab, lv in cands if lv and 0 < (entry - lv) <= cap_d]
        if not within:
            return None, None, None
        lab, lv = min(within, key=lambda x: x[1])
    return round(lv * (1 - (sbuf or 0) / 100), 2), lab, round(lv, 2)


def _resistance_entry(price, s, entry_buf):
    """The user's entry rule: buy the break THROUGH the overhead resistance, not a reclaim below it. Entry =
    just above the HIGHEST EMA sitting above the current price (clear ALL the near EMAs), with a tight stop
    just under the cluster. Returns (entry, stop, label) — or (None, None, None) when price is already above
    every EMA (no overhead → use the normal ORH/pivot break). VRT: clears the 9/21 (~$328); AAOI: clears
    them (~$181). Going back DOWN to the 50 after price left it = buyers lost power = not an entry."""
    # the 9/21/50 EMAs the trader reads (fall back to 10/20 until a re-scan populates 9/21)
    emas = [v for v in (s.get("ema9") or s.get("ema10"), s.get("ema21") or s.get("ema20"), s.get("ema50")) if v]
    above = [v for v in emas if price and v > price]
    if not above:
        return None, None, None
    entry = round(max(above) * (1 + (entry_buf or 0) / 100), 2)   # a little above the highest overhead EMA
    stop = round(min(above) * (1 - 0.003), 2)                     # just under the lowest cleared EMA → tight R
    n = len(above)
    return entry, stop, f"the {n} overhead EMA{'s' if n > 1 else ''}"


def compute_now(shared=False):
    # shared=True → the POSITION-AGNOSTIC view for Auto Pilot: ignore the owner's open positions and
    # ✗-rejections entirely, so the armed/confirmed list is IDENTICAL for everyone (the owner's local
    # preview matches what friends see). Without this, the owner's held names were filtered out of the
    # shared view via skip_buy, so local showed a shorter/different list than the hosted site.
    _mk = getattr(_ctx, "market", "us")    # NOT market() — `market` is shadowed by a local below
    _ck = _mk + (":shared" if shared else "")
    _c = _NOW_CACHE.get(_ck)
    if _c and time.time() - _c["t"] < 12:
        return _c["v"]
    settings = read_json(settings_f(), {})
    trades = [] if shared else enrich_trades(read_json(trades_f(), []))
    open_pos = [t for t in trades if t.get("status") == "open"]
    held = {t.get("ticker") for t in open_pos}
    # Only skip a name you currently HOLD (don't re-pitch what you're already in) or one you explicitly
    # PASSED (✗). A name you traded and CLOSED today (e.g. a stopped-out AAOI) stays eligible — if it sets
    # up and breaks again it's a valid re-entry, so it can re-arm and fire again. (Earlier this also excluded
    # closed-today trades; the user wants those back in play.) `taken` isn't skipped here — while the trade
    # is open it's already in `held`; once closed it should be eligible again. SHARED view skips nothing.
    _ov = {} if shared else read_json(status_f(), {})
    skip_buy = set(held) | {tk for tk, o in _ov.items()
                            if isinstance(o, dict) and o.get("status") == "rejected"}
    _today = now_date()    # used by the account/P&L block below (today's realized + open move)

    # LIVE OVERLAY (fixes "app stats wrong, website fine"): enrich_trades values R/P&L off the last DAILY
    # bar (12h cache). The website FRONTEND polls /api/live and overlays the live quote, so its positions
    # read live — but /api/now (the desktop app) did not, so the app's "I'm on it" R lagged the site
    # (INOD showed +0.42R off a stale $108 bar vs the site's +1.1R off the live $118). Overlay the live
    # quote here too — recompute last/P&L/R and re-run the coach off it — so the app matches the website.
    if open_pos and _us_session_active():
        try:
            _lq = scanner.fetch_quotes([t["ticker"] for t in open_pos])
        except Exception:
            _lq = {}
        _news_map = read_json(news_f(), {}).get("ticker_news", {})
        _rth = _rth_now()
        for t in open_pos:
            q = _lq.get(t["ticker"]) or {}
            px = q.get("price")
            if not px:
                continue
            e, sh, istop = t.get("entry"), t.get("shares"), t.get("initial_stop")
            t["last"] = px
            if e:
                t["pnl_pct"] = round((px / e - 1) * 100, 2)
            if e and sh:
                t["pnl"] = round((px - e) * sh, 2)
            if e and istop is not None and e > istop:
                t["r_open"] = round((px - e) / (e - istop), 2)
            # Is this a REGULAR-hours print? Only then can a stop/exit actually act. Prefer the quote's
            # own market_state; fall back to the clock. (MXL bug: an after-hours dip to $93.05 < the $93.20
            # stop auto-closed an OPEN position — but the broker stop is RTH-only, so the user was still in.)
            ms = q.get("market_state")
            is_reg = (ms == "REGULAR") if ms else _rth
            try:
                bars = scanner.get_bars(t["ticker"])
                if bars:                                     # swap today's close for the live print, re-coach
                    t["coach"] = position_coach(t, bars[:-1] + [{**bars[-1], "close": px}], settings, _news_map)
            except Exception:
                bars = None
            co = t.get("coach")
            if co and not is_reg:
                # EXTENDED hours: the live mark may be red, but it is NOT an exit/stop signal — exits are
                # decided on the daily CLOSE (16:00 ET), and a broker stop won't fill pre/post. Never let an
                # extended-hours tick fire a STOPPED OUT / auto-close / EXIT. Upside management is unaffected.
                t["_ext_hours"] = True
                co["stop_hit"] = False
                if co.get("action") == "EXIT":
                    co["action"], co["tone"] = "WATCH", "warn"
                    co["reasons"] = [f"under your stop/line in EXTENDED hours only — your stop is regular-hours, "
                                     f"so you're NOT out. Watch the regular session; exits confirm at the close."] \
                                    + (co.get("reasons") or [])

    # ACCOUNT / P&L summary for the app header (replaces the old verbose stance hero). Open P&L is live
    # (the overlay above set t['pnl']/t['last']); equity = base + realized + open; today = realized-today +
    # the open positions' move today (vs entry for names opened today, else vs yesterday's close).
    _base = settings.get("account_size") or 0
    _realized = sum((t["exit"] - t["entry"]) * t["shares"] for t in trades
                    if t.get("status") == "closed" and t.get("exit") and t.get("entry") and t.get("shares"))
    _open_pnl = sum((t.get("pnl") or 0) for t in open_pos)
    _day = sum((t["exit"] - t["entry"]) * t["shares"] for t in trades
               if t.get("exit_at") == _today and t.get("exit") and t.get("entry") and t.get("shares"))
    for t in open_pos:
        e, sh, last = t.get("entry"), t.get("shares"), t.get("last")
        if not (e and sh and last):
            continue
        if t.get("taken_at") == _today:
            _day += (last - e) * sh                          # opened today → from the fill
        else:
            try:
                _b = scanner.get_bars(t["ticker"])
                _prev = _b[-2]["close"] if len(_b) >= 2 else e
            except Exception:
                _prev = e
            _day += (last - _prev) * sh                      # held from before → today's mark-to-market move
    account = {"equity": round(_base + _realized + _open_pnl, 2), "base": round(_base, 2),
               "open": round(_open_pnl, 2), "realized": round(_realized, 2),
               "today": round(_day, 2), "positions": len(open_pos)}

    market = _effective_regime()
    posture = market.get("posture", 55)
    label = market.get("label", "")
    fg = market.get("fear_greed")
    try:
        pred = compute_prediction()
    except Exception:
        pred = {"daily": {}, "overall": {"state": ""}}
    overall_state = pred.get("overall", {}).get("state", "")
    daily = pred.get("daily", {})
    frothy = overall_state == "Extended / frothy — late-stage"

    # ---- stance light: should you be adding NEW risk at all? ----
    if posture < 45:
        light = "red"
        stance = f"Stand aside — weak tape ({label}). Protect capital; wait for a confirmed turn before new risk."
    elif frothy or posture < 55:
        light = "yellow"
        stance = ("Selective — extended / greedy tape. Only A-grade setups bought AT support; "
                  "do NOT chase breakouts — that's what's been costing you.")
    else:
        light = "green"
        stance = f"Green light — healthy tape ({label}). Take your A setups on confirmation."

    # ---- manage open positions: surface only what NEEDS attention (exits/trims/raises/watches) ----
    manage = []
    for t in open_pos:
        co = t.get("coach") or {}
        manage.append({"ticker": t["ticker"], "action": co.get("action", "HOLD"),
                       "tone": co.get("tone", "good"),
                       "reason": (co.get("reasons") or [""])[0],
                       "pnl_pct": t.get("pnl_pct"), "r_mult": co.get("r_mult"),
                       "stop_hit": co.get("stop_hit"), "stop": t.get("stop"), "last": t.get("last"),
                       "id": t.get("id")})
    # ONLY actions the user must take. WATCH = "I'm monitoring this" (earnings/news) — that's the
    # coach's job to watch, not a to-do for the user, so it folds into the quiet monitoring list, never
    # the action list. (User: "YOU watch the position, not me. Update only on ACTIONS I need to take.")
    todo = [m for m in manage if m["action"] in ("EXIT", "TRIM", "RAISE STOP", "GUARD STOP")]
    holds = [m for m in manage if m["action"] in ("HOLD", "WATCH")]

    # ---- the buy: A-grade, tape-appropriate, not held, no earnings — ALERT only once CONFIRMED ----
    # CONFIRMATION ENGINE (light by design): we shortlist the best ~12 A/A+ config-E setups, then live-quote
    # ONLY those (cached 30s, market-hours only) to detect the real intraday trigger. We NEVER live-watch the
    # whole universe — that's the daily forward test's job. A setup is CONFIRMED when:
    #   • breakout / EP  → price TAKES OUT the prior-day high (the Qulla/Luk "rotate above the prior-day high")
    #   • pullback family → price is back IN the support buy-zone (buy the reclaim of your limit)
    # Confirmed → it's a real "do this" buy call (beeps once). Not yet → it's ARMED (panel only, silent).
    # Grade PER ENTRY LEG, not the ticker headline: a name can headline B (the average of its legs) while its
    # PULLBACK leg is A (the JOBY case). We arm on the best A/A+ *leg*. A breakout leg is only A when the tape
    # is strong (the grade caps enforce config E), so the leg grade alone encodes the regime rule.
    def _size_leg(entry, stop):
        tmp = {"entry": entry, "risk_ps": (entry - stop) if (entry and stop and entry > stop) else 0}
        apply_sizing(tmp, settings)
        return tmp

    sug = read_json(suggest_f(), {}).get("items", [])
    graded = grade_suggestions(sug, settings) if sug else []
    active = _us_regular_open()                            # live BUY only in the regular cash session
    cands = []                                             # (suggestion, best A/A+ leg)
    if light != "red":
        for s in graded:
            if s["ticker"] in skip_buy or s.get("earnings_soon"):
                continue
            legs = [e for e in (s.get("entries") or [])
                    if e.get("entry") and e.get("stop") and not e.get("stale")
                    and e.get("grade") in ("A+", "A")]
            if not legs:
                continue
            best = sorted(legs, key=lambda e: e.get("rating", 0), reverse=True)[0]
            cands.append((s, best))
    cands.sort(key=lambda se: se[1].get("rating", 0), reverse=True)
    cands = cands[:18]                                     # shortlist cap — watch more names (5m bars are 2min-cached)
    # CONFIRMATION ENGINE (verified 2026-06-04 deep-research): a setup is a BUY the moment intraday price
    # TAKES OUT the OPENING-RANGE HIGH (high of the first 5-min candle). Stop = LOW OF DAY, capped ≤1× ADR
    # (if the day-low stop is wider than 1× ADR the name is too extended → skip, never widen). We pull 5-min
    # bars ONLY for the armed shortlist, ONLY in the regular session — never the whole universe.
    buys, armed = [], []
    for s, e in cands:
        leg_entry, leg_stop = e.get("entry"), e.get("stop")
        adr_pct = s.get("adr") or 0
        setup_type = s.get("setup_type") or ""
        sbuf = settings.get("stop_buffer_pct", 0.5) or 0
        ebuf = settings.get("entry_buffer_pct", 0.1)
        # THE ENTRY = a break ABOVE the overhead EMA cluster (clear all the near resistance), tight stop just
        # under it, room above — NOT a reclaim below the EMAs (the user's AAOI/VRT rule: once price left the
        # 50, going back down means buyers lost power; the next entry is THROUGH the 9/21 — VRT ~$328,
        # AAOI ~$181). If price is already above EVERY EMA there's no overhead → fall back to the ORH/pivot break.
        price = s.get("close") or leg_entry
        res_entry, res_stop, cleared = _resistance_entry(price, s, ebuf)
        oc, b5 = None, None
        if active:
            try:
                b5 = scanner.get_5m_today(s["ticker"])
                if b5 and res_entry:
                    oc = scanner.breakout_confirm(b5, res_entry, adr_pct)
                elif b5:
                    oc = scanner.orh_confirm(b5, buf_pct=ebuf, adr_pct=adr_pct)
            except Exception:
                oc, b5 = None, None
        triggered, too_extended, vol_wait, overhead = False, False, False, None
        entry, stop = (res_entry or leg_entry), (res_stop or leg_stop)
        orh = oc.get("orh") if oc else None
        lod = oc.get("lod") if oc else None
        if oc and oc.get("confirmed"):
            cur_px = b5[-1]["close"] if b5 else None          # the live price — where you'd actually buy NOW
            if res_entry:                                    # break ABOVE the EMA cluster (clear resistance)
                trig_lv = res_entry
                fb_stop = round(res_stop * (1 - sbuf / 100), 2) if (res_stop and sbuf) else res_stop
                trig_desc = f"broke above {cleared} (${_f(res_entry)})"
            else:                                            # price above all EMAs → ORH / pivot break
                trig_lv = orh
                fb_stop = round(lod * (1 - sbuf / 100), 2) if (lod and sbuf) else lod
                trig_desc = f"took out the opening-range high ${_f(orh)}"
            # ENTRY = the CURRENT price, not the stale trigger level (the move already fired). So a name that
            # broke its level hours ago and ran shows the buy where you'd ACTUALLY get in now (~$51 on VIAV,
            # not the $49.86 opening-range high). If buying here makes the stop wider than 1× ADR, it's a chase.
            cand_entry = max(cur_px or trig_lv, trig_lv) if trig_lv else cur_px
            # STOP = the tightest valid structure (nearest EMA / recent low ≥0.3× & ≤1× ADR), not always the
            # day low — minimize risk. Falls back to the trigger-based stop if nothing tighter is valid.
            _bs, stop_lab, _ = _best_stop(cand_entry, s, b5, adr_pct, sbuf)
            cand_stop = _bs if _bs is not None else fb_stop
            adr_px = (cand_entry * adr_pct / 100) if (cand_entry and adr_pct) else None
            raw_risk = (cand_entry - cand_stop) if (cand_entry and cand_stop) else None
            ep_ok = True                                     # EP must ALSO show massive open volume (#1 thing)
            if setup_type == "Episodic Pivot":
                ep_ok, _vr = scanner.ep_volume_ok(b5, s.get("avg_vol"))
            if adr_px and raw_risk and raw_risk > 1.0 * adr_px:
                too_extended = True                          # stop > 1× ADR → too extended, DON'T call
                cmsg = (f"{trig_desc.capitalize()}, but the stop ${_f(cand_stop)} is wider than 1× ADR — "
                        f"too extended. Skipping (don't widen the stop).")
            elif not ep_ok:
                vol_wait = True
                cmsg = (f"Broke the opening-range high ${_f(orh)} but EP volume is light (needs ≈ a day's "
                        f"volume in the first 15–20 min) — no call until the volume confirms.")
            else:
                # is there STILL a wall RIGHT above the entry (the next EMA / a swing high)? — no clean room.
                overhead, res_tight = _overhead_res(cand_entry, s, adr_pct)
                if res_tight:
                    cmsg = (f"{trig_desc.capitalize()}, but {overhead['label']} ${overhead['level']} is right "
                            f"overhead (+{overhead['dist_pct']}%) — no clean room. No call until it clears.")
                else:
                    triggered = True
                    entry, stop = cand_entry, cand_stop
                    warn = (f" ⚠ {overhead['label']} ${overhead['level']} still a bit overhead "
                            f"(+{overhead['dist_pct']}%)." if overhead else "")
                    stxt = f" (under the {stop_lab})" if stop_lab else ""
                    cmsg = f"CONFIRMED — {trig_desc}. Buy ~${_f(cand_entry)}, stop ${_f(cand_stop)}{stxt}.{warn}"
        elif res_entry:                                      # armed — waiting for the break above the EMAs
            if oc and oc.get("extended"):
                cmsg = (f"Already ran past the entry (${_f(res_entry)}) above the EMAs — no chase. "
                        f"Re-arms on a fresh break.")
            elif oc and oc.get("broke") and not oc.get("holding"):
                cmsg = (f"Tagged ${_f(res_entry)} above the EMAs then faded back under — no call. "
                        f"Fires again only on a clean break that HOLDS above the cluster.")
            else:
                cmsg = f"Armed — fires on a break above ${_f(res_entry)} (clears {cleared}, with room above)."
        else:                                                # armed, ORH-break setup (price already above EMAs)
            if oc and oc.get("extended"):
                cmsg = (f"Already ran past the opening-range high ${_f(orh)} — chasing it here means a stop "
                        f"wider than 1× ADR. No call; re-arms on a pullback or a fresh base.")
            elif oc and oc.get("broke") and not oc.get("holding"):
                cmsg = (f"Broke the opening-range high ${_f(orh)} then faded back below it (hit in the nose) — "
                        f"no call. Re-arms; fires again only if it RECLAIMS ${_f(orh)} cleanly.")
            elif orh:
                cmsg = f"Armed — fires when it takes out the 5-min opening-range high ${_f(orh)} (buyers stepping in)."
            else:
                cmsg = "Armed — fires on the opening-range-high break (buyers stepping in)."
        sized = _size_leg(entry, stop)
        # THE PLAN — per-setup (level / stop / volume-gate / trail differ by setup); 📋 icon opens it.
        plan = {**_plan_for(setup_type, leg_entry, orh, (stop if triggered else None),
                            sized, triggered, too_extended), "why": s.get("why")}
        rec = {"ticker": s["ticker"], "grade": e.get("grade"), "setup_type": s.get("setup_type"),
               "theme": s.get("theme"), "entry": entry, "stop": stop, "entry_type": e.get("entry_type"),
               "kind": e.get("kind"), "trigger": res_entry or orh or (s.get("prior_high") or leg_entry),
               "break_level": res_entry,    # the EMA-cluster break level (the armed/buy entry), if any
               "orh": orh, "lod": lod, "too_extended": too_extended, "vol_wait": vol_wait, "zone": leg_entry,
               "buyable_now": bool(e.get("buyable_now")),
               "trigger_note": e.get("trigger_note") or s.get("trigger_note"),
               "shares": sized.get("shares"), "dollar_risk": sized.get("dollar_risk"),
               "risk_pct_actual": sized.get("risk_pct_actual"), "pct_acct": sized.get("pct_acct"),
               "why": s.get("why"), "confirmed": triggered, "confirm": cmsg, "plan": plan,
               "overhead": overhead}
        (buys if triggered else armed).append(rec)

    # ---- the one-line VERDICT (the decisive bit) ----
    if todo:
        acts = "; ".join(f"{m['ticker']} → {m['action']}" for m in todo)
        verdict = f"Do this now — {acts}." + (f" Then, if you want: buy {buys[0]['ticker']} (confirmed)." if buys else "")
    elif buys:
        b = buys[0]
        verdict = (f"BUY {b['ticker']} — {b['shares']} sh @ ~${b['entry']}, stop ${b['stop']} "
                   f"({b.get('risk_pct_actual')}% risk). {b.get('confirm', '')}".strip())
    elif armed:
        a = armed[0]
        verdict = (f"Armed: {len(armed)} A-grade setup(s) lined up (best: {a['ticker']}). No call yet — "
                   f"I'll ping you the moment one confirms its trigger. Don't pre-empt it.")
    elif open_pos:
        verdict = (f"Nothing for you to do. I'm watching your {len(open_pos)} position(s) — "
                   f"I'll ping you the moment one needs an exit, trim, or stop-raise.")
    else:
        verdict = ("All clear — nothing worth buying yet. I'm watching the tape and I'll ping you only "
                   "when there's a confirmed A-grade entry. Cash is a position.")

    result = {"computed_at": time.strftime("%Y-%m-%d %H:%M"),
              "light": light, "stance": stance, "verdict": verdict,
              "todo": todo, "holds": holds, "buys": buys, "armed": armed,
              "posture": posture, "label": label, "fear_greed": fg,
              "market_state": market.get("market_state"),
              "daily_lean": daily.get("lean"), "daily_outlook": daily.get("outlook"),
              "overall_state": overall_state, "positions_count": len(open_pos), "account": account,
              "note": "Synthesized from market regime + Fear&Greed + your positions + graded setups. Not advice — the final call is yours."}
    _NOW_CACHE[_ck] = {"t": time.time(), "v": result}
    return result


# Personal fields that must NEVER reach the friends-facing Auto Pilot view.
_AUTOPILOT_STRIP = ("shares", "dollar_risk", "risk_pct_actual", "pct_acct")


def _autopilot_clean(rec):
    """Strip ALL personal sizing/risk from a setup record (and its plan) — Auto Pilot shows LEVELS ONLY
    (entry / stop / trigger / grade / why), per the user's spec. Leaves the educational setup mechanics."""
    out = {k: v for k, v in rec.items() if k not in _AUTOPILOT_STRIP}
    plan = out.get("plan")
    if isinstance(plan, dict):
        out["plan"] = {k: v for k, v in plan.items() if k != "size"}   # drop the "N sh · X% risk" line
    return out


def compute_autopilot():
    """The friends-facing, PERSONAL-DATA-FREE view of the confirmation engine — the website 'Auto Pilot'
    tab. Same ARMED (lined-up A-grade setups) + CONFIRMED (trigger taken out → buy) calls + tape stance the
    owner runs, but with NO positions, journal, account, or sizing. Reads ONLY shared data (suggestions +
    regime + live quotes), so it is safe to serve on the hosted build. v1 delivery = the browser keeps the
    tab open and polls this; the client alerts (sound + Notification) the moment a setup flips to confirmed."""
    n = compute_now(shared=True)   # position-agnostic: identical list for the owner's preview AND every friend
    buys = [_autopilot_clean(b) for b in n.get("buys", [])]
    armed = [_autopilot_clean(a) for a in n.get("armed", [])]
    # a verdict with NO personal references (compute_now's verdict mentions "your positions")
    if buys:
        verdict = f"🟢 {len(buys)} confirmed entr{'y' if len(buys) == 1 else 'ies'} — {buys[0]['ticker']} just triggered. Your call."
    elif armed:
        verdict = (f"{len(armed)} setup{'s' if len(armed) != 1 else ''} armed (best: {armed[0]['ticker']}). "
                   f"No buy yet — you'll be alerted the moment one confirms its trigger.")
    else:
        verdict = "Nothing armed right now — quiet or extended tape. Cash is a position."
    return {"computed_at": n.get("computed_at"), "updated_at": time.strftime("%H:%M:%S"),
            "light": n.get("light"), "stance": n.get("stance"), "verdict": verdict,
            "buys": buys, "armed": armed, "posture": n.get("posture"), "label": n.get("label"),
            "fear_greed": n.get("fear_greed"), "market_state": n.get("market_state"),
            "disclaimer": ("Not financial advice. Auto Pilot is an experimental work-in-progress that surfaces "
                           "educational momentum-setup ideas from a strategy — nothing is guaranteed and signals "
                           "can be wrong or mistimed. The final decision to take any trade is entirely yours, and "
                           "you trade at your own risk.")}


# --------------------------------------------------------------------------- #
# Prediction — a probabilistic forward read from all the data we have (NOT advice)
# --------------------------------------------------------------------------- #
def _lean_from_score(score):
    """Map a blended directional score to a forward lean label."""
    if score >= 2:
        return "Bullish"
    if score >= 0.7:
        return "Constructive"
    if score > -0.7:
        return "Neutral / chop"
    if score > -2:
        return "Cautious"
    return "Risk-off"


def compute_prediction():
    """Two reads: a DAILY guess (what's likely THIS session — driven by extended-hours/pre-market
    moves, today's catalysts, EOD footprint) and an OVERALL state (the structural regime — posture,
    Fear & Greed, breadth, multi-week rotation). NOT advice."""
    market = _effective_regime()
    posture = market.get("posture", 55)
    label = market.get("label", "")
    indexes = market.get("indexes", [])
    stretched = [i["name"] for i in indexes if i.get("stretched_50")]
    regime_live = bool(market.get("live"))

    heat = read_json(sector_heat_f(), {}).get("sectors", [])
    rising = [s["sector"] for s in heat if s.get("trend") == "Rising"]
    slowing = [s["sector"] for s in heat if s.get("trend") == "Slowing"]
    falling = [s["sector"] for s in heat if s.get("trend") == "Falling"]
    breadth = round(_mean([s.get("breadth", 50) for s in heat])) if heat else None

    news = read_json(news_f(), {})
    alerts = news.get("alerts", [])
    a_good = len([a for a in alerts if a.get("dir") == "buy"])
    a_bad = len([a for a in alerts if a.get("dir") == "avoid"])
    tn = news.get("ticker_news", {})
    t_good = len([1 for v in tn.values() if v.get("sentiment") == "good"])
    t_bad = len([1 for v in tn.values() if v.get("sentiment") == "bad"])

    susp = read_json(suspicious_f(), {})
    buys, sells = len(susp.get("buying", [])), len(susp.get("selling", []))
    pm = read_json(premarket_f(), {}).get("movers", [])
    pm_up = len([m for m in pm if m.get("gap", 0) >= 0])
    pm_dn = len(pm) - pm_up

    # shared near-term signals
    fg = market.get("fear_greed")                         # the Fear & Greed gauge (live-aware)
    bottoming = [i["name"] for i in indexes if i.get("state") == "Bottoming / turning up"]
    em = [i for i in indexes if i.get("ext_pct") is not None] if regime_live else []
    avg_ext = _mean([i["ext_pct"] for i in em]) if em else 0.0
    ms = market.get("market_state")
    when = ("Pre-market" if ms in ("PRE", "PREPRE")
            else "After-hours" if ms in ("POST", "POSTPOST") else None)
    pm_sectors = _premarket_sector_moves() if regime_live else {"when": None, "up": [], "down": []}
    up, dn = pm_sectors["up"], pm_sectors["down"]

    def _dir(v, hi=0, lo=0):
        return "pos" if v > hi else "neg" if v < lo else "neutral"

    # ===================== OVERALL — structural state of the market (days→weeks) =====================
    o, od = (posture - 55) / 10.0, []
    od.append({"text": f"Market regime: {label or 'n/a'} (posture {posture}/100)"
               + (" · 🌙 live" if regime_live else ""),
               "dir": "pos" if posture >= 60 else "neg" if posture < 45 else "neutral"})
    if fg:
        s = fg["score"]
        od.append({"text": f"Fear & Greed {s}/100 — {fg['label']}"
                   + (" (froth = correction risk)" if s >= 65 else " (washed out)" if s <= 30 else ""),
                   "dir": "neg" if s >= 65 else "pos" if 35 <= s <= 60 else "neutral"})
    if breadth is not None:
        o += (breadth - 50) / 15.0
        od.append({"text": f"Breadth: {breadth}% of names above their 20-day MA", "dir": _dir(breadth - 50, 5, -10)})
    o += (len(rising) - len(slowing) - 2 * len(falling)) * 0.12
    if rising:
        od.append({"text": f"Money rotating INTO: {', '.join(rising[:5])}", "dir": "pos"})
    if slowing or falling:
        od.append({"text": f"Cooling / rolling over: {', '.join((falling + slowing)[:5])}", "dir": "neg"})
    if len(stretched) >= 2:
        o -= 1.5
        od.append({"text": f"{', '.join(stretched)} stretched above the 50-MA — pullback/digestion risk", "dir": "neg"})
    if bottoming:
        state = "Bottoming — turning up"
    elif fg and fg["score"] >= 70 and (stretched or posture < 62):
        state = "Extended / frothy — late-stage"
    elif posture >= 65:
        state = "Healthy uptrend"
    elif posture >= 45:
        state = "Mixed / consolidating"
    elif posture >= 25:
        state = "Correction"
    else:
        state = "Deep correction"
    op = [f"The tape reads <b>{label or 'unclear'}</b> (posture {posture}/100)"
          + (f", and Fear &amp; Greed is <b>{fg['label']}</b> ({fg['score']}/100)" if fg else "") + "."]
    if state == "Bottoming — turning up":
        op.append(f"{', '.join(bottoming)} {'are' if len(bottoming) > 1 else 'is'} below the 50 but reclaiming the short EMAs "
                  f"with a higher low — an early turn; warm the watchlist but wait for the 50 reclaim before pressing.")
    elif state == "Extended / frothy — late-stage":
        op.append("Indexes are stretched and sentiment is greedy — late-stage; favor patient at-support entries over chasing "
                  "breakouts, and expect a pullback or sideways digestion rather than a clean leg up.")
    elif state == "Healthy uptrend":
        op.append("Trend and breadth are healthy — dips are buyable while leaders hold their lines.")
    elif posture < 45:
        op.append("Below the line — capital-preservation mode; let the tape prove a turn (reclaim the 50) before adding risk.")
    if rising:
        op.append(f"Leadership is rotating into {', '.join(rising[:4])}; that's where fresh setups should cluster.")
    if falling or slowing:
        op.append(f"Avoid fading strength into the cooling groups ({', '.join((falling + slowing)[:4])}).")
    overall = {"state": state, "lean": _lean_from_score(o), "score": round(o, 2),
               "confidence": "moderate" if len(od) >= 4 else "low",
               "outlook": " ".join(op), "drivers": od}

    # ===================== DAILY — what's likely to happen THIS session =====================
    d, dd = (posture - 55) / 18.0, []                     # regime as a lighter backdrop for today
    if regime_live and em and abs(avg_ext) >= 0.2:
        d += max(-1.5, min(1.5, avg_ext / 0.6))
        dd.append({"text": f"🌙 {when} index move: " + ", ".join(f"{i['name']} {i['ext_pct']:+.1f}%" for i in em),
                   "dir": "pos" if avg_ext > 0 else "neg"})
    if pm:
        d += max(-0.8, min(0.8, (pm_up - pm_dn) * 0.05))
        dd.append({"text": f"Pre-market movers: {pm_up} gapping up vs {pm_dn} down", "dir": _dir(pm_up - pm_dn)})
    if up or dn:
        d += max(-0.8, min(0.8, (len(up) - len(dn)) * 0.2))
        seg = []
        if up:
            seg.append("leading " + ", ".join(f"{x['sector']} {x['pct']:+.1f}%" for x in up))
        if dn:
            seg.append("lagging " + ", ".join(f"{x['sector']} {x['pct']:+.1f}%" for x in dn))
        dd.append({"text": f"🌙 {pm_sectors['when']} sector moves: " + "; ".join(seg),
                   "dir": "pos" if len(up) >= len(dn) else "neg"})
    d += (a_good - a_bad) * 0.5 + (t_good - t_bad) * 0.1
    if alerts:
        cat = "; ".join((("🚀 " if a["dir"] == "buy" else "🛑 " if a["dir"] == "avoid" else "👀 ") + a["title"])
                        for a in alerts[:3])
        dd.append({"text": f"Today's catalysts: {cat}", "dir": "pos" if a_good >= a_bad else "neg"})
    if buys or sells:
        d += (buys - sells) * 0.04
        dd.append({"text": f"Latest EOD footprint: {buys} unusual-buying vs {sells} unusual-selling names", "dir": _dir(buys - sells)})
    d_lean = ("Likely up" if d >= 1.5 else "Lean up" if d >= 0.5 else "Mixed / chop" if d > -0.5
              else "Lean down" if d > -1.5 else "Likely down")
    has_today = bool((regime_live and em) or pm or alerts)
    dp = []
    if regime_live and em and abs(avg_ext) >= 0.2:
        dp.append(f"{when}, the indexes are {'green' if avg_ext > 0 else 'red'} ({avg_ext:+.1f}% avg) — today likely opens "
                  f"{'up' if avg_ext > 0 else 'down'}; extended-hours moves can fade, so watch the first 30–60 min for follow-through.")
    elif not has_today:
        dp.append(f"Off-hours — no live read on today yet. Off yesterday's close the tape is {label or 'unclear'} "
                  f"(posture {posture}/100), so the base case is a {d_lean.lower()} session.")
    if pm:
        dp.append(f"{pm_up} names gapping up vs {pm_dn} down pre-market"
                  + (f"; leaders poking into {', '.join(x['sector'] for x in up[:3])}." if up else "."))
    if alerts:
        dp.append("Fresh catalysts are live (see drivers) — they'll drive which names actually move today.")
    if overall["state"] == "Extended / frothy — late-stage":
        dp.append("Tape is frothy, so even an up day is chase-prone — don't force breakouts; let the pullbacks come to you.")
    daily = {"lean": d_lean, "score": round(d, 2),
             "confidence": "moderate" if (has_today and abs(d) >= 1.2) else "low",
             "outlook": " ".join(dp) or "Not enough fresh data for a confident today-call yet.", "drivers": dd}

    return {"computed_at": time.strftime("%Y-%m-%d %H:%M"),
            "daily": daily, "overall": overall,
            "rising": rising[:8], "slowing": slowing[:8], "falling": falling[:8],
            "posture": posture, "label": label, "breadth": breadth, "regime_live": regime_live,
            "pm_sectors": pm_sectors, "fear_greed": fg,
            "note": "Probabilistic read from the data on hand — not a prediction you should trade blindly. The market does what it wants."}


def _regime_label(avg):
    if avg >= 80:
        return "Risk-on - uptrend"
    if avg >= 60:
        return "Constructive"
    if avg >= 45:
        return "Mixed / pullback"
    if avg >= 25:
        return "Caution - correction"
    return "Risk-off - deep correction"


def live_posture(quotes):
    """Recompute the blended market posture using LIVE index prices (overwrite today's close).
    Market-aware: blends the CURRENT market's benchmark indexes (US SPX/QQQ/IWM or IL TA125/TA35)."""
    indexes = scanner.mcfg(market())["indexes"]
    idx = []
    for name, sym in indexes:
        bars = scanner.get_bars(sym)
        if not bars or len(bars) < 60:
            continue
        q = quotes.get(sym.upper())
        ext_pct = None
        if q and q.get("price"):
            bars = bars[:-1] + [dict(bars[-1])]
            lp = q["price"]
            bars[-1]["close"] = lp
            bars[-1]["high"] = max(bars[-1]["high"], lp)
            bars[-1]["low"] = min(bars[-1]["low"], lp)
            ext_pct = q.get("ext_change_pct")    # pre/after-hours move on this index, if any
        try:
            one = scanner._regime_one(name, bars)
            one["ext_pct"] = ext_pct
            idx.append(one)
        except Exception:
            pass
    if not idx:
        return None
    avg = sum(i["posture"] for i in idx) / len(idx)
    ms = next((quotes[s.upper()].get("market_state") for _, s in indexes
               if quotes.get(s.upper())), None)
    extended = ms in ("PRE", "PREPRE", "POST", "POSTPOST")
    # carry the stored (daily) Fear & Greed nudge so the LIVE posture matches the GRADED posture.
    # F&G (breadth/VIX/highs-lows) is slow-moving, so reusing today's nudge intraday is correct and
    # avoids re-iterating the whole universe on every 45s tick.
    fg = read_json(market_f(), {}).get("fear_greed")
    nudge = fg.get("posture_nudge", 0) if fg else 0
    posture_raw = round(avg)
    posture = max(0, min(100, posture_raw + nudge))
    return {"posture": posture, "posture_raw": posture_raw,
            "label": _regime_label(posture), "indexes": idx,
            "market_state": ms, "extended": extended, "fear_greed": fg}


def _premarket_sector_moves():
    """Average TRUE extended-hours (pre/after) move per sector, from each member's `ext_change_pct`
    (the move vs the regular-session close). NOT perf_1d — during PRE that's polluted by yesterday's
    full session (its prev_close is 2 days back). Returns {'when', 'up', 'down'} (empty off-hours)."""
    heat = read_json(sector_heat_f(), {}).get("sectors", [])
    if not heat:
        return {"when": None, "up": [], "down": []}
    syms = list(dict.fromkeys([m["ticker"] for s in heat for m in s.get("members", [])]))
    try:
        quotes = scanner.fetch_quotes(syms)
    except Exception:
        return {"when": None, "up": [], "down": []}
    ms, rows = None, []
    for s in heat:
        moves = []
        for m in s.get("members", []):
            q = quotes.get(m["ticker"].upper())
            if q:
                ms = ms or q.get("market_state")
                if q.get("ext_price") is not None and q.get("ext_change_pct") is not None:
                    moves.append(q["ext_change_pct"])
        if len(moves) >= 2:                       # ≥2 members printing → a real sector move, not one name
            rows.append({"sector": s["sector"], "pct": round(sum(moves) / len(moves), 2), "n": len(moves)})
    if not rows or ms not in ("PRE", "PREPRE", "POST", "POSTPOST"):
        return {"when": None, "up": [], "down": []}
    rows.sort(key=lambda r: r["pct"], reverse=True)
    when = "Pre-market" if ms in ("PRE", "PREPRE") else "After-hours"
    return {"when": when,
            "up": [r for r in rows if r["pct"] >= 0.4][:4],
            "down": [r for r in rows if r["pct"] <= -0.4][-4:]}


def _effective_regime():
    """The market regime to use RIGHT NOW. During pre/after-hours this re-blends the current market's
    benchmark indexes (US SPX/QQQ/IWM or IL TA125/TA35) from their extended-hours prices (live_posture)
    so the gameplan & prediction reflect what's moving NOW — not yesterday's close. Outside extended
    hours it's the stored daily regime (market.json). Index quotes are 30s-cached, so this is cheap."""
    indexes = scanner.mcfg(market())["indexes"]
    stored = read_json(market_f(), {})
    try:
        idxq = scanner.fetch_quotes([s for _, s in indexes])
        ms = next((idxq[s.upper()].get("market_state") for _, s in indexes
                   if idxq.get(s.upper())), None)
        if ms in ("PRE", "PREPRE", "POST", "POSTPOST"):
            lp = live_posture(idxq)
            if lp:
                lp.setdefault("computed_at", stored.get("computed_at"))
                lp["live"] = True
                return lp
    except Exception:
        pass
    return {**stored, "live": False}


def live_sector_heat():
    """Re-rate Sector Heat with LIVE prices: recompute each member's & sector's TODAY % (perf_1d)
    and the heat score/rank from live quotes, keeping the multi-day trend/streak/breadth from the
    last EOD compute (those don't move intraday). Read-only — never overwrites the stored heat."""
    data = read_json(sector_heat_f(), {"sectors": []})
    sectors = data.get("sectors", [])
    if not sectors:
        return data
    syms = []
    for s in sectors:
        for m in s.get("members", []):
            syms.append(m["ticker"])
    quotes = scanner.fetch_quotes(list(dict.fromkeys(syms)))   # batched, 30s-cached, shared
    for s in sectors:
        day = []
        for m in s.get("members", []):
            q = quotes.get(m["ticker"].upper())
            if q and q.get("price") and q.get("prev_close"):
                m["perf_1d"] = round((q["price"] / q["prev_close"] - 1) * 100, 2)
                m["close"] = q["price"]
                day.append(m["perf_1d"])
        if day:
            s["perf_1d"] = round(sum(day) / len(day), 2)
            s["score"] = round(s.get("perf_1w", 0) * 0.4 + s.get("perf_1mo", 0) * 0.3
                               + s["perf_1d"] * 0.2 + (s.get("breadth", 50) - 50) * 0.05, 2)
    sectors.sort(key=lambda r: r["score"], reverse=True)
    n = len(sectors)
    for i, s in enumerate(sectors):
        s["rank"] = i + 1
        s["tier"] = "Hot" if s["rank"] <= max(1, n // 3) else ("Warm" if s["rank"] <= 2 * n // 3 else "Cool")
    data["sectors"] = sectors
    data["live"] = True
    data["live_at"] = time.strftime("%H:%M:%S")
    return data


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

    def _bytes(self, data, ctype, code=200, no_cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if no_cache:                       # always serve fresh app code after a restart/rebuild
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
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
        # don't let the browser cache the app shell/scripts — otherwise a rebuild looks like
        # "nothing changed" until a hard refresh. (Uploaded images can still cache.)
        no_cache = not relpath.startswith(("uploads/", "data/uploads/"))
        self._bytes(target.read_bytes(), ctype, no_cache=no_cache)

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
            self._json({**settings, "equity_info": compute_equity()})
        elif route == "env":
            # lets the frontend hide write-heavy pages (journal/strategy/watchlist) on the hosted
            # free service, where per-user data doesn't persist across the dyno sleeping.
            self._json({"hosted": HOSTED})
        elif route == "autopilot":
            # FRIENDS-FACING confirmation view (website "Auto Pilot" tab) — armed + confirmed setups +
            # tape stance, with NO personal data (positions/journal/account/sizing stripped). Shared data
            # only, so it's identical for every viewer and safe on the hosted build.
            self._json(compute_autopilot())
        elif route == "coach-config":
            # the coach threshold NUMBERS, single-sourced in rubric.py — the frontend's live coach
            # recompute (web/app.js) reads these so they can't drift from the backend coach.
            cfg = rubric.coach_config()
            s = read_json(settings_f(), {})                       # let settings.json override the guard knobs
            for k in ("guard_min_lock", "guard_buffer_adr", "guard_step_dollars"):
                if s.get(k) is not None:
                    cfg[k] = s[k]
            self._json(cfg)
        elif route == "screeners":
            self._json(read_json(screeners_f(), []))
        elif route == "suggestions":
            s = read_json(suggest_f(), {"items": []})
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
            s["items"] = grade_suggestions(s.get("items", []), _equity_settings())
            self._json(s)
        elif route == "market":
            self._json(read_json(market_f(), {}))
        elif route == "universe":
            u = read_json(universe_f(), {})
            u["status"] = UNIVERSE
            self._json(u)
        elif route == "suspicious":
            s = read_json(suspicious_f(), {"buying": [], "selling": []})
            s["status"] = SUSPECT
            self._json(s)
        elif route == "premarket":
            pm = read_json(premarket_f(), {"movers": []})
            rev = reverse_themes()
            heat = {h["sector"]: h for h in read_json(sector_heat_f(), {}).get("sectors", [])}
            news_map = read_json(news_f(), {}).get("ticker_news", {})
            sug = {i["ticker"]: i for i in read_json(suggest_f(), {}).get("items", [])}
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
        elif route == "spinning":
            sp = read_json(spinning_f(), {"spins": []})
            rev = reverse_themes()
            heat = {h["sector"]: h for h in read_json(sector_heat_f(), {}).get("sectors", [])}
            news_map = read_json(news_f(), {}).get("ticker_news", {})
            sug = {i["ticker"]: i for i in read_json(suggest_f(), {}).get("items", [])}
            for s in sp.get("spins", []):
                th = rev.get(s["ticker"])
                s["theme"] = th
                hr = heat.get(th)
                if hr:
                    s["theme_trend"] = hr.get("trend")
                    s["theme_tier"] = hr.get("tier")
                    s["theme_hot"] = hr.get("tier") == "Hot" or hr.get("trend") == "Rising"
                si = sug.get(s["ticker"])
                if si:
                    s["setup_type"] = si.get("setup_type")
                    s["rs_pct"] = si.get("rs_pct")
                nm = news_map.get(s["ticker"])
                if nm:
                    s["news_headline"] = nm["title"]
                    s["news_link"] = nm["link"]
                    s["news_dir"] = nm.get("sentiment")
                # leader (strong relative strength) + rising-sector flags, with a ranking boost
                s["leader"] = (s.get("rs_pct") or 0) >= 80
                s["rising_sector"] = bool(s.get("theme_hot"))
                s["base_score"] = s.get("score", 0)
                s["score"] = s["base_score"] + (8 if s["leader"] else 0) + (6 if s["rising_sector"] else 0)
            sp["spins"] = sorted(sp.get("spins", []), key=lambda x: -x.get("score", 0))
            sp["status"] = SPIN
            self._json(sp)
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
            bars = scanner.get_bars(t, max_age_hours=0.25)   # fresh-ish so today's forming candle shows
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
        elif route == "now":
            self._json(compute_now())
        elif route == "notifications":
            self._json(read_json(notifications_f(), {"items": []}))
        elif route == "prediction":
            self._json(compute_prediction())
        elif route == "forward" and len(parts) > 2 and parts[2] == "day":
            params = parse_qs(urlparse(self.path).query)
            self._json(forward_day((params.get("date", [""])[0] or "").strip()))
        elif route == "forward":
            self._json(score_forward())
        elif route == "pnl-calendar":
            self._json(read_json(pnl_f(), {}))
        elif route == "live":
            params = parse_qs(urlparse(self.path).query)
            req = (params.get("symbols", [""])[0] or "").split(",")
            idxsyms = [sym for _, sym in scanner.mcfg(market())["indexes"]]
            allsyms = list(dict.fromkeys([s.strip().upper() for s in req if s.strip()] + idxsyms))[:120]
            quotes = scanner.fetch_quotes(allsyms)
            ms = next((quotes[s.upper()]["market_state"] for _, s in scanner.mcfg(market())["indexes"]
                       if quotes.get(s.upper())), None)
            prices = {k: {kk: v.get(kk) for kk in ("price", "reg_price", "ext_price",
                                                   "ext_change_pct", "prev_close", "change_pct",
                                                   "day_high", "day_low", "day_open",
                                                   "market_state")}
                      for k, v in quotes.items()}
            self._json({"updated_at": time.strftime("%H:%M:%S"), "market_state": ms,
                        "session_date": _session_date(),
                        "prices": prices, "posture": live_posture(quotes)})
        elif route == "analyze" and len(parts) > 2:
            t = parts[2].upper()
            bars = scanner.get_bars(t)
            if not bars:
                self._json({"error": "no data"}, 404); return
            esettings = _equity_settings()
            a = scanner.analyze(t, bars, esettings)
            apply_sizing(a, esettings)
            a["sector"] = read_json(sectors_f(), {}).get(t, "Other")
            a["sector_hot"] = a["sector"] in read_json(suggest_f(), {}).get("hot_sectors", [])
            self._json({"analysis": a, "bars": bars})
        elif route == "groups" and len(parts) > 2 and parts[2] == "status":
            self._json(GROUPS)
        elif route == "groups":
            g = read_json(groups_f(), {"computed_at": None, "groups": []})
            g["status"] = GROUPS
            self._json(g)
        elif route == "sector-heat" and len(parts) > 2 and parts[2] == "status":
            self._json(SECTORH)
        elif route == "sector-heat" and len(parts) > 2 and parts[2] == "live":
            self._json(live_sector_heat())
        elif route == "sector-heat":
            self._json(read_json(sector_heat_f(), {"computed_at": None, "sectors": []}))
        elif route == "news" and len(parts) > 2 and parts[2] == "status":
            self._json(NEWS)
        elif route == "news":
            self._json(read_json(news_f(), {"computed_at": None, "sections": [], "ticker_news": {}}))
        elif route == "themes":
            self._json(read_json(themes_f(), {}))
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
        if HOSTED and route in ("refresh-all", "scan", "sector-heat", "news", "universe", "suspicious", "spinning", "groups"):
            # ⚠️ SHARED market data (regime / scan / sector heat / news / universe / suspicious / spinning / groups) is the
            # OWNER's WARM SNAPSHOT, shipped with each build. Friends must NOT re-scan and overwrite it — an independent
            # hosted scan is exactly what made the site diverge from the owner's local. It's read-only on the hosted site;
            # the owner refreshes what friends see by making a NEW BUILD.
            self._json({"ok": False, "error": "market data is read-only on the hosted site — it mirrors the owner's build"}, 403); return
        if route == "screeners":
            screeners = read_json(screeners_f(), [])
            raw = body.get("tickers", "")
            tickers = [t.strip().upper() for t in re.split(r"[\s,;]+", raw) if t.strip()]
            sid = re.sub(r"[^a-z0-9]+", "-", body.get("name", "screener").lower()).strip("-") or f"s{int(time.time())}"
            base_sid, n = sid, 2
            while any(s["id"] == sid for s in screeners):
                sid = f"{base_sid}-{n}"; n += 1
            screeners.append({"id": sid, "name": body.get("name", "Screener"),
                              "is_default": False, "tickers": tickers})
            write_json(screeners_f(), screeners)
            self._json({"ok": True, "id": sid})

        elif route == "scan" and len(parts) > 2:
            sid = parts[2]
            fresh = parse_qs(urlparse(self.path).query).get("fresh", ["0"])[0] == "1"
            with _scan_lock:
                if SCAN["running"]:
                    self._json({"ok": False, "error": "scan already running"}, 409); return
                _spawn(run_scan, sid, max_age=0 if fresh else 12)
            self._json({"ok": True})

        elif route == "suggestions" and len(parts) > 3:
            ticker, action = parts[2].upper(), parts[3]
            s = read_json(suggest_f(), {"items": []})
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
            elif action == "unpass":                    # undo a pass — back to a normal (pending) suggestion
                cur["status"] = "pending"
                cur.pop("reject_reason", None)
            elif action == "catalyst":
                cur["catalyst"] = body.get("catalyst", "")
            elif action == "take":
                cur["status"] = "taken"
                self._create_trade(it, body)
            ov[ticker] = cur
            write_json(status_f(), ov)
            _NOW_CACHE.clear()    # drop the 12s /api/now cache so a passed/taken name leaves the buy list NOW
            self._json({"ok": True})

        elif route == "notifications" and len(parts) > 2 and parts[2] == "clear":
            write_json(notifications_f(), {"items": []}); self._json({"ok": True})
        elif route == "notifications" and len(parts) > 3 and parts[3] == "read":
            data = read_json(notifications_f(), {"items": []})
            for it in data.get("items", []):
                if str(it.get("id")) == parts[2]:
                    it["read"] = True
            write_json(notifications_f(), data); self._json({"ok": True})

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
                _spawn(run_sector_heat)
            self._json({"ok": True})

        elif route == "news" and len(parts) > 2 and parts[2] == "refresh":
            with _news_lock:
                if NEWS["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_news_refresh)
            self._json({"ok": True})

        elif route == "universe" and len(parts) > 2 and parts[2] == "build":
            with _universe_lock:
                if UNIVERSE["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_build_universe)
            self._json({"ok": True})

        elif route == "suspicious" and len(parts) > 2 and parts[2] == "scan":
            with _suspect_lock:
                if SUSPECT["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_suspicious)
            self._json({"ok": True})

        elif route == "premarket" and len(parts) > 2 and parts[2] == "scan":
            with _premkt_lock:
                if PREMKT["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_premarket)
            self._json({"ok": True})

        elif route == "spinning" and len(parts) > 2 and parts[2] == "scan":
            with _spin_lock:
                if SPIN["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_spinning)
            self._json({"ok": True})

        elif route == "groups" and len(parts) > 2 and parts[2] == "detect":
            with _groups_lock:
                if GROUPS["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_detect_groups)
            self._json({"ok": True})

        elif route == "refresh-all":
            with _refresh_lock:
                if REFRESH["running"]:
                    self._json({"ok": False, "error": "already running"}, 409); return
                _spawn(run_refresh_all)
            self._json({"ok": True})

        elif route == "backup":
            self._json(backup_data())

        elif route == "telegram" and len(parts) > 2 and parts[2] == "test":
            ok = notify_telegram("✅ Live Coach test", "Phone alerts are wired — you'll get the actual BUY / "
                                 "EXIT / TRIM / RAISE-STOP calls here.")
            self._json({"ok": ok, "error": None if ok else "no message sent — check the bot token + chat id"})

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
            if "max_position_dollars" in body:                 # hard $ cap per position (None/0 = off)
                try:
                    v = float(body["max_position_dollars"] or 0)
                    s["max_position_dollars"] = v if v > 0 else None
                except (TypeError, ValueError):
                    pass
            if "stop_buffer_pct" in body:                      # % buffer below the day-low stop (0 = off)
                try:
                    s["stop_buffer_pct"] = max(0.0, min(5.0, float(body["stop_buffer_pct"] or 0)))
                except (TypeError, ValueError):
                    pass
            if "entry_buffer_pct" in body:                     # % price must clear the ORH to confirm (0 = off)
                try:
                    s["entry_buffer_pct"] = max(0.0, min(5.0, float(body["entry_buffer_pct"] or 0)))
                except (TypeError, ValueError):
                    pass
            if "intraday_rescan_min" in body:                  # auto re-scan cadence in minutes (0 = off)
                try:
                    s["intraday_rescan_min"] = max(0, int(float(body["intraday_rescan_min"] or 0)))
                except (TypeError, ValueError):
                    pass
            if "size_factor" in body:
                try:
                    s["size_factor"] = max(0.05, min(1.0, float(body["size_factor"]) or 1.0))
                except (TypeError, ValueError):
                    pass
            for k in ("telegram_token", "telegram_chat_id"):    # the user's own bot creds (local only)
                if k in body:
                    s[k] = (body[k] or "").strip()
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
            # freeze the risk basis: the FIRST time the stop is edited (e.g. raised to breakeven),
            # remember the original stop as initial_stop so R stays measured off the real risk.
            if "stop" in body and t.get("initial_stop") is None:
                t["initial_stop"] = t.get("stop")
            if "initial_stop" in body:                 # allow an explicit correction
                t["initial_stop"] = body["initial_stop"]
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
            screeners = [s for s in read_json(screeners_f(), []) if s["id"] != parts[2]]
            write_json(screeners_f(), screeners)
            self._json({"ok": True})
        else:
            self._json({"error": "unknown route"}, 404)

    # ---- trade helpers ----
    # These delegate to the module-level functions (create_trade/add_trade/close_trade/append_lesson)
    # so the Telegram chat bot can run the exact same write logic without a Handler instance.
    def _create_trade(self, sug, body):
        return create_trade(sug, body)

    def _add_trade(self, body):
        return add_trade(body)

    def _close_trade(self, tid, body):
        return close_trade(tid, body)

    def _append_lesson(self, ticker, lesson):
        return append_lesson(ticker, lesson)

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
    """Hosted only: the SHARED market data (regime, scan, sector heat, news) ships as a WARM SNAPSHOT with
    every build, so the site mirrors the owner's last build EXACTLY (deterministic; no cold-scan wait).

    ⚠️ BUG FIX (2026-06-05): this used to run_refresh_all() on boot, which RE-SCANNED the universe + recomputed
    the regime on the hosted server — OVERWRITING the shipped snapshot with the hosted's own independent scan
    (different prices/regime/timing). So the site never matched the owner's local and NO rebuild could fix it
    (the owner saw site=792 matches / A-grades vs local=780 / B-grades). Now we serve the shipped snapshot and
    only scan if there is genuinely NO snapshot to serve (a cold deploy with no data). The owner updates what
    friends see by making a NEW BUILD — not by the hosted server re-scanning itself."""
    while True:
        try:
            if not read_json(suggest_f(), {}).get("items"):   # ONLY when there's no shipped snapshot to serve
                run_refresh_all()
        except Exception:
            pass
        time.sleep(24 * 3600)


# --------------------------------------------------------------------------- #
# Background coach — desktop notifications so the trader never has to check the screen.
# Local Windows only; honors settings.json toggles. (User: "I don't want to do shit — you tell me.")
# --------------------------------------------------------------------------- #
# Per-alert re-reminder cooldowns (seconds): a still-pending alert won't beep again until this
# elapses. A beep ALWAYS means an action to take — see _now_watcher.
_ALERT_COOLDOWN = {"EXIT": 90 * 60, "TRIM": 3 * 3600, "RAISE STOP": 6 * 3600, "BUY": 6 * 3600,
                   "GUARD STOP": 6 * 3600,     # profit-lock raise: remind at most every 6h (re-fires if the level steps up)
                   "STOPPED OUT": 4 * 3600}    # stop-hit: fire clearly once, re-remind only every 4h


def notify_desktop(title, message, urgent=False):
    """Fire a Windows desktop notification (balloon/toast) via built-in .NET NotifyIcon — no installs,
    non-blocking, best-effort (never raises). Local Windows only. Also plays a SOUND so it isn't
    missed when idle: a single chime normally, an urgent triple-beep for actionable alerts (EXIT/buy)
    — NotifyIcon balloons don't reliably sound on their own."""
    if os.name != "nt":
        return False
    t = (title or "")[:120].replace('"', "'").replace("`", "'").replace("\n", " ")
    m = (message or "")[:255].replace('"', "'").replace("`", "'").replace("\n", " ")
    snd = ("1..3 | %{ [console]::beep(1180,260); Start-Sleep -Milliseconds 90 };" if urgent
           else "[System.Media.SystemSounds]::Exclamation.Play();")
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          "Add-Type -AssemblyName System.Windows.Forms;Add-Type -AssemblyName System.Drawing;"
          + snd +
          "$n=New-Object System.Windows.Forms.NotifyIcon;"
          "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
          f"$n.ShowBalloonTip(8000,'{t}','{m}',[System.Windows.Forms.ToolTipIcon]::Info);"
          "Start-Sleep -Seconds 9;$n.Dispose()")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _tg_api(method, params, timeout=12):
    """Low-level Telegram Bot API call (local only). Returns the parsed JSON dict, or None if no token is
    configured. Raises on a transport/HTTP error so callers can retry; never reads beyond the owner's
    settings.json for the token."""
    s = read_json(settings_owner_f(), {})
    token = (s.get("telegram_token") or "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tg_send_text(text):
    """Send a message to the configured chat (the user's phone), best-effort (never raises). Tries
    Markdown formatting first, then falls back to PLAIN text if Telegram 400s on an unbalanced */_
    (a ticker or free-text reason can contain those) so a reply is never silently dropped."""
    s = read_json(settings_owner_f(), {})
    chat = (s.get("telegram_chat_id") or "").strip()
    if not chat:
        return False
    text = (text or "")[:3900]
    for attempt in range(4):
        params = {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
        if attempt < 2:
            params["parse_mode"] = "Markdown"     # formatted first; drop it on retry if it errored
        try:
            res = _tg_api("sendMessage", params)
            if res and res.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.8 * (attempt + 1))
    return False


def notify_telegram(title, body):
    """Push an alert to the user's phone via their own Telegram bot (token + chat_id in settings). Local
    only, best-effort (never raises). The token is the USER's — they create the bot via @BotFather and paste
    it into Settings themselves; we never generate or store it anywhere but their local settings.json."""
    try:
        return tg_send_text(f"*{title}*\n{body}")
    except Exception:
        return False


def notifications_f():
    return DATA / "notifications.json"


def notify_state_f():
    # Persisted dedupe/cooldown ledger for desktop alerts (so nothing double-fires across restarts).
    return DATA / "notify_state.json"


def add_notification(title, body, light="", actionable=False):
    """Append an alert to the in-app notification feed (newest first, capped). The standalone Coach
    app shows these and lets you mark each one read/done."""
    try:
        data = read_json(notifications_f(), {"items": []})
        items = data.get("items", [])
        items.insert(0, {"id": int(time.time() * 1000), "ts": time.strftime("%H:%M"),
                         "date": now_date(), "title": title, "body": body,
                         "light": light, "actionable": bool(actionable), "read": False})
        data["items"] = items[:60]
        write_json(notifications_f(), data)
    except Exception:
        pass


def _et_now():
    """Current Eastern time, DST-correct when tz data is available (zoneinfo), else a fixed-offset
    approximation from the market config. Used to gate notifications to market hours."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        cfg = scanner.mcfg("us")
        return datetime.now(timezone.utc) + timedelta(hours=cfg.get("tz_offset", -4))


def _us_session_active():
    """True during the US active window (pre-market → after-hours, weekdays) — when pings make sense."""
    try:
        cfg = scanner.mcfg("us")
        et = _et_now()
        if et.weekday() not in cfg.get("trading_days", (0, 1, 2, 3, 4)):
            return False
        h = et.hour + et.minute / 60.0
        return 4.0 <= h <= 20.0
    except Exception:
        return True


def _rth_now():
    """True ONLY during the US REGULAR session (9:30–16:00 ET, weekdays) — when a broker stop can
    actually fill. A pre/after-hours print below the stop does NOT trigger a regular-hours stop, so
    stop-outs / exits must be gated to this window (the user's stop is RTH-only — an after-hours dip
    is not a fill, and auto-closing on it falsely flattens an open position)."""
    try:
        cfg = scanner.mcfg("us")
        et = _et_now()
        if et.weekday() not in cfg.get("trading_days", (0, 1, 2, 3, 4)):
            return False
        h = et.hour + et.minute / 60.0
        return 9.5 <= h < 16.0
    except Exception:
        return True             # fail OPEN (treat as RTH) so a clock/tz glitch never SUPPRESSES a real stop


def _us_regular_open():
    """True ONLY during the regular cash session (09:30–16:00 ET, weekdays). The confirmation engine fires
    a live BUY only in regular hours; after the close everything qualifying shows as ARMED (tomorrow's
    lineup) instead of a live call — so you can preview setups after hours without a beep."""
    try:
        cfg = scanner.mcfg("us")
        et = _et_now()
        if et.weekday() not in cfg.get("trading_days", (0, 1, 2, 3, 4)):
            return False
        h = et.hour + et.minute / 60.0
        return 9.5 <= h < 16.0
    except Exception:
        return False


def _now_watcher():
    """Background loop (local Windows only): turn the 'Now' verdict into desktop alerts — but ONLY for
    things that need a decision. The hard rule (the user's ask): **a beep means ACT.** The single best
    confirmed BUY, or an EXIT / RAISE STOP / TRIM on a position you hold — those toast + sound. Everything
    else (WATCH, HOLD, stance drift, 'sit tight', heartbeats) is SILENT — it lives in the panel + tray
    colour only, it never beeps.

    Per-alert dedupe + cooldowns are persisted to notify_state.json so the same alert never double-fires,
    and the FIRST loop after launch is always silent (kills the old startup beep). settings.json toggles:
    notify_enabled (default True) and notify_mode (set to 'off' to silence everything; any other value =
    fire actionable alerts)."""
    _ctx.market = "us"
    _ctx.udir = DATA
    st = read_json(notify_state_f(), {})
    fired = st.get("fired", {}) if isinstance(st, dict) else {}      # alert-key -> last-fired epoch
    # BUY keys confirmed on the PREVIOUS poll → lets a fresh re-break bypass cooldown. Seed from the ledger
    # so a RESTART doesn't treat every standing buy as 'fresh' and re-spam them (only genuine re-breaks fire).
    prev_buy_keys = {k for k in fired if k.startswith("BUY:")}
    # NO "coach online" heartbeat in the feed (user 2026-06-03: "only actual 'do that' stuff"). Liveness is
    # shown by the tray icon colour + the green "live" dot — the alerts feed is reserved for ACTIONS only.
    while True:
        active = _us_session_active()
        try:
            settings = read_json(settings_owner_f(), {})
            if settings.get("notify_enabled", True) and settings.get("notify_mode", "critical") != "off":
                n = compute_now()
                light = n.get("light")
                now_t = time.time()
                # ---- the ONLY things that may beep: discrete, actionable alerts ----
                alerts = []                  # each: (key, action, urgent, title, body, feed_light)
                for m in n.get("todo", []):
                    act = m.get("action")
                    if act not in ("EXIT", "TRIM", "RAISE STOP", "GUARD STOP"):
                        continue             # WATCH / HOLD are informational — panel only, never a beep
                    if act == "EXIT" and not _rth_now():
                        continue             # exits are decided on the regular-session CLOSE — never fire on a
                                             # pre/after-hours tick (broker stops don't fill outside RTH)
                    if m.get("stop_hit") and _rth_now():    # price traded through your stop → a distinct, clear stop-out ping
                        # GATED TO REGULAR HOURS: a broker stop only fills 9:30–16:00 ET. A pre/after-hours dip
                        # through the stop is NOT a fill — never auto-close on it (the MXL bug). compute_now
                        # already zeroes stop_hit outside RTH; this is the load-bearing second guard.
                        r = m.get("r_mult")
                        rtxt = f" ({r:+.1f}R)" if isinstance(r, (int, float)) else ""
                        # AUTO-CLOSE so the app REFLECTS the exit (the user's DOCN stayed open after a stop hit).
                        # Records exit at the stop; correct with "sold X <price>" if it gapped through. Toggle
                        # off with settings.auto_close_on_stop=false to keep it alert-only.
                        closed_txt = ""
                        if m.get("id") and settings.get("auto_close_on_stop", True):
                            try:
                                close_trade(m["id"], {"exit": m.get("stop"), "exit_at": now_date(),
                                                      "notes": "Auto-closed: stop hit."})
                                _NOW_CACHE.clear()
                                closed_txt = " — auto-closed in the journal at the stop"
                            except Exception:
                                pass
                        body = (f"Price ${m.get('last')} hit your stop ${m.get('stop')}{rtxt}{closed_txt}. "
                                f"If it gapped through, reply *sold {m['ticker']} <real price>* to correct.")
                        alerts.append((f"STOP:{m['ticker']}", "STOPPED OUT", True,
                                       f"🛑 STOPPED OUT {m['ticker']}", body, "red"))
                        continue
                    urgent = act == "EXIT"
                    icon = {"EXIT": "🔴", "GUARD STOP": "🛡️"}.get(act, "🟠")
                    flight = "green" if act == "GUARD STOP" else ("red" if urgent else "yellow")
                    alerts.append((f"{act}:{m['ticker']}", act, urgent,
                                   f"{icon} {act} {m['ticker']}", m.get("reason") or "", flight))
                buys = n.get("buys", [])
                if light != "red":
                    # ALERT EVERY confirmed buy, not just the best one. When ONDS + RKLB + LUNR all take
                    # out their opening-range high in the same window, each is a real call — pushing only
                    # buys[0] silently dropped the rest (the user missed ONDS/RKLB this way). The per-ticker
                    # dedupe + BUY cooldown below already stop any one name from re-spamming.
                    for b in buys:
                        body = (f"{b.get('shares')} sh @ ${b.get('entry')}, stop ${b.get('stop')} "
                                f"({b.get('risk_pct_actual')}% risk). {b.get('confirm', '')}").strip()
                        alerts.append((f"BUY:{b['ticker']}", "BUY", True,
                                       f"🟢 BUY {b['ticker']} · {b.get('grade')}", body, "green"))
                cur_keys = {a[0] for a in alerts}
                # Dedupe ONLY via the persisted ledger + cooldown — NOT a blanket "first loop seeds
                # everything silently". That seeding swallowed a freshly-confirmed BUY on every restart
                # (the user saw NXT confirm on screen but got no push because a restart had just seeded it
                # as 'fired'). Now a genuinely-new alert fires even on the first loop; anything already in
                # the ledger within its cooldown still stays quiet, so a restart never RE-beeps old alerts.
                for key, act, urgent, title, body, flight in alerts:
                    last = fired.get(key)
                    cd = _ALERT_COOLDOWN.get(act, 6 * 3600)
                    # A FRESHLY-confirmed buy (not confirmed on the previous poll) bypasses the cooldown — a
                    # name you got stopped on that BREAKS AGAIN is a brand-new signal, not a re-spam. This is
                    # the AAOI case: it alerted on the first break, you got stopped, it re-broke at $183, and
                    # the 6h cooldown ate the second alert. A continuous standing buy still honors the cooldown.
                    fresh_buy = (act == "BUY" and key not in prev_buy_keys)
                    if last is not None and now_t - last < cd and not fresh_buy:
                        continue             # already alerted, still inside its re-reminder cooldown
                    if act == "BUY" and not active:
                        continue             # never ping a new buy outside market hours
                    notify_desktop(title, body, urgent=urgent)
                    notify_telegram(title, body)             # phone push (no-op unless token+chat set)
                    add_notification(title, body, light=flight, actionable=True)
                    fired[key] = now_t
                prev_buy_keys = {a[0] for a in alerts if a[1] == "BUY"}   # for next poll's fresh-break check
                # bound the ledger: keep current alerts + anything fired within the last 24h
                fired = {k: v for k, v in fired.items() if k in cur_keys or now_t - v < 24 * 3600}
                write_json(notify_state_f(), {"fired": fired})
        except Exception:
            pass
        time.sleep(30 if active else 600)    # 30s during market hours so a breakout BUY/stop-out fires fast


def _tray_image(light, alert):
    """A 64px tray icon: a rounded square in the STANCE colour (green/yellow/red = go/selective/stand
    aside) with a little candlestick, plus a red BADGE dot when there's an action to take."""
    from PIL import Image, ImageDraw
    color = {"green": (34, 224, 161), "yellow": (255, 181, 61), "red": (255, 93, 115)}.get(light, (120, 140, 180))
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=color + (255,))
    ink = (12, 16, 24, 255)                                   # candlestick glyph
    d.rectangle([29, 20, 37, 46], fill=ink)
    d.line([(33, 12), (33, 54)], fill=ink, width=3)
    if alert:                                                 # red "you have an action" badge, top-right
        d.ellipse([40, 2, 62, 24], fill=(255, 38, 60, 255))
        d.ellipse([47, 9, 55, 17], fill=(255, 255, 255, 255))
    return im


# --------------------------------------------------------------------------- #
# Trade writes (module-level so both the HTTP handler AND the Telegram chat bot use one path)
# --------------------------------------------------------------------------- #
def create_trade(sug, body):
    """Open a trade FROM a suggestion (defaults entry/stop/target/setup from the graded idea, overridable
    by `body`). Mirrors the GUI 'take' action."""
    trades = read_json(trades_f(), [])
    entry = body.get("entry") or sug.get("entry")
    trades.append({
        "id": f"{sug['ticker']}-{int(time.time())}",
        "ticker": sug["ticker"], "setup_type": sug.get("setup_type", "Breakout"),
        "status": "open", "planned_entry": sug.get("entry"), "entry": entry,
        "stop": body.get("stop") or sug.get("stop"),
        "initial_stop": body.get("stop") or sug.get("stop"),   # risk basis — frozen at entry
        "target": body.get("target") or sug.get("target"),
        "shares": body.get("shares"), "taken_at": now_date(),
        "exit": None, "result_r": None, "result_pct": None, "rules_followed": None,
        "notes": body.get("notes", ""), "lesson": None, "screenshots": [],
    })
    write_json(trades_f(), trades)
    regen_trades_md()


def add_trade(body):
    """Append a fully-specified trade record (used by the journal log form and the chat bot)."""
    trades = read_json(trades_f(), [])
    body.setdefault("id", f"{body.get('ticker','T')}-{int(time.time())}")
    body.setdefault("status", "open")
    body.setdefault("taken_at", now_date())
    body.setdefault("screenshots", [])
    if body.get("initial_stop") is None:           # freeze the risk basis at entry
        body["initial_stop"] = body.get("stop")
    trades.append(body)
    write_json(trades_f(), trades)
    regen_trades_md()
    _NOW_CACHE.clear()    # new position → app's /api/now reflects it immediately (not up to 12s later)


def close_trade(tid, body):
    """Close a trade by id: record exit, realized R (off the INITIAL stop) + realized P&L, optional
    lesson. Equity stays derived (compute_equity) — we never mutate the base account here."""
    trades = read_json(trades_f(), [])
    t = next((x for x in trades if x["id"] == tid), None)
    if not t:
        return
    t["status"] = "closed"
    t["exit"] = body.get("exit")
    t["exit_at"] = body.get("exit_at") or now_date()   # exit date (entry date is `taken_at`)
    t["result_pct"] = body.get("result_pct")
    t["rules_followed"] = body.get("rules_followed")
    e, sh, x = t.get("entry"), t.get("shares"), body.get("exit")
    # realized R measured off the INITIAL stop (the real risk taken), not a raised/breakeven stop
    istop = t.get("initial_stop") if t.get("initial_stop") is not None else t.get("stop")
    if e and x and istop is not None and e > istop:
        t["result_r"] = round((x - e) / (e - istop), 2)
    else:
        t["result_r"] = body.get("result_r")
    if e and sh and x:
        t["realized_pnl"] = round((x - e) * sh, 2)
    if body.get("notes"):
        t["notes"] = body["notes"]
    if body.get("lesson"):
        t["lesson"] = body["lesson"]
        append_lesson(t["ticker"], body["lesson"])
    write_json(trades_f(), trades)
    regen_trades_md()
    _NOW_CACHE.clear()    # closed position leaves /api/now (and its skip_buy) immediately


def append_lesson(ticker, lesson):
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


# --------------------------------------------------------------------------- #
# Telegram chat bot — talk to the coach in plain English; it WRITES BACK into the app.
# Local only (runs beside _now_watcher). Keyless: a forgiving trading-vocabulary interpreter, NOT an LLM.
# "took NVDA 100 @ 120 stop 115" · "passed AVGO, too extended" · "sold NVDA at 134" ·
# "raise NVDA stop to 125" · "positions" · "setups" · "how am I doing" · "NVDA?"
# --------------------------------------------------------------------------- #
_TG_COMMON_WORDS = {
    "A", "I", "AT", "IT", "ON", "IN", "IS", "BE", "SO", "TO", "OR", "IF", "OF", "GO", "ME", "MY", "WE",
    "DO", "UP", "AN", "AS", "BY", "HE", "NO", "US", "AM", "PM", "OK", "ALL", "ANY", "ARE", "BUT", "CAN",
    "DID", "FOR", "GET", "GOT", "HAS", "HOW", "NOW", "OUT", "SEE", "THE", "TOO", "WAS", "WHO", "WHY",
    "YOU", "AND", "NOT", "OFF", "ADD", "BUY", "LOW", "NEW", "ONE", "TWO", "RUN", "DAY", "EPS", "ATH",
    "ADR", "CEO", "FDA", "SL", "TP", "PT", "EOD", "ETF", "USD", "R", "BE", "PER", "PRE",
}


def _f(x):
    """Trim a price to a clean string (134.0 -> 134, 1.2300 -> 1.23)."""
    if isinstance(x, (int, float)):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return x if x is not None else "?"


def _known_symbols():
    syms = set()
    try:
        syms |= set(read_json(universe_f(), {}).get("tickers", []) or [])
    except Exception:
        pass
    for it in read_json(suggest_f(), {}).get("items", []):
        if it.get("ticker"):
            syms.add(it["ticker"])
    for t in read_json(trades_f(), []):
        if t.get("ticker"):
            syms.add(t["ticker"])
    return syms


def _extract_ticker(text):
    """Best-effort ticker from a free-text message. $TICKER always wins; otherwise a token whose
    uppercase form is a known symbol (universe/suggestions/open trades), skipping lowercase common
    English words that happen to also be tickers ('it'/'on'/'all'). Falls back to a shouted ALL-CAPS token."""
    for c in re.findall(r"\$([A-Za-z]{1,5})", text):
        return c.upper()
    known = _known_symbols()
    for tok in re.findall(r"\b([A-Za-z]{1,5})\b", text):
        u = tok.upper()
        if u in _TG_COMMON_WORDS and tok != u:
            continue                     # lowercase everyday word — not a ticker
        if u in known:
            return u
    for tok in re.findall(r"\b([A-Z]{2,5})\b", text):   # last resort: an all-caps token not in the cache
        if tok not in _TG_COMMON_WORDS:
            return tok
    return None


def _num_after(text, keys):
    """First number following any of the regex key fragments (e.g. '@', 'stop', 'at')."""
    for k in keys:
        m = re.search(k + r"\s*\$?\s*(\d+(?:\.\d+)?)", text, re.I)
        if m:
            return float(m.group(1))
    return None


def _shares_in(text):
    m = re.search(r"(\d+)\s*(?:sh|shr|shrs|shares)\b", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\bx\s*(\d+)\b", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\s*@", text)          # "100 @ 120" — the count before the @ price is shares
    if m:
        return int(m.group(1))
    return None


def _bare_price(text, tkr):
    """Pull a loose price from a message when no '@'/'at' keyword precedes it (e.g. 'sold aehr 103.5').
    Strips the ticker and any share-count token first, then prefers a DECIMAL number (a price almost always
    has cents; a share count is a bare int) so '50sh nvda 134.2' -> 134.2, not 50."""
    s = re.sub(rf"\$?\b{re.escape(tkr)}\b", " ", text, flags=re.I) if tkr else text
    # drop clauses that carry their OWN number so they can't be mistaken for the entry/exit price
    s = re.sub(r"\b(?:stop|sl|target|tp|pt)\b\s*(?:at|@|to)?\s*\$?\s*\d+(?:\.\d+)?", " ", s, flags=re.I)
    s = re.sub(r"\b\d+\s*(?:sh|shr|shrs|shares)\b", " ", s, flags=re.I)   # drop "100 sh"
    s = re.sub(r"\bx\s*\d+\b", " ", s, flags=re.I)                        # drop "x100"
    s = re.sub(r"\b\d+\s*@", " ", s)                                      # drop the "100 @" share count
    decimals = re.findall(r"\d+\.\d+", s)
    if decimals:
        return float(decimals[-1])
    ints = re.findall(r"\b\d+\b", s)
    return float(ints[-1]) if ints else None


def _setup_in(low):
    if "deep pullback" in low:
        return "Deep Pullback"
    if "consolidat" in low:
        return "Consolidation"
    if "episodic" in low or "ep gap" in low:
        return "Episodic Pivot"
    if "avwap" in low:
        return "Pullback @ AVWAP"
    if "pullback" in low or "pull back" in low:
        return "Pullback"
    if "breakout" in low or "break out" in low or "broke out" in low:
        return "Breakout"
    return None


def _reason_after(raw, verbs, tkr):
    """Strip the ticker + the leading verb(s) from a message, leaving the trader's stated 'why'."""
    s = re.sub(rf"\$?\b{re.escape(tkr)}\b", "", raw, flags=re.I) if tkr else raw
    for v in sorted(verbs, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(v)}\b", "", s, flags=re.I)
    s = s.strip(" ,.-:;\t")
    return s or None


def _size_position(entry, stop, settings):
    tmp = {"entry": entry, "risk_ps": (entry - stop) if (entry and stop and entry > stop) else 0}
    apply_sizing(tmp, settings)
    return tmp


def _graded_sug(tkr):
    settings = read_json(settings_f(), {})
    sug = read_json(suggest_f(), {}).get("items", [])
    graded = grade_suggestions(sug, settings) if sug else []
    return next((s for s in graded if s.get("ticker") == tkr), None)


def _top_graded(n):
    settings = read_json(settings_f(), {})
    sug = read_json(suggest_f(), {}).get("items", [])
    graded = grade_suggestions(sug, settings) if sug else []
    graded.sort(key=lambda s: s.get("rating", 0), reverse=True)
    return graded[:n]


_TG_CLOSE = ("sold", "closed", "exited", "got out", "out of", "stopped out", "stop out", "take profit",
             "took profit", "selling", "close out", "dumped", "trimmed out", "cut ", "i'm out", "im out")
_TG_STOP = ("raise stop", "raise the stop", "raised stop", "raised the stop", "raised my stop",
            "move stop", "moved stop", "move the stop", "moved my stop", "move my stop", "stop to",
            "stop up", "stop now", "stop is now", "new stop", "set stop", "set the stop", "trail",
            "breakeven", "break even", "b/e", "tighten stop", "lower stop")
_TG_TOOK = ("took ", "bought", "entered", "got into", "got in", "long ", "filled", "starter", "started a",
            "i'm in", "im in", "adding", "add ", "grabbed", "opened", "open a", "took a")
_TG_PASS = ("passed", "pass on", "passing", "skipped", "skip ", "skipping", "avoided", "avoid", "not taking",
            "didn't take", "didnt take", "won't take", "wont take", "rejecting", "reject", "not buying")
_TG_WATCH = ("watching", "watch ", "keep an eye", "eyeing", "interested in", "on watch", "add to watch", "like ")
_TG_POS = ("positions", "what do i hold", "what am i holding", "my trades", "open trades", "holdings",
           "portfolio", "what i'm holding", "what im holding", "my book", "my positions")
_TG_PNL = ("how am i doing", "how'm i", "pnl", "p&l", "p and l", "my stats", "performance", "win rate",
           "how's my", "hows my", "my results", "equity", "how much")
_TG_SETUPS = ("setups", "setup ", "ideas", "what's good", "whats good", "top picks", "what should i buy",
              "anything good", "what to buy", "what's hot", "whats hot", "best setups", "gameplan",
              "what's up", "whats up", "any buys", "what now")


def _has(low, words):
    return any(w in low for w in words)


def _tg_help(greeting=False):
    head = "👋 I'm your trading coach. Just talk to me:" if greeting else "*What I can do:*"
    return (head + "\n"
            "• *took NVDA 100 @ 120 stop 115* — log a buy (I size & track it)\n"
            "• *passed AVGO, too extended* — log a skip + why\n"
            "• *sold NVDA at 134* / *stopped out of MSFT* — close a trade\n"
            "• *raise NVDA stop to 125* / *NVDA to breakeven* — move a stop\n"
            "• *watching SMCI* — add to your watch\n"
            "• *positions* — your open trades\n"
            "• *setups* / *what's good* — today's confirmed + armed ideas\n"
            "• *how am I doing* — P&L, win rate, R by setup\n"
            "• *NVDA?* — grade & plan for a ticker\n"
            "_Local only — I act while your Data Center is running._")


def _chat_take(raw, low, tkr):
    if not tkr:
        return "Which ticker did you take? e.g. *took NVDA 100 @ 120 stop 115*."
    settings = read_json(settings_f(), {})
    trades = read_json(trades_f(), [])
    existing = next((t for t in trades if t.get("ticker") == tkr and t.get("status") == "open"), None)
    adding = any(w in low for w in ("add", "another", "more", "again", "tranche", "second"))
    if existing and not adding:
        return (f"You already hold *{tkr}* (entry ${_f(existing.get('entry'))}, stop ${_f(existing.get('stop'))}). "
                f"Say *add {tkr} ...* to log a second tranche, or *close {tkr} at <price>* to close it.")
    sug = _graded_sug(tkr)
    entry = _num_after(low, [r"@", r"\bat\b", r"\bentry\b", r"\bfilled\b\s*(?:at|@)?", r"\bfor\b"])
    stop = _num_after(low, [r"\bstop\b", r"\bsl\b", r"\bstopped?\b\s*at"])
    target = _num_after(low, [r"\btarget\b", r"\btp\b", r"\bpt\b"])
    shares = _shares_in(low)
    setup = _setup_in(low) or (sug.get("setup_type") if sug else None) or "Breakout"
    if entry is None:
        entry = _bare_price(raw, tkr)             # "took AEHR 105.74" — your real fill beats the planned entry
    if entry is None and sug:
        entry = sug.get("entry")
    if entry is None:
        try:
            bars = scanner.get_bars(tkr)
            entry = bars[-1]["close"] if bars else None
        except Exception:
            entry = None
    if entry is None:
        return f"What price did you get filled on {tkr}? e.g. *took {tkr} @ 120 stop 115*."
    if stop is None and sug:
        stop = sug.get("stop")
    if shares is None and stop:
        shares = _size_position(entry, stop, settings).get("shares")
    body = {"ticker": tkr, "setup_type": setup, "status": "open", "entry": entry,
            "stop": stop, "initial_stop": stop,
            "target": target or (sug.get("target") if sug else None),
            "shares": shares, "taken_at": now_date(),
            "planned_entry": (sug.get("entry") if sug else None), "notes": raw}
    add_trade(body)
    ov = read_json(status_f(), {})                     # reflect on the dashboard overlay as 'taken'
    cur = ov.get(tkr, {})
    cur["status"] = "taken"
    ov[tkr] = cur
    write_json(status_f(), ov)
    lines = [f"✅ Logged *{tkr}* long — {setup}.",
             f"Entry ${_f(entry)}" + (f", stop ${_f(stop)}" if stop else ", *no stop set*")]
    if shares:
        extra = ""
        risk_ps = (entry - stop) if (entry and stop and entry > stop) else None
        if risk_ps:                                    # risk math off the ACTUAL share count
            dr = shares * risk_ps
            acct = settings.get("account_size")
            pct = f" ({dr / acct * 100:.2f}%)" if acct else ""
            extra = f" · risk ${_f(round(dr, 2))}{pct}"
        lines.append(f"{shares} sh{extra}")
    if not stop:
        lines.append(f"⚠️ No stop set — reply *raise {tkr} stop to <price>* so R stays measurable.")
    if existing and adding:
        lines.append("_(Logged as a second tranche.)_")
    return "\n".join(lines)


def _chat_close(raw, low, tkr):
    trades = read_json(trades_f(), [])
    open_t = [t for t in trades if t.get("status") == "open"]
    if not open_t:
        return "You have no open trades to close."
    if tkr:
        t = next((x for x in open_t if x.get("ticker") == tkr), None)
        if not t:
            return f"No open {tkr} position. Open: " + ", ".join(x["ticker"] for x in open_t) + "."
    elif len(open_t) == 1:
        t = open_t[0]
    else:
        return "Which one? Open: " + ", ".join(x["ticker"] for x in open_t) + ". e.g. *sold NVDA at 134*."
    exitp = _num_after(low, [r"@", r"\bat\b", r"\bfor\b", r"\bsold\b\s*(?:at|@|for)?", r"\bexit\b",
                             r"\bclosed?\b\s*(?:at|@)?"])
    if exitp is None:
        exitp = _bare_price(raw, t["ticker"])     # "sold aehr 103.5" — the loose number IS the exit
    if exitp is None:
        # NEVER assume a market price for a close — a wrong exit fakes the R. Ask.
        return f"What price did you exit {t['ticker']} at? e.g. *sold {t['ticker']} at 134*."
    lesson = None
    m = re.search(r"(?:lesson|learned|takeaway)\s*[:\-]?\s*(.+)", raw, re.I)
    if m:
        lesson = m.group(1).strip()
    close_trade(t["id"], {"exit": exitp, "exit_at": now_date(), "notes": raw, "lesson": lesson})
    t2 = next((x for x in read_json(trades_f(), []) if x["id"] == t["id"]), {})
    r, pnl = t2.get("result_r"), t2.get("realized_pnl")
    msg = [f"✅ Closed *{t['ticker']}* @ ${_f(exitp)}."]
    if r is not None:
        msg.append(f"Result: *{r:+.2f}R*" + (f" · ${pnl:+,.0f}" if isinstance(pnl, (int, float)) else ""))
    if lesson:
        msg.append(f"📝 Lesson saved: _{lesson}_")
    return "\n".join(msg)


def _chat_pass(raw, low, tkr):
    if not tkr:
        return "Which ticker did you pass on? e.g. *passed AVGO, too extended*."
    reason = _reason_after(raw, _TG_PASS, tkr)
    ov = read_json(status_f(), {})
    cur = ov.get(tkr, {})
    cur["status"] = "rejected"
    cur["reject_reason"] = reason or ""
    ov[tkr] = cur
    write_json(status_f(), ov)
    _NOW_CACHE.clear()       # drop it from the live buy/armed list immediately
    return f"👍 Marked *{tkr}* passed" + (f" — _{reason}_" if reason else "") + ". (Shows as rejected on the dashboard.)"


def _chat_watch(raw, low, tkr):
    if not tkr:
        return "Which ticker? e.g. *watching SMCI for the breakout*."
    note = _reason_after(raw, _TG_WATCH, tkr)
    ov = read_json(status_f(), {})
    cur = ov.get(tkr, {})
    cur["status"] = "approved"
    if note:
        cur["catalyst"] = note
    ov[tkr] = cur
    write_json(status_f(), ov)
    _NOW_CACHE.clear()
    return f"👀 Added *{tkr}* to your watch" + (f" — _{note}_" if note else "") + "."


def _chat_move_stop(raw, low, tkr):
    trades = read_json(trades_f(), [])
    open_t = [t for t in trades if t.get("status") == "open"]
    t = next((x for x in open_t if x.get("ticker") == tkr), None) if tkr else (open_t[0] if len(open_t) == 1 else None)
    if not t:
        if not open_t:
            return "No open trades to adjust."
        return "Which position? Open: " + ", ".join(x["ticker"] for x in open_t) + "."
    if "breakeven" in low or "break even" in low or "b/e" in low:
        newstop = t.get("entry")
    else:
        newstop = _num_after(low, [r"\bto\b", r"\bat\b", r"\bstop\b"])
    if newstop is None:
        # "raised stop docn 173.74" — the number isn't right after a keyword; take the loose price
        cl = re.sub(rf"\$?\b{re.escape(tkr)}\b", " ", raw, flags=re.I) if tkr else raw
        nums = re.findall(r"\d+(?:\.\d+)?", cl)
        dec = [n for n in nums if "." in n]
        if dec:
            newstop = float(dec[-1])
        elif nums:
            newstop = float(nums[-1])
    if newstop is None:
        return f"Move {t['ticker']}'s stop to what price? e.g. *raise {t['ticker']} stop to 125*."
    t["stop"] = newstop      # live stop only — initial_stop (risk basis) stays frozen so R is unchanged
    write_json(trades_f(), trades)
    regen_trades_md()
    _NOW_CACHE.clear()       # the coach/stop-out check picks up the new stop immediately
    init = t.get("initial_stop")
    be = " (breakeven)" if newstop == t.get("entry") else ""
    tail = f" Risk basis unchanged (init ${_f(init)}; R still measured off it)." if init is not None else ""
    return f"🔧 Moved *{t['ticker']}* stop to ${_f(newstop)}{be}.{tail}"


def _chat_positions():
    trades = enrich_trades(read_json(trades_f(), []))
    open_t = [t for t in trades if t.get("status") == "open"]
    if not open_t:
        return "📭 No open positions. Cash is a position."
    out = ["*Your open positions:*"]
    for t in open_t:
        co = t.get("coach") or {}
        act = co.get("action", "HOLD")
        emoji = {"EXIT": "🔴", "TRIM": "🟠", "RAISE STOP": "🟡", "GUARD STOP": "🛡️", "WATCH": "🔵", "HOLD": "🟢"}.get(act, "⚪")
        seg = []
        if t.get("entry"):
            seg.append(f"entry ${_f(t['entry'])}")
        if t.get("last"):
            seg.append(f"last ${_f(t['last'])}")
        if t.get("r_open") is not None:
            seg.append(f"{t['r_open']:+.2f}R")
        elif t.get("pnl_pct") is not None:
            seg.append(f"{t['pnl_pct']:+.1f}%")
        block = f"{emoji} *{t['ticker']}* {act} — " + " · ".join(seg)
        if co.get("reasons"):
            block += f"\n   ↳ {co['reasons'][0]}"
        out.append(block)
    return "\n".join(out)


def _chat_pnl():
    st = compute_stats()
    eq = compute_equity()
    out = ["*How you're doing:*",
           f"Equity ${eq['equity']:,.0f}  (base ${eq['base']:,.0f} + realized ${eq['realized']:+,.0f} "
           f"+ open ${eq['open']:+,.0f})"]
    if st.get("closed"):
        out.append(f"Closed {st['closed']} · win rate {st['win_rate']}% · avg {st['avg_r']:+.2f}R")
        for stp, v in st.get("by_setup", {}).items():
            out.append(f"   • {stp}: {v['n']} trades · {v['win_rate']}% win · {v['avg_r']:+.2f}R")
    else:
        out.append(f"No closed trades yet · {st.get('open', 0)} open.")
    return "\n".join(out)


def _chat_setups():
    try:
        n = compute_now()
    except Exception:
        n = {}
    out = []
    if n.get("verdict"):
        out.append("📣 " + n["verdict"])
    buys, armed = n.get("buys") or [], n.get("armed") or []
    if buys:
        out.append("\n*Confirmed now:*")
        for b in buys[:4]:
            out.append(f"🟢 *{b['ticker']}* {b.get('grade')} — {b.get('shares')} sh @ ${_f(b.get('entry'))}, "
                       f"stop ${_f(b.get('stop'))}")
    if armed:
        out.append("\n*Armed (waiting on trigger):*")
        for a in armed[:6]:
            out.append(f"⏳ *{a['ticker']}* {a.get('grade')} {a.get('setup_type')} — {a.get('confirm', '')}")
    if not buys and not armed:
        top = _top_graded(6)
        if top:
            out.append("*Top setups:*")
            for x in top:
                out.append(f"• *{x['ticker']}* {x.get('grade')} {x.get('setup_type')} — "
                           f"entry ${_f(x.get('entry'))}" + (f", stop ${_f(x.get('stop'))}" if x.get('stop') else ""))
    return "\n".join(out) or "No setups to show right now."


def _chat_ticker(tkr):
    trades = enrich_trades(read_json(trades_f(), []))
    held = next((t for t in trades if t.get("ticker") == tkr and t.get("status") == "open"), None)
    sug = _graded_sug(tkr)
    out = []
    if sug:
        out.append(f"*{tkr}* — {sug.get('grade')} · {sug.get('setup_type')}")
        if sug.get("entry"):
            out.append(f"Entry ${_f(sug['entry'])}" + (f", stop ${_f(sug['stop'])}" if sug.get('stop') else ""))
        if sug.get("zone_bottom") and sug.get("zone_top"):
            out.append(f"Buy-zone ${_f(sug['zone_bottom'])}–${_f(sug['zone_top'])}"
                       + (" · buyable now" if sug.get("buyable_now") else ""))
        if sug.get("why"):
            out.append("Why: " + sug["why"])
        if isinstance(sug.get("earnings_days"), int) and sug["earnings_days"] >= 0:
            out.append(f"⚠️ earnings in {sug['earnings_days']}d")
    else:
        out.append(f"*{tkr}* isn't in the current scan.")
        try:
            bars = scanner.get_bars(tkr)
            if bars:
                out.append(f"Last ${_f(bars[-1]['close'])}.")
        except Exception:
            pass
    if held:
        co = held.get("coach") or {}
        r = held.get("r_open")
        out.append(f"📌 You hold it: entry ${_f(held.get('entry'))}"
                   + (f", {r:+.2f}R" if r is not None else "")
                   + f", coach says {co.get('action', 'HOLD')}.")
    return "\n".join(out)


def handle_chat_message(text):
    """Interpret one inbound Telegram message and perform the action, returning the reply text. Pure
    keyword/structure parsing (no LLM) over the trading vocabulary."""
    _ctx.market = "us"
    _ctx.udir = DATA
    raw = (text or "").strip()
    low = raw.lower()
    if not raw:
        return None
    if low in ("/start", "start"):
        return _tg_help(greeting=True)
    if low in ("/help", "help", "?", "what can you do", "commands", "menu"):
        return _tg_help()
    if low in ("hi", "hey", "hello", "yo", "sup"):
        return _tg_help(greeting=True)

    tkr = _extract_ticker(raw)
    # order matters: a close ("took profit") must beat the entry verb ("took"); a stop-move and
    # watch must beat the entry verb too (so "add to watch" isn't read as a buy).
    if _has(low, _TG_CLOSE) or ("took" in low and any(w in low for w in ("profit", "off the table", "gains"))):
        return _chat_close(raw, low, tkr)
    if _has(low, _TG_STOP):
        return _chat_move_stop(raw, low, tkr)
    if _has(low, _TG_WATCH):
        return _chat_watch(raw, low, tkr)
    if _has(low, _TG_PASS):
        return _chat_pass(raw, low, tkr)
    if _has(low, _TG_TOOK):
        return _chat_take(raw, low, tkr)
    if _has(low, _TG_POS):
        return _chat_positions()
    if _has(low, _TG_PNL):
        return _chat_pnl()
    if _has(low, _TG_SETUPS):
        return _chat_setups()
    if tkr:
        return _chat_ticker(tkr)
    return ("🤔 Didn't catch that. Try:\n"
            "• *took NVDA 100 @ 120 stop 115*\n"
            "• *passed AVGO, too extended*\n"
            "• *sold NVDA at 134*\n"
            "• *positions* / *setups* / *how am I doing*\n"
            "Or *help* for the full list.")


def _tg_offset_f():
    return DATA / "telegram_offset.json"


def _telegram_bot_loop():
    """Local-only inbound loop: long-poll getUpdates, obey ONLY the configured chat_id, run each message
    through handle_chat_message, reply. Offset persisted so nothing is reprocessed across restarts; messages
    older than 6h are skipped (so we don't reply to ancient backlog on first run, but a text you sent while
    the app was briefly off still gets handled)."""
    _ctx.market = "us"
    _ctx.udir = DATA
    off = read_json(_tg_offset_f(), {}).get("offset", 0)
    while True:
        s = read_json(settings_owner_f(), {})
        token = (s.get("telegram_token") or "").strip()
        chat = (s.get("telegram_chat_id") or "").strip()
        if not token or not chat:
            time.sleep(30)               # creds not set yet — recheck (user may add them in Settings)
            continue
        try:
            res = _tg_api("getUpdates", {"offset": off, "timeout": 50}, timeout=60)
        except Exception:
            time.sleep(5)
            continue
        if not res or not res.get("ok"):
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            off = max(off, upd["update_id"] + 1)
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = (msg.get("text") or "").strip()
            frm = str((msg.get("chat") or {}).get("id") or "")
            if not text or frm != chat:
                continue                 # obey only the owner's chat; ignore non-text and strangers
            if msg.get("date") and time.time() - msg["date"] > 6 * 3600:
                continue                 # stale backlog — skip but still advance the offset
            try:
                reply = handle_chat_message(text)
            except Exception as e:
                reply = f"⚠️ Sorry, I hit an error handling that: {e}"
            if reply:
                tg_send_text(reply)
        write_json(_tg_offset_f(), {"offset": off})


def _intraday_rescan_loop():
    """Local only. During the REGULAR session, keep the armed/buy pool fresh WITHOUT a manual scan — the
    confirmation engine only live-watches names that were A/A+ in the LAST scan, so a new setup is invisible
    until a re-scan (the user kept having to scan manually before the bot reacted). Each tick does a FAST
    partial re-scan of the top names (~1 min) so it can run every few minutes; every ~6th tick it does a FULL
    discovery scan of the universe. Conservative so it never reproduces the earlier all-jobs-at-once freeze:
    staggered at boot, single-flight (claims SCAN's running flag), SKIPPED whenever any other heavy job is
    running, gated to regular hours. settings.intraday_rescan_min = cadence (default 5; 0 = off)."""
    _ctx.market = "us"
    _ctx.udir = DATA
    time.sleep(180)                                          # stagger past the launch catch-up jobs
    cycle = 0
    while True:
        mins = 5
        try:
            mins = int(read_json(settings_owner_f(), {}).get("intraday_rescan_min", 5) or 0)
            other_busy = any(j.get("running") for j in
                             (UNIVERSE, REFRESH, SUSPECT, SECTORH, NEWS, PREMKT, SPIN, GROUPS))
            claimed = False
            if mins > 0 and _us_regular_open() and not other_busy:
                with _scan_lock:                             # claim quickly so a user's manual scan still 409s fast
                    if not SCAN["running"]:
                        SCAN.update(running=True, current="intraday refresh")
                        claimed = True
            if claimed:
                try:
                    if cycle % 6 == 0:                       # ~every 30 min at a 5-min cadence: full discovery
                        sid = read_json(suggest_f(), {}).get("screener_id") \
                            or next((s.get("id") for s in read_json(screeners_f(), [])), None)
                        if sid:
                            run_scan(sid, max_age=0.5)        # NB: run_scan resets running=False at its end
                        else:
                            run_intraday_partial()
                    else:
                        run_intraday_partial()                # fast top-N refresh (~1 min)
                finally:
                    SCAN.update(running=False, current="")
                    _NOW_CACHE.clear()                        # live engine picks up the fresh scan now
                    cycle += 1
        except Exception:
            SCAN.update(running=False)
        time.sleep(max(120, (mins or 5) * 60))


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
    # auto EOD job (local only): finalize day P&L + run the post-close fresh scan + freeze the forward
    # snapshot. The loop's FIRST iteration runs immediately, so it also catches up if we launched after
    # the close — in the BACKGROUND, so the (multi-minute) close scan never blocks startup.
    threading.Thread(target=_forward_eod_loop, daemon=True).start()
    background = "--background" in sys.argv          # launched as a hidden startup program — no browser pop
    url = f"http://localhost:{PORT}"
    try:
        srv = _Server(("127.0.0.1", PORT), Handler)
    except OSError:
        # already running — just open the browser to the existing instance (unless we're the background boot)
        print(f"Data Center already running — opening {url}")
        if not background:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return
    # the notification ENGINE: recompute the verdict, fire desktop toasts, and append to the in-app
    # feed (the standalone Coach app, coach_app.py, displays it). Only the port-owning instance runs it.
    if os.name == "nt":
        threading.Thread(target=_now_watcher, daemon=True).start()
    # inbound Telegram chat bot (any OS): talk to the coach from your phone → it writes back into the app.
    # No-op until the user sets telegram_token + telegram_chat_id in Settings; obeys only that one chat.
    threading.Thread(target=_telegram_bot_loop, daemon=True).start()
    # keep the armed/buy pool fresh: periodic intraday re-scan so new setups surface without a manual scan.
    threading.Thread(target=_intraday_rescan_loop, daemon=True).start()
    print(f"Trading Data Center running at {url}  (close this window or Ctrl+C to stop)")
    if not background:
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
