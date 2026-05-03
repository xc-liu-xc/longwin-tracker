"""Convert docs/posts.json to individual markdown files under posts/."""
import json
import os
import re

from markdownify import markdownify

POSTS_JSON = "docs/posts.json"
OUT_DIR = "posts"


def slugify(text: str) -> str:
    text = text.strip().replace(" ", "-")
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text)
    return text[:40]


def to_markdown(post: dict) -> str:
    date = (post.get("createdAt") or "")[:10]
    title = post.get("title") or "（无标题）"
    pocode = post.get("poCode") or []
    tags = post.get("tags") or []

    frontmatter = "---\n"
    frontmatter += f"id: {post['id']}\n"
    frontmatter += f"title: \"{title.replace(chr(34), chr(39))}\"\n"
    frontmatter += f"date: {date}\n"
    frontmatter += f"url: {post.get('url', '')}\n"
    frontmatter += f"source: {post.get('source', 'qieman')}\n"
    if pocode:
        frontmatter += f"poCode: [{', '.join(pocode)}]\n"
    if tags:
        frontmatter += f"tags: [{', '.join(str(t) for t in tags)}]\n"
    if post.get("isSticky"):
        frontmatter += "sticky: true\n"
    if post.get("isAwesome"):
        frontmatter += "awesome: true\n"
    if post.get("hasAudio"):
        frontmatter += f"audioUrl: {post.get('audioUrl', '')}\n"
    frontmatter += f"likes: {post.get('likeNum', 0)}\n"
    frontmatter += f"comments: {post.get('commentNum', 0)}\n"
    frontmatter += "---\n"

    body = f"# {title}\n\n"

    rich = post.get("richContent") or ""
    if rich:
        md = markdownify(rich, heading_style="ATX", strip=["span"])
        # Collapse 3+ blank lines to 2
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        body += md
    elif post.get("summary"):
        body += post["summary"]

    return frontmatter + "\n" + body + "\n"


def main():
    with open(POSTS_JSON, encoding="utf-8") as f:
        posts = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)

    posts_sorted = sorted(posts.values(), key=lambda p: p.get("createdAt") or "")

    written = 0
    for post in posts_sorted:
        date = (post.get("createdAt") or "0000-00-00")[:10]
        pid = post["id"]
        title_slug = slugify(post.get("title") or "")
        filename = f"{date}-{pid}.md"
        if title_slug:
            filename = f"{date}-{pid}-{title_slug}.md"

        path = os.path.join(OUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_markdown(post))
        written += 1

    print(f"导出完成：{written} 篇 → {OUT_DIR}/")


if __name__ == "__main__":
    main()
