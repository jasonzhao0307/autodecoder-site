#!/usr/bin/env python3
"""把新发布的 URL 推给 IndexNow（Bing / Yandex 等共用；ChatGPT 搜索走 Bing 索引）。

用法：python3 _pdt/indexnow.py new-urls.txt
密钥文件放在站点根目录：<key>.txt，内容就是 key 本身。
"""
import json, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOST = "autodecoder.app"

keys = [p for p in ROOT.glob("*.txt") if len(p.stem) == 32 and p.read_text().strip() == p.stem]
if not keys:
    sys.exit("找不到 IndexNow 密钥文件")
key = keys[0].stem
urls = [u.strip() for u in pathlib.Path(sys.argv[1]).read_text().splitlines() if u.strip()]
if not urls:
    sys.exit(0)
body = json.dumps({"host": HOST, "key": key, "keyLocation": f"https://{HOST}/{key}.txt", "urlList": urls}).encode()
req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, headers={"Content-Type": "application/json; charset=utf-8"})
with urllib.request.urlopen(req, timeout=30) as r:
    print(f"IndexNow: {r.status}，{len(urls)} 个 URL")
