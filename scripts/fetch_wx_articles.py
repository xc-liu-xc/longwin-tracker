"""Fetch WeChat public articles linked in holdings history → merge into docs/content.json.

Only handles new-format URLs (mp.weixin.qq.com/s/xxx).
Old-format (__biz=) URLs require WeChat client and are skipped.
"""
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

DATA_PATH    = "docs/data.json"
CONTENT_PATH = "docs/content.json"
TZ_CN = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def is_new_format(url: str) -> bool:
    return "mp.weixin.qq.com/s/" in url and "__biz=" not in url


def wx_url_key(url: str) -> str:
    """Stable key for dedup: strip tracking params after '?'."""
    return url.split("?")[0].strip()


def fetch_wx_article(url: str, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"  rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
        except Exception as e:
            print(f"  fetch error: {e}")
            if attempt < retries - 1:
                time.sleep(1)
            continue

        html = r.text

        # Deleted / blocked
        if "该内容已被发布者删除" in html or "此内容因违规无法查看" in html:
            print("  deleted/blocked")
            return None

        # Title: og:title is most reliable
        title = ""
        m = re.search(r'<meta property="og:title" content="(.*?)"', html)
        if m:
            title = m.group(1).strip()
        if not title:
            m = re.search(r'<title>(.*?)</title>', html)
            if m:
                title = re.sub(r'\s+', ' ', m.group(1)).strip()

        # Publish timestamp → date string
        date_str = ""
        m = re.search(r'var ct = "(\d+)"', html)
        if m:
            ts = int(m.group(1))
            date_str = datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d")

        # Content: js_content div
        content = ""
        m = re.search(
            r'id="js_content"[^>]*>(.*?)<div[^>]+id="js_pc_qr_code"',
            html, re.DOTALL
        )
        if m:
            raw = m.group(1)
            # Keep paragraph breaks, strip tags
            raw = re.sub(r'</p>', '\n', raw)
            raw = re.sub(r'<br\s*/?>', '\n', raw)
            raw = re.sub(r'<[^>]+>', '', raw)
            raw = re.sub(r'&nbsp;', ' ', raw)
            raw = re.sub(r'&amp;', '&', raw)
            raw = re.sub(r'&lt;', '<', raw)
            raw = re.sub(r'&gt;', '>', raw)
            content = re.sub(r'\n{3,}', '\n\n', raw).strip()

        if not title and not content:
            print("  no title or content found")
            return None

        return {
            "title":      title,
            "summary":    "",
            "content":    content,
            "createDate": date_str,
            "tags":       [],
            "source":     "weixin",
        }

    return None


def collect_wx_links(data: dict) -> dict:
    """Return {url_key: {url, date, action, fund}} for all new-format WX links."""
    links = {}
    for h in data.get("holdings", []):
        for hist in h.get("history") or []:
            url = hist.get("articleLink") or ""
            if "mp.weixin.qq.com" in url and is_new_format(url):
                key = wx_url_key(url)
                if key not in links:
                    links[key] = {
                        "url":    url,
                        "date":   hist.get("date", ""),
                        "action": hist.get("action", ""),
                        "fund":   h.get("fundName", ""),
                    }
    return links


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    existing: dict = {}
    if os.path.exists(CONTENT_PATH):
        with open(CONTENT_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    wx_links = collect_wx_links(data)
    new_keys  = [k for k in wx_links if k not in existing]
    new_keys.sort(key=lambda k: wx_links[k]["date"])

    print(f"[wx] {len(wx_links)} new-format WX links, {len(existing)} cached, {len(new_keys)} to fetch")

    fetched = skipped = 0
    for key in new_keys:
        info = wx_links[key]
        print(f"[wx] {info['date']} {info['action']} {info['fund'][:20]}")
        print(f"     {info['url'][:70]}")

        article = fetch_wx_article(info["url"])
        if article:
            existing[key] = article
            fetched += 1
            print(f"  ✓ {article['title'][:60]}")
        else:
            skipped += 1

        time.sleep(0.8)

    with open(CONTENT_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(f"\n[wx] done: {fetched} fetched, {skipped} skipped, {len(existing)} total — {now_cn}")


if __name__ == "__main__":
    main()
