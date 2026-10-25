#!/bin/bash
# 在用户 Mac 上定时运行（launchd：~/Library/LaunchAgents/app.autodecoder.uscis-charts.plist）。
# USCIS 网站会拦 GitHub 的云服务器，家庭网络能正常访问，所以「本月用表A还是表B」由这台 Mac 抓取后推到仓库，
# 云端任务看到数据变了会自动重写当期文章。只抓网页、推数据，不调用 Claude。
# 操作的是 ~/.autodecoder/autodecoder-site 这份克隆（macOS 不允许后台任务访问「桌面」文件夹）。
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== $(date '+%F %T')"
/usr/bin/git pull -q --rebase --autostash
/usr/bin/python3 -I _pdt/update.py --charts-only
if /usr/bin/git diff --quiet -- _pdt/data/uscis-charts.json; then
  echo "USCIS 用表无变化"
  exit 0
fi
/usr/bin/git add _pdt/data/uscis-charts.json
/usr/bin/git -c user.name="autodecoder-mac" -c user.email="contact@autodecoder.app" commit -q -m "USCIS 用表更新（Mac 定时任务）"
/usr/bin/git pull -q --rebase
/usr/bin/git push -q
echo "已推送 USCIS 用表更新"
