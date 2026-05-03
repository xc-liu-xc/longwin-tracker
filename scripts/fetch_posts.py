import hashlib
import html
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

TZ_CN = timezone(timedelta(hours=8))

POSTS_DIR  = "docs/posts"
INDEX_PATH = "docs/posts-index.json"
LIST_URL   = "https://qieman.com/pmdj/v2/community/space/userCenterPost/list"
DETAIL_URL = "https://qieman.com/pmdj/v2/community/post/info"
PAGE_SIZE  = 20

USERS = {
    "793413":  "ETF拯救世界",
    "3194882": "新米练习菌",
}

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
        print("[posts] QIEMAN_TOKEN not set — anonymous mode")
    return headers


def fetch_post_list(space_user_id: str, page_num: int, headers: dict) -> list:
    params = {"spaceUserId": space_user_id, "pageNum": page_num,
              "pageSize": PAGE_SIZE, "postType": 1}
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
        h = dict(headers)
        h["x-sign"] = gen_x_sign()
        try:
            r = requests.get(DETAIL_URL, params={"id": post_id}, headers=h, timeout=15)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[posts] {post_id}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            return data.get("data") or data
        except Exception as e:
            print(f"[posts] {post_id}: detail error ({e})")
            if attempt < retries - 1:
                time.sleep(1)
    return None


def clean_str(s: str) -> str:
    return s.encode("utf-8", errors="ignore").decode("utf-8") if isinstance(s, str) else ""


def contents_to_html(raw_content: dict) -> str:
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


def normalize_post(raw: dict, detail: dict, author: str) -> tuple[dict, dict]:
    """Returns (index_entry, content_entry)."""
    post_id    = str(raw.get("id"))
    extra      = detail.get("extra") or {}
    po_code    = detail.get("poCode") or extra.get("poCode") or []
    rambling_tags = detail.get("ramblingTags") or extra.get("ramblingTags") or []
    mood       = detail.get("mood") or extra.get("textMood") or ""
    audio      = detail.get("audioInfo") or {}
    raw_content = raw.get("content") or {}
    rich       = detail.get("richContent") or ""
    if not rich:
        rich = contents_to_html(raw_content)
    intro  = detail.get("summary") or raw_content.get("intro") or ""
    title  = detail.get("title") or raw_content.get("title") or ""
    if not title and intro:
        title = intro[:50].rstrip("，。！？,.!? ") + ("…" if len(intro) > 50 else "")
    date = (raw.get("createdAt") or detail.get("createdAt") or "")[:10]

    index_entry = {
        "id":        post_id,
        "title":     clean_str(title),
        "date":      date,
        "author":    author,
        "source":    "qieman",
        "isRetweet": False,
        "summary":   clean_str(intro),
        "likeNum":   raw.get("likeNum") or 0,
        "commentNum":raw.get("commentNum") or 0,
        "hasAudio":  bool(audio.get("audioUrl")),
        "poCode":    po_code,
        "tags":      rambling_tags,
        "isSticky":  detail.get("isSticky") or False,
        "isAwesome": detail.get("isAwesome") or False,
    }

    content_entry = {
        "id":            post_id,
        "url":           f"https://qieman.com/content/content-detail?postId={post_id}",
        "richContent":   clean_str(rich),
        "images":        detail.get("images") or [],
        "audioUrl":      audio.get("audioUrl") or "",
        "audioDuration": audio.get("audioDuration") or 0,
        "retweetContent":"",
        "retweetAuthor": "",
    }

    return index_entry, content_entry


def load_index() -> tuple[list, set]:
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            entries = json.load(f)
    else:
        entries = []
    # Only consider qieman IDs (numeric, no wb_ prefix)
    existing_ids = {e["id"] for e in entries if not e["id"].startswith("wb_")}
    return entries, existing_ids


def save_index(entries: list):
    entries.sort(key=lambda e: e.get("date") or "", reverse=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def collect_user_posts(space_user_id: str, author: str, headers: dict) -> list:
    all_posts = []
    page = 1
    while True:
        items = fetch_post_list(space_user_id, page, headers)
        if not items:
            break
        all_posts.extend(items)
        print(f"[posts] {author} page {page}: {len(items)} items")
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)
    return all_posts


def main():
    headers = build_headers()
    os.makedirs(POSTS_DIR, exist_ok=True)

    index_entries, existing_ids = load_index()

    # Backfill author for legacy entries missing it
    for e in index_entries:
        if not e.get("author") and not e["id"].startswith("wb_"):
            e["author"] = "ETF拯救世界"

    all_new: list = []  # (raw, author)

    for space_user_id, author in USERS.items():
        print(f"\n[posts] === {author} (spaceUserId={space_user_id}) ===")
        raw_posts = collect_user_posts(space_user_id, author, headers)
        print(f"[posts] {author}: {len(raw_posts)} total on server")
        new = [(r, author) for r in raw_posts if str(r.get("id")) not in existing_ids]
        new = list(reversed(new))
        print(f"[posts] {author}: {len(new)} new to fetch")
        all_new.extend(new)

    if MAX_NEW_POSTS is not None:
        all_new = all_new[:MAX_NEW_POSTS]

    print(f"\n[posts] total new posts to fetch: {len(all_new)}")

    for raw, author in all_new:
        post_id = str(raw.get("id"))
        detail = fetch_post_detail(int(post_id), headers)
        if detail:
            idx, content = normalize_post(raw, detail, author)
            # Write content file
            with open(os.path.join(POSTS_DIR, post_id + ".json"), "w", encoding="utf-8") as f:
                json.dump(content, f, ensure_ascii=False, indent=2)
            index_entries.append(idx)
            print(f"[posts] {author} {post_id}: {idx['title']!r}")
        else:
            print(f"[posts] {post_id}: skipped")
        time.sleep(0.5)

    save_index(index_entries)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(f"\n[posts] done: {len(all_new)} new, {len(index_entries)} total — {now_cn}")


if __name__ == "__main__":
    main()
