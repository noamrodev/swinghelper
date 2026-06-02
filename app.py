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
    """Classify the market's benchmark indexes into a blended posture; store for dashboard + grade."""
    try:
        reg = scanner.market_regime(market())
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
    REFRESH.update(running=True, stage="Reading the market (SPX/QQQ/IWM)…", done=0, total=4)
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
    trail_n = 50 if patient else 9
    trail_label = "50 EMA" if patient else "9 EMA"

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

    reasons = []
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
    elif r_mult is not None and r_mult >= rubric.COACH_RAISE_R and stop and entry and stop < entry:
        action, tone = "RAISE STOP", "good"
        reasons.append(f"{rtxt} locked-in zone — raise the stop to breakeven (${entry}) so the trade can't turn red")
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

    return {"action": action, "tone": tone, "reasons": reasons, "e9": round(e9, 2),
            "e50": round(e50, 2), "trail_label": trail_label, "patient": patient, "armed": armed,
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


def grade_suggestions(items, settings):
    """Enrich scan items with sizing, theme/heat, news, earnings proximity, group leadership,
    and the composite 0-100 rating + letter grade. Mutates items in place and returns them
    sorted best-first. Canonical rubric: strategy/scoring.md (keep weights in sync)."""
    rev = reverse_themes()
    heat = {h["sector"]: h for h in read_json(sector_heat_f(), {}).get("sectors", [])}
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
    posture = read_json(market_f(), {}).get("posture", 55)               # 0-100 market regime
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


FORWARD_TOP_N = 50                      # how many of today's TOP suggestions to log each day
                                       # (50, not 10: the BEST setups are "worth waiting" and rarely
                                       # trigger same-day — a wider net captures the ones that DO have
                                       # an entry today, so the forward test still collects real data)


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
    # STABLE top-N set: rating, then raw `score` to break the rating-72 ties (NOT buyable-now-first, which
    # is a live-display concern that would make the frozen record depend on the capture-moment's quotes).
    graded = sorted(graded, key=lambda s: (s.get("rating", 0), s.get("score", 0)),
                    reverse=True)[:FORWARD_TOP_N]
    picks = [{"ticker": s["ticker"], "grade": s["grade"], "rating": s["rating"],
              "setup_type": s.get("setup_type"), "entry": s.get("entry"), "stop": s.get("stop"),
              "target": s.get("target"), "entry_type": s.get("entry_type"),
              # BOTH entry legs (pullback + breakout) so the forward test can score whichever actually
              # filled — a name that never dipped but broke out and ran is no longer logged as "no-fill".
              "entries": [{"kind": e.get("kind"), "entry_type": e.get("entry_type"),
                           "entry": e.get("entry"), "stop": e.get("stop"), "target": e.get("target")}
                          for e in (s.get("entries") or []) if e.get("entry") and e.get("stop")],
              "buyable_now": bool(s.get("buyable_now")), "trend_template": bool(s.get("trend_template")),
              "vcp": bool(s.get("vcp")), "theme": s.get("theme"),
              "theme_trend": s.get("theme_trend"), "close_at_signal": s.get("close")}
             for s in graded]
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


def _forward_eod_loop():
    """Local background heartbeat: check every ~30 min whether an EOD snapshot is due (per market)."""
    while True:
        _run_forward_eod_all()
        time.sleep(1800)


def _sim_forward(bars, trade_date, entry, stop, entry_type, setup_type=None):
    """`trade_date` is the session these picks are FOR (the day you'd act on the trigger — the snapshot
    is labeled by it). Looks at bars from trade_date onward (so it never fills on the prior close where
    the setup was identified — a buy-stop at that day's high would be a fake instant loss; real bug,
    fixed), waits for the entry trigger (buy-stop: a high >= entry; limit: a low <= entry), fills there,
    then exits on a daily close < the TRAILING EMA or a stop hit.
    TRAILING EMA = the **9 EMA** by default, but the **50 EMA** for the patient LONG-HOLD setups
    (Deep Pullback / Consolidation) — a strong 6-month leader caught deep at the 50 is held until it
    loses the 50 again, not the 9 (mirrors the live position coach + my-rules; the LITE case).
    The trade day hasn't printed yet → 'awaiting'. Trigger never hit → 'no-fill'. Returns {R,matured,exit,status}."""
    if not entry or not stop or entry <= stop:
        return None
    risk = entry - stop
    closes = [b["close"] for b in bars]
    patient = (setup_type or "").strip().lower() in ("deep pullback", "consolidation")
    trail = _ema_series(closes, 50 if patient else 9)     # long-hold leaders trail the 50, the rest the 9
    exit_label = "50ema" if patient else "9ema"
    after = [i for i, b in enumerate(bars) if b["time"] >= trade_date]   # the trade day onward
    if not after:
        return {"R": None, "matured": False, "exit": None, "status": "awaiting", "fi": None}
    start, end = after[0], min(len(bars) - 1, after[0] + 60)
    is_limit = (entry_type == "limit")
    fi = None                                              # fill index = first post-signal bar that triggers
    for j in range(start, end + 1):
        if (bars[j]["low"] <= entry) if is_limit else (bars[j]["high"] >= entry):
            fi = j
            break
    if fi is None:
        return {"R": None, "matured": False, "exit": None,
                "status": "no-fill" if (end - start) >= 3 else "awaiting", "fi": None}
    for j in range(fi, end + 1):
        b = bars[j]
        if b["low"] <= stop:
            px = b["open"] if b["open"] < stop else stop
            return {"R": round((px - entry) / risk, 2), "matured": True, "exit": "stop", "status": "matured", "fi": fi}
        if b["close"] < trail[j] and j > fi:               # trailing-EMA exit only AFTER the fill day
            return {"R": round((b["close"] - entry) / risk, 2), "matured": True, "exit": exit_label, "status": "matured", "fi": fi}
    return {"R": round((bars[end]["close"] - entry) / risk, 2),
            "matured": (end - fi) >= 5, "exit": "open", "status": "open", "fi": fi}


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


def score_forward():
    """Evaluate logged TOP-suggestion picks; per-day breakdown + lessons + an overall aggregate.
    Each pick is simulated forward with the house exit rules (close < 9-EMA or hard stop)."""
    log = read_json(forward_f(), {"snapshots": {}})
    snaps = log.get("snapshots", {})
    scored, pending, by_day = [], 0, []
    for date in sorted(snaps, reverse=True):
        snap = snaps[date]
        picks_out = []
        for p in snap.get("picks", []):
            bars = scanner.get_bars(p["ticker"])
            plan, r = _sim_forward_best(bars, date, p) if bars else (None, None)
            status = r["status"] if r else "no-data"
            R = r["R"] if r else None
            filled = bool(r and r.get("fi") is not None)
            # the leg that actually filled drives R / exit / status / chart levels; on a no-fill we keep
            # the primary entry as the reference (the leg falls back to it). A breakout that filled when
            # the pullback didn't now shows its OWN entry & "breakout" kind, not the missed pullback.
            out = {**p}
            if plan and filled:
                out.update({"entry": plan.get("entry"), "stop": plan.get("stop"),
                            "entry_type": plan.get("entry_type"), "filled_kind": plan.get("kind")})
            # idea PROGRESS: how far price has moved from the (reference) entry to the latest close,
            # regardless of whether a trigger filled — so a name that ran without ever giving its dip
            # still shows "the idea is +X%" (the user wants entrance-vs-now, not just fills).
            cur = bars[-1]["close"] if bars else None
            entry = out.get("entry")
            progress = round((cur - entry) / entry * 100, 1) if (cur and entry) else None
            picks_out.append({**out, "R": R, "matured": bool(r and r["matured"]),
                              "exit": r["exit"] if r else None, "fstatus": status,
                              "cur": cur, "progress_pct": progress})
            if r and r["matured"]:
                scored.append({**p, "date": date, "R": r["R"], "win": r["R"] > 0, "exit": r["exit"]})
            elif r and r["status"] == "open":
                pending += 1
        day_rs = [x["R"] for x in picks_out if x["R"] is not None]
        wins = [x for x in day_rs if x > 0]
        day_sum = {"n_scored": len(day_rs),
                   "avg_r": round(sum(day_rs) / len(day_rs), 2) if day_rs else None,
                   "win_rate": round(100 * len(wins) / len(day_rs)) if day_rs else None,
                   "matured": sum(1 for x in picks_out if x["matured"]),
                   "open": sum(1 for x in picks_out if x["fstatus"] == "open"),
                   "awaiting": sum(1 for x in picks_out if x["fstatus"] == "awaiting"),
                   "no_fill": sum(1 for x in picks_out if x["fstatus"] == "no-fill")}
        by_day.append({"date": date, "posture": snap.get("posture"),
                       "picks": sorted(picks_out, key=lambda x: -(x.get("rating") or 0)),
                       "summary": day_sum, "lesson": _day_lesson(picks_out)})
    def _agg_rs(rows):
        rs = [t["R"] for t in rows if t.get("R") is not None]
        if not rs:
            return None
        wins = [x for x in rs if x > 0]
        return {"n": len(rs), "avg_r": round(sum(rs) / len(rs), 2),
                "win_rate": round(100 * len(wins) / len(rs)),
                "pct_gt1R": round(100 * sum(1 for x in rs if x >= 1) / len(rs))}

    agg = _agg_rs(scored)
    if agg:
        agg["win_rate"] = round(agg["win_rate"], 1)
    # grade-vs-outcome + setup-vs-outcome: does an A actually beat a B? which setups pay? This turns the
    # rubric's "educated-guess" weights into evidence as matured picks accumulate.
    by_grade = {g: _agg_rs([t for t in scored if t.get("grade") == g]) for g in ("A+", "A", "B", "C", "D")}
    by_grade = {g: v for g, v in by_grade.items() if v}
    setups = {t.get("setup_type") for t in scored if t.get("setup_type")}
    by_setup = {st: _agg_rs([t for t in scored if t.get("setup_type") == st]) for st in setups}
    by_setup = {st: v for st, v in by_setup.items() if v}
    days = len(snaps)
    total_logged = sum(len(s.get("picks", [])) for s in snaps.values())
    recent = sorted(scored, key=lambda x: x["date"], reverse=True)[:20]
    return {"days_logged": days, "total_picks": total_logged, "matured": len(scored),
            "pending": pending, "aggregate": agg, "recent": recent, "by_day": by_day,
            "by_grade": by_grade, "by_setup": by_setup,
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
            "regime_live": regime_live, "market_state": market.get("market_state"),
            "exposure": exposure, "manage": manage, "buy_now": buy_now, "watch": watch,
            "avoid": avoid, "alerts": read_json(news_f(), {}).get("alerts", [])[:4],
            "lessons": _top_lessons(3), "bottom_line": bottom}


# --------------------------------------------------------------------------- #
# Prediction — a probabilistic forward read from all the data we have (NOT advice)
# --------------------------------------------------------------------------- #
def compute_prediction():
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

    drivers = []
    score = (posture - 55) / 10.0
    drivers.append({"text": f"Market regime: {label or 'n/a'} (posture {posture}/100)"
                    + (" · 🌙 live extended-hours read" if regime_live else ""),
                    "dir": "pos" if posture >= 60 else "neg" if posture < 45 else "neutral"})
    if regime_live:                       # the indexes are moving NOW (pre/after-hours) — surface it
        em = [i for i in indexes if i.get("ext_pct") is not None]
        avg_ext = _mean([i["ext_pct"] for i in em]) if em else 0
        if em and abs(avg_ext) >= 0.2:
            ms = market.get("market_state")
            when = "Pre-market" if ms in ("PRE", "PREPRE") else "After-hours"
            score += max(-1.5, min(1.5, avg_ext / 0.6))     # extended index move biases the lean
            drivers.append({"text": f"🌙 {when} index move: "
                            + ", ".join(f"{i['name']} {i['ext_pct']:+.1f}%" for i in em),
                            "dir": "pos" if avg_ext > 0 else "neg"})
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
    # 🌙 PRE/AFTER-HOURS sector moves — what's actually moving NOW (separate from the multi-day trend
    # above). A "cooling" group can still pop in pre-market; surface it so it's not missed (the
    # Photonics/Optics case). Live perf_1d during PRE/POST = the extended-hours move.
    pm_sectors = _premarket_sector_moves() if regime_live else {"when": None, "up": [], "down": []}
    up, dn = pm_sectors["up"], pm_sectors["down"]
    if up or dn:
        score += max(-0.8, min(0.8, (len(up) - len(dn)) * 0.2))
        seg = []
        if up:
            seg.append("leading " + ", ".join(f"{x['sector']} {x['pct']:+.1f}%" for x in up))
        if dn:
            seg.append("lagging " + ", ".join(f"{x['sector']} {x['pct']:+.1f}%" for x in dn))
        drivers.append({"text": f"🌙 {pm_sectors['when']} sector moves: " + "; ".join(seg),
                        "dir": "pos" if len(up) >= len(dn) else "neg"})
    score += (a_good - a_bad) * 0.5 + (t_good - t_bad) * 0.15
    if alerts:                                            # name the actual material catalysts driving it
        cat = "; ".join((("🚀 " if a["dir"] == "buy" else "🛑 " if a["dir"] == "avoid" else "👀 ") + a["title"])
                        for a in alerts[:3])
        drivers.append({"text": f"Catalysts: {cat}",
                        "dir": "pos" if a_good >= a_bad else "neg"})
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
    if pm_sectors["up"]:
        parts.append(f"{pm_sectors['when']}, money is poking into {', '.join(x['sector'] for x in pm_sectors['up'][:3])} "
                     f"— watch whether it holds into the open before committing (extended-hours moves often fade).")
    outlook = " ".join(parts)

    return {"computed_at": time.strftime("%Y-%m-%d %H:%M"),
            "lean": lean, "confidence": confidence, "score": round(score, 2),
            "outlook": outlook, "drivers": drivers,
            "rising": rising[:8], "slowing": slowing[:8], "falling": falling[:8],
            "posture": posture, "label": label, "breadth": breadth, "regime_live": regime_live,
            "pm_sectors": pm_sectors,
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
    """Recompute the blended market posture using LIVE index prices (overwrite today's close)."""
    idx = []
    for name, sym in scanner.INDEXES:
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
    ms = next((quotes[s.upper()].get("market_state") for _, s in scanner.INDEXES
               if quotes.get(s.upper())), None)
    extended = ms in ("PRE", "PREPRE", "POST", "POSTPOST")
    return {"posture": round(avg), "label": _regime_label(avg), "indexes": idx,
            "market_state": ms, "extended": extended}


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
    """The market regime to use RIGHT NOW. During pre/after-hours this re-blends SPX/QQQ/IWM from
    their extended-hours prices (live_posture) so the gameplan & prediction reflect what's moving
    NOW — not yesterday's close. Outside extended hours it's the stored daily regime (market.json).
    Index quotes are 30s-cached, so this is cheap."""
    market = read_json(market_f(), {})
    try:
        idxq = scanner.fetch_quotes([s for _, s in scanner.INDEXES])
        ms = next((idxq[s.upper()].get("market_state") for _, s in scanner.INDEXES
                   if idxq.get(s.upper())), None)
        if ms in ("PRE", "PREPRE", "POST", "POSTPOST"):
            lp = live_posture(idxq)
            if lp:
                lp.setdefault("computed_at", market.get("computed_at"))
                lp["live"] = True
                return lp
    except Exception:
        pass
    return {**market, "live": False}


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
        elif route == "coach-config":
            # the coach threshold NUMBERS, single-sourced in rubric.py — the frontend's live coach
            # recompute (web/app.js) reads these so they can't drift from the backend coach.
            self._json(rubric.coach_config())
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
        elif route == "prediction":
            self._json(compute_prediction())
        elif route == "forward":
            self._json(score_forward())
        elif route == "pnl-calendar":
            self._json(read_json(pnl_f(), {}))
        elif route == "live":
            params = parse_qs(urlparse(self.path).query)
            req = (params.get("symbols", [""])[0] or "").split(",")
            idxsyms = [sym for _, sym in scanner.INDEXES]
            allsyms = list(dict.fromkeys([s.strip().upper() for s in req if s.strip()] + idxsyms))[:120]
            quotes = scanner.fetch_quotes(allsyms)
            ms = next((quotes[s.upper()]["market_state"] for _, s in scanner.INDEXES
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
    def _create_trade(self, sug, body):
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

    def _add_trade(self, body):
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

    def _close_trade(self, tid, body):
        trades = read_json(trades_f(), [])
        t = next((x for x in trades if x["id"] == tid), None)
        if not t:
            return
        t["status"] = "closed"
        t["exit"] = body.get("exit")
        t["result_pct"] = body.get("result_pct")
        t["rules_followed"] = body.get("rules_followed")
        # realized P&L flows into the account balance
        e, sh, x = t.get("entry"), t.get("shares"), body.get("exit")
        # realized R measured off the INITIAL stop (the real risk taken), not a raised/breakeven stop
        istop = t.get("initial_stop") if t.get("initial_stop") is not None else t.get("stop")
        if e and x and istop is not None and e > istop:
            t["result_r"] = round((x - e) / (e - istop), 2)
        else:
            t["result_r"] = body.get("result_r")
        if e and sh and x:
            # realized P&L is recorded on the trade; equity (base + realized + open) is computed live
            # in compute_equity(), so we no longer mutate the typed-in base account_size here.
            t["realized_pnl"] = round((x - e) * sh, 2)
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
    # auto EOD job (local only): finalize day P&L + run the post-close fresh scan + freeze the forward
    # snapshot. The loop's FIRST iteration runs immediately, so it also catches up if we launched after
    # the close — in the BACKGROUND, so the (multi-minute) close scan never blocks startup.
    threading.Thread(target=_forward_eod_loop, daemon=True).start()
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
