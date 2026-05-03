"""Export all posts to markdown: reads posts-index.json + docs/posts/{id}.json"""
import json
import os
import re

from markdownify import markdownify

INDEX_PATH = "docs/posts-index.json"
POSTS_DIR  = "docs/posts"
MD_QIEMAN  = "posts"
MD_WEIBO   = "posts/weibo"


def slugify(text: str) -> str:
    text = text.strip().replace(" ", "-")
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text)
    return text[:40]


def html_to_md(html: str) -> str:
    if not html:
        return ""
    md = markdownify(html, heading_style="ATX", strip=["span"])
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def to_markdown(idx: dict, content: dict) -> str:
    title = idx.get("title") or "（无标题）"
    fm  = "---\n"
    fm += f"id: {idx['id']}\n"
    fm += f'title: "{title.replace(chr(34), chr(39))}"\n'
    fm += f"date: {idx.get('date', '')}\n"
    fm += f"author: {idx.get('author', '')}\n"
    fm += f"source: {idx.get('source', '')}\n"
    if idx.get("isRetweet"):
        fm += "isRetweet: true\n"
    fm += f"url: {content.get('url', '')}\n"
    if idx.get("poCode"):
        fm += f"poCode: [{', '.join(idx['poCode'])}]\n"
    if idx.get("tags"):
        fm += f"tags: [{', '.join(str(t) for t in idx['tags'])}]\n"
    if idx.get("isSticky"):
        fm += "sticky: true\n"
    if idx.get("isAwesome"):
        fm += "awesome: true\n"
    if idx.get("hasAudio") and content.get("audioUrl"):
        fm += f"audioUrl: {content['audioUrl']}\n"
    fm += f"likes: {idx.get('likeNum', 0)}\n"
    fm += f"comments: {idx.get('commentNum', 0)}\n"
    fm += "---\n"

    body = f"# {title}\n\n"
    body += html_to_md(content.get("richContent") or "")

    if content.get("retweetContent"):
        body += f"\n\n---\n\n> **转发自 @{content.get('retweetAuthor', '')}**\n>\n"
        for line in html_to_md(content["retweetContent"]).split("\n"):
            body += f"> {line}\n"

    return fm + "\n" + body + "\n"


def main():
    with open(INDEX_PATH, encoding="utf-8") as f:
        index = json.load(f)

    os.makedirs(MD_QIEMAN, exist_ok=True)
    os.makedirs(MD_WEIBO, exist_ok=True)

    written = 0
    for idx in index:
        post_id = idx["id"]
        content_path = os.path.join(POSTS_DIR, post_id + ".json")
        if not os.path.exists(content_path):
            print(f"[export] missing content file: {content_path}, skip")
            continue

        with open(content_path, encoding="utf-8") as f:
            content = json.load(f)

        is_weibo = post_id.startswith("wb_")
        date = idx.get("date") or "0000-00-00"
        title_slug = slugify(idx.get("title") or "")
        filename = f"{date}-{post_id}.md"
        if title_slug:
            filename = f"{date}-{post_id}-{title_slug}.md"

        out_dir = MD_WEIBO if is_weibo else MD_QIEMAN
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_markdown(idx, content))
        written += 1

    print(f"[export] done: {written} markdown files")


if __name__ == "__main__":
    main()
