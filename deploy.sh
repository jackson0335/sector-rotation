#!/bin/bash
# ダッシュボード生成 → GitHub Pagesにデプロイ
cd ~/sector_rotation
python3 integrated_dashboard.py
cp dashboard_integrated.html docs/index.html
cd docs
git add -A
git commit -m "update $(date +%Y-%m-%d_%H:%M)"
git push
