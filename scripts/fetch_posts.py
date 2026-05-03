import hashlib
import html
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

TZ_CN = timezone(timedelta(hours=8))

POSTS_PATH = "docs/posts.json"
SPACE_USER_ID = "793413"
LIST_URL = "https://qieman.com/pmdj/v2/community/space/userCenterPost/list"
DETAIL_URL = "https://qieman.com/pmdj/v2/community/post/info"
PAGE_SIZE = 20

# Set to None for full sync; set to a number (e.g. 5) for test runs
MAX_NEW_POSTS = None


def gen_x_sign() -> str:
    ts = int(time.time() * 1000)
    raw = str(int(1.01 * ts))
    h = hashlib.sha256(raw.encode()).hexdigest().upper()[:32]
    return str(ts) + h


def build_headers() -> dict:
    token = os.environ.get("QIEMAN_TOKEN")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "Referer": "https://qieman.com/",
        "Origin": "https://qieman.com",
        "x-broker": "0008",
        "x-sign": gen_x_sign(),
    }
    if token:
        headers["Authorization"] = token
        headers["Cookie"] = f"access_token={token}"
    else:
        print("[posts] QIEMAN_TOKEN not set — fetching as anonymous (restricted posts will have no content)")
    return headers


def fetch_post_list(page_num: int, headers: dict) -> list:
    params = {
        "spaceUserId": SPACE_USER_ID,
        "pageNum": page_num,
        "pageSize": PAGE_SIZE,
        "postType": 1,
    }
    try:
        r = requests.get(LIST_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data") or []
    except Exception as e:
        print(f"[posts] list page {page_num} error: {e}")
        return []


def fetch_post_detail(post_id: int, headers: dict, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        # Refresh x-sign on each attempt (timestamp-based)
        h = dict(headers)
        h["x-sign"] = gen_x_sign()
        try:
            r = requests.get(
                DETAIL_URL, params={"id": post_id}, headers=h, timeout=15
            )
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[posts] {post_id}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            item = data.get("data") or data
            return item
        except Exception as e:
            print(f"[posts] {post_id}: detail error ({e})")
            if attempt < retries - 1:
                time.sleep(1)
    return None


def contents_to_html(raw_content: dict) -> str:
    """Convert list API content.contents blocks to HTML paragraphs (fallback when richContent is null)."""
    parts = []
    for block in (raw_content.get("contents") or []):
        ct = block.get("contentType")
        detail_text = block.get("detail") or ""
        if ct == 1:
            for line in detail_text.split("\n"):
                parts.append("<p>" + html.escape(line) + "</p>")
        elif ct == 2 and detail_text.startswith("http"):
            parts.append('<img src="' + html.escape(detail_text) + '" />')
    return "\n".join(parts)


def normalize_post(raw: dict, detail: dict) -> dict:
    post_id = raw.get("id")
    # Newer posts put poCode/ramblingTags/mood inside an "extra" sub-object
    extra = detail.get("extra") or {}
    po_code = detail.get("poCode") or extra.get("poCode") or []
    rambling_tags = detail.get("ramblingTags") or extra.get("ramblingTags") or []
    mood = detail.get("mood") or extra.get("textMood") or ""
    audio = detail.get("audioInfo") or {}
    raw_content = raw.get("content") or {}
    rich = detail.get("richContent") or ""
    if not rich:
        rich = contents_to_html(raw_content)
    # Title: prefer detail API title, fall back to first 50 chars of intro
    intro = detail.get("summary") or raw_content.get("intro") or ""
    title = detail.get("title") or raw_content.get("title") or ""
    if not title and intro:
        title = intro[:50].rstrip("，。！？,.!? ") + ("…" if len(intro) > 50 else "")
    return {
        "id": post_id,
        "url": f"https://qieman.com/content/content-detail?postId={post_id}",
        "source": "qieman",
        "title": title,
        "summary": intro,
        "richContent": rich,
        "createdAt": raw.get("createdAt") or detail.get("createdAt") or "",
        "modifiedAt": detail.get("modified") or "",
        "images": detail.get("images") or [],
        "likeNum": raw.get("likeNum") or 0,
        "commentNum": raw.get("commentNum") or 0,
        "collectionCount": detail.get("collectionCount") or 0,
        "poCode": po_code,
        "tags": rambling_tags,
        "mood": mood,
        "isSticky": detail.get("isSticky") or False,
        "isAwesome": detail.get("isAwesome") or False,
        "hasAudio": bool(audio.get("audioUrl")),
        "audioUrl": audio.get("audioUrl") or "",
        "audioDuration": audio.get("audioDuration") or 0,
    }


def collect_all_post_ids(headers: dict) -> list:
    all_posts = []
    page = 1
    while True:
        items = fetch_post_list(page, headers)
        if not items:
            break
        all_posts.extend(items)
        print(f"[posts] list page {page}: {len(items)} items")
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)
    return all_posts


def main():
    headers = build_headers()

    existing: dict = {}
    if os.path.exists(POSTS_PATH):
        with open(POSTS_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    print("[posts] fetching post list...")
    all_raw = collect_all_post_ids(headers)
    print(f"[posts] total posts on server: {len(all_raw)}")

    existing_ids = set(str(p) for p in existing)
    new_raw = [p for p in all_raw if str(p.get("id")) not in existing_ids]
    # Fetch oldest first (list is newest-first, so reverse)
    new_raw = list(reversed(new_raw))

    if MAX_NEW_POSTS is not None:
        new_raw = new_raw[:MAX_NEW_POSTS]

    print(f"[posts] {len(existing)} cached, {len(new_raw)} to fetch")

    fetched = 0
    for raw in new_raw:
        post_id = raw.get("id")
        detail = fetch_post_detail(post_id, headers)
        if detail:
            post = normalize_post(raw, detail)
            existing[str(post_id)] = post
            fetched += 1
            print(f"[posts] {post_id}: {post['title']!r}")
        else:
            print(f"[posts] {post_id}: skipped (detail fetch failed)")
        time.sleep(0.5)

    os.makedirs("docs", exist_ok=True)
    with open(POSTS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(f"[posts] done: {fetched} new, {len(existing)} total — {now_cn}")


if __name__ == "__main__":
    main()
