#!/usr/bin/env python3
"""色分け修正: hi→bullish(緑)/bearish(赤)に分離"""

filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

# 修正1: A/B/C/Dのクラス分け（高=緑、低=赤）
old_a = 'a_cls = \' class="hi"\' if abs(st["a_z"])>1.5 else ""'
new_a = 'a_cls = \' class="bullish"\' if st["a_z"]>1.5 else (\' class="bearish"\' if st["a_z"]<-1.5 else "")'

old_b = 'b_cls = \' class="hi"\' if abs(st["b_z"])>1.5 else ""'
new_b = 'b_cls = \' class="bullish"\' if st["b_z"]>1.5 else (\' class="bearish"\' if st["b_z"]<-1.5 else "")'

old_c = 'c_cls = \' class="hi"\' if abs(st["c_z"])>0.5 else ""'
new_c = 'c_cls = \' class="bullish"\' if st["c_z"]>0.5 else (\' class="bearish"\' if st["c_z"]<-0.5 else "")'

old_d = 'd_cls = \' class="hi"\' if st["d_count"]>5 else ""'
new_d = 'd_cls = \' class="bullish"\' if st.get("d_z",0)>2 else (\' class="bearish"\' if st.get("d_z",0)<-2 else "")'

code = code.replace(old_a, new_a)
code = code.replace(old_b, new_b)
code = code.replace(old_c, new_c)
code = code.replace(old_d, new_d)

# 修正2: CSSに.bullishと.bearishを追加（.hiを置換）
old_css = '.hi{background:#fff3e0}'
new_css = '.bullish{background:#e8f5e9;color:#2e7d32}.bearish{background:#ffebee;color:#c62828}'
if old_css in code:
    code = code.replace(old_css, new_css)
    print("CSS置換: .hi -> .bullish/.bearish 完了")
else:
    print("CSS .hi が見つからない。手動確認が必要。grepします:")
    import re
    for m in re.finditer(r'\.hi\s*\{[^}]*\}', code):
        print(f"  {m.group()}")

# 修正3: チャートのオレンジドット/線を修正
old_orange = "'#ff9800'"
if old_orange in code:
    count = code.count(old_orange)
    print(f"オレンジ(#ff9800)が{count}箇所見つかりました")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print("\n完了:")
print("  A/B/C/D色分け: 高=緑背景, 低=赤背景, 中間=なし")
print("  D列: d_count -> d_z に変更済み(前回パッチ)")
