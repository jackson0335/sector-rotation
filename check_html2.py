import requests
from bs4 import BeautifulSoup

url = "https://kabuline.com/search/tw/6740/?frame=true"
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

res = requests.get(url, headers=headers, timeout=10)
soup = BeautifulSoup(res.text, 'html.parser')

all_tags = {}
for tag in soup.find_all(True):
    cls = " ".join(tag.get('class', []))
    key = f"{tag.name}.{cls}" if cls else tag.name
    if key not in all_tags:
        all_tags[key] = tag.get_text(strip=True)[:80]

for key in sorted(all_tags.keys()):
    print(f"{key}: {all_tags[key]}")
