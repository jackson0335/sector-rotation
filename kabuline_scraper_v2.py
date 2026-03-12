#!/usr/bin/env python3
"""
株ライン話題株ランキング → セクター別SNS注目度（v2: 売買感情追加）
"""
import requests
from bs4 import BeautifulSoup
import re, json, os, time
from datetime import datetime, timedelta
import urllib.request

SLACK_WEBHOOK = 'SLACK_WEBHOOK_URL'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')

SECTOR_MAP = {
    3656: "情報通信・サービスその他",
    3350: "小売",
    4075: "情報通信・サービスその他",
    5801: "鉄鋼・非鉄",
    5985: "鉄鋼・非鉄",
    2914:'食品',2802:'食品',2269:'食品',2501:'食品',2502:'食品',2801:'食品',
    1605:'エネルギー資源',5020:'エネルギー資源',5019:'エネルギー資源',
    1925:'建設・資材',1928:'建設・資材',1801:'建設・資材',1802:'建設・資材',5233:'建設・資材',
    4063:'素材・化学',4188:'素材・化学',4452:'素材・化学',4901:'素材・化学',4911:'素材・化学',
    3103:'素材・化学',4005:'素材・化学',4183:'素材・化学',
    4502:'医薬品',4503:'医薬品',4519:'医薬品',4568:'医薬品',4578:'医薬品',
    4507:'医薬品',4523:'医薬品',
    7203:'自動車・輸送機',7267:'自動車・輸送機',7269:'自動車・輸送機',7270:'自動車・輸送機',6902:'自動車・輸送機',
    5401:'鉄鋼・非鉄',5411:'鉄鋼・非鉄',5706:'鉄鋼・非鉄',5713:'鉄鋼・非鉄',5016:'鉄鋼・非鉄',
    5802:'鉄鋼・非鉄',5803:'鉄鋼・非鉄',
    6301:'機械',6305:'機械',7011:'機械',7012:'機械',6326:'機械',
    6356:'機械',6367:'機械',
    6501:'電機・精密',6503:'電機・精密',6758:'電機・精密',6861:'電機・精密',6920:'電機・精密',6594:'電機・精密',
    6740:'電機・精密',7771:'電機・精密',7746:'電機・精密',6613:'電機・精密',6753:'電機・精密',
    6981:'電機・精密',6857:'電機・精密',8035:'電機・精密',6702:'電機・精密',6752:'電機・精密',
    7751:'電機・精密',
    9984:'情報通信・サービスその他',9432:'情報通信・サービスその他',9433:'情報通信・サービスその他',
    4689:'情報通信・サービスその他',9434:'情報通信・サービスその他',
    7974:'情報通信・サービスその他',6098:'情報通信・サービスその他',6178:'情報通信・サービスその他',
    9501:'電気・ガス',9502:'電気・ガス',9503:'電気・ガス',9531:'電気・ガス',
    9020:'運輸・物流',9021:'運輸・物流',9062:'運輸・物流',9101:'運輸・物流',9104:'運輸・物流',
    9107:'運輸・物流',
    8001:'商社・卸売',8002:'商社・卸売',8031:'商社・卸売',8053:'商社・卸売',8058:'商社・卸売',
    8015:'商社・卸売',
    3382:'小売',8267:'小売',9983:'小売',7532:'小売',
    8306:'銀行',8316:'銀行',8411:'銀行',8308:'銀行',
    8591:'金融（除く銀行）',8593:'金融（除く銀行）',8697:'金融（除く銀行）',8725:'金融（除く銀行）',
    8766:'金融（除く銀行）',8729:'金融（除く銀行）',
    8801:'不動産',8802:'不動産',8830:'不動産',3291:'不動産',
    1357:'ETF除外',
}

SECTORS = ['食品','エネルギー資源','建設・資材','素材・化学','医薬品',
           '自動車・輸送機','鉄鋼・非鉄','機械','電機・精密',
           '情報通信・サービスその他','電気・ガス','運輸・物流',
           '商社・卸売','小売','銀行','金融（除く銀行）','不動産']

def scrape(date_str):
    url = f'https://kabuline.com/stock/hot_rank/{date_str}/'
    headers = {'User-Agent':'SectorRotationBot/1.0 (research)'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  取得エラー: {e}")
        return []
    soup = BeautifulSoup(r.text, 'html.parser')
    results = []
    for a in soup.select('a'):
        href = a.get('href', '')
        m = re.search(r'/search/tw/(\d{4})/', href)
        if not m:
            continue
        code = int(m.group(1))
        text = a.get_text(strip=True)
        code_str = str(code)
        if text.startswith(code_str):
            count_str = text[len(code_str):]
            try:
                count = int(count_str)
            except ValueError:
                continue
            sector = SECTOR_MAP.get(code, '未分類')
            if sector == 'ETF除外':
                continue
            if sector == '未分類':
                print(f'  未分類: {code} {count}件')
            results.append({
                'code': code,
                'count': count,
                'sector': sector,
            })
    return results

def fetch_sentiment(code):
    """銘柄ページから売買感情を取得"""
    url = f'https://kabuline.com/search/tw/{code}/?frame=true'
    headers = {'User-Agent':'SectorRotationBot/1.0 (research)'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        buy_el = soup.select_one('p.buy')
        sell_el = soup.select_one('p.sell')
        buy_pct = 50.0
        sell_pct = 50.0
        if buy_el:
            m = re.search(r'([\d.]+)%', buy_el.get_text())
            if m:
                buy_pct = float(m.group(1))
        if sell_el:
            m = re.search(r'([\d.]+)%', sell_el.get_text())
            if m:
                sell_pct = float(m.group(1))
        tweet_count = len(soup.select('li.tweet_list'))
        return {'buy_pct': buy_pct, 'sell_pct': sell_pct, 'tweet_count': tweet_count}
    except Exception as e:
        print(f"  感情取得エラー {code}: {e}")
        return {'buy_pct': 50.0, 'sell_pct': 50.0, 'tweet_count': 0}

def scrape_with_sentiment(date_str):
    """ランキング取得 + TOP銘柄の売買感情を追加取得"""
    data = scrape(date_str)
    if not data:
        return data
    top_codes = sorted(data, key=lambda x: x['count'], reverse=True)[:30]
    print(f"  売買感情取得中... (上位{len(top_codes)}銘柄)")
    for i, item in enumerate(top_codes):
        sentiment = fetch_sentiment(item['code'])
        item['buy_pct'] = sentiment['buy_pct']
        item['sell_pct'] = sentiment['sell_pct']
        item['tweet_count'] = sentiment['tweet_count']
        print(f"    {i+1}/{len(top_codes)} {item['code']} 買{sentiment['buy_pct']}% 売{sentiment['sell_pct']}%")
        time.sleep(0.5)
    for item in data:
        if 'buy_pct' not in item:
            item['buy_pct'] = 50.0
            item['sell_pct'] = 50.0
            item['tweet_count'] = 0
    return data

def sector_totals(data):
    totals = {s: 0 for s in SECTORS}
    for d in data:
        if d['sector'] in totals:
            totals[d['sector']] += d['count']
    return totals

def sector_sentiment(data):
    """セクター別の加重平均売買感情を算出"""
    sector_buy = {s: [] for s in SECTORS}
    for d in data:
        if d['sector'] in sector_buy and d.get('buy_pct', 50) != 50:
            sector_buy[d['sector']].append({
                'buy': d['buy_pct'],
                'weight': d['count']
            })
    result = {}
    for s in SECTORS:
        items = sector_buy[s]
        if items:
            total_w = sum(i['weight'] for i in items)
            if total_w > 0:
                avg_buy = sum(i['buy'] * i['weight'] for i in items) / total_w
                result[s] = round(avg_buy, 1)
            else:
                result[s] = 50.0
        else:
            result[s] = 50.0
    return result

def calc_zscore(today_totals, date_str):
    past = []
    for i in range(1, 31):
        d = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=i)).strftime('%Y-%m-%d')
        fp = os.path.join(LOG_DIR, f'kabuline_{d}.json')
        if os.path.exists(fp):
            with open(fp, 'r', encoding='utf-8') as f:
                past.append(json.load(f).get('sector_totals', {}))
    if len(past) < 5:
        return None, len(past)
    zscores = {}
    for s in SECTORS:
        vals = [p.get(s, 0) for p in past]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        if std > 0:
            zscores[s] = round((today_totals.get(s, 0) - mean) / std, 2)
        else:
            zscores[s] = 0.0
    return zscores, len(past)

def print_results(totals, zscores, n_past, date_str, data, sent):
    W = 75
    print('\n' + '=' * W)
    print(f'  セクター別SNS注目度（株ライン）v2')
    print(f'  {date_str}')
    print('=' * W + '\n')
    rows = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    for s, cnt in rows:
        buy = sent.get(s, 50.0)
        emotion = "強気" if buy >= 60 else "弱気" if buy <= 40 else "中立"
        if zscores:
            z = zscores.get(s, 0)
            sig = '注目急上昇' if z > 2.0 else ''
            print(f'  {s:<16} 言及={cnt:>4} z={z:>6.2f} 買{buy:>5.1f}% {emotion} {sig}')
        else:
            print(f'  {s:<16} 言及={cnt:>4} z=蓄積中 買{buy:>5.1f}% {emotion}')
    print('\n' + '-' * W)
    print('  TOP20 話題銘柄')
    print('-' * W)
    top = sorted(data, key=lambda x: x['count'], reverse=True)[:20]
    for i, d in enumerate(top, 1):
        bp = d.get('buy_pct', 50)
        print(f"  {i:>2}. {d['code']} {d['count']:>3}件 買{bp:>5.1f}% ({d['sector']})")
    print('\n' + '=' * W)

def send_slack(totals, zscores, data, date_str, sent):
    lines = [f'*セクター別SNS注目度 v2* ({date_str})\n']
    if zscores:
        alerts = [(s, z) for s, z in zscores.items() if z > 2.0]
        if alerts:
            for s, z in sorted(alerts, key=lambda x: x[1], reverse=True):
                buy = sent.get(s, 50.0)
                emotion = "強気" if buy >= 60 else "弱気" if buy <= 40 else "中立"
                lines.append(f'  {s}: z={z:.2f} 言及={totals[s]} 買{buy:.1f}% {emotion}')
        else:
            lines.append('  アラートなし')
    else:
        lines.append('  データ蓄積中')
    lines.append('\n*話題銘柄TOP5*')
    top5 = sorted(data, key=lambda x: x['count'], reverse=True)[:5]
    for d in top5:
        bp = d.get('buy_pct', 50)
        lines.append(f"  {d['code']} {d['count']}件 買{bp:.1f}% ({d['sector']})")
    payload = json.dumps({'text': '\n'.join(lines)}).encode('utf-8')
    req = urllib.request.Request(SLACK_WEBHOOK, data=payload,
                                headers={'Content-Type': 'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req)
        print('Slack通知送信完了')
    except Exception as e:
        print(f'Slack通知エラー: {e}')

def save_log(data, totals, zscores, date_str, sent):
    os.makedirs(LOG_DIR, exist_ok=True)
    fp = os.path.join(LOG_DIR, f'kabuline_{date_str}.json')
    obj = {
        'date': date_str,
        'raw': data,
        'sector_totals': totals,
        'sector_sentiment': sent,
        'zscores': zscores
    }
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f'ログ保存: {fp}')

def main():
    date_str = datetime.today().strftime('%Y-%m-%d')
    print(f'株ライン話題株ランキング取得中... ({date_str})')
    data = scrape_with_sentiment(date_str)
    if not data:
        yesterday = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
        print(f'  当日データなし。前日({yesterday})を試行...')
        data = scrape_with_sentiment(yesterday)
        date_str = yesterday
    if not data:
        print('  データ取得失敗。終了。')
        return
    print(f'  取得銘柄数: {len(data)}')
    totals = sector_totals(data)
    sent = sector_sentiment(data)
    zscores, n_past = calc_zscore(totals, date_str)
    print_results(totals, zscores, n_past, date_str, data, sent)
    save_log(data, totals, zscores, date_str, sent)
    send_slack(totals, zscores, data, date_str, sent)

if __name__ == '__main__':
    main()
