#!/usr/bin/env python3
"""
セクターローテーション感知システム v0.2
Slack通知対応
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import json
import os
import urllib.request

warnings.filterwarnings('ignore')

SECTOR_ETFS = {
    '1617.T': '食品',
    '1618.T': 'エネルギー資源',
    '1619.T': '建設・資材',
    '1620.T': '素材・化学',
    '1621.T': '医薬品',
    '1622.T': '自動車・輸送機',
    '1623.T': '鉄鋼・非鉄',
    '1624.T': '機械',
    '1625.T': '電機・精密',
    '1626.T': '情報通信・サービスその他',
    '1627.T': '電気・ガス',
    '1628.T': '運輸・物流',
    '1629.T': '商社・卸売',
    '1630.T': '小売',
    '1631.T': '銀行',
    '1632.T': '金融（除く銀行）',
    '1633.T': '不動産',
}

TOPIX_ETF = '1306.T'
SLACK_WEBHOOK_URL = 'SLACK_WEBHOOK_URL

RS_SHORT_WINDOW = 5
RS_LONG_WINDOW = 20
RS_TREND_WINDOW = 60
RS_ZSCORE_WINDOW = 60
RS_ZSCORE_THRESHOLD = 1.5
# B_OUT 専用の閾値（将来的に辞書化予定）
RS_ZSCORE_THRESHOLD_OUT = 2.5

BETA_SHORT_WINDOW = 20
BETA_LONG_WINDOW = 60
BETA_ZSCORE_WINDOW = 60
BETA_ZSCORE_THRESHOLD = 2.0

A_ZSCORE_WINDOW = 60
A_ZSCORE_THRESHOLD = 2.0
A_PERSISTENCE_DAYS = 3

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# バックテストで「効く」と判定されたセクター（名前ベース）
EFFECTIVE_SECTOR_NAMES = {
    '鉄鋼・非鉄',
    '機械',
    '銀行',
    '電機・精密',
    '運輸・物流',
    '小売',
    '商社・卸売',
    'エネルギー資源',
    '電気・ガス',
    '建設・資材',
}

def fetch_data():
    end_date = datetime.today()
    start_date = end_date - timedelta(days=400)
    tickers = list(SECTOR_ETFS.keys()) + [TOPIX_ETF]
    print("データ取得中...")
    raw = yf.download(tickers, start=start_date, end=end_date, progress=False)
    close = raw['Close'][tickers].dropna()
    volume = raw['Volume'][tickers].dropna()
    common_idx = close.index.intersection(volume.index)
    close = close.loc[common_idx]
    volume = volume.loc[common_idx]
    turnover = close * volume
    returns = close.pct_change().dropna()
    print(f"取得完了: {close.index[0].strftime('%Y-%m-%d')} - {close.index[-1].strftime('%Y-%m-%d')} ({len(close)}日)")
    return close, volume, turnover, returns

def calc_a_signals(turnover):
    sector_tickers = list(SECTOR_ETFS.keys())
    total = turnover[sector_tickers].sum(axis=1)
    share = turnover[sector_tickers].div(total, axis=0)
    share_mean = share.rolling(A_ZSCORE_WINDOW).mean()
    share_std = share.rolling(A_ZSCORE_WINDOW).std()
    share_zscore = (share - share_mean) / share_std
    share_zscore_avg = share_zscore.rolling(A_PERSISTENCE_DAYS).mean()
    results = {}
    for ticker in sector_tickers:
        z = share_zscore_avg[ticker].iloc[-1]
        results[ticker] = {
            'z_score': round(z, 2) if not np.isnan(z) else 0.0,
            'alert': bool(abs(z) > A_ZSCORE_THRESHOLD) if not np.isnan(z) else False,
        }
    return results

def calc_b_signals(close):
    sector_tickers = list(SECTOR_ETFS.keys())
    rs = pd.DataFrame()
    for ticker in sector_tickers:
        rs[ticker] = close[ticker] / close[TOPIX_ETF]
    rs_momentum = rs.pct_change(RS_SHORT_WINDOW)
    rs_acceleration = rs_momentum - rs_momentum.shift(RS_LONG_WINDOW)
    rs_ma = rs.rolling(RS_TREND_WINDOW).mean()
    rs_trend = rs_ma.pct_change(RS_LONG_WINDOW)
    acc_mean = rs_acceleration.rolling(RS_ZSCORE_WINDOW).mean()
    acc_std = rs_acceleration.rolling(RS_ZSCORE_WINDOW).std()
    acc_zscore = (rs_acceleration - acc_mean) / acc_std
    results = {}
    for ticker in sector_tickers:
        z = acc_zscore[ticker].iloc[-1]
        trend = rs_trend[ticker].iloc[-1]
        acc = rs_acceleration[ticker].iloc[-1]
        signal = '-'
        if not np.isnan(z) and not np.isnan(trend):
            if trend < 0 and acc > 0 and z > RS_ZSCORE_THRESHOLD:
                signal = 'ROTATION IN'
            elif trend > 0 and acc < 0 and z < -RS_ZSCORE_THRESHOLD_OUT:
                signal = 'ROTATION OUT'
        results[ticker] = {
            'z_score': round(z, 2) if not np.isnan(z) else 0.0,
            'trend': round(trend, 4) if not np.isnan(trend) else 0.0,
            'signal': signal,
            'alert': signal != '-',
        }
    return results

def calc_c_signals(returns):
    sector_tickers = list(SECTOR_ETFS.keys())
    topix_ret = returns[TOPIX_ETF]
    def rolling_beta(sector_ret, market_ret, window):
        cov = sector_ret.rolling(window).cov(market_ret)
        var = market_ret.rolling(window).var()
        return cov / var
    beta_short = pd.DataFrame()
    beta_long = pd.DataFrame()
    for ticker in sector_tickers:
        beta_short[ticker] = rolling_beta(returns[ticker], topix_ret, BETA_SHORT_WINDOW)
        beta_long[ticker] = rolling_beta(returns[ticker], topix_ret, BETA_LONG_WINDOW)
    divergence = beta_short - beta_long
    div_mean = divergence.rolling(BETA_ZSCORE_WINDOW).mean()
    div_std = divergence.rolling(BETA_ZSCORE_WINDOW).std()
    div_zscore = (divergence - div_mean) / div_std
    results = {}
    for ticker in sector_tickers:
        z = div_zscore[ticker].iloc[-1]
        bs = beta_short[ticker].iloc[-1]
        bl = beta_long[ticker].iloc[-1]
        signal = '-'
        if not np.isnan(z):
            if z > BETA_ZSCORE_THRESHOLD:
                signal = 'ベータ上方異常'
            elif z < -BETA_ZSCORE_THRESHOLD:
                signal = 'ベータ下方異常'
        results[ticker] = {
            'z_score': round(z, 2) if not np.isnan(z) else 0.0,
            'beta_short': round(bs, 3) if not np.isnan(bs) else 0.0,
            'beta_long': round(bl, 3) if not np.isnan(bl) else 0.0,
            'signal': signal,
            'alert': signal != '-',
        }
    return results


def calc_combo2_today(close, returns):
    """
    COMBO_2（逆張り買い候補）の判定。
    条件: 同一セクターで B_OUT と C_DOWN が同日〜3日以内に発火している場合。
    B_OUT: RS加速度z < -RS_ZSCORE_THRESHOLD_OUT, trend>0, acc<0
    C_DOWN: β乖離z < -BETA_ZSCORE_THRESHOLD
    """
    sector_tickers = list(SECTOR_ETFS.keys())

    # B_OUT 系列
    rs = pd.DataFrame()
    for ticker in sector_tickers:
        rs[ticker] = close[ticker] / close[TOPIX_ETF]
    rs_momentum = rs.pct_change(RS_SHORT_WINDOW)
    rs_acceleration = rs_momentum - rs_momentum.shift(RS_LONG_WINDOW)
    rs_ma = rs.rolling(RS_TREND_WINDOW).mean()
    rs_trend = rs_ma.pct_change(RS_LONG_WINDOW)
    acc_mean = rs_acceleration.rolling(RS_ZSCORE_WINDOW).mean()
    acc_std = rs_acceleration.rolling(RS_ZSCORE_WINDOW).std()
    acc_zscore = (rs_acceleration - acc_mean) / acc_std

    # C_DOWN 系列
    topix_ret = returns[TOPIX_ETF]

    def rolling_beta(sector_ret, market_ret, window):
        cov = sector_ret.rolling(window).cov(market_ret)
        var = market_ret.rolling(window).var()
        return cov / var

    beta_short = pd.DataFrame()
    beta_long = pd.DataFrame()
    for ticker in sector_tickers:
        beta_short[ticker] = rolling_beta(returns[ticker], topix_ret, BETA_SHORT_WINDOW)
        beta_long[ticker] = rolling_beta(returns[ticker], topix_ret, BETA_LONG_WINDOW)
    divergence = beta_short - beta_long
    div_mean = divergence.rolling(BETA_ZSCORE_WINDOW).mean()
    div_std = divergence.rolling(BETA_ZSCORE_WINDOW).std()
    div_zscore = (divergence - div_mean) / div_std

    if len(rs.index) == 0:
        return {}

    # 過去4営業日（同日〜3日前）
    idx_window = rs.index[-4:]
    combo2 = {}
    for ticker in sector_tickers:
        # B_OUT 条件
        z_b = acc_zscore[ticker].loc[idx_window]
        trend = rs_trend[ticker].loc[idx_window]
        acc = rs_acceleration[ticker].loc[idx_window]
        b_mask = (trend > 0) & (acc < 0) & (z_b < -RS_ZSCORE_THRESHOLD_OUT)

        # C_DOWN 条件
        z_c = div_zscore[ticker].loc[idx_window]
        c_mask = z_c < -BETA_ZSCORE_THRESHOLD

        has_combo = bool(b_mask.any() and c_mask.any())
        if not has_combo:
            combo2[ticker] = {'active': False}
            continue

        # それぞれの条件を満たした中で最新のzを取る
        b_z_val = float(z_b[b_mask].iloc[-1]) if b_mask.any() else float('nan')
        c_z_val = float(z_c[c_mask].iloc[-1]) if c_mask.any() else float('nan')
        combo2[ticker] = {
            'active': True,
            'B_z_combo': round(b_z_val, 2) if not np.isnan(b_z_val) else None,
            'C_z_combo': round(c_z_val, 2) if not np.isnan(c_z_val) else None,
        }
    return combo2

def build_dashboard(a_signals, b_signals, c_signals, combo2_info):
    sector_tickers = list(SECTOR_ETFS.keys())
    rows = []
    for ticker in sector_tickers:
        name = SECTOR_ETFS[ticker]
        a = a_signals[ticker]
        b = b_signals[ticker]
        c = c_signals[ticker]
        combo2 = combo2_info.get(ticker, {}) if combo2_info else {}
        combo2_active = bool(combo2.get('active'))
        count = sum([a['alert'], b['alert'], c['alert']])
        if combo2_active:
            confidence = '🔴 最高'
        elif count >= 3:
            confidence = '🔴 最高'
        elif count == 2:
            confidence = '🟠 高'
        elif count == 1:
            confidence = '🟡 中'
        else:
            confidence = '⚪ -'
        rows.append({
            'セクター': name, 'ticker': ticker,
            'A_z': a['z_score'], 'A_alert': a['alert'],
            'B_z': b['z_score'], 'B_signal': b['signal'], 'B_alert': b['alert'],
            'C_z': c['z_score'], 'C_signal': c['signal'], 'C_alert': c['alert'],
            'confidence': confidence, 'alert_count': count,
            'COMBO2': combo2_active,
            'COMBO2_B_z': combo2.get('B_z_combo'),
            'COMBO2_C_z': combo2.get('C_z_combo'),
        })
    df = pd.DataFrame(rows).sort_values('alert_count', ascending=False)
    return df

def print_dashboard(df, date_str):
    W = 62
    print(f"\n{'=' * W}")
    print(f"  セクターローテーション感知ダッシュボード")
    print(f"  {date_str}")
    print(f"{'=' * W}\n")
    header = f"{'セクター':<14} {'A_z':>5} {'A':>2} {'B_z':>5} {'B':>2} {'C_z':>5} {'C':>2} {'確信度'}"
    print(header)
    print('-' * W)
    for _, row in df.iterrows():
        sector_name = row['セクター']
        effective = sector_name in EFFECTIVE_SECTOR_NAMES
        # B_IN かつ非効率セクターなら表示上はフィルタ（ログでは保持）
        b_alert_display = row['B_alert']
        if row['B_signal'] == 'ROTATION IN' and not effective:
            b_alert_display = False
        a_mark = '🔔' if row['A_alert'] else '  '
        b_mark = '🔔' if b_alert_display else '  '
        c_mark = '🔔' if row['C_alert'] else '  '
        line = f"{sector_name:<14} {row['A_z']:>5} {a_mark} {row['B_z']:>5} {b_mark} {row['C_z']:>5} {c_mark} {row['confidence']}"
        print(line)
    print(f"\n{'=' * W}")
    print(f"  アラートサマリー")
    print(f"{'=' * W}")
    found = False
    for level in ['🔴 最高', '🟠 高', '🟡 中']:
        subset = df[df['confidence'] == level]
        if len(subset) > 0:
            found = True
            print(f"\n{level}:")
            for _, row in subset.iterrows():
                details = []
                if row['A_alert']:
                    details.append(f"出来高z={row['A_z']}")
                # B_IN のうち非効率セクターはサマリーから除外
                if row['B_alert'] and not (row['B_signal'] == 'ROTATION IN' and row['セクター'] not in EFFECTIVE_SECTOR_NAMES):
                    details.append(f"RS: {row['B_signal']}")
                if row['C_alert']:
                    details.append(f"beta: {row['C_signal']}")
                if row.get('COMBO2'):
                    details.append("COMBO_2 逆張り買い候補")
                print(f"  {row['セクター']}: {', '.join(details)}")
    if not found:
        print("\n  本日のアラートはありません。")
    print(f"\n{'=' * W}")

def save_log(df, date_str):
    os.makedirs(LOG_DIR, exist_ok=True)
    filepath = os.path.join(LOG_DIR, f"signal_{date_str}.json")
    records = []
    for _, row in df.iterrows():
        records.append({
            'date': date_str, 'sector': row['セクター'], 'ticker': row['ticker'],
            'A_z': row['A_z'], 'A_alert': row['A_alert'],
            'B_z': row['B_z'], 'B_signal': row['B_signal'],
            'C_z': row['C_z'], 'C_signal': row['C_signal'],
            'confidence': row['confidence'], 'alert_count': row['alert_count'],
            'COMBO2': bool(row.get('COMBO2', False)),
        })
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"\nログ保存: {filepath}")

def send_slack(df, date_str):
    lines = [f"*セクターローテーション感知* ({date_str})\n"]
    found = False
    filtered_notes = []
    combo2_lines = []

    for level in ['🔴 最高', '🟠 高', '🟡 中']:
        subset = df[df['confidence'] == level]
        if len(subset) > 0:
            found = True
            lines.append(f"\n*{level}*")
            for _, row in subset.iterrows():
                sector_name = row['セクター']
                details = []
                if row['A_alert']:
                    details.append(f"出来高z={row['A_z']}")
                # B_IN のうち非効率セクターは本文から除外し、別途フィルタ注記へ
                if row['B_alert']:
                    if row['B_signal'] == 'ROTATION IN' and sector_name not in EFFECTIVE_SECTOR_NAMES:
                        filtered_notes.append(f"{sector_name} B_IN (非対象セクター)")
                    else:
                        details.append(f"RS: {row['B_signal']}")
                if row['C_alert']:
                    details.append(f"β: {row['C_signal']}")
                lines.append(f"  • {sector_name}: {', '.join(details) if details else '-'}")

                # COMBO_2 詳細行（確信度は自動的に🔴）
                if row.get('COMBO2'):
                    b_z_combo = row.get('COMBO2_B_z')
                    c_z_combo = row.get('COMBO2_C_z')
                    combo_text = (
                        f"🔴 {sector_name} COMBO_2 逆張り買い候補\n"
                        f"   B_OUT z={b_z_combo:.2f} + C_DOWN z={c_z_combo:.2f}\n"
                        "   バックテスト実績: 勝率75%, 平均超過リターン+2.96% (20日, n=4)"
                    )
                    combo2_lines.append(combo_text)

    if not found:
        lines.append("本日のアラートはありません。")

    if combo2_lines:
        lines.append("\n*COMBO_2 シグナル詳細*")
        lines.extend(combo2_lines)

    if filtered_notes:
        uniq = sorted(set(filtered_notes))
        lines.append("\n_※ フィルタ除外: " + ", ".join(uniq) + "_")
    text = '\n'.join(lines)
    payload = json.dumps({'text': text}).encode('utf-8')
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req)
        print("Slack通知送信完了")
    except Exception as e:
        print(f"Slack通知エラー: {e}")

def main():
    close, volume, turnover, returns = fetch_data()
    date_str = close.index[-1].strftime('%Y-%m-%d')
    a_signals = calc_a_signals(turnover)
    b_signals = calc_b_signals(close)
    c_signals = calc_c_signals(returns)
    combo2_info = calc_combo2_today(close, returns)
    df = build_dashboard(a_signals, b_signals, c_signals, combo2_info)
    print_dashboard(df, date_str)
    save_log(df, date_str)
    send_slack(df, date_str)

if __name__ == '__main__':
    main()
