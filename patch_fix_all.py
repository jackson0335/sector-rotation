#!/usr/bin/env python3
filepath = "integrated_dashboard.py"
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

fixes = 0

# === 1. 京都FGのD=0問題: 個別株Dをbuy_pctベースに修正 ===
old_stock_d = '''    # 個別株にSNS言及数を付与し、銘柄別d_zを計算
    sns_by_code = {}
    for p in sns_for_sec:
        pc = str(p.get("code",""))
        sns_by_code[pc] = sns_by_code.get(pc,0) + p.get("count",0)
    # セクター内の総言及数
    sec_total_mentions = sum(sns_by_code.values()) if sns_by_code else 0
    for st in stock_list:
        st_mentions = sns_by_code.get(st["code"], 0)
        st["d_count"] = st_mentions
        st["d_buy_pct"] = round(d_buy_pct, 1)
        # 銘柄別d_z: 言及がある銘柄はセクターd_zを言及割合で重み付け
        if sec_total_mentions > 0 and st_mentions > 0:
            mention_share = st_mentions / sec_total_mentions
            # 言及シェアが高い銘柄ほどセクターd_zに近い値、低い銘柄はd_zが小さい
            st["d_z"] = round(d_z * min(mention_share * len(sns_by_code), 2.0), 1)
        else:
            st["d_z"] = 0.0  # 言及なし=SNS注目なし'''

new_stock_d = '''    # 個別株のD値: buy_pctがある銘柄のみ算出、なければ0
    kbl_stock_map = {ks["code"]: ks for ks in kbl_stocks if ks.get("buy_pct") is not None}
    for st in stock_list:
        ks = kbl_stock_map.get(st["code"])
        if ks:
            st["d_z"] = round((ks["buy_pct"] - 50) / 25, 1)
            st["d_count"] = ks.get("tweet_count", 0)
            st["d_buy_pct"] = ks["buy_pct"]
        else:
            st["d_z"] = 0.0
            st["d_count"] = 0
            st["d_buy_pct"] = 0.0'''

if old_stock_d in code:
    code = code.replace(old_stock_d, new_stock_d)
    fixes += 1
    print("1. 個別株D修正: OK")
else:
    print("1. 個別株D修正: 対象なし")

# === 2. チャートからD線・Dマーカー削除 ===
old_d_line = '''    // D線（紫）
    if(dH&&dH.length>=2){cx.strokeStyle='#ab47bc';cx.lineWidth=1.5;cx.beginPath();
    dH.forEach((v,i)=>{const x=p.l+i/(dH.length-1)*cw,y=p.t+ch-(v-mn)/rg*ch;i===0?cx.moveTo(x,y):cx.lineTo(x,y)});cx.stroke();
    cx.fillStyle='#ab47bc';cx.font='bold 11px sans-serif';const dy2=p.t+ch-(dH[dH.length-1]-mn)/rg*ch;cx.fillText('D:'+dH[dH.length-1].toFixed(1),W-p.r-50,dy2-4)}
    const dx=W-p.r-20,dzy=p.t+ch-(0-mn)/rg*ch;
    const dy=p.t+ch-(s.d_z-mn)/rg*ch;
    cx.font='10px sans-serif';cx.fillText('D:'+s.d_z.toFixed(1),dx-25,dy-8);'''

new_d_line = '''    const dx=W-p.r-20;'''

if old_d_line in code:
    code = code.replace(old_d_line, new_d_line)
    fixes += 1
    print("2. D線削除: OK")
else:
    print("2. D線削除: 対象なし")

# === 3. C重複マーカー削除 ===
old_c_marker = '''    // C現在値（橙マーカー）
    const cy_=p.t+ch-(s.c_z-mn)/rg*ch;
    cx.fillStyle='#ffa726';cx.beginPath();cx.arc(dx-40,cy_,5,0,Math.PI*2);cx.fill();
    cx.font='10px sans-serif';cx.fillText('C:'+s.c_z.toFixed(1),dx-65,cy_-8);'''

if old_c_marker in code:
    code = code.replace(old_c_marker, '')
    fixes += 1
    print("3. C重複マーカー削除: OK")
else:
    print("3. C重複マーカー削除: 対象なし")

# === 4. 凡例からD削除、出来高*の説明追加 ===
old_legend = "cx.fillText('緑=A(売買代金) 青=B(勢い) 橙●=C(感応度) 紫●=D(SNS)',p.l,p.t+ch+18+15);"
new_legend = "cx.fillText('緑=A(売買代金) 青=B(勢い) 橙=C(感応度)',p.l,p.t+ch+18+15);"

if old_legend in code:
    code = code.replace(old_legend, new_legend)
    fixes += 1
    print("4. 凡例修正: OK")
else:
    print("4. 凡例修正: 対象なし")

# === 5. 出来高*の説明追加（テーブルヘッダー） ===
old_vol_header = '<th style="text-align:right">出来高</th>'
new_vol_header = '<th style="text-align:right">出来高<br><span style="font-weight:normal;font-size:10px">*=5日/60日&gt;1.5倍</span></th>'

if old_vol_header in code:
    code = code.replace(old_vol_header, new_vol_header)
    fixes += 1
    print("5. 出来高*説明追加: OK")
else:
    print("5. 出来高*説明追加: 対象なし")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)

print(f"\n=== {fixes}箇所修正完了 ===")
