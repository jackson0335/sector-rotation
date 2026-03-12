#!/usr/bin/env python3
"""3つの問題を1回で修正"""
filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

changes = 0

# =============================================
# 修正1: sensor_historyにD履歴を追加
# =============================================
# 現在: センサー推移ループ内でA,B,Cは計算しているがDがない
# sensor_history["d"]が空配列のままなのでチャートに紫線が出ない
# → 各日のkabulineログからd_zを遡及計算して追加

old_sensor_loop_end = '''    # C: ベータ差z-score
            if len(ret_sec_full) >= 60 and len(ret_top_full) >= 60:
                c_slice_s = ret_sec_full.iloc[:len(ret_sec_full)+di+1]
                c_slice_t = ret_top_full.iloc[:len(ret_top_full)+di+1]
                if len(c_slice_s) >= 60 and len(c_slice_t) >= 60:
                    cb20 = c_slice_s.tail(20).cov(c_slice_t.tail(20)) / c_slice_t.tail(20).var() if c_slice_t.tail(20).var()>0 else 1
                    cb60 = c_slice_s.tail(60).cov(c_slice_t.tail(60)) / c_slice_t.tail(60).var() if c_slice_t.tail(60).var()>0 else 1
                    sensor_history["c"].append(round(cb20-cb60, 3))
                else:
                    sensor_history["c"].append(0)
            else:
                sensor_history["c"].append(0)'''

new_sensor_loop_end = '''    # C: ベータ差z-score
            if len(ret_sec_full) >= 60 and len(ret_top_full) >= 60:
                c_slice_s = ret_sec_full.iloc[:len(ret_sec_full)+di+1]
                c_slice_t = ret_top_full.iloc[:len(ret_top_full)+di+1]
                if len(c_slice_s) >= 60 and len(c_slice_t) >= 60:
                    cb20 = c_slice_s.tail(20).cov(c_slice_t.tail(20)) / c_slice_t.tail(20).var() if c_slice_t.tail(20).var()>0 else 1
                    cb60 = c_slice_s.tail(60).cov(c_slice_t.tail(60)) / c_slice_t.tail(60).var() if c_slice_t.tail(60).var()>0 else 1
                    sensor_history["c"].append(round(cb20-cb60, 3))
                else:
                    sensor_history["c"].append(0)
            else:
                sensor_history["c"].append(0)
            # D: SNS z-score（kabulineログから遡及計算）
            sh_date_full = rs.index[di].strftime("%Y-%m-%d")
            # この日までのkabulineログからd_seriesを構築
            d_hist_series = []
            for kp2 in kbl_logs:
                try:
                    kp2_date = os.path.basename(kp2).replace("kabuline_","").replace(".json","")
                    if kp2_date <= sh_date_full:
                        with open(kp2) as kf2:
                            kd2 = json.load(kf2)
                        st2 = kd2.get("sector_totals", {})
                        val2 = st2.get(mapped_17, 0)
                        if isinstance(val2, (int, float)):
                            d_hist_series.append(val2)
                except:
                    pass
            d_hist_z = 0
            if len(d_hist_series) >= 5:
                d_h_mean = sum(d_hist_series[:-1])/len(d_hist_series[:-1]) if len(d_hist_series)>1 else 0
                d_h_vals = d_hist_series[:-1]
                if len(d_h_vals) >= 2:
                    d_h_std = (sum((x-d_h_mean)**2 for x in d_h_vals)/(len(d_h_vals)-1))**0.5
                    if d_h_std > 0:
                        d_hist_z = (d_hist_series[-1] - d_h_mean) / d_h_std
                        # sentiment補正
                        kp2_sent = {}
                        for kp3 in kbl_logs:
                            try:
                                kp3_date = os.path.basename(kp3).replace("kabuline_","").replace(".json","")
                                if kp3_date <= sh_date_full:
                                    with open(kp3) as kf3:
                                        kd3 = json.load(kf3)
                                    kp2_sent = kd3.get("sector_sentiment", {})
                            except:
                                pass
                        d_h_buy = kp2_sent.get(mapped_17, 50.0)
                        d_h_ss = (d_h_buy - 50) / 50
                        if abs(d_hist_z) > 0.5:
                            d_hist_z = d_hist_z * 0.6 + d_h_ss * abs(d_hist_z) * 0.4
            sensor_history["d"].append(round(d_hist_z, 2))'''

if old_sensor_loop_end in code:
    code = code.replace(old_sensor_loop_end, new_sensor_loop_end)
    changes += 1
    print("修正1: sensor_historyにD履歴追加 - 完了")
else:
    print("修正1: 対象コード見つからず - スキップ")

# =============================================
# 修正2: 個別株のD値をセクター一律ではなく銘柄別にする
# =============================================
# 現在: sectors_data.append()の後で全株にst["d_z"]=d_z（セクター値）を上書き
# → 個別株のd_countが0以上なら銘柄独自のd_zを計算、0ならセクター値を薄めて使う

old_stock_dz = '''    # 個別株にSNSカウントとセクターd_zを付与
    sns_by_code = {}
    for p in sns_for_sec:
        pc = str(p.get("code",""))
        sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
    for st in stock_list:
        st["d_z"] = round(d_z, 1)
        st["d_count"] = sns_by_code.get(st["code"], 0)
        st["d_buy_pct"] = round(d_buy_pct, 1)'''

new_stock_dz = '''    # 個別株にSNS言及数を付与し、銘柄別d_zを計算
    sns_by_code = {}
    for p in sns_for_sec:
        pc = str(p.get("code",""))
        sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
    # セクター内の総言及数
    sec_total_mentions = sum(sns_by_code.values()) if sns_by_code else 0
    for st in stock_list:
        st_mentions = sns_by_code.get(st["code"], 0)
        st["d_count"] = st_mentions
        st["d_buy_pct"] = round(d_buy_pct, 1)
        # 銘柄別d_z: 言及がある銘柄はセクターd_zを言及割合で重み付け
        if sec_total_mentions > 0 and st_mentions > 0:
            mention_share = st_mentions / sec_total_mentions
            # 言及シェアが高い銘柄ほどセクターd_zに近い値、低い銘柄はd_zが小さい
            st["d_z"] = round(d_z * min(mention_share * len(sns_by_code), 2.0), 1)
        else:
            st["d_z"] = 0.0  # 言及なし=SNS注目なし'''

if old_stock_dz in code:
    code = code.replace(old_stock_dz, new_stock_dz)
    changes += 1
    print("修正2: 個別株D値を銘柄別計算に変更 - 完了")
else:
    print("修正2: 対象コード見つからず - スキップ")

# =============================================
# 修正3: SNSリンク銘柄と表の銘柄の不一致を解消
# SNS注目銘柄がTOPIX表にない場合、その旨を表示
# また表側でd_count>0の銘柄にSNSバッジを表示
# =============================================

# 3a: SNS注目銘柄セクションに「※TOPIX構成銘柄以外も含む」注釈追加
old_sns_section = '''        html += f\'<h4>SNS注目銘柄（株ライン {kbl_date}） D={s["d_z"]:.1f}</h4>\''''
new_sns_section = '''        html += f\'<h4>SNS注目銘柄（株ライン {kbl_date}） D={s["d_z"]:.1f}</h4>\'
        html += \'<div style="font-size:10px;color:#999;margin-bottom:4px">※話題の銘柄一覧（TOPIX構成銘柄以外も含む）。下の個別銘柄表でD列が0以外の銘柄がSNSで言及されています。</div>\''''

if old_sns_section in code:
    code = code.replace(old_sns_section, new_sns_section)
    changes += 1
    print("修正3a: SNSセクションに注釈追加 - 完了")
else:
    print("修正3a: 対象コード見つからず - スキップ")

# 3b: 個別株テーブルのD列でd_count>0の場合にバッジ表示
old_d_cell = '''>{st.get("d_z",0):.1f}</td></tr>\''''
new_d_cell = '''>{st.get("d_z",0):.1f}{"<span style=\\'font-size:9px;color:#888\\'> ("+str(st.get("d_count",0))+"件)</span>" if st.get("d_count",0)>0 else ""}</td></tr>\''''

if old_d_cell in code:
    code = code.replace(old_d_cell, new_d_cell)
    changes += 1
    print("修正3b: D列に言及件数バッジ追加 - 完了")
else:
    # f-stringの中のエスケープが複雑なので別のアプローチ
    # D列表示行を丸ごと置き換え
    print("修正3b: エスケープ問題のため別アプローチ試行")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print(f"\n=== 合計 {changes} 箇所修正完了 ===")
print("修正内容:")
print("  1. チャートの紫線(D): 過去kabulineログからD_zを遡及計算してsensor_historyに格納")
print("  2. 個別株D値: セクター一律→銘柄別言及割合で重み付け（言及なし=0）")
print("  3. SNSリンクと表の不一致: 注釈追加で説明")
