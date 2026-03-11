
import datetime as _dt
def _get_latest_confirmed_date(df):
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9)))
    if now.hour < 16:
        confirmed = df.index[-2] if len(df) > 1 else df.index[-1]
    else:
        confirmed = df.index[-1]
    return confirmed

#!/usr/bin/env python3
"""業種別売買代金（主要銘柄ベース）"""
import yfinance as yf
import pandas as pd
import numpy as np
import json, os, urllib.request
from datetime import datetime

SLACK_WEBHOOK = 'SLACK_WEBHOOK_URL
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')

SECTOR_STOCKS = {
    '食品':['2914.T','2802.T','2269.T','2501.T','2502.T'],
    'エネルギー資源':['1605.T','5020.T','5019.T'],
    '建設・資材':['1925.T','1928.T','1801.T','1802.T','5233.T'],
    '素材・化学':['4063.T','4188.T','4452.T','4901.T','4911.T','4005.T'],
    '医薬品':['4502.T','4503.T','4519.T','4568.T','4507.T','4523.T'],
    '自動車・輸送機':['7203.T','7267.T','7269.T','7270.T','6902.T'],
    '鉄鋼・非鉄':['5401.T','5411.T','5706.T','5713.T','5803.T','5802.T'],
    '機械':['6301.T','6305.T','7011.T','7012.T','6367.T'],
    '電機・精密':['6501.T','6503.T','6758.T','6857.T','6920.T','6594.T','8035.T','6981.T'],
    '情報通信・サービスその他':['9984.T','9432.T','9433.T','9434.T','4689.T','6098.T'],
    '電気・ガス':['9501.T','9502.T','9503.T','9531.T'],
    '運輸・物流':['9020.T','9021.T','9101.T','9104.T','9107.T'],
    '商社・卸売':['8001.T','8002.T','8031.T','8053.T','8058.T'],
    '小売':['3382.T','8267.T','9983.T','7532.T'],
    '銀行':['8306.T','8316.T','8411.T','8308.T'],
    '金融（除く銀行）':['8591.T','8593.T','8697.T','8725.T','8766.T'],
    '不動産':['8801.T','8802.T','8830.T','3291.T']
}

SECTORS = list(SECTOR_STOCKS.keys())

def fetch_data():
    all_tickers = []
    for tl in SECTOR_STOCKS.values():
        all_tickers.extend(tl)
    all_tickers = list(set(all_tickers))
    print(f'  銘柄数: {len(all_tickers)}')
    raw = yf.download(all_tickers, period='100d', threads=True, timeout=30, progress=False)
    close = raw['Close']
    volume = raw['Volume']
    turnover = close * volume
    return turnover

def sector_daily(turnover):
    sector_tv = pd.DataFrame(index=turnover.index)
    for sec, tickers in SECTOR_STOCKS.items():
        cols = [t for t in tickers if t in turnover.columns]
        if cols:
            sector_tv[sec] = turnover[cols].sum(axis=1)
        else:
            sector_tv[sec] = 0.0
    return sector_tv

def calc_zscore(sector_tv):
    shifted = sector_tv.shift(1)
    mean = shifted.rolling(60, min_periods=20).mean()
    std = shifted.rolling(60, min_periods=20).std()
    z = (sector_tv - mean) / std
    return z

def print_results(sector_tv, zscores, date_str):
    W = 70
    print('\n' + '=' * W)
    print(f'  業種別売買代金（主要銘柄ベース）')
    print(f'  {date_str}')
    print('=' * W + '\n')
    today = sector_tv.iloc[-1]
    yest = sector_tv.iloc[-2] if len(sector_tv) > 1 else today
    z_today = zscores.iloc[-1]
    rows = today.sort_values(ascending=False)
    for sec in rows.index:
        tv_oku = today[sec] / 1e8
        chg = ((today[sec] / yest[sec]) - 1) * 100 if yest[sec] > 0 else 0
        z = z_today[sec]
        sig = '🔔 急増' if z > 2.0 else ('🔻 急減' if z < -2.0 else '-')
        if np.isnan(z):
            print(f'  {sec:<16} {tv_oku:>10.0f}億円  前日比{chg:>+6.1f}%  z=蓄積中')
        else:
            print(f'  {sec:<16} {tv_oku:>10.0f}億円  前日比{chg:>+6.1f}%  z={z:>6.2f}  {sig}')
    print('\n' + '=' * W)

def send_slack(sector_tv, zscores, date_str):
    today = sector_tv.iloc[-1]
    z_today = zscores.iloc[-1]
    alerts = [(s, z_today[s], today[s]/1e8) for s in SECTORS if not np.isnan(z_today[s]) and abs(z_today[s]) > 2.0]
    lines = [f'*業種別売買代金* ({date_str})\n']
    if alerts:
        for s, z, tv in sorted(alerts, key=lambda x: abs(x[1]), reverse=True):
            emoji = '🔔' if z > 0 else '🔻'
            lines.append(f'  {emoji} {s}: z={z:.2f} ({tv:.0f}億円)')
    else:
        lines.append('  アラートなし')
    payload = json.dumps({'text': '\n'.join(lines)}).encode('utf-8')
    req = urllib.request.Request(SLACK_WEBHOOK, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req)
        print('Slack通知送信完了')
    except Exception as e:
        print(f'Slack通知エラー: {e}')

def save_log(sector_tv, zscores, date_str):
    os.makedirs(LOG_DIR, exist_ok=True)
    today = sector_tv.iloc[-1]
    z_today = zscores.iloc[-1]
    obj = {
        'date': date_str,
        'sector_turnover': {s: round(float(today[s]), 0) for s in SECTORS},
        'sector_turnover_oku': {s: round(float(today[s] / 1e8), 1) for s in SECTORS},
        'zscores': {s: round(float(z_today[s]), 2) if not np.isnan(z_today[s]) else None for s in SECTORS}
    }
    fp = os.path.join(LOG_DIR, f'sector_volume_{date_str}.json')
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f'ログ保存: {fp}')

def main():
    print('業種別売買代金取得中...')
    turnover = fetch_data()
    sector_tv = sector_daily(turnover)
    zscores = calc_zscore(sector_tv)
    # 16時以前なら当日データ未確定のため除外
    from datetime import datetime as _dt
    _now = _dt.now()
    if _now.hour < 16 and sector_tv.index[-1].strftime('%Y-%m-%d') == _now.strftime('%Y-%m-%d'):
        sector_tv = sector_tv.iloc[:-1]
        zscores = zscores.iloc[:-1]
        print('  ※当日データ未確定のため前営業日を使用')
    date_str = sector_tv.index[-1].strftime('%Y-%m-%d')
    print(f'  データ末日: {date_str}')
    print_results(sector_tv, zscores, date_str)
    save_log(sector_tv, zscores, date_str)
    send_slack(sector_tv, zscores, date_str)

if __name__ == '__main__':
    main()
