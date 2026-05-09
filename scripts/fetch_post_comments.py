"""Fetch qieman post comments and extract E大 (ETF拯救世界) contributions.

Two kinds of "elder items" are captured per post:
  - top:    a top-level comment authored by E大 (brokerUserId == ELDER_USER_ID)
  - reply:  a reply by E大 nested under another user's top-level comment;
            the parent top-level comment (and the specific sub-reply being
            answered, if any) is preserved as context.

Output:
  docs/posts/{post_id}-elder.json   per-post elder items
  docs/posts/elder-index.json        light index { post_id -> {elderCount,...} }

Usage:
  python scripts/fetch_post_comments.py --post-id 73459        # single post (smoke)
  python scripts/fetch_post_comments.py --limit 5              # first 5 posts
  python scripts/fetch_post_comments.py                        # all qieman posts (incremental)
  python scripts/fetch_post_comments.py --force                # ignore commentNum cache
"""
import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests

TZ_CN = timezone(timedelta(hours=8))

POSTS_DIR        = "docs/posts"
POSTS_INDEX_PATH = "docs/posts-index.json"
ELDER_INDEX_PATH = "docs/posts/elder-index.json"
COMMENT_LIST_URL = "https://qieman.com/pmdj/v2/community/comment/list"
REPLY_LIST_URL   = "https://qieman.com/pmdj/v2/community/reply/list"

PAGE_SIZE       = 20
TARGET_USERS    = {           # brokerUserId -> display name
    "793413":  "ETF拯救世界",
    "3194882": "新米练习菌",
}
SLEEP_BETWEEN   = 0.4
MAX_PAGES_GUARD = 1000        # safety net
RECENT_DAYS     = 7           # always re-fetch posts within this window
                              # (posts-index.json's commentNum is frozen at first
                              # crawl, so stale-count incremental check would
                              # otherwise skip old posts forever)


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
        "Origin":  "https://qieman.com",
        "x-broker": "0008",
        "x-sign":   gen_x_sign(),
    }
    if token:
        headers["Authorization"] = token
        headers["Cookie"] = f"access_token={token}"
    else:
        print("[comments] QIEMAN_TOKEN not set — anonymous mode")
    return headers


def fetch_comments_page(post_id: str, page_num: int, headers: dict, retries: int = 3) -> Optional[list]:
    params = {
        "pageNum": page_num,
        "pageSize": PAGE_SIZE,
        "postId": post_id,
        "sortType": "DEFAULT",
    }
    for attempt in range(retries):
        h = dict(headers)
        h["x-sign"] = gen_x_sign()
        try:
            r = requests.get(COMMENT_LIST_URL, params=params, headers=h, timeout=20)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[comments] {post_id} p{page_num}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else (data.get("data") or [])
        except Exception as e:
            print(f"[comments] {post_id} p{page_num}: error {e}")
            if attempt < retries - 1:
                time.sleep(1 + attempt)
    return None


def fetch_all_comments(post_id: str, headers: dict) -> Optional[list]:
    all_comments: list = []
    page = 1
    while page <= MAX_PAGES_GUARD:
        items = fetch_comments_page(post_id, page, headers)
        if items is None:
            return None
        if not items:
            break
        all_comments.extend(items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN)
    return all_comments


def fetch_replies_page(comment_id: int, page_num: int, headers: dict, retries: int = 3) -> Optional[list]:
    params = {
        "commentId": comment_id,
        "pageNum": page_num,
        "pageSize": PAGE_SIZE,
        "sortType": "DEFAULT",
    }
    for attempt in range(retries):
        h = dict(headers)
        h["x-sign"] = gen_x_sign()
        try:
            r = requests.get(REPLY_LIST_URL, params=params, headers=h, timeout=20)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[comments] reply {comment_id} p{page_num}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else (data.get("data") or [])
        except Exception as e:
            print(f"[comments] reply {comment_id} p{page_num}: error {e}")
            if attempt < retries - 1:
                time.sleep(1 + attempt)
    return None


def fetch_all_replies(comment_id: int, headers: dict) -> Optional[list]:
    all_replies: list = []
    page = 1
    while page <= MAX_PAGES_GUARD:
        items = fetch_replies_page(comment_id, page, headers)
        if items is None:
            return None
        if not items:
            break
        all_replies.extend(items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN)
    return all_replies


def normalize_top(c: dict, author: str) -> dict:
    return {
        "type":          "top",
        "id":            c["id"],
        "author":        author,
        "authorUserId":  str(c.get("brokerUserId") or ""),
        "content":       c.get("content") or "",
        "images":        c.get("images") or [],
        "createdAt":     c.get("createdAt") or "",
        "ipLocation":    c.get("ipLocation") or "",
        "likeNum":       c.get("likeNum") or 0,
    }


def normalize_reply(reply: dict, parent: dict, siblings: list, author: str) -> dict:
    parent_summary = {
        "id":         parent["id"],
        "userName":   parent.get("userName") or "",
        "content":    parent.get("content") or "",
        "images":     parent.get("images") or [],
        "createdAt":  parent.get("createdAt") or "",
        "ipLocation": parent.get("ipLocation") or "",
        "likeNum":    parent.get("likeNum") or 0,
    }
    reply_to = None
    to_reply_id = reply.get("toReplyId")
    if to_reply_id:
        for sib in siblings:
            if sib.get("id") == to_reply_id:
                reply_to = {
                    "id":       sib["id"],
                    "userName": sib.get("userName") or "",
                    "content":  sib.get("content") or "",
                    "images":   sib.get("images") or [],
                }
                break
    return {
        "type":          "reply",
        "id":            reply["id"],
        "author":        author,
        "authorUserId":  str(reply.get("brokerUserId") or ""),
        "content":       reply.get("content") or "",
        "images":        reply.get("images") or [],
        "createdAt":     reply.get("createdAt") or "",
        "ipLocation":    reply.get("ipLocation") or "",
        "likeNum":       reply.get("likeNum") or 0,
        "parent":        parent_summary,
        "replyTo":       reply_to,
    }


def extract_elder_items(comments: list) -> tuple[list, int]:
    items: list = []
    truncated = 0
    for c in comments:
        broker = str(c.get("brokerUserId") or "")
        children = c.get("children") or []
        if broker in TARGET_USERS:
            items.append(normalize_top(c, TARGET_USERS[broker]))
        for child in children:
            cb = str(child.get("brokerUserId") or "")
            if cb in TARGET_USERS:
                items.append(normalize_reply(child, c, children, TARGET_USERS[cb]))
        actual_total = c.get("commentNum") or 0
        if actual_total > len(children):
            truncated += 1
    items.sort(key=lambda x: x.get("createdAt") or "")
    return items, truncated


def process_post(post_id: str, total_comment_num: int, headers: dict) -> Optional[dict]:
    comments = fetch_all_comments(post_id, headers)
    if comments is None:
        return None

    expanded = 0
    expansion_failed = 0
    for c in comments:
        actual = c.get("commentNum") or 0
        children = c.get("children") or []
        if actual > len(children):
            full = fetch_all_replies(c["id"], headers)
            if full is None:
                expansion_failed += 1
                continue
            c["children"] = full
            expanded += 1
            time.sleep(SLEEP_BETWEEN)

    items, truncated = extract_elder_items(comments)
    return {
        "postId":             post_id,
        "fetchedAt":          datetime.now(TZ_CN).isoformat(timespec="seconds"),
        "totalCommentNum":    total_comment_num,
        "totalScanned":       len(comments),
        "expandedTopLevel":   expanded,
        "expansionFailed":    expansion_failed,
        "truncatedTopLevel":  truncated,
        "items":              items,
    }


def load_post_index() -> list:
    if os.path.exists(POSTS_INDEX_PATH):
        with open(POSTS_INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_elder_index() -> dict:
    if os.path.exists(ELDER_INDEX_PATH):
        with open(ELDER_INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_elder_index(idx: dict):
    with open(ELDER_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def write_elder(post_id: str, data: dict):
    path = os.path.join(POSTS_DIR, f"{post_id}-elder.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-id", help="Process a single post (smoke test)")
    ap.add_argument("--limit", type=int, help="Max posts to process")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if commentNum unchanged")
    args = ap.parse_args()

    headers = build_headers()
    elder_index = load_elder_index()
    posts = load_post_index()
    qm_posts = [p for p in posts if not p.get("id", "").startswith("wb_")]

    if args.post_id:
        post = next((p for p in qm_posts if p["id"] == args.post_id), None)
        if not post:
            print(f"[comments] post {args.post_id} not in index — proceeding with commentNum=0")
            post = {"id": args.post_id, "commentNum": 0}
        targets = [post]
    else:
        targets = qm_posts

    if args.limit:
        targets = targets[: args.limit]

    print(f"[comments] processing {len(targets)} posts (force={args.force})")

    today = date.today()
    skipped = processed = failed = 0
    for i, post in enumerate(targets, 1):
        pid = post["id"]
        cnum = post.get("commentNum") or 0
        existing = elder_index.get(pid)
        is_recent = False
        post_date_str = post.get("date") or ""
        if post_date_str:
            try:
                is_recent = (today - date.fromisoformat(post_date_str)).days <= RECENT_DAYS
            except ValueError:
                pass
        if (not args.force and not is_recent
                and existing and existing.get("totalCommentNum") == cnum):
            skipped += 1
            continue

        tag = "recent" if is_recent else "stale"
        print(f"[comments] [{i}/{len(targets)}] {pid} (commentNum={cnum}, {tag}) ...")
        data = process_post(pid, cnum, headers)
        if data is None:
            print(f"[comments] {pid}: FAILED")
            failed += 1
            continue
        write_elder(pid, data)
        by_author: dict = {}
        for it in data["items"]:
            a = it.get("author") or "?"
            by_author[a] = by_author.get(a, 0) + 1
        elder_index[pid] = {
            "elderCount":        len(data["items"]),
            "byAuthor":          by_author,
            "totalCommentNum":   cnum,
            "totalScanned":      data["totalScanned"],
            "expandedTopLevel":  data["expandedTopLevel"],
            "truncatedTopLevel": data["truncatedTopLevel"],
            "fetchedAt":         data["fetchedAt"],
        }
        save_elder_index(elder_index)
        processed += 1
        msg = (f"[comments] {pid}: {len(data['items'])} elder / "
               f"{data['totalScanned']} scanned, "
               f"{data['expandedTopLevel']} expanded")
        if data["truncatedTopLevel"]:
            msg += f"  WARN: {data['truncatedTopLevel']} still truncated"
        if data["expansionFailed"]:
            msg += f"  WARN: {data['expansionFailed']} expansion failures"
        print(msg)

    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST")
    print(f"\n[comments] done: processed={processed} skipped={skipped} "
          f"failed={failed} — {now_cn}")


if __name__ == "__main__":
    main()
