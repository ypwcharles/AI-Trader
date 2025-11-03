import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


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
    "ON", "BIIB", "LULU", "CDW", "GFS"
]


def patch_today_open_for_symbol(symbol: str, out_dir: Path, base_url: str, token: str, timeout: float = 10.0):
    url = f"{base_url.rstrip('/')}/quote"
    r = requests.get(url, params={"symbol": symbol, "token": token}, timeout=timeout)
    r.raise_for_status()
    q = r.json()
    o = q.get("o")
    t = q.get("t")
    if o is None or not isinstance(t, (int, float)):
        return False, "missing o/t"
    q_date = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    if q_date != today_str:
        return False, f"quote not for today ({q_date})"

    fp = out_dir / f"daily_prices_{symbol}.json"
    if not fp.exists():
        return False, "daily json not found"

    with fp.open("r", encoding="utf-8") as f:
        data = json.load(f)

    series = data.setdefault("Time Series (Daily)", {})
    # Only set today's open; keep the entry minimal (no close to avoid look-ahead)
    series[today_str] = {"1. open": float(o)}

    with fp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return True, "patched"


def main():
    token = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_TOKEN")
    if not token:
        raise SystemExit("FINNHUB_API_KEY not set")
    base_url = os.getenv("FINNHUB_API_BASE", "https://finnhub.io/api/v1")

    out_dir = Path(__file__).resolve().parent
    symbols = list(all_nasdaq_100_symbols) + ["QQQ"]

    ok = 0
    for i, sym in enumerate(symbols, 1):
        try:
            success, msg = patch_today_open_for_symbol(sym, out_dir, base_url, token)
            if success:
                ok += 1
            else:
                pass
        except Exception as e:
            msg = str(e)
        if i % 30 == 0:
            time.sleep(1.05)  # rough 30/s limiter
        print(f"{sym}: {msg}")

    print(f"Patched {ok}/{len(symbols)} symbols")


if __name__ == "__main__":
    main()

