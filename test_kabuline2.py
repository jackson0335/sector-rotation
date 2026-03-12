import requests
from bs4 import BeautifulSoup

url = "https://kabuline.com/search/tw/6740/?frame=true"
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

res = requests.get(url, headers=headers, timeout=10)
soup = BeautifulSoup(res.text, 'html.parser')

tweets = []
for li in soup.select('li.tweet_list'):
    user_el = li.select_one('span.tweet_icon_span')
    text_el = li.select_one('div.TweetPopText')
    time_el = li.select_one('span.time')
    if not text_el:
        text_el = li.select_one('p.tx')
    username = user_el.get_text(strip=True) if user_el else "不明"
    text = text_el.get_text(strip=True) if text_el else "不明"
    time = time_el.get_text(strip=True) if time_el else "不明"
    if username and text and "限定公開" not in text:
        tweets.append({"user": username, "time": time, "text": text})

print(f"取得ツイート数: {len(tweets)}")
for i, tw in enumerate(tweets[:10]):
    print(f"\n--- {i+1} ---")
    print(f"@{tw['user']} ({tw['time']})")
    print(tw['text'][:120])

buy_el = soup.select_one('p.buy')
sell_el = soup.select_one('p.sell')
if buy_el and sell_el:
    print(f"\n売買感情: {buy_el.get_text(strip=True)} / {sell_el.get_text(strip=True)}")
