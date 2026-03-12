#!/usr/bin/env python3
"""Dセンサーを465銘柄ベースに修正。buy_pctがある銘柄のみ判定、それ以外はD=0"""
filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

# === 修正1: kbl_stocksの読み込みを追加（kbl_sentiment読み込みの直後） ===
old_kbl_load = '''    kbl_sentiment = kdata.get("sector_sentiment", {})
    print(f"  kabuline: {len(kbl_logs)}日分, 最新{os.path.basename(kbl_logs[-1])}")'''

new_kbl_load = '''    kbl_sentiment = kdata.get("sector_sentiment", {})
    kbl_stocks = kdata.get("stocks", [])  # 465銘柄個別データ
    print(f"  kabuline: {len(kbl_logs)}日分, 最新{os.path.basename(kbl_logs[-1])}")'''

code = code.replace(old_kbl_load, new_kbl_load)

# kbl_stocks初期値も追加（kbl_sentiment = {} の後）
old_kbl_init = '''kbl_sentiment = {}
if kbl_logs:'''
new_kbl_init = '''kbl_sentiment = {}
kbl_stocks = []
if kbl_logs:'''
code = code.replace(old_kbl_init, new_kbl_init)

# === 修正2: Dセンサー計算を丸ごと差し替え ===
old_d_sensor = '''    # === センサーD: SNS（kabuline sector_totals + sentiment） ===
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
        d_z = mention_z
    # SNS投稿（raw内の銘柄をセクターマッチ）
    # 33業種名と17業種名の両方でマッチ
    map_33_to_17 = {v:k for k,v in map_17_to_33.items()}
    sec_17 = mapped_17  # この33業種に対応する17業種名
    for p in kbl_posts:
        p_sec = p.get("sector", "")
        # 銘柄コードからセクターを引くパターンも試す
        p_code = str(p.get("code", ""))
        p_in_sector = any(p_code == c for c, n in sector_stocks.get(sec, []))
        if p_sec == sec or p_sec == sec_17 or p_in_sector:
            sns_for_sec.append(p)'''

new_d_sensor = '''    # === センサーD: SNS（465銘柄個別データからbuy_pctがある銘柄のみ） ===
    d_z = 0
    d_count = 0
    d_buy_pct = 50.0
    sns_for_sec = []
    # このセクターに属する銘柄のうちbuy_pctがある銘柄だけ抽出
    sec_codes = set(c for c, n in sector_stocks.get(sec, []))
    d_stocks_with_sentiment = []
    for ks in kbl_stocks:
        if ks.get("code","") in sec_codes and ks.get("buy_pct") is not None:
            d_stocks_with_sentiment.append(ks)
            sns_for_sec.append(ks)
    # センチメントがある銘柄が1つ以上あればD値を計算
    if d_stocks_with_sentiment:
        d_count = sum(s.get("tweet_count", 0) for s in d_stocks_with_sentiment)
        buy_vals = [s["buy_pct"] for s in d_stocks_with_sentiment]
        d_buy_pct = sum(buy_vals) / len(buy_vals)
        # 買率を-1〜+1に変換（50%=0, 70%=+0.4, 30%=-0.4）
        d_z = (d_buy_pct - 50) / 25  # -2〜+2のレンジ
        d_z = max(-5, min(5, d_z))  # クリップ'''

code = code.replace(old_d_sensor, new_d_sensor)

# === 修正3: 個別株のD値もbuy_pctベースに修正 ===
old_stock_d = '''    # 個別株にSNSカウントとセクターd_zを付与
    sns_by_code = {}
    for p in sns_for_sec:
        pc = str(p.get("code",""))
        sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
    for st in stock_list:
        st["d_z"] = round(d_z, 1)
        st["d_count"] = sns_by_code.get(st["code"], 0)
        st["d_buy_pct"] = round(d_buy_pct, 1)'''

new_stock_d = '''    # 個別株のD値: buy_pctがある銘柄のみ算出、なければ0
    kbl_stock_map = {ks["code"]: ks for ks in kbl_stocks if ks.get("buy_pct") is not None}
    for st in stock_list:
        ks = kbl_stock_map.get(st["code"])
        if ks:
            st["d_z"] = round((ks["buy_pct"] - 50) / 25, 1)
            st["d_count"] = ks.get("tweet_count", 0)
            st["d_buy_pct"] = ks["buy_pct"]
        else:
            st["d_z"] = 0.0
            st["d_count"] = 0
            st["d_buy_pct"] = 0.0'''

code = code.replace(old_stock_d, new_stock_d)

# === 修正4: SNSリンクをbuy_pctがある銘柄のみに制限 ===
old_sns_link = '''    if s["sns_posts"]:
        html += f\'<h4>SNS注目銘柄（株ライン {kbl_date}） D={s["d_z"]:.1f}</h4>\'
        html += \'<div style="font-size:10px;color:#999;margin-bottom:4px">※話題の銘柄一覧（TOPIX構成銘柄以外も含む）。下の個別銘柄表でD列が0以外の銘柄がSNSで言及されています。</div>\'
        for p in s["sns_posts"][:5]:
            code = p.get("code","")
            count = p.get("count",0)
            name_lookup = {c: n for c, n in sector_stocks.get(sec, [])}
            sname = name_lookup.get(str(code), "")
            html += f\'<div style="padding:4px 0;font-size:12px"><a href="https://kabuline.com/stock/code/{code}/" target="_blank" class="sns-link">{code} {sname}</a> {count}件</div>\''''

new_sns_link = '''    if s["sns_posts"]:
        html += f\'<h4>SNS注目銘柄（株ライン {kbl_date}） D={s["d_z"]:.1f}</h4>\'
        for p in s["sns_posts"][:5]:
            pcode = p.get("code","")
            pcount = p.get("tweet_count",0)
            pbuy = p.get("buy_pct","")
            name_lookup = {c: n for c, n in sector_stocks.get(sec, [])}
            sname = name_lookup.get(str(pcode), "")
            buy_str = f" 買{pbuy}%" if pbuy else ""
            html += f\'<div style="padding:4px 0;font-size:12px"><a href="https://kabuline.com/stock/code/{pcode}/" target="_blank" class="sns-link">{pcode} {sname}</a> {pcount}件{buy_str}</div>\''''

code = code.replace(old_sns_link, new_sns_link)

# === 修正5: sensor_historyのD履歴もbuy_pctベースに修正 ===
# 既存のD履歴計算（前パッチで追加したもの）を簡潔に差し替え
# D履歴は過去ログにstocksデータがないため0で埋める（今日以降蓄積される）
old_d_hist = '''            # D: SNS z-score（kabulineログから遡及計算）
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

new_d_hist = '''            # D: SNS（過去ログにstocksデータがないため今日以降蓄積）
            sensor_history["d"].append(0)'''

code = code.replace(old_d_hist, new_d_hist)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

# 検証
checks = [
    ("kbl_stocks読み込み", "kbl_stocks = kdata.get" in code),
    ("新Dセンサー", "d_stocks_with_sentiment" in code),
    ("個別株D", "kbl_stock_map" in code),
    ("SNSリンク修正", "tweet_count" in code),
    ("D履歴簡潔化", 'sensor_history["d"].append(0)' in code),
    ("旧sector_totals参照なし", "st.get(mapped_17, 0)" not in code or "sector_totals" not in code.split("センサーD")[1] if "センサーD" in code else True),
]
print("=== 検証 ===")
all_ok = True
for name, ok in checks:
    status = "OK" if ok else "NG"
    if not ok: all_ok = False
    print(f"  {name}: {status}")
print(f"\n{'全修正完了' if all_ok else '一部失敗あり - 確認必要'}")
