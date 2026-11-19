"""
Forensic Invariant & Multi-Disciplinary Verification Suite for APEX-QUANT-OMNI v78.0
Validates causality, econophysics bounds, microstructure VWAP/BVC, Kelly sizing, and execution parity.
"""
from datetime import datetime, timezone
import numpy as np
import pandas as pd

import bot


def _make_synthetic_bars(n_15m: int = 320, trend_drift: float = 0.0001) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx15 = pd.date_range("2026-01-05 00:00:00+00:00", periods=n_15m, freq="15min")
    rng = np.random.default_rng(42)
    rets = rng.normal(trend_drift, 0.0012, size=n_15m)
    close = 2650.0 * np.cumprod(1.0 + rets)
    open_px = np.roll(close, 1)
    open_px[0] = 2650.0
    high = np.maximum(open_px, close) + rng.uniform(0.4, 2.5, size=n_15m)
    low = np.minimum(open_px, close) - rng.uniform(0.4, 2.5, size=n_15m)
    df15 = pd.DataFrame(
        {"Open": open_px, "High": high, "Low": low, "Close": close, "Volume": np.zeros(n_15m)},
        index=idx15,
    )
    df1h = (
        df15.resample("1h")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )
    return df15, df1h


def test_causal_1h_alignment_no_future_leak():
    df15_raw, df1h_raw = _make_synthetic_bars(320)
    df15 = bot.compute_indicators(df15_raw, atr_period=14)
    df1h_a = bot.compute_indicators(df1h_raw, atr_period=14)

    df1h_mut = df1h_raw.copy()
    target_15m_ts = pd.Timestamp("2026-01-06 10:30:00+00:00")
    unclosed_1h_ts = pd.Timestamp("2026-01-06 10:00:00+00:00")
    df1h_mut.loc[unclosed_1h_ts, "Close"] *= 0.70
    df1h_mut.loc[unclosed_1h_ts, "Low"] *= 0.70
    df1h_b = bot.compute_indicators(df1h_mut, atr_period=14)

    idx15 = df15.index.get_loc(target_15m_ts)
    sig_a = bot.score_bar_confluence(df15, df1h_a, idx15, z_threshold=2.0)
    sig_b = bot.score_bar_confluence(df15, df1h_b, idx15, z_threshold=2.0)

    assert sig_a["mtf_state"] == sig_b["mtf_state"]
    assert sig_a["mtf_norm"] == sig_b["mtf_norm"]
    assert sig_a["score"] == sig_b["score"]


def test_smc_liquidity_sweep_fires_on_prior20_breach():
    df15_raw, _ = _make_synthetic_bars(80)
    df = bot.compute_indicators(df15_raw, atr_period=14)
    prior_min = float(df["Low"].iloc[-22:-2].min())

    df.iloc[-2, df.columns.get_loc("Low")] = prior_min - 5.0
    df.iloc[-2, df.columns.get_loc("Close")] = prior_min + 0.5
    df.iloc[-1, df.columns.get_loc("Open")] = prior_min + 0.5
    df.iloc[-1, df.columns.get_loc("Low")] = prior_min + 0.2
    df.iloc[-1, df.columns.get_loc("Close")] = prior_min + 3.0

    pts, state, direction, _, _ = bot._evaluate_smc_pillar(df, len(df) - 1)
    assert state == "BULLISH_LIQUIDITY_SWEEP"
    assert direction == "BUY"
    assert pts >= 95.0


def test_zero_loss_profit_factor_bounded():
    summary = bot._summarize_trades([1.5, 1.5], atr_period=14, z_threshold=2.0)
    assert summary["pf"] <= 10.0


def test_conservative_intrabar_sl_before_promotion():
    state = {
        "side": "BUY",
        "entry": 2650.0,
        "sl_dist": 10.0,
        "cur_sl": 2640.0,
        "tp1": 2665.0,
        "trail_trig": 2668.0,
        "tp2": 2675.0,
        "tp3": 2690.0,
        "tp1_hit": 0,
        "tp2_hit": 0,
        "locked_trail_r": 0.0,
        "cost_r": 0.05,
    }
    _, reason, realized_r = bot.step_trade_state_on_bar(state, hi=2670.0, lo=2639.0)
    assert reason == "CLOSED_SL"
    assert realized_r == -1.05


def test_econophysics_and_microstructure_bounds():
    df15_raw, _ = _make_synthetic_bars(120, trend_drift=0.0005)
    df = bot.compute_indicators(df15_raw, atr_period=14)
    assert (df["HURST"] >= 0.15).all() and (df["HURST"] <= 0.95).all()
    assert (df["KAUFMAN_ER"] >= 0.0).all() and (df["KAUFMAN_ER"] <= 1.0).all()
    assert (df["ENTROPY"] >= 0.0).all() and (df["ENTROPY"] <= 1.0).all()
    assert (df["GK_VOL"] >= 0.0).all()
    assert (df["SESSION_VWAP"] > 0.0).all()


def test_causal_macro_alignment_no_future_leak():
    df15_raw, _ = _make_synthetic_bars(120)
    idx_1h = pd.date_range("2026-01-05 00:00:00+00:00", periods=40, freq="1h")
    dxy_df = pd.DataFrame({"Close": np.linspace(105.0, 103.0, 40), "MACRO_Z": np.full(40, -1.2)}, index=idx_1h)
    dxy_mut = dxy_df.copy()
    ts_target = pd.Timestamp("2026-01-06 10:30:00+00:00")
    dxy_mut.loc[pd.Timestamp("2026-01-06 10:00:00+00:00"), "MACRO_Z"] = 4.5

    s1, _ = bot._evaluate_causal_macro_pillar({"dxy": dxy_df}, ts_target, "BUY")
    s2, _ = bot._evaluate_causal_macro_pillar({"dxy": dxy_mut}, ts_target, "BUY")
    assert s1 == s2 == 100.0


def test_vol_kelly_sizing_scales_down_in_high_vol():
    bt = {"trades": 30, "wr": 55.0, "oos_trades": 12, "oos_wr": 58.0}
    low_vol_risk, _ = bot.compute_vol_kelly_risk_percent(1.0, bt, atr_ratio=0.90)
    high_vol_risk, _ = bot.compute_vol_kelly_risk_percent(1.0, bt, atr_ratio=2.20)
    assert high_vol_risk < low_vol_risk


def test_december_january_ff_year_rollover():
    now_dec = datetime(2026, 12, 30, 12, 0, tzinfo=timezone.utc)
    dt_jan = bot._parse_ff_time_to_utc("Thu Jan 2", "8:30am", now_dec, assumed_tz=timezone.utc)
    assert dt_jan is not None
    assert dt_jan.year == 2027
    assert dt_jan.month == 1
    assert dt_jan.day == 2


def test_dynamic_closed_bar_selector_post_close():
    df15_raw, _ = _make_synthetic_bars(40)
    last_bar_open = df15_raw.index[-1].to_pydatetime()
    idx, is_stale, _ = bot.select_closed_bar_index(df15_raw, last_bar_open + pd.Timedelta(minutes=20))
    assert idx == len(df15_raw) - 1
    assert is_stale is False
