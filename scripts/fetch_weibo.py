"""Scrape Weibo posts for a given UID using browser cookies.

Usage:
    WEIBO_COOKIE="SUB=...; SUBP=..." python scripts/fetch_weibo.py
or set cookie in the WEIBO_COOKIE env var, or edit COOKIE below.

Writes directly to docs/posts/{wb_id}.json + docs/posts-index.json,
compatible with the existing two-tier storage used by import_weibo.py.
"""
import html as html_lib
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

# ── Config ──────────────────────────────────────────────────────────────────
UID          = "7519797263"
AUTHOR       = "二级市场捡辣鸡冠军"
START_DATE   = "2024-10-01"   # only import posts on or after this date
POSTS_DIR    = "docs/posts"
INDEX_PATH   = "docs/posts-index.json"
SOURCE       = "weibo"

# Cookie can come from env var WEIBO_COOKIE or be pasted here directly.
COOKIE = os.environ.get("WEIBO_COOKIE", "")

TZ_CN = timezone(timedelta(hours=8))

API_URL = "https://weibo.com/ajax/statuses/mymblog"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"https://weibo.com/u/{UID}",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def clean(s) -> str:
    return s.encode("utf-8", errors="ignore").decode("utf-8") if isinstance(s, str) else ""


def wb_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(<br\s*/>|<br>)+", "\n", text)
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = clean(text).strip()
    lines = text.split("\n")
    return "\n".join("<p>" + html_lib.escape(l) + "</p>" for l in lines)


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/>|<br>", " ", text or "")
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return clean(text).strip()


def parse_weibo_date(created_at: str) -> str:
    """Parse Weibo's 'Sun May 03 12:34:56 +0800 2026' format."""
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0800 %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # Sometimes it comes as a relative string like "1小时前"; fall back to today
    return datetime.now(TZ_CN).strftime("%Y-%m-%d")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if COOKIE:
        s.headers["Cookie"] = COOKIE
    return s


# ── Fetch ────────────────────────────────────────────────────────────────────

def fetch_page(session: requests.Session, page: int = 1, since_id: str = "") -> Optional[dict]:
    params = {"uid": UID, "page": page, "feature": 0}
    if since_id:
        params["since_id"] = since_id
    try:
        r = session.get(API_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[weibo] fetch error: {e}")
        return None


def extract_posts(data: dict) -> tuple[list, str]:
    """Return (list_of_mblog_dicts, next_since_id)."""
    inner = data.get("data") or {}
    mblogs = inner.get("list") or []
    since_id = str(inner.get("since_id") or "")
    return mblogs, since_id


# ── Process ──────────────────────────────────────────────────────────────────

def process_mblog(mblog: dict) -> tuple[dict, dict]:
    post_id  = "wb_" + str(mblog["id"])
    bid      = mblog.get("bid") or mblog.get("id", "")
    date     = parse_weibo_date(mblog.get("created_at", ""))
    raw_text = clean(mblog.get("text") or "")
    plain    = strip_html(raw_text)
    summary  = plain[:80].rstrip()

    title = re.split(r"[。！？\n]", plain)[0].strip()
    if not title:
        title = plain[:40]
    if len(plain) > 40 and title == plain[:40]:
        title += "…"

    rich = wb_html(raw_text)

    rt = mblog.get("retweeted_status") or {}
    retweet_content = wb_html(clean(rt.get("text") or ""))
    retweet_author  = clean((rt.get("user") or {}).get("screen_name") or "")
    is_retweet = bool(rt)

    url = f"https://weibo.com/{UID}/{bid}"

    # Images
    pic_ids = mblog.get("pic_ids") or []

    index_entry = {
        "id":         post_id,
        "title":      clean(title),
        "date":       date,
        "author":     AUTHOR,
        "source":     SOURCE,
        "isRetweet":  is_retweet,
        "summary":    clean(summary),
        "likeNum":    mblog.get("attitudes_count") or 0,
        "commentNum": mblog.get("comments_count") or 0,
        "hasAudio":   False,
        "poCode":     [],
        "tags":       [],
        "isSticky":   False,
        "isAwesome":  False,
    }
    content_entry = {
        "id":             post_id,
        "url":            url,
        "richContent":    clean(rich),
        "images":         pic_ids,
        "audioUrl":       "",
        "audioDuration":  0,
        "retweetContent": clean(retweet_content),
        "retweetAuthor":  retweet_author,
    }
    return index_entry, content_entry


# ── Index helpers ────────────────────────────────────────────────────────────

def load_index() -> tuple[list, set]:
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            entries = json.load(f)
    else:
        entries = []
    existing_ids = {e["id"] for e in entries}
    return entries, existing_ids


def save_index(entries: list):
    entries.sort(key=lambda e: e.get("date") or "", reverse=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not COOKIE:
        print("[weibo] WARNING: WEIBO_COOKIE not set — requests will be unauthenticated")

    os.makedirs(POSTS_DIR, exist_ok=True)
    index_entries, existing_ids = load_index()

    session   = make_session()
    since_id  = ""
    page_num  = 0
    new_count = 0
    stop = False

    while not stop:
        page_num += 1
        print(f"[weibo] fetching page {page_num} (since_id={since_id!r})")
        data = fetch_page(session, page_num, since_id)
        if not data:
            print("[weibo] empty response, stopping")
            break

        mblogs, next_since_id = extract_posts(data)
        if not mblogs:
            print("[weibo] no cards found, stopping")
            break

        print(f"[weibo] page {page_num}: {len(mblogs)} posts")

        for mblog in mblogs:
            date = parse_weibo_date(mblog.get("created_at", ""))
            if date < START_DATE:
                print(f"[weibo] reached date {date} < {START_DATE}, stopping")
                stop = True
                break

            idx, content = process_mblog(mblog)
            post_id = idx["id"]

            # Write content file (always refresh)
            content_path = os.path.join(POSTS_DIR, post_id + ".json")
            with open(content_path, "w", encoding="utf-8") as f:
                json.dump(content, f, ensure_ascii=False, indent=2)

            if post_id not in existing_ids:
                index_entries.append(idx)
                existing_ids.add(post_id)
                new_count += 1
                print(f"[weibo] +{post_id} {date} {idx['title']!r}")

        if stop or not next_since_id or next_since_id == since_id:
            break
        since_id = next_since_id
        time.sleep(1)

    save_index(index_entries)
    print(f"\n[weibo] done: {new_count} new posts, {len(index_entries)} total in index")


if __name__ == "__main__":
    main()
