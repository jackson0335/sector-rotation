#!/usr/bin/env python3
"""3つの修正を適用"""

filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

# 修正1: d_buy_pctをJSON出力に追加
old_save = '"d_z":round(d_z,1),'
new_save = '"d_z":round(d_z,1),"d_buy_pct":round(d_buy_pct,1),'
code = code.replace(old_save, new_save)

# 修正2: 個別株テーブルのD列をd_countからd_zに変更、色分け修正
# 高い=緑、低い=赤に統一
old_stock_d = '>{st["d_count"]}</td></tr>'
new_stock_d = '>{st.get("d_z",0):.1f}</td></tr>'
code = code.replace(old_stock_d, new_stock_d)

# 修正3: 個別株テーブルのA/B/Cセル色分け - オレンジを緑/赤に変更
# 現在のa_cls, b_cls, c_cls, d_clsの定義を探して修正
old_color = ' style="background:#fff3e0"'
if old_color in code:
    print("オレンジ背景(#fff3e0)発見、修正対象を確認中...")

# a_cls/b_cls/c_cls/d_clsの定義箇所を確認
import re
# 色定義パターンを探す
matches = list(re.finditer(r'(a_cls|b_cls|c_cls|d_cls)\s*=', code))
for m in matches:
    start = m.start()
    snippet = code[start:start+200]
    print(f"  {snippet[:100]}")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print("\n修正1: d_buy_pctをJSON出力に追加 - 完了")
print("修正2: 個別株テーブルD列をd_z表示に変更 - 完了")
print("修正3: 色分けは定義箇所を確認中 - 上記出力を確認してください")
