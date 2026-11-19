#!/usr/bin/env python3
"""
APEX-QUANT-OMNI SOVEREIGN (v90.0 — ZERO-WHIPSAW EDITION)
Spot Gold (XAUUSD) Institutional Multi-Disciplinary Quantitative Signal & Execution Engine

7-Discipline Quantitative Synthesis (Strictly Causal, Zero Look-Ahead):
1. Smart Money Structure (SMC): Liquidity Sweeps (1-bar/2-bar), BOS, BOS-Retest, ChoCh & S/R Headroom
2. Market Microstructure: Bulk Volume Classification (BVC) VPIN-Proxy, CLV Effort & Body-to-Wick Filter
3. Econophysics & Regime Science: Rolling Hurst Exponent (R/S), Kaufman Efficiency Ratio, Shannon Entropy
4. Statistical Fair Value: Anchored Daily UTC Session VWAP + EMA20 Z-Score Anti-Climax Guard
5. Cross-Asset Macro Drivers: Causal 1H DXY (DX-Y.NYB) & US 10Y Yield (^TNX) Alignment
6. Econometric Validation: 70/30 Purged Walk-Forward OOS with 16-Bar Embargo, SQN & Regularized PF
7. Zero-Whipsaw & Precision Systems: Spike/Outlier Guard, SQLite Cooldown Lock, Vol-Kelly, WAL, Circuit Breaker
"""

import os
import re
import math
import time
import html
import json
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

# ==============================================================================
# LOGGING & GLOBAL CONSTANTS
# ==============================================================================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("APEX-QUANT-OMNI")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)

BOT_VERSION = "v90.0"
BOT_EDITION = "APEX-QUANT-OMNI SOVEREIGN v90.0 (ZERO-WHIPSAW EDITION)"
EPSILON = 1e-8
DB_PATH = os.getenv("SQLITE_DB_PATH", "trading_signals.db")
CHART_PATH = os.getenv("BACKTEST_CHART_PATH", "backtest_performance.png")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
CONTRACT_OZ = float(os.getenv("XAU_CONTRACT_OZ", "100.0"))
BASE_CONFLUENCE_THRESHOLD = int(os.getenv("CONFLUENCE_THRESHOLD", "70"))
MAX_ALLOWED_SPREAD_USD = float(os.getenv("MAX_ALLOWED_SPREAD_USD", "3.00"))
FORCE_NOTIFY = os.getenv("FORCE_NOTIFY", "false").lower() in ("1", "true", "yes")
REQUIRE_POSITIVE_BACKTEST = os.getenv("REQUIRE_POSITIVE_BACKTEST", "false").lower() in ("1", "true", "yes")
ENABLE_MACRO_DRIVERS = os.getenv("ENABLE_MACRO_DRIVERS", "true").lower() in ("1", "true", "yes")
ENABLE_VOL_KELLY_SIZING = os.getenv("ENABLE_VOL_KELLY_SIZING", "true").lower() in ("1", "true", "yes")

NEWS_BLACKOUT_BEFORE_MIN = int(os.getenv("NEWS_BLACKOUT_BEFORE_MIN", "45"))
NEWS_BLACKOUT_AFTER_MIN = int(os.getenv("NEWS_BLACKOUT_AFTER_MIN", "30"))
NEWS_CACHE_TTL_MINUTES = int(os.getenv("NEWS_CACHE_TTL_MINUTES", "15"))
NEWS_DEGRADED_TTL_MINUTES = int(os.getenv("NEWS_DEGRADED_TTL_MINUTES", "2"))

MAX_DATA_STALE_MINUTES = int(os.getenv("MAX_DATA_STALE_MINUTES", "45"))
MAX_DAILY_LOSS_R = float(os.getenv("MAX_DAILY_LOSS_R", "3.0"))
MIN_BT_TRADES = int(os.getenv("MIN_BT_TRADES", "8"))
PURGED_EMBARGO_BARS = int(os.getenv("PURGED_EMBARGO_BARS", "16"))
MAX_POLLING_RUNTIME_SEC = int(os.getenv("MAX_POLLING_RUNTIME_SEC", "0"))
SIGNAL_COOLDOWN_MINUTES = int(os.getenv("SIGNAL_COOLDOWN_MINUTES", "60"))

MAX_EXTENSION_ATR = 1.50
MAX_ENTRY_DRIFT_ATR = 0.65
MIN_ORDERFLOW_CONFIRM = 0.12
MIN_PRIORITY_PILLAR_NORM = 60.0
MIN_KAUFMAN_ER = float(os.getenv("MIN_KAUFMAN_ER", "0.18"))
MAX_SHANNON_ENTROPY = float(os.getenv("MAX_SHANNON_ENTROPY", "0.94"))
MIN_SR_HEADROOM_ATR = float(os.getenv("MIN_SR_HEADROOM_ATR", "0.35"))
MAX_SPIKE_ATR_MULT = float(os.getenv("MAX_SPIKE_ATR_MULT", "4.5"))
SIM_SLIPPAGE_USD = float(os.getenv("SIM_SLIPPAGE_USD", "0.15"))
MIN_SL_USD = float(os.getenv("MIN_SL_USD", "3.00"))

WIB_TZ = timezone(timedelta(hours=7))
NY_TZ = ZoneInfo("America/New_York")

TRANCHE_TP1_WEIGHT = 0.50
TRANCHE_TP2_WEIGHT = 0.30
TRANCHE_TP3_WEIGHT = 0.20

# 6-Pillar Multi-Disciplinary Confluence Architecture (Exact Sum = 1.00)
PILLAR_WEIGHTS = {
    "smc": 0.28,          # Smart Money Structure (Sweep / BOS-Retest / FVG): 28%
    "order_flow": 0.22,   # Microstructure (BVC VPIN-Proxy + Effort/Result): 22%
    "regime": 0.15,       # Econophysics (Hurst + Kaufman ER + Shannon Entropy): 15%
    "vwap_zscore": 0.15,  # Session VWAP Fair Value + Z-Score Pullback: 15%
    "macro": 0.10,        # Cross-Asset Macro (Causal DXY + US10Y Yield): 10%
    "mtf": 0.10,          # Multi-Timeframe 1H/15M & Garman-Klass Harmony: 10%
}
assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 1e-9, "PILLAR_WEIGHTS must sum to 1.00"
assert abs((TRANCHE_TP1_WEIGHT + TRANCHE_TP2_WEIGHT + TRANCHE_TP3_WEIGHT) - 1.0) < 1e-9

CRITICAL_NEWS_KEYWORDS = (
    "non-farm", "nonfarm", "nfp", "fomc", "federal funds", "interest rate",
    "powell", "cpi", "core cpi", "ppi", "pce", "gdp", "retail sales",
    "unemployment", "ism manufacturing", "ism services", "jolts", "adp",
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def format_utc_wib(dt_utc: datetime) -> str:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_utc = dt_utc.astimezone(timezone.utc)
    dt_wib = dt_utc.astimezone(WIB_TZ)
    return f"{dt_utc.strftime('%H:%M UTC')} / {dt_wib.strftime('%H:%M WIB')}"


def _format_minutes_human(total_min: float) -> str:
    m = max(0, int(round(total_min)))
    return f"{m // 60}j {m % 60}m" if m >= 60 else f"{m}m"


def is_fx_weekend_closed(now_utc: datetime) -> bool:
    wd = now_utc.weekday()
    hh = now_utc.hour
    return (wd == 4 and hh >= 22) or wd == 5 or (wd == 6 and hh < 22)


# ==============================================================================
# SQLITE PERSISTENCE LAYER (WAL + SAFE MIGRATIONS + CLEAN CHECKPOINT)
# ==============================================================================
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            decision TEXT NOT NULL,
            score INTEGER NOT NULL,
            entry_price REAL,
            sl REAL,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            lot_size REAL,
            spread_usd REAL,
            adx REAL,
            z_score REAL,
            order_flow REAL,
            hurst REAL,
            kaufman_er REAL,
            entropy REAL,
            macro_score REAL,
            smc_state TEXT,
            mtf_state TEXT,
            news_status TEXT,
            source TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at_utc TEXT NOT NULL,
            opened_bar_utc TEXT,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            initial_sl REAL NOT NULL,
            current_sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            lot_size REAL NOT NULL,
            score INTEGER NOT NULL,
            status TEXT NOT NULL,
            tp1_hit INTEGER NOT NULL DEFAULT 0,
            tp2_hit INTEGER NOT NULL DEFAULT 0,
            locked_trail_r REAL NOT NULL DEFAULT 0.0,
            cost_r REAL NOT NULL DEFAULT 0.0,
            closed_at_utc TEXT,
            exit_price REAL,
            realized_r REAL DEFAULT 0.0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_cache (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            fetched_at_utc TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            is_degraded INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Idempotent migrations for pre-existing v76.x / v77.x SQLite files
    _ensure_column(conn, "signals_audit", "hurst", "REAL DEFAULT 0.5")
    _ensure_column(conn, "signals_audit", "kaufman_er", "REAL DEFAULT 0.0")
    _ensure_column(conn, "signals_audit", "entropy", "REAL DEFAULT 0.5")
    _ensure_column(conn, "signals_audit", "macro_score", "REAL DEFAULT 50.0")
    _ensure_column(conn, "active_trades", "opened_bar_utc", "TEXT")
    _ensure_column(conn, "active_trades", "tp2_hit", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "active_trades", "locked_trail_r", "REAL NOT NULL DEFAULT 0.0")
    _ensure_column(conn, "active_trades", "cost_r", "REAL NOT NULL DEFAULT 0.0")
    _ensure_column(conn, "news_cache", "is_degraded", "INTEGER NOT NULL DEFAULT 0")

    conn.commit()
    return conn


def close_db(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.warning(f"SQLite WAL checkpoint skipped: {exc}")
    finally:
        conn.close()


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def forensic_audit_log(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute("""
        INSERT INTO signals_audit (
            timestamp_utc, decision, score, entry_price, sl, tp1, tp2, tp3,
            lot_size, spread_usd, adx, z_score, order_flow, hurst, kaufman_er,
            entropy, macro_score, smc_state, mtf_state, news_status, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record["timestamp_utc"], record["decision"], record["score"],
        record["entry_price"], record["sl"], record["tp1"], record["tp2"],
        record["tp3"], record["lot_size"], record["spread_usd"], record["adx"],
        record["z_score"], record["order_flow"], record["hurst"],
        record["kaufman_er"], record["entropy"], record["macro_score"],
        record["smc_state"], record["mtf_state"], record["news_status"], record["source"],
    ))
    conn.commit()


def check_signal_cooldown(
    conn: sqlite3.Connection,
    bar_time_utc: datetime,
    bias: str,
    smc_state: str,
    smc_norm: float,
    now_utc: datetime,
) -> tuple[bool, str]:
    """
    SQLite Deduplication & Anti-Whipsaw Cooldown Lock:
    - Blocks duplicate signals on the exact same M15 bar.
    - Blocks sudden opposite-direction whipsaw flips within SIGNAL_COOLDOWN_MINUTES
      unless validated by a confirmed ChoCh or Liquidity Sweep (>= 90 pts).
    """
    if bias not in ("BUY", "SELL"):
        return True, "Standby"

    last_bar = get_state(conn, "last_signal_bar_utc", "")
    last_side = get_state(conn, "last_signal_side", "")
    last_time_str = get_state(conn, "last_signal_time_utc", "")

    cur_bar_iso = bar_time_utc.astimezone(timezone.utc).isoformat()
    if last_bar == cur_bar_iso and last_side == bias:
        return False, "Terkunci (Duplikat Bar)"

    if last_side in ("BUY", "SELL") and last_side != bias and last_time_str:
        try:
            last_dt = datetime.fromisoformat(last_time_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_min = (now_utc - last_dt).total_seconds() / 60.0
            structural_flip = ("SWEEP" in smc_state or "CHOCH" in smc_state) and smc_norm >= 90.0
            if elapsed_min < SIGNAL_COOLDOWN_MINUTES and not structural_flip:
                rem = max(1, int(round(SIGNAL_COOLDOWN_MINUTES - elapsed_min)))
                return False, f"Terkunci (Anti-Whipsaw {rem}m)"
        except Exception:
            pass

    return True, "Lolos"


def record_signal_cooldown(
    conn: sqlite3.Connection, bar_time_utc: datetime, bias: str, now_utc: datetime
) -> None:
    set_state(conn, "last_signal_bar_utc", bar_time_utc.astimezone(timezone.utc).isoformat())
    set_state(conn, "last_signal_side", bias)
    set_state(conn, "last_signal_time_utc", now_utc.astimezone(timezone.utc).isoformat())


# ==============================================================================
# REAL-TIME ECONOMIC CALENDAR SCRAPER & HARD BLACKOUT GOVERNOR
# ==============================================================================
def _infer_rollover_year(month_num: int, now_utc: datetime) -> int:
    if month_num == 1 and now_utc.month == 12:
        return now_utc.year + 1
    if month_num == 12 and now_utc.month == 1:
        return now_utc.year - 1
    return now_utc.year


def _parse_ff_time_to_utc(
    date_str: str, time_str: str, now_utc: datetime, assumed_tz: timezone | ZoneInfo = timezone.utc
) -> datetime | None:
    t_clean = time_str.strip().lower()
    if not t_clean or any(tok in t_clean for tok in ("all day", "tentative", "day")):
        return None
    d_clean = re.sub(r"\s+", " ", date_str.strip())
    d_parts = d_clean.split(" ")
    if len(d_parts) >= 3 and len(d_parts[0]) <= 4:
        d_clean = f"{d_parts[1]} {d_parts[2]}"
    try:
        month_probe = datetime.strptime(d_clean.split(" ")[0], "%b").month
        year_resolved = _infer_rollover_year(month_probe, now_utc)
        dt_naive = datetime.strptime(f"{year_resolved} {d_clean} {t_clean}", "%Y %b %d %I:%M%p")
        return dt_naive.replace(tzinfo=assumed_tz).astimezone(timezone.utc)
    except Exception:
        return None


def fetch_forexfactory_json_feed() -> list[dict]:
    resp = requests.get(
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        headers={"User-Agent": BROWSER_HEADERS["User-Agent"]},
        timeout=12,
    )
    resp.raise_for_status()
    events = []
    for item in resp.json():
        if str(item.get("country", "")).upper() != "USD":
            continue
        if str(item.get("impact", "")).lower() != "high":
            continue
        raw_date = item.get("date", "")
        if not raw_date:
            continue
        try:
            dt = datetime.fromisoformat(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=NY_TZ)
            dt_utc = dt.astimezone(timezone.utc)
        except Exception:
            continue
        events.append({
            "title": str(item.get("title", "USD High Impact")).strip(),
            "country": "USD",
            "impact": "High",
            "timestamp_utc": dt_utc.isoformat(),
            "forecast": str(item.get("forecast", "")).strip(),
            "previous": str(item.get("previous", "")).strip(),
            "provider": "ForexFactory JSON",
        })
    return events


def scrape_investing_html(now_utc: datetime) -> list[dict]:
    url = "https://www.investing.com/economic-calendar/"
    headers = {**BROWSER_HEADERS, "Referer": "https://www.investing.com/"}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code != 200 or "Just a moment" in resp.text:
        raise RuntimeError(f"Investing.com HTML blocked/unavailable (HTTP {resp.status_code})")

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("tr.js-event-item")
    if not rows:
        raise RuntimeError("Investing.com HTML parsed 0 event rows")

    events = []
    for row in rows:
        curr_cell = row.select_one("td.flagCur")
        currency = curr_cell.get_text(" ", strip=True).upper() if curr_cell else ""
        if "USD" not in currency:
            continue

        bulls = row.select("td.sentiment i.grayFullBullishIcon")
        sent_td = row.select_one("td.sentiment")
        sentiment_title = (sent_td.get("title", "") if sent_td else "").lower()
        if len(bulls) < 3 and "high" not in sentiment_title:
            continue

        dt_raw = row.get("data-event-datetime", "").strip()
        if not dt_raw:
            continue
        try:
            dt_utc = datetime.strptime(dt_raw, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        event_cell = row.select_one("td.event a") or row.select_one("td.event")
        title = event_cell.get_text(" ", strip=True) if event_cell else "USD High Impact"
        forecast_cell = row.select_one("td.fore")
        prev_cell = row.select_one("td.prev")

        events.append({
            "title": title,
            "country": "USD",
            "impact": "High",
            "timestamp_utc": dt_utc.isoformat(),
            "forecast": forecast_cell.get_text(strip=True) if forecast_cell else "",
            "previous": prev_cell.get_text(strip=True) if prev_cell else "",
            "provider": "Investing.com HTML",
        })

    return events


def scrape_forexfactory_html(now_utc: datetime) -> list[dict]:
    url = "https://www.forexfactory.com/calendar?week=this"
    headers = {**BROWSER_HEADERS, "Referer": "https://www.forexfactory.com/"}
    cookies = {"fftimezoneoffset": "0", "ffdst": "0"}
    resp = requests.get(url, headers=headers, cookies=cookies, timeout=12)
    if resp.status_code != 200 or "Just a moment" in resp.text or "cf-browser-verification" in resp.text:
        raise RuntimeError(f"ForexFactory HTML blocked/unavailable (HTTP {resp.status_code})")

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("tr.calendar__row")
    if not rows:
        raise RuntimeError("ForexFactory HTML parsed 0 calendar rows")

    events = []
    current_date_str = ""
    current_time_str = ""

    for row in rows:
        date_cell = row.select_one("td.calendar__date")
        if date_cell and date_cell.get_text(strip=True):
            current_date_str = date_cell.get_text(" ", strip=True)

        time_cell = row.select_one("td.calendar__time")
        if time_cell and time_cell.get_text(strip=True):
            current_time_str = time_cell.get_text(" ", strip=True)

        curr_cell = row.select_one("td.calendar__currency")
        currency = curr_cell.get_text(strip=True).upper() if curr_cell else ""
        if currency != "USD":
            continue

        impact_span = row.select_one("td.calendar__impact span")
        impact_classes = " ".join(impact_span.get("class", [])) if impact_span else ""
        impact_title = (impact_span.get("title", "") if impact_span else "").lower()
        if ("impact-red" not in impact_classes) and ("high" not in impact_title):
            continue

        title_cell = row.select_one("td.calendar__event span.calendar__event-title") or row.select_one("td.calendar__event")
        title = title_cell.get_text(" ", strip=True) if title_cell else "USD High Impact"

        dt_utc = _parse_ff_time_to_utc(current_date_str, current_time_str, now_utc, assumed_tz=timezone.utc)
        if dt_utc is None:
            continue

        forecast_cell = row.select_one("td.calendar__forecast")
        prev_cell = row.select_one("td.calendar__previous")
        events.append({
            "title": title,
            "country": "USD",
            "impact": "High",
            "timestamp_utc": dt_utc.isoformat(),
            "forecast": forecast_cell.get_text(strip=True) if forecast_cell else "",
            "previous": prev_cell.get_text(strip=True) if prev_cell else "",
            "provider": "ForexFactory HTML",
        })

    return events


def generate_deterministic_nfp_guard(now_utc: datetime) -> list[dict]:
    events = []
    for month_offset in (0, 1):
        year = now_utc.year + (now_utc.month + month_offset - 1) // 12
        month = (now_utc.month + month_offset - 1) % 12 + 1
        first_day = datetime(year, month, 1, 8, 30, tzinfo=NY_TZ)
        days_until_friday = (4 - first_day.weekday()) % 7
        nfp_utc = (first_day + timedelta(days=days_until_friday)).astimezone(timezone.utc)
        events.append({
            "title": "Non-Farm Employment Change (NFP Deterministic Guard)",
            "country": "USD",
            "impact": "High",
            "timestamp_utc": nfp_utc.isoformat(),
            "forecast": "",
            "previous": "",
            "provider": "Deterministic NFP Guard",
        })
    return events


def _deduplicate_events(events: list[dict]) -> list[dict]:
    seen = set()
    merged = []
    for ev in events:
        title_norm = re.sub(r"[^a-z0-9]", "", ev["title"].lower())[:18]
        ts_bucket = ev["timestamp_utc"][:16]
        key = (title_norm, ts_bucket)
        if key not in seen:
            seen.add(key)
            merged.append(ev)
    merged.sort(key=lambda x: x["timestamp_utc"])
    return merged


def fetch_realtime_usd_high_impact_calendar(
    conn: sqlite3.Connection, force_refresh: bool = False
) -> tuple[list[dict], str]:
    now_utc = datetime.now(timezone.utc)

    if not force_refresh:
        row = conn.execute(
            "SELECT fetched_at_utc, source, payload_json, is_degraded FROM news_cache WHERE id = 1"
        ).fetchone()
        if row:
            try:
                fetched_at = datetime.fromisoformat(row[0])
                is_degraded = bool(row[3])
                ttl_min = NEWS_DEGRADED_TTL_MINUTES if is_degraded else NEWS_CACHE_TTL_MINUTES
                age_min = (now_utc - fetched_at).total_seconds() / 60.0
                if age_min <= ttl_min:
                    return json.loads(row[2]), f"{row[1]} (Cached {age_min:.0f}m)"
            except Exception:
                pass

    scraped_events: list[dict] = []
    source_parts: list[str] = []

    try:
        ff_json = fetch_forexfactory_json_feed()
        if ff_json:
            scraped_events.extend(ff_json)
            source_parts.append(f"ForexFactory JSON ({len(ff_json)})")
            logger.info(f"Real-Time News fetched [ForexFactory JSON]: {len(ff_json)} USD High-Impact event(s)")
    except Exception as exc:
        logger.info(f"ForexFactory JSON feed unavailable ({exc}); checking Investing.com...")

    if not scraped_events:
        try:
            inv_html = scrape_investing_html(now_utc)
            if inv_html:
                scraped_events.extend(inv_html)
                source_parts.append(f"Investing.com HTML ({len(inv_html)})")
        except Exception as exc:
            logger.info(f"Investing.com HTML unavailable ({exc}); checking ForexFactory HTML...")

    if not scraped_events:
        try:
            ff_html = scrape_forexfactory_html(now_utc)
            if ff_html:
                scraped_events.extend(ff_html)
                source_parts.append(f"ForexFactory HTML ({len(ff_html)})")
        except Exception as exc:
            logger.warning(f"All external news feeds unavailable ({exc}); using Deterministic NFP Guard.")

    nfp_events = generate_deterministic_nfp_guard(now_utc)
    is_degraded = int(len(scraped_events) == 0)
    if is_degraded:
        source_parts.append("Deterministic NFP Guard (DEGRADED)")

    all_events = _deduplicate_events(scraped_events + nfp_events)
    source_label = " + ".join(source_parts)

    conn.execute(
        """
        INSERT INTO news_cache (id, fetched_at_utc, source, payload_json, is_degraded)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            fetched_at_utc = excluded.fetched_at_utc,
            source = excluded.source,
            payload_json = excluded.payload_json,
            is_degraded = excluded.is_degraded
        """,
        (now_utc.isoformat(), source_label, json.dumps(all_events), is_degraded),
    )
    conn.commit()
    return all_events, source_label


def evaluate_news_governor(
    events: list[dict], now_utc: datetime, source_label: str
) -> dict:
    active_blackouts = []
    upcoming = []

    for ev in events:
        try:
            ev_dt = datetime.fromisoformat(ev["timestamp_utc"]).astimezone(timezone.utc)
        except Exception:
            continue

        delta_min = (ev_dt - now_utc).total_seconds() / 60.0
        is_tier1 = any(kw in ev["title"].lower() for kw in CRITICAL_NEWS_KEYWORDS)
        before_pad = NEWS_BLACKOUT_BEFORE_MIN + (15 if is_tier1 else 0)

        if -NEWS_BLACKOUT_AFTER_MIN <= delta_min <= before_pad:
            active_blackouts.append({
                **ev,
                "dt_utc": ev_dt,
                "delta_min": delta_min,
                "resume_utc": ev_dt + timedelta(minutes=NEWS_BLACKOUT_AFTER_MIN),
                "is_tier1": is_tier1,
            })
        elif 0 < delta_min <= 72 * 60:
            upcoming.append({**ev, "dt_utc": ev_dt, "delta_min": delta_min, "is_tier1": is_tier1})

    upcoming.sort(key=lambda x: x["delta_min"])

    if active_blackouts:
        active_blackouts.sort(key=lambda x: abs(x["delta_min"]))
        primary = active_blackouts[0]
        rel = (
            f"in {int(round(primary['delta_min']))}m"
            if primary["delta_min"] >= 0
            else f"{int(round(abs(primary['delta_min'])))}m ago"
        )
        return {
            "hard_blackout": True,
            "status_code": f"HARD_BLACKOUT_ACTIVE ({primary['title']} | {rel})",
            "active_event": primary,
            "resume_utc": max(b["resume_utc"] for b in active_blackouts),
            "upcoming": upcoming[:6],
            "source": source_label,
        }

    return {
        "hard_blackout": False,
        "status_code": "NEWS_CLEAR",
        "active_event": None,
        "resume_utc": None,
        "upcoming": upcoming[:6],
        "source": source_label,
    }


# ==============================================================================
# SPOT GOLD & CROSS-ASSET MACRO INGESTION + CROSS-FEED SANITY GUARD
# ==============================================================================
def fetch_swissquote_bbo() -> dict | None:
    url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
    try:
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        candidates = []
        for entry in resp.json():
            for p in entry.get("spreadProfilePrices", []):
                bid = float(p.get("bid", 0.0))
                ask = float(p.get("ask", 0.0))
                profile = str(p.get("spreadProfile", "")).lower()
                if bid > 500.0 and ask >= bid and (ask - bid) <= 25.0:
                    candidates.append((profile, bid, ask, round(ask - bid, 2)))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (0 if "standard" in x[0] else (1 if "prime" in x[0] else 2), x[3]))
        profile, bid, ask, spread = candidates[0]
        mid = round((bid + ask) / 2.0, 2)
        logger.info(
            f"Live Spot BBO Verified [Swissquote {profile or 'default'}]: "
            f"Bid={bid:.2f} | Ask={ask:.2f} | Spread=${spread:.2f}"
        )
        return {"bid": bid, "ask": ask, "mid": mid, "spread": spread, "source": f"Swissquote BBO ({profile or 'std'})"}
    except Exception as exc:
        logger.warning(f"Swissquote BBO fetch failed: {exc}")
    return None


def _clean_ohlcv(df: pd.DataFrame, min_price: float = 100.0) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).strip() for c in df.columns]
    df = df.rename(columns={c: c.strip().capitalize() for c in df.columns})
    if not all(col in df.columns for col in ("Open", "High", "Low", "Close")):
        return pd.DataFrame()
    if "Volume" not in df.columns:
        df = df.assign(Volume=1.0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    valid_mask = (
        (df["Close"] > min_price)
        & (df["High"] >= df["Low"])
        & (df["High"] >= df["Open"])
        & (df["High"] >= df["Close"])
        & (df["Low"] <= df["Open"])
        & (df["Low"] <= df["Close"])
        & (((df["High"] - df["Low"]) / df["Close"].clip(lower=EPSILON)) <= 0.12)
    )
    return df[valid_mask]


def _check_cross_feed_sanity(df15: pd.DataFrame, bbo: dict | None) -> bool:
    if df15.empty:
        return False
    if bbo is None:
        return True
    last_close = float(df15["Close"].iloc[-1])
    diff = abs(last_close - bbo["mid"])
    if diff > max(20.0, 0.015 * bbo["mid"]):
        logger.warning(f"Cross-feed sanity reject: FeedClose={last_close:.2f} vs BBO={bbo['mid']:.2f} (diff=${diff:.2f})")
        return False
    return True


def validate_market_data_sanity(
    df15: pd.DataFrame, df1h: pd.DataFrame, bbo: dict | None
) -> tuple[bool, str]:
    """Hard structural sanity gate before analysis (anti-data-ngaco)."""
    if df15 is None or df15.empty or len(df15) < 220:
        return False, "INSUFFICIENT_15M_ROWS"
    if df1h is None or df1h.empty or len(df1h) < 80:
        return False, "INSUFFICIENT_1H_ROWS"
    if not df15.index.is_monotonic_increasing or not df1h.index.is_monotonic_increasing:
        return False, "NON_MONOTONIC_TIMESTAMPS"
    if (df15[["Open", "High", "Low", "Close"]] <= 0).any().any() or df15[["Open", "High", "Low", "Close"]].isna().any().any():
        return False, "INVALID_OR_NAN_OHLC"
    if not _check_cross_feed_sanity(df15, bbo):
        return False, "CROSS_FEED_DIVERGENCE"
    return True, "Clean"


def _fetch_twelvedata_spot(interval: str, outputsize: int) -> pd.DataFrame:
    if not TWELVEDATA_API_KEY:
        return pd.DataFrame()
    td_iv = "15min" if interval == "15m" else "1h"
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD",
            "interval": td_iv,
            "outputsize": outputsize,
            "timezone": "UTC",
            "apikey": TWELVEDATA_API_KEY,
        },
        timeout=12,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") == "error":
        raise RuntimeError(f"TwelveData API error: {payload.get('message', 'unknown')}")
    values = payload.get("values", [])
    if not values:
        return pd.DataFrame()
    df = pd.DataFrame(values)
    df = df.rename(columns={"datetime": "ts", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df.index = pd.to_datetime(df["ts"], utc=True)
    return _clean_ohlcv(df)


def _fetch_yfinance_symbol(symbol: str, interval: str, period: str, min_price: float = 0.5) -> pd.DataFrame:
    df = yf.download(symbol, interval=interval, period=period, progress=False, threads=False)
    if df is not None and not df.empty:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    return _clean_ohlcv(df, min_price=min_price)


def _fetch_yahoo_v8_http(symbol: str, interval: str, range_str: str, min_price: float = 0.5) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_str}"
    resp = requests.get(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=6)
    if resp.status_code != 200:
        return pd.DataFrame()
    result = resp.json().get("chart", {}).get("result", [])
    if not result:
        return pd.DataFrame()
    ts = result[0].get("timestamp", [])
    q = result[0].get("indicators", {}).get("quote", [{}])[0]
    df = pd.DataFrame({
        "Open": q.get("open", []),
        "High": q.get("high", []),
        "Low": q.get("low", []),
        "Close": q.get("close", []),
        "Volume": q.get("volume", [1.0] * len(ts)),
    }, index=pd.to_datetime(ts, unit="s", utc=True))
    return _clean_ohlcv(df, min_price=min_price)


def _fetch_binance_paxg_paginated(interval: str, total_bars: int) -> pd.DataFrame:
    all_rows = []
    end_ms = None
    remaining = total_bars

    while remaining > 0:
        batch = min(1000, remaining)
        params = {"symbol": "PAXGUSDT", "interval": interval, "limit": batch}
        if end_ms is not None:
            params["endTime"] = end_ms
        resp = requests.get("https://data-api.binance.vision/api/v3/klines", params=params, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows = rows + all_rows
        end_ms = int(rows[0][0]) - 1
        remaining -= len(rows)
        if len(rows) < batch:
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "Open", "High", "Low", "Close", "Volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return _clean_ohlcv(df)


def _fetch_kraken_paxg(interval_min: int) -> pd.DataFrame:
    resp = requests.get(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": "PAXGUSD", "interval": interval_min},
        timeout=10,
    )
    resp.raise_for_status()
    res = resp.json().get("result", {})
    pair_key = next((k for k in res.keys() if k != "last"), None)
    if not pair_key:
        return pd.DataFrame()
    df = pd.DataFrame(res[pair_key], columns=["ts", "Open", "High", "Low", "Close", "vwap", "Volume", "count"])
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    return _clean_ohlcv(df)


def _calibrate_spot_proxy(df: pd.DataFrame, bbo: dict | None, now_utc: datetime) -> pd.DataFrame:
    if df.empty or not bbo or is_fx_weekend_closed(now_utc):
        return df
    last_close = float(df["Close"].iloc[-1])
    if last_close <= 0:
        return df
    offset = bbo["mid"] - last_close
    if abs(offset) > 100.0:
        scale = bbo["mid"] / last_close
        for c in ("Open", "High", "Low", "Close"):
            df[c] = df[c] * scale
    else:
        for c in ("Open", "High", "Low", "Close"):
            df[c] = df[c] + offset
    return df


def ingest_spot_gold_data(now_utc: datetime) -> tuple[pd.DataFrame, pd.DataFrame, dict | None, str]:
    bbo = fetch_swissquote_bbo()

    if TWELVEDATA_API_KEY:
        try:
            df15 = _fetch_twelvedata_spot("15m", 5000)
            df1h = _fetch_twelvedata_spot("1h", 1800)
            if len(df15) >= 300 and len(df1h) >= 120 and _check_cross_feed_sanity(df15, bbo):
                src = "Nominal (Pure FX: TwelveData XAU/USD UTC + Swissquote BBO)"
                logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
                return df15, df1h, bbo, src
        except Exception as exc:
            logger.warning(f"TwelveData XAU/USD feed skipped: {exc}")

    try:
        df15 = _fetch_yfinance_symbol("XAUUSD=X", "15m", "30d", min_price=100.0)
        if len(df15) >= 300 and _check_cross_feed_sanity(df15, bbo):
            df1h = _fetch_yfinance_symbol("XAUUSD=X", "1h", "90d", min_price=100.0)
            if len(df1h) >= 120:
                src = "Nominal (Primary: yfinance XAUUSD=X)"
                logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
                return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"yfinance XAUUSD=X failed: {exc}")

    try:
        df15 = _fetch_yahoo_v8_http("XAUUSD=X", "15m", "30d", min_price=100.0)
        if len(df15) >= 300 and _check_cross_feed_sanity(df15, bbo):
            df1h = _fetch_yahoo_v8_http("XAUUSD=X", "1h", "90d", min_price=100.0)
            if len(df1h) >= 120:
                src = "Failover Active (Secondary: Yahoo v8 HTTP XAUUSD=X)"
                logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
                return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"Yahoo v8 HTTP failed: {exc}")
    logger.info("Yahoo Spot feeds unavailable/rate-limited; advancing to paginated Spot Proxy...")

    try:
        df15 = _calibrate_spot_proxy(_fetch_binance_paxg_paginated("15m", 2500), bbo, now_utc)
        df1h = _calibrate_spot_proxy(_fetch_binance_paxg_paginated("1h", 1000), bbo, now_utc)
        if len(df15) >= 300 and len(df1h) >= 120:
            src = "Failover Active (Tertiary: Paginated Spot PAXG/XAUUSD + Swissquote BBO)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"Tertiary Binance Vision feed failed: {exc}")

    try:
        df15 = _calibrate_spot_proxy(_fetch_kraken_paxg(15), bbo, now_utc)
        df1h = _calibrate_spot_proxy(_fetch_kraken_paxg(60), bbo, now_utc)
        if len(df15) >= 220 and len(df1h) >= 80:
            src = "Failover Active (Quaternary: Kraken PAXGUSD + Swissquote BBO)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"Quaternary Kraken feed failed: {exc}")

    raise RuntimeError("CRITICAL: All Spot Gold data sources exhausted.")


def ingest_macro_drivers() -> dict[str, pd.DataFrame]:
    """
    Fail-soft ingestion of 1H cross-asset Gold drivers:
    - DXY (DX-Y.NYB): Inverse USD strength driver
    - US 10Y Yield (^TNX): Opportunity-cost / real-yield driver
    """
    macro: dict[str, pd.DataFrame] = {}
    if not ENABLE_MACRO_DRIVERS:
        return macro

    for key, symbol in (("dxy", "DX-Y.NYB"), ("tnx", "^TNX")):
        try:
            df = _fetch_yahoo_v8_http(symbol, "1h", "90d", min_price=0.5)
            if df.empty:
                df = _fetch_yfinance_symbol(symbol, "1h", "90d", min_price=0.5)
            if len(df) >= 30:
                ema20 = df["Close"].ewm(span=20, adjust=False).mean()
                std20 = df["Close"].rolling(20, min_periods=20).std().clip(lower=EPSILON)
                df["MACRO_Z"] = ((df["Close"] - ema20) / std20).fillna(0.0)
                macro[key] = df
        except Exception as exc:
            logger.info(f"Macro driver [{symbol}] skipped (fail-soft): {exc}")
    if macro:
        logger.info(f"Cross-Asset Macro Drivers loaded: {', '.join(k.upper() for k in macro.keys())}")
    return macro


def select_closed_bar_index(df15: pd.DataFrame, now_utc: datetime) -> tuple[int, bool, float]:
    last_open_utc = df15.index[-1].to_pydatetime()
    if last_open_utc.tzinfo is None:
        last_open_utc = last_open_utc.replace(tzinfo=timezone.utc)
    open_age_min = (now_utc - last_open_utc).total_seconds() / 60.0

    if open_age_min < 15.0 and len(df15) >= 2:
        closed_idx = len(df15) - 2
    else:
        closed_idx = len(df15) - 1

    closed_open_utc = df15.index[closed_idx].to_pydatetime()
    if closed_open_utc.tzinfo is None:
        closed_open_utc = closed_open_utc.replace(tzinfo=timezone.utc)
    closed_bar_close_utc = closed_open_utc + timedelta(minutes=15)
    closed_age_min = max(0.0, (now_utc - closed_bar_close_utc).total_seconds() / 60.0)
    is_stale = (not is_fx_weekend_closed(now_utc)) and (closed_age_min > MAX_DATA_STALE_MINUTES)
    return closed_idx, is_stale, closed_age_min


# ==============================================================================
# ECONOPHYSICS, MICROSTRUCTURE & TECHNICAL INDICATOR ENGINE
# ==============================================================================
def _compute_hurst_rs(series: np.ndarray) -> float:
    """Simplified multi-lag R/S Hurst Exponent on price series (0.5=random, >0.54=persistent)."""
    n = len(series)
    if n < 32:
        return 0.50
    diffs = np.diff(np.log(np.maximum(series, EPSILON)))
    lags = (8, 16, 32)
    tau = []
    rs_vals = []
    for lag in lags:
        if lag > len(diffs):
            continue
        chunks = len(diffs) // lag
        if chunks < 1:
            continue
        sub_rs = []
        for c in range(chunks):
            seg = diffs[c * lag: (c + 1) * lag]
            dev = np.cumsum(seg - np.mean(seg))
            r = float(np.max(dev) - np.min(dev))
            s = float(np.std(seg, ddof=1)) if len(seg) > 1 else 0.0
            if s > EPSILON:
                sub_rs.append(r / s)
        if sub_rs:
            tau.append(math.log(lag))
            rs_vals.append(math.log(max(float(np.mean(sub_rs)), EPSILON)))
    if len(tau) < 2:
        return 0.50
    slope = float(np.polyfit(tau, rs_vals, 1)[0])
    return float(min(0.95, max(0.15, slope)))


def _compute_shannon_entropy(returns: np.ndarray, bins: int = 8) -> float:
    """Normalized Shannon entropy in [0, 1] of bar returns (lower = more structured flow)."""
    if len(returns) < 16 or float(np.std(returns)) <= EPSILON:
        return 0.50
    hist, _ = np.histogram(returns, bins=bins)
    probs = hist[hist > 0] / float(len(returns))
    ent = -float(np.sum(probs * np.log2(probs)))
    return float(min(1.0, max(0.0, ent / math.log2(bins))))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def compute_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    high, low, close, open_px = out["High"], out["Low"], out["Close"], out["Open"]
    prev_close = close.shift(1)

    # 1. Wilder ATR + Garman-Klass Volatility (7.4x more efficient than close-to-close)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["ATR"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean().clip(lower=EPSILON)
    out["ATR_MA20"] = out["ATR"].rolling(20, min_periods=1).mean().clip(lower=EPSILON)
    out["ATR_RATIO"] = (out["ATR"] / out["ATR_MA20"]).fillna(1.0)

    log_hl = np.log((high / low.clip(lower=EPSILON)).clip(lower=EPSILON))
    log_co = np.log((close / open_px.clip(lower=EPSILON)).clip(lower=EPSILON))
    gk_var = (0.5 * (log_hl ** 2) - (2.0 * math.log(2.0) - 1.0) * (log_co ** 2)).clip(lower=0.0)
    out["GK_VOL"] = np.sqrt(gk_var.rolling(20, min_periods=1).mean()).fillna(0.0)
    gk_ma50 = out["GK_VOL"].rolling(50, min_periods=1).mean().clip(lower=EPSILON)
    out["GK_RATIO"] = (out["GK_VOL"] / gk_ma50).fillna(1.0)

    # 2. Wilder ADX(14)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr14 = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean().clip(lower=EPSILON)
    plus_di = 100.0 * pd.Series(plus_dm, index=out.index).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr14
    minus_di = 100.0 * pd.Series(minus_dm, index=out.index).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr14
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + EPSILON)
    out["ADX"] = dx.ewm(alpha=1.0 / 14.0, adjust=False).mean().fillna(0.0)

    # 3. EMAs & Z-Score
    out["EMA20"] = close.ewm(span=20, adjust=False).mean()
    out["EMA50"] = close.ewm(span=50, adjust=False).mean()
    out["EMA200"] = close.ewm(span=200, adjust=False).mean()
    std20 = close.rolling(20, min_periods=20).std().clip(lower=EPSILON)
    out["ZSCORE"] = ((close - out["EMA20"]) / std20).fillna(0.0)

    # 4. Anchored Daily UTC Session VWAP (using real volume or range proxy)
    bar_range = (high - low).clip(lower=EPSILON)
    clv = ((close - low) - (high - close)) / bar_range
    body_ratio = (close - open_px).abs() / bar_range
    out["BODY_RATIO"] = body_ratio.fillna(0.0)

    vol_series = pd.to_numeric(out["Volume"], errors="coerce").fillna(0.0)
    non_zero_ratio = float((vol_series > 0).mean()) if len(vol_series) else 0.0
    has_real_vol = bool(
        non_zero_ratio >= 0.70
        and vol_series.nunique() >= 10
        and float(vol_series.std()) > EPSILON
    )

    if has_real_vol:
        vol_clean = vol_series.replace(0, np.nan).ffill().fillna(1.0).clip(lower=EPSILON)
        vol_ma = vol_clean.rolling(20, min_periods=1).mean().clip(lower=EPSILON)
        rel_effort = (vol_clean / vol_ma).clip(upper=3.0)
        eff_vol = vol_clean
        out["VOL_MODE"] = "REAL_VOL"
    else:
        range_effort = (bar_range / out["ATR"]).clip(0.5, 2.2)
        rel_effort = range_effort * (0.4 + 0.8 * body_ratio)
        eff_vol = range_effort
        out["VOL_MODE"] = "FX_RANGE_PROXY"

    typical_px = (high + low + close) / 3.0
    day_key = out.index.strftime("%Y-%m-%d")
    pv_cum = (typical_px * eff_vol).groupby(day_key).cumsum()
    v_cum = eff_vol.groupby(day_key).cumsum().clip(lower=EPSILON)
    out["SESSION_VWAP"] = (pv_cum / v_cum).fillna(out["EMA20"])
    out["VWAP_DIST_ATR"] = ((close - out["SESSION_VWAP"]) / out["ATR"]).fillna(0.0)

    # 5. Microstructure Order Flow: Blend of CLV*Effort and BVC VPIN-Proxy
    ret_1 = close.diff().fillna(0.0)
    ret_std = ret_1.rolling(20, min_periods=5).std().bfill().clip(lower=EPSILON)
    z_ret = (ret_1 / ret_std).clip(-4.0, 4.0)
    bvc_buy_prob = z_ret.apply(_normal_cdf)
    bvc_imbalance = (2.0 * bvc_buy_prob - 1.0) * rel_effort.clip(0.4, 2.0)

    clv_flow = clv * rel_effort
    blended_flow = 0.60 * clv_flow + 0.40 * bvc_imbalance
    out["ORDER_FLOW"] = blended_flow.rolling(2, min_periods=1).mean().clip(-1.0, 1.0).fillna(0.0)
    out["OF_MOMENTUM"] = (out["ORDER_FLOW"] - out["ORDER_FLOW"].shift(2)).fillna(0.0)

    # 6. Econophysics Regime Metrics: Kaufman ER(20), Rolling Hurst(64), Shannon Entropy(32)
    net_change = (close - close.shift(20)).abs()
    path_vol = close.diff().abs().rolling(20, min_periods=1).sum().clip(lower=EPSILON)
    out["KAUFMAN_ER"] = (net_change / path_vol).fillna(0.0).clip(0.0, 1.0)

    close_vals = close.to_numpy(dtype=float)
    ret_vals = np.diff(np.log(np.maximum(close_vals, EPSILON)), prepend=math.log(max(close_vals[0], EPSILON)))
    hurst_arr = np.full(len(out), 0.50, dtype=float)
    ent_arr = np.full(len(out), 0.50, dtype=float)
    for k in range(64, len(out)):
        hurst_arr[k] = _compute_hurst_rs(close_vals[k - 63: k + 1])
        ent_arr[k] = _compute_shannon_entropy(ret_vals[k - 31: k + 1])
    out["HURST"] = hurst_arr
    out["ENTROPY"] = ent_arr

    return out


def _evaluate_smc_pillar(df: pd.DataFrame, idx: int) -> tuple[float, str, str, float, float]:
    w = df.iloc[max(0, idx - 36): idx + 1]
    if len(w) < 24:
        return 35.0, "NEUTRAL_RANGE", "NONE", float(df["Low"].iloc[idx]), float(df["High"].iloc[idx])

    c = float(w["Close"].iloc[-1])
    o = float(w["Open"].iloc[-1])
    ema20 = float(w["EMA20"].iloc[-1])
    atr = max(float(w["ATR"].iloc[-1]), EPSILON)

    h_prev20_cur = float(w["High"].iloc[-21:-1].max())
    l_prev20_cur = float(w["Low"].iloc[-21:-1].min())
    h_prev20_t2 = float(w["High"].iloc[-22:-2].max())
    l_prev20_t2 = float(w["Low"].iloc[-22:-2].min())

    recent_swing_low = float(w["Low"].iloc[-10:].min())
    recent_swing_high = float(w["High"].iloc[-10:].max())

    bull_fvg = float(w["Low"].iloc[-1]) > float(w["High"].iloc[-3])
    bear_fvg = float(w["High"].iloc[-1]) < float(w["Low"].iloc[-3])

    bull_sweep_1bar = float(w["Low"].iloc[-1]) < l_prev20_cur and c > l_prev20_cur and c > o
    bull_sweep_2bar = float(w["Low"].iloc[-2]) < l_prev20_t2 and c > l_prev20_t2 and c > o
    bear_sweep_1bar = float(w["High"].iloc[-1]) > h_prev20_cur and c < h_prev20_cur and c < o
    bear_sweep_2bar = float(w["High"].iloc[-2]) > h_prev20_t2 and c < h_prev20_t2 and c < o

    if bull_sweep_1bar or bull_sweep_2bar:
        return (100.0 if bull_fvg else 95.0), "BULLISH_LIQUIDITY_SWEEP", "BUY", recent_swing_low, recent_swing_high
    if bear_sweep_1bar or bear_sweep_2bar:
        return (100.0 if bear_fvg else 95.0), "BEARISH_LIQUIDITY_SWEEP", "SELL", recent_swing_low, recent_swing_high

    prior_window_bull_bos = (w["Close"].iloc[-9:-1] > float(w["High"].iloc[-29:-9].max())).any() if len(w) >= 29 else False
    prior_window_bear_bos = (w["Close"].iloc[-9:-1] < float(w["Low"].iloc[-29:-9].min())).any() if len(w) >= 29 else False
    near_value_zone = abs(c - ema20) <= 0.9 * atr

    if prior_window_bull_bos and near_value_zone and c > recent_swing_low:
        return 96.0, "BULLISH_BOS_PULLBACK_RETEST", "BUY", recent_swing_low, recent_swing_high
    if prior_window_bear_bos and near_value_zone and c < recent_swing_high:
        return 96.0, "BEARISH_BOS_PULLBACK_RETEST", "SELL", recent_swing_low, recent_swing_high

    if c > h_prev20_cur:
        ext = (c - ema20) / atr
        pts = 88.0 if (bull_fvg and ext <= 1.3) else (78.0 if ext <= 1.5 else 52.0)
        return pts, "BULLISH_BOS_UP", "BUY", recent_swing_low, recent_swing_high
    if c < l_prev20_cur:
        ext = (ema20 - c) / atr
        pts = 88.0 if (bear_fvg and ext <= 1.3) else (78.0 if ext <= 1.5 else 52.0)
        return pts, "BEARISH_BOS_DOWN", "SELL", recent_swing_low, recent_swing_high

    # Programmatic ChoCh (Change of Character) after prior counter-swing structure
    if len(w) >= 24:
        prior_bear = float(w["High"].iloc[-14:-6].max()) < float(w["High"].iloc[-22:-14].max())
        prior_bull = float(w["Low"].iloc[-14:-6].min()) > float(w["Low"].iloc[-22:-14].min())
        choch_high_lvl = float(w["High"].iloc[-10:-1].max())
        choch_low_lvl = float(w["Low"].iloc[-10:-1].min())
        if prior_bear and c > choch_high_lvl and c > o:
            return (92.0 if bull_fvg else 86.0), "BULLISH_CHOCH_REVERSAL", "BUY", recent_swing_low, recent_swing_high
        if prior_bull and c < choch_low_lvl and c < o:
            return (92.0 if bear_fvg else 86.0), "BEARISH_CHOCH_REVERSAL", "SELL", recent_swing_low, recent_swing_high

    hh = float(w["High"].iloc[-6:].max()) > float(w["High"].iloc[-14:-6].max())
    hl = float(w["Low"].iloc[-6:].min()) > float(w["Low"].iloc[-14:-6].min())
    lh = float(w["High"].iloc[-6:].max()) < float(w["High"].iloc[-14:-6].max())
    ll = float(w["Low"].iloc[-6:].min()) < float(w["Low"].iloc[-14:-6].min())

    if hh and hl:
        return (80.0 if near_value_zone else 68.0), "BULLISH_HH_HL_STRUCTURE", "BUY", recent_swing_low, recent_swing_high
    if lh and ll:
        return (80.0 if near_value_zone else 68.0), "BEARISH_LH_LL_STRUCTURE", "SELL", recent_swing_low, recent_swing_high
    return 35.0, "CONSOLIDATION_RANGE", "NONE", recent_swing_low, recent_swing_high


def _evaluate_causal_macro_pillar(
    macro_data: dict[str, pd.DataFrame] | None, ts: pd.Timestamp, bias: str
) -> tuple[float, str]:
    """
    Strictly causal 1H cross-asset macro alignment (ts - 45m cutoff):
    Gold BUY benefits from falling DXY (MACRO_Z < 0) & falling US10Y Yields (MACRO_Z < 0).
    """
    if not macro_data or bias not in ("BUY", "SELL"):
        return 55.0, "MACRO_NEUTRAL_NO_FEED"

    cutoff = ts - pd.Timedelta(minutes=45)
    z_readings = {}
    for key in ("dxy", "tnx"):
        df_m = macro_data.get(key)
        if df_m is not None and not df_m.empty:
            pos = int(df_m.index.searchsorted(cutoff, side="right"))
            if pos > 0:
                z_readings[key] = float(df_m["MACRO_Z"].iloc[pos - 1])

    if not z_readings:
        return 55.0, "MACRO_NEUTRAL_WAIT"

    # Negative DXY/TNX Z-score is bullish for Gold
    avg_gold_tailwind = -float(np.mean(list(z_readings.values())))
    aligned_macro = avg_gold_tailwind if bias == "BUY" else -avg_gold_tailwind

    if aligned_macro >= 0.40:
        norm = 100.0
    elif aligned_macro >= 0.0:
        norm = 75.0
    elif aligned_macro >= -0.65:
        norm = 45.0
    else:
        norm = 15.0

    parts = [f"{k.upper()}={v:+.2f}Z" for k, v in z_readings.items()]
    return norm, f"MACRO({' '.join(parts)})"


def score_bar_confluence(
    df15: pd.DataFrame,
    df1h: pd.DataFrame,
    idx15: int,
    z_threshold: float,
    macro_data: dict[str, pd.DataFrame] | None = None,
) -> dict:
    row = df15.iloc[idx15]
    ts = df15.index[idx15]

    cutoff_1h = ts - pd.Timedelta(minutes=45)
    h1_pos = int(df1h.index.searchsorted(cutoff_1h, side="right")) if not df1h.empty else 0

    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    atr = max(float(row["ATR"]), EPSILON)
    atr_ratio = float(row["ATR_RATIO"])
    gk_ratio = float(row.get("GK_RATIO", 1.0))
    adx = float(row["ADX"])
    z = float(row["ZSCORE"])
    vwap_dist_atr = float(row.get("VWAP_DIST_ATR", 0.0))
    of = float(row["ORDER_FLOW"])
    of_mom = float(row["OF_MOMENTUM"])
    body_ratio = float(row["BODY_RATIO"])
    vol_mode = str(row["VOL_MODE"])
    hurst = float(row.get("HURST", 0.50))
    kaufman_er = float(row.get("KAUFMAN_ER", 0.30))
    entropy = float(row.get("ENTROPY", 0.50))

    # 1. SMC Structure (28%)
    smc_norm, smc_state, smc_dir, swing_low, swing_high = _evaluate_smc_pillar(df15, idx15)

    # 2. Causal MTF Harmony 1H/15M + Garman-Klass Vol (10%)
    if h1_pos <= 0:
        bias = smc_dir if smc_dir in ("BUY", "SELL") else "NEUTRAL"
        mtf_norm = 0.0
        mtf_state = "1H_INSUFFICIENT_HISTORY"
        htf_aligned = False
    else:
        h1_row = df1h.iloc[h1_pos - 1]
        h1_bull = float(h1_row["EMA20"]) > float(h1_row["EMA50"]) and float(h1_row["Close"]) > float(h1_row["EMA50"])
        h1_bear = float(h1_row["EMA20"]) < float(h1_row["EMA50"]) and float(h1_row["Close"]) < float(h1_row["EMA50"])
        m15_bull = ema20 > ema50
        m15_bear = ema20 < ema50

        if h1_bull and m15_bull:
            bias = "BUY"
            mtf_norm = 100.0 if (adx >= 20.0 and 0.70 <= gk_ratio <= 1.85) else 80.0
            mtf_state = "1H_15M_FULL_BULL_HARMONY"
            htf_aligned = True
        elif h1_bear and m15_bear:
            bias = "SELL"
            mtf_norm = 100.0 if (adx >= 20.0 and 0.70 <= gk_ratio <= 1.85) else 80.0
            mtf_state = "1H_15M_FULL_BEAR_HARMONY"
            htf_aligned = True
        elif h1_bull and smc_dir == "BUY":
            bias = "BUY"
            mtf_norm = 70.0
            mtf_state = "1H_BULL_PULLBACK_ALIGN"
            htf_aligned = True
        elif h1_bear and smc_dir == "SELL":
            bias = "SELL"
            mtf_norm = 70.0
            mtf_state = "1H_BEAR_PULLBACK_ALIGN"
            htf_aligned = True
        else:
            if smc_dir in ("BUY", "SELL"):
                bias = smc_dir
            elif z <= -z_threshold:
                bias = "BUY"
            elif z >= z_threshold:
                bias = "SELL"
            else:
                bias = "NEUTRAL"
            mtf_norm = 20.0
            mtf_state = "MTF_DIVERGENCE"
            htf_aligned = False

    if bias in ("BUY", "SELL") and smc_dir != "NONE" and smc_dir != bias:
        smc_norm = max(15.0, smc_norm - 55.0)

    # 3. Microstructure Order Flow: BVC VPIN-Proxy + CLV Effort (22%)
    if bias == "NEUTRAL":
        of_aligned = 0.0
        of_norm = 20.0
        of_label = f"NEUTRAL_FLOW ({of:+.2f}, Δ{of_mom:+.2f})"
    else:
        of_aligned = of if bias == "BUY" else -of
        mom_accel = (of_mom >= 0.02) if bias == "BUY" else (of_mom <= -0.02)
        if of_aligned >= 0.30:
            of_norm = 100.0 if mom_accel else 88.0
            of_label = f"{'BULL_SURGE' if bias == 'BUY' else 'BEAR_SURGE'} ({of:+.2f}, Δ{of_mom:+.2f})"
        elif of_aligned >= MIN_ORDERFLOW_CONFIRM:
            of_norm = 80.0 if mom_accel else 68.0
            of_label = f"{'BULL_ABSORPTION' if bias == 'BUY' else 'BEAR_DISTRIBUTION'} ({of:+.2f}, Δ{of_mom:+.2f})"
        elif of_aligned > 0.0:
            of_norm = 35.0
            of_label = f"BALANCED_ABSORPTION ({of:+.2f}, Δ{of_mom:+.2f})"
        else:
            of_norm = 10.0
            of_label = f"COUNTER_ORDERFLOW ({of:+.2f}, Δ{of_mom:+.2f})"

    # 4. Econophysics Regime Pillar: Hurst + Kaufman ER + Shannon Entropy + Trigger (15%)
    candle_bull = float(row["Close"]) > float(row["Open"])
    candle_bear = float(row["Close"]) < float(row["Open"])
    trigger_ok = ((bias == "BUY" and candle_bull) or (bias == "SELL" and candle_bear)) and (body_ratio >= 0.30)

    regime_quality_ok = (kaufman_er >= MIN_KAUFMAN_ER) and (entropy <= MAX_SHANNON_ENTROPY)
    if hurst >= 0.54 and kaufman_er >= 0.30 and entropy <= 0.85 and trigger_ok:
        regime_norm = 100.0
    elif regime_quality_ok and hurst >= 0.47 and trigger_ok:
        regime_norm = 80.0
    elif regime_quality_ok:
        regime_norm = 55.0
    else:
        regime_norm = 20.0

    # 5. Session VWAP Value Alignment + Z-Score Anti-Climax (15%)
    ext_atr = (close - ema20) / atr
    if bias == "BUY":
        vwap_ok = -1.2 <= vwap_dist_atr <= 1.8
        if -1.3 <= z <= 0.35 and ext_atr <= MAX_EXTENSION_ATR and vwap_ok:
            vwap_z_norm = 100.0
        elif -z_threshold <= z < -1.3:
            vwap_z_norm = 72.0
        elif ext_atr > MAX_EXTENSION_ATR or z < -z_threshold:
            vwap_z_norm = 10.0
        else:
            vwap_z_norm = 45.0
        anti_chase_ok = (ext_atr <= MAX_EXTENSION_ATR) and (abs(z) <= z_threshold)
    elif bias == "SELL":
        vwap_ok = -1.8 <= vwap_dist_atr <= 1.2
        if -0.35 <= z <= 1.3 and ext_atr >= -MAX_EXTENSION_ATR and vwap_ok:
            vwap_z_norm = 100.0
        elif 1.3 < z <= z_threshold:
            vwap_z_norm = 72.0
        elif ext_atr < -MAX_EXTENSION_ATR or z > z_threshold:
            vwap_z_norm = 10.0
        else:
            vwap_z_norm = 45.0
        anti_chase_ok = (ext_atr >= -MAX_EXTENSION_ATR) and (abs(z) <= z_threshold)
    else:
        vwap_z_norm = 35.0
        anti_chase_ok = False

    # 6. Cross-Asset Macro Drivers: Causal DXY + US10Y Yield (10%)
    macro_norm, macro_label = _evaluate_causal_macro_pillar(macro_data, ts, bias)

    # 7. Zero-Whipsaw Spike Guard & Support/Resistance Headroom Guard
    atr_ma20 = max(float(row.get("ATR_MA20", atr)), EPSILON)
    bar_range = float(row["High"] - row["Low"])
    prev_close = float(df15["Close"].iloc[idx15 - 1]) if idx15 > 0 else close
    range_atr = bar_range / atr_ma20
    jump_atr = abs(close - prev_close) / atr_ma20
    spike_ok = not (range_atr > MAX_SPIKE_ATR_MULT or (jump_atr > 4.0 and body_ratio < 0.45))
    spike_status = "Clean" if spike_ok else f"Spike Filtered ({range_atr:.1f}x ATR)"

    look_start = max(0, idx15 - 20)
    if idx15 - look_start >= 10:
        res_20 = float(df15["High"].iloc[look_start:idx15].max())
        sup_20 = float(df15["Low"].iloc[look_start:idx15].min())
    else:
        res_20, sup_20 = swing_high, swing_low

    is_breakout = "BOS_UP" in smc_state or "BOS_DOWN" in smc_state or "CHOCH" in smc_state
    if bias == "BUY" and not is_breakout and (0.0 <= (res_20 - close) / atr < MIN_SR_HEADROOM_ATR):
        sr_headroom_ok = False
        sr_note = f"Near 20-bar Resistance ({res_20:.2f})"
    elif bias == "SELL" and not is_breakout and (0.0 <= (close - sup_20) / atr < MIN_SR_HEADROOM_ATR):
        sr_headroom_ok = False
        sr_note = f"Near 20-bar Support ({sup_20:.2f})"
    else:
        sr_headroom_ok = True
        sr_note = "Clear"

    weighted_score = (
        smc_norm * PILLAR_WEIGHTS["smc"]
        + of_norm * PILLAR_WEIGHTS["order_flow"]
        + regime_norm * PILLAR_WEIGHTS["regime"]
        + vwap_z_norm * PILLAR_WEIGHTS["vwap_zscore"]
        + macro_norm * PILLAR_WEIGHTS["macro"]
        + mtf_norm * PILLAR_WEIGHTS["mtf"]
    )
    total_score = int(round(min(100.0, max(0.0, weighted_score))))

    of_ok = of_aligned >= MIN_ORDERFLOW_CONFIRM
    priority_pillars_ok = (smc_norm >= MIN_PRIORITY_PILLAR_NORM) and (of_norm >= MIN_PRIORITY_PILLAR_NORM)

    weighted_breakdown = (
        f"SMC={smc_norm:.0f}(w28) OF={of_norm:.0f}(w22) Reg={regime_norm:.0f}(w15) "
        f"VWAPZ={vwap_z_norm:.0f}(w15) Mac={macro_norm:.0f}(w10) MTF={mtf_norm:.0f}(w10)"
    )

    return {
        "bias": bias,
        "score": total_score,
        "bar_close": close,
        "bar_time_utc": ts.to_pydatetime(),
        "smc_state": smc_state,
        "mtf_state": mtf_state,
        "htf_aligned": htf_aligned,
        "of_label": of_label,
        "order_flow": of,
        "of_momentum": of_mom,
        "vol_mode": vol_mode,
        "z_score": z,
        "vwap_dist_atr": vwap_dist_atr,
        "adx": adx,
        "atr": atr,
        "atr_ratio": atr_ratio,
        "gk_ratio": gk_ratio,
        "hurst": hurst,
        "kaufman_er": kaufman_er,
        "entropy": entropy,
        "macro_norm": macro_norm,
        "macro_label": macro_label,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "trigger_ok": trigger_ok,
        "anti_chase_ok": anti_chase_ok,
        "regime_quality_ok": regime_quality_ok,
        "spike_ok": spike_ok,
        "spike_status": spike_status,
        "sr_headroom_ok": sr_headroom_ok,
        "sr_note": sr_note,
        "of_ok": of_ok,
        "smc_norm": smc_norm,
        "of_norm": of_norm,
        "regime_norm": regime_norm,
        "vwap_z_norm": vwap_z_norm,
        "mtf_norm": mtf_norm,
        "priority_pillars_ok": priority_pillars_ok,
        "weighted_breakdown": weighted_breakdown,
        "ext_atr": ext_atr,
    }


# ==============================================================================
# SHARED RISK GEOMETRY, BAYESIAN KELLY SIZING & EXECUTION PARITY ENGINE
# ==============================================================================
def compute_trade_levels(
    entry: float, bias: str, atr: float, swing_low: float, swing_high: float
) -> dict:
    if bias not in ("BUY", "SELL"):
        return {"sl_dist": 0.0, "sl": 0.0, "tp1": 0.0, "trail_trig": 0.0, "tp2": 0.0, "tp3": 0.0}

    struct_buf = abs(entry - (swing_low if bias == "BUY" else swing_high)) + 0.5 * atr
    sl_dist = min(max(struct_buf, 1.5 * atr, MIN_SL_USD), max(2.5 * atr, MIN_SL_USD))

    if bias == "BUY":
        return {
            "sl_dist": sl_dist,
            "sl": round(entry - sl_dist, 2),
            "tp1": round(entry + 1.5 * sl_dist, 2),
            "trail_trig": round(entry + 1.8 * sl_dist, 2),
            "tp2": round(entry + 2.5 * sl_dist, 2),
            "tp3": round(entry + 4.0 * sl_dist, 2),
        }
    return {
        "sl_dist": sl_dist,
        "sl": round(entry + sl_dist, 2),
        "tp1": round(entry - 1.5 * sl_dist, 2),
        "trail_trig": round(entry - 1.8 * sl_dist, 2),
        "tp2": round(entry - 2.5 * sl_dist, 2),
        "tp3": round(entry - 4.0 * sl_dist, 2),
    }


def compute_vol_kelly_risk_percent(
    base_risk_pct: float, best_bt: dict, atr_ratio: float
) -> tuple[float, float]:
    """
    Combines Bayesian-shrunk Fractional Kelly (0.25x) with Volatility Targeting:
    - Bayesian Beta(5,5) prior prevents small-sample WR over-betting.
    - Volatility target scales down risk when ATR_RATIO > 1.25.
    """
    if not ENABLE_VOL_KELLY_SIZING:
        return base_risk_pct, 1.0

    n_oos = int(best_bt.get("oos_trades", 0))
    if n_oos >= 5:
        wr_raw = float(best_bt.get("oos_wr", 50.0)) / 100.0
        n_eff = n_oos
    else:
        wr_raw = float(best_bt.get("wr", 50.0)) / 100.0
        n_eff = int(best_bt.get("trades", 0))

    wins_est = wr_raw * n_eff
    post_p = (5.0 + wins_est) / (10.0 + n_eff)
    # Conservative effective payoff ratio b from 3-tranche R expectation (~1.20R avg win vs 1.0R loss)
    b = 1.20
    full_kelly = max(0.0, post_p - (1.0 - post_p) / b)
    quarter_kelly_pct = 0.25 * full_kelly * 100.0
    vol_scalar = min(1.15, max(0.50, 1.0 / max(atr_ratio, 0.85)))

    blended_pct = 0.5 * base_risk_pct + 0.5 * min(base_risk_pct * 1.5, max(base_risk_pct * 0.5, quarter_kelly_pct))
    final_pct = round(min(base_risk_pct * 1.35, max(base_risk_pct * 0.50, blended_pct * vol_scalar)), 3)
    return final_pct, round(post_p, 3)


def _calc_partial_realized_r(
    tp1_hit: int, tp2_hit: int, rem_exit_r: float, cost_r: float
) -> float:
    r = 0.0
    rem_weight = 1.0
    if tp1_hit:
        r += TRANCHE_TP1_WEIGHT * 1.5
        rem_weight -= TRANCHE_TP1_WEIGHT
    if tp2_hit:
        r += TRANCHE_TP2_WEIGHT * 2.5
        rem_weight -= TRANCHE_TP2_WEIGHT
    r += rem_weight * rem_exit_r
    return round(r - cost_r, 4)


def step_trade_state_on_bar(state: dict, hi: float, lo: float) -> tuple[dict, str | None, float]:
    side = state["side"]
    entry = state["entry"]
    sl_dist = state["sl_dist"]
    cur_sl = state["cur_sl"]
    tp1 = state["tp1"]
    trail_trig = state["trail_trig"]
    tp2 = state["tp2"]
    tp3 = state["tp3"]
    tp1_hit = state["tp1_hit"]
    tp2_hit = state["tp2_hit"]
    locked_trail_r = state["locked_trail_r"]
    cost_r = state["cost_r"]

    if side == "BUY":
        if lo <= cur_sl:
            if tp2_hit:
                reason = "CLOSED_TP2_TRAIL"
                rem_r = max(1.5, locked_trail_r)
            elif tp1_hit:
                reason = "CLOSED_BE_TRAIL" if locked_trail_r > 0 else "CLOSED_BE"
                rem_r = locked_trail_r
            else:
                reason = "CLOSED_SL"
                rem_r = -1.0
            return state, reason, _calc_partial_realized_r(tp1_hit, tp2_hit, rem_r, cost_r)

        new_state = dict(state)
        if not tp1_hit and hi >= tp1:
            new_state["tp1_hit"] = 1
            new_state["cur_sl"] = entry
            new_state["locked_trail_r"] = max(locked_trail_r, 0.0)
        if new_state["tp1_hit"] and new_state["locked_trail_r"] < 0.5 and hi >= trail_trig:
            new_state["locked_trail_r"] = 0.5
            new_state["cur_sl"] = round(entry + 0.5 * sl_dist, 2)
        if new_state["tp1_hit"] and not tp2_hit and hi >= tp2:
            new_state["tp2_hit"] = 1
            new_state["locked_trail_r"] = max(new_state["locked_trail_r"], 1.5)
            new_state["cur_sl"] = round(entry + 1.5 * sl_dist, 2)
        if hi >= tp3:
            new_state["tp1_hit"] = 1
            new_state["tp2_hit"] = 1
            return new_state, "CLOSED_TP3", _calc_partial_realized_r(1, 1, 4.0, cost_r)
        return new_state, None, 0.0

    if hi >= cur_sl:
        if tp2_hit:
            reason = "CLOSED_TP2_TRAIL"
            rem_r = max(1.5, locked_trail_r)
        elif tp1_hit:
            reason = "CLOSED_BE_TRAIL" if locked_trail_r > 0 else "CLOSED_BE"
            rem_r = locked_trail_r
        else:
            reason = "CLOSED_SL"
            rem_r = -1.0
        return state, reason, _calc_partial_realized_r(tp1_hit, tp2_hit, rem_r, cost_r)

    new_state = dict(state)
    if not tp1_hit and lo <= tp1:
        new_state["tp1_hit"] = 1
        new_state["cur_sl"] = entry
        new_state["locked_trail_r"] = max(locked_trail_r, 0.0)
    if new_state["tp1_hit"] and new_state["locked_trail_r"] < 0.5 and lo <= trail_trig:
        new_state["locked_trail_r"] = 0.5
        new_state["cur_sl"] = round(entry - 0.5 * sl_dist, 2)
    if new_state["tp1_hit"] and not tp2_hit and lo <= tp2:
        new_state["tp2_hit"] = 1
        new_state["locked_trail_r"] = max(new_state["locked_trail_r"], 1.5)
        new_state["cur_sl"] = round(entry - 1.5 * sl_dist, 2)
    if lo <= tp3:
        new_state["tp1_hit"] = 1
        new_state["tp2_hit"] = 1
        return new_state, "CLOSED_TP3", _calc_partial_realized_r(1, 1, 4.0, cost_r)
    return new_state, None, 0.0


# ==============================================================================
# SESSION GOVERNOR
# ==============================================================================
def evaluate_session_governor(now_utc: datetime, spread_usd: float) -> tuple[bool, str, str, int]:
    if is_fx_weekend_closed(now_utc):
        return False, "WEEKEND_MARKET_CLOSED", "WEEKEND_CLOSED", 99
    hh = now_utc.hour
    if hh == 21:
        return False, "DAILY_ROLLOVER_BLACKOUT (21:00-22:00 UTC)", "ROLLOVER_BLACKOUT", 99
    if hh == 22:
        return False, "ASIAN_OPEN_ILLIQUIDITY (22:00-23:00 UTC)", "ASIAN_OPEN_HOLD", 99
    if spread_usd > MAX_ALLOWED_SPREAD_USD:
        return False, f"SPREAD_GUARD_TRIGGERED (${spread_usd:.2f} > ${MAX_ALLOWED_SPREAD_USD:.2f})", "HIGH_SPREAD", 99

    if 7 <= hh < 20:
        return True, "SESSION_CLEAR", "LONDON_NY_ACTIVE", BASE_CONFLUENCE_THRESHOLD
    return True, "SESSION_CLEAR", "ASIAN_SESSION_ACTIVE", max(BASE_CONFLUENCE_THRESHOLD + 8, 76)


# ==============================================================================
# 48-BAR PURGED WALK-FORWARD BACKTESTER (70/30 + 16-BAR EMBARGO)
# ==============================================================================
def _summarize_trades(trades_r: list[float], atr_period: int, z_threshold: float) -> dict:
    equity = [100.0]
    for tr in trades_r:
        equity.append(equity[-1] * (1.0 + (RISK_PERCENT / 100.0) * tr))

    if not trades_r:
        return {
            "atr": atr_period, "z": z_threshold, "trades": 0,
            "wr": 0.0, "pf": 1.0, "mdd": 0.0, "exp": 0.0, "sqn": 0.0, "equity": equity,
        }

    wins = [r for r in trades_r if r > 0]
    losses = [abs(r) for r in trades_r if r < 0]
    wr = 100.0 * len(wins) / len(trades_r)
    pf = min(10.0, sum(wins) / max(sum(losses), 1.0)) if wins else 0.0
    exp = float(np.mean(trades_r))
    std_r = float(np.std(trades_r, ddof=1)) if len(trades_r) > 1 else 0.0
    sqn = (math.sqrt(len(trades_r)) * exp / std_r) if std_r > EPSILON else 0.0

    eq_arr = np.array(equity)
    peaks = np.maximum.accumulate(eq_arr)
    dd = (peaks - eq_arr) / np.maximum(peaks, EPSILON)
    mdd = float(dd.max() * 100.0)

    return {
        "atr": atr_period, "z": z_threshold, "trades": len(trades_r),
        "wr": round(wr, 1), "pf": round(pf, 2), "mdd": round(mdd, 1),
        "exp": round(exp, 2), "sqn": round(sqn, 2), "equity": equity,
    }


def run_synchronized_backtest(
    df15_raw: pd.DataFrame,
    df1h_raw: pd.DataFrame,
    atr_period: int,
    z_threshold: float,
    spread_usd: float,
    macro_data: dict[str, pd.DataFrame] | None = None,
    start_idx: int = 210,
    end_idx: int | None = None,
    precomputed_df15: pd.DataFrame | None = None,
    precomputed_df1h: pd.DataFrame | None = None,
) -> dict:
    df15 = precomputed_df15 if precomputed_df15 is not None else compute_indicators(df15_raw, atr_period=atr_period)
    df1h = precomputed_df1h if precomputed_df1h is not None else compute_indicators(df1h_raw, atr_period=atr_period)
    trades_r: list[float] = []

    n_limit = (len(df15) - 1) if end_idx is None else min(end_idx, len(df15) - 1)
    i = max(210, start_idx)

    while i < n_limit - 12:
        bar_dt = df15.index[i].to_pydatetime()
        sess_ok, _, _, req_sc = evaluate_session_governor(bar_dt, spread_usd)
        if not sess_ok:
            i += 1
            continue

        sig = score_bar_confluence(df15, df1h, i, z_threshold, macro_data=macro_data)
        if (
            sig["bias"] in ("BUY", "SELL")
            and sig["score"] >= req_sc
            and sig["htf_aligned"]
            and sig["priority_pillars_ok"]
            and sig["regime_quality_ok"]
            and sig["spike_ok"]
            and sig["sr_headroom_ok"]
            and sig["adx"] >= 20.0
            and sig["anti_chase_ok"]
            and sig["of_ok"]
            and sig["trigger_ok"]
        ):
            half_spr = spread_usd / 2.0
            mid_entry = float(df15["Close"].iloc[i])
            entry = round(mid_entry + half_spr if sig["bias"] == "BUY" else mid_entry - half_spr, 2)
            levels = compute_trade_levels(entry, sig["bias"], sig["atr"], sig["swing_low"], sig["swing_high"])
            sl_dist = levels["sl_dist"]
            cost_r = (spread_usd + SIM_SLIPPAGE_USD) / max(sl_dist, EPSILON)

            state = {
                "side": sig["bias"],
                "entry": entry,
                "sl_dist": sl_dist,
                "cur_sl": levels["sl"],
                "tp1": levels["tp1"],
                "trail_trig": levels["trail_trig"],
                "tp2": levels["tp2"],
                "tp3": levels["tp3"],
                "tp1_hit": 0,
                "tp2_hit": 0,
                "locked_trail_r": 0.0,
                "cost_r": cost_r,
            }

            exit_idx = min(n_limit - 1, i + 48)
            trade_r = 0.0
            closed = False

            for j in range(i + 1, min(n_limit, i + 49)):
                hi = float(df15["High"].iloc[j])
                lo = float(df15["Low"].iloc[j])
                state, reason, realized = step_trade_state_on_bar(state, hi, lo)
                if reason is not None:
                    trade_r = realized
                    exit_idx = j
                    closed = True
                    break

            if not closed:
                final_c = float(df15["Close"].iloc[exit_idx])
                raw_r = ((final_c - entry) if sig["bias"] == "BUY" else (entry - final_c)) / max(sl_dist, EPSILON)
                trade_r = _calc_partial_realized_r(state["tp1_hit"], state["tp2_hit"], raw_r, cost_r)

            trades_r.append(trade_r)
            i = exit_idx + 1
        else:
            i += 1

    return _summarize_trades(trades_r, atr_period, z_threshold)


def grid_search_optimize(
    df15_raw: pd.DataFrame,
    df1h_raw: pd.DataFrame,
    spread_usd: float,
    macro_data: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """
    Purged Walk-Forward Optimization (Lopez de Prado standard):
    - In-Sample: bars [210, split_idx] (70% usable window)
    - Embargo Gap: PURGED_EMBARGO_BARS (16 bars = 4 hours) skipped to prevent autocorrelation leakage
    - Out-of-Sample: bars [split_idx + embargo, total_bars]
    """
    total_bars = len(df15_raw) - 1
    usable = total_bars - 210
    split_idx = 210 + int(usable * 0.70) if usable >= 160 else total_bars
    oos_start = split_idx + PURGED_EMBARGO_BARS

    best_is = None
    best_obj = -9999.0
    indicator_cache: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for atr_p in (10, 14, 20):
        df15_ind = compute_indicators(df15_raw, atr_period=atr_p)
        df1h_ind = compute_indicators(df1h_raw, atr_period=atr_p)
        indicator_cache[atr_p] = (df15_ind, df1h_ind)
        for z_th in (1.5, 2.0, 2.5):
            res_is = run_synchronized_backtest(
                df15_raw, df1h_raw, atr_p, z_th, spread_usd,
                macro_data=macro_data, start_idx=210, end_idx=split_idx,
                precomputed_df15=df15_ind, precomputed_df1h=df1h_ind,
            )
            obj = (res_is["exp"] * 10.0) + min(res_is["pf"], 4.0) + min(res_is["sqn"], 3.0) - (res_is["mdd"] * 0.25)
            if res_is["trades"] < MIN_BT_TRADES:
                obj -= (MIN_BT_TRADES - res_is["trades"]) * 1.5
            if obj > best_obj:
                best_obj = obj
                best_is = res_is

    best_df15, best_df1h = indicator_cache[best_is["atr"]]
    best_full = run_synchronized_backtest(
        df15_raw, df1h_raw, best_is["atr"], best_is["z"], spread_usd,
        macro_data=macro_data, start_idx=210, end_idx=total_bars,
        precomputed_df15=best_df15, precomputed_df1h=best_df1h,
    )
    if oos_start < total_bars - 15:
        oos = run_synchronized_backtest(
            df15_raw, df1h_raw, best_is["atr"], best_is["z"], spread_usd,
            macro_data=macro_data, start_idx=oos_start, end_idx=total_bars,
            precomputed_df15=best_df15, precomputed_df1h=best_df1h,
        )
    else:
        oos = {"trades": 0, "wr": 0.0, "pf": 1.0, "mdd": 0.0, "exp": 0.0, "sqn": 0.0}

    best_full["is_trades"] = best_is["trades"]
    best_full["is_exp"] = best_is["exp"]
    best_full["oos_trades"] = oos["trades"]
    best_full["oos_wr"] = oos["wr"]
    best_full["oos_pf"] = oos["pf"]
    best_full["oos_exp"] = oos["exp"]
    best_full["oos_sqn"] = oos.get("sqn", 0.0)
    best_full["_df15"] = best_df15
    best_full["_df1h"] = best_df1h

    logger.info(
        f"Purged Walk-Forward (ATR={best_full['atr']}, Z={best_full['z']}): "
        f"Full N={best_full['trades']} WR={best_full['wr']}% PF={best_full['pf']:.2f} SQN={best_full['sqn']:.2f} Exp={best_full['exp']:+.2f}R | "
        f"OOS N={oos['trades']} WR={oos['wr']}% PF={oos['pf']:.2f} Exp={oos['exp']:+.2f}R"
    )
    return best_full


def save_backtest_chart(best_bt: dict, chart_path: str = CHART_PATH) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")
    eq = best_bt.get("equity", [100.0])
    ax.plot(range(len(eq)), eq, color="#f0b90b", linewidth=2.0, label="Net Equity Curve (%)")
    ax.axhline(100.0, color="#8b949e", linestyle="--", linewidth=0.9)
    ax.set_title(
        f"APEX-QUANT-OMNI {BOT_VERSION} | ATR={best_bt['atr']} Z={best_bt['z']} | "
        f"N={best_bt['trades']} WR={best_bt['wr']}% PF={best_bt['pf']} SQN={best_bt.get('sqn', 0.0)} "
        f"Exp={best_bt['exp']:+.2f}R (OOS {best_bt.get('oos_exp', 0.0):+.2f}R)",
        color="#e6edf3", fontsize=9, pad=10,
    )
    ax.set_xlabel("Closed Trades", color="#8b949e", fontsize=9)
    ax.set_ylabel("Equity Index", color="#8b949e", fontsize=9)
    ax.tick_params(colors="#8b949e")
    ax.grid(color="#30363d", linestyle=":", linewidth=0.7)
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#e6edf3")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close(fig)
    logger.info(f"Saved backtest performance chart to [{chart_path}].")


# ==============================================================================
# ACTIVE TRADE LIFECYCLE TRACKER & RISK CIRCUIT BREAKER
# ==============================================================================
def check_daily_loss_circuit_breaker(conn: sqlite3.Connection, now_utc: datetime) -> tuple[bool, float]:
    day_prefix = now_utc.strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT realized_r FROM active_trades WHERE status LIKE 'CLOSED_%' AND closed_at_utc LIKE ?",
        (f"{day_prefix}%",),
    ).fetchall()
    daily_r = sum(float(r[0] or 0.0) for r in rows)
    return (daily_r <= -MAX_DAILY_LOSS_R), round(daily_r, 2)


def monitor_active_trades(
    conn: sqlite3.Connection, df15_raw: pd.DataFrame, bid: float, ask: float, spread_usd: float, now_utc: datetime
) -> dict | None:
    rows = conn.execute(
        "SELECT id, opened_at_utc, opened_bar_utc, side, entry_price, initial_sl, current_sl, "
        "tp1, tp2, tp3, lot_size, status, tp1_hit, tp2_hit, locked_trail_r, cost_r "
        "FROM active_trades WHERE status IN ('OPEN', 'TP1_BE', 'TP2_TRAIL') ORDER BY id ASC"
    ).fetchall()
    if not rows:
        return None

    latest_active = None
    for row in rows:
        (
            tid, opened_str, opened_bar_str, side, entry, init_sl, cur_sl,
            tp1, tp2, tp3, lot_sz, status, tp1_hit, tp2_hit, locked_trail_r, saved_cost_r
        ) = row

        sl_dist = max(abs(entry - init_sl), EPSILON)
        cost_r = float(saved_cost_r) if saved_cost_r else ((spread_usd + SIM_SLIPPAGE_USD) / sl_dist)
        trail_trig = round(entry + 1.8 * sl_dist if side == "BUY" else entry - 1.8 * sl_dist, 2)

        if opened_bar_str:
            ref_dt = datetime.fromisoformat(opened_bar_str).astimezone(timezone.utc)
            bars_since = df15_raw[df15_raw.index > ref_dt]
        else:
            opened_dt = datetime.fromisoformat(opened_str).astimezone(timezone.utc)
            opened_floor = opened_dt.replace(minute=(opened_dt.minute // 15) * 15, second=0, microsecond=0)
            bars_since = df15_raw[df15_raw.index >= opened_floor]

        price_steps = [(float(r["High"]), float(r["Low"])) for _, r in bars_since.iterrows()]
        live_px = bid if side == "BUY" else ask
        price_steps.append((live_px, live_px))

        state = {
            "side": side,
            "entry": entry,
            "sl_dist": sl_dist,
            "cur_sl": cur_sl,
            "tp1": tp1,
            "trail_trig": trail_trig,
            "tp2": tp2,
            "tp3": tp3,
            "tp1_hit": int(tp1_hit),
            "tp2_hit": int(tp2_hit),
            "locked_trail_r": float(locked_trail_r),
            "cost_r": cost_r,
        }

        closed_reason = None
        realized_r = 0.0
        exit_px = live_px
        prev_tp1 = state["tp1_hit"]
        prev_tp2 = state["tp2_hit"]

        for hi, lo in price_steps:
            state, reason, r_val = step_trade_state_on_bar(state, hi, lo)
            if reason is not None:
                closed_reason = reason
                realized_r = r_val
                exit_px = tp3 if reason == "CLOSED_TP3" else state["cur_sl"]
                break

        if closed_reason:
            conn.execute(
                "UPDATE active_trades SET status = ?, current_sl = ?, tp1_hit = ?, tp2_hit = ?, "
                "locked_trail_r = ?, closed_at_utc = ?, exit_price = ?, realized_r = ? WHERE id = ?",
                (
                    closed_reason, state["cur_sl"], state["tp1_hit"], state["tp2_hit"],
                    state["locked_trail_r"], now_utc.isoformat(), exit_px, realized_r, tid,
                ),
            )
            conn.commit()
            tag = "✅" if realized_r > 0 else "❌"
            msg = (
                f"{tag} <b>TRADE UPDATE #{tid} — {_esc(side)} XAUUSD</b>\n"
                f"<b>Status:</b> <code>{_esc(closed_reason)}</code> (<code>{realized_r:+.2f}R</code> net)\n"
                f"<b>Entry:</b> <code>{entry:.2f}</code> → <b>Exit:</b> <code>{exit_px:.2f}</code>"
            )
            send_telegram_message(msg)
            logger.info(f"Active Trade #{tid} closed: {closed_reason} @ {exit_px:.2f} ({realized_r:+.2f}R net)")
            continue

        new_status = "TP2_TRAIL" if state["tp2_hit"] else ("TP1_BE" if state["tp1_hit"] else "OPEN")
        if state["tp1_hit"] != prev_tp1 or state["tp2_hit"] != prev_tp2 or state["cur_sl"] != cur_sl:
            conn.execute(
                "UPDATE active_trades SET status = ?, current_sl = ?, tp1_hit = ?, tp2_hit = ?, locked_trail_r = ? WHERE id = ?",
                (new_status, state["cur_sl"], state["tp1_hit"], state["tp2_hit"], state["locked_trail_r"], tid),
            )
            conn.commit()
            if state["tp2_hit"] and not prev_tp2:
                msg = (
                    f"🚀 <b>TP2 HIT #{tid} — {_esc(side)} XAUUSD</b>\n"
                    f"<b>TP2:</b> <code>{tp2:.2f}</code> tersentuh! 30% closed; SL digeser ke <code>{state['cur_sl']:.2f}</code> (+1.5R)."
                )
                send_telegram_message(msg)
            elif state["tp1_hit"] and not prev_tp1:
                msg = (
                    f"🎯 <b>TP1 HIT #{tid} — {_esc(side)} XAUUSD</b>\n"
                    f"<b>TP1:</b> <code>{tp1:.2f}</code> tersentuh! 50% closed; SL digeser ke <code>{state['cur_sl']:.2f}</code>."
                )
                send_telegram_message(msg)
            logger.info(f"Active Trade #{tid} updated: status={new_status} | SL={state['cur_sl']:.2f}")

        latest_active = {
            "id": tid, "side": side, "entry": entry, "sl": state["cur_sl"],
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "status": new_status,
        }

    return latest_active


def register_active_trade(
    conn: sqlite3.Connection,
    now_utc: datetime,
    opened_bar_utc: datetime,
    side: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    lot_sz: float,
    score: int,
    cost_r: float,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO active_trades (
            opened_at_utc, opened_bar_utc, side, entry_price, initial_sl, current_sl,
            tp1, tp2, tp3, lot_size, score, status, cost_r
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """,
        (now_utc.isoformat(), opened_bar_utc.isoformat(), side, entry, sl, sl, tp1, tp2, tp3, lot_sz, score, cost_r),
    )
    conn.commit()
    return int(cur.lastrowid)


# ==============================================================================
# WEBHOOK DELIVERY (429 RATE-LIMIT + RETRY-SAFE BYTES + DISCORD UNESCAPE)
# ==============================================================================
def _post_with_backoff(
    url: str, json_payload: dict | None = None, file_bytes: tuple[str, bytes] | None = None, data: dict | None = None
) -> requests.Response | None:
    delays = (2, 4, 8)
    last_resp = None
    for idx, delay in enumerate(delays):
        try:
            files = {"photo": (file_bytes[0], file_bytes[1], "image/png")} if file_bytes else None
            resp = requests.post(url, json=json_payload, files=files, data=data, timeout=15)
            last_resp = resp
            if 200 <= resp.status_code < 300 or resp.status_code == 400:
                return resp
            wait_sec = delay
            if resp.status_code == 429:
                try:
                    wait_sec = min(30, int(resp.json().get("parameters", {}).get("retry_after", delay)))
                except Exception:
                    wait_sec = delay
            logger.warning(f"Webhook HTTP {resp.status_code} ({resp.text[:120]}); retrying in {wait_sec}s...")
        except Exception as exc:
            wait_sec = delay
            logger.warning(f"Webhook request error: {exc}; retrying in {wait_sec}s...")
        if idx < len(delays) - 1:
            time.sleep(wait_sec)
    return last_resp


def send_telegram_message(text_html: str, chat_id: str | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    msg_safe = text_html[:4050]
    resp = _post_with_backoff(url, json_payload={
        "chat_id": target, "text": msg_safe, "parse_mode": "HTML", "disable_web_page_preview": True,
    })
    if resp is not None and 200 <= resp.status_code < 300:
        return True
    if resp is not None and resp.status_code == 400:
        logger.warning(f"Telegram HTML rejected ({resp.text[:140]}); falling back to plain text...")
        plain = html.unescape(re.sub(r"<[^>]+>", "", msg_safe))[:4050]
        resp2 = _post_with_backoff(url, json_payload={"chat_id": target, "text": plain, "disable_web_page_preview": True})
        return resp2 is not None and 200 <= resp2.status_code < 300
    status = resp.status_code if resp is not None else "None"
    logger.warning(f"Telegram sendMessage failed: status={status}")
    return False


def send_telegram_photo(photo_path: str, caption: str, chat_id: str | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not os.path.exists(photo_path):
        return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as fh:
        payload_bytes = fh.read()
    resp = _post_with_backoff(
        url,
        file_bytes=(os.path.basename(photo_path), payload_bytes),
        data={"chat_id": target, "caption": caption[:1000]},
    )
    return resp is not None and 200 <= resp.status_code < 300


def send_discord_alert(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")
        return
    clean = html.unescape(re.sub(r"<[^>]+>", "", message))
    _post_with_backoff(DISCORD_WEBHOOK_URL, json_payload={"content": clean[:1950]})


# ==============================================================================
# INSTITUTIONAL TELEGRAM / DISCORD WEBHOOK FORMATTER (v90.0 TEMPLATE)
# ==============================================================================
def build_telegram_signal_card(
    decision: str,
    bias: str,
    sig: dict,
    req_score: int,
    bid: float,
    ask: float,
    spread_usd: float,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    lot_out: float,
    risk_out: float,
    eff_risk_pct: float,
    sniper_note: str,
    session_name: str,
    news_gov: dict,
    best_bt: dict,
    active_trade: dict | None,
    data_src: str = "Spot Feed Nominal",
    cooldown_status: str = "Lolos",
    webhook_label: str = "Terkirim via Exponential Backoff",
    exec_utc: datetime | None = None,
) -> str:
    ts_iso = (exec_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    is_verified = decision.startswith("VERIFIED")

    mtf_pillar_status = (
        f"Selaras ({_esc(sig['smc_state'])} | {_esc(sig['macro_label'])})"
        if (sig["htf_aligned"] and sig["priority_pillars_ok"] and sig["sr_headroom_ok"])
        else f"Terblokir ({_esc(sniper_note)})"
    )
    sanity_guard_status = f"{_esc(sig.get('spike_status', 'Clean'))} (Spr ${spread_usd:.2f})"
    sesi_status = (
        f"Terverifikasi Aman ({_esc(session_name)})"
        if "ACTIVE" in session_name
        else f"Terfilter ({_esc(session_name)})"
    )
    trail_proto = (
        "Aktif @ TP1 Breakeven (+0.5R Trail @ 1.8R)"
        if is_verified
        else f"Standby ({_esc(news_gov['status_code'])})"
    )

    if is_verified:
        entry_str = f"{entry:.2f}"
        sl_str = f"{sl:.2f}"
        tp1_str = f"{tp1:.2f}"
        tp2_str = f"{tp2:.2f}"
        tp3_str = f"{tp3:.2f}"
    else:
        entry_str = f"{bid:.2f} / {ask:.2f} (Standby)"
        sl_str = f"{sl:.2f}" if sl > 0 else "N/A (HOLD)"
        tp1_str = f"{tp1:.2f}" if tp1 > 0 else "N/A (HOLD)"
        tp2_str = f"{tp2:.2f}" if tp2 > 0 else "N/A (HOLD)"
        tp3_str = f"{tp3:.2f}" if tp3 > 0 else "N/A (HOLD)"

    pos_extra = ""
    if active_trade:
        pos_extra = (
            f"\n• <i>Posisi Aktif #{active_trade['id']}:</i> "
            f"<code>{_esc(active_trade['side'])} @ {active_trade['entry']:.2f}</code> "
            f"(SL <code>{active_trade['sl']:.2f}</code> | <code>{_esc(active_trade['status'])}</code>)"
        )

    return (
        f"🛡️ <b>{_esc(BOT_EDITION)}</b> 🛡️\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 <b>Aset:</b> XAUUSD (SPOT GOLD)\n"
        f"📊 <b>Keputusan Sinyal:</b> <code>{_esc(decision)}</code>\n"
        f"🎯 <b>Harga Entry Presisi:</b> <code>{_esc(entry_str)}</code>\n"
        f"🛑 <b>Stop Loss Forensik:</b> <code>{_esc(sl_str)}</code>\n"
        f"📈 <b>Take Profit 1 (1.5R):</b> <code>{_esc(tp1_str)}</code>\n"
        f"📈 <b>Take Profit 2 (2.5R):</b> <code>{_esc(tp2_str)}</code>\n"
        f"🚀 <b>Take Profit 3 (4.0R):</b> <code>{_esc(tp3_str)}</code>\n"
        f"🔄 <b>Protokol Trailing Stop:</b> {_esc(trail_proto)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>Anti-Whipsaw &amp; Data Sanity Guard:</b>\n"
        f"• <i>Validasi Multi-TF &amp; Pilar:</i> {mtf_pillar_status}\n"
        f"• <i>Status Data &amp; Spike Guard:</i> {sanity_guard_status}\n"
        f"• <i>Status Cooldown SQLite:</i> {_esc(cooldown_status)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Multi-Source, Sesi &amp; Bot 2-Arah:</b>\n"
        f"• <i>Feed Aktif:</i> {_esc(data_src)}\n"
        f"• <i>Sesi Pasar:</i> {sesi_status}\n"
        f"• <i>Perintah Interaktif:</i> Siap via Telegram (/check /signal)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>Grid Search &amp; Exponential Backoff:</b>\n"
        f"• <i>Periode ATR Optimal:</i> <code>{best_bt['atr']}</code>\n"
        f"• <i>Ambang Z-Score Optimal:</i> <code>{best_bt['z']:.1f}</code>\n"
        f"• <i>Status Webhook:</i> {_esc(webhook_label)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Risiko &amp; Position Sizing:</b>\n"
        f"• <i>Rekomendasi Lot:</i> <code>{lot_out:.2f} Lots</code> (Risiko {eff_risk_pct:.2f}%)\n"
        f"• <i>Nilai Risiko (USD):</i> <code>${risk_out:.2f}</code>{pos_extra}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔬 <b>Audit Forensik, Artifact DB &amp; Backtest:</b>\n"
        f"• <i>Skor Konfluensi:</i> <code>{sig['score']}/100</code> (Req&gt;={req_score})\n"
        f"• <i>Artifact SQLite:</i> Tersimpan &amp; Terunggah\n"
        f"• <i>Win Rate Teroptimasi:</i> <code>{best_bt['wr']:.1f}%</code> (OOS {best_bt.get('oos_wr', 0.0):.1f}%)\n"
        f"• <i>Profit Factor:</i> <code>{best_bt['pf']:.2f}</code> (SQN {best_bt.get('sqn', 0.0):.2f})\n"
        f"• <i>Max Drawdown:</i> <code>{best_bt['mdd']:.1f}%</code>\n"
        f"• <i>Waktu Eksekusi (UTC):</i> <code>{_esc(ts_iso)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )


# ==============================================================================
# CORE ANALYSIS CYCLE
# ==============================================================================
def run_analysis_cycle(
    conn: sqlite3.Connection,
    notify_mode: str = "auto",
    reply_chat_id: str | None = None,
    force_news_refresh: bool = False,
    suppress_hold_notify: bool = False,
) -> dict:
    now_utc = datetime.now(timezone.utc)

    events, news_src = fetch_realtime_usd_high_impact_calendar(conn, force_refresh=force_news_refresh)
    news_gov = evaluate_news_governor(events, now_utc, news_src)

    df15_raw, df1h_raw, bbo, data_src = ingest_spot_gold_data(now_utc)
    sanity_ok, sanity_reason = validate_market_data_sanity(df15_raw, df1h_raw, bbo)
    if not sanity_ok:
        raise RuntimeError(f"MARKET_DATA_SANITY_CIRCUIT_BREAKER: {sanity_reason}")

    macro_data = ingest_macro_drivers()
    bid = bbo["bid"] if bbo else float(df15_raw["Close"].iloc[-1])
    ask = bbo["ask"] if bbo else bid
    spread_usd = bbo["spread"] if bbo else 0.50

    active_trade = monitor_active_trades(conn, df15_raw, bid, ask, spread_usd, now_utc)
    cb_tripped, daily_r = check_daily_loss_circuit_breaker(conn, now_utc)

    best_bt = grid_search_optimize(df15_raw, df1h_raw, spread_usd, macro_data=macro_data)
    save_backtest_chart(best_bt, CHART_PATH)
    oos_neg_edge = (
        (best_bt.get("oos_trades", 0) >= 5 and best_bt.get("oos_exp", 0.0) <= 0.0)
        or (best_bt["trades"] >= 8 and best_bt["exp"] <= 0.0)
    )

    df15 = best_bt.pop("_df15", None)
    df1h = best_bt.pop("_df1h", None)
    if df15 is None:
        df15 = compute_indicators(df15_raw, atr_period=best_bt["atr"])
    if df1h is None:
        df1h = compute_indicators(df1h_raw, atr_period=best_bt["atr"])
    closed_idx, is_stale, closed_age_min = select_closed_bar_index(df15, now_utc)
    sig = score_bar_confluence(df15, df1h, closed_idx, best_bt["z"], macro_data=macro_data)

    bias = sig["bias"]
    atr = sig["atr"]
    if bias in ("BUY", "SELL"):
        entry = ask if bias == "BUY" else bid
        drift_atr = abs(entry - sig["bar_close"]) / max(atr, EPSILON)
        levels = compute_trade_levels(entry, bias, atr, sig["swing_low"], sig["swing_high"])
        sl_dist = levels["sl_dist"]
        sl, tp1, tp2, tp3 = levels["sl"], levels["tp1"], levels["tp2"], levels["tp3"]
        cost_r = (spread_usd + SIM_SLIPPAGE_USD) / max(sl_dist, EPSILON)
    else:
        entry = round((bid + ask) / 2.0, 2)
        drift_atr = 0.0
        sl_dist = max(1.5 * atr, MIN_SL_USD)
        sl, tp1, tp2, tp3 = 0.0, 0.0, 0.0, 0.0
        cost_r = 0.0

    eff_risk_pct, post_wr = compute_vol_kelly_risk_percent(RISK_PERCENT, best_bt, sig["atr_ratio"])
    target_risk_usd = round(ACCOUNT_BALANCE * (eff_risk_pct / 100.0), 2)
    raw_lot = target_risk_usd / max(sl_dist * CONTRACT_OZ, EPSILON)
    lot_size = round(max(0.01, min(5.0, raw_lot)), 2)
    actual_risk_usd = round(lot_size * sl_dist * CONTRACT_OZ, 2)
    min_lot_overrisk = (raw_lot < 0.01) and (actual_risk_usd > target_risk_usd * 1.25)

    session_ok, session_reason, session_name, req_score = evaluate_session_governor(now_utc, spread_usd)
    cooldown_ok, cooldown_status = check_signal_cooldown(
        conn, sig["bar_time_utc"], bias, sig["smc_state"], sig["smc_norm"], now_utc
    )

    if news_gov["hard_blackout"]:
        decision = "NO_SIGNAL / HARD BLACKOUT"
        sniper_note = f"Signal suspended ({news_gov['status_code']}) to avoid slippage"
        lot_out, risk_out = 0.0, 0.0
    elif not session_ok:
        decision = "NO_SIGNAL / SESSION HOLD"
        sniper_note = session_reason
        lot_out, risk_out = 0.0, 0.0
    elif is_stale:
        decision = "NO_SIGNAL / STALE DATA"
        sniper_note = f"Feed candle terlambat ({closed_age_min:.0f}m > {MAX_DATA_STALE_MINUTES}m)"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["spike_ok"]:
        decision = "NO_SIGNAL / SPIKE FILTERED"
        sniper_note = f"Anomali outlier terdeteksi ({sig['spike_status']})"
        lot_out, risk_out = 0.0, 0.0
    elif cb_tripped:
        decision = "NO_SIGNAL / CIRCUIT BREAKER"
        sniper_note = f"Daily realized loss {daily_r:+.2f}R <= -{MAX_DAILY_LOSS_R:.1f}R limit"
        lot_out, risk_out = 0.0, 0.0
    elif active_trade is not None:
        decision = "NO_SIGNAL / POSITION ACTIVE"
        sniper_note = f"Trade #{active_trade['id']} ({active_trade['side']}) masih {active_trade['status']} (single-position lock)"
        lot_out, risk_out = 0.0, 0.0
    elif not cooldown_ok:
        decision = "NO_SIGNAL / COOLDOWN LOCK"
        sniper_note = f"Anti-Whipsaw Cooldown aktif ({cooldown_status})"
        lot_out, risk_out = 0.0, 0.0
    elif REQUIRE_POSITIVE_BACKTEST and oos_neg_edge:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"OOS/BT edge negatif (BT Exp {best_bt['exp']:+.2f}R, OOS {best_bt.get('oos_exp', 0.0):+.2f}R)"
        lot_out, risk_out = 0.0, 0.0
    elif bias == "NEUTRAL":
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Tidak ada bias terarah ({sig['mtf_state']})"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["htf_aligned"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Kontra tren 1H ({sig['mtf_state']})"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["sr_headroom_ok"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Anti-Whipsaw S/R Guard ({sig['sr_note']})"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["regime_quality_ok"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Rezim noise/choppy (ER={sig['kaufman_er']:.2f}, Ent={sig['entropy']:.2f}, H={sig['hurst']:.2f})"
        lot_out, risk_out = 0.0, 0.0
    elif drift_atr > MAX_ENTRY_DRIFT_ATR:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Entry drift terlalu jauh ({drift_atr:.2f} ATR dari close M15)"
        lot_out, risk_out = 0.0, 0.0
    elif sig["smc_norm"] < MIN_PRIORITY_PILLAR_NORM:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"SMC Structure lemah ({sig['smc_norm']:.0f}/{MIN_PRIORITY_PILLAR_NORM:.0f})"
        lot_out, risk_out = 0.0, 0.0
    elif sig["of_norm"] < MIN_PRIORITY_PILLAR_NORM or not sig["of_ok"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"OrderFlow belum terkonfirmasi ({sig['order_flow']:+.2f})"
        lot_out, risk_out = 0.0, 0.0
    elif sig["score"] < req_score:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Skor {sig['score']} belum mencapai Req {req_score}"
        lot_out, risk_out = 0.0, 0.0
    elif sig["adx"] < 20.0:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"ADX lemah ({sig['adx']:.1f} below 20.0)"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["anti_chase_ok"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = f"Harga klimaks/overextended (Z={sig['z_score']:+.2f}, Ext={sig['ext_atr']:+.2f} ATR)"
        lot_out, risk_out = 0.0, 0.0
    elif not sig["trigger_ok"]:
        decision = "NO_SIGNAL / HOLD"
        sniper_note = "Menunggu candle pemicu berkonviksi"
        lot_out, risk_out = 0.0, 0.0
    else:
        decision = f"VERIFIED {bias}"
        sniper_note = "ALL_ZERO_WHIPSAW_GATES_PASSED"
        if min_lot_overrisk:
            sniper_note += f" (WARN: min lot 0.01 risks ${actual_risk_usd:.2f} > target ${target_risk_usd:.2f})"
        lot_out, risk_out = lot_size, actual_risk_usd
        record_signal_cooldown(conn, sig["bar_time_utc"], bias, now_utc)
        tid = register_active_trade(
            conn, now_utc, sig["bar_time_utc"], bias, entry, sl, tp1, tp2, tp3, lot_out, sig["score"], cost_r
        )
        active_trade = {
            "id": tid, "side": bias, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "status": "OPEN",
        }

    is_verified_op = decision.startswith("VERIFIED")
    should_send = (
        notify_mode == "command"
        or is_verified_op
        or news_gov["hard_blackout"]
        or (FORCE_NOTIFY and not suppress_hold_notify)
    )
    planned_wh_label = (
        "On-Demand Reply (Exponential Backoff)"
        if notify_mode == "command"
        else ("Terkirim via Exponential Backoff" if should_send else "Standby (Hold Threshold)")
    )

    msg_html = build_telegram_signal_card(
        decision=decision, bias=bias, sig=sig, req_score=req_score,
        bid=bid, ask=ask, spread_usd=spread_usd,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
        lot_out=lot_out, risk_out=risk_out, eff_risk_pct=eff_risk_pct,
        sniper_note=sniper_note, session_name=session_name,
        news_gov=news_gov, best_bt=best_bt, active_trade=active_trade,
        data_src=data_src, cooldown_status=cooldown_status,
        webhook_label=planned_wh_label, exec_utc=now_utc,
    )

    webhook_status = "Standby (Hold Threshold)"
    if should_send:
        ok_tg = send_telegram_message(msg_html, chat_id=reply_chat_id)
        if notify_mode == "command":
            webhook_status = "On-Demand Reply (Exponential Backoff)" if ok_tg else "Reply Failed"
        else:
            send_discord_alert(msg_html)
            webhook_status = "Terkirim via Exponential Backoff" if ok_tg else "Telegram Delivery Failed"
    elif suppress_hold_notify and FORCE_NOTIFY:
        webhook_status = "Skipped Duplicate (Already Replied via /check)"

    if oos_neg_edge:
        edge_tag = "NEGATIVE_EXPECTANCY_WARNING"
    elif best_bt.get("oos_trades", 0) >= 3 and best_bt.get("oos_exp", 0.0) <= 0.0:
        edge_tag = "OOS_SAMPLE_THIN_NEGATIVE"
    else:
        edge_tag = "POSITIVE_EDGE"

    forensic_audit_log(conn, {
        "timestamp_utc": now_utc.isoformat(),
        "decision": decision,
        "score": sig["score"],
        "entry_price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "lot_size": lot_out,
        "spread_usd": spread_usd,
        "adx": round(sig["adx"], 2),
        "z_score": round(sig["z_score"], 4),
        "order_flow": round(sig["order_flow"], 4),
        "hurst": round(sig["hurst"], 4),
        "kaufman_er": round(sig["kaufman_er"], 4),
        "entropy": round(sig["entropy"], 4),
        "macro_score": round(sig["macro_norm"], 1),
        "smc_state": sig["smc_state"],
        "mtf_state": sig["mtf_state"],
        "news_status": news_gov["status_code"],
        "source": data_src,
    })

    logger.info("========== FORENSIC AUDIT LOG ==========")
    logger.info(f"Asset=XAUUSD (SPOT GOLD) | Decision={decision} | Score={sig['score']}/100 (Req>={req_score}) | ClosedBar={sig['bar_time_utc'].strftime('%Y-%m-%d %H:%M UTC')} (age={closed_age_min:.1f}m)")
    logger.info(f"WeightedPillars: {sig['weighted_breakdown']} | VolMode={sig['vol_mode']} | Drift={drift_atr:.2f} ATR | Source={data_src}")
    logger.info(f"Guards: Sanity={sanity_reason} | Spike={sig['spike_status']} | SR_Headroom={sig['sr_note']} | Cooldown={cooldown_status}")
    logger.info(f"Entry={entry:.2f} | SL={sl:.2f} | TP1={tp1:.2f} | TP2={tp2:.2f} | TP3={tp3:.2f} | Bid={bid:.2f} Ask={ask:.2f} Spread=${spread_usd:.2f}")
    logger.info(f"Z={sig['z_score']:.3f} VWAP_Dist={sig['vwap_dist_atr']:+.2f}ATR | ADX={sig['adx']:.1f} GK_Ratio={sig['gk_ratio']:.2f} | H={sig['hurst']:.2f} ER={sig['kaufman_er']:.2f} Ent={sig['entropy']:.2f}")
    logger.info(f"SMC={sig['smc_state']} | OF={sig['of_label']} | MTF={sig['mtf_state']} | {sig['macro_label']}")
    logger.info(f"SniperGate={sniper_note} | News={news_gov['status_code']} | Session={session_name} | DailyR={daily_r:+.2f}R")
    logger.info(f"PurgedWF(ATR={best_bt['atr']}, Z={best_bt['z']}, N={best_bt['trades']}) | WR={best_bt['wr']}% | PF={best_bt['pf']:.2f} | SQN={best_bt.get('sqn', 0.0):.2f} | Exp={best_bt['exp']:+.2f}R | OOS_Exp={best_bt.get('oos_exp', 0.0):+.2f}R | Edge={edge_tag}")
    logger.info(f"Lot={lot_out:.2f} | ActualRiskUSD=${risk_out:.2f} ({eff_risk_pct:.2f}% Kelly-Vol, PostWR={post_wr:.2%}) | ActiveTrade={active_trade['id'] if active_trade else 'None'} | Webhook={webhook_status}")
    logger.info("========================================")

    return {"decision": decision, "score": sig["score"], "news_gov": news_gov, "best_bt": best_bt}


# ==============================================================================
# INTERACTIVE 2-WAY TELEGRAM COMMAND MODULE
# ==============================================================================
def _build_news_reply(conn: sqlite3.Connection, force_refresh: bool = True) -> str:
    now_utc = datetime.now(timezone.utc)
    events, src = fetch_realtime_usd_high_impact_calendar(conn, force_refresh=force_refresh)
    gov = evaluate_news_governor(events, now_utc, src)

    lines = [
        f"📅 <b>USD HIGH-IMPACT NEWS ({BOT_VERSION})</b>",
        f"<b>Status:</b> <code>{_esc(gov['status_code'])}</code>",
        f"<b>Blackout:</b> -{NEWS_BLACKOUT_BEFORE_MIN}m / +{NEWS_BLACKOUT_AFTER_MIN}m",
    ]
    if gov["hard_blackout"] and gov["resume_utc"]:
        lines.append(f"⛔ <b>Lanjut Est:</b> <code>{_esc(format_utc_wib(gov['resume_utc']))}</code>")

    if gov["upcoming"]:
        lines.append("\n<b>Jadwal Dekat (72j):</b>")
        for ev in gov["upcoming"][:5]:
            tag = "🔴" if ev["is_tier1"] else "🟠"
            lines.append(
                f"{tag} <code>{_esc(format_utc_wib(ev['dt_utc']))}</code> "
                f"(+{_format_minutes_human(ev['delta_min'])}) — {_esc(ev['title'])}"
            )
    else:
        lines.append("\n✅ Tidak ada berita High-Impact USD 72 jam ke depan.")
    return "\n".join(lines)


def _build_audit_reply(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT timestamp_utc, decision, score, entry_price, spread_usd "
        "FROM signals_audit ORDER BY id DESC LIMIT 5"
    ).fetchall()
    closed = conn.execute(
        "SELECT status, realized_r FROM active_trades WHERE status LIKE 'CLOSED_%'"
    ).fetchall()

    lines = ["📜 <b>5 AUDIT &amp; FORWARD-TEST</b>"]
    if closed:
        wins = sum(1 for _, r in closed if r > 0)
        net_r = sum(r for _, r in closed)
        lines.append(f"<b>Live Forward:</b> {len(closed)} Trades | WR {100.0 * wins / len(closed):.1f}% | Net <code>{net_r:+.2f}R</code>\n")
    else:
        lines.append("<b>Live Forward:</b> Belum ada trade tertutup\n")

    if not rows:
        lines.append("Belum ada riwayat audit tersimpan.")
    else:
        for ts, dec, sc, ent, spr in rows:
            lines.append(f"• <code>{_esc(ts[11:16])}Z</code> | <b>{_esc(dec)}</b> ({sc}) @ <code>{ent:.2f}</code> (${spr:.2f})")
    return "\n".join(lines)


def process_telegram_commands(conn: sqlite3.Connection, poll_timeout: int = 0) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    offset_str = get_state(conn, "tg_update_offset", "0")
    offset = int(offset_str) if offset_str.isdigit() else 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        resp = requests.post(
            url,
            json={"offset": offset, "timeout": poll_timeout, "allowed_updates": ["message"]},
            timeout=poll_timeout + 10,
        )
        if resp.status_code == 409:
            logger.warning("Telegram 409 Conflict detected; deleting active webhook and retrying...")
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook", timeout=8)
            resp = requests.post(
                url,
                json={"offset": offset, "timeout": poll_timeout, "allowed_updates": ["message"]},
                timeout=poll_timeout + 10,
            )
        if resp.status_code != 200:
            logger.warning(f"Telegram getUpdates HTTP {resp.status_code}")
            return False
        updates = resp.json().get("result", [])
    except Exception as exc:
        logger.warning(f"Telegram 2-Way Listener error: {exc}")
        return False

    if not updates:
        return False

    logger.info(f"Telegram 2-Way Listener fetched {len(updates)} pending update(s).")
    max_uid = max(int(u.get("update_id", 0)) + 1 for u in updates)
    set_state(conn, "tg_update_offset", str(max_uid))

    seen_batch = set()
    handled_check = False

    for upd in updates:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not text.startswith("/") or not chat_id:
            continue
        if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
            logger.warning(f"Ignoring unauthorized Telegram chat_id={chat_id}")
            continue

        cmd = text.split()[0].split("@")[0].lower()
        batch_key = (chat_id, cmd)
        if batch_key in seen_batch:
            logger.info(f"Deduplicated repeated command [{cmd}] in current batch.")
            continue
        seen_batch.add(batch_key)

        logger.info(f"Processing 2-Way Telegram command [{cmd}] from chat_id=***")
        if cmd in ("/check", "/signal"):
            run_analysis_cycle(conn, notify_mode="command", reply_chat_id=chat_id, force_news_refresh=True)
            handled_check = True
        elif cmd == "/news":
            send_telegram_message(_build_news_reply(conn, force_refresh=True), chat_id=chat_id)
        elif cmd == "/audit":
            send_telegram_message(_build_audit_reply(conn), chat_id=chat_id)
        elif cmd == "/chart":
            if os.path.exists(CHART_PATH):
                send_telegram_photo(CHART_PATH, f"{BOT_EDITION} Backtest Equity", chat_id=chat_id)
            else:
                send_telegram_message("Chart belum tersedia; jalankan /check terlebih dahulu.", chat_id=chat_id)
        elif cmd == "/status":
            now_utc = datetime.now(timezone.utc)
            bbo = fetch_swissquote_bbo()
            spr_val = bbo["spread"] if bbo else 0.0
            _, _, s_name, req_sc = evaluate_session_governor(now_utc, spr_val)
            events, src = fetch_realtime_usd_high_impact_calendar(conn, force_refresh=False)
            ng = evaluate_news_governor(events, now_utc, src)
            bbo_line = (
                f"<b>Spot:</b> <code>{bbo['bid']:.2f}/{bbo['ask']:.2f}</code> (Spr <code>${spr_val:.2f}</code>)"
                if bbo else "<b>Spot:</b> <i>Unavailable</i>"
            )
            status_msg = (
                f"🛰 <b>STATUS XAUUSD ({BOT_VERSION})</b>\n"
                f"<b>Waktu:</b> <code>{_esc(format_utc_wib(now_utc))}</code>\n"
                f"<b>Sesi:</b> <code>{_esc(s_name)}</code> (Req&gt;={req_sc})\n"
                f"<b>News:</b> <code>{_esc(ng['status_code'])}</code>\n"
                f"{bbo_line}"
            )
            send_telegram_message(status_msg, chat_id=chat_id)
        elif cmd in ("/help", "/start"):
            help_msg = (
                f"🤖 <b>{_esc(BOT_EDITION)}</b>\n"
                "/check atau /signal — Analisa cepat XAUUSD + news\n"
                "/news — Jadwal High-Impact USD\n"
                "/status — Sesi &amp; spread live\n"
                "/chart — Grafik backtest\n"
                "/audit — 5 sinyal &amp; forward-test live"
            )
            send_telegram_message(help_msg, chat_id=chat_id)

    return handled_check


def _seconds_since_last_m15_close(now_utc: datetime) -> float:
    minute_mod = now_utc.minute % 15
    return minute_mod * 60.0 + now_utc.second + (now_utc.microsecond / 1_000_000.0)


def run_continuous_polling_loop(conn: sqlite3.Connection) -> None:
    """
    Precision M15 Candle-Close Aligned Polling Loop:
    Executes within ~2 seconds of :00, :15, :30, :45 UTC bar close instead of drifting every 900s.
    """
    logger.info(f"Starting {BOT_EDITION} Precision Candle-Aligned Polling Mode...")
    started_at = time.time()
    last_bar_bucket = ""

    while True:
        try:
            if MAX_POLLING_RUNTIME_SEC > 0 and (time.time() - started_at) >= MAX_POLLING_RUNTIME_SEC:
                logger.info(f"Polling graceful exit after {MAX_POLLING_RUNTIME_SEC}s.")
                break

            process_telegram_commands(conn, poll_timeout=10)
            now_utc = datetime.now(timezone.utc)
            bucket_min = (now_utc.minute // 15) * 15
            current_bucket = f"{now_utc.strftime('%Y-%m-%d %H')}:{bucket_min:02d}"
            sec_after_close = _seconds_since_last_m15_close(now_utc)

            if current_bucket != last_bar_bucket and sec_after_close >= 2.0:
                run_analysis_cycle(conn, notify_mode="auto")
                last_bar_bucket = current_bucket
        except KeyboardInterrupt:
            logger.info("Polling stopped by user.")
            break
        except Exception as exc:
            logger.error(f"Polling loop error: {exc}")
            send_telegram_message(f"🚨 <b>CIRCUIT BREAKER / POLLING ERROR ({BOT_VERSION}):</b> <code>{_esc(exc)}</code>")
            time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=BOT_EDITION)
    parser.add_argument("--mode", choices=["oneshot", "polling"], default="oneshot")
    args = parser.parse_args()

    conn = init_db(DB_PATH)
    try:
        if args.mode == "polling":
            run_continuous_polling_loop(conn)
        else:
            handled_check = process_telegram_commands(conn, poll_timeout=0)
            if not handled_check:
                run_analysis_cycle(conn, notify_mode="auto")
            else:
                logger.info("Skipped redundant auto cycle because /check or /signal already executed in oneshot batch.")
    except Exception as exc:
        logger.exception(f"Circuit Breaker triggered — Fatal execution error: {exc}")
        send_telegram_message(
            f"🚨 <b>CIRCUIT BREAKER DARURAT ({_esc(BOT_EDITION)})</b>\n"
            f"<b>Status:</b> Eksekusi dihentikan aman\n"
            f"<b>Penyebab:</b> <code>{_esc(exc)}</code>"
        )
        raise
    finally:
        close_db(conn)


if __name__ == "__main__":
    main()
