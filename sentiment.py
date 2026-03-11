#!/usr/bin/env python3
"""
セクター別センチメント分析 v0.1
X API Free + LLMによるセンチメント判定
"""

import urllib.request
import urllib.parse
import json
import os
import time
from datetime import datetime

# ============================================================
# 設定
# ============================================================

BEARER_TOKEN = 'AAAAAAAAAAAAAAAAAAAAAPXc2QEAAAAAWJ84OmKxIZMMYQP64oJfX2oNZd8%3Dvo5kMSvov59vDd0LdR6pAMaCSahprXkWY0tzTwxyZhko97PX4K'

SLACK_WEBHOOK_URL = 'SLACK_WEBHOOK_URL

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Free枠は月1,500件。まず注目セクターに絞る
SECTOR_QUERIES = {
    '電機・精密': '半導体株 OR AI半導体 OR 電機株 OR ソニー株',
    '情報通信': 'IT株 OR SaaS OR DX株 OR 情報通信株',
    '銀行': '銀行株 OR メガバンク OR 金利上昇 銀行 OR 利ざや',
    '建設・資材': '建設株 OR ゼネコン OR 建設受注',
    '商社・卸売': '商社株 OR 総合商社 OR バフェット 商社',
}

MAX_TWEETS_PER_SECTOR = 20

# ============================================================
# X API ツイート取得
# ============================================================

def search_tweets(query, max_results=10):
    url = 'https://api.twitter.com/2/tweets/search/recent'
    params = {
        'query': f'{query} lang:ja -is:retweet',
        'max_results': min(max_results, 100),
        'tweet.fields': 'created_at,public_metrics',
    }

    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        full_url,
        headers={
            'Authorization': f'Bearer {BEARER_TOKEN}',
            'Content-Type': 'application/json',
        },
    )

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            if 'data' in data:
                return data['data']
            return []
    except urllib.error.HTTPError as e:
        print(f"  APIエラー: {e.code} {e.reason}")
        return []
    except Exception as e:
        print(f"  エラー: {e}")
        return []

# ============================================================
# 簡易センチメント判定（キーワードベース v0.1）
# LLM版は Phase 2 で差し替え
# ============================================================

BULLISH_WORDS = [
    '買い', '仕込み', '上がる', '上昇', '強い', '割安', '反発',
    '好決算', '増配', '最高益', '期待', 'チャンス', '底打ち',
    '出遅れ', 'ブレイク', '注目', '有望', '押し目',
]

BEARISH_WORDS = [
    '売り', '下がる', '下落', '弱い', '割高', '天井', '暴落',
    '悪決算', '減配', '赤字', '危険', 'リスク', '逃げ',
    'ショート', '崩壊', 'バブル', '過熱', '利確',
]

def judge_sentiment(text):
    bull_count = sum(1 for w in BULLISH_WORDS if w in text)
    bear_count = sum(1 for w in BEARISH_WORDS if w in text)

    if bull_count > bear_count:
        return 'bullish'
    elif bear_count > bull_count:
        return 'bearish'
    else:
        return 'neutral'

# ============================================================
# セクター別センチメント集計
# ============================================================

def analyze_sectors():
    results = []

    for sector, query in SECTOR_QUERIES.items():
        print(f"  取得中: {sector}...")
        tweets = search_tweets(query, max_results=MAX_TWEETS_PER_SECTOR)
        time.sleep(1)

        if not tweets:
            print(f"    ツイート取得できず")
            results.append({
                'セクター': sector,
                'ツイート数': 0,
                '強気': 0,
                '弱気': 0,
                '中立': 0,
                '強気比率': 0.0,
                'センチメントスコア': 0.0,
                'シグナル': '-',
                'alert': False,
                'サンプル': [],
            })
            continue

        sentiments = []
        samples = []
        for tweet in tweets:
            text = tweet['text']
            s = judge_sentiment(text)
            sentiments.append(s)
            if len(samples) < 3:
                samples.append({
                    'text': text[:80],
                    'sentiment': s,
                })

        total = len(sentiments)
        bull = sentiments.count('bullish')
        bear = sentiments.count('bearish')
        neutral = sentiments.count('neutral')

        bull_ratio = bull / total * 100 if total > 0 else 0
        # センチメントスコア: -1(全弱気) 〜 +1(全強気)
        score = (bull - bear) / total if total > 0 else 0

        signal = '-'
        if score > 0.3:
            signal = '強気優勢'
        elif score < -0.3:
            signal = '弱気優勢'

        results.append({
            'セクター': sector,
            'ツイート数': total,
            '強気': bull,
            '弱気': bear,
            '中立': neutral,
            '強気比率': round(bull_ratio, 1),
            'センチメントスコア': round(score, 2),
            'シグナル': signal,
            'alert': signal != '-',
            'サンプル': samples,
        })

    return results

# ============================================================
# 表示
# ============================================================

def print_results(results, date_str):
    W = 62
    print(f"\n{'=' * W}")
    print(f"  セクター別センチメント分析（X/Twitter）")
    print(f"  {date_str}")
    print(f"{'=' * W}\n")

    for r in results:
        mark = '🔔' if r['alert'] else '  '
        bar_len = int(abs(r['センチメントスコア']) * 20)
        if r['センチメントスコア'] > 0:
            bar = '🟢' * bar_len
        elif r['センチメントスコア'] < 0:
            bar = '🔴' * bar_len
        else:
            bar = '⚪'

        print(f"  {r['セクター']:<12} {bar} スコア={r['センチメントスコア']:>5}  "
              f"強気{r['強気']}/ 弱気{r['弱気']}/ 中立{r['中立']}  "
              f"(n={r['ツイート数']}) {mark} {r['シグナル']}")

    # サンプルツイート表示
    print(f"\n{'-' * W}")
    print(f"  サンプルツイート")
    print(f"{'-' * W}")
    for r in results:
        if r['サンプル']:
            print(f"\n  [{r['セクター']}]")
            for s in r['サンプル']:
                emoji = '🟢' if s['sentiment'] == 'bullish' else '🔴' if s['sentiment'] == 'bearish' else '⚪'
                print(f"    {emoji} {s['text']}...")

    print(f"\n{'=' * W}")

# ============================================================
# Slack通知
# ============================================================

def send_slack(results, date_str):
    lines = [f"*セクター別センチメント* ({date_str})\n"]

    for r in results:
        if r['ツイート数'] == 0:
            continue
        if r['センチメントスコア'] > 0:
            emoji = '🟢'
        elif r['センチメントスコア'] < 0:
            emoji = '🔴'
        else:
            emoji = '⚪'

        alert = ' 🔔' if r['alert'] else ''
        lines.append(
            f"  {emoji} {r['セクター']}: スコア={r['センチメントスコア']} "
            f"(強気{r['強気']}/弱気{r['弱気']}/中立{r['中立']}, n={r['ツイート数']}){alert}"
        )

    # A・B・Cとの突合コメント
    lines.append(f"\n_※ セクターローテーション感知（A・B・C）のアラートと併せて判断してください_")

    text = '\n'.join(lines)
    payload = json.dumps({'text': text}).encode('utf-8')
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req)
        print("Slack通知送信完了")
    except Exception as e:
        print(f"Slack通知エラー: {e}")

# ============================================================
# ログ保存
# ============================================================

def save_log(results, date_str):
    os.makedirs(LOG_DIR, exist_ok=True)
    # サンプルツイートはログに含める
    filepath = os.path.join(LOG_DIR, f"sentiment_{date_str}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"ログ保存: {filepath}")

# ============================================================
# メイン
# ============================================================

def main():
    date_str = datetime.today().strftime('%Y-%m-%d')
    print("X API センチメント分析開始...")
    results = analyze_sectors()
    print_results(results, date_str)
    save_log(results, date_str)
    send_slack(results, date_str)

if __name__ == '__main__':
    main()
