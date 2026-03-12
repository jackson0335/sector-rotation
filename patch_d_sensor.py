#!/usr/bin/env python3
"""integrated_dashboard.pyのDセンサー部分にsentimentを追加するパッチ"""
import re

filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

# 1. kabulineログ読み込み部分にsentiment追加
old_kbl = '''kbl_zscores = {}
if kbl_logs:
    with open(kbl_logs[-1]) as f:'''

new_kbl = '''kbl_zscores = {}
kbl_sentiment = {}
if kbl_logs:
    with open(kbl_logs[-1]) as f:'''

code = code.replace(old_kbl, new_kbl)

old_kbl2 = '''        kbl_zscores = kdata["zscores"]'''
new_kbl2 = '''        kbl_zscores = kdata["zscores"]
    kbl_sentiment = kdata.get("sector_sentiment", {})'''

code = code.replace(old_kbl2, new_kbl2)

# 2. Dセンサー計算部分を差し替え
old_d = '''    # === センサーD: SNS（kabuline sector_totalsから自前z-score計算） ===
    d_z = 0
    d_count = 0
    sns_for_sec = []
    mapped_17 = map_17_to_33.get(sec, "")
    # 過去30日分のsector_totalsを収集
    d_series = []
    for kp in kbl_logs[-30:]:
        try:
            with open(kp) as kf:
                kd = json.load(kf)
            st = kd.get("sector_totals", {})
            val = st.get(mapped_17, 0)
            if isinstance(val, (int, float)):
                d_series.append(val)
        except:
            pass
    if d_series:
        d_count = d_series[-1] if d_series else 0
        if len(d_series) >= 5:
            import statistics
            d_mean = statistics.mean(d_series[:-1]) if len(d_series) > 1 else 0
            d_std = statistics.stdev(d_series[:-1]) if len(d_series) > 2 else 0
            if d_std > 0:
                d_z = (d_series[-1] - d_mean) / d_std'''

new_d = '''    # === センサーD: SNS（kabuline sector_totals + sentiment） ===
    d_z = 0
    d_count = 0
    d_buy_pct = 50.0
    sns_for_sec = []
    mapped_17 = map_17_to_33.get(sec, "")
    # 過去30日分のsector_totalsを収集
    d_series = []
    for kp in kbl_logs[-30:]:
        try:
            with open(kp) as kf:
                kd = json.load(kf)
            st = kd.get("sector_totals", {})
            val = st.get(mapped_17, 0)
            if isinstance(val, (int, float)):
                d_series.append(val)
        except:
            pass
    # 言及数z-score
    mention_z = 0
    if d_series:
        d_count = d_series[-1] if d_series else 0
        if len(d_series) >= 5:
            import statistics
            d_mean = statistics.mean(d_series[:-1]) if len(d_series) > 1 else 0
            d_std = statistics.stdev(d_series[:-1]) if len(d_series) > 2 else 0
            if d_std > 0:
                mention_z = (d_series[-1] - d_mean) / d_std
    # 売買感情スコア（買%を-1〜+1に変換: 50%=0, 70%=+0.4, 30%=-0.4）
    sentiment_score = 0
    d_buy_pct = kbl_sentiment.get(mapped_17, 50.0)
    sentiment_score = (d_buy_pct - 50) / 50  # -1 to +1
    # D_z = 言及数z * 0.6 + 感情スコア * 言及z補正 * 0.4
    if abs(mention_z) > 0.5:
        d_z = mention_z * 0.6 + sentiment_score * abs(mention_z) * 0.4
    else:
        d_z = mention_z'''

code = code.replace(old_d, new_d)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print("パッチ適用完了")
print("変更点:")
print("  1. kbl_sentiment読み込み追加")
print("  2. D_z = 言及数z*0.6 + 感情スコア*言及z補正*0.4")
print("  3. 言及数が少ない時(z<0.5)は感情を無視（ノイズ回避）")
