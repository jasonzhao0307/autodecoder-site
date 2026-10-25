#!/usr/bin/env python3
"""每天拉一次最新数据，并入 _pdt/data/（GitHub Actions 定时跑，也可本地跑）。

用法：python3 _pdt/update.py [--charts-only]
1. 排期：Priority Date Tracker 后端的免费接口 GET /current（app 用户众包上报的最新一期 + 上一期）。
   政府站有 Cloudflare，服务器直连抓不到，所以不直接抓 travel.state.gov。
2. USCIS 本月用表A还是表B：抓 USCIS「Adjustment of Status Filing Charts」页面的原文。
任一步失败只打警告、保留已有数据，不影响后面生成网页。
"""
import json, pathlib, re, sys, urllib.request

DATA = pathlib.Path(__file__).resolve().parent / "data"
CURRENT_API = "https://k7eixdawjhrg5gu6n6wvhi3vhm0qbfdv.lambda-url.us-east-1.on.aws/current"
USCIS = "https://www.uscis.gov/green-card/green-card-processes-and-procedures/visa-availability-priority-dates/adjustment-of-status-filing-charts-from-the-visa-bulletin"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def prev_month(m):
    y, mo = map(int, m.split("-"))
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def merge_month(months, month, dates, released=None):
    """按格子合并（不删格子）：服务器某期缺的格子不会冲掉这里已有的。返回是否有变化。"""
    e = months.setdefault(month, {"released": released, "cells": {}})
    changed = False
    if released and not e.get("released"):
        e["released"] = released
        changed = True
    for v in (dates or {}).values():
        if not v.get("category") or not v.get("country") or not v.get("finalActionDate"):
            continue
        key = f"{v['category']}|{v['country']}"
        new = {"A": v["finalActionDate"], "B": v.get("filingDate")}
        if e["cells"].get(key) != new:
            e["cells"][key] = new
            changed = True
    return changed


def update_bulletins():
    path = DATA / "bulletins.json"
    db = json.loads(path.read_text())
    cur = json.loads(get(CURRENT_API))
    month = cur.get("bulletinMonth")
    if not month or not re.fullmatch(r"\d{4}-\d{2}", month) or len(cur.get("dates") or {}) < 20:
        raise ValueError(f"/current 数据不完整: {str(cur)[:200]}")
    released = (cur.get("releasedAt") or "")[:10] or None
    changed = merge_month(db["months"], month, cur["dates"], released)
    if cur.get("previousDates"):
        changed |= merge_month(db["months"], prev_month(month), cur["previousDates"])
    if changed:
        db["months"] = dict(sorted(db["months"].items()))
        path.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n")
    print(f"bulletins: 最新 {month}，{'有更新' if changed else '无变化'}", file=sys.stderr)


def update_charts():
    path = DATA / "uscis-charts.json"
    charts = json.loads(path.read_text()) if path.exists() else {}
    text = re.sub(r"<[^>]+>", " ", get(USCIS))
    text = re.sub(r"\s+", " ", text.replace("&nbsp;", " "))
    pat = re.compile(r"For all (family-sponsored|employment-based) preference categories,? you must use the "
                     r"(Dates for Filing|Final Action Dates?) chart in the Department of State Visa Bulletin for (\w+) (\d{4})", re.I)
    found = 0
    for kind, chart, mon, year in pat.findall(text):
        if mon.capitalize() not in MONTHS:
            continue
        m = f"{year}-{MONTHS.index(mon.capitalize()) + 1:02d}"
        k = "family" if kind.lower().startswith("family") else "employment"
        charts.setdefault(m, {})[k] = "B" if chart.lower().startswith("dates for filing") else "A"
        found += 1
    if not found:
        raise ValueError("USCIS 页面里没找到用表说明（页面改版？）")
    path.write_text(json.dumps(dict(sorted(charts.items())), indent=1) + "\n")
    print(f"uscis-charts: 解析到 {found} 条", file=sys.stderr)


if __name__ == "__main__":
    # --charts-only：只更新 USCIS 用表（用户 Mac 上的定时任务用；USCIS 会拦 GitHub 的云服务器）
    steps = (update_charts,) if "--charts-only" in sys.argv else (update_bulletins, update_charts)
    for step in steps:
        try:
            step()
        except Exception as e:   # 网络/格式问题：保留旧数据，照常生成网页
            print(f"⚠️ {step.__name__} 失败: {e}", file=sys.stderr)
