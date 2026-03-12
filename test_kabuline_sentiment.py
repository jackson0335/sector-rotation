import requests
from bs4 import BeautifulSoup

url = "https://kabuline.com/search/tw/6740/?frame=true"
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

res = requests.get(url, headers=headers, timeout=10)
print("ステータス:", res.status_code)
print("HTML長さ:", len(res.text))

soup = BeautifulSoup(res.text, 'html.parser')

tweets = []
for li in soup.select('li'):
    text = li.get_text(strip=True)
    ems = li.select('em')
    if len(ems) >= 1:
        username = ems[0].get_text(strip=True).strip('_')
        content_parts = []
        for em in ems[1:]:
            content_parts.append(em.get_text(strip=True))
        if not content_parts:
            content_parts = [text]
        tweets.append({
            "user": username,
            "text": " ".join(content_parts)
        })

print(f"\n取得ツイート数: {len(tweets)}")
for i, tw in enumerate(tweets[:10]):
    print(f"\n--- {i+1} ---")
    print(f"ユーザー: @{tw['user']}")
    print(f"本文: {tw['text'][:100]}")
