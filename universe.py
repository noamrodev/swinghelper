"""
Universe builder for the Trading Data Center.

Assembles the full tradeable US stock universe and coarse-filters it to the "Market Leaders"
criteria, so the scanner runs on a real, auto-refreshed universe instead of a hand-pasted list.

Two keyless data sources (standard library only):
  1. NASDAQ Trader symbol directory  -> every listed US symbol (NASDAQ / NYSE / NYSE American).
  2. Yahoo crumb-authed batch quote   -> price, market cap, avg volume (cheap, ~100 symbols/call).

Pipeline: all symbols -> drop ETFs/warrants/units/test issues -> batch-quote -> keep
price > $10, mkt cap > $300M, price x avg-vol > $10M -> rank by dollar volume -> top N.
The technical leg (ADR, EMA stack, perf, setups) is done downstream by scanner.py.
"""
import urllib.request
import urllib.parse
import http.cookiejar
import json
import time

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

SYM_FILES = [
    ("nasdaqlisted", "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"),
    ("otherlisted", "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"),
]
# name fragments that mark non-common-stock instruments (warrants, units, preferreds, funds...)
_BAD_NAME = ("warrant", " unit", "units", " right", "depositary", "preferred",
             "% notes", "convertible", " etf", " etn", " fund", " trust ", "acquisition")
_EXCH_MAP = {"A": "AMEX", "N": "NYSE", "P": "ARCA", "Z": "BATS", "V": "IEX"}


def _http(url, opener=None, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    if opener is None:
        return urllib.request.urlopen(req, timeout=timeout).read()
    return opener.open(req, timeout=timeout).read()


def fetch_symbols(exchanges="all"):
    """Common-stock symbols from the NASDAQ Trader directory -> {symbol: exchange}.
    exchanges: 'all' (NASDAQ/NYSE/AMEX) | 'nyse_nasdaq' | 'nyse' | 'nasdaq'."""
    syms = {}
    for name, url in SYM_FILES:
        try:
            lines = _http(url).decode("utf-8", "replace").splitlines()
        except Exception:
            continue
        header = lines[0].split("|")
        idx = {h.strip(): i for i, h in enumerate(header)}
        for ln in lines[1:]:
            if ln.startswith("File Creation") or "|" not in ln:
                continue
            f = ln.split("|")
            try:
                if name == "nasdaqlisted":
                    sym = f[idx["Symbol"]].strip()
                    nm = f[idx["Security Name"]]
                    test = f[idx["Test Issue"]].strip()
                    etf = f[idx["ETF"]].strip()
                    exch = "NASDAQ"
                else:
                    sym = f[idx["ACT Symbol"]].strip()
                    nm = f[idx["Security Name"]]
                    test = f[idx["Test Issue"]].strip()
                    etf = f[idx["ETF"]].strip()
                    exch = _EXCH_MAP.get(f[idx["Exchange"]].strip(), f[idx["Exchange"]].strip())
            except Exception:
                continue
            if test == "Y" or etf == "Y":
                continue
            if not sym.isalpha() or not (1 <= len(sym) <= 5):
                continue                                  # drop class/warrant/unit suffixes & odd symbols
            if any(b in nm.lower() for b in _BAD_NAME):
                continue
            if exchanges == "nasdaq" and exch != "NASDAQ":
                continue
            if exchanges == "nyse" and exch != "NYSE":
                continue
            if exchanges == "nyse_nasdaq" and exch not in ("NYSE", "NASDAQ"):
                continue
            if exchanges == "all" and exch not in ("NASDAQ", "NYSE", "AMEX"):
                continue                                  # skip Arca/BATS/IEX (fund venues)
            syms[sym] = exch
    return syms


def _yahoo_session():
    """Cookie + crumb so the keyless batch-quote endpoint accepts us."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        _http("https://fc.yahoo.com", op, 8)
    except Exception:
        pass                                              # primes cookies; 4xx here is normal
    _http("https://finance.yahoo.com", op, 15)
    crumb = _http("https://query1.finance.yahoo.com/v1/test/getcrumb", op, 15).decode().strip()
    return op, crumb


def batch_quotes(symbols, progress=None, chunk=100):
    """{symbol: {price, mktcap, avgvol}} via Yahoo crumb quote, fetched in chunks."""
    op, crumb = _yahoo_session()
    out = {}
    total = len(symbols)
    for i in range(0, total, chunk):
        part = symbols[i:i + chunk]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?crumb="
               + urllib.parse.quote(crumb) + "&symbols=" + ",".join(part))
        ok = False
        for attempt in range(2):
            try:
                d = json.loads(_http(url, op, 20))
                for q in d.get("quoteResponse", {}).get("result", []):
                    out[q.get("symbol")] = {
                        "price": q.get("regularMarketPrice"),
                        "mktcap": q.get("marketCap"),
                        "avgvol": q.get("averageDailyVolume3Month") or q.get("regularMarketVolume"),
                    }
                ok = True
                break
            except Exception:
                try:                                      # crumb can expire -> refresh once and retry
                    op, crumb = _yahoo_session()
                    url = ("https://query1.finance.yahoo.com/v7/finance/quote?crumb="
                           + urllib.parse.quote(crumb) + "&symbols=" + ",".join(part))
                except Exception:
                    pass
        if progress:
            progress(min(i + chunk, total), total)
        time.sleep(0.1 if ok else 0.4)
    return out


def build_universe(exchanges="all", size=800, min_price=10, min_mktcap_m=300,
                   min_dollar_vol_m=10, progress=None):
    """Full pipeline -> ranked ticker list + stats. `progress(stage, total, done)` optional."""
    syms = fetch_symbols(exchanges)
    symbols = sorted(syms.keys())
    if progress:
        progress("symbols", len(symbols), len(symbols))
    quotes = batch_quotes(symbols, progress=(lambda d, t: progress("quotes", t, d)) if progress else None)
    rows = []
    for sym, info in quotes.items():
        p, mc, av = info["price"], info["mktcap"], info["avgvol"]
        if not p or not mc or not av:
            continue
        if p < min_price or mc < min_mktcap_m * 1e6:
            continue
        dollar_vol = p * av
        if dollar_vol < min_dollar_vol_m * 1e6:
            continue
        rows.append((sym, dollar_vol, p, mc))
    rows.sort(key=lambda r: r[1], reverse=True)
    top = rows[:size]
    return {
        "tickers": [r[0] for r in top],
        "universe_total": len(symbols),
        "passed_filter": len(rows),
        "kept": len(top),
        "exchanges": exchanges,
        "params": {"size": size, "min_price": min_price, "min_mktcap_m": min_mktcap_m,
                   "min_dollar_vol_m": min_dollar_vol_m},
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    def prog(stage, total, done):
        print(f"  [{stage}] {done}/{total}", end="\r")
    print("Building universe (all US, top 800 by liquidity)...")
    u = build_universe(progress=prog)
    print()
    print(f"universe_total={u['universe_total']}  passed_filter={u['passed_filter']}  kept={u['kept']}")
    print("top 25:", u["tickers"][:25])
    for must in ("NVDA", "ASTS", "QUBT", "DOCN", "INOD", "MSFT"):
        print(f"  {must} in universe: {must in u['tickers']}")
