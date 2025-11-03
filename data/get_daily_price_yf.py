import argparse
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Iterable, Dict, Optional
import pandas as pd

import yfinance as yf
try:
    from zoneinfo import ZoneInfo
except Exception:  # py<3.9 fallback not expected on our base image
    ZoneInfo = None  # type: ignore


all_nasdaq_100_symbols = [
    "NVDA", "MSFT", "AAPL", "GOOG", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
    "NFLX", "PLTR", "COST", "ASML", "AMD", "CSCO", "AZN", "TMUS", "MU", "LIN",
    "PEP", "SHOP", "APP", "INTU", "AMAT", "LRCX", "PDD", "QCOM", "ARM", "INTC",
    "BKNG", "AMGN", "TXN", "ISRG", "GILD", "KLAC", "PANW", "ADBE", "HON",
    "CRWD", "CEG", "ADI", "ADP", "DASH", "CMCSA", "VRTX", "MELI", "SBUX",
    "CDNS", "ORLY", "SNPS", "MSTR", "MDLZ", "ABNB", "MRVL", "CTAS", "TRI",
    "MAR", "MNST", "CSX", "ADSK", "PYPL", "FTNT", "AEP", "WDAY", "REGN", "ROP",
    "NXPI", "DDOG", "AXON", "ROST", "IDXX", "EA", "PCAR", "FAST", "EXC", "TTWO",
    "XEL", "ZS", "PAYX", "WBD", "BKR", "CPRT", "CCEP", "FANG", "TEAM", "CHTR",
    "KDP", "MCHP", "GEHC", "VRSK", "CTSH", "CSGP", "KHC", "ODFL", "DXCM", "TTD",
    "ON", "BIIB", "LULU", "CDW", "GFS",
    # Additional user-requested symbols
    "BABA", "COIN", "HOOD", "IBIT", "ETHA", "ASTS", "RKLB", "RBLX", "FNMA",
    "CRWV", "GLD", "SLV"
]


def _ensure_date_str(d) -> str:
    """Return YYYY-MM-DD for a datetime/date/Timestamp, normalized to UTC and naive."""
    if isinstance(d, datetime):
        dt = d
    elif isinstance(d, date):
        dt = datetime.combine(d, datetime.min.time())
    else:
        # Pandas Timestamp or like
        try:
            dt = d.to_pydatetime()
        except Exception:
            dt = datetime.fromisoformat(str(d))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d")


def _bump_end_inclusive(end: str) -> str:
    """Yahoo/yfinance's `end` is exclusive for most intervals.
    Bump by +1 day so that a daily bar for `end` date is included when available.
    """
    d = datetime.strptime(end, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def _to_market_time_str(ts: datetime, market_tz: str) -> str:
    """Return YYYY-MM-DD HH:MM:SS in market timezone for a pandas index timestamp."""
    if ZoneInfo is None:
        # best effort: treat as naive and return as-is string
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    z = ZoneInfo(market_tz)
    # pandas may give naive or tz-aware timestamps
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_local = ts.astimezone(z)
    return ts_local.strftime("%Y-%m-%d %H:%M:%S")


def fetch_one(symbol: str, start: str, end: str, interval: str = "1d", period_days: Optional[int] = None, market_tz: str = "America/New_York") -> Dict:
    end_exc = _bump_end_inclusive(end)
    # For intraday, Yahoo sometimes limits long ranges; if period_days is given, compute start accordingly when start is None
    start_arg = start
    if interval != "1d" and (not start_arg) and period_days:
        start_arg = (datetime.now(timezone.utc).date() - timedelta(days=period_days)).strftime("%Y-%m-%d")

    df = yf.download(
        symbol,
        start=start_arg,
        end=end_exc,
        interval=interval,
        auto_adjust=False,
        progress=False,
        actions=False,
        prepost=False,
    )
    ts: Dict[str, Dict[str, float]] = {}
    key_name = "Time Series (Daily)" if interval == "1d" else "Time Series (60min)"
    if not df.empty:
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(-1, axis=1)
        for row in df.itertuples(index=True):
            idx = row.Index
            if interval == "1d":
                tkey = _ensure_date_str(idx)
            else:
                tkey = _to_market_time_str(idx, market_tz)
            o = getattr(row, "Open", None)
            h = getattr(row, "High", None)
            l = getattr(row, "Low", None)
            c = getattr(row, "Close", None)
            v = getattr(row, "Volume", None)
            ts[tkey] = {
                "1. open": float(o) if o is not None else None,
                "2. high": float(h) if h is not None else None,
                "3. low": float(l) if l is not None else None,
                "4. close": float(c) if c is not None else None,
                "5. volume": int(v) if v is not None else None,
            }
    last_avail = max(ts.keys()) if ts else None

    out = {
        "Meta Data": {
            "1. Information": ("Daily Prices (open, high, low, close) and Volumes - Yahoo Finance" if interval == "1d" else "Intraday 60min Prices (open, high, low, close) and Volumes - Yahoo Finance"),
            "2. Symbol": symbol,
            "3. Last Refreshed": last_avail or (end if interval == "1d" else f"{end} 16:00:00"),
            "4. Output Size": "custom",
            "5. Time Zone": "US/Eastern",
        },
        key_name: ts,
    }
    return out


def _merge_into(path: Path, payload: Dict, key_name: str) -> Dict:
    """Merge payload[key_name] into existing file's same key if exists, new wins."""
    if not path.exists():
        return payload
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return payload
    old_ts = old.get(key_name, {}) if isinstance(old, dict) else {}
    new_ts = payload.get(key_name, {}) if isinstance(payload, dict) else {}
    merged_ts = {**old_ts, **new_ts}
    merged = payload.copy()
    merged[key_name] = merged_ts
    if "Meta Data" not in merged and isinstance(old, dict) and "Meta Data" in old:
        merged["Meta Data"] = old["Meta Data"]
    return merged


def save_payload(symbol: str, payload: Dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"daily_prices_{symbol}.json"
    import json
    # Merge on the relevant key to preserve prior bars
    key_name = "Time Series (60min)" if "Time Series (60min)" in payload else "Time Series (Daily)"
    payload = _merge_into(fp, payload, key_name)
    with fp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)
    # Special-case QQQ benchmark duplication
    if symbol == "QQQ" and key_name == "Time Series (60min)":
        fp2 = out_dir / f"Adaily_prices_{symbol}.json"
        merged2 = _merge_into(fp2, payload, key_name)
        with fp2.open("w", encoding="utf-8") as f2:
            json.dump(merged2, f2, ensure_ascii=False, indent=4)


def main():
    parser = argparse.ArgumentParser(description="Fetch daily prices using yfinance and save AlphaVantage-like JSON files.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: 400 days before today)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--symbols", nargs="*", help="Symbols to fetch (default: NASDAQ 100 + QQQ)")
    parser.add_argument("--interval", default="1d", choices=["1d", "60m"], help="Price interval: 1d (daily) or 60m (hourly)")
    parser.add_argument("--period-days", type=int, help="When interval is 60m and start is omitted, look back this many days")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end_date = args.end or today.strftime("%Y-%m-%d")
    if args.interval == "1d":
        start_date = args.start or (today - timedelta(days=400)).strftime("%Y-%m-%d")
    else:
        # For hourly, default to 30 days if not provided
        start_date = args.start or None

    syms: Iterable[str] = args.symbols or [*all_nasdaq_100_symbols, "QQQ"]
    out_dir = Path(__file__).resolve().parent
    ok = 0
    for i, sym in enumerate(syms, 1):
        try:
            payload = fetch_one(sym, start_date, end_date, interval=args.interval, period_days=args.period_days or (30 if args.interval != "1d" else None))
            save_payload(sym, payload, out_dir)
            ok += 1
        except Exception as e:
            print(f"Error fetching {sym}: {e}")
        if i % 10 == 0:
            print(f"Progress: {i}/{len(list(syms))}")
    print(f"Done. Saved {ok}/{len(list(syms))} files to {out_dir}")


if __name__ == "__main__":
    main()
