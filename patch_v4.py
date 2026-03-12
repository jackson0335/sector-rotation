filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

old_css = '.hi{{background:#fff3e0}'
new_css = '.bullish{{background:#e8f5e9;color:#2e7d32}}.bearish{{background:#ffebee;color:#c62828}'
code = code.replace(old_css, new_css)

print("置換数:", code.count('.bullish{{'))

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)
print("CSS修正完了")
