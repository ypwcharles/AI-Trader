import json
import os
import glob
from datetime import datetime, time
from zoneinfo import ZoneInfo


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

# 合并所有以 daily_price 开头的 json，逐文件一行写入 merged.jsonl
current_dir = os.path.dirname(__file__)
pattern = os.path.join(current_dir, 'daily_price*.json')
files = sorted(glob.glob(pattern))

output_file = os.path.join(current_dir, 'merged.jsonl')

with open(output_file, 'w', encoding='utf-8') as fout:
    for fp in files:
        basename = os.path.basename(fp)
        # 仅当文件名包含任一纳指100成分符号时才写入
        if not any(symbol in basename for symbol in all_nasdaq_100_symbols):
            continue
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 统一重命名："1. open" -> "1. buy price"；"4. close" -> "4. sell price"
        # 对于最新的一天，只保留并写入 "1. buy price"
        try:
            # 查找所有以 "Time Series" 开头的键
            series = None
            for key, value in data.items():
                if key.startswith("Time Series"):
                    series = value
                    break
            if isinstance(series, dict) and series:
                # 先对所有日期做键名重命名
                for d, bar in list(series.items()):
                    if not isinstance(bar, dict):
                        continue
                    if "1. open" in bar:
                        bar["1. buy price"] = bar.pop("1. open")
                    if "4. close" in bar:
                        bar["4. sell price"] = bar.pop("4. close")
                # 再处理最新日期：
                # 默认仅保留买入价以避免前瞻；但若已过收盘或显式允许，则保留完整（含卖出价）
                latest_date = max(series.keys())
                latest_bar = series.get(latest_date, {})
                if isinstance(latest_bar, dict):
                    # 判断是否应包含当日收盘价
                    market_tz = os.environ.get("MARKET_TZ", "America/New_York")
                    market_close_str = os.environ.get("MARKET_CLOSE", "16:00")
                    include_today_close_env = os.environ.get("MERGE_INCLUDE_TODAY_CLOSE", "").strip().lower()
                    include_today_close = include_today_close_env in ("1", "true", "yes", "on")

                    try:
                        ch, cm = [int(x) for x in market_close_str.split(":", 1)]
                    except Exception:
                        ch, cm = 16, 0

                    try:
                        now_market = datetime.now(ZoneInfo(market_tz))
                        close_dt = now_market.replace(hour=ch, minute=cm, second=0, microsecond=0)
                        market_closed_today = now_market >= close_dt
                    except Exception:
                        market_closed_today = False

                    # 仅当运行日与 latest_date 同一天且已过收盘，或强制开启时，保留完整；否则只保留开盘价
                    try:
                        today_market_str = datetime.now(ZoneInfo(market_tz)).strftime("%Y-%m-%d")
                    except Exception:
                        today_market_str = datetime.utcnow().strftime("%Y-%m-%d")

                    if include_today_close or (latest_date != today_market_str) or market_closed_today:
                        # 保留已重命名后的完整 K 线（含 "4. sell price" 时，前端可按收盘价计算资产）
                        pass  # series[latest_date] 已经是完整 bar
                    else:
                        # 开盘前/盘中：仅保留买入价字段
                        buy_val = latest_bar.get("1. buy price")
                        series[latest_date] = {"1. buy price": buy_val} if buy_val is not None else {}
                # 更新 Meta Data 描述
                meta = data.get("Meta Data", {})
                if isinstance(meta, dict):
                    meta["1. Information"] = "Daily Prices (buy price, high, low, sell price) and Volumes"
        except Exception:
            # 若结构异常则原样写入
            pass

        fout.write(json.dumps(data, ensure_ascii=False) + "\n")
