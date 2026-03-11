#!/usr/bin/env python3
"""
backtest.py

目的:
  sector_rotation.py のB（RS加速度）/C（ベータ乖離）シグナルが過去にどれだけ有効だったかを検証する。

出力:
  - コンソール集計（シグナル別×期間別、セクター別）
  - backtest.html（plotly）
  - logs/backtest_results.json（全イベント詳細）

注意:
  - 同一セクターで20営業日以内の再発火は無視（最初のみカウント）
  - 出来高0の日はイベント/計算から除外
  - NaNが出るイベントは除外
"""

from __future__ import annotations

import json
import os
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from scipy.stats import binomtest
import yfinance as yf

import sector_rotation as sr

SignalType = Literal["B_IN", "B_OUT", "C_UP", "C_DOWN"]
Horizon = Literal[5, 10, 20]


@dataclass(frozen=True)
class Event:
    date: str
    ticker: str
    sector: str
    signal: SignalType
    z_score: float
    entry_date: str
    entry_open: float
    exit_date_5: str | None
    exit_close_5: float | None
    ret_5: float | None
    topix_ret_5: float | None
    ex_ret_5: float | None
    exit_date_10: str | None
    exit_close_10: float | None
    ret_10: float | None
    topix_ret_10: float | None
    ex_ret_10: float | None
    exit_date_20: str | None
    exit_close_20: float | None
    ret_20: float | None
    topix_ret_20: float | None
    ex_ret_20: float | None


def fetch_ohlcv_2y() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    2年（約500営業日）程度を確保するため、カレンダー約760日を取得して欠損を落とす。
    """
    tickers = list(sr.SECTOR_ETFS.keys()) + [sr.TOPIX_ETF]
    end = datetime.today()
    start = end - timedelta(days=760)
    print("データ取得中（2年程度）...")
    raw = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=False)
    close = raw["Close"][tickers].dropna(how="all")
    open_ = raw["Open"][tickers].dropna(how="all")
    volume = raw["Volume"][tickers].dropna(how="all")

    common = close.index.intersection(open_.index).intersection(volume.index)
    close = close.loc[common].dropna()
    open_ = open_.loc[common].dropna()
    volume = volume.loc[common].dropna()

    # おおよそ直近500営業日に寄せる（長すぎる場合）
    if len(close) > 520:
        close = close.iloc[-520:]
        open_ = open_.iloc[-520:]
        volume = volume.iloc[-520:]

    print(f"取得完了: {close.index[0].strftime('%Y-%m-%d')} - {close.index[-1].strftime('%Y-%m-%d')} ({len(close)}日)")
    return open_, close, volume


def _rolling_zscore(df: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = df.rolling(window).mean()
    std = df.rolling(window).std()
    return (df - mean) / std


def calc_b_signals_all(close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    sector_rotation.py のBと同じパラメータ/ロジックで全日分の
    - rs（対TOPIX）
    - rs_acc_z（RS加速度z-score）
    - signal（"B_IN"/"B_OUT"/""） を返す
    """
    tickers = list(sr.SECTOR_ETFS.keys())
    rs = close[tickers].div(close[sr.TOPIX_ETF], axis=0)
    rs_mom = rs.pct_change(sr.RS_SHORT_WINDOW)
    rs_acc = rs_mom - rs_mom.shift(sr.RS_LONG_WINDOW)
    rs_ma = rs.rolling(sr.RS_TREND_WINDOW).mean()
    rs_trend = rs_ma.pct_change(sr.RS_LONG_WINDOW)
    rs_acc_z = _rolling_zscore(rs_acc.shift(1), sr.RS_ZSCORE_WINDOW)  # ルックアヘッド除去

    sig = pd.DataFrame("", index=rs.index, columns=tickers)
    cond_in = (rs_trend < 0) & (rs_acc > 0) & (rs_acc_z > sr.RS_ZSCORE_THRESHOLD)
    cond_out = (rs_trend > 0) & (rs_acc < 0) & (rs_acc_z < -sr.RS_ZSCORE_THRESHOLD)
    sig[cond_in] = "B_IN"
    sig[cond_out] = "B_OUT"
    return rs, rs_acc_z, sig


def calc_c_signals_all(close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    sector_rotation.py のCと同じパラメータで全日分の
    - div_z（β短期−β長期のz-score）
    - signal（"C_UP"/"C_DOWN"/""） を返す
    """
    tickers = list(sr.SECTOR_ETFS.keys())
    rets = close.pct_change().dropna()
    mkt = rets[sr.TOPIX_ETF]

    def rolling_beta(sector_ret: pd.Series, market_ret: pd.Series, window: int) -> pd.Series:
        cov = sector_ret.rolling(window).cov(market_ret)
        var = market_ret.rolling(window).var()
        return cov / var

    beta_s = pd.DataFrame(index=rets.index, columns=tickers, dtype=float)
    beta_l = pd.DataFrame(index=rets.index, columns=tickers, dtype=float)
    for t in tickers:
        beta_s[t] = rolling_beta(rets[t], mkt, sr.BETA_SHORT_WINDOW)
        beta_l[t] = rolling_beta(rets[t], mkt, sr.BETA_LONG_WINDOW)

    div = beta_s - beta_l
    div_z = _rolling_zscore(div.shift(1), sr.BETA_ZSCORE_WINDOW)  # ルックアヘッド除去

    sig = pd.DataFrame("", index=div_z.index, columns=tickers)
    sig[div_z > sr.BETA_ZSCORE_THRESHOLD] = "C_UP"
    sig[div_z < -sr.BETA_ZSCORE_THRESHOLD] = "C_DOWN"
    return div_z, sig


def _cooldown_filter(signal_df: pd.DataFrame, cooldown_bdays: int = 20) -> pd.DataFrame:
    """
    同一セクターで連続発火する場合、最初の発火のみカウントし、
    以後 cooldown_bdays 営業日以内の再発火は無視する。
    """
    out = signal_df.copy()
    tickers = signal_df.columns
    idx = signal_df.index
    last_fire_pos = {t: -10_000 for t in tickers}
    for pos, dt in enumerate(idx):
        for t in tickers:
            if out.at[dt, t] == "":
                continue
            if pos - last_fire_pos[t] <= cooldown_bdays:
                out.at[dt, t] = ""
            else:
                last_fire_pos[t] = pos
    return out


def _is_tradeable_day(volume_row: pd.Series) -> bool:
    # 出来高0が含まれる場合はスキップ（そのセクターのイベントで使う）
    try:
        return float(volume_row) > 0.0
    except Exception:
        return False


def _compute_forward_returns_for_event(
    signal_pos: int,
    ticker: str,
    open_: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    horizons: tuple[int, int, int] = (5, 10, 20),
) -> dict:
    """
    発火日 pos の「翌日始値」をエントリーとして、発火日+H の終値で決済する。
    （仕様文言「発火日翌日始値→H日後終値」に合わせ、Hは発火日基準）
    """
    idx = close.index
    entry_pos = signal_pos + 1
    if entry_pos >= len(idx):
        return {}

    entry_dt = idx[entry_pos]
    if not _is_tradeable_day(volume.at[entry_dt, ticker]):
        return {}
    if not _is_tradeable_day(volume.at[entry_dt, sr.TOPIX_ETF]):
        return {}

    entry_open = float(open_.at[entry_dt, ticker])
    entry_open_topix = float(open_.at[entry_dt, sr.TOPIX_ETF])
    if not np.isfinite(entry_open) or entry_open <= 0:
        return {}
    if not np.isfinite(entry_open_topix) or entry_open_topix <= 0:
        return {}

    out: dict = {
        "entry_date": entry_dt.strftime("%Y-%m-%d"),
        "entry_open": entry_open,
    }

    for h in horizons:
        exit_pos = signal_pos + h
        if exit_pos >= len(idx):
            out[f"exit_date_{h}"] = None
            out[f"exit_close_{h}"] = None
            out[f"ret_{h}"] = None
            out[f"topix_ret_{h}"] = None
            out[f"ex_ret_{h}"] = None
            continue

        exit_dt = idx[exit_pos]
        if not _is_tradeable_day(volume.at[exit_dt, ticker]):
            out[f"exit_date_{h}"] = None
            out[f"exit_close_{h}"] = None
            out[f"ret_{h}"] = None
            out[f"topix_ret_{h}"] = None
            out[f"ex_ret_{h}"] = None
            continue
        if not _is_tradeable_day(volume.at[exit_dt, sr.TOPIX_ETF]):
            out[f"exit_date_{h}"] = None
            out[f"exit_close_{h}"] = None
            out[f"ret_{h}"] = None
            out[f"topix_ret_{h}"] = None
            out[f"ex_ret_{h}"] = None
            continue

        exit_close = float(close.at[exit_dt, ticker])
        exit_close_topix = float(close.at[exit_dt, sr.TOPIX_ETF])
        if not np.isfinite(exit_close) or exit_close <= 0:
            continue
        if not np.isfinite(exit_close_topix) or exit_close_topix <= 0:
            continue

        ret = (exit_close / entry_open) - 1.0
        topix_ret = (exit_close_topix / entry_open_topix) - 1.0
        ex = ret - topix_ret

        out[f"exit_date_{h}"] = exit_dt.strftime("%Y-%m-%d")
        out[f"exit_close_{h}"] = exit_close
        out[f"ret_{h}"] = float(ret)
        out[f"topix_ret_{h}"] = float(topix_ret)
        out[f"ex_ret_{h}"] = float(ex)

    return out


def build_events(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
) -> tuple[list[Event], pd.DataFrame, pd.DataFrame]:
    """
    B/Cシグナルのイベント一覧を構築。
    戻り値: (events, rs_df, b_z_df)
    """
    tickers = list(sr.SECTOR_ETFS.keys())
    rs, b_z, b_sig = calc_b_signals_all(close)
    c_z, c_sig = calc_c_signals_all(close)

    b_sig = _cooldown_filter(b_sig, cooldown_bdays=20)
    c_sig = _cooldown_filter(c_sig, cooldown_bdays=20)

    events: list[Event] = []

    # Bイベント
    for dt_pos, dt in enumerate(b_sig.index):
        for t in tickers:
            s = b_sig.at[dt, t]
            if s == "":
                continue
            if not _is_tradeable_day(volume.at[dt, t]):
                continue
            z = float(b_z.at[dt, t]) if pd.notna(b_z.at[dt, t]) else np.nan
            if not np.isfinite(z):
                continue

            fwd = _compute_forward_returns_for_event(dt_pos, t, open_, close, volume)
            if not fwd:
                continue
            ex20 = fwd.get("ex_ret_20")
            if ex20 is None:
                continue

            events.append(
                Event(
                    date=dt.strftime("%Y-%m-%d"),
                    ticker=t,
                    sector=sr.SECTOR_ETFS[t],
                    signal=s,  # type: ignore[arg-type]
                    z_score=float(z),
                    entry_date=fwd["entry_date"],
                    entry_open=float(fwd["entry_open"]),
                    exit_date_5=fwd.get("exit_date_5"),
                    exit_close_5=fwd.get("exit_close_5"),
                    ret_5=fwd.get("ret_5"),
                    topix_ret_5=fwd.get("topix_ret_5"),
                    ex_ret_5=fwd.get("ex_ret_5"),
                    exit_date_10=fwd.get("exit_date_10"),
                    exit_close_10=fwd.get("exit_close_10"),
                    ret_10=fwd.get("ret_10"),
                    topix_ret_10=fwd.get("topix_ret_10"),
                    ex_ret_10=fwd.get("ex_ret_10"),
                    exit_date_20=fwd.get("exit_date_20"),
                    exit_close_20=fwd.get("exit_close_20"),
                    ret_20=fwd.get("ret_20"),
                    topix_ret_20=fwd.get("topix_ret_20"),
                    ex_ret_20=fwd.get("ex_ret_20"),
                )
            )

    # Cイベント
    for dt_pos, dt in enumerate(c_sig.index):
        for t in tickers:
            s = c_sig.at[dt, t]
            if s == "":
                continue
            if not _is_tradeable_day(volume.at[dt, t]):
                continue
            z = float(c_z.at[dt, t]) if pd.notna(c_z.at[dt, t]) else np.nan
            if not np.isfinite(z):
                continue

            fwd = _compute_forward_returns_for_event(dt_pos, t, open_, close, volume)
            if not fwd:
                continue
            ex20 = fwd.get("ex_ret_20")
            if ex20 is None:
                continue

            events.append(
                Event(
                    date=dt.strftime("%Y-%m-%d"),
                    ticker=t,
                    sector=sr.SECTOR_ETFS[t],
                    signal=s,  # type: ignore[arg-type]
                    z_score=float(z),
                    entry_date=fwd["entry_date"],
                    entry_open=float(fwd["entry_open"]),
                    exit_date_5=fwd.get("exit_date_5"),
                    exit_close_5=fwd.get("exit_close_5"),
                    ret_5=fwd.get("ret_5"),
                    topix_ret_5=fwd.get("topix_ret_5"),
                    ex_ret_5=fwd.get("ex_ret_5"),
                    exit_date_10=fwd.get("exit_date_10"),
                    exit_close_10=fwd.get("exit_close_10"),
                    ret_10=fwd.get("ret_10"),
                    topix_ret_10=fwd.get("topix_ret_10"),
                    ex_ret_10=fwd.get("ex_ret_10"),
                    exit_date_20=fwd.get("exit_date_20"),
                    exit_close_20=fwd.get("exit_close_20"),
                    ret_20=fwd.get("ret_20"),
                    topix_ret_20=fwd.get("topix_ret_20"),
                    ex_ret_20=fwd.get("ex_ret_20"),
                )
            )

    return events, rs, b_sig


def events_to_frame(events: list[Event]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(e) for e in events])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    for h in (5, 10, 20):
        if f"exit_date_{h}" in df.columns:
            df[f"exit_date_{h}"] = pd.to_datetime(df[f"exit_date_{h}"], errors="coerce")
    return df.sort_values(["date", "ticker", "signal"]).reset_index(drop=True)


def summarize_by_signal(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    rows = []
    for sig in ["B_IN", "B_OUT", "C_UP", "C_DOWN"]:
        sub = df[df["signal"] == sig]
        for h in (5, 10, 20):
            ex_col = f"ex_ret_{h}"
            vals = sub[ex_col].dropna().astype(float)
            if len(vals) == 0:
                rows.append(
                    {
                        "signal": sig,
                        "horizon": h,
                        "count": 0,
                        "win_rate": np.nan,
                        "mean_excess_%": np.nan,
                        "median_excess_%": np.nan,
                        "max_excess_%": np.nan,
                        "min_excess_%": np.nan,
                    }
                )
                continue
            rows.append(
                {
                    "signal": sig,
                    "horizon": h,
                    "count": int(len(vals)),
                    "win_rate": float((vals > 0).mean()),
                    "mean_excess_%": float(vals.mean() * 100),
                    "median_excess_%": float(vals.median() * 100),
                    "max_excess_%": float(vals.max() * 100),
                    "min_excess_%": float(vals.min() * 100),
                }
            )
    out = pd.DataFrame(rows)
    return out


def summarize_by_sector(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sub = df.copy()
    # 代表として20日超過リターンで平均を出す
    vals = sub[["sector", "signal", "ex_ret_20"]].dropna()
    g = vals.groupby(["sector", "signal"])["ex_ret_20"]
    out = g.agg(count="count", mean_excess=lambda x: float(x.mean() * 100)).reset_index()
    return out.sort_values(["count", "mean_excess"], ascending=[False, False])


def print_console_reports(df: pd.DataFrame) -> None:
    print("\n====================")
    print("シグナル別×期間別 集計")
    print("====================")
    by_sig = summarize_by_signal(df)
    if by_sig.empty:
        print("イベントなし")
    else:
        show = by_sig.copy()
        show["win_rate"] = (show["win_rate"] * 100).round(1)
        show[["mean_excess_%", "median_excess_%", "max_excess_%", "min_excess_%"]] = show[
            ["mean_excess_%", "median_excess_%", "max_excess_%", "min_excess_%"]
        ].round(2)
        print(show.to_string(index=False))

    print("\n====================")
    print("セクター別 集計（20日超過リターン）")
    print("====================")
    by_sector = summarize_by_sector(df)
    if by_sector.empty:
        print("イベントなし")
    else:
        show2 = by_sector.copy()
        show2["mean_excess"] = show2["mean_excess"].round(2)
        print(show2.to_string(index=False))


def build_histograms(df: pd.DataFrame) -> go.Figure:
    signals = ["B_IN", "B_OUT", "C_UP", "C_DOWN"]
    horizons = [5, 10, 20]
    fig = make_subplots(
        rows=len(signals),
        cols=len(horizons),
        subplot_titles=[f"{s} / {h}日" for s in signals for h in horizons],
        vertical_spacing=0.08,
        horizontal_spacing=0.05,
    )
    for r, s in enumerate(signals, start=1):
        for c, h in enumerate(horizons, start=1):
            vals = df[df["signal"] == s][f"ex_ret_{h}"].dropna().astype(float) * 100
            fig.add_trace(
                go.Histogram(
                    x=vals,
                    nbinsx=30,
                    marker_color="#2563eb" if "IN" in s or "UP" in s else "#ef4444",
                    opacity=0.85,
                    showlegend=False,
                ),
                row=r,
                col=c,
            )
    fig.update_layout(
        template="plotly_white",
        title="シグナル別 超過リターン分布（ヒストグラム）",
        height=900,
        margin=dict(l=40, r=20, t=70, b=40),
        bargap=0.05,
    )
    fig.update_xaxes(title_text="超過リターン（%）")
    fig.update_yaxes(title_text="件数")
    return fig


def build_rs_marker_small_multiples(rs: pd.DataFrame, b_sig: pd.DataFrame) -> go.Figure:
    tickers = list(sr.SECTOR_ETFS.keys())
    rs = rs[tickers].copy()
    b_sig = b_sig[tickers].copy()

    cols, rows = 4, 5
    fig = make_subplots(
        rows=rows,
        cols=cols,
        subplot_titles=[sr.SECTOR_ETFS[t] for t in tickers] + [""] * (rows * cols - len(tickers)),
        horizontal_spacing=0.03,
        vertical_spacing=0.08,
    )

    for i, t in enumerate(tickers):
        r = i // cols + 1
        c = i % cols + 1
        s = rs[t].dropna()
        if s.empty:
            continue
        fig.add_trace(
            go.Scatter(x=s.index, y=s, mode="lines", line=dict(color="#6b7280", width=1.5), showlegend=False),
            row=r,
            col=c,
        )
        in_dates = b_sig.index[b_sig[t] == "B_IN"]
        out_dates = b_sig.index[b_sig[t] == "B_OUT"]
        if len(in_dates) > 0:
            fig.add_trace(
                go.Scatter(
                    x=in_dates,
                    y=rs.loc[in_dates, t],
                    mode="markers",
                    marker=dict(symbol="triangle-up", size=8, color="#16a34a"),
                    showlegend=False,
                ),
                row=r,
                col=c,
            )
        if len(out_dates) > 0:
            fig.add_trace(
                go.Scatter(
                    x=out_dates,
                    y=rs.loc[out_dates, t],
                    mode="markers",
                    marker=dict(symbol="triangle-down", size=8, color="#ef4444"),
                    showlegend=False,
                ),
                row=r,
                col=c,
            )

    fig.update_layout(
        template="plotly_white",
        title="RS（対TOPIX）推移とBシグナル発火（IN=緑▲ / OUT=赤▼）",
        height=950,
        margin=dict(l=40, r=20, t=70, b=40),
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f3f4f6", zeroline=False)
    return fig


def build_cumulative_excess_curve(df: pd.DataFrame, horizon: int = 20) -> go.Figure:
    """
    仮想戦略（イベントごとに20日保有）:
      - B_IN: ロング（超過 = sector - topix）
      - B_OUT: ショート（超過 = (-sector) - topix）
    イベントが重なる場合は単純に平均（等金額分散）で合成。
    """
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="イベントなし", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        fig.update_layout(template="plotly_white", title="累積超過リターン曲線（仮想戦略）", height=220)
        return fig

    # エントリー日ごとに1期間リターンを並べ、日次累積に落とす（イベントのexit日に一括計上）
    sub_in = df[df["signal"] == "B_IN"].dropna(subset=[f"ex_ret_{horizon}", f"exit_date_{horizon}"])
    sub_out = df[df["signal"] == "B_OUT"].dropna(subset=[f"ret_{horizon}", f"topix_ret_{horizon}", f"exit_date_{horizon}"])

    # B_OUTはショート: excess = (-sector_ret) - topix_ret
    out_ex = (-sub_out[f"ret_{horizon}"].astype(float)) - sub_out[f"topix_ret_{horizon}"].astype(float)

    in_series = sub_in.groupby(f"exit_date_{horizon}")[f"ex_ret_{horizon}"].mean().astype(float)
    out_series = out_ex.groupby(sub_out[f"exit_date_{horizon}"]).mean().astype(float)

    all_dates = pd.to_datetime(sorted(set(in_series.index.tolist()) | set(out_series.index.tolist())))
    daily_in = in_series.reindex(all_dates).fillna(0.0)
    daily_out = out_series.reindex(all_dates).fillna(0.0)

    # 複利累積
    cum_in = (1.0 + daily_in).cumprod() - 1.0
    cum_out = (1.0 + daily_out).cumprod() - 1.0

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=all_dates, y=cum_in * 100, mode="lines", name="B_IN（ロング）", line=dict(color="#2563eb", width=2)))
    fig.add_trace(go.Scatter(x=all_dates, y=cum_out * 100, mode="lines", name="B_OUT（ショート）", line=dict(color="#ef4444", width=2)))
    fig.update_layout(
        template="plotly_white",
        title=f"累積超過リターン曲線（仮想戦略, {horizon}日ホールド, exit日一括計上）",
        height=360,
        margin=dict(l=40, r=20, t=70, b=40),
        hovermode="x unified",
        yaxis_title="累積超過リターン（%）",
    )
    return fig


def save_results_json(events: list[Event]) -> Path:
    logs_dir = Path(getattr(sr, "LOG_DIR", Path(__file__).resolve().parent / "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    out = logs_dir / "backtest_results.json"
    payload = [asdict(e) for e in events]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def save_and_open_html(figs: list[tuple[str, go.Figure]], output_path: Path) -> None:
    parts: list[str] = []
    for i, (title, fig) in enumerate(figs):
        parts.append(
            f'<section class="card"><h3>{title}</h3>'
            + pio.to_html(
                fig,
                include_plotlyjs="cdn" if i == 0 else False,
                full_html=False,
                config={"displaylogo": False, "responsive": True},
            )
            + "</section>"
        )

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sector_rotation backtest</title>
  <style>
    body {{ background: #fff; color: #111827; font-family: sans-serif; margin: 16px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px 2px; margin: 12px 0 18px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    h2 {{ margin: 0 0 8px; }}
    h3 {{ margin: 8px 0 6px; font-size: 16px; }}
    .meta {{ color: #6b7280; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <h2>セクターローテーション バックテスト</h2>
  <div class="meta">生成: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
  {''.join(parts)}
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    webbrowser.open(f"file://{output_path.resolve()}")


# ====================
# 追加分析（既存ロジックは変更しない）
# ====================

def _calc_b_signals_all_with_threshold(close: pd.DataFrame, threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickers = list(sr.SECTOR_ETFS.keys())
    rs = close[tickers].div(close[sr.TOPIX_ETF], axis=0)
    rs_mom = rs.pct_change(sr.RS_SHORT_WINDOW)
    rs_acc = rs_mom - rs_mom.shift(sr.RS_LONG_WINDOW)
    rs_ma = rs.rolling(sr.RS_TREND_WINDOW).mean()
    rs_trend = rs_ma.pct_change(sr.RS_LONG_WINDOW)
    rs_acc_z = _rolling_zscore(rs_acc, sr.RS_ZSCORE_WINDOW)

    sig = pd.DataFrame("", index=rs.index, columns=tickers)
    cond_in = (rs_trend < 0) & (rs_acc > 0) & (rs_acc_z > threshold)
    cond_out = (rs_trend > 0) & (rs_acc < 0) & (rs_acc_z < -threshold)
    sig[cond_in] = "B_IN"
    sig[cond_out] = "B_OUT"
    return rs_acc_z, sig


def _calc_c_signals_all_with_threshold(close: pd.DataFrame, threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickers = list(sr.SECTOR_ETFS.keys())
    rets = close.pct_change().dropna()
    mkt = rets[sr.TOPIX_ETF]

    def rolling_beta(sector_ret: pd.Series, market_ret: pd.Series, window: int) -> pd.Series:
        cov = sector_ret.rolling(window).cov(market_ret)
        var = market_ret.rolling(window).var()
        return cov / var

    beta_s = pd.DataFrame(index=rets.index, columns=tickers, dtype=float)
    beta_l = pd.DataFrame(index=rets.index, columns=tickers, dtype=float)
    for t in tickers:
        beta_s[t] = rolling_beta(rets[t], mkt, sr.BETA_SHORT_WINDOW)
        beta_l[t] = rolling_beta(rets[t], mkt, sr.BETA_LONG_WINDOW)
    div = beta_s - beta_l
    div_z = _rolling_zscore(div, sr.BETA_ZSCORE_WINDOW)

    sig = pd.DataFrame("", index=div_z.index, columns=tickers)
    sig[div_z > threshold] = "C_UP"
    sig[div_z < -threshold] = "C_DOWN"
    return div_z, sig


def _calc_a_share_zscore(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    tickers = list(sr.SECTOR_ETFS.keys())
    turnover = close[tickers].mul(volume[tickers])
    total = turnover.sum(axis=1)
    share = turnover.div(total, axis=0)
    mean = share.rolling(sr.A_ZSCORE_WINDOW).mean()
    std = share.rolling(sr.A_ZSCORE_WINDOW).std()
    z = (share - mean) / std
    return z.rolling(sr.A_PERSISTENCE_DAYS).mean()


def _events_from_signal(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    sig_df: pd.DataFrame,
    z_df: pd.DataFrame,
    signal_value: str,
) -> pd.DataFrame:
    tickers = list(sr.SECTOR_ETFS.keys())
    sig_df = sig_df.copy()
    sig_df = _cooldown_filter(sig_df, cooldown_bdays=20)
    records: list[dict] = []
    for pos, dt in enumerate(sig_df.index):
        for t in tickers:
            if sig_df.at[dt, t] != signal_value:
                continue
            if not _is_tradeable_day(volume.at[dt, t]):
                continue
            z = z_df.at[dt, t] if (dt in z_df.index and t in z_df.columns) else np.nan
            if not np.isfinite(float(z)) if pd.notna(z) else True:
                continue
            fwd = _compute_forward_returns_for_event(pos, t, open_, close, volume)
            if not fwd:
                continue
            if fwd.get("ex_ret_20") is None:
                continue
            rec = {
                "date": dt,
                "ticker": t,
                "sector": sr.SECTOR_ETFS[t],
                "signal": signal_value,
                "z_score": float(z),
            }
            for h in (5, 10, 20):
                rec[f"ex_ret_{h}"] = fwd.get(f"ex_ret_{h}")
            records.append(rec)
    out = pd.DataFrame(records)
    if out.empty:
        return out
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def threshold_sensitivity_analysis(open_: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    # B: 1.5, 2.0, 2.5, 3.0
    for thr in [1.5, 2.0, 2.5, 3.0]:
        b_z, b_sig = _calc_b_signals_all_with_threshold(close, threshold=thr)
        for sig in ["B_IN", "B_OUT"]:
            ev = _events_from_signal(open_, close, volume, b_sig, b_z, sig)
            vals = ev["ex_ret_20"].dropna().astype(float) if not ev.empty else pd.Series([], dtype=float)
            rows.append(
                {
                    "signal": sig,
                    "threshold": thr,
                    "count": int(len(vals)),
                    "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
                    "mean_excess_%": float(vals.mean() * 100) if len(vals) else np.nan,
                }
            )

    # C: 2.0, 2.5, 3.0, 3.5
    for thr in [2.0, 2.5, 3.0, 3.5]:
        c_z, c_sig = _calc_c_signals_all_with_threshold(close, threshold=thr)
        for sig in ["C_UP", "C_DOWN"]:
            ev = _events_from_signal(open_, close, volume, c_sig, c_z, sig)
            vals = ev["ex_ret_20"].dropna().astype(float) if not ev.empty else pd.Series([], dtype=float)
            rows.append(
                {
                    "signal": sig,
                    "threshold": thr,
                    "count": int(len(vals)),
                    "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
                    "mean_excess_%": float(vals.mean() * 100) if len(vals) else np.nan,
                }
            )

    df = pd.DataFrame(rows)
    return df


def print_threshold_sensitivity(df_sens: pd.DataFrame) -> None:
    print("\n====================")
    print("閾値感度分析（20日超過リターン）")
    print("====================")
    if df_sens.empty:
        print("データなし")
        return
    show = df_sens.copy()
    show["win_rate"] = (show["win_rate"] * 100).round(1)
    show["mean_excess_%"] = show["mean_excess_%"].round(2)
    print(show.sort_values(["signal", "threshold"]).to_string(index=False))


def build_threshold_sensitivity_fig(df_sens: pd.DataFrame) -> go.Figure:
    if df_sens.empty:
        fig = go.Figure()
        fig.add_annotation(text="データなし", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        fig.update_layout(template="plotly_white", title="閾値感度分析（平均超過リターン, 20日）", height=220)
        return fig

    fig = make_subplots(rows=1, cols=2, subplot_titles=["B（RS加速度）", "C（ベータ乖離）"], horizontal_spacing=0.12)
    colors = {"B_IN": "#2563eb", "B_OUT": "#ef4444", "C_UP": "#2563eb", "C_DOWN": "#ef4444"}

    b = df_sens[df_sens["signal"].isin(["B_IN", "B_OUT"])].sort_values("threshold")
    c = df_sens[df_sens["signal"].isin(["C_UP", "C_DOWN"])].sort_values("threshold")

    for sig in ["B_IN", "B_OUT"]:
        sub = b[b["signal"] == sig]
        fig.add_trace(
            go.Bar(
                x=sub["threshold"].astype(str),
                y=sub["mean_excess_%"],
                name=sig,
                marker_color=colors[sig],
                opacity=0.85,
            ),
            row=1,
            col=1,
        )
    for sig in ["C_UP", "C_DOWN"]:
        sub = c[c["signal"] == sig]
        fig.add_trace(
            go.Bar(
                x=sub["threshold"].astype(str),
                y=sub["mean_excess_%"],
                name=sig,
                marker_color=colors[sig],
                opacity=0.85,
            ),
            row=1,
            col=2,
        )

    fig.update_layout(
        template="plotly_white",
        title="閾値感度分析（平均超過リターン, 20日）",
        barmode="group",
        height=420,
        margin=dict(l=40, r=20, t=70, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        yaxis_title="平均超過リターン（%）",
    )
    return fig


def combo_signal_analysis(open_: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame) -> tuple[pd.DataFrame, go.Figure]:
    tickers = list(sr.SECTOR_ETFS.keys())
    idx = close.index

    b_z, b_sig = _calc_b_signals_all_with_threshold(close, threshold=sr.RS_ZSCORE_THRESHOLD)
    c_z, c_sig = _calc_c_signals_all_with_threshold(close, threshold=sr.BETA_ZSCORE_THRESHOLD)
    a_z = _calc_a_share_zscore(close, volume)

    b_sig = _cooldown_filter(b_sig, cooldown_bdays=20)
    c_sig = _cooldown_filter(c_sig, cooldown_bdays=20)

    def within_window(sig_other: pd.Series, pos: int, w: int = 3) -> bool:
        lo = max(0, pos - w)
        hi = min(len(sig_other) - 1, pos + w)
        return bool(sig_other.iloc[lo : hi + 1].any())

    # combo signals (anchor = B signal day)
    combo1 = pd.DataFrame("", index=idx, columns=tickers)
    combo2 = pd.DataFrame("", index=idx, columns=tickers)
    combo3 = pd.DataFrame("", index=idx, columns=tickers)

    for pos, dt in enumerate(idx):
        for t in tickers:
            if b_sig.at[dt, t] == "B_IN":
                other = (c_sig[t] == "C_UP") if t in c_sig.columns else pd.Series(False, index=c_sig.index)
                if dt in other.index and within_window(other.loc[other.index.intersection(idx)], pos, 3):
                    combo1.at[dt, t] = "COMBO_1"
                # COMBO_3: A_z > 1.0（同日）
                if dt in a_z.index and t in a_z.columns:
                    v = a_z.at[dt, t]
                    if pd.notna(v) and float(v) > 1.0:
                        combo3.at[dt, t] = "COMBO_3"
            if b_sig.at[dt, t] == "B_OUT":
                other = (c_sig[t] == "C_DOWN") if t in c_sig.columns else pd.Series(False, index=c_sig.index)
                if dt in other.index and within_window(other.loc[other.index.intersection(idx)], pos, 3):
                    combo2.at[dt, t] = "COMBO_2"

    # compute events per combo (use B z-score if available else NaN)
    ev1 = _events_from_signal(open_, close, volume, combo1, b_z.reindex(idx), "COMBO_1")
    ev2 = _events_from_signal(open_, close, volume, combo2, b_z.reindex(idx), "COMBO_2")
    ev3 = _events_from_signal(open_, close, volume, combo3, b_z.reindex(idx), "COMBO_3")

    all_ev = pd.concat([ev1, ev2, ev3], ignore_index=True) if not (ev1.empty and ev2.empty and ev3.empty) else pd.DataFrame()

    print("\n====================")
    print("複合シグナル検証（超過リターン）")
    print("====================")
    if all_ev.empty:
        print("イベントなし")
    else:
        rows = []
        for sig in ["COMBO_1", "COMBO_2", "COMBO_3"]:
            sub = all_ev[all_ev["signal"] == sig]
            for h in (5, 10, 20):
                vals = sub[f"ex_ret_{h}"].dropna().astype(float)
                rows.append(
                    {
                        "combo": sig,
                        "horizon": h,
                        "count": int(len(vals)),
                        "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
                        "mean_excess_%": float(vals.mean() * 100) if len(vals) else np.nan,
                    }
                )
        tab = pd.DataFrame(rows)
        show = tab.copy()
        show["win_rate"] = (show["win_rate"] * 100).round(1)
        show["mean_excess_%"] = show["mean_excess_%"].round(2)
        print(show.to_string(index=False))

        # COMBO_2 の二項検定（20日）
        combo2_20 = all_ev[(all_ev["signal"] == "COMBO_2")]["ex_ret_20"].dropna().astype(float)
        if len(combo2_20) > 0:
            wins = int((combo2_20 > 0).sum())
            total = int(len(combo2_20))
            wr_pct = wins / total * 100
            result = binomtest(wins, n=total, p=0.5, alternative="greater")
            p_val = result.pvalue
            sig_str = "※統計的に有意でない(p>0.05)" if p_val > 0.05 else "★統計的に有意(p≤0.05)"
            print(f"\nCOMBO_2: 勝率{wr_pct:.0f}% ({wins}件), p値={p_val:.2f} {sig_str}")

    # HTML: combo histograms (20日)
    fig = make_subplots(rows=1, cols=3, subplot_titles=["COMBO_1（20日）", "COMBO_2（20日）", "COMBO_3（20日）"], horizontal_spacing=0.08)
    for i, sig in enumerate(["COMBO_1", "COMBO_2", "COMBO_3"], start=1):
        vals = all_ev[all_ev["signal"] == sig]["ex_ret_20"].dropna().astype(float) * 100 if not all_ev.empty else pd.Series([], dtype=float)
        fig.add_trace(
            go.Histogram(x=vals, nbinsx=25, marker_color="#2563eb" if sig in ["COMBO_1", "COMBO_3"] else "#ef4444", opacity=0.85, showlegend=False),
            row=1,
            col=i,
        )
    fig.update_layout(
        template="plotly_white",
        title="複合シグナル 超過リターン分布（ヒストグラム, 20日）",
        height=360,
        margin=dict(l=40, r=20, t=70, b=40),
        bargap=0.06,
    )
    fig.update_xaxes(title_text="超過リターン（%）")
    fig.update_yaxes(title_text="件数")
    return all_ev, fig


def sector_filtering_analysis(df_events: pd.DataFrame) -> None:
    print("\n====================")
    print("セクターフィルタリング（効くセクターのみ）")
    print("====================")
    if df_events.empty:
        print("イベントなし")
        return
    sec_mean = df_events.groupby("sector")["ex_ret_20"].mean().dropna()
    good = set(sec_mean[sec_mean > 0].index.tolist())
    if not good:
        print("平均超過リターン（20日）が正のセクターがありません。")
        return
    print(f"対象セクター数: {len(good)} / {df_events['sector'].nunique()}")
    filtered = df_events[df_events["sector"].isin(good)]
    tab = summarize_by_signal(filtered)
    if tab.empty:
        print("フィルタ後イベントなし")
        return
    show = tab[tab["horizon"] == 20].copy()
    show["win_rate"] = (show["win_rate"] * 100).round(1)
    show["mean_excess_%"] = show["mean_excess_%"].round(2)
    print(show[["signal", "horizon", "count", "win_rate", "mean_excess_%"]].to_string(index=False))


def walk_forward_validation(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
) -> None:
    """
    ウォークフォワード検証:
    - 全期間を前半（トレーニング）と後半（テスト）に分割する。
    - トレーニング期間で有効セクターと最適閾値を選定し、
      テスト期間でその設定を固定適用して成績を計測する。
    """
    print("\n" + "=" * 50)
    print("ウォークフォワード検証")
    print("=" * 50)

    n = len(close)
    if n < 2:
        print("データ不足のため検証をスキップします。")
        return

    n_train = n // 2
    train_close = close.iloc[:n_train]
    train_open = open_.iloc[:n_train]
    train_volume = volume.iloc[:n_train]
    test_close = close.iloc[n_train:]
    test_open = open_.iloc[n_train:]
    test_volume = volume.iloc[n_train:]

    t_start = close.index[0].strftime("%Y-%m-%d")
    t_mid = close.index[n_train].strftime("%Y-%m-%d")
    t_end = close.index[-1].strftime("%Y-%m-%d")
    print(f"  トレーニング期間: {t_start} 〜 {close.index[n_train - 1].strftime('%Y-%m-%d')} ({n_train}日)")
    print(f"  テスト期間      : {t_mid} 〜 {t_end} ({n - n_train}日)")

    thresholds = [1.5, 2.0, 2.5, 3.0]
    signals = ["B_IN", "B_OUT", "C_UP", "C_DOWN"]

    # ---- トレーニング期間での有効セクター・最適閾値の選定 -------------------
    # 各シグナルで20日超過リターンが正のセクターを選定
    def _get_events_20d(oc: pd.DataFrame, op: pd.DataFrame, vo: pd.DataFrame, thr: float) -> pd.DataFrame:
        b_z, b_sig = _calc_b_signals_all_with_threshold(oc, threshold=thr)
        c_z, c_sig = _calc_c_signals_all_with_threshold(oc, threshold=thr)
        dfs = []
        for sv in ["B_IN", "B_OUT"]:
            e = _events_from_signal(op, oc, vo, b_sig, b_z, sv)
            if not e.empty:
                dfs.append(e)
        for sv in ["C_UP", "C_DOWN"]:
            e = _events_from_signal(op, oc, vo, c_sig, c_z, sv)
            if not e.empty:
                dfs.append(e)
        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    # トレーニングデータでシグナル別・閾値別に20日勝率を計算
    best_thresholds: dict[str, float] = {}
    valid_sectors: dict[str, set] = {}

    print("\n  [トレーニング] シグナル別 最適閾値の選定")
    print(f"  {'シグナル':<10} {'最適閾値':>8} {'勝率':>8} {'件数':>6}")
    print("  " + "-" * 38)

    for sig in signals:
        best_wr = -1.0
        best_thr = thresholds[0]
        for thr in thresholds:
            ev = _get_events_20d(train_close, train_open, train_volume, thr)
            if ev.empty:
                continue
            sub = ev[ev["signal"] == sig]
            vals = sub["ex_ret_20"].dropna().astype(float)
            if len(vals) == 0:
                continue
            wr = float((vals > 0).mean())
            if wr > best_wr:
                best_wr = wr
                best_thr = thr
        best_thresholds[sig] = best_thr
        wr_str = f"{best_wr * 100:.1f}%" if best_wr >= 0 else "N/A"
        # 有効セクターの選定（トレーニング期間で平均超過リターン>0のセクター）
        ev_best = _get_events_20d(train_close, train_open, train_volume, best_thr)
        if not ev_best.empty:
            sub_best = ev_best[ev_best["signal"] == sig]
            sec_mean = sub_best.groupby("sector")["ex_ret_20"].mean().dropna()
            valid_sectors[sig] = set(sec_mean[sec_mean > 0].index.tolist())
            count_str = str(len(sub_best["ex_ret_20"].dropna()))
        else:
            valid_sectors[sig] = set()
            count_str = "0"
        print(f"  {sig:<10} {best_thr:>8.1f} {wr_str:>8} {count_str:>6}")

    # ---- テスト期間での評価 ------------------------------------------------
    print("\n  [テスト期間] トレーニング選定パラメータを固定適用")

    train_results: dict[str, dict] = {}
    test_results: dict[str, dict] = {}

    for sig in signals:
        thr = best_thresholds.get(sig, thresholds[0])
        vsec = valid_sectors.get(sig, set())

        # トレーニング成績
        ev_tr = _get_events_20d(train_close, train_open, train_volume, thr)
        if not ev_tr.empty and vsec:
            sub_tr = ev_tr[(ev_tr["signal"] == sig) & (ev_tr["sector"].isin(vsec))]
        elif not ev_tr.empty:
            sub_tr = ev_tr[ev_tr["signal"] == sig]
        else:
            sub_tr = pd.DataFrame()
        vals_tr = sub_tr["ex_ret_20"].dropna().astype(float) if not sub_tr.empty else pd.Series([], dtype=float)

        # テスト成績
        ev_te = _get_events_20d(test_close, test_open, test_volume, thr)
        if not ev_te.empty and vsec:
            sub_te = ev_te[(ev_te["signal"] == sig) & (ev_te["sector"].isin(vsec))]
        elif not ev_te.empty:
            sub_te = ev_te[ev_te["signal"] == sig]
        else:
            sub_te = pd.DataFrame()
        vals_te = sub_te["ex_ret_20"].dropna().astype(float) if not sub_te.empty else pd.Series([], dtype=float)

        train_results[sig] = {
            "count": int(len(vals_tr)),
            "win_rate": float((vals_tr > 0).mean()) if len(vals_tr) else float("nan"),
            "mean_ex": float(vals_tr.mean() * 100) if len(vals_tr) else float("nan"),
        }
        test_results[sig] = {
            "count": int(len(vals_te)),
            "win_rate": float((vals_te > 0).mean()) if len(vals_te) else float("nan"),
            "mean_ex": float(vals_te.mean() * 100) if len(vals_te) else float("nan"),
        }

    # 比較表を出力
    print(f"\n  {'シグナル':<10} {'閾値':>6} | {'[訓練] 件数':>10} {'勝率':>8} {'平均超過%':>10} | {'[テスト] 件数':>10} {'勝率':>8} {'平均超過%':>10}")
    print("  " + "-" * 90)
    for sig in signals:
        thr = best_thresholds.get(sig, thresholds[0])
        tr = train_results[sig]
        te = test_results[sig]

        def _fmt(v: float, fmt: str) -> str:
            return f"{v:{fmt}}" if not (isinstance(v, float) and np.isnan(v)) else "  N/A"

        wr_tr = _fmt(tr["win_rate"] * 100, ".1f") + "%" if not np.isnan(tr["win_rate"]) else "  N/A"
        wr_te = _fmt(te["win_rate"] * 100, ".1f") + "%" if not np.isnan(te["win_rate"]) else "  N/A"
        me_tr = _fmt(tr["mean_ex"], ".2f") if not np.isnan(tr["mean_ex"]) else "  N/A"
        me_te = _fmt(te["mean_ex"], ".2f") if not np.isnan(te["mean_ex"]) else "  N/A"
        print(
            f"  {sig:<10} {thr:>6.1f} |"
            f" {tr['count']:>10} {wr_tr:>8} {me_tr:>10} |"
            f" {te['count']:>10} {wr_te:>8} {me_te:>10}"
        )

    print("\n  ※ テスト期間の成績が「真の実力」に近い数字です。")
    print("=" * 50)


def main() -> None:
    open_, close, volume = fetch_ohlcv_2y()
    events, rs, b_sig = build_events(open_=open_, close=close, volume=volume)
    json_path = save_results_json(events)

    df = events_to_frame(events)
    print_console_reports(df)
    print(f"\nJSON保存: {json_path}")

    if df.empty:
        print("イベントが無いためHTMLは最小表示になります。")

    fig1 = build_histograms(df) if not df.empty else go.Figure()
    fig2 = build_rs_marker_small_multiples(rs, b_sig)
    fig3 = build_cumulative_excess_curve(df, horizon=20)

    out_html = Path(__file__).resolve().parent / "backtest.html"
    save_and_open_html(
        [
            ("1. シグナル別 超過リターン分布（ヒストグラム）", fig1),
            ("2. RS推移 + Bシグナル発火マーカー（Small Multiples）", fig2),
            ("3. 累積超過リターン曲線（仮想戦略）", fig3),
        ],
        out_html,
    )
    print(f"HTML生成: {out_html}")

    # ===== 追加分析（main()の最後に追加）=====
    df_sens = threshold_sensitivity_analysis(open_=open_, close=close, volume=volume)
    print_threshold_sensitivity(df_sens)

    combo_df, combo_fig = combo_signal_analysis(open_=open_, close=close, volume=volume)

    sector_filtering_analysis(df_events=df)

    sens_fig = build_threshold_sensitivity_fig(df_sens)

    # backtest.html に追加（上書き生成）
    save_and_open_html(
        [
            ("1. シグナル別 超過リターン分布（ヒストグラム）", fig1),
            ("2. RS推移 + Bシグナル発火マーカー（Small Multiples）", fig2),
            ("3. 累積超過リターン曲線（仮想戦略）", fig3),
            ("4. 閾値感度分析（平均超過リターン, 20日）", sens_fig),
            ("5. 複合シグナル 超過リターン分布（ヒストグラム）", combo_fig),
        ],
        out_html,
    )

    # ウォークフォワード検証
    walk_forward_validation(open_=open_, close=close, volume=volume)


if __name__ == "__main__":
    main()

