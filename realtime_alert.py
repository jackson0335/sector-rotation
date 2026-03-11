#!/usr/bin/env python3
import yfinance as yf
import datetime as dt
import json, os, requests

TOPIX_ETF = "1306.T"
SECTOR_ETFS = {
    "食品":"1617.T","エネルギー資源":"1618.T","建設・資材":"1619.T",
    "素材・化学":"1620.T","医薬品":"1621.T","自動車・輸送機":"1622.T",
    "鉄鋼・非鉄":"1623.T","機械":"1624.T","電機・精密":"1625.T",
    "情報通信・サービスその他":"1626.T","電気・ガス":"1627.T",
    "運輸・物流":"1628.T","商社・卸売":"1629.T","小売":"1630.T",
    "銀行":"1631.T","金融（除く銀行）":"1632.T","不動産":"1633.T",
}
WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
STATE_FILE = os.path.expanduser("~/sector_rotation/logs/realtime_state.json")
LOG_DIR = os.path.expanduser("~/sector_rotation/logs")

def get_intraday_rs():
    tickers = [TOPIX_ETF] + list(SECTOR_ETFS.values())
    data = yf.download(tickers, period="5d", interval="1d", auto_adjust=True, progress=False)
    close = data["Close"].dropna(subset=[TOPIX_ETF])
    if len(close) < 2:
        return {}
    result = {}
    for name, etf in SECTOR_ETFS.items():
        if etf in close.columns:
            rs = close[etf] / close[TOPIX_ETF]
            rs_now = float(rs.iloc[-1])
            rs_prev = float(rs.iloc[-2])
            rs_chg = (rs_now / rs_prev - 1) * 100
            result[name] = round(rs_chg, 2)
    return result

def load_prev_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)

def send_slack(text):
    if not WEBHOOK:
        print(f"[Slack skip] {text}")
        return
    try:
        requests.post(WEBHOOK, json={"text": text}, timeout=10)
    except Exception as e:
        print(f"[Slack error] {e}")

def main():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    if now.hour < 9 or now.hour >= 15:
        print(f"[{now.strftime('%H:%M')}] 取引時間外")
        return
    rs = get_intraday_rs()
    if not rs:
        print("データ取得失敗")
        return
    prev = load_prev_state()
    alerts = []
    for sector, chg in rs.items():
        prev_chg = prev.get(sector, 0)
        if abs(chg - prev_chg) >= 1.0:
            direction = "UP" if chg > prev_chg else "DOWN"
            alerts.append(f"  {direction} {sector} RS {prev_chg:+.1f}% -> {chg:+.1f}%")
    if alerts:
        msg = f"Sector alert {now.strftime('%H:%M')}\n" + "\n".join(alerts)
        send_slack(msg)
        print(msg)
    else:
        print(f"[{now.strftime('%H:%M')}] no change")
    save_state(rs)

if __name__ == "__main__":
    main()
