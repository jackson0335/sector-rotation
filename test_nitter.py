from ntscraper import Nitter

scraper = Nitter(log_level=1, skip_instance_check=True)
username = "nepekabu"

instances = [
    "https://nitter.poast.org",
    "https://nitter.catsarch.com",
    "https://xcancel.com",
    "https://nitter.space",
    "https://lightbrd.com",
    "https://nitter.privacyredirect.com",
    "https://nuku.trabun.org"
]

for inst in instances:
    print("試行中:", inst)
    try:
        tweets = scraper.get_tweets(username, mode='user', number=5, instance=inst)
        if tweets and 'tweets' in tweets and len(tweets['tweets']) > 0:
            for t in tweets['tweets']:
                print("---")
                print("日時:", t.get('date', 'N/A'))
                print("本文:", t.get('text', 'N/A'))
            print("取得成功:", len(tweets['tweets']), "件 from", inst)
            break
        else:
            print("空結果:", inst)
    except Exception as e:
        print("エラー:", inst, str(e))
else:
    print("全インスタンス失敗。")
