#!/usr/bin/env python3
"""統合ダッシュボード v9 - 33業種・全銘柄・初見対応"""
import json, os, glob, datetime as dt, warnings
warnings.filterwarnings("ignore")
import pandas as pd
import numpy as np
import yfinance as yf

# === 設定 ===
LOG_DIR = os.path.expanduser("~/sector_rotation/logs")
OUT_HTML = os.path.expanduser("~/sector_rotation/dashboard_integrated.html")
JPX_FILE = os.path.expanduser("~/sector_rotation/jpx_list.csv")
TOPIX_ETF = "1306.T"
WEBHOOK = os.environ.get("SLACK_WEBHOOK", "SLACK_WEBHOOK_URL")
os.makedirs(LOG_DIR, exist_ok=True)

# === 33業種マスタ読み込み ===
print("33業種マスタ読み込み...")
jpx = pd.read_excel(JPX_FILE, engine="xlrd")
jpx = jpx[(jpx["33業種コード"] != "-") & (~jpx["市場・商品区分"].str.contains("ETF|ETN", na=False))].copy()
jpx = jpx[jpx["規模区分"].isin(["TOPIX Large70","TOPIX Mid400"])]
jpx["33業種コード"] = jpx["33業種コード"].astype(str)
print(f"  Large70+Mid400: {len(jpx)}銘柄")

# 規模ソート
size_map = {"TOPIX Large70":1,"TOPIX Mid400":2,"TOPIX Small 1":3,"TOPIX Small 2":4}
jpx["size_rank"] = jpx["規模区分"].map(size_map).fillna(5)
jpx["market_rank"] = jpx["市場・商品区分"].apply(lambda x: 0 if "プライム" in str(x) else 1)
jpx = jpx.sort_values(["market_rank","size_rank","コード"])

# 33業種別に上位30銘柄を選定
sector_stocks = {}
sector_names_33 = sorted(jpx["33業種区分"].unique())
for sec in sector_names_33:
    sub = jpx[jpx["33業種区分"]==sec]
    sector_stocks[sec] = [(str(r["コード"]), r["銘柄名"].strip()) for _,r in sub.iterrows()]

total_stocks = sum(len(v) for v in sector_stocks.values())
print(f"  33業種: {len(sector_names_33)}業種, 対象: {total_stocks}銘柄")

# === yfinance データ取得 ===
print("価格データ取得中...")
all_codes = [TOPIX_ETF]
for sec, stks in sector_stocks.items():
    for code, name in stks:
        t = f"{code}.T"
        if t not in all_codes:
            all_codes.append(t)

end_date = dt.date.today() + dt.timedelta(days=1)
start_date = end_date - dt.timedelta(days=250)
raw = yf.download(all_codes, start=start_date, end=end_date, auto_adjust=True, progress=False, threads=True)

# Close/Volume取得
if isinstance(raw.columns, pd.MultiIndex):
    close = raw["Close"].copy()
    volume = raw["Volume"].copy()
else:
    close = raw[["Close"]].copy()
    close.columns = [all_codes[0]]
    volume = raw[["Volume"]].copy()
    volume.columns = [all_codes[0]]

# TOPIX欠損行を除外
close = close.dropna(subset=[TOPIX_ETF])
volume = volume.loc[close.index]

# 当日除外判定
now_jst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
today_str = now_jst.strftime("%Y-%m-%d")
last_date_str = close.index[-1].strftime("%Y-%m-%d")
if now_jst.hour < 16 and last_date_str == today_str and len(close) > 1:
    close = close.iloc[:-1]
    volume = volume.iloc[:-1]
    print(f"  取引時間中 - 当日({today_str})除外")

data_date = close.index[-1].strftime("%Y-%m-%d")
data_start = close.index[0].strftime("%Y-%m-%d")
topix_last = float(close[TOPIX_ETF].iloc[-1])
topix_prev = float(close[TOPIX_ETF].iloc[-2]) if len(close)>1 else topix_last
topix_chg = (topix_last/topix_prev-1)*100
print(f"  期間: {data_start} - {data_date}, TOPIX: {topix_last:.0f} ({topix_chg:+.1f}%)")

# === signal/kabuline/sector_volumeログ読み込み ===
print("ログ読み込み...")

# signal
sig_logs = sorted(glob.glob(os.path.join(LOG_DIR, "signal_*.json")))
sig_latest = {}
if sig_logs:
    with open(sig_logs[-1]) as f:
        sdata = json.load(f)
    for row in sdata:
        sig_latest[row.get("sector","")] = row
    print(f"  signal: {len(sig_logs)}日分, 最新{os.path.basename(sig_logs[-1])}")

# kabuline
kbl_logs = sorted(glob.glob(os.path.join(LOG_DIR, "kabuline_*.json")))
kbl_date = ""
kbl_posts = []
kbl_zscores = {}
if kbl_logs:
    with open(kbl_logs[-1]) as f:
        kdata = json.load(f)
    kbl_date = kdata.get("date","")
    kbl_posts = kdata.get("raw",[])
    if kdata.get("zscores") and isinstance(kdata["zscores"], dict):
        kbl_zscores = kdata["zscores"]
    print(f"  kabuline: {len(kbl_logs)}日分, 最新{os.path.basename(kbl_logs[-1])}")

# sector_volume
vol_logs = sorted(glob.glob(os.path.join(LOG_DIR, "sector_volume_*.json")))
vol_latest = {}
if vol_logs:
    with open(vol_logs[-1]) as f:
        vdata = json.load(f)
    if isinstance(vdata, dict) and "sectors" in vdata:
        for row in vdata["sectors"]:
            vol_latest[row.get("sector","")] = row
    elif isinstance(vdata, list):
        for row in vdata:
            vol_latest[row.get("sector","")] = row
    print(f"  sector_volume: {len(vol_logs)}日分, 最新{os.path.basename(vol_logs[-1])}")

# === 33業種ごとにセンサー計算 ===
print("33業種センサー計算...")

# 17業種→33業種マッピング（signalは17業種ベースなので近似マッピング）
map_17_to_33 = {
    "水産・農林業":"食品","食料品":"食品",
    "鉱業":"エネルギー資源","石油・石炭製品":"エネルギー資源",
    "建設業":"建設・資材","ガラス・土石製品":"建設・資材","金属製品":"建設・資材",
    "繊維製品":"素材・化学","パルプ・紙":"素材・化学","化学":"素材・化学","ゴム製品":"素材・化学",
    "医薬品":"医薬品",
    "輸送用機器":"自動車・輸送機",
    "鉄鋼":"鉄鋼・非鉄","非鉄金属":"鉄鋼・非鉄",
    "機械":"機械",
    "電気機器":"電機・精密","精密機器":"電機・精密",
    "その他製品":"情報通信・サービスその他","情報・通信業":"情報通信・サービスその他","サービス業":"情報通信・サービスその他",
    "電気・ガス業":"電気・ガス",
    "陸運業":"運輸・物流","海運業":"運輸・物流","空運業":"運輸・物流","倉庫・運輸関連業":"運輸・物流",
    "卸売業":"商社・卸売",
    "小売業":"小売",
    "銀行業":"銀行",
    "証券、商品先物取引業":"金融（除く銀行）","保険業":"金融（除く銀行）","その他金融業":"金融（除く銀行）",
    "不動産業":"不動産",
}

sectors_data = []
for sec in sector_names_33:
    stks = sector_stocks[sec]
    tickers = [f"{c}.T" for c,n in stks if f"{c}.T" in close.columns]
    
    if not tickers:
        sectors_data.append({"name":sec,"score":50,"level":"LOW","direction":"NEUTRAL",
            "a_z":0,"b_z":0,"c_z":0,"d_z":0,"d_count":0,
            "b_sig":"-","c_sig":"-","rs_5d":0,"rs_20d":0,
            "sensors":{"A":"neutral","B":"neutral","C":"neutral","D":"neutral"},
            "rs_chart":[],"stocks":[],"sns_posts":[]})
        continue
    
    # セクター平均株価（等金額ウエイト）
    # 売買代金加重平均
    turnover = (close[tickers] * volume[tickers]).tail(60).mean()  # 60日平均売買代金
    tw = turnover / turnover.sum() if turnover.sum() > 0 else pd.Series(1/len(tickers), index=tickers)
    sec_close = (close[tickers] * tw).sum(axis=1).dropna()
    if len(sec_close) < 5:
        sectors_data.append({"name":sec,"score":50,"level":"LOW","direction":"NEUTRAL",
            "a_z":0,"b_z":0,"c_z":0,"d_z":0,"d_count":0,
            "b_sig":"-","c_sig":"-","rs_5d":0,"rs_20d":0,
            "sensors":{"A":"neutral","B":"neutral","C":"neutral","D":"neutral"},
            "rs_chart":[],"stocks":[],"sns_posts":[]})
        continue
    
    topix = close[TOPIX_ETF].loc[sec_close.index]
    
    # RS計算
    rs = sec_close / topix
    rs_ma60 = rs.rolling(60).mean()
    rs_dev = ((rs - rs_ma60) / rs_ma60 * 100).dropna()
    
    # RS推移チャート（日付付き）
    rs_chart = [{"d":d.strftime("%m/%d"),"v":round(float(v),2)} for d,v in rs_dev.tail(60).items()]
    
    # RS 5日・20日変化
    rs_5d = 0
    rs_20d = 0
    if len(rs) >= 6:
        rs_5d = (float(rs.iloc[-1])/float(rs.iloc[-6])-1)*100
    if len(rs) >= 21:
        rs_20d = (float(rs.iloc[-1])/float(rs.iloc[-21])-1)*100
    
    # === センサーB: RS加速度 ===
    rs_short = rs.pct_change(5)
    rs_long = rs.pct_change(20)
    rs_accel = (rs_short - rs_long).dropna()
    b_z = 0
    b_sig = "-"
    if len(rs_accel) >= 60:
        mean = rs_accel.tail(60).mean()
        std = rs_accel.tail(60).std()
        if std > 0:
            b_z = float((rs_accel.iloc[-1] - mean) / std)
            if b_z > 1.5: b_sig = "IN"
            elif b_z < -1.5: b_sig = "OUT"
    
    # === センサーC: ベータ異常 ===
    ret_sec = sec_close.pct_change().dropna()
    ret_topix = topix.pct_change().dropna()
    common = ret_sec.index.intersection(ret_topix.index)
    c_z = 0
    c_sig = "-"
    if len(common) >= 60:
        rs_ = ret_sec.loc[common]
        rt_ = ret_topix.loc[common]
        beta_short = rs_.tail(20).cov(rt_.tail(20)) / rt_.tail(20).var() if rt_.tail(20).var() > 0 else 1
        beta_long = rs_.tail(60).cov(rt_.tail(60)) / rt_.tail(60).var() if rt_.tail(60).var() > 0 else 1
        beta_diff_series = []
        for i in range(60, len(common)):
            win = common[i-60:i]
            rs_w = rs_.loc[win]
            rt_w = rt_.loc[win]
            bs = rs_w[-20:].cov(rt_w[-20:]) / rt_w[-20:].var() if rt_w[-20:].var() > 0 else 1
            bl = rs_w.cov(rt_w) / rt_w.var() if rt_w.var() > 0 else 1
            beta_diff_series.append(bs - bl)
        if beta_diff_series:
            bd = pd.Series(beta_diff_series)
            if bd.std() > 0:
                c_z = float((bd.iloc[-1] - bd.mean()) / bd.std())
                if c_z > 1.5: c_sig = "UP"
                elif c_z < -1.5: c_sig = "DOWN"
    
    # === センサーA: 売買代金z-score ===
    sec_vol = volume[tickers].sum(axis=1).dropna() if tickers else pd.Series()
    a_z = 0
    if len(sec_vol) >= 60:
        vol_mean = sec_vol.tail(60).mean()
        vol_std = sec_vol.tail(60).std()
        if vol_std > 0:
            a_z = float((sec_vol.iloc[-1] - vol_mean) / vol_std)
    
    # signalからA_zを上書き（17業種マッピング経由）
    mapped_17 = map_17_to_33.get(sec, "")
    if mapped_17 in sig_latest:
        sl = sig_latest[mapped_17]
        # signalの値があればそちらを使う（より精度高い）
    
    # === センサーD: SNS（kabuline sector_totalsから自前z-score計算） ===
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
                d_z = (d_series[-1] - d_mean) / d_std
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
            sns_for_sec.append(p)
    
    # センサー状態
    sensors = {
        "A": "bull" if a_z > 1.5 else ("bear" if a_z < -1.5 else "neutral"),
        "B": "bull" if b_sig == "IN" else ("bear" if b_sig == "OUT" else "neutral"),
        "C": "bull" if c_sig == "UP" else ("bear" if c_sig == "DOWN" else "neutral"),
        "D": "bull" if d_z > 2 else ("bear" if d_z < -2 else "neutral"),
    }
    
    # スコア算出
    score = 50
    if b_sig == "IN": score += min(abs(b_z)*5, 20)
    elif b_sig == "OUT": score -= min(abs(b_z)*5, 20)
    if c_sig == "UP": score += min(abs(c_z)*4, 15)
    elif c_sig == "DOWN": score -= min(abs(c_z)*4, 15)
    score += max(min(a_z*3, 10), -10)
    score += max(min(d_z*2, 5), -5)
    score = max(0, min(100, score))
    
    bull = sum(1 for v in sensors.values() if v=="bull")
    bear = sum(1 for v in sensors.values() if v=="bear")
    
    if score >= 60: level = "MEDIUM"
    elif score <= 40: level = "MEDIUM"
    else: level = "LOW"
    if score >= 75: level = "HIGH"
    elif score <= 25: level = "HIGH"
    
    direction = "NEUTRAL"
    if score >= 60: direction = "IN"
    elif score <= 40: direction = "OUT"
    
    # === 個別株データ ===
    stock_list = []
    for code, name in stks:
        t = f"{code}.T"
        if t not in close.columns: continue
        sc = close[t].dropna()
        if len(sc) < 5: continue
        
        price = float(sc.iloc[-1])
        # 個別RS
        stk_rs = sc / close[TOPIX_ETF].loc[sc.index]
        stk_rs_5d = (float(stk_rs.iloc[-1])/float(stk_rs.iloc[-6])-1)*100 if len(stk_rs)>=6 else 0
        stk_rs_20d = (float(stk_rs.iloc[-1])/float(stk_rs.iloc[-21])-1)*100 if len(stk_rs)>=21 else 0
        
        # 出来高倍率
        sv = volume[t].dropna() if t in volume.columns else pd.Series()
        vol_ratio = 1.0
        if len(sv) >= 60:
            v5 = sv.tail(5).mean()
            v60 = sv.tail(60).mean()
            vol_ratio = v5/v60 if v60 > 0 else 1.0
        
        # 個別A(出来高z)
        stk_a_z = 0
        if len(sv) >= 60:
            vm = sv.tail(60).mean()
            vs = sv.tail(60).std()
            if vs > 0:
                stk_a_z = float((sv.iloc[-1] - vm) / vs)
        
        # 個別B(RS加速度z)
        stk_b_z = 0
        if len(stk_rs) >= 25:
            stk_rs_sh = stk_rs.pct_change(5)
            stk_rs_lg = stk_rs.pct_change(20)
            stk_accel = (stk_rs_sh - stk_rs_lg).dropna()
            if len(stk_accel) >= 20:
                sm = stk_accel.tail(20).mean()
                ss = stk_accel.tail(20).std()
                if ss > 0:
                    stk_b_z = float((stk_accel.iloc[-1] - sm) / ss)
        
        # 個別C(ベータz) - 簡易版
        stk_c_z_raw = 0
        stk_ret = sc.pct_change().dropna()
        top_ret = close[TOPIX_ETF].pct_change().dropna()
        ci = stk_ret.index.intersection(top_ret.index)
        if len(ci) >= 60:
            sr = stk_ret.loc[ci]
            tr = top_ret.loc[ci]
            b20 = sr.tail(20).cov(tr.tail(20)) / tr.tail(20).var() if tr.tail(20).var()>0 else 1
            b60 = sr.tail(60).cov(tr.tail(60)) / tr.tail(60).var() if tr.tail(60).var()>0 else 1
            stk_c_z_raw = b20 - b60  # beta差生値（後でセクター内z-score化）
        
        # 個別D(SNS) - 銘柄別言及数
        stk_d_count = 0
        for p in kbl_posts:
            if str(p.get("code","")) == code:
                stk_d_count = p.get("count", 0)
        
        # 個別RS推移
        stk_rs_ma = stk_rs.rolling(60).mean()
        stk_rs_dev = ((stk_rs - stk_rs_ma)/stk_rs_ma*100).dropna().tail(60)
        rs_hist = [{"d":d.strftime("%m/%d"),"v":round(float(v),2)} for d,v in stk_rs_dev.items()]
        
        stock_list.append({
            "code":code,"name":name,"price":round(price,0),
            "rs_5d":round(stk_rs_5d,1),"rs_20d":round(stk_rs_20d,1),
            "vol_ratio":round(vol_ratio,1),
            "a_z":round(stk_a_z,1),"b_z":round(stk_b_z,1),
            "c_z":round(stk_c_z_raw,3),"d_count":stk_d_count,
            "rs_hist":rs_hist
        })
    

    # 個別株Cをセクター内z-score化
    c_raws = [st.get("c_z", 0) for st in stock_list if st.get("c_z", 0) != 0]
    if len(c_raws) >= 3:
        import statistics
        c_mean = statistics.mean(c_raws)
        c_std = statistics.stdev(c_raws)
        if c_std > 0:
            for st in stock_list:
                st["c_z"] = round((st["c_z"] - c_mean) / c_std, 1)

    stock_list.sort(key=lambda x: x["rs_5d"], reverse=True)
    
    # センサー推移（過去60日分を遡及計算）
    ret_sec_full = sec_close.pct_change().dropna()
    ret_top_full = topix.pct_change().dropna()
    sensor_history = {"dates":[],"a":[],"b":[],"c":[],"d":[]}
    if len(rs) >= 65 and len(sec_vol) >= 65:
        for di in range(-60, 0):
            sh_date = rs.index[di].strftime("%m/%d")
            sensor_history["dates"].append(sh_date)
            # A: 出来高z
            vslice = sec_vol.iloc[:len(sec_vol)+di+1]
            if len(vslice)>=60:
                vm=vslice.tail(60).mean();vs_=vslice.tail(60).std()
                sensor_history["a"].append(round(float((vslice.iloc[-1]-vm)/vs_),2) if vs_>0 else 0)
            else:
                sensor_history["a"].append(0)
            # B: RS加速度z
            rslice = rs.iloc[:len(rs)+di+1]
            if len(rslice)>=25:
                rsh=rslice.pct_change(5);rlg=rslice.pct_change(20);acc=(rsh-rlg).dropna()
                if len(acc)>=60:
                    m=acc.tail(60).mean();s_=acc.tail(60).std()
                    sensor_history["b"].append(round(float((acc.iloc[-1]-m)/s_),2) if s_>0 else 0)
                else:
                    sensor_history["b"].append(0)
            else:
                sensor_history["b"].append(0)
            # C: 省略（計算コスト高いため最新値のみ）
            # C: ベータ差z-score
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

    sectors_data.append({
        "name":sec,"score":round(score,1),"level":level,"direction":direction,
        "a_z":round(a_z,1),"b_z":round(b_z,1),"c_z":round(c_z,1),"d_z":round(d_z,1),
        "d_count":d_count,"b_sig":b_sig,"c_sig":c_sig,
        "rs_5d":round(rs_5d,1),"rs_20d":round(rs_20d,1),
        "sensors":sensors,"rs_chart":rs_chart,
        "sensor_history":sensor_history,
        "stocks":stock_list,"sns_posts":sns_for_sec
    })

sectors_data.sort(key=lambda s: -s["score"])

# === ヒートマップデータ（33業種×過去90日 ABCDスコア推移） ===
print("ヒートマップ生成（ABCDスコア遡及計算）...")
hm_sectors = [s["name"] for s in sectors_data]
hm_dates = []
hm_values = []
date_range = close.index[-90:] if len(close)>=90 else close.index

# kabuline日次データをプリロード
kbl_daily = {}
for kp in kbl_logs:
    try:
        with open(kp) as kf:
            kd = json.load(kf)
        d_str = kd.get("date","")
        kbl_daily[d_str] = kd.get("sector_totals", {})
    except:
        pass

for sec_info in sectors_data:
    sec = sec_info["name"]
    tickers = [f"{c}.T" for c,n in sector_stocks.get(sec,[]) if f"{c}.T" in close.columns]
    mapped_17 = map_17_to_33.get(sec, "")
    row = []
    
    if not tickers:
        for d in date_range:
            ds = d.strftime("%m/%d")
            if ds not in hm_dates and sec == sectors_data[0]["name"]:
                hm_dates.append(ds)
            row.append(50)
        hm_values.append(row)
        continue
    
    sec_close_full = close[tickers].mean(axis=1).dropna()
    topix_full = close[TOPIX_ETF]
    rs_full = (sec_close_full / topix_full).dropna()
    
    # 出来高
    sec_vol = volume[tickers].sum(axis=1).dropna() if tickers else pd.Series()
    
    for d in date_range:
        ds = d.strftime("%m/%d")
        if ds not in hm_dates and sec == sectors_data[0]["name"]:
            hm_dates.append(ds)
        
        rs_to_d = rs_full.loc[:d]
        vol_to_d = sec_vol.loc[:d] if len(sec_vol)>0 else pd.Series()
        
        sc = 50  # ベーススコア
        
        # B: RS加速度
        if len(rs_to_d) >= 25:
            rs_sh = rs_to_d.pct_change(5)
            rs_lg = rs_to_d.pct_change(20)
            acc = (rs_sh - rs_lg).dropna()
            if len(acc) >= 60:
                m = acc.tail(60).mean()
                s = acc.tail(60).std()
                if s > 0:
                    bz = float((acc.iloc[-1] - m) / s)
                    if bz > 1.5: sc += min(abs(bz)*5, 20)
                    elif bz < -1.5: sc -= min(abs(bz)*5, 20)
        
        # A: 出来高
        if len(vol_to_d) >= 60:
            vm = vol_to_d.tail(60).mean()
            vs = vol_to_d.tail(60).std()
            if vs > 0:
                az = float((vol_to_d.iloc[-1] - vm) / vs)
                sc += max(min(az*3, 10), -10)
        
        sc = max(0, min(100, sc))
        row.append(round(sc, 1))
    
    hm_values.append(row)

# === HTML生成 ===
print("HTML生成...")

# 資金流入/流出セクター
inflow = [s for s in sectors_data if s["direction"]=="IN"]
outflow = [s for s in sectors_data if s["direction"]=="OUT"]
inflow_txt = ", ".join([f'{s["name"]}({s["score"]:.0f}pt)' for s in inflow[:5]]) or "なし"
outflow_txt = ", ".join([f'{s["name"]}({s["score"]:.0f}pt)' for s in outflow[:5]]) or "なし"

# signal/kabuline日付
sig_date = os.path.basename(sig_logs[-1]).replace("signal_","").replace(".json","") if sig_logs else "-"

html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<title>33業種セクターローテーション</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;background:#fafafa;color:#333;font-size:14px}}
.wrap{{max-width:960px;margin:0 auto;padding:16px}}
h1{{font-size:18px;font-weight:700;margin-bottom:4px}}
.meta{{color:#888;font-size:12px;margin-bottom:12px}}
.summary{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px;margin-bottom:16px}}
.summary .in{{color:#1565c0}} .summary .out{{color:#c62828}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;margin-bottom:8px;cursor:pointer;transition:box-shadow .2s}}
.card:hover{{box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
.card-h{{display:flex;align-items:center;padding:12px 16px;gap:12px}}
.badge{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;color:#fff;min-width:60px;text-align:center}}
.badge-high{{background:#2e7d32}} .badge-med{{background:#1565c0}} .badge-low{{background:#bdbdbd}}
.badge-med-in{{background:#43a047}} .badge-med-out{{background:#e53935}}
.badge-in{{background:#1b5e20}} .badge-out{{background:#b71c1c}}
.badge-in{{background:#2e7d32}} .badge-out{{background:#c62828}}
.score{{font-size:20px;font-weight:700;min-width:36px;text-align:right}}
.sec-name{{font-weight:600;flex:1}}
.rs{{font-size:13px;min-width:60px;text-align:right}}
.sensors-mini{{display:flex;gap:4px;font-size:11px}}
.sensors-mini span{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.s-bull{{background:#2e7d32}} .s-bear{{background:#c62828}} .s-neut{{background:#ccc}}
.spark{{width:120px;height:32px}}
.detail{{display:none;padding:0 16px 16px;border-top:1px solid #f0f0f0}}
.detail.open{{display:block}}
.sensor-row{{display:flex;gap:16px;margin:12px 0;flex-wrap:wrap}}
.sensor-box{{flex:1;min-width:200px;background:#f8f8f8;border-radius:6px;padding:10px}}
.sensor-box .label{{font-size:11px;color:#888;margin-bottom:4px}}
.sensor-box .val{{font-size:18px;font-weight:700}}
.sensor-box .desc{{font-size:11px;color:#aaa;margin-top:4px}}
.chart-area{{margin:12px 0}}
.chart-area h4{{font-size:13px;color:#666;margin-bottom:6px}}
.chart-area canvas{{width:100%;border:1px solid #f0f0f0;border-radius:4px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}}
th{{background:#f5f5f5;padding:6px 8px;text-align:left;font-weight:600;border-bottom:2px solid #e0e0e0}}
td{{padding:6px 8px;border-bottom:1px solid #f0f0f0}}
tr:hover{{background:#f8f8ff}}
.hi{{background:#fff3e0}}
.guide{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px;margin-top:16px;font-size:12px;color:#666}}
.guide h3{{font-size:14px;color:#333;margin-bottom:8px}}
.hm-wrap{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px;margin-bottom:16px;overflow-x:auto}}
.hm-wrap h3{{font-size:14px;margin-bottom:8px}}
.score-formula{{background:#f0f4ff;border:1px solid #c5cae9;border-radius:6px;padding:10px;margin:12px 0;font-size:12px;color:#555}}
.sns-link{{color:#1565c0;text-decoration:none}}
.sns-link:hover{{text-decoration:underline}}
</style></head><body>
<div class="wrap">
<h1>33業種 セクターローテーション</h1>
<div class="meta">{data_date} | TOPIX {topix_last:,.0f}（{topix_chg:+.1f}%）| シグナル:{sig_date} SNS:{kbl_date}</div>

<div class="summary">
<b>資金流入兆候:</b> <span class="in">{inflow_txt}</span><br>
<b>資金流出兆候:</b> <span class="out">{outflow_txt}</span>
</div>

<div class="hm-wrap">
<h3>ヒストリカル・セクターローテーション（過去90日・ABCDスコア推移）</h3>
<div style="color:#888;font-size:11px;margin-bottom:4px">緑=スコア高(資金流入兆候) / 赤=スコア低(資金流出兆候) / 白=中立(50)</div>
<canvas id="hm" height="500"></canvas>
</div>
"""

# セクターカード生成
for i, s in enumerate(sectors_data):
    badge_cls = "badge-low"
    if s["level"] == "HIGH" and s["direction"] == "IN":
        badge_cls = "badge-in"
    elif s["level"] == "HIGH" and s["direction"] == "OUT":
        badge_cls = "badge-out"
    elif s["level"] == "MEDIUM" and s["direction"] == "IN":
        badge_cls = "badge-med-in"
    elif s["level"] == "MEDIUM" and s["direction"] == "OUT":
        badge_cls = "badge-med-out"
    sc_color = "#c62828" if s["score"]<40 else ("#2e7d32" if s["score"]>60 else "#333")
    rs_color = "#c62828" if s["rs_5d"]<0 else "#2e7d32"
    
    sensor_dots = ""
    for key in ["A","B","C","D"]:
        st = s["sensors"][key]
        cls = "s-bull" if st=="bull" else ("s-bear" if st=="bear" else "s-neut")
        sensor_dots += f'<span class="{cls}" title="{key}"></span>'
    
    html += f"""
<div class="card" onclick="toggle({i})">
<div class="card-h">
  <span class="badge {badge_cls}">{s["level"]}</span>
  <span class="score" style="color:{sc_color}">{s["score"]:.0f}</span>
  <span class="sec-name">{s["name"]}</span>
  <span class="sensors-mini">{sensor_dots}</span>
  <span class="rs" style="color:{rs_color}">{s["rs_5d"]:+.1f}%</span>
  <canvas class="spark" id="sp-{i}"></canvas>
</div>
<div class="detail" id="dt-{i}">
  <div class="score-formula">
    <b>スコア算出:</b> 50基準 + B(対TOPIXの勢い変化)x5 + C(市場感応度の変化)x4 + A(売買代金の異常度)x3 + D(SNS注目度)x2 → 0-100。60以上=資金流入兆候 / 40以下=資金流出兆候
  </div>
  <div class="sensor-row">
    <div class="sensor-box">
      <div class="label">A 売買代金</div>
      <div class="val" style="color:{'#2e7d32' if s['a_z']>1.5 else ('#c62828' if s['a_z']<-1.5 else '#333')}">{s["a_z"]:.1f}</div>
      <div class="desc">過去60日の出来高からどれだけ外れているか。1.5超=異常に多い</div>
    </div>
    <div class="sensor-box">
      <div class="label">B 対TOPIXの勢い変化</div>
      <div class="val" style="color:{'#2e7d32' if s['b_sig']=='IN' else ('#c62828' if s['b_sig']=='OUT' else '#333')}">{s["b_z"]:.1f} {s["b_sig"]}</div>
      <div class="desc">この業種がTOPIXに対して加速しているか減速しているか。IN=資金流入加速</div>
    </div>
    <div class="sensor-box">
      <div class="label">C 市場感応度の変化</div>
      <div class="val" style="color:{'#2e7d32' if s['c_sig']=='UP' else ('#c62828' if s['c_sig']=='DOWN' else '#333')}">{s["c_z"]:.1f} {s["c_sig"]}</div>
      <div class="desc">市場全体への連動度が直近で変わったか。UP=感応度上昇</div>
    </div>
    <div class="sensor-box">
      <div class="label">D SNS注目度</div>
      <div class="val" style="color:{'#2e7d32' if s['d_z']>2 else ('#c62828' if s['d_z']<-2 else '#333')}">{s["d_z"]:.1f}</div>
      <div class="desc">株ラインでの言及数が過去30日からどれだけ外れているか。2超=急増</div>
    </div>
  </div>
  
  <div class="chart-area">
    <h4>対TOPIXパフォーマンス推移（過去60日）</h4>
    <div style="font-size:11px;color:#888">0%より上=TOPIXに勝っている。下=負けている。</div>
    <canvas id="rs-{i}" height="180"></canvas>
  </div>
  
  <div class="chart-area">
    <h4>センサー推移（A/B/C/D）</h4>
    <div style="font-size:11px;color:#888">各センサーのz-score推移。点線=閾値(+-1.5)。横軸=日付。</div>
    <canvas id="sn-{i}" height="200"></canvas>
  </div>
"""
    
    # SNS投稿
    if s["sns_posts"]:
        html += f'<h4>SNS注目銘柄（株ライン {kbl_date}）</h4>'
        for p in s["sns_posts"][:5]:
            code = p.get("code","")
            count = p.get("count",0)
            name_lookup = {c: n for c, n in sector_stocks.get(sec, [])}
            sname = name_lookup.get(str(code), "")
            html += f'<div style="padding:4px 0;font-size:12px"><a href="https://kabuline.com/stock/code/{code}/" target="_blank" class="sns-link">{code} {sname}</a> {count}件</div>'
    
    # 個別株テーブル（ABCD付き）
    html += """
  <h4>個別銘柄</h4>
  <table>
  <tr><th>銘柄</th><th style="text-align:right">株価</th><th style="text-align:right">5日RS</th><th style="text-align:right">20日RS</th><th style="text-align:right">出来高</th><th style="text-align:right">A</th><th style="text-align:right">B</th><th style="text-align:right">C</th><th style="text-align:right">D</th></tr>
"""
    for st in s["stocks"][:30]:
        a_cls = ' class="hi"' if abs(st["a_z"])>1.5 else ""
        b_cls = ' class="hi"' if abs(st["b_z"])>1.5 else ""
        c_cls = ' class="hi"' if abs(st["c_z"])>0.5 else ""
        d_cls = ' class="hi"' if st["d_count"]>5 else ""
        rs_c = "color:#c62828" if st["rs_5d"]<0 else "color:#2e7d32"
        vf = " *" if st["vol_ratio"]>1.5 else ""
        html += f'<tr><td><b>{st["code"]}</b> {st["name"]}</td><td style="text-align:right">{st["price"]:,.0f}</td><td style="text-align:right;{rs_c}">{st["rs_5d"]:+.1f}%</td><td style="text-align:right">{st["rs_20d"]:+.1f}%</td><td style="text-align:right">{st["vol_ratio"]:.1f}x{vf}</td><td style="text-align:right"{a_cls}>{st["a_z"]:.1f}</td><td style="text-align:right"{b_cls}>{st["b_z"]:.1f}</td><td style="text-align:right"{c_cls}>{st["c_z"]:.1f}</td><td style="text-align:right"{d_cls}>{st["d_count"]}</td></tr>'
    
    html += "</table></div></div>"

# ガイド
html += """
<div class="guide">
<h3>このダッシュボードの見方</h3>
<p><b>スコア(0-100):</b> 50が中立。60以上は資金が流入し始めている兆候、40以下は流出兆候。4つのセンサーの加重合計。</p>
<p style="margin-top:6px"><b>4つのセンサー:</b></p>
<p><b>A 売買代金:</b> この業種の出来高が過去60日と比べてどれだけ異常か（z-score）。急に売買が増えれば資金移動のサイン。</p>
<p><b>B 対TOPIXの勢い変化:</b> この業種のTOPIXに対するパフォーマンスが加速しているか減速しているか。IN=他の業種から資金が流入している兆候。</p>
<p><b>C 市場感応度の変化:</b> 市場全体との連動度(ベータ)が短期と長期で変わったか。変化はリスク選好の変化を示唆。</p>
<p><b>D SNS注目度:</b> 株ライン（Twitter投資家の言及集計サイト）での注目度。急増は個人投資家の関心集中を示す。</p>
<p style="margin-top:6px"><b>●の色:</b> 緑=強気シグナル / 赤=弱気シグナル / グレー=平常</p>
<p style="margin-top:6px"><b>個別銘柄のA/B/C/D:</b> セクターと同じ手法を各銘柄に適用。Aは出来高z-score、Bは対TOPIX RS加速度z-score、Cはベータ差、Dは株ライン言及数。</p>
<p style="margin-top:8px;color:#aaa">更新: 毎営業日16:30自動(cron) | 手動: cd ~/sector_rotation && python3 integrated_dashboard.py</p>
</div>
"""

# JavaScript
html += """
<script>
const DATA = """ + json.dumps(sectors_data, ensure_ascii=False) + """;
const hmS = """ + json.dumps(hm_sectors, ensure_ascii=False) + """;
const hmD = """ + json.dumps(hm_dates, ensure_ascii=False) + """;
const hmV = """ + json.dumps(hm_values) + """;

function toggle(i){
    const d=document.getElementById('dt-'+i);
    const wasOpen = d.classList.contains('open');
    d.classList.toggle('open');
    if(!wasOpen){
        drawRS('rs-'+i, DATA[i].rs_chart);
        drawSensor('sn-'+i, i);
        drawStockRS('stk-'+i, DATA[i].stocks.slice(0,5));
    }
}

// スパークライン描画
DATA.forEach((s,i)=>{
    const cv=document.getElementById('sp-'+i);
    if(!cv||!s.rs_chart||s.rs_chart.length<2)return;
    const cx=cv.getContext('2d');
    cv.width=120;cv.height=32;
    const vs=s.rs_chart.map(d=>d.v);
    const mn=Math.min(...vs),mx=Math.max(...vs),rg=mx-mn||1;
    cx.strokeStyle=vs[vs.length-1]>=0?'#2e7d32':'#c62828';
    cx.lineWidth=1.5;cx.beginPath();
    vs.forEach((v,j)=>{
        const x=j/(vs.length-1)*118+1;
        const y=30-(v-mn)/rg*28+1;
        j===0?cx.moveTo(x,y):cx.lineTo(x,y);
    });
    cx.stroke();
    // ゼロ線
    const zy=30-(0-mn)/rg*28+1;
    cx.strokeStyle='#ddd';cx.lineWidth=0.5;cx.setLineDash([2,2]);
    cx.beginPath();cx.moveTo(0,zy);cx.lineTo(120,zy);cx.stroke();
});

function drawRS(id, data){
    const cv=document.getElementById(id);if(!cv)return;
    const cx=cv.getContext('2d');
    const W=cv.parentElement.offsetWidth-4;cv.width=W;cv.height=180;
    if(!data||data.length<2){cx.fillStyle='#aaa';cx.fillText('データなし',W/2-20,90);return}
    const vs=data.map(d=>d.v),mn=Math.min(...vs,0),mx=Math.max(...vs,0),rg=mx-mn||1;
    const p={t:20,b:30,l:45,r:15},cw=W-p.l-p.r,ch=130;
    // ゼロ線
    const zy=p.t+ch-(0-mn)/rg*ch;
    cx.strokeStyle='#e0e0e0';cx.lineWidth=1;cx.beginPath();cx.moveTo(p.l,zy);cx.lineTo(W-p.r,zy);cx.stroke();
    cx.fillStyle='#aaa';cx.font='10px sans-serif';cx.fillText('0%',p.l-25,zy+4);
    // 線
    cx.strokeStyle='#1565c0';cx.lineWidth=2;cx.beginPath();
    data.forEach((d,i)=>{const x=p.l+i/(data.length-1)*cw,y=p.t+ch-(d.v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});
    cx.stroke();
    // 日付
    cx.fillStyle='#999';cx.font='10px sans-serif';
    cx.fillText(data[0].d,p.l,p.t+ch+20);
    if(data.length>30)cx.fillText(data[Math.floor(data.length/2)].d,p.l+cw/2-15,p.t+ch+20);
    cx.fillText(data[data.length-1].d,W-p.r-25,p.t+ch+20);
    // 最新値
    const lv=vs[vs.length-1],ly=p.t+ch-(lv-mn)/rg*ch;
    cx.fillStyle='#1565c0';cx.font='bold 12px sans-serif';cx.fillText(lv.toFixed(1)+'%',W-p.r-50,ly-8);
    // 軸
    cx.fillStyle='#ccc';cx.font='10px sans-serif';
    cx.fillText(mx.toFixed(1)+'%',2,p.t+10);cx.fillText(mn.toFixed(1)+'%',2,p.t+ch);
}


function drawSensor(id, sectorIdx){
    const cv=document.getElementById(id);if(!cv)return;
    const cx=cv.getContext('2d');
    const W=cv.parentElement.offsetWidth-4;cv.width=W;cv.height=200;
    const s=DATA[sectorIdx];
    const h=s.sensor_history||{};
    const dates=h.dates||[],aH=h.a||[],bH=h.b||[],cH=h.c||[],dH=h.d||[];
    const p={t:20,b:30,l:45,r:15},cw=W-p.l-p.r,ch=140;
    if(dates.length<3){
        cx.fillStyle='#aaa';cx.font='12px sans-serif';
        cx.fillText('データ蓄積中',W/2-30,100);return;
    }
    const all=[...aH,...bH,...cH,...dH];
    const mn=Math.min(...all,-2),mx=Math.max(...all,2),rg=mx-mn||1;
    // 閾値線
    [1.5,-1.5,0].forEach(th=>{
        const y=p.t+ch-(th-mn)/rg*ch;
        cx.strokeStyle=th===0?'#ccc':'#e8e8e8';
        cx.lineWidth=1;cx.setLineDash(th===0?[]:[3,3]);
        cx.beginPath();cx.moveTo(p.l,y);cx.lineTo(W-p.r,y);cx.stroke();
        cx.setLineDash([]);
        cx.fillStyle='#aaa';cx.font='10px sans-serif';
        cx.fillText(th===0?'0':th>0?'+1.5':'-1.5',2,y+4);
    });
    // A線（緑）
    if(aH.length>=2){cx.strokeStyle='#66bb6a';cx.lineWidth=1.5;cx.beginPath();
    aH.forEach((v,i)=>{const x=p.l+i/(aH.length-1)*cw,y=p.t+ch-(v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});cx.stroke();
    cx.fillStyle='#66bb6a';cx.font='bold 11px sans-serif';const ay=p.t+ch-(aH[aH.length-1]-mn)/rg*ch;cx.fillText('A:'+aH[aH.length-1].toFixed(1),W-p.r-50,ay-4)}
    // B線（青）
    if(bH.length>=2){cx.strokeStyle='#42a5f5';cx.lineWidth=1.5;cx.beginPath();
    bH.forEach((v,i)=>{const x=p.l+i/(bH.length-1)*cw,y=p.t+ch-(v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});cx.stroke();
    cx.fillStyle='#42a5f5';cx.font='bold 11px sans-serif';const by=p.t+ch-(bH[bH.length-1]-mn)/rg*ch;cx.fillText('B:'+bH[bH.length-1].toFixed(1),W-p.r-50,by+12)}
    // C線（橙）
    if(cH&&cH.length>=2){cx.strokeStyle='#ffa726';cx.lineWidth=1.5;cx.beginPath();
    cH.forEach((v,i)=>{const x=p.l+i/(cH.length-1)*cw,y=p.t+ch-(v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});cx.stroke();
    cx.fillStyle='#ffa726';cx.font='bold 11px sans-serif';const cy2=p.t+ch-(cH[cH.length-1]-mn)/rg*ch;cx.fillText('C:'+cH[cH.length-1].toFixed(1),W-p.r-50,cy2+24)}
    // D線（紫）
    if(dH&&dH.length>=2){cx.strokeStyle='#ab47bc';cx.lineWidth=1.5;cx.beginPath();
    dH.forEach((v,i)=>{const x=p.l+i/(dH.length-1)*cw,y=p.t+ch-(v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});cx.stroke();
    cx.fillStyle='#ab47bc';cx.font='bold 11px sans-serif';const dy2=p.t+ch-(dH[dH.length-1]-mn)/rg*ch;cx.fillText('D:'+dH[dH.length-1].toFixed(1),W-p.r-50,dy2-4)}
    const dx=W-p.r-20,dzy=p.t+ch-(0-mn)/rg*ch;
    const dy=p.t+ch-(s.d_z-mn)/rg*ch;
    cx.font='10px sans-serif';cx.fillText('D:'+s.d_z.toFixed(1),dx-25,dy-8);
    // C現在値（橙マーカー）
    const cy_=p.t+ch-(s.c_z-mn)/rg*ch;
    cx.fillStyle='#ffa726';cx.beginPath();cx.arc(dx-40,cy_,5,0,Math.PI*2);cx.fill();
    cx.font='10px sans-serif';cx.fillText('C:'+s.c_z.toFixed(1),dx-65,cy_-8);
    // 日付ラベル
    cx.fillStyle='#999';cx.font='10px sans-serif';
    cx.fillText(dates[0],p.l,p.t+ch+20);
    if(dates.length>20)cx.fillText(dates[Math.floor(dates.length/2)],p.l+cw/2-15,p.t+ch+20);
    cx.fillText(dates[dates.length-1],W-p.r-30,p.t+ch+20);
    // 凡例
    cx.fillText('緑=A(売買代金) 青=B(勢い) 橙●=C(感応度) 紫●=D(SNS)',p.l,p.t+ch+18+15);
}

function drawHM(){
    const cv=document.getElementById('hm'),cx=cv.getContext('2d');
    const rows=hmS.length,cols=hmD.length;
    const p={t:10,b:30,l:140,r:10};
    const W=Math.max(cv.parentElement.offsetWidth-28,p.l+cols*3+p.r);
    const cW=Math.max((W-p.l-p.r)/cols,2),cH=Math.max(450/rows,12);
    cv.width=W;cv.height=p.t+rows*cH+p.b;
    for(let r=0;r<rows;r++){
        cx.fillStyle='#333';cx.font='10px sans-serif';cx.fillText(hmS[r],4,p.t+r*cH+cH/2+3);
        for(let c=0;c<cols;c++){
            const v=hmV[r][c];
            let cl;
            if(v>=50){const t=Math.min((v-50)/30,1);cl=`rgb(${Math.round(245-t*180)},${Math.round(245-t*50)},${Math.round(245-t*180)})`}
            else{const t=Math.min((50-v)/30,1);cl=`rgb(${Math.round(245-t*50)},${Math.round(245-t*180)},${Math.round(245-t*180)})`}
            cx.fillStyle=cl;cx.fillRect(p.l+c*cW,p.t+r*cH,cW+.5,cH-1);
        }
    }
    cx.fillStyle='#999';cx.font='10px sans-serif';
    cx.fillText(hmD[0],p.l,p.t+rows*cH+18);
    if(cols>20)cx.fillText(hmD[Math.floor(cols/2)],p.l+Math.floor(cols/2)*cW,p.t+rows*cH+18);
    cx.fillText(hmD[cols-1],p.l+(cols-1)*cW-20,p.t+rows*cH+18);
}

drawHM();
</script>
</div></body></html>"""

# 出力
with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"HTML出力: {OUT_HTML}")

# ログ保存
log_path = os.path.join(LOG_DIR, f"dashboard_{data_date}.json")
with open(log_path, "w", encoding="utf-8") as f:
    json.dump({"date":data_date,"topix":topix_last,"topix_chg":round(topix_chg,1),"sectors":sectors_data}, f, ensure_ascii=False, indent=2)
print(f"ログ: {log_path}")

# Slack
print("Slack送信...")
try:
    import requests
    lines = [f"33業種ローテーション {data_date}",f"TOPIX {topix_last:,.0f} ({topix_chg:+.1f}%)",""]
    if inflow:
        lines.append("資金流入兆候:")
        for s in inflow[:5]:
            top_stk = s["stocks"][0] if s["stocks"] else None
            stk_info = f" | 注目: {top_stk['code']} {top_stk['name']} RS{top_stk['rs_5d']:+.1f}%" if top_stk else ""
            lines.append(f"  {s['name']} {s['score']:.0f}pt (B:{s['b_z']:.1f} A:{s['a_z']:.1f}){stk_info}")
    if outflow:
        lines.append("資金流出兆候:")
        for s in outflow[:5]:
            lines.append(f"  {s['name']} {s['score']:.0f}pt")
    text = "\n".join(lines)
    r = requests.post(WEBHOOK, json={"text":text}, timeout=10)
    print(f"  Slack: {r.status_code}")
except Exception as e:
    print(f"  Slack error: {e}")

print("=== 完了 ===")
