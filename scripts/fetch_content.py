import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

import requests

TZ_CN = timezone(timedelta(hours=8))

CONTENT_DIR = "docs"
CONTENT_PATH = os.path.join(CONTENT_DIR, "content.json")
DATA_PATH = "docs/data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    )
}


def extract_item_id(url: str) -> Optional[str]:
    """Extract itemId from content.qieman.com article URLs.

    Handles:
      https://content.qieman.com/n/items/23089
      https://content.qieman.com/items/1850
      https://content.qieman.com/n/items/18625?preview=1
    Returns None for WeChat or other non-qieman links.
    """
    if not url or "content.qieman.com" not in url:
        return None
    m = re.search(r"/items/(\d+)", url)
    return m.group(1) if m else None


def fetch_article(item_id: str, retries: int = 3) -> Optional[dict]:
    """Fetch a single article via __NEXT_DATA__ SSR payload.

    Returns dict with title, summary, content, createDate, tags, or None on failure.
    """
    url = f"https://content.qieman.com/n/items/{item_id}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[content] {item_id}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                # Try old URL format without /n/
                url_old = f"https://content.qieman.com/items/{item_id}"
                r = requests.get(url_old, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[content] {item_id}: fetch error ({e})")
            if attempt < retries - 1:
                time.sleep(1)
            continue

        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            r.text,
            re.DOTALL,
        )
        if not m:
            print(f"[content] {item_id}: no __NEXT_DATA__ in response")
            return None

        try:
            data = json.loads(m.group(1))
            page_props = data["props"]["pageProps"]
            item = page_props.get("item") or {}
            article = item.get("article") or {}

            title = article.get("title") or ""
            summary = article.get("summary") or ""
            content = article.get("content") or ""
            create_date = article.get("createDate")
            tags = article.get("tags") or []

            if not title:
                print(f"[content] {item_id}: missing title in __NEXT_DATA__")
                return None

            return {
                "title": title,
                "summary": summary,
                "content": content,
                "createDate": create_date,
                "tags": tags,
            }
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            print(f"[content] {item_id}: parse error ({e})")
            return None

    return None


def collect_item_ids(data: dict) -> Set[str]:
    """Collect all content.qieman.com itemIds from data.json."""
    ids: Set[str] = set()
    for signal in data.get("recentSignals", []):
        iid = extract_item_id(signal.get("articleLink") or "")
        if iid:
            ids.add(iid)
    for holding in data.get("holdings", []):
        for hist in holding.get("history", []):
            iid = extract_item_id(hist.get("articleLink") or "")
            if iid:
                ids.add(iid)
    return ids


def main():
    if not os.path.exists(DATA_PATH):
        print(f"[content] {DATA_PATH} not found, run fetch_signals.py first")
        return

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # Load existing cache (incremental updates)
    existing: dict = {}
    if os.path.exists(CONTENT_PATH):
        with open(CONTENT_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    item_ids = collect_item_ids(data)
    new_ids = sorted(item_ids - set(existing.keys()), key=int)

    print(
        f"[content] {len(item_ids)} total itemIds, "
        f"{len(existing)} cached, "
        f"{len(new_ids)} to fetch"
    )

    fetched = 0
    for iid in new_ids:
        article = fetch_article(iid)
        if article:
            existing[iid] = article
            fetched += 1
            print(f"[content] {iid}: {article['title']!r}")
        else:
            print(f"[content] {iid}: skipped (fetch/parse failed)")
        time.sleep(0.5)

    os.makedirs(CONTENT_DIR, exist_ok=True)
    with open(CONTENT_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(
        f"[content] done: {fetched} new, {len(existing)} total — {now_cn}"
    )


if __name__ == "__main__":
    main()
