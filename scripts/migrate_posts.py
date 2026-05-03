"""One-time migration: split docs/posts.json → docs/posts/{id}.json + docs/posts-index.json"""
import json, os

OLD_PATH  = "docs/posts.json"
POSTS_DIR = "docs/posts"
INDEX_PATH = "docs/posts-index.json"

INDEX_FIELDS = [
    "id", "title", "date", "author", "source", "isRetweet",
    "summary", "likeNum", "commentNum", "hasAudio",
    "poCode", "tags", "isSticky", "isAwesome",
]
CONTENT_FIELDS = [
    "id", "url", "richContent", "images",
    "audioUrl", "audioDuration", "retweetContent", "retweetAuthor",
]

def main():
    with open(OLD_PATH, encoding="utf-8") as f:
        posts = json.load(f)

    os.makedirs(POSTS_DIR, exist_ok=True)
    index_entries = []

    for pid, post in posts.items():
        post_id = str(post.get("id") or pid)
        date = (post.get("createdAt") or "")[:10]

        idx = {
            "id":        post_id,
            "title":     post.get("title") or "",
            "date":      date,
            "author":    post.get("author") or "ETF拯救世界",
            "source":    post.get("source") or "qieman",
            "isRetweet": False,
            "summary":   post.get("summary") or "",
            "likeNum":   post.get("likeNum") or 0,
            "commentNum":post.get("commentNum") or 0,
            "hasAudio":  post.get("hasAudio") or False,
            "poCode":    post.get("poCode") or [],
            "tags":      post.get("tags") or [],
            "isSticky":  post.get("isSticky") or False,
            "isAwesome": post.get("isAwesome") or False,
        }

        content = {
            "id":             post_id,
            "url":            post.get("url") or "",
            "richContent":    post.get("richContent") or "",
            "images":         post.get("images") or [],
            "audioUrl":       post.get("audioUrl") or "",
            "audioDuration":  post.get("audioDuration") or 0,
            "retweetContent": "",
            "retweetAuthor":  "",
        }

        with open(os.path.join(POSTS_DIR, post_id + ".json"), "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

        index_entries.append(idx)

    index_entries.sort(key=lambda e: e.get("date") or "", reverse=True)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_entries, f, ensure_ascii=False, indent=2)

    print(f"[migrate] {len(index_entries)} posts → {POSTS_DIR}/ + {INDEX_PATH}")

if __name__ == "__main__":
    main()
