#!/usr/bin/env python3
"""
dashboard.py

sector_rotation.py と同じデータソース（yfinance TOPIX-17業種ETF）・同じパラメータで、
指定セクションをPlotlyで描画し、dashboard.html を保存してブラウザで自動表示する。

要件:
- sector_rotation.py の定義（SECTOR_ETFS/各パラメータ/fetch_data 等）を import して再利用（コピペ禁止）
- データ取得は1回だけ実行し、全セクションで使い回す
"""

from __future__ import annotations

import json
import os
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import yfinance as yf

import sector_rotation as sr


def _clip_recent(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df
    start = df.index.max() - timedelta(days=days)
    return df.loc[df.index >= start]


def _rolling_zscore(x: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = x.rolling(window).mean()
    std = x.rolling(window).std()
    return (x - mean) / std


def calc_a_volume_share_zscore(turnover: pd.DataFrame) -> pd.DataFrame:
    tickers = list(sr.SECTOR_ETFS.keys())
    total = turnover[tickers].sum(axis=1)
    share = turnover[tickers].div(total, axis=0)
    z = _rolling_zscore(share, sr.A_ZSCORE_WINDOW)
    return z.rolling(sr.A_PERSISTENCE_DAYS).mean()


def calc_b_rs_acc_zscore(close: pd.DataFrame) -> pd.DataFrame:
    tickers = list(sr.SECTOR_ETFS.keys())
    rs = close[tickers].div(close[sr.TOPIX_ETF], axis=0)
    rs_mom = rs.pct_change(sr.RS_SHORT_WINDOW)
    rs_acc = rs_mom - rs_mom.shift(sr.RS_LONG_WINDOW)
    return _rolling_zscore(rs_acc, sr.RS_ZSCORE_WINDOW)


def calc_b_signal(close: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    tickers = list(sr.SECTOR_ETFS.keys())
    rs = close[tickers].div(close[sr.TOPIX_ETF], axis=0)
    rs_mom = rs.pct_change(sr.RS_SHORT_WINDOW)
    rs_acc = rs_mom - rs_mom.shift(sr.RS_LONG_WINDOW)
    rs_ma = rs.rolling(sr.RS_TREND_WINDOW).mean()
    rs_trend = rs_ma.pct_change(sr.RS_LONG_WINDOW)

    acc_z = _rolling_zscore(rs_acc, sr.RS_ZSCORE_WINDOW)
    latest_z = acc_z.iloc[-1]
    latest_trend = rs_trend.iloc[-1]
    latest_acc = rs_acc.iloc[-1]

    sig = pd.Series("-", index=tickers, dtype="object")
    cond_in = (latest_trend < 0) & (latest_acc > 0) & (latest_z > sr.RS_ZSCORE_THRESHOLD)
    cond_out = (latest_trend > 0) & (latest_acc < 0) & (latest_z < -sr.RS_ZSCORE_THRESHOLD)
    sig[cond_in] = "ROTATION IN"
    sig[cond_out] = "ROTATION OUT"
    return latest_z, sig


def calc_c_beta_div_zscore(returns: pd.DataFrame) -> pd.DataFrame:
    tickers = list(sr.SECTOR_ETFS.keys())
    mkt = returns[sr.TOPIX_ETF]

    def rolling_beta(sector_ret: pd.Series, market_ret: pd.Series, window: int) -> pd.Series:
        cov = sector_ret.rolling(window).cov(market_ret)
        var = market_ret.rolling(window).var()
        return cov / var

    beta_s = pd.DataFrame(index=returns.index)
    beta_l = pd.DataFrame(index=returns.index)
    for t in tickers:
        beta_s[t] = rolling_beta(returns[t], mkt, sr.BETA_SHORT_WINDOW)
        beta_l[t] = rolling_beta(returns[t], mkt, sr.BETA_LONG_WINDOW)
    div = beta_s - beta_l
    return _rolling_zscore(div, sr.BETA_ZSCORE_WINDOW)


def calc_c_signal(returns: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    z = calc_c_beta_div_zscore(returns)
    latest_z = z.iloc[-1]
    tickers = list(sr.SECTOR_ETFS.keys())
    sig = pd.Series("-", index=tickers, dtype="object")
    sig[latest_z > sr.BETA_ZSCORE_THRESHOLD] = "ベータ上方異常"
    sig[latest_z < -sr.BETA_ZSCORE_THRESHOLD] = "ベータ下方異常"
    return latest_z, sig


def _safe_round(x: float) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return float("nan")
    return float(x)

def _minmax_to_unit(x: pd.Series) -> pd.Series:
    """
    min-max正規化で [-1, +1] にスケール。
    NaNは無視し、全て同一値（または有効値が1つ以下）の場合は0で返す。
    """
    v = x.astype(float)
    valid = v.dropna()
    if valid.size <= 1:
        return pd.Series(0.0, index=v.index)
    mn = float(valid.min())
    mx = float(valid.max())
    if np.isclose(mx, mn):
        return pd.Series(0.0, index=v.index)
    scaled01 = (v - mn) / (mx - mn)
    return scaled01 * 2.0 - 1.0


@dataclass(frozen=True)
class SectorSnapshot:
    ticker: str
    name: str
    a_z: float
    b_z: float
    c_z: float
    integrated_raw: float
    integrated: float
    confidence: str
    a_alert: bool
    b_alert: bool
    c_alert: bool
    b_signal: str
    c_signal: str
    action: str


def build_snapshots(close: pd.DataFrame, turnover: pd.DataFrame, returns: pd.DataFrame) -> list[SectorSnapshot]:
    tickers = list(sr.SECTOR_ETFS.keys())

    a_z_series = calc_a_volume_share_zscore(turnover).iloc[-1]
    b_z_series, b_sig = calc_b_signal(close)
    c_z_series, c_sig = calc_c_signal(returns)

    a_latest = a_z_series.reindex(tickers).astype(float)
    b_latest = b_z_series.reindex(tickers).astype(float)
    c_latest = c_z_series.reindex(tickers).astype(float)

    # 改善2: 統合前に各指標を min-max 正規化（-1〜+1）して合算
    a_norm = _minmax_to_unit(a_latest)
    b_norm = _minmax_to_unit(b_latest)
    c_norm = _minmax_to_unit(c_latest)

    def recommend_action(conf: str, b_signal: str) -> str:
        if conf == "🔴" and b_signal == "ROTATION IN":
            return "セクター内代表銘柄を即調査"
        if conf == "🔴" and b_signal == "ROTATION OUT":
            return "保有銘柄の売却検討"
        if conf == "🟠" and b_signal == "ROTATION IN":
            return "ウォッチリスト追加、翌日確認"
        if conf == "🟠" and b_signal == "ROTATION OUT":
            return "保有銘柄にこのセクターがないか確認"
        if conf == "🟡":
            return "経過観察"
        return "-"

    rows: list[SectorSnapshot] = []
    for t in tickers:
        a_z = _safe_round(a_latest.get(t, np.nan))
        b_z = _safe_round(b_latest.get(t, np.nan))
        c_z = _safe_round(c_latest.get(t, np.nan))

        a_alert = bool(not np.isnan(a_z) and abs(a_z) > sr.A_ZSCORE_THRESHOLD)
        b_alert = bool(not np.isnan(b_z) and abs(b_z) > sr.RS_ZSCORE_THRESHOLD)
        c_alert = bool(not np.isnan(c_z) and abs(c_z) > sr.BETA_ZSCORE_THRESHOLD)

        alert_count = int(a_alert) + int(b_alert) + int(c_alert)
        if alert_count >= 3:
            confidence = "🔴"
        elif alert_count == 2:
            confidence = "🟠"
        elif alert_count == 1:
            confidence = "🟡"
        else:
            confidence = ""

        integrated_raw = float(np.nan_to_num(a_z) + np.nan_to_num(b_z) + np.nan_to_num(c_z))
        integrated = float(
            np.nan_to_num(a_norm.get(t, 0.0))
            + np.nan_to_num(b_norm.get(t, 0.0))
            + np.nan_to_num(c_norm.get(t, 0.0))
        )

        rows.append(
            SectorSnapshot(
                ticker=t,
                name=sr.SECTOR_ETFS[t],
                a_z=a_z,
                b_z=b_z,
                c_z=c_z,
                integrated_raw=integrated_raw,
                integrated=integrated,
                confidence=confidence,
                a_alert=a_alert,
                b_alert=b_alert,
                c_alert=c_alert,
                b_signal=str(b_sig.get(t, "-")),
                c_signal=str(c_sig.get(t, "-")),
                action=recommend_action(confidence, str(b_sig.get(t, "-"))),
            )
        )

    rows.sort(key=lambda r: r.integrated, reverse=True)
    return rows


def _heatmap_colorscale() -> list:
    """
    改善6: 閾値内はほぼグレー、閾値超で一気に色が付く diverging。
    強いマイナス（ROTATION OUT / 売り圧力）: 赤 (#FF4136)
    ゼロ付近（正常）: グレー (#F0F0F0)
    強いプラス（ROTATION IN / 買い圧力）: 青 (#0074D9)
    """
    vmin, vmax = -3.0, 3.0
    stops = [
        (-3.0, "#FF4136"),
        (-1.5, "#FFB3AD"),
        (-0.5, "#F0F0F0"),
        (0.0, "#F0F0F0"),
        (0.5, "#F0F0F0"),
        (1.5, "#A7C9F5"),
        (3.0, "#0074D9"),
    ]
    to_pos = lambda v: (v - vmin) / (vmax - vmin)
    return [[to_pos(v), c] for v, c in stops]


def _heatmap_z_range() -> tuple[float, float]:
    return -3.0, 3.0


def build_section0_market_environment(asof_date: str) -> go.Figure:
    """
    改善1: マーケット環境セクション（過去3ヶ月の日次、横並び4枚）
    fetch_data()とは別にyfinanceで取得する。
    """
    tickers = ["1306.T", "2070.T", "USDJPY=X", "^TNX"]
    titles = ["TOPIX（1306.T）", "日経VI（2070.T）", "ドル円（USDJPY=X）", "10年金利（^TNX）"]

    end = datetime.today()
    start = end - timedelta(days=92)
    raw = yf.download(tickers, start=start, end=end, progress=False)
    close = raw["Close"].dropna(how="all")

    fig = make_subplots(rows=1, cols=4, subplot_titles=titles, horizontal_spacing=0.04)
    for i, t in enumerate(tickers):
        col = i + 1
        s = close[t].dropna()
        if s.empty:
            continue
        fig.add_trace(
            go.Scatter(x=s.index, y=s, mode="lines", line=dict(color="#111827", width=2), showlegend=False),
            row=1,
            col=col,
        )
        last_x = s.index[-1]
        last_y = float(s.iloc[-1])
        fig.add_annotation(
            x=last_x,
            y=last_y,
            text=f"{last_y:.2f}",
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(size=11, color="#111827"),
            row=1,
            col=col,
        )

    fig.update_layout(
        template="plotly_white",
        title="マーケット環境",
        height=320,
        margin=dict(l=40, r=20, t=70, b=40),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f3f4f6", zeroline=False)
    return fig


def build_section1_heatmap(snapshots: list[SectorSnapshot], asof_date: str) -> go.Figure:
    names = [s.name for s in snapshots]
    tickers = [s.ticker for s in snapshots]
    a = [s.a_z for s in snapshots]
    b = [s.b_z for s in snapshots]
    c = [s.c_z for s in snapshots]
    integ = [s.integrated for s in snapshots]

    y_labels = ["A（出来高z-score）", "B（RS加速度z-score）", "C（ベータ乖離z-score）", "統合スコア"]
    z = np.array([a, b, c, integ], dtype=float)

    # z-score表示テキスト
    text = []
    for r in range(z.shape[0]):
        row = []
        for col in range(z.shape[1]):
            v = z[r, col]
            row.append("" if np.isnan(v) else f"{v:.2f}")
        text.append(row)

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=names,
            y=y_labels,
            colorscale=_heatmap_colorscale(),
            zmin=_heatmap_z_range()[0],
            zmax=_heatmap_z_range()[1],
            zmid=0,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 12},
            hovertemplate="セクター=%{x}<br>ticker=%{customdata}<br>指標=%{y}<br>値=%{z:.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        template="plotly_white",
        title=f"セクターローテーション ヒートマップ（{asof_date}）",
        margin=dict(l=80, r=20, t=60, b=40),
        xaxis=dict(tickangle=-30),
        yaxis=dict(autorange="reversed"),
        height=320,
    )

    # 統合スコア行のうち、アラートが出ているセクターを強調（太字 + 背景色）
    alerted_cols = [i for i, s in enumerate(snapshots) if (s.a_alert or s.b_alert or s.c_alert)]

    # 背景色はオーバーレイHeatmapで実現（対象セルだけ薄い色）
    highlight = np.full_like(z, np.nan, dtype=float)
    for col_idx in alerted_cols:
        highlight[3, col_idx] = 1.0
    fig.add_trace(
        go.Heatmap(
            z=highlight,
            x=names,
            y=y_labels,
            colorscale=[[0.0, "rgba(0,0,0,0)"], [1.0, "#fff7ed"]],
            showscale=False,
            hoverinfo="skip",
        )
    )

    # 太字はannotationで上書き（統合スコア行のみ）
    for col_idx, s in enumerate(snapshots):
        if s.a_alert or s.b_alert or s.c_alert:
            v = s.integrated
            if np.isnan(v):
                continue
            fig.add_annotation(
                x=s.name,
                y=y_labels[3],
                text=f"<b>{v:.2f}</b>",
                showarrow=False,
                font=dict(size=12, color="black"),
            )

    # hoverでtickerも見たい場合のためにcustomdata（zと同shape: 4x17）
    fig.data[0].customdata = np.tile(np.array(tickers, dtype=object), (4, 1))
    return fig


def build_sectionA_sector_relative_performance(close: pd.DataFrame, snapshots: list[SectorSnapshot]) -> go.Figure:
    """
    セクションA: 各セクターETF終値をTOPIX終値で正規化し、基準日（約3ヶ月前）=100で表示。
    - アラートあり: 太く/濃く
    - アラートなし: 薄いグレー
    - TOPIX自体: y=100 の水平線
    """
    tickers = list(sr.SECTOR_ETFS.keys())
    df = _clip_recent(close[tickers + [sr.TOPIX_ETF]].copy(), days=92)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="データなし", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        fig.update_layout(template="plotly_white", title="セクター別 相対パフォーマンス（対TOPIX, 基準=100）", height=220)
        return fig

    ratio = df[tickers].div(df[sr.TOPIX_ETF], axis=0)
    base = ratio.iloc[0]
    rel = ratio.div(base, axis=1) * 100.0

    alerted = {s.ticker for s in snapshots if (s.a_alert or s.b_alert or s.c_alert)}
    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
        "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#0ea5e9", "#f97316", "#22c55e", "#ef4444", "#a855f7", "#14b8a6", "#eab308",
    ]
    color_by_ticker = {t: palette[i % len(palette)] for i, t in enumerate(tickers)}

    fig = go.Figure()
    for t in tickers:
        name = sr.SECTOR_ETFS[t]
        s = rel[t].dropna()
        if s.empty:
            continue
        if t in alerted:
            fig.add_trace(
                go.Scatter(
                    x=s.index,
                    y=s,
                    mode="lines",
                    name=name,
                    line=dict(color=color_by_ticker[t], width=3),
                    hovertemplate=f"{name}<br>%{{x|%Y-%m-%d}}<br>%{{y:.2f}}<extra></extra>",
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=s.index,
                    y=s,
                    mode="lines",
                    name=name,
                    line=dict(color="#9ca3af", width=1),
                    opacity=0.30,
                    hovertemplate=f"{name}<br>%{{x|%Y-%m-%d}}<br>%{{y:.2f}}<extra></extra>",
                )
            )

    fig.add_hline(y=100, line_dash="solid", line_color="#111827", line_width=1)
    fig.update_layout(
        template="plotly_white",
        title="セクター別 相対パフォーマンス（対TOPIX, 基準=100, 過去3ヶ月）",
        height=520,
        margin=dict(l=40, r=20, t=70, b=40),
        hovermode="x unified",
        yaxis_title="相対パフォーマンス（基準=100）",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


def _load_trends_json(asof_date: str):
    logs_dir = _find_logs_dir()
    path = logs_dir / f"trends_{asof_date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_trends_payload(payload) -> tuple[pd.DataFrame, dict[str, list[dict]]]:
    """
    trends_YYYY-MM-DD.json の形式が多少違っても拾えるように柔軟に解釈する。
    期待する出力:
      - df: sector, z_score, change_rate
      - breakdown: sector -> keywords list (dict)
    """
    breakdown: dict[str, list[dict]] = {}
    rows: list[dict] = []

    # ルートがlistの場合は results として扱う
    if isinstance(payload, list):
        payload = {"results": payload}

    # 形式1: {"sectors": {"食品": {"z_score":..,"change_rate":..,"keywords":[...]}, ...}}
    if isinstance(payload, dict):
        sectors = payload.get("sectors")
        if isinstance(sectors, dict):
            for sector, v in sectors.items():
                if not isinstance(v, dict):
                    continue
                z = v.get("z_score", v.get("z"))
                ch = v.get("change_rate", v.get("change", v.get("pct_change")))
                rows.append({"sector": sector, "z_score": z, "change_rate": ch})
                kw = v.get("keywords") or v.get("breakdown") or v.get("items")
                if isinstance(kw, list):
                    breakdown[sector] = [k for k in kw if isinstance(k, dict)]

    # 形式2: {"results":[{"sector":..,"z_score":..,"change_rate":..,"keywords":[...]}, ...]}
    if not rows:
        results = None
        if isinstance(payload, dict):
            results = payload.get("results") or payload.get("data")
        if isinstance(results, list):
            for r in results:
                if not isinstance(r, dict):
                    continue
                sector = r.get("sector") or r.get("name")
                if not sector:
                    continue
                rows.append(
                    {
                        "sector": sector,
                        "z_score": r.get("z_score", r.get("z")),
                        "change_rate": r.get("change_rate", r.get("change")),
                    }
                )
                kw = r.get("keywords") or r.get("breakdown")
                if isinstance(kw, list):
                    breakdown[str(sector)] = [k for k in kw if isinstance(k, dict)]

    df = pd.DataFrame(rows)
    if df.empty:
        return df, breakdown
    df["z_score"] = pd.to_numeric(df["z_score"], errors="coerce")
    df["change_rate"] = pd.to_numeric(df["change_rate"], errors="coerce")
    return df, breakdown


def build_sectionB_google_trends(asof_date: str) -> tuple[go.Figure, str]:
    payload = _load_trends_json(asof_date)
    if not payload:
        fig = go.Figure()
        fig.add_annotation(text="Google Trendsデータなし", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(size=16))
        fig.update_layout(template="plotly_white", title="Google Trends 詳細", height=220, margin=dict(l=20, r=20, t=60, b=20))
        return fig, ""

    df, breakdown = _parse_trends_payload(payload)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="Google Trendsデータ形式を解釈できませんでした", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        fig.update_layout(template="plotly_white", title="Google Trends 詳細", height=220, margin=dict(l=20, r=20, t=60, b=20))
        return fig, ""

    df = df.sort_values("z_score", ascending=False)
    colors = []
    for ch in df["change_rate"].to_numpy():
        if np.isnan(ch):
            colors.append("#9ca3af")
        elif ch >= 0:
            colors.append("#0074D9")
        else:
            colors.append("#FF4136")

    text = []
    for z, ch in zip(df["z_score"].to_numpy(), df["change_rate"].to_numpy()):
        if np.isnan(ch):
            text.append(f"z={z:.2f}")
        else:
            text.append(f"z={z:.2f}, Δ={ch:.1f}%")

    fig = go.Figure(
        data=[
            go.Bar(
                x=df["z_score"],
                y=df["sector"],
                orientation="h",
                marker_color=colors,
                text=text,
                textposition="outside",
                hovertemplate="%{y}<br>z-score=%{x:.2f}<br>変化率=%{customdata}<extra></extra>",
                customdata=[("-" if np.isnan(ch) else f"{ch:.1f}%") for ch in df["change_rate"].to_numpy()],
            )
        ]
    )
    fig.update_layout(
        template="plotly_white",
        title="Google Trends 詳細（z-score降順、色=変化率の符号）",
        height=max(420, 18 * len(df) + 220),
        margin=dict(l=120, r=20, t=70, b=40),
        xaxis_title="関心度 z-score",
        yaxis=dict(autorange="reversed"),
    )

    # キーワード内訳（JSONに含まれている場合のみ）をHTMLで追加表示（折りたたみ）
    breakdown_html_parts: list[str] = []
    if breakdown:
        for sector, items in breakdown.items():
            if not items:
                continue
            rows = []
            for it in items[:12]:
                k = it.get("keyword") or it.get("term") or it.get("name") or ""
                v = it.get("score") or it.get("value") or it.get("z_score") or ""
                rows.append(
                    "<tr>"
                    f"<td style='padding:4px; border-bottom:1px solid #f3f4f6'>{k}</td>"
                    f"<td style='padding:4px; border-bottom:1px solid #f3f4f6; text-align:right'>{v}</td>"
                    "</tr>"
                )
            if not rows:
                continue
            breakdown_html_parts.append(
                "<details>"
                f"<summary>▶ {sector} のキーワード内訳</summary>"
                "<div style='overflow:auto'>"
                "<table style='border-collapse:collapse; width:100%; font-size:12px'>"
                "<thead><tr><th style='text-align:left; border-bottom:1px solid #e5e7eb; padding:4px'>keyword</th>"
                "<th style='text-align:right; border-bottom:1px solid #e5e7eb; padding:4px'>score</th></tr></thead>"
                "<tbody>"
                + "".join(rows)
                + "</tbody></table></div></details>"
            )

    breakdown_html = ""
    if breakdown_html_parts:
        breakdown_html = "<div style='margin:10px 0 6px'>" + "".join(breakdown_html_parts) + "</div>"
    return fig, breakdown_html


def build_sectionC_explanation_panel_html() -> str:
    text = """【指標の読み方】
- RS加速度 z-score (B): セクターの対TOPIXパフォーマンスの「加速度」を標準偏差で正規化した値。+1.5以上 = そのセクターへの資金流入が通常より著しく加速（ROTATION IN）。-1.5以下 = 資金流出が加速（ROTATION OUT）。
- ベータ乖離 z-score (C): セクターの短期ベータ（20日）と長期ベータ（60日）の乖離を標準偏差で正規化した値。+2.0以上 = セクターがTOPIXに対して異常に敏感に動いている（買い圧力の可能性）。-2.0以下 = 異常に鈍感（資金流出・ディフェンシブ化の可能性）。
- 出来高シェア z-score (A): セクターの売買代金がTOPIX全体に占める比率の変化。+2.0以上 = 通常より有意に取引が集中している。※現在はETF出来高ベースで精度に限界あり。
- Google Trends z-score (D): セクター関連キーワードのGoogle検索量の直近4週間変化。+1.5以上 = 個人投資家の関心が急上昇。B/Cシグナルとの一致は信頼度を高め、不一致は機関主導の可能性を示唆。
- 確信度: 🔴最高 = A+B+C全発火、🟠高 = 2つ発火、🟡中 = 1つ発火。

【閾値の根拠】
z-score ±1.5〜2.0 は正規分布の約93〜95%タイル。「20〜30営業日に1回程度しか起きない異常値」を検知する水準。今後バックテストにより最適値を調整予定。"""
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
    return f"<div class='explain' style='line-height:1.6; font-size:13px; color:#111827'>{safe}</div>"


def _find_logs_dir() -> Path:
    # 要件: ~/sector_rotation/logs/ にある
    p1 = Path(os.path.expanduser("~/sector_rotation/logs"))
    if p1.exists():
        return p1
    # 互換: sector_rotation.py と同じ logs ディレクトリ
    if hasattr(sr, "LOG_DIR"):
        return Path(getattr(sr, "LOG_DIR"))
    return p1


def _load_log_for_date(logs_dir: Path, date_str: str) -> list[dict] | None:
    path = logs_dir / f"signal_{date_str}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_latest_previous_log(logs_dir: Path, asof_date: str, lookback_days: int = 7) -> tuple[str, list[dict]] | tuple[None, None]:
    base = datetime.strptime(asof_date, "%Y-%m-%d")
    for i in range(1, lookback_days + 1):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        data = _load_log_for_date(logs_dir, d)
        if data:
            return d, data
    return None, None


def _prev_trend_arrow(today: float, prev: float | None, eps: float = 0.10) -> str:
    if prev is None or np.isnan(prev) or np.isnan(today):
        return "-"
    d = today - prev
    if d > eps:
        return "↑"
    if d < -eps:
        return "↓"
    return "→"


def build_section2_alert_table(snapshots: list[SectorSnapshot], asof_date: str) -> go.Figure:
    alerted = [s for s in snapshots if (s.a_alert or s.b_alert or s.c_alert)]

    logs_dir = _find_logs_dir()
    _prev_date, prev_log = _find_latest_previous_log(logs_dir, asof_date)

    prev_integrated_by_ticker: dict[str, float] = {}
    prev_az_by_ticker: dict[str, float] = {}
    prev_bz_by_ticker: dict[str, float] = {}
    prev_cz_by_ticker: dict[str, float] = {}
    if prev_log:
        for r in prev_log:
            t = r.get("ticker")
            if not t:
                continue
            # 旧ログ形式には integrated が無いので、あれば使う/無ければ A+B+C で復元
            if "integrated" in r:
                prev_integrated_by_ticker[t] = float(r["integrated"])
            else:
                prev_integrated_by_ticker[t] = float(
                    np.nan_to_num(r.get("A_z", 0.0))
                    + np.nan_to_num(r.get("B_z", 0.0))
                    + np.nan_to_num(r.get("C_z", 0.0))
                )
            if "A_z" in r:
                prev_az_by_ticker[t] = float(r["A_z"])
            if "B_z" in r:
                prev_bz_by_ticker[t] = float(r["B_z"])
            if "C_z" in r:
                prev_cz_by_ticker[t] = float(r["C_z"])

    if not alerted:
        fig = go.Figure()
        fig.add_annotation(
            text="アラートなし",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=16),
        )
        fig.update_layout(
            template="plotly_white",
            title="アラートサマリー",
            height=180,
            margin=dict(l=20, r=20, t=60, b=20),
        )
        return fig

    def fmt(v: float) -> str:
        return "-" if np.isnan(v) else f"{v:.2f}"

    def delta_arrow(today: float, prev: float | None) -> str:
        if prev is None or np.isnan(prev) or np.isnan(today):
            return "-"
        d = today - prev
        if d >= 0.50:
            return "↑"
        if d >= 0.15:
            return "↗"
        if d <= -0.50:
            return "↓"
        if d <= -0.15:
            return "↘"
        return "→"

    headers = ["セクター名", "確信度", "A_z", "B_z（シグナル）", "C_z（シグナル）", "前日比", "推奨アクション"]
    cells = [
        [s.name for s in alerted],
        [s.confidence for s in alerted],
        [fmt(s.a_z) for s in alerted],
        [f"{fmt(s.b_z)} ({s.b_signal})" for s in alerted],
        [f"{fmt(s.c_z)} ({s.c_signal})" for s in alerted],
        [
            (
                delta_arrow(s.a_z, prev_az_by_ticker.get(s.ticker))
                + delta_arrow(s.b_z, prev_bz_by_ticker.get(s.ticker))
                + delta_arrow(s.c_z, prev_cz_by_ticker.get(s.ticker))
            )
            for s in alerted
        ],
        [s.action for s in alerted],
    ]

    conf_colors = []
    for s in alerted:
        if s.confidence == "🔴":
            conf_colors.append("#fee2e2")
        elif s.confidence == "🟠":
            conf_colors.append("#ffedd5")
        elif s.confidence == "🟡":
            conf_colors.append("#fef9c3")
        else:
            conf_colors.append("white")

    fill_cols = [
        ["white"] * len(alerted),
        conf_colors,
        ["white"] * len(alerted),
        ["white"] * len(alerted),
        ["white"] * len(alerted),
        ["white"] * len(alerted),
        ["white"] * len(alerted),
    ]

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=headers, fill_color="#f3f4f6", align="left", font=dict(size=12)),
                cells=dict(values=cells, fill_color=fill_cols, align="left", font=dict(size=12)),
            )
        ]
    )
    fig.update_layout(
        template="plotly_white",
        title="アラートサマリー（アラート発生セクターのみ）",
        margin=dict(l=20, r=20, t=60, b=20),
        height=320,
    )
    return fig


def _build_threshold_colored_traces(
    x: Iterable, y: pd.Series, thr: float, name: str
) -> list[go.Scatter]:
    y_arr = y.to_numpy(dtype=float)
    mask = np.abs(y_arr) > thr
    y_gray = np.where(mask, np.nan, y_arr)
    y_red = np.where(mask, y_arr, np.nan)
    return [
        go.Scatter(x=x, y=y_gray, mode="lines", line=dict(color="#9ca3af", width=1.5), showlegend=False, name=name),
        go.Scatter(x=x, y=y_red, mode="lines", line=dict(color="#ef4444", width=2.0), showlegend=False, name=name),
    ]


def build_small_multiples(
    z_df: pd.DataFrame,
    threshold: float,
    section_title: str,
    days: int = 92,
    cols: int = 4,
    rows: int = 5,
) -> go.Figure:
    tickers = list(sr.SECTOR_ETFS.keys())
    z_df = _clip_recent(z_df[tickers], days=days)

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
        series = z_df[t].dropna()
        if series.empty:
            continue
        traces = _build_threshold_colored_traces(series.index, series, threshold, name=sr.SECTOR_ETFS[t])
        for tr in traces:
            fig.add_trace(tr, row=r, col=c)
        fig.add_hline(
            y=threshold,
            line_dash="dash",
            line_color="#ef4444",
            line_width=1,
            row=r,
            col=c,
        )
        fig.add_hline(
            y=-threshold,
            line_dash="dash",
            line_color="#ef4444",
            line_width=1,
            row=r,
            col=c,
        )

        last_x = series.index[-1]
        last_y = float(series.iloc[-1])
        fig.add_annotation(
            x=last_x,
            y=last_y,
            text=f"{last_y:.2f}",
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(size=10, color="#111827"),
            row=r,
            col=c,
        )

    fig.update_layout(
        template="plotly_white",
        title=section_title,
        showlegend=False,
        height=950,
        margin=dict(l=40, r=20, t=70, b=40),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=True, gridcolor="#f3f4f6")
    return fig


def _to_card_html(fig: go.Figure, include_js: bool) -> str:
    return pio.to_html(
        fig,
        include_plotlyjs="cdn" if include_js else False,
        full_html=False,
        config={"displaylogo": False, "responsive": True},
    )


def save_and_open_html(
    sections: list[tuple[str, str]],
    output_path: Path,
    data_last_date: str,
    business_days_ago: int,
    generated_at: str,
) -> None:
    cards_html = "\n".join([f'<section class="card"><h3>{title}</h3>{body}</section>' for title, body in sections])
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sector_rotation dashboard</title>
  <style>
    body {{
      background: #ffffff;
      color: #111827;
      font-family: sans-serif;
      margin: 16px;
    }}
    .card {{
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 10px 12px 2px;
      margin: 12px 0 18px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    h2 {{ margin: 0 0 8px; }}
    h3 {{ margin: 8px 0 6px; font-size: 16px; }}
    .meta {{ color: #6b7280; margin-bottom: 12px; }}
    details > summary {{
      cursor: pointer;
      font-weight: 600;
      color: #111827;
      margin: 6px 0 8px;
      list-style: none;
    }}
    details > summary::-webkit-details-marker {{ display: none; }}
  </style>
</head>
<body>
  <h2>セクターローテーション ダッシュボード</h2>
  <div class="meta">データ最終日: {data_last_date}（{business_days_ago}営業日前）</div>
  <div class="meta">ダッシュボード生成: {generated_at}</div>
  {cards_html}
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    webbrowser.open(f"file://{output_path.resolve()}")


def main() -> None:
    close, _volume, turnover, returns = sr.fetch_data()  # データ取得は1回だけ
    asof_date = close.index[-1].strftime("%Y-%m-%d")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        today = datetime.today().date()
        last = datetime.strptime(asof_date, "%Y-%m-%d").date()
        business_days_ago = max(0, (len(pd.bdate_range(last, today)) - 1))
    except Exception:
        business_days_ago = 0

    snapshots = build_snapshots(close=close, turnover=turnover, returns=returns)

    # Section 0: Market environment (separate yf.download)
    fig_mkt = build_section0_market_environment(asof_date=asof_date)

    # Section 1
    fig_heat = build_section1_heatmap(snapshots, asof_date=asof_date)

    # Section A (new): relative performance chart right after heatmap
    fig_rel = build_sectionA_sector_relative_performance(close=close, snapshots=snapshots)

    # Section 2
    fig_table = build_section2_alert_table(snapshots, asof_date=asof_date)

    # Section 3/4/5 small multiples
    b_z = calc_b_rs_acc_zscore(close)
    c_z = calc_c_beta_div_zscore(returns)
    a_z = calc_a_volume_share_zscore(turnover)

    fig_b = build_small_multiples(
        b_z,
        threshold=sr.RS_ZSCORE_THRESHOLD,
        section_title=f"RS加速度 z-score（過去3ヶ月, 閾値 ±{sr.RS_ZSCORE_THRESHOLD}σ）",
    )
    fig_c = build_small_multiples(
        c_z,
        threshold=sr.BETA_ZSCORE_THRESHOLD,
        section_title=f"ベータ乖離 z-score（過去3ヶ月, 閾値 ±{sr.BETA_ZSCORE_THRESHOLD}σ）",
    )
    fig_a = build_small_multiples(
        a_z,
        threshold=sr.A_ZSCORE_THRESHOLD,
        section_title=f"出来高シェア z-score（過去3ヶ月, 閾値 ±{sr.A_ZSCORE_THRESHOLD}σ）",
    )

    b_html = f"<details><summary>▶ RS加速度 z-score 詳細を表示</summary>{_to_card_html(fig_b, include_js=False)}</details>"
    c_html = f"<details><summary>▶ ベータ乖離 z-score 詳細を表示</summary>{_to_card_html(fig_c, include_js=False)}</details>"
    a_html = f"<details><summary>▶ 出来高シェア z-score 詳細を表示</summary>{_to_card_html(fig_a, include_js=False)}</details>"

    # Section B (new): Google Trends details
    fig_trends, trends_breakdown_html = build_sectionB_google_trends(asof_date=asof_date)
    trends_body = _to_card_html(fig_trends, include_js=False) + trends_breakdown_html

    # Section C (new): explanation panel (always at bottom)
    explain_body = build_sectionC_explanation_panel_html()

    sections: list[tuple[str, str]] = [
        ("マーケット環境", _to_card_html(fig_mkt, include_js=True)),
        (f"セクターローテーション ヒートマップ（{asof_date}）", _to_card_html(fig_heat, include_js=False)),
        ("セクター別株価チャート（対TOPIX相対パフォーマンス）", _to_card_html(fig_rel, include_js=False)),
        ("アラートサマリーテーブル", _to_card_html(fig_table, include_js=False)),
        ("Google Trends 詳細", trends_body),
        ("RS加速度 z-score（Small Multiples）", b_html),
        ("ベータ乖離 z-score（Small Multiples）", c_html),
        ("出来高シェア z-score（Small Multiples）", a_html),
        ("シグナル解説", explain_body),
    ]

    out = Path(__file__).resolve().parent / "dashboard.html"
    save_and_open_html(
        sections,
        out,
        data_last_date=asof_date,
        business_days_ago=int(business_days_ago),
        generated_at=generated_at,
    )
    print(f"生成完了: {out} (データ末日: {asof_date})")


if __name__ == "__main__":
    main()

