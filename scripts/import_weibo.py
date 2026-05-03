"""One-time import: parse local weibo JSON → docs/posts/{id}.json + docs/posts-index.json + posts/weibo/*.md"""
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from markdownify import markdownify

WEIBO_JSON = sys.argv[1] if len(sys.argv) > 1 else (
    "/Users/bytedance/Downloads/"
    "二级市场捡辣鸡冠军-从2020-11到2024-10-v1.0.0-稳部落数据导出记录.json"
)
POSTS_DIR   = "docs/posts"
INDEX_PATH  = "docs/posts-index.json"
MD_DIR      = "posts/weibo"
SOURCE      = "weibo"

TZ_CN = timezone(timedelta(hours=8))


def clean(s: str) -> str:
    return s.encode("utf-8", errors="ignore").decode("utf-8") if isinstance(s, str) else ""


def wb_html(text: str) -> str:
    """Convert weibo text (HTML with <br />) to clean HTML paragraphs."""
    if not text:
        return ""
    # Replace <br /> sequences with paragraph breaks
    text = re.sub(r"(<br\s*/>)+", "\n", text)
    # Strip inline <a> tags but keep text
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL)
    # Strip other inline tags
    text = re.sub(r'<[^>]+>', '', text)
    text = clean(text).strip()
    lines = text.split("\n")
    return "\n".join("<p>" + html_lib.escape(l) + "</p>" for l in lines)


def strip_html(text: str) -> str:
    """Plain text from weibo HTML."""
    text = re.sub(r"<br\s*/>", " ", text or "")
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return clean(text).strip()


def parse_date(record: dict) -> str:
    ts = record.get("created_timestamp_at")
    if ts:
        return datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d")
    raw = record.get("created_at", "")
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S +0800 %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def weibo_url(record: dict) -> str:
    uid = record.get("user", {}).get("id", "")
    bid = record.get("bid") or record.get("id", "")
    return f"https://weibo.com/{uid}/{bid}"


def process_record(record: dict, author: str) -> tuple[dict, dict]:
    """Returns (index_entry, content_entry)."""
    post_id   = "wb_" + str(record["id"])
    is_retweet = bool(record.get("retweeted_status"))
    date      = parse_date(record)
    raw_text  = clean(record.get("text") or "")
    summary   = strip_html(raw_text)[:80].rstrip()

    # Title: first sentence or first 40 chars
    title = re.split(r"[。！？\n]", strip_html(raw_text))[0].strip()
    if not title:
        title = strip_html(raw_text)[:40]
    if len(strip_html(raw_text)) > 40 and title == strip_html(raw_text)[:40]:
        title += "…"

    # Rich content
    rich = wb_html(raw_text)

    # Retweet
    rt = record.get("retweeted_status") or {}
    retweet_content = wb_html(clean(rt.get("text") or ""))
    retweet_author  = clean((rt.get("user") or {}).get("screen_name") or "")

    # Images
    pic_ids = record.get("pic_ids") or []

    index_entry = {
        "id":         post_id,
        "title":      clean(title),
        "date":       date,
        "author":     author,
        "source":     SOURCE,
        "isRetweet":  is_retweet,
        "summary":    clean(summary),
        "likeNum":    record.get("attitudes_count") or 0,
        "commentNum": record.get("comments_count") or 0,
        "hasAudio":   False,
        "poCode":     [],
        "tags":       [],
        "isSticky":   False,
        "isAwesome":  False,
    }

    content_entry = {
        "id":             post_id,
        "url":            weibo_url(record),
        "richContent":    clean(rich),
        "images":         pic_ids,
        "audioUrl":       "",
        "audioDuration":  0,
        "retweetContent": clean(retweet_content),
        "retweetAuthor":  retweet_author,
    }

    return index_entry, content_entry


def to_markdown(idx: dict, content: dict) -> str:
    title  = idx.get("title") or "（无标题）"
    fm  = "---\n"
    fm += f"id: {idx['id']}\n"
    fm += f'title: "{title.replace(chr(34), chr(39))}"\n'
    fm += f"date: {idx['date']}\n"
    fm += f"author: {idx['author']}\n"
    fm += f"source: {idx['source']}\n"
    fm += f"isRetweet: {str(idx['isRetweet']).lower()}\n"
    fm += f"url: {content['url']}\n"
    fm += f"likes: {idx['likeNum']}\n"
    fm += f"comments: {idx['commentNum']}\n"
    fm += "---\n"

    body = f"# {title}\n\n"
    md = markdownify(content["richContent"], heading_style="ATX", strip=["span"])
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    body += md

    if content.get("retweetContent"):
        body += f"\n\n---\n\n> **转发自 @{content['retweetAuthor']}**\n>\n"
        rt_md = markdownify(content["retweetContent"], heading_style="ATX", strip=["span"])
        rt_md = re.sub(r"\n{3,}", "\n\n", rt_md).strip()
        for line in rt_md.split("\n"):
            body += f"> {line}\n"

    return fm + "\n" + body + "\n"


def main():
    with open(WEIBO_JSON, encoding="utf-8") as f:
        raw = json.load(f)

    uid     = list(raw["export_data"].keys())[0]
    data    = raw["export_data"][uid]
    records = data["record_list"]
    author  = data["info"].get("screen_name") or uid
    print(f"[weibo] author: {author}, loaded {len(records)} records")

    # Load existing index
    existing_index: list = []
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            existing_index = json.load(f)
    existing_ids = {e["id"] for e in existing_index}

    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(MD_DIR, exist_ok=True)

    new_entries = []
    written = 0
    for record in records:
        idx, content = process_record(record, author)
        post_id = idx["id"]

        # Write content file (always overwrite to stay fresh)
        content_path = os.path.join(POSTS_DIR, post_id + ".json")
        with open(content_path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

        if post_id not in existing_ids:
            new_entries.append(idx)
            written += 1

    # Merge and sort index
    all_entries = existing_index + new_entries
    all_entries.sort(key=lambda e: e.get("date") or "", reverse=True)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)

    print(f"[weibo] done: {written} new index entries, {len(all_entries)} total")
    print(f"[weibo] content files → {POSTS_DIR}/")
    print(f"[weibo] markdown files → {MD_DIR}/")


if __name__ == "__main__":
    main()
