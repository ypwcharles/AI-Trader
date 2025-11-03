#!/usr/bin/env python3
"""
Simple trading-hour scheduler for AI-Trader.

Runs scripts/run_all.py automatically during US market regular session
(09:30–16:00 America/New_York) at a configurable minute of each hour
(default :30), Monday–Friday. Skips weekends, and optionally skips
US market holidays. Uses a small state file to avoid duplicate runs
per slot and a lock to prevent overlapping jobs.

Environment variables (override defaults):
  MARKET_TZ=America/New_York
  MARKET_OPEN=09:30
  MARKET_CLOSE=16:00
  RUN_MINUTE=30                 # minute of each hour to run (0–59)
  PERIOD_MINUTES=60             # run every N minutes inside window
  SKIP_HOLIDAYS=1               # skip US market holidays when 1
  RUN_ONCE_IF_IN_WINDOW=0       # if 1, run once then exit if in window now
  CATCH_UP_OPEN=1               # if 1, when started after open, run once immediately for the open slot
  SCHED_CONFIG=configs/default_config.json  # forwarded to run_all.py
  RUN_TODAY=1                   # forwarded so main.py uses market-aware dates

Usage:
  python scripts/scheduler.py          # daemon loop (Ctrl+C to stop)
  RUN_ONCE_IF_IN_WINDOW=1 python scripts/scheduler.py   # single-shot
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

try:
    from zoneinfo import ZoneInfo
except Exception:  # Python <3.9 fallback (not expected here)
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@dataclass
class ScheduleCfg:
    tz: str = os.getenv("MARKET_TZ", "America/New_York")
    open_hm: str = os.getenv("MARKET_OPEN", "09:30")
    close_hm: str = os.getenv("MARKET_CLOSE", "16:00")
    run_minute: int = int(os.getenv("RUN_MINUTE", "30"))
    period_minutes: int = int(os.getenv("PERIOD_MINUTES", "60"))
    once: bool = os.getenv("RUN_ONCE_IF_IN_WINDOW", "0").strip().lower() in ("1", "true", "yes", "on")
    config: str = os.getenv("SCHED_CONFIG", str(ROOT / "configs" / "default_config.json"))
    skip_holidays: bool = os.getenv("SKIP_HOLIDAYS", "1").strip().lower() in ("1", "true", "yes", "on")
    catch_up_open: bool = os.getenv("CATCH_UP_OPEN", "1").strip().lower() in ("1", "true", "yes", "on")


def _now_market(cfg: ScheduleCfg) -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(cfg.tz))


def _parse_hm(hm: str) -> tuple[int, int]:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
        return h, m
    except Exception:
        return 9, 30


def _in_window(now_mkt: datetime, cfg: ScheduleCfg) -> bool:
    oh, om = _parse_hm(cfg.open_hm)
    ch, cm = _parse_hm(cfg.close_hm)
    start = now_mkt.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = now_mkt.replace(hour=ch, minute=cm, second=0, microsecond=0)
    # Monday=0 ... Friday=5
    if now_mkt.weekday() >= 5:
        return False
    if cfg.skip_holidays and _is_market_holiday(now_mkt.date()):
        return False
    return start <= now_mkt < end


def _holiday_provider():
    """Return a callable(date) -> bool for US market holidays.
    Tries holidays.financial.NYSE, then holidays.NYSE; falls back to no-holidays.
    """
    try:
        from holidays.financial import NYSE  # type: ignore
        cal = NYSE()
        return lambda d: d in cal
    except Exception:
        try:
            import holidays  # type: ignore
            if hasattr(holidays, "NYSE"):
                cal = holidays.NYSE()
                return lambda d: d in cal
        except Exception:
            pass
    # Fallback: no holiday
    return lambda d: False


_is_market_holiday = _holiday_provider()


def _slot_key(now_mkt: datetime, cfg: ScheduleCfg) -> str:
    # Identify the intended slot at current time
    minute = cfg.run_minute
    slot = now_mkt.replace(minute=minute, second=0, microsecond=0)
    # If we ticked after the minute within the hour (e.g., 10:31), slot is next hour:minute
    if now_mkt.minute > minute or (now_mkt.minute == minute and now_mkt.second > 0):
        slot = slot + timedelta(minutes=cfg.period_minutes)
    return slot.strftime("%Y-%m-%d %H:%M")


def _should_run_now(now_mkt: datetime, cfg: ScheduleCfg) -> bool:
    if not _in_window(now_mkt, cfg):
        return False
    # Fire when we are within the first 59 seconds of the target minute
    return now_mkt.minute == cfg.run_minute


def _state_path() -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    return DATA / ".scheduler_state.json"


def _load_state() -> Dict[str, str]:
    fp = _state_path()
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, str]) -> None:
    fp = _state_path()
    try:
        fp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _lock_path() -> Path:
    return DATA / ".run_all.lock"


def _acquire_lock() -> bool:
    # Very simple lock by creating file if not exists; remove on release
    fp = _lock_path()
    try:
        import os as _os
        fd = _os.open(str(fp), _os.O_CREAT | _os.O_EXCL | _os.O_RDWR)
        _os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _release_lock() -> None:
    try:
        _lock_path().unlink(missing_ok=True)  # py3.8+: not available; but our base is 3.12
    except TypeError:
        try:
            if _lock_path().exists():
                _lock_path().unlink()
        except Exception:
            pass


def run_once(cfg: ScheduleCfg) -> int:
    env = os.environ.copy()
    # ensure RUN_TODAY is on so main.py uses market-aware dates
    env.setdefault("RUN_TODAY", "1")
    env.setdefault("MARKET_TZ", cfg.tz)
    env.setdefault("MARKET_OPEN", cfg.open_hm)
    env.setdefault("MARKET_CLOSE", cfg.close_hm)
    cmd = [sys.executable, str(ROOT / "scripts" / "run_all.py"), "--config", cfg.config]
    print("[scheduler] Running:", " ".join(cmd))
    try:
        return subprocess.call(cmd, cwd=str(ROOT), env=env)
    except KeyboardInterrupt:
        return 130


def loop(cfg: ScheduleCfg) -> None:
    state = _load_state()
    print("[scheduler] Started with:", cfg)
    try:
        # Optional catch-up at market open slot, if starting after open
        if cfg.catch_up_open:
            now0 = _now_market(cfg)
            if _in_window(now0, cfg):
                oh, om = _parse_hm(cfg.open_hm)
                open_slot = now0.replace(hour=oh, minute=om, second=0, microsecond=0)
                open_key = open_slot.strftime("%Y-%m-%d %H:%M")
                if now0 >= open_slot and state.get(open_key) != "done":
                    if _acquire_lock():
                        try:
                            print("[scheduler] Catch-up: running open slot", open_key)
                            rc = run_once(cfg)
                            state[open_key] = "done" if rc == 0 else f"rc={rc}"
                            _save_state(state)
                        finally:
                            _release_lock()
        while True:
            now_mkt = _now_market(cfg)
            if _should_run_now(now_mkt, cfg):
                # Compute current slot key and check state
                slot = now_mkt.replace(second=0, microsecond=0)
                # Normalize slot to the configured minute
                slot = slot.replace(minute=cfg.run_minute)
                key = slot.strftime("%Y-%m-%d %H:%M")
                if state.get(key) == "done":
                    # already executed for this slot
                    pass
                else:
                    if _acquire_lock():
                        try:
                            rc = run_once(cfg)
                            state[key] = "done" if rc == 0 else f"rc={rc}"
                            # keep only recent ~200 keys to bound file size
                            if len(state) > 200:
                                # naive prune: drop oldest by key sort
                                for k in sorted(state.keys())[:-200]:
                                    state.pop(k, None)
                            _save_state(state)
                        finally:
                            _release_lock()
                    else:
                        print("[scheduler] Another run appears active; skipping this slot.")
            # Sleep to next minute boundary to reduce CPU usage
            now = datetime.utcnow()
            sleep = 60 - now.second
            time.sleep(max(1, min(sleep, 60)))
            if cfg.once:
                # Exit after the first eligible check if configured
                break
    except KeyboardInterrupt:
        print("[scheduler] Stopped by user.")


if __name__ == "__main__":
    cfg = ScheduleCfg()
    if cfg.once:
        # Run immediately if currently in window, otherwise exit silently
        if _in_window(_now_market(cfg), cfg):
            if _acquire_lock():
                try:
                    sys.exit(run_once(cfg))
                finally:
                    _release_lock()
        else:
            print("[scheduler] Not within trading window; exiting.")
            sys.exit(0)
    else:
        loop(cfg)
