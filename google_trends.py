#!/usr/bin/env python3
"""
セクター関心度指数 v0.1
Google Trendsベースのセクター別検索トレンド監視
"""

from pytrends.request import TrendReq
import pandas as pd
import numpy as np
import json
import os
import time
import urllib.request
from datetime import datetime

# ============================================================
# セクター別キーワード辞書
# ============================================================

SECTOR_KEYWORDS = {
    '食品': ['食品株', '食料品 株価', 'ディフェンシブ 食品'],
    'エネルギー資源': ['エネルギー株', '原油 株', '石油 株価', 'INPEX'],
    '建設・資材': ['建設株', 'ゼネコン 株価', '建設 受注'],
    '素材・化学': ['化学 株価', '素材 株', '信越化学'],
    '医薬品': ['製薬 株価', '医薬品株', 'バイオ 株'],
    '自動車・輸送機': ['自動車株', 'トヨタ 株価', 'EV 株'],
    '鉄鋼・非鉄': ['鉄鋼株', '日本製鉄 株価', '非鉄 株'],
    '機械': ['機械株', '設備投資 株', 'ファナック 株価'],
    '電機・精密': ['半導体株', 'AI 半導体', '電機 株価', 'ソニー 株価'],
    '情報通信・サービスその他': ['IT株', '情報通信 株価', 'SaaS 株', 'DX 株'],
    '電気・ガス': ['電力株', '電気 ガス 株価', '公益 株'],
    '運輸・物流': ['運輸株', '鉄道 株価', '物流 株', 'JR 株'],
    '商社・卸売': ['商社株', '総合商社 株価', '三菱商事 株価', 'バフェット 商社'],
    '小売': ['小売株', '百貨店 株価', 'インバウンド 小売'],
    '銀行': ['銀行株', 'メガバンク 株価', '金利上昇 銀行', '利ざや 改善'],
    '金融（除く銀行）': ['証券株', '保険株', 'リース 株価', '金融 株'],
    '不動産': ['不動産株', 'REIT', '地価 上昇', 'マンション 株'],
}

SLACK_WEBHOOK_URL = 'SLACK_WEBHOOK_URL
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
ZSCORE_THRESHOLD = 1.5

# ============================================================
# Google Trends データ取得
# ============================================================

def fetch_trends():
    pytrends = TrendReq(hl='ja-JP', tz=540)
    all_results = {}

    for sector, keywords in SECTOR_KEYWORDS.items():
        print(f"  取得中: {sector}...")
        sector_scores = []

        for kw in keywords:
            try:
                pytrends.build_payload([kw], timeframe='today 3-m', geo='JP')
                data = pytrends.interest_over_time()
                if not data.empty and kw in data.columns:
                    sector_scores.append(data[kw])
                time.sleep(15)  # レート制限対策
            except Exception as e:
                print(f"    スキップ: {kw} ({e})")
                time.sleep(30)
                continue

        if sector_scores:
            combined = pd.concat(sector_scores, axis=1)
            all_results[sector] = combined.mean(axis=1)
        else:
            print(f"    警告: {sector} のデータ取得失敗")

    if all_results:
        df = pd.DataFrame(all_results)
        return df
    else:
        return pd.DataFrame()

# ============================================================
# 関心度異常検知
# ============================================================

def calc_trend_signals(df):
    if df.empty:
        return []

    # 直近値と過去平均の比較
    # Google Trendsは週次データなので、直近4週 vs その前の8週で比較
    results = []

    for sector in df.columns:
        series = df[sector].dropna()
        if len(series) < 12:
            continue

        recent = series.iloc[-4:].mean()   # 直近4週の平均
        baseline = series.iloc[-12:-4].mean()  # その前8週の平均
        baseline_std = series.iloc[-12:-4].std()

        if baseline_std > 0:
            z = (recent - baseline) / baseline_std
        else:
            z = 0.0

        pct_change = ((recent - baseline) / baseline * 100) if baseline > 0 else 0.0

        signal = '-'
        if z > ZSCORE_THRESHOLD:
            signal = '関心度急上昇'
        elif z < -ZSCORE_THRESHOLD:
            signal = '関心度急低下'

        results.append({
            'セクター': sector,
            '直近4週平均': round(recent, 1),
            '基準8週平均': round(baseline, 1),
            'z_score': round(z, 2),
            '変化率': round(pct_change, 1),
            'シグナル': signal,
            'alert': signal != '-',
        })

    return results

# ============================================================
# 表示
# ============================================================

def print_results(results, date_str):
    W = 62
    print(f"\n{'=' * W}")
    print(f"  セクター関心度指数（Google Trends）")
    print(f"  {date_str}")
    print(f"{'=' * W}\n")

    df = pd.DataFrame(results).sort_values('z_score', ascending=False)

    for _, row in df.iterrows():
        mark = '🔔' if row['alert'] else '  '
        print(f"  {row['セクター']:<14} z={row['z_score']:>5}  変化率={row['変化率']:>6}%  {mark} {row['シグナル']}")

    alerts = df[df['alert']]
    print(f"\n{'=' * W}")
    if len(alerts) > 0:
        print(f"  関心度アラート: {len(alerts)}件")
        for _, row in alerts.iterrows():
            print(f"    → {row['セクター']}: {row['シグナル']} (z={row['z_score']}, 変化率={row['変化率']}%)")
    else:
        print("  関心度アラートはありません。")
    print(f"{'=' * W}")

    return df

# ============================================================
# Slack通知
# ============================================================

def send_slack(results, date_str):
    df = pd.DataFrame(results).sort_values('z_score', ascending=False)
    alerts = df[df['alert']]

    lines = [f"*セクター関心度（Google Trends）* ({date_str})\n"]

    if len(alerts) > 0:
        for _, row in alerts.iterrows():
            emoji = '🔺' if row['z_score'] > 0 else '🔻'
            lines.append(f"  {emoji} {row['セクター']}: {row['シグナル']} (z={row['z_score']}, {row['変化率']}%)")
    else:
        lines.append("関心度に異常なし。")

    # 上位3セクターと下位3セクターも常に表示
    lines.append(f"\n*関心度 上位3:*")
    for _, row in df.head(3).iterrows():
        lines.append(f"  {row['セクター']}: z={row['z_score']}, {row['変化率']}%")

    lines.append(f"\n*関心度 下位3:*")
    for _, row in df.tail(3).iterrows():
        lines.append(f"  {row['セクター']}: z={row['z_score']}, {row['変化率']}%")

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

# ============================================================
# ログ保存
# ============================================================

def save_log(results, date_str):
    os.makedirs(LOG_DIR, exist_ok=True)
    filepath = os.path.join(LOG_DIR, f"trends_{date_str}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"ログ保存: {filepath}")

# ============================================================
# メイン
# ============================================================

def main():
    date_str = datetime.today().strftime('%Y-%m-%d')
    print("Google Trends データ取得開始...")
    df = fetch_trends()

    if df.empty:
        print("データ取得に失敗しました。")
        return

    results = calc_trend_signals(df)
    print_results(results, date_str)
    save_log(results, date_str)
    send_slack(results, date_str)

if __name__ == '__main__':
    main()
