#!/usr/bin/env python3
"""465銘柄の個別SNS言及数を株ラインから取得"""
import requests, json, os, time, datetime
from bs4 import BeautifulSoup

LOG_DIR = os.path.expanduser("~/sector_rotation/logs")
JPX_FILE = os.path.expanduser("~/sector_rotation/jpx_list.csv")
os.makedirs(LOG_DIR, exist_ok=True)

# 銘柄リスト読み込み（integrated_dashboard.pyと同じ母集団）
import pandas as pd
jpx = pd.read_excel(JPX_FILE, engine="xlrd")
jpx = jpx[(jpx["33業種コード"] != "-") & (~jpx["市場・商品区分"].str.contains("ETF|ETN", na=False))].copy()
jpx = jpx[jpx["規模区分"].isin(["TOPIX Large70","TOPIX Mid400"])]
codes = [(str(r["コード"]), r["銘柄名"].strip(), r["33業種区分"]) for _, r in jpx.iterrows()]
print(f"対象銘柄数: {len(codes)}")

headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
results = []
errors = 0

for i, (code, name, sector) in enumerate(codes):
    url = f"https://kabuline.com/search/tw/{code}/"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tweet_count = len(soup.select("li.tweet_list"))
            # 買売比率（あれば）
            buy_el = soup.select_one("p.buy")
            sell_el = soup.select_one("p.sell")
            buy_pct = None
            if buy_el and sell_el:
                import re
                bm = re.search(r"([\d.]+)", buy_el.get_text())
                if bm:
                    buy_pct = float(bm.group(1))
            results.append({
                "code": code, "name": name, "sector": sector,
                "tweet_count": tweet_count, "buy_pct": buy_pct
            })
        else:
            results.append({"code": code, "name": name, "sector": sector, "tweet_count": 0, "buy_pct": None})
            errors += 1
    except Exception as e:
        results.append({"code": code, "name": name, "sector": sector, "tweet_count": 0, "buy_pct": None})
        errors += 1

    # 進捗表示（50件ごと）
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(codes)} 完了...")

    # レート制限（0.3秒間隔）
    time.sleep(0.3)

# 集計
today = datetime.date.today().isoformat()
sector_totals = {}
sector_sentiment = {}
for r in results:
    sec = r["sector"]
    sector_totals[sec] = sector_totals.get(sec, 0) + r["tweet_count"]
    if r["buy_pct"] is not None:
        if sec not in sector_sentiment:
            sector_sentiment[sec] = {"sum": 0, "count": 0}
        sector_sentiment[sec]["sum"] += r["buy_pct"]
        sector_sentiment[sec]["count"] += 1

sector_sentiment_avg = {}
for sec, v in sector_sentiment.items():
    sector_sentiment_avg[sec] = round(v["sum"] / v["count"], 1) if v["count"] > 0 else 50.0

# 保存
output = {
    "date": today,
    "stock_count": len(results),
    "errors": errors,
    "sector_totals": sector_totals,
    "sector_sentiment": sector_sentiment_avg,
    "stocks": results
}
outpath = os.path.join(LOG_DIR, f"kabuline_{today}.json")
with open(outpath, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n=== 完了 ===")
print(f"取得: {len(results)}銘柄, エラー: {errors}")
print(f"保存: {outpath}")

# TOP20表示
top20 = sorted(results, key=lambda x: -x["tweet_count"])[:20]
print(f"\nTOP20 SNS言及:")
for r in top20:
    bp = f" 買{r['buy_pct']}%" if r["buy_pct"] else ""
    print(f"  {r['code']} {r['name']} {r['tweet_count']}件{bp} ({r['sector']})")

# セクター集計表示
print(f"\nセクター別言及数:")
for sec in sorted(sector_totals, key=lambda x: -sector_totals[x]):
    sent = sector_sentiment_avg.get(sec, "-")
    print(f"  {sec}: {sector_totals[sec]}件 (買{sent}%)")
