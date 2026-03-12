#!/usr/bin/env python3
"""最終パッチ: 個別株d_z追加 + SNS銘柄を個別株テーブルに統合"""

filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

changes = 0

# 修正1: 個別株のstock_listにd_zとd_countを追加
# stocksのappend部分を探して、d_zとd_buy_pctを追加
old_stock = '"d_count":0'
# 個別株にはセクター単位のd_zを入れる（個別株単位のSNSデータは持っていないため）
# まずstock_list構築部分を確認して、sns_for_secから銘柄別カウントを渡す

# stock辞書にd_z, d_countを追加する箇所を修正
# 現在stockにd_countがないのでテーブル表示が0になる
# sns_for_secからcode別のカウントを集計して各stockに入れる

# セクターループ内、stock_list構築の直前にコード挿入
old_stocklist = '        "stocks":stock_list,"sns_posts":sns_for_sec'
new_stocklist = '''        # 個別株にSNSカウントとセクターd_zを付与
        sns_by_code = {}
        for p in sns_for_sec:
            pc = str(p.get("code",""))
            sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
        for st in stock_list:
            st["d_z"] = round(d_z, 1)
            st["d_count"] = sns_by_code.get(st["code"], 0)
            st["d_buy_pct"] = round(d_buy_pct, 1)
        "stocks":stock_list,"sns_posts":sns_for_sec'''

code = code.replace(old_stocklist, new_stocklist)
changes += 1

# 修正2: SNS注目銘柄セクションにセクターd_zと買感情を表示
old_sns_header = "f'<h4>SNS注目銘柄（株ライン {kbl_date}）</h4>'"
new_sns_header = "f'<h4>SNS注目銘柄（株ライン {kbl_date}） D={s[\"d_z\"]:.1f}</h4>'"
if old_sns_header in code:
    code = code.replace(old_sns_header, new_sns_header)
    changes += 1

# 修正3: d_clsを個別株テーブルでd_zベースに修正（既に前パッチで対応済みだが確認）
old_dcls = 'd_cls = \' class="bullish"\' if st.get("d_z",0)>2 else (\' class="bearish"\' if st.get("d_z",0)<-2 else "")'
if old_dcls in code:
    print("d_cls: 既に修正済み")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print(f"パッチ適用: {changes}箇所")
print("修正内容:")
print("  1. 個別株にd_z(セクター値)とd_count(銘柄別言及数)を付与")
print("  2. SNSセクションにD値を表示")
