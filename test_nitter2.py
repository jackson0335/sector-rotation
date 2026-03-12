import requests
from bs4 import BeautifulSoup

username = "nepekabu"
instances = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.catsarch.com"
]

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

for inst in instances:
    url = f"{inst}/{username}"
    print("試行中:", url)
    try:
        res = requests.get(url, headers=headers, timeout=10)
        print("ステータス:", res.status_code)
        soup = BeautifulSoup(res.text, 'html.parser')
        tweets = soup.select('.timeline-item .tweet-content')
        if tweets:
            for i, tw in enumerate(tweets[:5]):
                print(f"--- ツイート{i+1} ---")
                print(tw.get_text(strip=True))
            print(f"\n取得成功: {len(tweets[:5])}件 from {inst}")
            break
        else:
            print("ツイート要素なし。HTML長さ:", len(res.text))
    except Exception as e:
        print("エラー:", str(e))
else:
    print("全インスタンス失敗。")
