#!/usr/bin/env python3
"""
APEX-QUANT-OMNI SOVEREIGN v75.5 (HTML-Hardened Sniper Precision Edition)
Institutional-Grade Automated XAUUSD (Spot Gold) Signal Generation:
  - Multi-Source Failover + Live Swissquote BBO Spread Calibration
  - High-Impact USD News Blackout Governor (CPI/NFP/FOMC)
  - ADX Regime Filter + Liquidity Sweep & Fair Value Gap (FVG) Sniper Detection
  - Anti-Chasing Extension Guard + Pullback-in-Trend Scoring + Order Flow Gate
  - Hybrid Structural-ATR Stop Loss + Dynamic Killzone Thresholding
  - Synchronized Grid Search Optimization + Auto-Trailing Stop + SQLite Forensic Audit
  - HTML-Safe & Batch-Deduplicated 2-Way Telegram Command Engine
"""

# CRITICAL SERVERLESS SAFEGUARD: Headless Matplotlib Backend MUST be set first
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import argparse
import html
import json
import logging
import os
import re
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ==============================================================================
# GLOBAL CONSTANTS & CONFIGURATION
# ==============================================================================
ENGINE_VERSION = "v75.5"
SYMBOL_PRIMARY = "XAUUSD=X"  # Spot Gold (Explicitly avoiding futures GC=F)
ASSET_LABEL = "XAUUSD (SPOT GOLD)"
DB_PATH = os.getenv("SQLITE_DB_PATH", "trading_signals.db")
CHART_PATH = os.getenv("BACKTEST_CHART_PATH", "backtest_performance.png")
EPSILON = 1e-8
WIB_TZ = timezone(timedelta(hours=7))

# Account & Risk Defaults
DEFAULT_ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
DEFAULT_RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
CONTRACT_OUNCES_PER_LOT = 100.0  # Standard 1.00 XAUUSD Lot = 100 Troy Ounces
MIN_LOT_SIZE = 0.01
MAX_LOT_SIZE = 50.00
CONFLUENCE_THRESHOLD = int(os.getenv("CONFLUENCE_THRESHOLD", "68"))
ASIAN_SESSION_MIN_SCORE = max(CONFLUENCE_THRESHOLD + 8, 76)
MAX_ALLOWED_SPREAD_USD = float(os.getenv("MAX_ALLOWED_SPREAD_USD", "3.00"))

# Sniper Precision Guards
MAX_EXTENSION_ATR = float(os.getenv("MAX_EXTENSION_ATR", "1.80"))
MIN_ORDER_FLOW_CONFIRM = float(os.getenv("MIN_ORDER_FLOW_CONFIRM", "0.12"))
ADX_TREND_MIN = float(os.getenv("ADX_TREND_MIN", "18.0"))

# High-Impact USD News Governor Settings
NEWS_BLACKOUT_BEFORE_MIN = int(os.getenv("NEWS_BLACKOUT_BEFORE_MIN", "45"))
NEWS_BLACKOUT_AFTER_MIN = int(os.getenv("NEWS_BLACKOUT_AFTER_MIN", "30"))
NEWS_CACHE_TTL_SECONDS = int(os.getenv("NEWS_CACHE_TTL_SECONDS", "14400"))
TIER1_GOLD_CATALYSTS = (
    "CPI", "NON-FARM", "NFP", "FOMC", "FEDERAL FUNDS", "POWELL",
    "PCE", "PPI", "GDP", "UNEMPLOYMENT", "RETAIL SALES", "ISM", "JOLTS",
)

# Secrets & Webhook Endpoints
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# Grid Search Parameter Space
ATR_PERIOD_GRID = [10, 14, 20]
Z_THRESHOLD_GRID = [1.5, 2.0, 2.5]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("APEX_QUANT_OMNI")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _esc(val: Any) -> str:
    """Escapes <, >, & safely for Telegram HTML parse_mode."""
    return html.escape(str(val), quote=False)


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================
@dataclass
class SpotQuote:
    bid: float
    ask: float
    mid: float
    spread: float
    provider: str


@dataclass
class NewsEvent:
    title: str
    event_utc: str
    impact: str
    forecast: str
    previous: str
    is_tier1_catalyst: bool


@dataclass
class NewsGovernorStatus:
    is_allowed: bool
    guard_state: str
    reason: str
    source: str
    active_blackout_event: Optional[str]
    upcoming_events: List[NewsEvent]


@dataclass
class SessionStatus:
    is_allowed: bool
    session_name: str
    reason: str
    utc_timestamp: str


@dataclass
class GridSearchResult:
    optimal_atr_period: int
    optimal_z_threshold: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    expectancy_r: float
    total_trades: int
    net_return_pct: float
    equity_curve: List[float]
    timestamps: List[pd.Timestamp]
    trade_log: List[Dict[str, Any]]


@dataclass
class SignalReport:
    timestamp_utc: str
    candle_timestamp_utc: str
    asset: str
    data_source: str
    spot_quote: Optional[SpotQuote]
    session_status: SessionStatus
    news_status: NewsGovernorStatus
    signal_decision: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    trailing_stop_protocol: str
    confluence_score: int
    required_score: int
    z_score: float
    adx_value: float
    smc_state: str
    order_flow_state: str
    mtf_harmony_state: str
    sniper_filter_note: str
    atr_value: float
    optimal_atr_period: int
    optimal_z_threshold: float
    recommended_lot: float
    risk_amount_usd: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    expectancy_r: float
    command_listener_status: str
    webhook_status: str
    pillar_breakdown: Dict[str, Any]


# ==============================================================================
# EXPONENTIAL BACKOFF HTTP & WEBHOOK DELIVERY ENGINE
# ==============================================================================
def request_with_exponential_backoff(
    method: str,
    url: str,
    max_retries: int = 3,
    backoff_delays: Tuple[int, ...] = (2, 4, 8),
    timeout: int = 15,
    **kwargs: Any,
) -> Optional[requests.Response]:
    """Executes HTTP requests with strict exponential backoff (2s, 4s, 8s)."""
    attempt = 0
    while attempt <= max_retries:
        try:
            resp = requests.request(method=method, url=url, timeout=timeout, **kwargs)
            if resp.status_code in (200, 201, 204):
                return resp

            if resp.status_code == 404:
                logger.warning(f"HTTP 404 on {url}; failing over immediately.")
                return resp

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt < max_retries:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(15, max(delay, int(retry_after)))
                    logger.warning(
                        f"HTTP {resp.status_code} on {url}. Retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue

            logger.warning(
                f"HTTP request returned status {resp.status_code} on {url}: {resp.text[:200]}"
            )
            return resp
        except requests.RequestException as exc:
            if attempt < max_retries:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning(
                    f"Request exception ({exc.__class__.__name__}): {exc}. "
                    f"Retrying in {delay}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(delay)
                attempt += 1
            else:
                logger.error(f"Request exhausted all {max_retries} retries: {exc}")
                return None
    return None


def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Sends a formatted Telegram message using Exponential Backoff (2s, 4s, 8s).
    Includes automatic plain-text fallback if Telegram rejects HTML entities (HTTP 400).
    """
    target_chat = (chat_id or TELEGRAM_CHAT_ID).strip()
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        logger.warning("Telegram credentials not configured; skipping Telegram delivery.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    resp = request_with_exponential_backoff(
        "POST", url, max_retries=3, backoff_delays=(2, 4, 8), json=payload
    )
    if resp is not None and resp.status_code == 200:
        return True

    # Automatic plain-text fallback if HTML parsing ever fails with HTTP 400
    if resp is not None and resp.status_code == 400 and parse_mode:
        logger.warning("Telegram HTML parse 400 detected; retrying with plain-text fallback...")
        plain_text = html.unescape(re.sub(r"<[^>]+>", "", text))
        fallback_payload = {
            "chat_id": target_chat,
            "text": plain_text,
            "disable_web_page_preview": True,
        }
        fb_resp = request_with_exponential_backoff(
            "POST", url, max_retries=2, backoff_delays=(2, 4), json=fallback_payload
        )
        if fb_resp is not None and fb_resp.status_code == 200:
            return True

    status_code = resp.status_code if resp is not None else "None"
    logger.error(f"sendMessage failed: status={status_code}")
    return False


def send_telegram_photo(
    image_path: str,
    caption: str = "",
    chat_id: Optional[str] = None,
) -> bool:
    """Uploads an image artifact to Telegram using Exponential Backoff (2s, 4s, 8s)."""
    target_chat = (chat_id or TELEGRAM_CHAT_ID).strip()
    if not TELEGRAM_BOT_TOKEN or not target_chat or not os.path.exists(image_path):
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    delays = (2, 4, 8)
    for attempt in range(4):
        try:
            with open(image_path, "rb") as img_file:
                files = {"photo": img_file}
                data = {"chat_id": target_chat, "caption": caption[:1024], "parse_mode": "HTML"}
                resp = requests.post(url, data=data, files=files, timeout=20)
            if resp.status_code == 200:
                return True
            if (resp.status_code == 429 or 500 <= resp.status_code < 600) and attempt < 3:
                time.sleep(delays[attempt])
                continue
            logger.error(f"Telegram sendPhoto error {resp.status_code}: {resp.text[:200]}")
            return False
        except requests.RequestException as exc:
            if attempt < 3:
                time.sleep(delays[attempt])
            else:
                logger.error(f"Telegram sendPhoto failed: {exc}")
                return False
    return False


def send_discord_webhook(report: SignalReport) -> bool:
    """Sends a structured institutional embed to Discord using Exponential Backoff."""
    if not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")
        return False

    if "BUY" in report.signal_decision:
        color = 0x10B981
    elif "SELL" in report.signal_decision:
        color = 0xEF4444
    else:
        color = 0xF59E0B

    spread_str = (
        f"Bid `{report.spot_quote.bid:.2f}` / Ask `{report.spot_quote.ask:.2f}` "
        f"(Spread `${report.spot_quote.spread:.2f}`)"
        if report.spot_quote
        else "N/A"
    )

    embed = {
        "title": f"APEX-QUANT-OMNI SOVEREIGN {ENGINE_VERSION} | {report.asset}",
        "description": (
            f"**Signal Decision:** `{report.signal_decision}`\n"
            f"**Trailing Stop Protocol:** `{report.trailing_stop_protocol}`\n"
            f"**Session Governor:** `{report.session_status.session_name}`\n"
            f"**Macro News Governor:** `{report.news_status.guard_state}`\n"
            f"**Sniper Filter:** `{report.sniper_filter_note}`\n"
            f"**Live Spot BBO:** {spread_str}"
        ),
        "color": color,
        "fields": [
            {"name": "Precision Entry", "value": f"`{report.entry_price:.2f}`", "inline": True},
            {"name": "Forensic Stop Loss", "value": f"`{report.stop_loss:.2f}`", "inline": True},
            {"name": "Confluence Score", "value": f"`{report.confluence_score}/{report.required_score}`", "inline": True},
            {"name": "TP1 (1.5R)", "value": f"`{report.tp1:.2f}`", "inline": True},
            {"name": "TP2 (2.5R)", "value": f"`{report.tp2:.2f}`", "inline": True},
            {"name": "TP3 (4.0R)", "value": f"`{report.tp3:.2f}`", "inline": True},
            {
                "name": "Risk & Sizing",
                "value": f"Lot: `{report.recommended_lot:.2f}` | Risk: `${report.risk_amount_usd:.2f}`",
                "inline": True,
            },
            {
                "name": "Grid Search Optimal",
                "value": f"ATR: `{report.optimal_atr_period}` | Z: `{report.optimal_z_threshold:.1f}` | ADX: `{report.adx_value:.1f}`",
                "inline": True,
            },
            {
                "name": "Backtest Metrics",
                "value": (
                    f"WR: `{report.win_rate:.1f}%` | PF: `{report.profit_factor:.2f}` | "
                    f"MDD: `{report.max_drawdown:.1f}%` | Exp: `{report.expectancy_r:+.2f}R`"
                ),
                "inline": True,
            },
        ],
        "footer": {"text": f"Feed: {report.data_source} | UTC: {report.timestamp_utc}"},
    }

    payload = {"username": f"APEX-QUANT-OMNI {ENGINE_VERSION}", "embeds": [embed]}
    resp = request_with_exponential_backoff(
        "POST", DISCORD_WEBHOOK_URL, max_retries=3, backoff_delays=(2, 4, 8), json=payload
    )
    return resp is not None and resp.status_code in (200, 204)


def send_emergency_telegram_alert(context: str, error: Exception) -> None:
    """Dispatches critical exception telemetry to Telegram so silent failures never occur."""
    tb_snippet = _esc(traceback.format_exc()[-900:])
    msg = (
        f"🚨 <b>APEX-QUANT-OMNI {_esc(ENGINE_VERSION)} — EMERGENCY EXCEPTION ALERT</b> 🚨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>Module:</b> {_esc(context)}\n"
        f"❌ <b>Error:</b> <code>{_esc(error)}</code>\n"
        f"🕒 <b>UTC:</b> {_esc(datetime.now(timezone.utc).isoformat())}\n"
        f"<pre>{tb_snippet}</pre>"
    )
    send_telegram_message(msg)


# ==============================================================================
# SQLITE ARTIFACT PERSISTENCE & FORENSIC AUDIT LOGGER
# ==============================================================================
def init_sqlite_db(db_path: str = DB_PATH) -> None:
    """Initializes the SQLite database schema for forensic audit, state, and trailing stops."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS signals_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                candle_timestamp_utc TEXT NOT NULL,
                asset TEXT NOT NULL,
                data_source TEXT NOT NULL,
                session_name TEXT NOT NULL,
                session_allowed INTEGER NOT NULL,
                signal_decision TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp3 REAL NOT NULL,
                trailing_stop_protocol TEXT NOT NULL,
                confluence_score INTEGER NOT NULL,
                z_score REAL NOT NULL,
                smc_state TEXT NOT NULL,
                order_flow_state TEXT NOT NULL,
                mtf_harmony_state TEXT NOT NULL,
                atr_value REAL NOT NULL,
                optimal_atr_period INTEGER NOT NULL,
                optimal_z_threshold REAL NOT NULL,
                recommended_lot REAL NOT NULL,
                risk_amount_usd REAL NOT NULL,
                win_rate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                webhook_status TEXT NOT NULL,
                raw_metrics_json TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS active_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_utc TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                initial_sl REAL NOT NULL,
                current_sl REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp3 REAL NOT NULL,
                atr_at_entry REAL NOT NULL,
                tp1_hit INTEGER NOT NULL DEFAULT 0,
                peak_favorable_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                updated_utc TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_bot_state(key: str, default: str = "", db_path: str = DB_PATH) -> str:
    init_sqlite_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def set_bot_state(key: str, value: str, db_path: str = DB_PATH) -> None:
    init_sqlite_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def forensic_audit_log(report: SignalReport, db_path: str = DB_PATH) -> None:
    """Outputs raw metrics to console and persists audit record into SQLite."""
    init_sqlite_db(db_path)

    raw_payload = {
        "pillar_breakdown": report.pillar_breakdown,
        "session": asdict(report.session_status),
        "news_governor": {
            "is_allowed": report.news_status.is_allowed,
            "guard_state": report.news_status.guard_state,
            "reason": report.news_status.reason,
            "source": report.news_status.source,
            "active_blackout_event": report.news_status.active_blackout_event,
            "upcoming_events": [asdict(ev) for ev in report.news_status.upcoming_events],
        },
        "spot_quote": asdict(report.spot_quote) if report.spot_quote else None,
        "z_score": report.z_score,
        "adx_value": report.adx_value,
        "sniper_filter_note": report.sniper_filter_note,
        "required_score": report.required_score,
        "atr_value": report.atr_value,
        "expectancy_r": report.expectancy_r,
        "optimal_atr_period": report.optimal_atr_period,
        "optimal_z_threshold": report.optimal_z_threshold,
    }

    spread_log = (
        f"Bid={report.spot_quote.bid:.2f} Ask={report.spot_quote.ask:.2f} Spread=${report.spot_quote.spread:.2f}"
        if report.spot_quote
        else "SpotBBO=Unavailable"
    )

    logger.info("========== FORENSIC AUDIT LOG ==========")
    logger.info(
        f"Asset={report.asset} | Decision={report.signal_decision} | "
        f"Score={report.confluence_score}/100 (Req>={report.required_score}) | Source={report.data_source}"
    )
    logger.info(
        f"Entry={report.entry_price:.2f} | SL={report.stop_loss:.2f} | "
        f"TP1={report.tp1:.2f} | TP2={report.tp2:.2f} | TP3={report.tp3:.2f} | {spread_log}"
    )
    logger.info(
        f"Z-Score={report.z_score:.4f} | ADX={report.adx_value:.2f} | ATR={report.atr_value:.4f} | "
        f"SMC={report.smc_state} | OrderFlow={report.order_flow_state} | MTF={report.mtf_harmony_state}"
    )
    logger.info(
        f"SniperGate={report.sniper_filter_note} | "
        f"NewsGovernor={report.news_status.guard_state} | Session={report.session_status.session_name}"
    )
    logger.info(
        f"GridSearch(ATR={report.optimal_atr_period}, Z={report.optimal_z_threshold:.1f}) | "
        f"WR={report.win_rate:.1f}% | PF={report.profit_factor:.2f} | "
        f"MDD={report.max_drawdown:.1f}% | Exp={report.expectancy_r:+.2f}R"
    )
    logger.info(
        f"Lot={report.recommended_lot:.2f} | RiskUSD=${report.risk_amount_usd:.2f} | "
        f"Trailing={report.trailing_stop_protocol} | Webhook={report.webhook_status}"
    )
    logger.info("========================================")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO signals_audit (
                timestamp_utc, candle_timestamp_utc, asset, data_source,
                session_name, session_allowed, signal_decision, entry_price,
                stop_loss, tp1, tp2, tp3, trailing_stop_protocol,
                confluence_score, z_score, smc_state, order_flow_state,
                mtf_harmony_state, atr_value, optimal_atr_period,
                optimal_z_threshold, recommended_lot, risk_amount_usd,
                win_rate, profit_factor, max_drawdown, webhook_status,
                raw_metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.timestamp_utc,
                report.candle_timestamp_utc,
                report.asset,
                report.data_source,
                report.session_status.session_name,
                1 if (report.session_status.is_allowed and report.news_status.is_allowed) else 0,
                report.signal_decision,
                float(report.entry_price),
                float(report.stop_loss),
                float(report.tp1),
                float(report.tp2),
                float(report.tp3),
                report.trailing_stop_protocol,
                int(report.confluence_score),
                float(report.z_score),
                report.smc_state,
                report.order_flow_state,
                report.mtf_harmony_state,
                float(report.atr_value),
                int(report.optimal_atr_period),
                float(report.optimal_z_threshold),
                float(report.recommended_lot),
                float(report.risk_amount_usd),
                float(report.win_rate),
                float(report.profit_factor),
                float(report.max_drawdown),
                report.webhook_status,
                json.dumps(raw_payload),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# MACRO HIGH-IMPACT USD NEWS CALENDAR GOVERNOR (CPI / NFP / FOMC)
# ==============================================================================
def _fetch_raw_economic_calendar() -> Tuple[List[Dict[str, Any]], str]:
    """Fetches weekly macro calendar JSON with 4-hour SQLite cache."""
    now_epoch = int(time.time())
    cached_ts_str = get_bot_state("usd_news_cache_ts", "0")
    cached_json_str = get_bot_state("usd_news_cache_json", "")

    if cached_ts_str.isdigit() and cached_json_str:
        age = now_epoch - int(cached_ts_str)
        if age < NEWS_CACHE_TTL_SECONDS:
            try:
                parsed = json.loads(cached_json_str)
                if isinstance(parsed, list):
                    return parsed, "SQLite Cache (ForexFactory Weekly)"
            except ValueError:
                pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
    }
    endpoints = (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
    )
    for url in endpoints:
        resp = request_with_exponential_backoff(
            "GET", url, max_retries=1, backoff_delays=(2,), timeout=10, headers=headers
        )
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    set_bot_state("usd_news_cache_ts", str(now_epoch))
                    set_bot_state("usd_news_cache_json", json.dumps(data))
                    return data, "Live ForexFactory Weekly JSON"
            except ValueError:
                continue

    if cached_json_str:
        try:
            parsed = json.loads(cached_json_str)
            if isinstance(parsed, list):
                return parsed, "Stale Cache Fallback"
        except ValueError:
            pass

    return [], "Fallback (Deterministic NFP Guard)"


def evaluate_news_governor(now_utc: Optional[datetime] = None) -> NewsGovernorStatus:
    """Checks High-Impact USD events (CPI, NFP, FOMC, etc.) with [-45m, +30m] blackout."""
    now = now_utc or datetime.now(timezone.utc)
    raw_events, source = _fetch_raw_economic_calendar()

    high_usd_events: List[Tuple[datetime, NewsEvent]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        country = str(item.get("country", "")).strip().upper()
        impact = str(item.get("impact", "")).strip().upper()
        if country != "USD" or impact != "HIGH":
            continue

        date_str = str(item.get("date", "")).strip()
        if not date_str:
            continue
        try:
            ev_dt = datetime.fromisoformat(date_str)
            if ev_dt.tzinfo is None:
                ev_dt = ev_dt.replace(tzinfo=timezone.utc)
            else:
                ev_dt = ev_dt.astimezone(timezone.utc)
        except ValueError:
            continue

        title = str(item.get("title", "USD High-Impact Event")).strip()
        is_tier1 = any(kw in title.upper() for kw in TIER1_GOLD_CATALYSTS)
        ev_obj = NewsEvent(
            title=title,
            event_utc=ev_dt.isoformat(),
            impact="HIGH",
            forecast=str(item.get("forecast", "") or "-").strip(),
            previous=str(item.get("previous", "") or "-").strip(),
            is_tier1_catalyst=is_tier1,
        )
        high_usd_events.append((ev_dt, ev_obj))

    high_usd_events.sort(key=lambda pair: pair[0])

    horizon = now + timedelta(hours=48)
    upcoming_48h: List[NewsEvent] = [
        ev for dt_ev, ev in high_usd_events if (now - timedelta(minutes=NEWS_BLACKOUT_AFTER_MIN)) <= dt_ev <= horizon
    ]

    for dt_ev, ev in high_usd_events:
        diff_min = (dt_ev - now).total_seconds() / 60.0
        if -NEWS_BLACKOUT_AFTER_MIN <= diff_min <= NEWS_BLACKOUT_BEFORE_MIN:
            if diff_min >= 0:
                timing_desc = f"releases in {int(round(diff_min))} min"
            else:
                timing_desc = f"released {int(round(abs(diff_min)))} min ago"
            return NewsGovernorStatus(
                is_allowed=False,
                guard_state="NEWS_BLACKOUT_GUARD",
                reason=f"USD High-Impact [{ev.title}] {timing_desc}. Execution suppressed.",
                source=source,
                active_blackout_event=f"{ev.title} ({ev.event_utc})",
                upcoming_events=upcoming_48h[:8],
            )

    if not raw_events:
        is_first_friday = now.weekday() == 4 and 1 <= now.day <= 7
        if is_first_friday and (12 * 60) <= (now.hour * 60 + now.minute) <= (14 * 60 + 30):
            return NewsGovernorStatus(
                is_allowed=False,
                guard_state="NFP_FAILSAFE_BLACKOUT",
                reason="First-Friday NFP blackout window (12:00-14:30 UTC) active while calendar offline.",
                source=source,
                active_blackout_event="US Non-Farm Payrolls (Deterministic Window)",
                upcoming_events=[],
            )

    if upcoming_48h:
        next_ev = upcoming_48h[0]
        next_dt = datetime.fromisoformat(next_ev.event_utc)
        mins_away = int(round((next_dt - now).total_seconds() / 60.0))
        hrs_away = mins_away / 60.0
        reason_str = (
            f"Clear | Next USD High-Impact: {next_ev.title} in {hrs_away:.1f}h"
            if hrs_away >= 1.0
            else f"Clear | Next USD High-Impact: {next_ev.title} in {mins_away}m"
        )
    else:
        reason_str = "Clear | No USD High-Impact releases within 48h."

    return NewsGovernorStatus(
        is_allowed=True,
        guard_state="NEWS_CLEAR",
        reason=reason_str,
        source=source,
        active_blackout_event=None,
        upcoming_events=upcoming_48h[:8],
    )


# ==============================================================================
# MARKET SESSION GOVERNOR & TIMING SAFETY
# ==============================================================================
def evaluate_market_session(
    now_utc: Optional[datetime] = None,
    spot_quote: Optional[SpotQuote] = None,
) -> SessionStatus:
    """Verifies UTC time, day of week, liquidity killzone, and live BBO spread."""
    now = now_utc or datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour
    ts_str = now.isoformat()

    if weekday == 4 and hour >= 21:
        return SessionStatus(
            is_allowed=False,
            session_name="WEEKEND_CLOSE_GUARD",
            reason="Friday close window (>=21:00 UTC) suppressed to prevent weekend gap & spread slippage.",
            utc_timestamp=ts_str,
        )
    if weekday == 5:
        return SessionStatus(
            is_allowed=False,
            session_name="WEEKEND_CLOSED",
            reason="Saturday spot gold market closed.",
            utc_timestamp=ts_str,
        )
    if weekday == 6 and hour < 23:
        return SessionStatus(
            is_allowed=False,
            session_name="SUNDAY_OPEN_GUARD",
            reason="Sunday pre-open / illiquid initial open (<23:00 UTC) suppressed.",
            utc_timestamp=ts_str,
        )

    if 21 <= hour < 22:
        return SessionStatus(
            is_allowed=False,
            session_name="DAILY_ROLLOVER_GUARD",
            reason="Daily FX/Gold rollover hour (21:00-22:00 UTC) suppressed due to spread expansion.",
            utc_timestamp=ts_str,
        )
    if 22 <= hour < 23:
        return SessionStatus(
            is_allowed=False,
            session_name="ASIAN_OPEN_NOISE_GUARD",
            reason="Low-liquidity Asian open transition (22:00-23:00 UTC) suppressed.",
            utc_timestamp=ts_str,
        )

    if spot_quote is not None and spot_quote.spread > MAX_ALLOWED_SPREAD_USD:
        return SessionStatus(
            is_allowed=False,
            session_name="SPREAD_SPIKE_GUARD",
            reason=(
                f"Live spot spread ${spot_quote.spread:.2f} exceeds guard "
                f"limit ${MAX_ALLOWED_SPREAD_USD:.2f}."
            ),
            utc_timestamp=ts_str,
        )

    if 0 <= hour < 7:
        session_name = "ASIAN_SESSION_ACTIVE"
    elif 7 <= hour < 12:
        session_name = "LONDON_SESSION_PRIME"
    elif 12 <= hour < 16:
        session_name = "LONDON_NY_OVERLAP_PEAK"
    else:
        session_name = "NEW_YORK_SESSION_ACTIVE"

    return SessionStatus(
        is_allowed=True,
        session_name=session_name,
        reason="Active institutional liquidity window & tight spread verified.",
        utc_timestamp=ts_str,
    )


# ==============================================================================
# MULTI-SOURCE FREE FALLBACK DATA PIPELINE (MT5 SPOT GOLD SYNC & FAILOVER)
# ==============================================================================
def _normalize_ohlcv_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Flattens MultiIndex columns, normalizes UTC index, and adds EffectiveVolume."""
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.loc[:, ~df.columns.duplicated()]
    rename_map = {c: str(c).strip().title() for c in df.columns}
    df = df.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            return pd.DataFrame()

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df["Volume"] = df["Volume"].fillna(0.0)

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    range_proxy = ((df["High"] - df["Low"]).abs() + (df["Close"] - df["Open"]).abs() + EPSILON) * 1000.0
    df["EffectiveVolume"] = np.where(df["Volume"] > 0.0, df["Volume"], range_proxy)
    return df


def fetch_swissquote_live_spot_quote() -> Optional[SpotQuote]:
    """Queries Swissquote free public BBO feed for live MT5-grade XAU/USD Bid/Ask/Spread."""
    url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
    resp = request_with_exponential_backoff(
        "GET", url, max_retries=1, backoff_delays=(2,), timeout=8
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            spreads = data[0].get("spreadProfilePrices", [])
            if spreads:
                bid = float(spreads[0]["bid"])
                ask = float(spreads[0]["ask"])
                if bid > 100.0 and ask >= bid:
                    return SpotQuote(
                        bid=round(bid, 2),
                        ask=round(ask, 2),
                        mid=round((bid + ask) / 2.0, 2),
                        spread=round(ask - bid, 2),
                        provider="Swissquote Institutional BBO",
                    )
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return None


def _calibrate_dataframe_to_live_spot(df: pd.DataFrame, spot_quote: Optional[SpotQuote]) -> pd.DataFrame:
    """Applies exact basis calibration to align feed closing price with live XAU/USD spot mid."""
    if df.empty or spot_quote is None:
        return df
    last_close = float(df["Close"].iloc[-1])
    basis_ratio = spot_quote.mid / (last_close + EPSILON)
    if 0.95 <= basis_ratio <= 1.05:
        df = df.copy()
        for col in ("Open", "High", "Low", "Close"):
            df[col] = df[col] * basis_ratio
    return df


def _fetch_from_yfinance_pair(ticker: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Primary Probe: yfinance official Spot Gold ticker XAUUSD=X."""
    try:
        raw_15m = yf.download(
            tickers=ticker, period="30d", interval="15m", progress=False, auto_adjust=False, threads=False
        )
        df_15m = _normalize_ohlcv_dataframe(raw_15m)
        if len(df_15m) < 120:
            return pd.DataFrame(), pd.DataFrame()
        raw_1h = yf.download(
            tickers=ticker, period="60d", interval="1h", progress=False, auto_adjust=False, threads=False
        )
        df_1h = _normalize_ohlcv_dataframe(raw_1h)
        return df_15m, df_1h
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


def _fetch_from_yahoo_chart_http(ticker: str, interval: str, range_str: str) -> pd.DataFrame:
    """Secondary Feed: Fast Yahoo Finance v8 HTTP Chart API probe."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": interval, "range": range_str, "includePrePost": "false"}
    resp = request_with_exponential_backoff(
        "GET", url, max_retries=1, backoff_delays=(2,), timeout=10, headers=headers, params=params
    )
    if resp is None or resp.status_code != 200:
        return pd.DataFrame()
    try:
        payload = resp.json()
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "Open": quote["open"],
                "High": quote["high"],
                "Low": quote["low"],
                "Close": quote["close"],
                "Volume": quote.get("volume", [0.0] * len(timestamps)),
            },
            index=pd.to_datetime(timestamps, unit="s", utc=True),
        )
        return _normalize_ohlcv_dataframe(df)
    except (KeyError, IndexError, TypeError, ValueError):
        return pd.DataFrame()


def _fetch_from_yahoo_http_pair(ticker: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df_15m = _fetch_from_yahoo_chart_http(ticker, "15m", "1mo")
    if len(df_15m) < 120:
        return pd.DataFrame(), pd.DataFrame()
    df_1h = _fetch_from_yahoo_chart_http(ticker, "1h", "3mo")
    return df_15m, df_1h


def _fetch_from_binance_spot_gold(interval: str, limit: int, spot_quote: Optional[SpotQuote]) -> pd.DataFrame:
    """Tertiary Feed: Binance Vision public spot gold PAXGUSDT calibrated to Swissquote BBO."""
    url = "https://data-api.binance.vision/api/v3/klines"
    params = {"symbol": "PAXGUSDT", "interval": interval, "limit": limit}
    resp = request_with_exponential_backoff(
        "GET", url, max_retries=2, backoff_delays=(2, 4), timeout=12, params=params
    )
    if resp is None or resp.status_code != 200:
        return pd.DataFrame()
    try:
        rows = resp.json()
        if not isinstance(rows, list) or len(rows) < 50:
            return pd.DataFrame()
        df = pd.DataFrame(
            {
                "Open": [float(r[1]) for r in rows],
                "High": [float(r[2]) for r in rows],
                "Low": [float(r[3]) for r in rows],
                "Close": [float(r[4]) for r in rows],
                "Volume": [float(r[5]) for r in rows],
            },
            index=pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True),
        )
        return _calibrate_dataframe_to_live_spot(_normalize_ohlcv_dataframe(df), spot_quote)
    except (ValueError, KeyError, IndexError, TypeError):
        return pd.DataFrame()


def _fetch_from_kraken_spot_gold(interval_min: int, spot_quote: Optional[SpotQuote]) -> pd.DataFrame:
    """Quaternary Failover Feed: Kraken public OHLC API for PAXGUSD."""
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "PAXGUSD", "interval": interval_min}
    resp = request_with_exponential_backoff(
        "GET", url, max_retries=2, backoff_delays=(2, 4), timeout=12, params=params
    )
    if resp is None or resp.status_code != 200:
        return pd.DataFrame()
    try:
        result = resp.json().get("result", {})
        pair_key = next((k for k in result.keys() if k != "last"), None)
        if not pair_key:
            return pd.DataFrame()
        rows = result[pair_key]
        df = pd.DataFrame(
            {
                "Open": [float(r[1]) for r in rows],
                "High": [float(r[2]) for r in rows],
                "Low": [float(r[3]) for r in rows],
                "Close": [float(r[4]) for r in rows],
                "Volume": [float(r[6]) for r in rows],
            },
            index=pd.to_datetime([int(r[0]) for r in rows], unit="s", utc=True),
        )
        return _calibrate_dataframe_to_live_spot(_normalize_ohlcv_dataframe(df), spot_quote)
    except (ValueError, KeyError, IndexError, TypeError):
        return pd.DataFrame()


def fetch_xauusd_mtf_data() -> Tuple[pd.DataFrame, pd.DataFrame, str, Optional[SpotQuote]]:
    """Fault-tolerant Multi-Source Data Ingestion for 15M execution and 1H macro data."""
    spot_quote = fetch_swissquote_live_spot_quote()
    if spot_quote:
        logger.info(
            f"Live Spot BBO Verified [{spot_quote.provider}]: "
            f"Bid={spot_quote.bid:.2f} | Ask={spot_quote.ask:.2f} | Spread=${spot_quote.spread:.2f}"
        )

    sources = [
        (
            "Nominal (Primary: yfinance XAUUSD=X)",
            lambda: _fetch_from_yfinance_pair(SYMBOL_PRIMARY),
        ),
        (
            "Failover Active (Secondary: Yahoo v8 HTTP XAUUSD=X)",
            lambda: _fetch_from_yahoo_http_pair(SYMBOL_PRIMARY),
        ),
        (
            "Failover Active (Tertiary: Spot PAXG/XAUUSD + Swissquote BBO)",
            lambda: (
                _fetch_from_binance_spot_gold("15m", 1000, spot_quote),
                _fetch_from_binance_spot_gold("1h", 750, spot_quote),
            ),
        ),
        (
            "Failover Active (Quaternary: Kraken PAXGUSD + Swissquote BBO)",
            lambda: (
                _fetch_from_kraken_spot_gold(15, spot_quote),
                _fetch_from_kraken_spot_gold(60, spot_quote),
            ),
        ),
    ]

    for source_label, loader in sources:
        try:
            df_15m, df_1h = loader()
            if len(df_15m) >= 120 and len(df_1h) >= 60:
                logger.info(
                    f"Data ingestion succeeded via [{source_label}] | "
                    f"15M bars={len(df_15m)} | 1H bars={len(df_1h)}"
                )
                return df_15m, df_1h, source_label, spot_quote
            logger.info(f"Feed [{source_label}] unavailable or rate-limited; advancing failover...")
        except Exception as exc:
            logger.warning(f"Feed [{source_label}] raised exception: {exc}. Advancing failover...")

    raise RuntimeError(
        "CRITICAL DATA OUTAGE: All multi-source Spot Gold (XAUUSD) feeds failed."
    )


# ==============================================================================
# TECHNICAL INDICATORS, ADX REGIME & 5-PILLAR SNIPER QUANT ENGINE
# ==============================================================================
def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Computes Wilder-smoothed Average True Range with zero-division guard."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / max(period, 1), adjust=False).mean()
    return atr.fillna(true_range).clip(lower=EPSILON)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Computes Wilder's ADX to distinguish Trending vs Chop/Sideways regimes."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    alpha = 1.0 / max(period, 1)
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean().clip(lower=EPSILON)

    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / (tr_smooth + EPSILON)
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / (tr_smooth + EPSILON)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + EPSILON)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx.fillna(0.0)


def compute_rolling_zscore(series: pd.Series, window: int = 20) -> pd.Series:
    """Computes rolling Z-Score with mandatory epsilon protection against zero std."""
    rolling_mean = series.rolling(window=window, min_periods=max(5, window // 2)).mean()
    rolling_std = series.rolling(window=window, min_periods=max(5, window // 2)).std()
    z_score = (series - rolling_mean) / (rolling_std + EPSILON)
    return z_score.fillna(0.0)


def analyze_pillar_1_smc(df: pd.DataFrame, lookback: int = 80) -> Dict[str, Any]:
    """
    Pillar I (Sniper Smart Money Concepts):
    Detects Swing Highs/Lows, BOS, ChoCh, Institutional Liquidity Sweeps,
    and active Fair Value Gap (FVG) retests. Max 25 points.
    """
    sub = df.tail(lookback).copy()
    opens = sub["Open"].values
    highs = sub["High"].values
    lows = sub["Low"].values
    closes = sub["Close"].values
    n = len(sub)

    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []
    k = 3
    for i in range(k, n - k):
        if highs[i] == np.max(highs[i - k : i + k + 1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == np.min(lows[i - k : i + k + 1]):
            swing_lows.append((i, float(lows[i])))

    last_close = float(closes[-1])
    last_open = float(opens[-1])
    last_high = float(highs[-1])
    last_low = float(lows[-1])
    prev_close = float(closes[-2]) if n >= 2 else last_close

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {
            "bias": "NEUTRAL",
            "points": 8,
            "state": "SMC_CONSOLIDATION",
            "event": "NONE",
            "swing_high": float(np.max(highs)),
            "swing_low": float(np.min(lows)),
            "is_sweep": False,
        }

    sh_prev, sh_last = swing_highs[-2][1], swing_highs[-1][1]
    sl_prev, sl_last = swing_lows[-2][1], swing_lows[-1][1]

    bull_sweep = last_low < sl_last and last_close > sl_last and last_close > last_open
    bear_sweep = last_high > sh_last and last_close < sh_last and last_close < last_open

    in_bull_fvg = False
    in_bear_fvg = False
    for idx in range(max(2, n - 15), n - 1):
        if lows[idx] > highs[idx - 2]:
            if highs[idx - 2] <= last_low <= lows[idx] and last_close > last_open:
                in_bull_fvg = True
        if highs[idx] < lows[idx - 2]:
            if highs[idx] <= last_high <= lows[idx - 2] and last_close < last_open:
                in_bear_fvg = True

    higher_highs = sh_last > sh_prev
    higher_lows = sl_last > sl_prev
    lower_highs = sh_last < sh_prev
    lower_lows = sl_last < sl_prev

    bull_bos = last_close > sh_last and prev_close <= sh_last
    bear_bos = last_close < sl_last and prev_close >= sl_last
    bull_choch = lower_lows and last_close > sh_last
    bear_choch = higher_highs and last_close < sl_last

    base_meta = {"swing_high": sh_last, "swing_low": sl_last}

    if bull_sweep:
        return {**base_meta, "bias": "BULLISH", "points": 25, "state": "BULLISH_LIQ_SWEEP", "event": "SWEEP_UP", "is_sweep": True}
    if bear_sweep:
        return {**base_meta, "bias": "BEARISH", "points": 25, "state": "BEARISH_LIQ_SWEEP", "event": "SWEEP_DOWN", "is_sweep": True}
    if bull_choch:
        return {**base_meta, "bias": "BULLISH", "points": 24, "state": "BULLISH_CHOCH_REVERSAL", "event": "CHOCH_UP", "is_sweep": False}
    if bear_choch:
        return {**base_meta, "bias": "BEARISH", "points": 24, "state": "BEARISH_CHOCH_REVERSAL", "event": "CHOCH_DOWN", "is_sweep": False}
    if in_bull_fvg and (higher_lows or higher_highs):
        return {**base_meta, "bias": "BULLISH", "points": 23, "state": "BULLISH_FVG_RETEST", "event": "FVG_UP", "is_sweep": False}
    if in_bear_fvg and (lower_highs or lower_lows):
        return {**base_meta, "bias": "BEARISH", "points": 23, "state": "BEARISH_FVG_RETEST", "event": "FVG_DOWN", "is_sweep": False}
    if bull_bos or (higher_highs and higher_lows and last_close > sl_last):
        pts = 22 if bull_bos else 18
        ev = "BOS_UP" if bull_bos else "HH_HL_STRUCTURE"
        return {**base_meta, "bias": "BULLISH", "points": pts, "state": f"BULLISH_{ev}", "event": ev, "is_sweep": False}
    if bear_bos or (lower_highs and lower_lows and last_close < sh_last):
        pts = 22 if bear_bos else 18
        ev = "BOS_DOWN" if bear_bos else "LH_LL_STRUCTURE"
        return {**base_meta, "bias": "BEARISH", "points": pts, "state": f"BEARISH_{ev}", "event": ev, "is_sweep": False}

    if last_close > (sh_last + sl_last) / 2.0:
        return {**base_meta, "bias": "BULLISH", "points": 11, "state": "UPPER_RANGE_PREMIUM", "event": "RANGE_BULL", "is_sweep": False}
    return {**base_meta, "bias": "BEARISH", "points": 11, "state": "LOWER_RANGE_DISCOUNT", "event": "RANGE_BEAR", "is_sweep": False}


def analyze_pillar_2_order_flow(df: pd.DataFrame, window: int = 8) -> Dict[str, Any]:
    """Pillar II (Order Flow & Trigger Candle): Volume-weighted body-to-wick delta."""
    sub = df.tail(window).copy()
    body = sub["Close"] - sub["Open"]
    abs_body = body.abs()
    upper_wick = sub["High"] - sub[["Open", "Close"]].max(axis=1)
    lower_wick = sub[["Open", "Close"]].min(axis=1) - sub["Low"]
    total_range = (sub["High"] - sub["Low"]).clip(lower=EPSILON)

    vol = sub["EffectiveVolume"]
    vol_weights = vol / (vol.sum() + EPSILON)

    bar_pressure = (body + 0.5 * (lower_wick - upper_wick)) / (total_range + EPSILON)
    vw_pressure = float((bar_pressure * vol_weights).sum())
    body_efficiency = float(((abs_body / (total_range + EPSILON)) * vol_weights).sum())

    last_body = float(body.iloc[-1])
    last_range = float(total_range.iloc[-1])
    last_body_ratio = abs(last_body) / (last_range + EPSILON)
    last_dir = "BULLISH" if last_body > 0 else ("BEARISH" if last_body < 0 else "NEUTRAL")

    if vw_pressure >= MIN_ORDER_FLOW_CONFIRM:
        pts = min(20, int(13 + vw_pressure * 12 + body_efficiency * 4))
        if last_dir == "BULLISH" and last_body_ratio >= 0.40:
            pts = min(20, pts + 2)
        return {
            "bias": "BULLISH",
            "points": pts,
            "state": f"BULL_IMBALANCE ({vw_pressure:+.2f})",
            "vw_pressure": vw_pressure,
            "trigger_body_ratio": round(last_body_ratio, 2),
            "trigger_dir": last_dir,
        }
    if vw_pressure <= -MIN_ORDER_FLOW_CONFIRM:
        pts = min(20, int(13 + abs(vw_pressure) * 12 + body_efficiency * 4))
        if last_dir == "BEARISH" and last_body_ratio >= 0.40:
            pts = min(20, pts + 2)
        return {
            "bias": "BEARISH",
            "points": pts,
            "state": f"BEAR_IMBALANCE ({vw_pressure:+.2f})",
            "vw_pressure": vw_pressure,
            "trigger_body_ratio": round(last_body_ratio, 2),
            "trigger_dir": last_dir,
        }
    return {
        "bias": "NEUTRAL",
        "points": 6,
        "state": f"BALANCED_ABSORPTION ({vw_pressure:+.2f})",
        "vw_pressure": vw_pressure,
        "trigger_body_ratio": round(last_body_ratio, 2),
        "trigger_dir": last_dir,
    }


def analyze_pillar_3_zscore_sniper(
    df: pd.DataFrame,
    z_threshold: float,
    macro_bias: str,
    window: int = 20,
) -> Dict[str, Any]:
    """Pillar III (Sniper Z-Score Pullback & Anti-Chasing Engine). Max 20 points."""
    z_series = compute_rolling_zscore(df["Close"], window=window)
    z_now = float(z_series.iloc[-1])
    z_prev = float(z_series.iloc[-2]) if len(z_series) >= 2 else z_now

    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    atr14 = compute_atr(df, period=14)
    ema20_dist_atr = float((df["Close"].iloc[-1] - ema20.iloc[-1]) / (atr14.iloc[-1] + EPSILON))

    if z_now <= -z_threshold:
        pts = 20 if z_now > z_prev else 17
        return {
            "bias": "BULLISH",
            "points": pts,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "EXTREME_OVERSOLD_REVERSION",
        }
    if z_now >= z_threshold:
        pts = 20 if z_now < z_prev else 17
        return {
            "bias": "BEARISH",
            "points": pts,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "EXTREME_OVERBOUGHT_REVERSION",
        }

    if macro_bias == "BULLISH" and -z_threshold < z_now <= 0.65 and z_now >= z_prev:
        return {
            "bias": "BULLISH",
            "points": 18,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "OPTIMAL_BULL_PULLBACK",
        }
    if macro_bias == "BEARISH" and -0.65 <= z_now < z_threshold and z_now <= z_prev:
        return {
            "bias": "BEARISH",
            "points": 18,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "OPTIMAL_BEAR_PULLBACK",
        }

    if z_now < -0.65:
        return {
            "bias": "NEUTRAL",
            "points": 7,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "STRETCHED_LOW_NO_CHASE",
        }
    if z_now > 0.65:
        return {
            "bias": "NEUTRAL",
            "points": 7,
            "z_score": z_now,
            "ema20_dist_atr": round(ema20_dist_atr, 2),
            "state": "STRETCHED_HIGH_NO_CHASE",
        }

    return {
        "bias": macro_bias if macro_bias != "NEUTRAL" else "NEUTRAL",
        "points": 11,
        "z_score": z_now,
        "ema20_dist_atr": round(ema20_dist_atr, 2),
        "state": "EQUILIBRIUM_VALUE_ZONE",
    }


def analyze_pillar_4_mtf_harmony(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> Dict[str, Any]:
    """Pillar IV (Multi-Timeframe Fractal Harmony + ADX Trend Regime). Max 20 points."""
    ema20_1h = df_1h["Close"].ewm(span=20, adjust=False).mean()
    ema50_1h = df_1h["Close"].ewm(span=50, adjust=False).mean()
    ema20_15m = df_15m["Close"].ewm(span=20, adjust=False).mean()
    ema50_15m = df_15m["Close"].ewm(span=50, adjust=False).mean()

    adx_15m = float(compute_adx(df_15m, period=14).iloc[-1])
    adx_1h = float(compute_adx(df_1h, period=14).iloc[-1])

    macro_bull = float(ema20_1h.iloc[-1]) > float(ema50_1h.iloc[-1]) and float(df_1h["Close"].iloc[-1]) > float(ema20_1h.iloc[-1])
    macro_bear = float(ema20_1h.iloc[-1]) < float(ema50_1h.iloc[-1]) and float(df_1h["Close"].iloc[-1]) < float(ema20_1h.iloc[-1])

    exec_bull = float(ema20_15m.iloc[-1]) > float(ema50_15m.iloc[-1])
    exec_bear = float(ema20_15m.iloc[-1]) < float(ema50_15m.iloc[-1])

    smc_1h = analyze_pillar_1_smc(df_1h, lookback=60)

    if macro_bull and exec_bull and smc_1h["bias"] == "BULLISH":
        pts = 20 if adx_15m >= ADX_TREND_MIN else 15
        return {"bias": "BULLISH", "points": pts, "adx_15m": round(adx_15m, 2), "adx_1h": round(adx_1h, 2), "state": "1H_15M_FULL_BULL_HARMONY"}
    if macro_bear and exec_bear and smc_1h["bias"] == "BEARISH":
        pts = 20 if adx_15m >= ADX_TREND_MIN else 15
        return {"bias": "BEARISH", "points": pts, "adx_15m": round(adx_15m, 2), "adx_1h": round(adx_1h, 2), "state": "1H_15M_FULL_BEAR_HARMONY"}
    if (macro_bull and exec_bull) or (smc_1h["bias"] == "BULLISH" and exec_bull):
        return {"bias": "BULLISH", "points": 14, "adx_15m": round(adx_15m, 2), "adx_1h": round(adx_1h, 2), "state": "1H_15M_PARTIAL_BULL_ALIGN"}
    if (macro_bear and exec_bear) or (smc_1h["bias"] == "BEARISH" and exec_bear):
        return {"bias": "BEARISH", "points": 14, "adx_15m": round(adx_15m, 2), "adx_1h": round(adx_1h, 2), "state": "1H_15M_PARTIAL_BEAR_ALIGN"}

    return {"bias": "NEUTRAL", "points": 5, "adx_15m": round(adx_15m, 2), "adx_1h": round(adx_1h, 2), "state": "MTF_DIVERGENCE"}


def analyze_pillar_5_atr_risk(
    df_15m: pd.DataFrame,
    direction: str,
    optimal_atr_period: int,
    swing_high: float,
    swing_low: float,
    spot_quote: Optional[SpotQuote] = None,
    account_balance: float = DEFAULT_ACCOUNT_BALANCE,
    risk_percent: float = DEFAULT_RISK_PERCENT,
) -> Dict[str, Any]:
    """Pillar V (Hybrid Structural-ATR Risk & Dynamic Sizing)."""
    atr_14 = float(compute_atr(df_15m, period=14).iloc[-1])
    atr_opt = float(compute_atr(df_15m, period=optimal_atr_period).iloc[-1])
    atr_exec = (atr_14 + atr_opt) / 2.0

    close_last = float(df_15m["Close"].iloc[-1])
    if spot_quote is not None:
        entry = spot_quote.ask if direction == "BULLISH" else spot_quote.bid
    else:
        entry = close_last

    atr_series = compute_atr(df_15m, period=14)
    median_atr = float(atr_series.tail(100).median()) + EPSILON
    vol_ratio = atr_14 / median_atr

    min_sl_dist = max(1.2 * atr_exec, 1.50)
    max_sl_dist = max(2.2 * atr_exec, 3.00)

    if direction == "BEARISH":
        structural_dist = (swing_high + 0.35 * atr_exec) - entry
        risk_dist = min(max_sl_dist, max(min_sl_dist, structural_dist))
        sl = entry + risk_dist
        tp1 = entry - (1.5 * risk_dist)
        tp2 = entry - (2.5 * risk_dist)
        tp3 = entry - (4.0 * risk_dist)
    else:
        structural_dist = entry - (swing_low - 0.35 * atr_exec)
        risk_dist = min(max_sl_dist, max(min_sl_dist, structural_dist))
        sl = entry - risk_dist
        tp1 = entry + (1.5 * risk_dist)
        tp2 = entry + (2.5 * risk_dist)
        tp3 = entry + (4.0 * risk_dist)

    risk_usd = account_balance * (risk_percent / 100.0)
    raw_lots = risk_usd / ((risk_dist + EPSILON) * CONTRACT_OUNCES_PER_LOT)
    recommended_lot = round(min(MAX_LOT_SIZE, max(MIN_LOT_SIZE, raw_lots)), 2)

    if 0.6 <= vol_ratio <= 2.2:
        pts = 15
        vol_state = "OPTIMAL_ATR_REGIME"
    elif 0.35 <= vol_ratio <= 3.0:
        pts = 11
        vol_state = "MODERATE_ATR_REGIME"
    else:
        pts = 5
        vol_state = "EXTREME_VOLATILITY_GUARD"

    return {
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "atr_14": round(atr_14, 4),
        "atr_exec": round(atr_exec, 4),
        "risk_distance": round(risk_dist, 2),
        "recommended_lot": recommended_lot,
        "risk_amount_usd": round(risk_usd, 2),
        "points": pts,
        "vol_state": vol_state,
    }


# ==============================================================================
# SYNCHRONIZED GRID SEARCH OPTIMIZATION (ZERO TRAIN-SERVE SKEW)
# ==============================================================================
def _simulate_strategy_backtest(
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    atr_period: int,
    z_threshold: float,
    initial_capital: float = DEFAULT_ACCOUNT_BALANCE,
    risk_pct: float = DEFAULT_RISK_PERCENT,
) -> GridSearchResult:
    """Synchronized Sniper strategy backtest with multi-tier TP & BE trailing."""
    df = df_15m.copy()
    df["ATR"] = compute_atr(df, period=atr_period)
    df["ADX"] = compute_adx(df, period=14)
    df["Z"] = compute_rolling_zscore(df["Close"], window=20)
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    h1 = df_1h[["Close"]].copy()
    h1["H1_EMA20"] = h1["Close"].ewm(span=20, adjust=False).mean()
    h1["H1_EMA50"] = h1["Close"].ewm(span=50, adjust=False).mean()
    h1["H1_BIAS"] = np.where(
        (h1["H1_EMA20"] > h1["H1_EMA50"]) & (h1["Close"] > h1["H1_EMA20"]),
        1,
        np.where((h1["H1_EMA20"] < h1["H1_EMA50"]) & (h1["Close"] < h1["H1_EMA20"]), -1, 0),
    )
    df["H1_BIAS"] = h1[["H1_BIAS"]].shift(1).reindex(df.index, method="ffill").fillna(0)

    body = df["Close"] - df["Open"]
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    total_range = (df["High"] - df["Low"]).clip(lower=EPSILON)
    df["BAR_FLOW"] = (body + 0.5 * (lower_wick - upper_wick)) / total_range
    df["VW_FLOW"] = df["BAR_FLOW"].rolling(window=8, min_periods=3).mean().fillna(0.0)
    df["BODY_RATIO"] = body.abs() / total_range
    df["SWING_HIGH_12"] = df["High"].rolling(window=12, min_periods=5).max()
    df["SWING_LOW_12"] = df["Low"].rolling(window=12, min_periods=5).min()

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    equity_curve: List[float] = [initial_capital]
    timestamps: List[pd.Timestamp] = [df.index[0]]
    trade_log: List[Dict[str, Any]] = []

    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    total_r = 0.0

    in_pos = False
    pos_dir = 0
    entry_p = 0.0
    sl_p = 0.0
    tp1_p = 0.0
    tp2_p = 0.0
    tp3_p = 0.0
    risk_dist = 1.0
    pos_atr = 1.0
    remaining_weight = 1.0
    realized_r = 0.0
    tp1_hit = False
    tp2_hit = False
    risk_usd = 0.0
    entry_time: Optional[pd.Timestamp] = None

    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    atrs = df["ATR"].values
    adxs = df["ADX"].values
    zs = df["Z"].values
    ema20s = df["EMA20"].values
    ema50s = df["EMA50"].values
    h1_biases = df["H1_BIAS"].values
    vw_flows = df["VW_FLOW"].values
    body_ratios = df["BODY_RATIO"].values
    sh12 = df["SWING_HIGH_12"].values
    sl12 = df["SWING_LOW_12"].values
    idx_list = df.index

    start_bar = max(60, atr_period + 5)
    for i in range(start_bar, len(df)):
        if in_pos:
            trade_closed = False
            exit_p = closes[i]

            if pos_dir == 1:
                if lows[i] <= sl_p:
                    rem_r = (sl_p - entry_p) / (risk_dist + EPSILON)
                    realized_r += remaining_weight * rem_r
                    exit_p = sl_p
                    trade_closed = True
                else:
                    if not tp1_hit and highs[i] >= tp1_p:
                        tp1_hit = True
                        realized_r += 0.50 * 1.5
                        remaining_weight -= 0.50
                        sl_p = max(sl_p, entry_p)
                    if not tp2_hit and highs[i] >= tp2_p:
                        tp2_hit = True
                        realized_r += 0.25 * 2.5
                        remaining_weight -= 0.25
                        sl_p = max(sl_p, tp1_p)
                    if highs[i] >= tp3_p:
                        realized_r += remaining_weight * 4.0
                        exit_p = tp3_p
                        trade_closed = True
                    elif tp1_hit:
                        trail_step = highs[i] - (1.0 * pos_atr)
                        sl_p = max(sl_p, trail_step)
            else:
                if highs[i] >= sl_p:
                    rem_r = (entry_p - sl_p) / (risk_dist + EPSILON)
                    realized_r += remaining_weight * rem_r
                    exit_p = sl_p
                    trade_closed = True
                else:
                    if not tp1_hit and lows[i] <= tp1_p:
                        tp1_hit = True
                        realized_r += 0.50 * 1.5
                        remaining_weight -= 0.50
                        sl_p = min(sl_p, entry_p)
                    if not tp2_hit and lows[i] <= tp2_p:
                        tp2_hit = True
                        realized_r += 0.25 * 2.5
                        remaining_weight -= 0.25
                        sl_p = min(sl_p, tp1_p)
                    if lows[i] <= tp3_p:
                        realized_r += remaining_weight * 4.0
                        exit_p = tp3_p
                        trade_closed = True
                    elif tp1_hit:
                        trail_step = lows[i] + (1.0 * pos_atr)
                        sl_p = min(sl_p, trail_step)

            if trade_closed:
                pnl = risk_usd * realized_r
                equity += pnl
                total_r += realized_r
                if pnl >= 0:
                    gross_profit += pnl
                    wins += 1
                else:
                    gross_loss += abs(pnl)
                    losses += 1
                peak_equity = max(peak_equity, equity)
                dd_pct = ((peak_equity - equity) / (peak_equity + EPSILON)) * 100.0
                max_drawdown_pct = max(max_drawdown_pct, dd_pct)
                equity_curve.append(equity)
                timestamps.append(idx_list[i])
                trade_log.append(
                    {
                        "entry_time": str(entry_time),
                        "exit_time": str(idx_list[i]),
                        "direction": "BUY" if pos_dir == 1 else "SELL",
                        "entry": round(entry_p, 2),
                        "exit": round(exit_p, 2),
                        "r_multiple": round(realized_r, 2),
                        "pnl_usd": round(pnl, 2),
                    }
                )
                in_pos = False
                continue

        else:
            bar_dt = idx_list[i].to_pydatetime()
            if 21 <= bar_dt.hour < 23:
                continue

            z_val = zs[i]
            z_prev = zs[i - 1]
            c_val = closes[i]
            atr_val = max(atrs[i], 0.5)
            adx_val = adxs[i]
            ext_atr = (c_val - ema20s[i]) / (atr_val + EPSILON)

            bull_trend = ema20s[i] > ema50s[i] and h1_biases[i] >= 0 and adx_val >= ADX_TREND_MIN
            bear_trend = ema20s[i] < ema50s[i] and h1_biases[i] <= 0 and adx_val >= ADX_TREND_MIN

            bull_pullback = bull_trend and (-z_threshold < z_val <= 0.65) and z_val >= z_prev and ext_atr <= MAX_EXTENSION_ATR
            bull_sweep_rev = z_val <= -z_threshold and z_val > z_prev
            bull_signal = (
                (bull_pullback or bull_sweep_rev)
                and vw_flows[i] >= MIN_ORDER_FLOW_CONFIRM
                and c_val > opens[i]
                and body_ratios[i] >= 0.35
            )

            bear_pullback = bear_trend and (-0.65 <= z_val < z_threshold) and z_val <= z_prev and ext_atr >= -MAX_EXTENSION_ATR
            bear_sweep_rev = z_val >= z_threshold and z_val < z_prev
            bear_signal = (
                (bear_pullback or bear_sweep_rev)
                and vw_flows[i] <= -MIN_ORDER_FLOW_CONFIRM
                and c_val < opens[i]
                and body_ratios[i] >= 0.35
            )

            if bull_signal and not bear_signal:
                in_pos = True
                pos_dir = 1
                entry_p = c_val
                pos_atr = atr_val
                struct_dist = entry_p - (sl12[i] - 0.35 * pos_atr)
                risk_dist = min(max(2.2 * pos_atr, 3.00), max(max(1.2 * pos_atr, 1.50), struct_dist))
                sl_p = entry_p - risk_dist
                tp1_p = entry_p + 1.5 * risk_dist
                tp2_p = entry_p + 2.5 * risk_dist
                tp3_p = entry_p + 4.0 * risk_dist
                remaining_weight = 1.0
                realized_r = 0.0
                tp1_hit = False
                tp2_hit = False
                risk_usd = equity * (risk_pct / 100.0)
                entry_time = idx_list[i]
            elif bear_signal and not bull_signal:
                in_pos = True
                pos_dir = -1
                entry_p = c_val
                pos_atr = atr_val
                struct_dist = (sh12[i] + 0.35 * pos_atr) - entry_p
                risk_dist = min(max(2.2 * pos_atr, 3.00), max(max(1.2 * pos_atr, 1.50), struct_dist))
                sl_p = entry_p + risk_dist
                tp1_p = entry_p - 1.5 * risk_dist
                tp2_p = entry_p - 2.5 * risk_dist
                tp3_p = entry_p - 4.0 * risk_dist
                remaining_weight = 1.0
                realized_r = 0.0
                tp1_hit = False
                tp2_hit = False
                risk_usd = equity * (risk_pct / 100.0)
                entry_time = idx_list[i]

    total_trades = wins + losses
    win_rate = (wins / (total_trades + EPSILON)) * 100.0 if total_trades > 0 else 0.0
    expectancy_r = (total_r / (total_trades + EPSILON)) if total_trades > 0 else 0.0

    if total_trades == 0:
        profit_factor = 0.0
    elif gross_loss < 1e-6:
        profit_factor = min(9.99, gross_profit / (1.0 + EPSILON)) if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / (gross_loss + EPSILON)

    net_ret = ((equity - initial_capital) / (initial_capital + EPSILON)) * 100.0

    return GridSearchResult(
        optimal_atr_period=atr_period,
        optimal_z_threshold=z_threshold,
        win_rate=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        max_drawdown=round(max_drawdown_pct, 2),
        expectancy_r=round(expectancy_r, 2),
        total_trades=total_trades,
        net_return_pct=round(net_ret, 2),
        equity_curve=equity_curve,
        timestamps=timestamps,
        trade_log=trade_log,
    )


def run_grid_search_optimization(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> GridSearchResult:
    """Selects optimal (ATR period, Z-Threshold) yielding highest Profit Factor."""
    candidates: List[GridSearchResult] = []
    for atr_p in ATR_PERIOD_GRID:
        for z_thresh in Z_THRESHOLD_GRID:
            candidates.append(_simulate_strategy_backtest(df_15m, df_1h, atr_p, z_thresh))

    viable = [c for c in candidates if c.total_trades >= 4]
    pool = viable if viable else candidates

    best = max(
        pool,
        key=lambda c: (c.profit_factor, c.expectancy_r, c.win_rate, -c.max_drawdown),
    )
    logger.info(
        f"Grid Search Optimal Selected: ATR={best.optimal_atr_period}, "
        f"Z={best.optimal_z_threshold:.1f} | Trades={best.total_trades} | "
        f"WR={best.win_rate:.1f}% | PF={best.profit_factor:.2f} | "
        f"MDD={best.max_drawdown:.1f}% | Exp={best.expectancy_r:+.2f}R"
    )
    return best


# ==============================================================================
# MATPLOTLIB BACKTEST CHART GENERATION (HEADLESS AGG)
# ==============================================================================
def generate_backtest_chart(
    df_15m: pd.DataFrame,
    grid_res: GridSearchResult,
    chart_path: str = CHART_PATH,
) -> str:
    """Renders multi-panel equity & Z-Score chart safely on headless Linux runners."""
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7.8), gridspec_kw={"height_ratios": [2.2, 1.0]}, facecolor="#0B0F19"
    )

    ax1.set_facecolor("#111827")
    eq_times = pd.to_datetime(grid_res.timestamps)
    eq_vals = np.array(grid_res.equity_curve, dtype=float)
    hwm_vals = np.maximum.accumulate(eq_vals)

    ax1.plot(eq_times, eq_vals, color="#10B981", linewidth=2.2, label="Simulated Equity ($)")
    ax1.plot(eq_times, hwm_vals, color="#6EE7B7", linestyle=":", linewidth=1.1, alpha=0.7, label="High-Water Mark")
    ax1.fill_between(eq_times, eq_vals, hwm_vals, color="#EF4444", alpha=0.12, label="Drawdown Zone")
    ax1.axhline(DEFAULT_ACCOUNT_BALANCE, color="#6B7280", linestyle="--", linewidth=1.0, alpha=0.7)

    ax1.set_title(
        f"APEX-QUANT-OMNI {ENGINE_VERSION} | XAUUSD Sniper Grid-Optimized Performance\n"
        f"Optimal ATR={grid_res.optimal_atr_period} | Z-Thresh={grid_res.optimal_z_threshold:.1f} | "
        f"Trades={grid_res.total_trades} | Win Rate={grid_res.win_rate:.1f}% | "
        f"PF={grid_res.profit_factor:.2f} | Max DD={grid_res.max_drawdown:.1f}% | Exp={grid_res.expectancy_r:+.2f}R",
        color="#F9FAFB",
        fontsize=10.5,
        fontweight="bold",
        pad=12,
    )
    ax1.set_ylabel("Account Equity (USD)", color="#D1D5DB", fontsize=10)
    ax1.tick_params(colors="#9CA3AF", labelsize=9)
    ax1.grid(True, color="#1F2937", linestyle=":", linewidth=0.8)
    ax1.legend(loc="upper left", ncol=3, facecolor="#1F2937", edgecolor="#374151", labelcolor="#F9FAFB", fontsize=8)

    ax2.set_facecolor("#111827")
    z_tail = compute_rolling_zscore(df_15m["Close"], window=20).tail(250)
    ax2.plot(z_tail.index, z_tail.values, color="#38BDF8", linewidth=1.5, label="Rolling Z-Score (20)")
    ax2.axhline(
        grid_res.optimal_z_threshold,
        color="#EF4444",
        linestyle="--",
        linewidth=1.1,
        label=f"+Z Threshold ({grid_res.optimal_z_threshold:.1f})",
    )
    ax2.axhline(
        -grid_res.optimal_z_threshold,
        color="#10B981",
        linestyle="--",
        linewidth=1.1,
        label=f"-Z Threshold (-{grid_res.optimal_z_threshold:.1f})",
    )
    ax2.axhline(0.0, color="#6B7280", linestyle="-", linewidth=0.7, alpha=0.6)
    ax2.set_ylabel("Z-Score", color="#D1D5DB", fontsize=10)
    ax2.tick_params(colors="#9CA3AF", labelsize=9)
    ax2.grid(True, color="#1F2937", linestyle=":", linewidth=0.8)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax2.legend(loc="upper left", ncol=3, facecolor="#1F2937", edgecolor="#374151", labelcolor="#F9FAFB", fontsize=8)

    fig.autofmt_xdate()
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info(f"Saved backtest performance chart to [{chart_path}].")
    return chart_path


# ==============================================================================
# AUTO-TRAILING STOP ENGINE (LIVE SQLITE STATE MACHINE)
# ==============================================================================
def update_auto_trailing_stop_engine(
    current_price: float,
    current_atr: float,
    new_signal_decision: str,
    entry_price: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    db_path: str = DB_PATH,
) -> str:
    """Monitors open positions in SQLite; shifts SL to Breakeven at TP1 + ATR trailing."""
    init_sqlite_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    protocol_status = "Active @ TP1 Breakeven"

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, direction, entry_price, current_sl, tp1, tp2, tp3, atr_at_entry, tp1_hit, peak_favorable_price "
            "FROM active_positions WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()

        if row:
            pos_id, direction, pos_entry, curr_sl, pos_tp1, pos_tp2, pos_tp3, pos_atr, tp1_hit, peak_fav = row
            atr_step = max(0.5 * max(current_atr, pos_atr), 0.50)

            if direction == "BUY":
                peak_fav = max(peak_fav, current_price)
                if current_price <= curr_sl:
                    cur.execute(
                        "UPDATE active_positions SET status = 'CLOSED_SL', updated_utc = ? WHERE id = ?",
                        (now_iso, pos_id),
                    )
                    protocol_status = f"Prior BUY Closed @ SL {curr_sl:.2f} | Ready @ TP1 Breakeven"
                elif current_price >= pos_tp3:
                    cur.execute(
                        "UPDATE active_positions SET status = 'CLOSED_TP3', updated_utc = ? WHERE id = ?",
                        (now_iso, pos_id),
                    )
                    protocol_status = f"Prior BUY Hit TP3 {pos_tp3:.2f} (4.0R)"
                else:
                    if current_price >= pos_tp1 and tp1_hit == 0:
                        tp1_hit = 1
                        curr_sl = max(curr_sl, pos_entry)
                    if tp1_hit == 1:
                        new_trail = max(pos_entry, peak_fav - atr_step)
                        if new_trail > curr_sl:
                            curr_sl = round(new_trail, 2)
                        protocol_status = f"Breakeven+ATR Trailing Active (SL @ {curr_sl:.2f})"
                    cur.execute(
                        "UPDATE active_positions SET current_sl = ?, tp1_hit = ?, peak_favorable_price = ?, updated_utc = ? WHERE id = ?",
                        (curr_sl, tp1_hit, peak_fav, now_iso, pos_id),
                    )
            else:
                peak_fav = min(peak_fav, current_price)
                if current_price >= curr_sl:
                    cur.execute(
                        "UPDATE active_positions SET status = 'CLOSED_SL', updated_utc = ? WHERE id = ?",
                        (now_iso, pos_id),
                    )
                    protocol_status = f"Prior SELL Closed @ SL {curr_sl:.2f} | Ready @ TP1 Breakeven"
                elif current_price <= pos_tp3:
                    cur.execute(
                        "UPDATE active_positions SET status = 'CLOSED_TP3', updated_utc = ? WHERE id = ?",
                        (now_iso, pos_id),
                    )
                    protocol_status = f"Prior SELL Hit TP3 {pos_tp3:.2f} (4.0R)"
                else:
                    if current_price <= pos_tp1 and tp1_hit == 0:
                        tp1_hit = 1
                        curr_sl = min(curr_sl, pos_entry)
                    if tp1_hit == 1:
                        new_trail = min(pos_entry, peak_fav + atr_step)
                        if new_trail < curr_sl:
                            curr_sl = round(new_trail, 2)
                        protocol_status = f"Breakeven+ATR Trailing Active (SL @ {curr_sl:.2f})"
                    cur.execute(
                        "UPDATE active_positions SET current_sl = ?, tp1_hit = ?, peak_favorable_price = ?, updated_utc = ? WHERE id = ?",
                        (curr_sl, tp1_hit, peak_fav, now_iso, pos_id),
                    )

        if "VERIFIED" in new_signal_decision:
            new_dir = "BUY" if "BUY" in new_signal_decision else "SELL"
            cur.execute("SELECT id, direction FROM active_positions WHERE status = 'OPEN'")
            existing_open = cur.fetchall()
            if not any(r[1] == new_dir for r in existing_open):
                cur.execute(
                    """
                    INSERT INTO active_positions (
                        opened_utc, direction, entry_price, initial_sl, current_sl,
                        tp1, tp2, tp3, atr_at_entry, tp1_hit, peak_favorable_price,
                        status, updated_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'OPEN', ?)
                    """,
                    (
                        now_iso, new_dir, entry_price, sl, sl,
                        tp1, tp2, tp3, current_atr, entry_price, now_iso,
                    ),
                )
                protocol_status = "Active @ TP1 Breakeven"

        conn.commit()
    finally:
        conn.close()

    return protocol_status


# ==============================================================================
# MASTER SNIPER SIGNAL SYNTHESIS & INSTITUTIONAL WEBHOOK FORMATTING
# ==============================================================================
def run_full_quant_evaluation(command_listener_status: str = "Active / Ready for /check") -> SignalReport:
    """Executes the Sniper Precision 5-Pillar + ADX + Anti-Chasing + Grid Search pipeline."""
    df_15m, df_1h, data_source, spot_quote = fetch_xauusd_mtf_data()
    session_status = evaluate_market_session(spot_quote=spot_quote)
    news_status = evaluate_news_governor()

    grid_res = run_grid_search_optimization(df_15m, df_1h)
    generate_backtest_chart(df_15m, grid_res, CHART_PATH)

    p1 = analyze_pillar_1_smc(df_15m)
    p2 = analyze_pillar_2_order_flow(df_15m)
    p4 = analyze_pillar_4_mtf_harmony(df_15m, df_1h)
    p3 = analyze_pillar_3_zscore_sniper(
        df_15m, z_threshold=grid_res.optimal_z_threshold, macro_bias=p4["bias"]
    )

    bull_score = 0
    bear_score = 0
    for pillar in (p1, p2, p3, p4):
        if pillar["bias"] == "BULLISH":
            bull_score += pillar["points"]
        elif pillar["bias"] == "BEARISH":
            bear_score += pillar["points"]
        else:
            bull_score += pillar["points"] // 2
            bear_score += pillar["points"] // 2

    dominant_dir = "BULLISH" if bull_score >= bear_score else "BEARISH"
    p5 = analyze_pillar_5_atr_risk(
        df_15m=df_15m,
        direction=dominant_dir,
        optimal_atr_period=grid_res.optimal_atr_period,
        swing_high=p1["swing_high"],
        swing_low=p1["swing_low"],
        spot_quote=spot_quote,
    )

    total_bull = min(100, bull_score + (p5["points"] if dominant_dir == "BULLISH" else 0))
    total_bear = min(100, bear_score + (p5["points"] if dominant_dir == "BEARISH" else 0))
    confluence_score = max(total_bull, total_bear)

    required_score = (
        ASIAN_SESSION_MIN_SCORE
        if session_status.session_name == "ASIAN_SESSION_ACTIVE"
        else CONFLUENCE_THRESHOLD
    )

    adx_val = float(p4["adx_15m"])
    ext_atr = float(p3["ema20_dist_atr"])
    vw_flow = float(p2["vw_pressure"])
    trigger_dir = p2["trigger_dir"]
    is_sweep = bool(p1.get("is_sweep", False))

    sniper_blocks: List[str] = []
    raw_dir = "BUY" if (total_bull >= required_score and total_bull > total_bear + 8) else (
        "SELL" if (total_bear >= required_score and total_bear > total_bull + 8) else "NONE"
    )

    if raw_dir == "NONE":
        signal_decision = "NEUTRAL / HOLD"
        sniper_note = f"Confluence {confluence_score} below Req {required_score}"
    else:
        if raw_dir == "BUY" and vw_flow < MIN_ORDER_FLOW_CONFIRM:
            sniper_blocks.append(f"WeakBullFlow({vw_flow:+.2f})")
        if raw_dir == "SELL" and vw_flow > -MIN_ORDER_FLOW_CONFIRM:
            sniper_blocks.append(f"WeakBearFlow({vw_flow:+.2f})")

        if raw_dir == "BUY" and trigger_dir != "BULLISH":
            sniper_blocks.append("NoBullCandleClose")
        if raw_dir == "SELL" and trigger_dir != "BEARISH":
            sniper_blocks.append("NoBearCandleClose")

        if raw_dir == "BUY" and ext_atr > MAX_EXTENSION_ATR:
            sniper_blocks.append(f"OverextendedHigh(+{ext_atr:.2f}ATR)")
        if raw_dir == "SELL" and ext_atr < -MAX_EXTENSION_ATR:
            sniper_blocks.append(f"OverextendedLow({ext_atr:.2f}ATR)")

        if adx_val < ADX_TREND_MIN and not is_sweep:
            sniper_blocks.append(f"ChopRegime(ADX={adx_val:.1f} below {ADX_TREND_MIN})")

        if sniper_blocks:
            signal_decision = "NEUTRAL / HOLD"
            sniper_note = "Filtered: " + ", ".join(sniper_blocks)
        else:
            signal_decision = f"VERIFIED {raw_dir}"
            sniper_note = f"PASSED ALL GATES (ADX={adx_val:.1f}, Ext={ext_atr:+.2f}ATR, Flow={vw_flow:+.2f})"

    trailing_status = update_auto_trailing_stop_engine(
        current_price=p5["entry"],
        current_atr=p5["atr_exec"],
        new_signal_decision=signal_decision,
        entry_price=p5["entry"],
        sl=p5["sl"],
        tp1=p5["tp1"],
        tp2=p5["tp2"],
        tp3=p5["tp3"],
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    candle_ts = df_15m.index[-1].isoformat()

    return SignalReport(
        timestamp_utc=now_utc,
        candle_timestamp_utc=candle_ts,
        asset=ASSET_LABEL,
        data_source=data_source,
        spot_quote=spot_quote,
        session_status=session_status,
        news_status=news_status,
        signal_decision=signal_decision,
        entry_price=p5["entry"],
        stop_loss=p5["sl"],
        tp1=p5["tp1"],
        tp2=p5["tp2"],
        tp3=p5["tp3"],
        trailing_stop_protocol=trailing_status,
        confluence_score=int(confluence_score),
        required_score=int(required_score),
        z_score=round(p3["z_score"], 4),
        adx_value=round(adx_val, 2),
        smc_state=p1["state"],
        order_flow_state=p2["state"],
        mtf_harmony_state=p4["state"],
        sniper_filter_note=sniper_note,
        atr_value=p5["atr_exec"],
        optimal_atr_period=grid_res.optimal_atr_period,
        optimal_z_threshold=grid_res.optimal_z_threshold,
        recommended_lot=p5["recommended_lot"],
        risk_amount_usd=p5["risk_amount_usd"],
        win_rate=grid_res.win_rate,
        profit_factor=grid_res.profit_factor,
        max_drawdown=grid_res.max_drawdown,
        expectancy_r=grid_res.expectancy_r,
        command_listener_status=command_listener_status,
        webhook_status="Delivered via Exponential Backoff",
        pillar_breakdown={
            "P1_SMC": p1,
            "P2_OrderFlow": p2,
            "P3_ZScore": p3,
            "P4_MTFHarmony": p4,
            "P5_ATRRisk": p5,
        },
    )


def format_institutional_telegram_report(report: SignalReport) -> str:
    """Formats the HTML-escaped institutional forensic template for Telegram."""
    bbo_line = (
        f"• <i>Live Spot BBO:</i> Bid {report.spot_quote.bid:.2f} / "
        f"Ask {report.spot_quote.ask:.2f} (Spread ${report.spot_quote.spread:.2f})\n"
        if report.spot_quote
        else "• <i>Live Spot BBO:</i> Feed Close Sync\n"
    )
    return (
        f"🛡️ <b>APEX-QUANT-OMNI SOVEREIGN {_esc(ENGINE_VERSION)} (SNIPER PRECISION)</b> 🛡️\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 <b>Asset:</b> {_esc(report.asset)}\n"
        f"📊 <b>Signal Decision:</b> {_esc(report.signal_decision)}\n"
        f"🎯 <b>Precision Entry:</b> {report.entry_price:.2f}\n"
        f"🛑 <b>Forensic Stop Loss:</b> {report.stop_loss:.2f}\n"
        f"📈 <b>Take Profit 1 (1.5R):</b> {report.tp1:.2f}\n"
        f"📈 <b>Take Profit 2 (2.5R):</b> {report.tp2:.2f}\n"
        f"🚀 <b>Take Profit 3 (4.0R):</b> {report.tp3:.2f}\n"
        f"🔄 <b>Trailing Stop Protocol:</b> {_esc(report.trailing_stop_protocol)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Interactive 2-Way &amp; Macro Guards:</b>\n"
        f"• <i>Command Listener:</i> {_esc(report.command_listener_status)}\n"
        f"• <i>Multi-Source Feed:</i> {_esc(report.data_source)}\n"
        f"{bbo_line}"
        f"• <i>Session Governor:</i> {_esc(report.session_status.session_name)} "
        f"({'OPEN' if report.session_status.is_allowed else 'SUPPRESSED'})\n"
        f"• <i>USD News Governor:</i> {_esc(report.news_status.guard_state)} — {_esc(report.news_status.reason)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>Sniper Engine &amp; Grid Search:</b>\n"
        f"• <i>Sniper Gate:</i> {_esc(report.sniper_filter_note)}\n"
        f"• <i>Optimal ATR / Z-Thresh:</i> {report.optimal_atr_period} / {report.optimal_z_threshold:.1f}\n"
        f"• <i>ADX / Z-Score:</i> {report.adx_value:.1f} / {report.z_score:+.2f}\n"
        f"• <i>SMC / Order Flow:</i> {_esc(report.smc_state)} | {_esc(report.order_flow_state)}\n"
        f"• <i>MTF Harmony:</i> {_esc(report.mtf_harmony_state)}\n"
        f"• <i>Webhook Status:</i> {_esc(report.webhook_status)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Risk &amp; Position Sizing:</b>\n"
        f"• <i>Recommended Lot:</i> {report.recommended_lot:.2f} Lots ({DEFAULT_RISK_PERCENT:.1f}% Risk)\n"
        f"• <i>Risk Amount (USD):</i> ${report.risk_amount_usd:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔬 <b>Forensic Audit, DB Artifact &amp; Backtest:</b>\n"
        f"• <i>Confluence Score:</i> {report.confluence_score}/100 (Req: {report.required_score})\n"
        f"• <i>SQLite Artifact:</i> Saved &amp; Uploaded\n"
        f"• <i>Optimized Win Rate:</i> {report.win_rate:.1f}%\n"
        f"• <i>Profit Factor:</i> {report.profit_factor:.2f} (Exp: {report.expectancy_r:+.2f}R)\n"
        f"• <i>Max Drawdown:</i> {report.max_drawdown:.1f}%\n"
        f"• <i>Timestamp (UTC):</i> {_esc(report.timestamp_utc)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )


# ==============================================================================
# INTERACTIVE 2-WAY TELEGRAM COMMAND HANDLER (/check, /news, /status, /chart, /audit, /help)
# ==============================================================================
def _build_news_message() -> str:
    news_status = evaluate_news_governor()
    lines = [
        f"📅 <b>APEX-QUANT-OMNI {_esc(ENGINE_VERSION)} — USD HIGH-IMPACT NEWS CALENDAR</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Status Governor:</b> {_esc(news_status.guard_state)}",
        f"• <b>Keterangan:</b> {_esc(news_status.reason)}",
        f"• <b>Blackout Window:</b> -{NEWS_BLACKOUT_BEFORE_MIN}m s/d +{NEWS_BLACKOUT_AFTER_MIN}m",
        f"• <b>Sumber Feed:</b> {_esc(news_status.source)}",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not news_status.upcoming_events:
        lines.append("Tidak ada rilis USD High-Impact dalam 48 jam ke depan.")
    else:
        lines.append("<b>Jadwal USD High-Impact (48 Jam Ke Depan):</b>")
        for ev in news_status.upcoming_events:
            dt_utc = datetime.fromisoformat(ev.event_utc)
            dt_wib = dt_utc.astimezone(WIB_TZ)
            tag = "🔥" if ev.is_tier1_catalyst else "⚠️"
            lines.append(
                f"{tag} <b>{_esc(ev.title)}</b>\n"
                f"   🕒 <code>{dt_utc.strftime('%d %b %H:%M UTC')}</code> | "
                f"<code>{dt_wib.strftime('%H:%M WIB')}</code>\n"
                f"   📊 Forecast: <code>{_esc(ev.forecast)}</code> | Prev: <code>{_esc(ev.previous)}</code>"
            )
    return "\n".join(lines)


def _build_status_message() -> str:
    spot_quote = fetch_swissquote_live_spot_quote()
    session = evaluate_market_session(spot_quote=spot_quote)
    news_status = evaluate_news_governor()
    init_sqlite_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM signals_audit")
        total_rows = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM active_positions WHERE status = 'OPEN'")
        open_pos = cur.fetchone()[0]
        cur.execute(
            "SELECT timestamp_utc, signal_decision, entry_price, confluence_score "
            "FROM signals_audit ORDER BY id DESC LIMIT 1"
        )
        last_row = cur.fetchone()
    finally:
        conn.close()

    last_summary = (
        f"{last_row[1]} @ {last_row[2]:.2f} ({last_row[3]}/100) [{last_row[0]}]"
        if last_row
        else "No signals logged yet"
    )
    bbo_txt = (
        f"{spot_quote.bid:.2f} / {spot_quote.ask:.2f} (Spread ${spot_quote.spread:.2f})"
        if spot_quote
        else "Unavailable"
    )
    return (
        f"🩺 <b>APEX-QUANT-OMNI {_esc(ENGINE_VERSION)} — SYSTEM HEALTH STATUS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Engine State:</b> ONLINE &amp; OPERATIONAL\n"
        f"• <b>Live Spot XAU/USD:</b> {_esc(bbo_txt)}\n"
        f"• <b>Session Governor:</b> {_esc(session.session_name)} "
        f"({'ACTIVE' if session.is_allowed else 'SUPPRESSED'})\n"
        f"• <b>USD News Governor:</b> {_esc(news_status.guard_state)} — {_esc(news_status.reason)}\n"
        f"• <b>SQLite Audit Records:</b> {total_rows} (Open Trailing Pos: {open_pos})\n"
        f"• <b>Latest Audit Entry:</b> {_esc(last_summary)}\n"
        f"• <b>UTC Clock:</b> {_esc(session.utc_timestamp)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Commands: <code>/check</code> | <code>/news</code> | <code>/status</code> | <code>/chart</code> | <code>/audit</code> | <code>/help</code>"
    )


def _build_audit_history_message() -> str:
    init_sqlite_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT timestamp_utc, signal_decision, entry_price, confluence_score, z_score "
            "FROM signals_audit ORDER BY id DESC LIMIT 5"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "📂 <b>SQLite Forensic Audit:</b> No historical entries recorded yet."

    lines = [f"🔬 <b>APEX-QUANT-OMNI {_esc(ENGINE_VERSION)} — LAST 5 FORENSIC AUDITS</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
    for ts, dec, entry, score, z in rows:
        lines.append(f"• <code>{_esc(ts[:19])}Z</code> | <b>{_esc(dec)}</b> @ {entry:.2f} | {score}/100 | Z={z:+.2f}")
    return "\n".join(lines)


def process_telegram_commands(poll_timeout: int = 0) -> int:
    """
    Polls Telegram `getUpdates` via POST JSON (with 409 conflict recovery)
    and deduplicates stacked identical commands within the same polling batch.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.info("TELEGRAM_BOT_TOKEN missing; skipping 2-way command polling.")
        return 0

    last_offset_str = get_bot_state("telegram_update_offset", "0")
    offset = int(last_offset_str) if last_offset_str.isdigit() else 0

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    payload: Dict[str, Any] = {
        "offset": offset,
        "timeout": poll_timeout,
        "allowed_updates": ["message"],
    }
    resp = request_with_exponential_backoff(
        "POST", url, max_retries=2, backoff_delays=(2, 4), timeout=poll_timeout + 10, json=payload
    )

    if resp is not None and resp.status_code == 409:
        logger.warning("Telegram 409 Conflict detected (stale webhook active). Clearing webhook...")
        del_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
        request_with_exponential_backoff("POST", del_url, max_retries=1, json={"drop_pending_updates": False})
        resp = request_with_exponential_backoff(
            "POST", url, max_retries=2, backoff_delays=(2, 4), timeout=poll_timeout + 10, json=payload
        )

    if resp is None or resp.status_code != 200:
        status_code = resp.status_code if resp is not None else "None"
        logger.warning(f"Telegram getUpdates failed: status={status_code}")
        return 0

    try:
        updates = resp.json().get("result", [])
    except (ValueError, AttributeError):
        return 0

    if updates:
        logger.info(f"Telegram 2-Way Listener fetched {len(updates)} pending update(s).")

    # Collect authorized unique commands in batch (prevents 3x redundant /check evaluations)
    queued_commands: List[Tuple[str, str]] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for upd in updates:
        upd_id = int(upd.get("update_id", 0))
        if upd_id >= offset:
            offset = upd_id + 1
            set_bot_state("telegram_update_offset", str(offset))

        msg = upd.get("message") or {}
        text = str(msg.get("text", "")).strip()
        chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
        if not text or not chat_id:
            continue

        if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
            logger.warning(
                f"Ignored unauthorized Telegram command from chat_id={chat_id} "
                f"(expected={TELEGRAM_CHAT_ID})"
            )
            continue

        raw_token = text.split()[0].split("@")[0].lower()
        cmd = raw_token if raw_token.startswith("/") else f"/{raw_token}"
        if cmd in ("/check", "/news", "/status", "/audit", "/chart", "/help", "/start"):
            pair = (chat_id, cmd)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                queued_commands.append(pair)

    processed = 0
    for chat_id, cmd in queued_commands:
        logger.info(f"Processing 2-Way Telegram command [{cmd}] from chat_id={chat_id}")

        if cmd == "/check":
            send_telegram_message("⏳ <i>Executing Sniper XAUUSD 5-Pillar + ADX + News + Grid Search scan...</i>", chat_id=chat_id)
            report = run_full_quant_evaluation(command_listener_status="Active (On-Demand /check Executed)")
            report.webhook_status = "On-Demand /check Reply (Exponential Backoff)"
            forensic_audit_log(report)
            send_telegram_message(format_institutional_telegram_report(report), chat_id=chat_id)
            if os.path.exists(CHART_PATH):
                send_telegram_photo(CHART_PATH, caption="📊 Live Sniper Grid-Optimized Backtest Curve", chat_id=chat_id)
            processed += 1

        elif cmd == "/news":
            send_telegram_message(_build_news_message(), chat_id=chat_id)
            processed += 1

        elif cmd == "/status":
            send_telegram_message(_build_status_message(), chat_id=chat_id)
            processed += 1

        elif cmd == "/audit":
            send_telegram_message(_build_audit_history_message(), chat_id=chat_id)
            processed += 1

        elif cmd == "/chart":
            if not os.path.exists(CHART_PATH):
                df_15m, df_1h, _, _ = fetch_xauusd_mtf_data()
                grid_res = run_grid_search_optimization(df_15m, df_1h)
                generate_backtest_chart(df_15m, grid_res, CHART_PATH)
            send_telegram_photo(CHART_PATH, caption="📈 APEX-QUANT-OMNI Sniper Backtest", chat_id=chat_id)
            processed += 1

        elif cmd in ("/help", "/start"):
            help_msg = (
                f"🤖 <b>APEX-QUANT-OMNI {_esc(ENGINE_VERSION)} Interactive Commands</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <code>/check</code> — Live Sniper XAUUSD Signal, ADX, News &amp; Confluence\n"
                f"• <code>/news</code> — Jadwal Berita USD High-Impact (CPI/NFP/FOMC) UTC &amp; WIB\n"
                f"• <code>/status</code> — System Health, Live Spread &amp; Governors\n"
                f"• <code>/chart</code> — Latest Grid-Optimized Equity Curve Chart\n"
                f"• <code>/audit</code> — Last 5 SQLite Forensic Audit Records\n"
                f"• <code>/help</code> — Show this command menu"
            )
            send_telegram_message(help_msg, chat_id=chat_id)
            processed += 1

    return processed


# ==============================================================================
# EXECUTION ORCHESTRATION (ONESHOT SERVERLESS & CONTINUOUS 2-WAY POLLING)
# ==============================================================================
def execute_scheduled_cycle(force_notify: bool = False) -> None:
    """Runs one complete serverless cycle with Session/Spread/News/Sniper gates."""
    init_sqlite_db(DB_PATH)
    process_telegram_commands(poll_timeout=0)

    report = run_full_quant_evaluation(command_listener_status="Active / Ready for /check")
    session = report.session_status
    news = report.news_status

    last_notified_candle = get_bot_state("last_notified_candle_utc", "")
    is_new_candle = report.candle_timestamp_utc != last_notified_candle
    is_verified = "VERIFIED" in report.signal_decision

    if (not session.is_allowed or not news.is_allowed) and not force_notify:
        reasons = []
        if not session.is_allowed:
            reasons.append(f"Session:{session.session_name}")
        if not news.is_allowed:
            reasons.append(f"News:{news.guard_state}")
        report.webhook_status = f"Suppressed ({' & '.join(reasons)})"
        forensic_audit_log(report)
        logger.info(f"Automated alert suppressed: {report.webhook_status}")
        return

    if (is_verified and is_new_candle) or force_notify:
        tg_ok = send_telegram_message(format_institutional_telegram_report(report))
        if tg_ok and os.path.exists(CHART_PATH):
            send_telegram_photo(CHART_PATH, caption=f"📊 {_esc(report.asset)} | {_esc(report.signal_decision)}")
        dc_ok = send_discord_webhook(report)

        if tg_ok or dc_ok:
            report.webhook_status = "Delivered via Exponential Backoff"
            set_bot_state("last_notified_candle_utc", report.candle_timestamp_utc)
        else:
            report.webhook_status = "Logged Locally (No Webhook Target Configured/Reached)"
    else:
        report.webhook_status = "Standby (Neutral Confluence or Already Notified)"

    forensic_audit_log(report)


def run_continuous_polling_loop(scan_interval_sec: int = 900) -> None:
    """Runs persistent 2-way Telegram listener with periodic 15-minute scans."""
    logger.info(f"Starting continuous 2-Way Telegram Listener + {scan_interval_sec}s scan loop...")
    last_scan_ts = 0.0
    while True:
        try:
            process_telegram_commands(poll_timeout=15)
            now_ts = time.time()
            if now_ts - last_scan_ts >= scan_interval_sec:
                execute_scheduled_cycle(force_notify=False)
                last_scan_ts = now_ts
        except KeyboardInterrupt:
            logger.info("Graceful shutdown requested.")
            break
        except Exception as exc:
