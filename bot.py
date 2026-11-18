#!/usr/bin/env python3
"""
APEX-QUANT-OMNI SOVEREIGN (v75.9)
Institutional-Grade Spot Gold (XAUUSD) Quantitative Signal & Execution Engine
Weighted SniperGate: SMC 35% | Order Flow 30% | Z-Score 15% | ATR/Vol 10% | MTF 10%
Real-Time Economic Calendar Scraper + Hard Blackout + Clean Telegram Cards
"""

import os
import re
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
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("APEX-QUANT-OMNI")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)

BOT_VERSION = "v75.9"
EPSILON = 1e-8
DB_PATH = os.getenv("SQLITE_DB_PATH", "trading_signals.db")
CHART_PATH = os.getenv("BACKTEST_CHART_PATH", "backtest_performance.png")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
BASE_CONFLUENCE_THRESHOLD = int(os.getenv("CONFLUENCE_THRESHOLD", "68"))
MAX_ALLOWED_SPREAD_USD = float(os.getenv("MAX_ALLOWED_SPREAD_USD", "3.00"))
FORCE_NOTIFY = os.getenv("FORCE_NOTIFY", "false").lower() in ("1", "true", "yes")

NEWS_BLACKOUT_BEFORE_MIN = int(os.getenv("NEWS_BLACKOUT_BEFORE_MIN", "45"))
NEWS_BLACKOUT_AFTER_MIN = int(os.getenv("NEWS_BLACKOUT_AFTER_MIN", "30"))
NEWS_CACHE_TTL_MINUTES = int(os.getenv("NEWS_CACHE_TTL_MINUTES", "15"))

MAX_EXTENSION_ATR = 1.80
MIN_ORDERFLOW_CONFIRM = 0.12
MIN_PRIORITY_PILLAR_NORM = 60.0
WIB_TZ = timezone(timedelta(hours=7))
NY_TZ = ZoneInfo("America/New_York")

# SniperGate Confluence Weights (Exact Sum = 1.00 / 100%)
PILLAR_WEIGHTS = {
    "smc": 0.35,         # Market Structure (SMC BOS/Sweep/FVG): 35%
    "order_flow": 0.30,  # Order Flow Momentum & Delta: 30%
    "zscore": 0.15,      # Z-Score Pullback & Anti-Chasing: 15%
    "atr_vol": 0.10,     # ATR/Volatility Expansion & Trigger: 10%
    "mtf": 0.10,         # Multi-Timeframe 1H/15M Harmony: 10%
}
assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 1e-9, "PILLAR_WEIGHTS must sum to 1.00"

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
    if m >= 60:
        return f"{m // 60}j {m % 60}m"
    return f"{m}m"


# ==============================================================================
# SQLITE PERSISTENCE LAYER
# ==============================================================================
def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
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
            smc_state TEXT,
            mtf_state TEXT,
            news_status TEXT,
            source TEXT
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
            payload_json TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


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
            lot_size, spread_usd, adx, z_score, order_flow, smc_state,
            mtf_state, news_status, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record["timestamp_utc"], record["decision"], record["score"],
        record["entry_price"], record["sl"], record["tp1"], record["tp2"],
        record["tp3"], record["lot_size"], record["spread_usd"], record["adx"],
        record["z_score"], record["order_flow"], record["smc_state"],
        record["mtf_state"], record["news_status"], record["source"],
    ))
    conn.commit()


# ==============================================================================
# REAL-TIME ECONOMIC CALENDAR SCRAPER & HARD BLACKOUT GOVERNOR
# ==============================================================================
def _parse_ff_time_to_utc(date_str: str, time_str: str, year_hint: int) -> datetime | None:
    t_clean = time_str.strip().lower()
    if not t_clean or any(tok in t_clean for tok in ("all day", "tentative", "day")):
        return None
    d_clean = re.sub(r"\s+", " ", date_str.strip())
    d_parts = d_clean.split(" ")
    if len(d_parts) >= 3 and len(d_parts[0]) <= 4:
        d_clean = f"{d_parts[1]} {d_parts[2]}"
    try:
        dt_naive = datetime.strptime(f"{year_hint} {d_clean} {t_clean}", "%Y %b %d %I:%M%p")
        return dt_naive.replace(tzinfo=NY_TZ).astimezone(timezone.utc)
    except Exception:
        return None


def scrape_forexfactory_html(now_utc: datetime) -> list[dict]:
    url = "https://www.forexfactory.com/calendar?week=this"
    headers = {**BROWSER_HEADERS, "Referer": "https://www.forexfactory.com/"}
    resp = requests.get(url, headers=headers, timeout=12)
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

        dt_utc = _parse_ff_time_to_utc(current_date_str, current_time_str, now_utc.year)
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


def scrape_investing_html(now_utc: datetime) -> list[dict]:
    url = "https://www.investing.com/economic-calendar/"
    headers = {**BROWSER_HEADERS, "Referer": "https://www.investing.com/"}
    resp = requests.get(url, headers=headers, timeout=12)
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


def fetch_forexfactory_json_feed() -> list[dict]:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    resp = requests.get(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=12)
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
            "SELECT fetched_at_utc, source, payload_json FROM news_cache WHERE id = 1"
        ).fetchone()
        if row:
            try:
                fetched_at = datetime.fromisoformat(row[0])
                age_min = (now_utc - fetched_at).total_seconds() / 60.0
                if age_min <= NEWS_CACHE_TTL_MINUTES:
                    return json.loads(row[2]), f"{row[1]} (Cached {age_min:.0f}m)"
            except Exception:
                pass

    scraped_events: list[dict] = []
    source_parts: list[str] = []

    try:
        ff_html = scrape_forexfactory_html(now_utc)
        if ff_html:
            scraped_events.extend(ff_html)
            source_parts.append(f"ForexFactory HTML ({len(ff_html)})")
            logger.info(f"Real-Time News Scrape succeeded [ForexFactory HTML]: {len(ff_html)} USD High-Impact event(s)")
    except Exception as exc:
        logger.info(f"ForexFactory HTML scrape skipped/blocked ({exc}); checking Investing.com...")

    if not scraped_events:
        try:
            inv_html = scrape_investing_html(now_utc)
            if inv_html:
                scraped_events.extend(inv_html)
                source_parts.append(f"Investing.com HTML ({len(inv_html)})")
                logger.info(f"Real-Time News Scrape succeeded [Investing.com HTML]: {len(inv_html)} USD High-Impact event(s)")
        except Exception as exc:
            logger.info(f"Investing.com HTML scrape skipped/blocked ({exc}); failing over to ForexFactory JSON...")

    if not scraped_events:
        try:
            ff_json = fetch_forexfactory_json_feed()
            scraped_events.extend(ff_json)
            source_parts.append(f"ForexFactory JSON ({len(ff_json)})")
            logger.info(f"News Calendar Failover succeeded [ForexFactory JSON]: {len(ff_json)} USD High-Impact event(s)")
        except Exception as exc:
            logger.warning(f"ForexFactory JSON feed failed ({exc}); using Deterministic NFP Guard.")

    nfp_events = generate_deterministic_nfp_guard(now_utc)
    if not source_parts:
        source_parts.append("Deterministic NFP Guard")

    all_events = _deduplicate_events(scraped_events + nfp_events)
    source_label = " + ".join(source_parts)

    conn.execute(
        """
        INSERT INTO news_cache (id, fetched_at_utc, source, payload_json)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            fetched_at_utc = excluded.fetched_at_utc,
            source = excluded.source,
            payload_json = excluded.payload_json
        """,
        (now_utc.isoformat(), source_label, json.dumps(all_events)),
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

        if -NEWS_BLACKOUT_AFTER_MIN <= delta_min <= NEWS_BLACKOUT_BEFORE_MIN:
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
# SPOT GOLD MULTI-SOURCE DATA INGESTION & SWISSQUOTE LIVE BBO
# ==============================================================================
def fetch_swissquote_bbo() -> dict | None:
    url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
    try:
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        for entry in resp.json():
            for p in entry.get("spreadProfilePrices", []):
                bid = float(p.get("bid", 0.0))
                ask = float(p.get("ask", 0.0))
                if bid > 500.0 and ask >= bid:
                    spread = round(ask - bid, 2)
                    mid = round((bid + ask) / 2.0, 2)
                    logger.info(
                        f"Live Spot BBO Verified [Swissquote Institutional BBO]: "
                        f"Bid={bid:.2f} | Ask={ask:.2f} | Spread=${spread:.2f}"
                    )
                    return {"bid": bid, "ask": ask, "mid": mid, "spread": spread, "source": "Swissquote Institutional BBO"}
    except Exception as exc:
        logger.warning(f"Swissquote BBO fetch failed: {exc}")
    return None


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
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
    return df[~df.index.duplicated(keep="last")]


def _fetch_yfinance_spot(interval: str, period: str) -> pd.DataFrame:
    df = yf.download("XAUUSD=X", interval=interval, period=period, progress=False, threads=False)
    return _clean_ohlcv(df)


def _fetch_yahoo_v8_http(interval: str, range_str: str) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X?interval={interval}&range={range_str}"
    for attempt in range(2):
        resp = requests.get(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=8)
        if resp.status_code == 429:
            if attempt == 0:
                logger.warning(f"HTTP 429 on {url.split('?')[0]}. Retrying in 2s (attempt 1/1)...")
                time.sleep(2)
                continue
            logger.warning(f"HTTP 429 on {url.split('?')[0]}: Too Many Requests")
            return pd.DataFrame()
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
        return _clean_ohlcv(df)
    return pd.DataFrame()


def _fetch_binance_paxg(interval: str, limit: int) -> pd.DataFrame:
    resp = requests.get(
        "https://data-api.binance.vision/api/v3/klines",
        params={"symbol": "PAXGUSDT", "interval": interval, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json(), columns=[
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


def _calibrate_spot_proxy(df: pd.DataFrame, bbo: dict | None) -> pd.DataFrame:
    if df.empty or not bbo:
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


def ingest_spot_gold_data() -> tuple[pd.DataFrame, pd.DataFrame, dict | None, str]:
    bbo = fetch_swissquote_bbo()

    df15 = _fetch_yfinance_spot("15m", "15d")
    if len(df15) >= 200:
        df1h = _fetch_yfinance_spot("1h", "60d")
        if len(df1h) >= 100:
            src = "Nominal (Primary: yfinance XAUUSD=X)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    logger.info("Feed [Nominal (Primary: yfinance XAUUSD=X)] unavailable or rate-limited; advancing failover...")

    df15 = _fetch_yahoo_v8_http("15m", "15d")
    if len(df15) >= 200:
        df1h = _fetch_yahoo_v8_http("1h", "60d")
        if len(df1h) >= 100:
            src = "Failover Active (Secondary: Yahoo v8 HTTP XAUUSD=X)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    logger.info("Feed [Failover Active (Secondary: Yahoo v8 HTTP XAUUSD=X)] unavailable or rate-limited; advancing failover...")

    try:
        df15 = _calibrate_spot_proxy(_fetch_binance_paxg("15m", 1000), bbo)
        df1h = _calibrate_spot_proxy(_fetch_binance_paxg("1h", 750), bbo)
        if len(df15) >= 200 and len(df1h) >= 100:
            src = "Failover Active (Tertiary: Spot PAXG/XAUUSD + Swissquote BBO)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"Tertiary Binance Vision feed failed: {exc}")

    try:
        df15 = _calibrate_spot_proxy(_fetch_kraken_paxg(15), bbo)
        df1h = _calibrate_spot_proxy(_fetch_kraken_paxg(60), bbo)
        if len(df15) >= 150 and len(df1h) >= 80:
            src = "Failover Active (Quaternary: Kraken PAXGUSD + Swissquote BBO)"
            logger.info(f"Data ingestion succeeded via [{src}] | 15M bars={len(df15)} | 1H bars={len(df1h)}")
            return df15, df1h, bbo, src
    except Exception as exc:
        logger.warning(f"Quaternary Kraken feed failed: {exc}")

    raise RuntimeError("CRITICAL: All Spot Gold data sources exhausted.")


# ==============================================================================
# TECHNICAL INDICATORS & WEIGHTED 5-PILLAR HARMONIC CONFLUENCE ENGINE
# ==============================================================================
def compute_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    high, low, close = out["High"], out["Low"], out["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["ATR"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean().clip(lower=EPSILON)
    out["ATR_MA20"] = out["ATR"].rolling(20).mean().bfill().clip(lower=EPSILON)
    out["ATR_RATIO"] = (out["ATR"] / out["ATR_MA20"]).fillna(1.0)

    # Wilder ADX(14)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr14 = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean().clip(lower=EPSILON)
    plus_di = 100.0 * pd.Series(plus_dm, index=out.index).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr14
    minus_di = 100.0 * pd.Series(minus_dm, index=out.index).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr14
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + EPSILON)
    out["ADX"] = dx.ewm(alpha=1.0 / 14.0, adjust=False).mean()

    # EMAs & Z-Score
    out["EMA20"] = close.ewm(span=20, adjust=False).mean()
    out["EMA50"] = close.ewm(span=50, adjust=False).mean()
    out["EMA200"] = close.ewm(span=200, adjust=False).mean()
    std20 = close.rolling(20).std().clip(lower=EPSILON)
    out["ZSCORE"] = (close - out["EMA20"]) / std20

    # Volume Order Flow & 2-Bar Order Flow Momentum Acceleration
    bar_range = (high - low).clip(lower=EPSILON)
    clv = ((close - low) - (high - close)) / bar_range
    vol_safe = out["Volume"].replace(0, np.nan).ffill().fillna(1.0).clip(lower=EPSILON)
    vol_ma = vol_safe.rolling(20).mean().clip(lower=EPSILON)
    rel_vol = (vol_safe / vol_ma).clip(upper=3.0)
    out["ORDER_FLOW"] = (clv * rel_vol).rolling(3).mean().clip(-1.0, 1.0).fillna(0.0)
    out["OF_MOMENTUM"] = (out["ORDER_FLOW"] - out["ORDER_FLOW"].shift(2)).fillna(0.0)
    return out


def _evaluate_smc_pillar(df: pd.DataFrame, idx: int) -> tuple[float, str, str, float, float]:
    """
    Pillar 1 (Normalized 0-100, Weight 35%):
    SMC Liquidity Sweep, Break of Structure (BOS), Fair Value Gap (FVG), & Swing Structure.
    """
    w = df.iloc[max(0, idx - 35): idx + 1]
    if len(w) < 15:
        return 35.0, "NEUTRAL_RANGE", "NONE", float(df["Low"].iloc[idx]), float(df["High"].iloc[idx])

    c = float(w["Close"].iloc[-1])
    h_prev20 = float(w["High"].iloc[-21:-1].max())
    l_prev20 = float(w["Low"].iloc[-21:-1].min())
    recent_swing_low = float(w["Low"].iloc[-10:].min())
    recent_swing_high = float(w["High"].iloc[-10:].max())

    bull_fvg = float(w["Low"].iloc[-1]) > float(w["High"].iloc[-3])
    bear_fvg = float(w["High"].iloc[-1]) < float(w["Low"].iloc[-3])
    bull_sweep = float(w["Low"].iloc[-2]) < l_prev20 and float(w["Close"].iloc[-1]) > l_prev20
    bear_sweep = float(w["High"].iloc[-2]) > h_prev20 and float(w["Close"].iloc[-1]) < h_prev20

    if bull_sweep:
        return (100.0 if bull_fvg else 95.0), "BULLISH_LIQUIDITY_SWEEP", "BUY", recent_swing_low, recent_swing_high
    if bear_sweep:
        return (100.0 if bear_fvg else 95.0), "BEARISH_LIQUIDITY_SWEEP", "SELL", recent_swing_low, recent_swing_high
    if c > h_prev20:
        return (95.0 if bull_fvg else 84.0), "BULLISH_BOS_UP", "BUY", recent_swing_low, recent_swing_high
    if c < l_prev20:
        return (95.0 if bear_fvg else 84.0), "BEARISH_BOS_DOWN", "SELL", recent_swing_low, recent_swing_high

    hh = float(w["High"].iloc[-6:].max()) > float(w["High"].iloc[-14:-6].max())
    hl = float(w["Low"].iloc[-6:].min()) > float(w["Low"].iloc[-14:-6].min())
    lh = float(w["High"].iloc[-6:].max()) < float(w["High"].iloc[-14:-6].max())
    ll = float(w["Low"].iloc[-6:].min()) < float(w["Low"].iloc[-14:-6].min())

    if hh and hl:
        return (78.0 if bull_fvg else 66.0), "BULLISH_HH_HL_STRUCTURE", "BUY", recent_swing_low, recent_swing_high
    if lh and ll:
        return (78.0 if bear_fvg else 66.0), "BEARISH_LH_LL_STRUCTURE", "SELL", recent_swing_low, recent_swing_high
    return 35.0, "CONSOLIDATION_RANGE", "NONE", recent_swing_low, recent_swing_high


def score_bar_confluence(
    df15: pd.DataFrame,
    df1h: pd.DataFrame,
    idx15: int,
    z_threshold: float,
) -> dict:
    """
    Weighted SniperGate Confluence Engine (Sum = 100%):
    - SMC (Market Structure):  35% (0.35)
    - Order Flow Momentum:     30% (0.30)
    - Z-Score Pullback:        15% (0.15)
    - ATR/Volatility Regime:   10% (0.10)
    - MTF Harmony (1H/15M):    10% (0.10)
    """
    row = df15.iloc[idx15]
    ts = df15.index[idx15]
    h1_slice = df1h[df1h.index <= ts]
    h1_row = h1_slice.iloc[-1] if not h1_slice.empty else row

    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    atr = max(float(row["ATR"]), EPSILON)
    atr_ratio = float(row["ATR_RATIO"])
    adx = float(row["ADX"])
    z = float(row["ZSCORE"])
    of = float(row["ORDER_FLOW"])
    of_mom = float(row["OF_MOMENTUM"])

    # 1. SMC (Market Structure) - Weight 35%
    smc_norm, smc_state, smc_dir, swing_low, swing_high = _evaluate_smc_pillar(df15, idx15)

    # 2. MTF Harmony (1H/15M) - Weight 10%
    h1_bull = float(h1_row["EMA20"]) > float(h1_row["EMA50"]) and float(h1_row["Close"]) > float(h1_row["EMA50"])
    h1_bear = float(h1_row["EMA20"]) < float(h1_row["EMA50"]) and float(h1_row["Close"]) < float(h1_row["EMA50"])
    m15_bull = ema20 > ema50
    m15_bear = ema20 < ema50

    if h1_bull and m15_bull:
        bias = "BUY"
        mtf_norm = 100.0 if adx >= 20.0 else 80.0
        mtf_state = "1H_15M_FULL_BULL_HARMONY"
    elif h1_bear and m15_bear:
        bias = "SELL"
        mtf_norm = 100.0 if adx >= 20.0 else 80.0
        mtf_state = "1H_15M_FULL_BEAR_HARMONY"
    elif h1_bull and smc_dir == "BUY":
        bias = "BUY"
        mtf_norm = 65.0
        mtf_state = "1H_BULL_PULLBACK_ALIGN"
    elif h1_bear and smc_dir == "SELL":
        bias = "SELL"
        mtf_norm = 65.0
        mtf_state = "1H_BEAR_PULLBACK_ALIGN"
    else:
        bias = smc_dir if smc_dir in ("BUY", "SELL") else ("BUY" if z <= -z_threshold else "SELL")
        mtf_norm = 25.0
        mtf_state = "MTF_DIVERGENCE"

    if smc_dir != "NONE" and smc_dir != bias:
        smc_norm = max(15.0, smc_norm - 55.0)

    # 3. Order Flow Momentum & Delta - Weight 30%
    of_aligned = of if bias == "BUY" else -of
    mom_accel = (of_mom >= 0.02) if bias == "BUY" else (of_mom <= -0.02)
    if of_aligned >= 0.35:
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

    # 4. Z-Score Pullback & Anti-Chasing - Weight 15%
    ext_atr = (close - ema20) / atr
    if bias == "BUY":
        if -1.0 <= z <= 0.8 and abs(ext_atr) <= MAX_EXTENSION_ATR:
            z_norm = 100.0
        elif -z_threshold <= z < -1.0:
            z_norm = 72.0
        elif ext_atr > MAX_EXTENSION_ATR:
            z_norm = 10.0
        else:
            z_norm = 45.0
    else:
        if -0.8 <= z <= 1.0 and abs(ext_atr) <= MAX_EXTENSION_ATR:
            z_norm = 100.0
        elif 1.0 < z <= z_threshold:
            z_norm = 72.0
        elif ext_atr < -MAX_EXTENSION_ATR:
            z_norm = 10.0
        else:
            z_norm = 45.0

    # 5. ATR/Volatility Expansion & Trigger Candle - Weight 10%
    candle_bull = float(row["Close"]) > float(row["Open"])
    candle_bear = float(row["Close"]) < float(row["Open"])
    trigger_ok = (bias == "BUY" and candle_bull) or (bias == "SELL" and candle_bear)
    if 0.80 <= atr_ratio <= 1.80 and adx >= 18.0 and trigger_ok:
        atr_vol_norm = 100.0
    elif 0.65 <= atr_ratio <= 2.10 and trigger_ok:
        atr_vol_norm = 80.0
    elif 0.65 <= atr_ratio <= 2.10:
        atr_vol_norm = 55.0
    else:
        atr_vol_norm = 25.0

    weighted_score = (
        smc_norm * PILLAR_WEIGHTS["smc"]
        + of_norm * PILLAR_WEIGHTS["order_flow"]
        + z_norm * PILLAR_WEIGHTS["zscore"]
        + atr_vol_norm * PILLAR_WEIGHTS["atr_vol"]
        + mtf_norm * PILLAR_WEIGHTS["mtf"]
    )
    total_score = int(round(min(100.0, max(0.0, weighted_score))))

    anti_chase_ok = (ext_atr <= MAX_EXTENSION_ATR) if bias == "BUY" else (ext_atr >= -MAX_EXTENSION_ATR)
    of_ok = of_aligned >= MIN_ORDERFLOW_CONFIRM
    priority_pillars_ok = (smc_norm >= MIN_PRIORITY_PILLAR_NORM) and (of_norm >= MIN_PRIORITY_PILLAR_NORM)

    weighted_breakdown = (
        f"SMC={smc_norm:.0f}(w35) OF={of_norm:.0f}(w30) Z={z_norm:.0f}(w15) "
        f"ATRVol={atr_vol_norm:.0f}(w10) MTF={mtf_norm:.0f}(w10)"
    )

    return {
        "bias": bias,
        "score": total_score,
        "smc_state": smc_state,
        "mtf_state": mtf_state,
        "of_label": of_label,
        "order_flow": of,
        "of_momentum": of_mom,
        "z_score": z,
        "adx": adx,
        "atr": atr,
        "atr_ratio": atr_ratio,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "trigger_ok": trigger_ok,
        "anti_chase_ok": anti_chase_ok,
        "of_ok": of_ok,
        "smc_norm": smc_norm,
        "of_norm": of_norm,
        "z_norm": z_norm,
        "atr_vol_norm": atr_vol_norm,
        "mtf_norm": mtf_norm,
        "priority_pillars_ok": priority_pillars_ok,
        "weighted_breakdown": weighted_breakdown,
        "ext_atr": ext_atr,
    }


# ==============================================================================
# SYNCHRONIZED GRID SEARCH BACKTESTER & PERFORMANCE CHART
# ==============================================================================
def run_synchronized_backtest(
    df15_raw: pd.DataFrame, df1h_raw: pd.DataFrame, atr_period: int, z_threshold: float
) -> dict:
    df15 = compute_indicators(df15_raw, atr_period=atr_period)
    df1h = compute_indicators(df1h_raw, atr_period=atr_period)
    trades_r = []
    equity = [100.0]

    i = 210
    n = len(df15)
    while i < n - 12:
        sig = score_bar_confluence(df15, df1h, i, z_threshold)
        if (
            sig["score"] >= BASE_CONFLUENCE_THRESHOLD
            and sig["priority_pillars_ok"]
            and sig["adx"] >= 18.0
            and sig["anti_chase_ok"]
            and sig["of_ok"]
            and sig["trigger_ok"]
        ):
            entry = float(df15["Close"].iloc[i])
            atr = sig["atr"]
            risk_dist = max(1.5 * atr, 3.0)
            sl = entry - risk_dist if sig["bias"] == "BUY" else entry + risk_dist
            tp1 = entry + 1.5 * risk_dist if sig["bias"] == "BUY" else entry - 1.5 * risk_dist
            tp2 = entry + 2.5 * risk_dist if sig["bias"] == "BUY" else entry - 2.5 * risk_dist

            tp1_hit = False
            trade_r = 0.0
            cur_sl = sl
            exit_idx = min(n - 1, i + 24)

            for j in range(i + 1, min(n, i + 25)):
                hi = float(df15["High"].iloc[j])
                lo = float(df15["Low"].iloc[j])
                if sig["bias"] == "BUY":
                    if lo <= cur_sl:
                        trade_r = 0.5 * 1.5 if tp1_hit else -1.0
                        exit_idx = j
                        break
                    if not tp1_hit and hi >= tp1:
                        tp1_hit = True
                        cur_sl = entry
                    if hi >= tp2:
                        trade_r = 0.5 * 1.5 + 0.5 * 2.5
                        exit_idx = j
                        break
                else:
                    if hi >= cur_sl:
                        trade_r = 0.5 * 1.5 if tp1_hit else -1.0
                        exit_idx = j
                        break
                    if not tp1_hit and lo <= tp1:
                        tp1_hit = True
                        cur_sl = entry
                    if lo <= tp2:
                        trade_r = 0.5 * 1.5 + 0.5 * 2.5
                        exit_idx = j
                        break
            else:
                final_c = float(df15["Close"].iloc[exit_idx])
                raw_r = ((final_c - entry) if sig["bias"] == "BUY" else (entry - final_c)) / risk_dist
                trade_r = (0.5 * 1.5 + 0.5 * raw_r) if tp1_hit else raw_r

            trades_r.append(trade_r)
            equity.append(equity[-1] * (1.0 + 0.01 * trade_r))
            i = exit_idx + 1
        else:
            i += 1

    if not trades_r:
        return {
            "atr": atr_period, "z": z_threshold, "trades": 0,
            "wr": 0.0, "pf": 1.0, "mdd": 0.0, "exp": 0.0, "equity": equity,
        }

    wins = [r for r in trades_r if r > 0]
    losses = [abs(r) for r in trades_r if r < 0]
    wr = 100.0 * len(wins) / len(trades_r)
    pf = sum(wins) / max(sum(losses), EPSILON)
    exp = float(np.mean(trades_r))

    eq_arr = np.array(equity)
    peaks = np.maximum.accumulate(eq_arr)
    dd = (peaks - eq_arr) / np.maximum(peaks, EPSILON)
    mdd = float(dd.max() * 100.0)

    return {
        "atr": atr_period, "z": z_threshold, "trades": len(trades_r),
        "wr": round(wr, 1), "pf": round(pf, 2), "mdd": round(mdd, 1),
        "exp": round(exp, 2), "equity": equity,
    }


def grid_search_optimize(df15_raw: pd.DataFrame, df1h_raw: pd.DataFrame) -> dict:
    best = None
    best_obj = -999.0
    for atr_p in (10, 14, 20):
        for z_th in (1.5, 2.0, 2.5):
            res = run_synchronized_backtest(df15_raw, df1h_raw, atr_p, z_th)
            obj = (res["exp"] * 10.0) + res["pf"] - (res["mdd"] * 0.25)
            if res["trades"] < 3:
                obj -= 5.0
            if obj > best_obj:
                best_obj = obj
                best = res
    logger.info(
        f"Grid Search Optimal Selected: ATR={best['atr']}, Z={best['z']} | "
        f"Trades={best['trades']} | WR={best['wr']}% | PF={best['pf']:.2f} | "
        f"MDD={best['mdd']}% | Exp={best['exp']:+.2f}R"
    )
    return best


def save_backtest_chart(best_bt: dict, chart_path: str = CHART_PATH) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")
    eq = best_bt.get("equity", [100.0])
    ax.plot(range(len(eq)), eq, color="#f0b90b", linewidth=2.0, label="Equity Curve (%)")
    ax.axhline(100.0, color="#8b949e", linestyle="--", linewidth=0.9)
    ax.set_title(
        f"APEX-QUANT-OMNI {BOT_VERSION} | ATR={best_bt['atr']} Z={best_bt['z']} | "
        f"WR={best_bt['wr']}% | PF={best_bt['pf']} | Exp={best_bt['exp']:+.2f}R",
        color="#e6edf3", fontsize=10, pad=10,
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
# SESSION GOVERNOR & RISK SIZING
# ==============================================================================
def evaluate_session_governor(now_utc: datetime, spread_usd: float) -> tuple[bool, str, str, int]:
    wd = now_utc.weekday()
    hh = now_utc.hour

    if (wd == 4 and hh >= 22) or wd == 5 or (wd == 6 and hh < 22):
        return False, "WEEKEND_MARKET_CLOSED", "WEEKEND_CLOSED", 99
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
# WEBHOOK DELIVERY (EXPONENTIAL BACKOFF + HTML SAFE + PLAIN-TEXT FALLBACK)
# ==============================================================================
def _post_with_backoff(url: str, json_payload: dict | None = None, files=None, data=None) -> requests.Response | None:
    delays = (2, 4, 8)
    last_resp = None
    for idx, delay in enumerate(delays):
        try:
            resp = requests.post(url, json=json_payload, files=files, data=data, timeout=15)
            last_resp = resp
            if 200 <= resp.status_code < 300:
                return resp
            if resp.status_code == 400:
                return resp
            logger.warning(f"Webhook HTTP {resp.status_code} ({resp.text[:120]}); retrying in {delay}s...")
        except Exception as exc:
            logger.warning(f"Webhook request error: {exc}; retrying in {delay}s...")
        if idx < len(delays) - 1:
            time.sleep(delay)
    return last_resp


def send_telegram_message(text_html: str, chat_id: str | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = _post_with_backoff(url, json_payload={
        "chat_id": target, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": True,
    })
    if resp is not None and 200 <= resp.status_code < 300:
        return True
    if resp is not None and resp.status_code == 400:
        logger.warning(f"Telegram HTML rejected ({resp.text[:140]}); falling back to plain text...")
        plain = re.sub(r"<[^>]+>", "", text_html)
        resp2 = _post_with_backoff(url, json_payload={
            "chat_id": target, "text": plain, "disable_web_page_preview": True,
        })
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
        resp = _post_with_backoff(url, files={"photo": fh}, data={"chat_id": target, "caption": caption[:1000]})
    return resp is not None and 200 <= resp.status_code < 300


def send_discord_alert(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")
        return
    clean = re.sub(r"<[^>]+>", "", message)
    _post_with_backoff(DISCORD_WEBHOOK_URL, json_payload={"content": clean[:1950]})


# ==============================================================================
# CLEAN TELEGRAM CARD FORMATTER
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
    sniper_note: str,
    session_name: str,
    news_gov: dict,
    best_bt: dict,
) -> str:
    if news_gov["upcoming"]:
        nxt = news_gov["upcoming"][0]
        news_short = f"CLEAR ({_esc(nxt['title'])} {_format_minutes_human(nxt['delta_min'])})"
    else:
        news_short = "CLEAR"

    if news_gov["hard_blackout"]:
        ev = news_gov["active_event"]
        resume_str = format_utc_wib(news_gov["resume_utc"]) if news_gov["resume_utc"] else "-"
        return (
            f"⛔ <b>XAUUSD HARD BLACKOUT</b>\n"
            f"<b>Berita:</b> {_esc(ev['title'])}\n"
            f"<b>Spot:</b> <code>{bid:.2f} / {ask:.2f}</code> (Spr <code>${spread_usd:.2f}</code>)\n"
            f"<b>Status:</b> Sinyal BUY/SELL ditangguhkan (anti-slippage)\n"
            f"<b>Lanjut Est:</b> <code>{_esc(resume_str)}</code>"
        )

    if decision.startswith("VERIFIED"):
        icon = "🟢" if bias == "BUY" else "🔴"
        return (
            f"{icon} <b>{_esc(decision)} — XAUUSD</b> | Score <code>{sig['score']}/100</code>\n"
            f"<b>Entry:</b> <code>{entry:.2f}</code> | <b>SL:</b> <code>{sl:.2f}</code>\n"
            f"<b>TP1:</b> <code>{tp1:.2f}</code> | <b>TP2:</b> <code>{tp2:.2f}</code> | <b>TP3:</b> <code>{tp3:.2f}</code>\n"
            f"<b>Risk:</b> Lot <code>{lot_out:.2f}</code> (<code>${risk_out:.0f}</code>) | Trailing: <code>TP1 BE</code> | Spr <code>${spread_usd:.2f}</code>\n"
            f"<b>News:</b> {_esc(news_short)} | <b>BT:</b> WR <code>{best_bt['wr']}%</code> PF <code>{best_bt['pf']}</code>"
        )

    return (
        f"🟡 <b>XAUUSD HOLD</b> | Score <code>{sig['score']}/100</code> (Req&gt;={req_score})\n"
        f"<b>Spot:</b> <code>{bid:.2f} / {ask:.2f}</code> (Spr <code>${spread_usd:.2f}</code>)\n"
        f"<b>Bias ({_esc(bias)}):</b> {_esc(sniper_note)}\n"
        f"<b>Sesi:</b> <code>{_esc(session_name)}</code>\n"
        f"<b>News:</b> {_esc(news_short)}\n"
        f"<b>BT:</b> WR <code>{best_bt['wr']}%</code> | PF <code>{best_bt['pf']}</code> | Exp <code>{best_bt['exp']:+.2f}R</code>"
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

    # 1. Real-Time Economic Calendar Scrape & Hard Blackout Evaluation
    events, news_src = fetch_realtime_usd_high_impact_calendar(conn, force_refresh=force_news_refresh)
    news_gov = evaluate_news_governor(events, now_utc, news_src)

    # 2. Multi-Source Spot Gold Ingestion & Calibration
    df15_raw, df1h_raw, bbo, data_src = ingest_spot_gold_data()
    bid = bbo["bid"] if bbo else float(df15_raw["Close"].iloc[-1])
    ask = bbo["ask"] if bbo else bid
    spread_usd = bbo["spread"] if bbo else 0.50

    # 3. Synchronized Grid Search & Chart Generation
    best_bt = grid_search_optimize(df15_raw, df1h_raw)
    save_backtest_chart(best_bt, CHART_PATH)

    df15 = compute_indicators(df15_raw, atr_period=best_bt["atr"])
    df1h = compute_indicators(df1h_raw, atr_period=best_bt["atr"])
    sig = score_bar_confluence(df15, df1h, len(df15) - 1, best_bt["z"])

    # 4. Hybrid Structural-ATR Risk Levels
    bias = sig["bias"]
    entry = ask if bias == "BUY" else bid
    atr = sig["atr"]
    struct_buf = max(1.5 * atr, abs(entry - (sig["swing_low"] if bias == "BUY" else sig["swing_high"])) + 0.5 * atr)
    sl_dist = min(max(struct_buf, 1.5 * atr), 2.5 * atr)

    if bias == "BUY":
        sl = round(entry - sl_dist, 2)
        tp1 = round(entry + 1.5 * sl_dist, 2)
        tp2 = round(entry + 2.5 * sl_dist, 2)
        tp3 = round(entry + 4.0 * sl_dist, 2)
    else:
        sl = round(entry + sl_dist, 2)
        tp1 = round(entry - 1.5 * sl_dist, 2)
        tp2 = round(entry - 2.5 * sl_dist, 2)
        tp3 = round(entry - 4.0 * sl_dist, 2)

    risk_usd = round(ACCOUNT_BALANCE * (RISK_PERCENT / 100.0), 2)
    lot_size = round(max(0.01, min(5.0, risk_usd / max(sl_dist * 100.0, EPSILON))), 2)

    # 5. Dual Governors + Weighted SniperGate
    session_ok, session_reason, session_name, req_score = evaluate_session_governor(now_utc, spread_usd)

    if news_gov["hard_blackout"]:
        decision = "HARD BLACKOUT / NEWS HOLD"
        sniper_note = f"Signal suspended ({news_gov['status_code']}) to avoid slippage"
        lot_out, risk_out = 0.0, 0.0
    elif not session_ok:
        decision = "SESSION HOLD"
        sniper_note = session_reason
        lot_out, risk_out = 0.0, 0.0
    elif sig["smc_norm"] < MIN_PRIORITY_PILLAR_NORM:
        decision = "NEUTRAL / HOLD"
        sniper_note = f"SMC Structure lemah ({sig['smc_norm']:.0f}/{MIN_PRIORITY_PILLAR_NORM:.0f})"
        lot_out, risk_out = lot_size, risk_usd
    elif sig["of_norm"] < MIN_PRIORITY_PILLAR_NORM or not sig["of_ok"]:
        decision = "NEUTRAL / HOLD"
        sniper_note = f"OrderFlow belum terkonfirmasi ({sig['order_flow']:+.2f})"
        lot_out, risk_out = lot_size, risk_usd
    elif sig["score"] < req_score:
        decision = "NEUTRAL / HOLD"
        sniper_note = f"Skor {sig['score']} belum mencapai Req {req_score}"
        lot_out, risk_out = lot_size, risk_usd
    elif sig["adx"] < 18.0:
        decision = "NEUTRAL / HOLD"
        sniper_note = f"ADX lemah ({sig['adx']:.1f} below 18.0)"
        lot_out, risk_out = lot_size, risk_usd
    elif not sig["anti_chase_ok"]:
        decision = "NEUTRAL / HOLD"
        sniper_note = f"Harga overextended ({sig['ext_atr']:+.2f} ATR)"
        lot_out, risk_out = lot_size, risk_usd
    elif not sig["trigger_ok"]:
        decision = "NEUTRAL / HOLD"
        sniper_note = "Menunggu konfirmasi candle pemicu"
        lot_out, risk_out = lot_size, risk_usd
    else:
        decision = f"VERIFIED {bias}"
        sniper_note = "ALL_WEIGHTED_SNIPER_GATES_PASSED"
        lot_out, risk_out = lot_size, risk_usd

    # 6. Build Clean Telegram Card
    msg_html = build_telegram_signal_card(
        decision=decision, bias=bias, sig=sig, req_score=req_score,
        bid=bid, ask=ask, spread_usd=spread_usd,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
        lot_out=lot_out, risk_out=risk_out,
        sniper_note=sniper_note, session_name=session_name,
        news_gov=news_gov, best_bt=best_bt,
    )

    # 7. Notification Routing (Avoid Duplicate Send When /check Already Replied)
    is_verified_op = decision.startswith("VERIFIED")
    should_send = (
        notify_mode == "command"
        or is_verified_op
        or news_gov["hard_blackout"]
        or (FORCE_NOTIFY and not suppress_hold_notify)
    )

    webhook_status = "Standby (Hold Threshold)"
    if should_send:
        ok_tg = send_telegram_message(msg_html, chat_id=reply_chat_id)
        if notify_mode == "command":
            webhook_status = "On-Demand /check Reply (Exponential Backoff)" if ok_tg else "Reply Failed"
        else:
            send_discord_alert(msg_html)
            webhook_status = "Delivered via Exponential Backoff" if ok_tg else "Telegram Delivery Failed"
    elif suppress_hold_notify and FORCE_NOTIFY:
        webhook_status = "Skipped Duplicate (Already Replied via /check)"
        if not DISCORD_WEBHOOK_URL:
            logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")
    elif not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")

    if notify_mode != "command" and not DISCORD_WEBHOOK_URL and should_send:
        logger.info("DISCORD_WEBHOOK_URL not configured; skipping Discord delivery.")

    # 8. Full Forensic Audit Log (GitHub Actions Console + SQLite)
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
        "smc_state": sig["smc_state"],
        "mtf_state": sig["mtf_state"],
        "news_status": news_gov["status_code"],
        "source": data_src,
    })

    logger.info("========== FORENSIC AUDIT LOG ==========")
    logger.info(f"Asset=XAUUSD (SPOT GOLD) | Decision={decision} | Score={sig['score']}/100 (Req>={req_score}) | Source={data_src}")
    logger.info(f"WeightedPillars: {sig['weighted_breakdown']}")
    logger.info(f"Entry={entry:.2f} | SL={sl:.2f} | TP1={tp1:.2f} | TP2={tp2:.2f} | TP3={tp3:.2f} | Bid={bid:.2f} Ask={ask:.2f} Spread=${spread_usd:.2f}")
    logger.info(f"Z-Score={sig['z_score']:.4f} | ADX={sig['adx']:.2f} | ATR={atr:.4f} (Ratio={sig['atr_ratio']:.2f}) | SMC={sig['smc_state']} | OrderFlow={sig['of_label']} | MTF={sig['mtf_state']}")
    logger.info(f"SniperGate={sniper_note} | NewsGovernor={news_gov['status_code']} | NewsSrc={news_gov['source']} | Session={session_name}")
    logger.info(f"GridSearch(ATR={best_bt['atr']}, Z={best_bt['z']}) | WR={best_bt['wr']}% | PF={best_bt['pf']:.2f} | MDD={best_bt['mdd']}% | Exp={best_bt['exp']:+.2f}R")
    logger.info(f"Lot={lot_out:.2f} | RiskUSD=${risk_out:.2f} | Trailing=Active @ TP1 Breakeven | Webhook={webhook_status}")
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
    if not rows:
        return "📜 Belum ada riwayat audit tersimpan."
    lines = ["📜 <b>5 AUDIT TERAKHIR</b>"]
    for ts, dec, sc, ent, spr in rows:
        lines.append(f"• <code>{_esc(ts[11:16])}Z</code> | <b>{_esc(dec)}</b> ({sc}) @ <code>{ent:.2f}</code> (${spr:.2f})")
    return "\n".join(lines)


def process_telegram_commands(conn: sqlite3.Connection, poll_timeout: int = 0) -> bool:
    """Processes pending Telegram commands. Returns True if /check already ran and replied."""
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
    max_uid = offset
    seen_batch = set()
    handled_check = False

    for upd in updates:
        uid = int(upd.get("update_id", 0))
        if uid >= max_uid:
            max_uid = uid + 1
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
        if cmd == "/check":
            run_analysis_cycle(conn, notify_mode="command", reply_chat_id=chat_id, force_news_refresh=True)
            handled_check = True
        elif cmd == "/news":
            send_telegram_message(_build_news_reply(conn, force_refresh=True), chat_id=chat_id)
        elif cmd == "/audit":
            send_telegram_message(_build_audit_reply(conn), chat_id=chat_id)
        elif cmd == "/chart":
            if os.path.exists(CHART_PATH):
                send_telegram_photo(CHART_PATH, f"APEX-QUANT-OMNI {BOT_VERSION} Backtest Equity", chat_id=chat_id)
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
                f"🤖 <b>APEX-QUANT-OMNI {BOT_VERSION}</b>\n"
                "/check — Analisa cepat XAUUSD + news\n"
                "/news — Jadwal High-Impact USD\n"
                "/status — Sesi &amp; spread live\n"
                "/chart — Grafik backtest\n"
                "/audit — 5 sinyal terakhir"
            )
            send_telegram_message(help_msg, chat_id=chat_id)

    set_state(conn, "tg_update_offset", str(max_uid))
    return handled_check


def run_continuous_polling_loop(conn: sqlite3.Connection) -> None:
    logger.info(f"Starting APEX-QUANT-OMNI {BOT_VERSION} Continuous Polling Mode...")
    last_bar_run = 0.0
    while True:
        try:
            process_telegram_commands(conn, poll_timeout=15)
            now_ts = time.time()
            if now_ts - last_bar_run >= 900:
                run_analysis_cycle(conn, notify_mode="auto")
                last_bar_run = now_ts
        except KeyboardInterrupt:
            logger.info("Polling stopped by user.")
            break
        except Exception as exc:
            logger.error(f"Polling loop error: {exc}")
            send_telegram_message(f"⚠️ <b>BOT ERROR ({BOT_VERSION}):</b> <code>{_esc(exc)}</code>")
            time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"APEX-QUANT-OMNI {BOT_VERSION}")
    parser.add_argument("--mode", choices=["oneshot", "polling"], default="oneshot")
    args = parser.parse_args()

    conn = init_db(DB_PATH)
    try:
        if args.mode == "polling":
            run_continuous_polling_loop(conn)
        else:
            handled_check = process_telegram_commands(conn, poll_timeout=0)
            run_analysis_cycle(conn, notify_mode="auto", suppress_hold_notify=handled_check)
    except Exception as exc:
        logger.exception(f"Fatal execution error: {exc}")
        send_telegram_message(f"🚨 <b>FATAL ERROR ({BOT_VERSION}):</b> <code>{_esc(exc)}</code>")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
