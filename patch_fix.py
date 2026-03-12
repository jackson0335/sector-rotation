filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

old = '''        "sensor_history":sensor_history,
        # 個別株にSNSカウントとセクターd_zを付与
        sns_by_code = {}
        for p in sns_for_sec:
            pc = str(p.get("code",""))
            sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
        for st in stock_list:
            st["d_z"] = round(d_z, 1)
            st["d_count"] = sns_by_code.get(st["code"], 0)
            st["d_buy_pct"] = round(d_buy_pct, 1)
        "stocks":stock_list,"sns_posts":sns_for_sec
    })'''

new = '''        "sensor_history":sensor_history,
        "stocks":stock_list,"sns_posts":sns_for_sec
    })

    # 個別株にSNSカウントとセクターd_zを付与
    sns_by_code = {}
    for p in sns_for_sec:
        pc = str(p.get("code",""))
        sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
    for st in stock_list:
        st["d_z"] = round(d_z, 1)
        st["d_count"] = sns_by_code.get(st["code"], 0)
        st["d_buy_pct"] = round(d_buy_pct, 1)'''

code = code.replace(old, new)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)
print("修正完了")
