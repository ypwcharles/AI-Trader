#!/usr/bin/env python3
"""
One-click runner: fetch latest prices (yfinance) -> merge -> start MCP -> run main -> shutdown MCP.

Usage examples:
  python scripts/run_all.py                       # fetch default window (last ~400 days) until today, run default config
  python scripts/run_all.py --start 2025-10-01    # custom start
  python scripts/run_all.py --end 2025-10-28      # custom end (inclusive)
  python scripts/run_all.py --config configs/default_config.json

This script respects .env via load_dotenv() and uses the same interpreter for all steps.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
AGENT_TOOLS = ROOT / "agent_tools"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    print("$", " ".join(cmd), f"(cwd={cwd or ROOT})")
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT))
    if check and proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc.returncode


def start_mcp() -> subprocess.Popen:
    # Start the MCP services manager in agent_tools directory
    print("Starting MCP services in background…")
    proc = subprocess.Popen([sys.executable, "start_mcp_services.py"], cwd=str(AGENT_TOOLS))
    return proc


def wait_for_mcp(host: str, ports: list[int], timeout_sec: float = 20.0) -> None:
    print(f"Waiting for MCP ports {ports} on {host}…")
    deadline = time.time() + timeout_sec
    urls = [f"http://{host}:{p}/mcp" for p in ports]
    while time.time() < deadline:
        ok = True
        for u in urls:
            try:
                r = requests.get(u, timeout=0.8)
                # Streamable-HTTP responds 406 Not Acceptable to GET on /mcp; treat it as alive
                if r.status_code not in (200, 202, 406):
                    ok = False
                    break
            except Exception:
                ok = False
                break
        if ok:
            print("MCP services are ready.")
            return
        time.sleep(0.5)
    raise RuntimeError("MCP services did not become ready in time")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Fetch prices -> merge -> start MCP -> run main.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: ~400 days ago)")
    parser.add_argument("--end", help="End date YYYY-MM-DD inclusive (default: today)")
    parser.add_argument("--symbols", nargs="*", help="Optional list of symbols to fetch")
    parser.add_argument("--config", default=str(ROOT / "configs" / "default_config.json"), help="Path to config JSON for main.py")
    args = parser.parse_args()

    # Compute dates
    today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
    start = args.start or None  # yfinance fetcher defaults to ~400 days when None
    end = args.end or today

    # 1) Fetch latest prices (yfinance)
    fetch_cmd = [sys.executable, str(DATA / "get_daily_price_yf.py")]
    # Pass interval from env if provided (e.g., "60m" for hourly)
    price_interval = os.getenv("PRICE_INTERVAL", "").strip()
    if price_interval:
        fetch_cmd += ["--interval", price_interval]
        if price_interval != "1d":
            period_days = os.getenv("PRICE_PERIOD_DAYS", "30").strip()
            if period_days.isdigit():
                fetch_cmd += ["--period-days", period_days]
    if start:
        fetch_cmd += ["--start", start]
    if end:
        fetch_cmd += ["--end", end]
    if args.symbols:
        fetch_cmd += ["--symbols", *args.symbols]
    run(fetch_cmd)

    # 2) Merge into merged.jsonl
    run([sys.executable, str(DATA / "merge_jsonl.py")])

    # 3) Start MCP services in background and wait for readiness
    mcp = start_mcp()
    try:
        host = os.getenv("MCP_HOST", "127.0.0.1")
        ports = [
            int(os.getenv("MATH_HTTP_PORT", "8000")),
            int(os.getenv("SEARCH_HTTP_PORT", "8001")),
            int(os.getenv("TRADE_HTTP_PORT", "8002")),
            int(os.getenv("GETPRICE_HTTP_PORT", "8003")),
        ]
        wait_for_mcp(host, ports, timeout_sec=30.0)

        # 4) Run main.py with selected config
        run([sys.executable, str(ROOT / "main.py"), args.config])
    finally:
        # Stop MCP services gracefully
        print("Stopping MCP services…")
        try:
            if mcp.poll() is None:
                if os.name == "posix":
                    os.kill(mcp.pid, signal.SIGTERM)
                else:
                    mcp.terminate()
                try:
                    mcp.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mcp.kill()
        except Exception as e:
            print("Warning: failed to stop MCP services:", e)


if __name__ == "__main__":
    main()
