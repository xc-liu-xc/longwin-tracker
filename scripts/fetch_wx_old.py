"""Fetch old-format WeChat articles (__biz= URLs) via AppleScript + Chrome.

These URLs require a real browser (JS bot-detection). Uses Chrome.app via
AppleScript to load each page and extract content.

Prerequisites:
  - Chrome.app installed
  - Chrome > View > Developer > Allow JavaScript from Apple Events enabled

Usage:
    python scripts/fetch_wx_old.py
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List

DATA_PATH    = "docs/data.json"
CONTENT_PATH = "docs/content.json"
TZ_CN = timezone(timedelta(hours=8))

_AS_TEMPLATE = r'''tell application "Google Chrome"
    activate
    set newTab to make new tab at end of tabs of window 1
    set URL of newTab to "%%URL%%"
    delay %%DELAY%%
    set jsCode to "
(function() {
  var t = document.querySelector('meta[property=\"og:title\"]');
  var title = t ? t.content : document.title;
  var ctM = document.body.innerHTML.match(/var ct = \"(\\d+)\"/);
  var ct = ctM ? ctM[1] : '';
  var el = document.getElementById('js_content');
  var content = el ? el.innerText : '';
  var body = document.body.innerText;
  var isErr = body.indexOf('%%ERR1%%') >= 0
           || body.indexOf('%%ERR2%%') >= 0
           || body.indexOf('%%ERR3%%') >= 0;
  return JSON.stringify({title:title.trim(), ct:ct,
    content:content.substring(0,8000), isErr:isErr, hasContent:!!el});
})()
"
    set jsResult to execute newTab javascript jsCode
    close newTab
    return jsResult
end tell
'''


def build_applescript(url: str, delay: int) -> str:
    safe_url = url.replace('"', '%22')
    return (
        _AS_TEMPLATE
        .replace('%%URL%%', safe_url)
        .replace('%%DELAY%%', str(delay))
        .replace('%%ERR1%%', '参数错误')
        .replace('%%ERR2%%', '已被发布者删除')
        .replace('%%ERR3%%', '内容不存在')
    )


def run_applescript(url: str, delay: int = 5) -> Optional[dict]:
    import tempfile
    script = build_applescript(url, delay)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript',
                                     delete=False, encoding='utf-8') as f:
        f.write(script)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["osascript", tmp_path],
            capture_output=True, text=True, timeout=35
        )
        if result.returncode != 0:
            print(f"  applescript error: {result.stderr.strip()[:100]}")
            return None
        out = result.stdout.strip()
        if not out:
            return None
        return json.loads(out)
    except subprocess.TimeoutExpired:
        print("  timeout")
        return None
    except json.JSONDecodeError as e:
        print(f"  json parse error: {e} — raw: {result.stdout[:100]}")
        return None
    finally:
        os.unlink(tmp_path)


def wx_url_key(url: str) -> str:
    """Stable key: just __biz + mid, strip session tokens."""
    m_biz = re.search(r'__biz=([^&]+)', url)
    m_mid = re.search(r'mid=(\d+)', url)
    m_idx = re.search(r'idx=(\d+)', url)
    if m_biz and m_mid:
        return f"wx_old_{m_biz.group(1)}_{m_mid.group(1)}_{m_idx.group(1) if m_idx else '1'}"
    return url.split('?')[0]


def collect_old_links(data: dict) -> List[dict]:
    """Collect all __biz= format links with metadata."""
    seen = set()
    links = []
    for h in data.get("holdings", []):
        for hist in h.get("history") or []:
            url = hist.get("articleLink") or ""
            if "__biz=" not in url:
                continue
            key = wx_url_key(url)
            if key in seen:
                continue
            seen.add(key)
            links.append({
                "key":    key,
                "url":    url,
                "date":   hist.get("date", ""),
                "action": hist.get("action", ""),
                "fund":   h.get("fundName", ""),
            })
    return links


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    existing: dict = {}
    if os.path.exists(CONTENT_PATH):
        with open(CONTENT_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    links = collect_old_links(data)
    new_links = [l for l in links if l["key"] not in existing]
    new_links.sort(key=lambda l: l["date"])

    print(f"[wx_old] {len(links)} old-format links, {len(new_links)} to fetch")

    if not new_links:
        print("[wx_old] nothing to do")
        return

    fetched = skipped = 0
    for i, info in enumerate(new_links, 1):
        print(f"\n[{i}/{len(new_links)}] {info['date']} {info['action']} {info['fund'][:25]}")

        data_from_chrome = run_applescript(info["url"], delay=5)

        if not data_from_chrome:
            print("  no response from Chrome")
            skipped += 1
            time.sleep(1)
            continue

        if data_from_chrome.get("isErr"):
            print("  page reports error (deleted/expired)")
            skipped += 1
            time.sleep(0.5)
            continue

        title = data_from_chrome.get("title", "").strip()
        content = data_from_chrome.get("content", "").strip()
        ct = data_from_chrome.get("ct", "")

        if not title and not content:
            print("  empty title and content")
            skipped += 1
            continue

        date_str = info["date"]
        if ct:
            try:
                ts = int(ct)
                date_str = datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d")
            except Exception:
                pass

        existing[info["key"]] = {
            "title":      title,
            "summary":    "",
            "content":    content,
            "createDate": date_str,
            "tags":       [],
            "source":     "weixin_old",
            "url":        info["url"],
        }
        fetched += 1
        print(f"  ✓ {title[:60]}")

        # Save incrementally every 5 articles
        if fetched % 5 == 0:
            with open(CONTENT_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f"  [saved {fetched} so far]")

        time.sleep(1.5)

    with open(CONTENT_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(f"\n[wx_old] done: {fetched} fetched, {skipped} skipped, {len(existing)} total — {now_cn}")


if __name__ == "__main__":
    main()
