import argparse
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Iterable, Dict
import pandas as pd

import yfinance as yf


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


def fetch_one(symbol: str, start: str, end: str) -> Dict:
    end_exc = _bump_end_inclusive(end)
    df = yf.download(
        symbol,
        start=start,
        end=end_exc,           # make end inclusive for daily bars
        interval="1d",
        auto_adjust=False,
        progress=False,
        actions=False,
        prepost=False,
    )
    # Some symbols may return empty frames; handle gracefully
    ts: Dict[str, Dict[str, float]] = {}
    if not df.empty:
        # Flatten to single-level columns if yfinance returns MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(-1, axis=1)
        # Iterate with itertuples to avoid FutureWarnings casting Series
        for row in df.itertuples(index=True):
            date_str = _ensure_date_str(row.Index)
            o = getattr(row, "Open", None)
            h = getattr(row, "High", None)
            l = getattr(row, "Low", None)
            c = getattr(row, "Close", None)
            v = getattr(row, "Volume", None)
            ts[date_str] = {
                "1. open": float(o) if o is not None else None,
                "2. high": float(h) if h is not None else None,
                "3. low": float(l) if l is not None else None,
                "4. close": float(c) if c is not None else None,
                "5. volume": int(v) if v is not None else None,
            }
    # Determine last available date in the series (if any)
    last_avail = max(ts.keys()) if ts else None

    out = {
        "Meta Data": {
            "1. Information": "Daily Prices (open, high, low, close) and Volumes - Yahoo Finance",
            "2. Symbol": symbol,
            # Prefer actual last available date to avoid confusion
            "3. Last Refreshed": last_avail or end,
            "4. Output Size": "custom",
            "5. Time Zone": "US/Eastern",
        },
        "Time Series (Daily)": ts,
    }
    return out


def save_payload(symbol: str, payload: Dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"daily_prices_{symbol}.json"
    import json
    with fp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


def main():
    parser = argparse.ArgumentParser(description="Fetch daily prices using yfinance and save AlphaVantage-like JSON files.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: 400 days before today)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--symbols", nargs="*", help="Symbols to fetch (default: NASDAQ 100 + QQQ)")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end_date = args.end or today.strftime("%Y-%m-%d")
    start_date = args.start or (today - timedelta(days=400)).strftime("%Y-%m-%d")

    syms: Iterable[str] = args.symbols or [*all_nasdaq_100_symbols, "QQQ"]
    out_dir = Path(__file__).resolve().parent
    ok = 0
    for i, sym in enumerate(syms, 1):
        try:
            payload = fetch_one(sym, start_date, end_date)
            save_payload(sym, payload, out_dir)
            ok += 1
        except Exception as e:
            print(f"Error fetching {sym}: {e}")
        if i % 10 == 0:
            print(f"Progress: {i}/{len(list(syms))}")
    print(f"Done. Saved {ok}/{len(list(syms))} files to {out_dir}")


if __name__ == "__main__":
    main()
