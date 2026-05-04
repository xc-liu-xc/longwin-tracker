# Plan: 接入且慢E大内容数据

<!-- /autoplan restore point: /Users/bytedance/.gstack/projects/xc-liu-xc-longwin-tracker/main-autoplan-restore-20260503-153445.md -->

## 背景

当前项目已完成：
- 通过逆向工程且慢 GraphQL API 抓取长赢计划买卖信号（`scripts/fetch_signals.py`）
- 通过 Tushare API 抓取持仓基金历史净值（`scripts/fetch_nav.py`）
- GitHub Pages 静态展示页面（`docs/index.html`）
- GitHub Actions 每日自动更新（`.github/workflows/update.yml`）

## 目标（修订后）

接入 E大 在且慢发布的文章内容，与买卖信号关联展示，让用户无需离开页面即可了解每次操作背后的逻辑。

**关键发现（API 探测结果）：**
- `content.qieman.com/n/items/{itemId}` 和 `content.qieman.com/items/{itemId}` 通过 `__NEXT_DATA__` 返回完整文章数据（无需 token，公开可访问）
- `data.json` 已有 237 个 `articleLink`：107 个 content.qieman.com 链接（可抓取），130 个微信公众号链接（无结构化数据）
- 没有找到用户空间内容列表的 JSON API — 原计划的 user-space API 不存在
- 用户确认：历史内容用爬虫，新内容手动维护

## 修订后的实现范围

### 新增文件
- `scripts/fetch_content.py` — 从 data.json 读取所有 content.qieman.com articleLink，抓取文章元数据，写入 `docs/content.json`
- `docs/content.json` — 文章元数据，以 itemId（字符串）为 key

### 修改文件
- `docs/index.html` — 在信号时间线和持仓历史行中展示文章标题
- `.github/workflows/update.yml` — 新增 fetch_content.py 步骤

### 不在范围内
- 用户空间内容列表 API（不存在 JSON 接口）
- 微信公众号文章抓取（无结构化数据）
- 文章正文展示（只展示标题和摘要）
- 新文章自动发现（手动维护）

## content.json 数据结构

```json
{
  "23089": {
    "title": "2026年4月ETF计划（五）",
    "summary": "正常调仓",
    "content": "<p>...完整 HTML 正文...</p>",
    "createDate": 1776873600000,
    "tags": []
  }
}
```

**关键约定：**
- key 为字符串（JSON key 必须是字符串）
- title / summary 为纯文本
- content 为原始 HTML（来自 qieman），存储备用，前端展示方式后续决定
- 前端当前只展示 title，如需渲染 content HTML 必须先用 DOMPurify 清洗

## scripts/fetch_content.py 实现要点

```python
import requests, re, json, os, time
from bs4 import BeautifulSoup  # 或直接用 re 提取 __NEXT_DATA__

def extract_item_id(url: str) -> str | None:
    """从 articleLink 提取 itemId，支持两种 URL 格式，去除 query string"""
    # 匹配 /items/23089 或 /n/items/23089，忽略 ?preview=1 等参数
    m = re.search(r'/items/(\d+)', url)
    return m.group(1) if m else None

def fetch_article(item_id: str) -> dict | None:
    """抓取单篇文章，返回 {title, summary, createDate, tags} 或 None"""
    url = f"https://content.qieman.com/n/items/{item_id}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[content] {item_id}: fetch error {e}")
        return None
    
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', 
                  r.text, re.DOTALL)
    if not m:
        print(f"[content] {item_id}: no __NEXT_DATA__")
        return None
    
    try:
        data = json.loads(m.group(1))
        article = data["props"]["pageProps"]["item"]["article"]
        return {
            "title": article.get("title") or "",
            "summary": article.get("summary") or "",
            "createDate": article.get("createDate"),
            "tags": article.get("tags") or [],
        }
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        print(f"[content] {item_id}: parse error {e}")
        return None

def main():
    # 1. 读取现有 content.json（增量更新）
    content_path = "docs/content.json"
    existing = {}
    if os.path.exists(content_path):
        with open(content_path) as f:
            existing = json.load(f)
    
    # 2. 从 data.json 收集所有 content.qieman.com articleLink
    with open("docs/data.json") as f:
        data = json.load(f)
    
    item_ids = set()
    for signal in data.get("recentSignals", []):
        iid = extract_item_id(signal.get("articleLink") or "")
        if iid: item_ids.add(iid)
    for holding in data.get("holdings", []):
        for hist in holding.get("history", []):
            iid = extract_item_id(hist.get("articleLink") or "")
            if iid: item_ids.add(iid)
    
    # 3. 只抓取未缓存的
    new_ids = item_ids - set(existing.keys())
    print(f"[content] {len(item_ids)} total, {len(existing)} cached, {len(new_ids)} to fetch")
    
    for iid in sorted(new_ids):
        article = fetch_article(iid)
        if article:
            existing[iid] = article
            print(f"[content] {iid}: {article['title']!r}")
        time.sleep(0.5)  # 避免触发限流
    
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    print(f"[content] done: {len(existing)} articles saved")
```

## docs/index.html 修改要点

```javascript
// 1. 并行加载，content.json 失败不影响主页面
let contentMap = {};
Promise.all([
  fetch('data.json').then(r => r.json()),
  fetch('content.json').then(r => r.json()).catch(() => ({}))
]).then(([data, content]) => {
  contentMap = content;
  render(data);
});

// 2. itemId 提取（与 Python 端保持一致）
function extractItemId(url) {
  if (!url) return null;
  const m = url.match(/\/items\/(\d+)/);
  return m ? m[1] : null;  // 返回字符串，与 content.json key 类型一致
}

// 3. 渲染文章标题（纯文本，不用 innerHTML）
function renderArticleTitle(articleLink) {
  const itemId = extractItemId(articleLink);
  if (!itemId || !contentMap[itemId]) return '';
  const title = contentMap[itemId].title;
  if (!title) return '';
  // 使用 textContent 赋值，不用 innerHTML
  const span = document.createElement('span');
  span.className = 'tl-article-title';
  span.textContent = title;  // XSS 安全
  return span.outerHTML;
}
```

**CSS 新增：**
```css
.tl-article-title {
  font-size: 12px;
  color: #888;
  display: block;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}
@media (max-width: 600px) {
  .tl-article-title { max-width: calc(100vw - 180px); }
}
```

## .github/workflows/update.yml 修改

```yaml
- name: Fetch content metadata
  run: python scripts/fetch_content.py

# 在 commit 步骤中加入 docs/content.json
- name: Commit and push
  run: |
    git add docs/data.json docs/nav/ docs/content.json
    git diff --staged --quiet || git commit -m "chore: update longwin data $(TZ='Asia/Shanghai' date '+%Y-%m-%d')"
    git push
```

**注意：** fetch_content.py 必须在 fetch_signals.py 之后运行（需要 data.json 已更新）。

## 已知风险和缓解措施

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| `__NEXT_DATA__` 结构变更 | HIGH | 防御性 `.get()` 访问，解析失败时跳过并记录 |
| content.qieman.com 限流 | HIGH | 每次请求间隔 0.5s，429 时指数退避重试 |
| `?preview=1` 链接 403 | HIGH | 提取 itemId 时去除 query string |
| data.json 每日覆盖 | CRITICAL | content.json 独立文件，不合并进 data.json |
| XSS（文章 HTML 内容） | HIGH | 只存储 title/summary 纯文本，前端用 textContent |
| content.json 加载失败 | MEDIUM | 并行加载，失败时降级为空 map，页面正常渲染 |
| WeChat 链接无数据 | MEDIUM | 跳过，不显示标题槽位，保留原链接 |

## 不在范围内（推迟到 TODOS）

- 文章摘要展示（先验证标题是否有用）
- 新文章自动发现（用户确认手动维护）
- 微信文章标题抓取（需要浏览器渲染）
- 测试套件（建议后续添加 pytest + responses）

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|---------|
| 1 | CEO | 使用 __NEXT_DATA__ 而非 GraphQL API | Mechanical | P3 (pragmatic) | GraphQL 无 content 查询，__NEXT_DATA__ 已验证可用 | GraphQL |
| 2 | CEO | 不抓取微信文章 | Mechanical | P5 (explicit) | 无结构化数据，og:title 不可用 | 浏览器渲染 |
| 3 | CEO | 历史内容爬虫 + 新内容手动维护 | User decision | — | 用户明确确认 | 全自动发现 |
| 4 | Eng | content.json 独立文件，不合并进 data.json | Mechanical | P5 (explicit) | data.json 每日覆盖，合并会导致内容丢失 | 合并进 data.json |
| 5 | Eng | 单文件 content.json（非 per-item 文件） | Mechanical | P3 (pragmatic) | 107 个文件 = 107 次 HTTP 请求，单文件更简单 | per-item 文件 |
| 6 | Eng | 只存储 title/summary，不存储 content HTML | Mechanical | P5 (explicit) | XSS 风险，title/summary 已满足需求 | 存储完整 HTML |
| 7 | Design | 不在 modal 中展示文章内容 | Mechanical | P5 (explicit) | modal 是 NAV 图表，混合两种心智模型 | modal 展示 |
| 8 | Design | 先只展示标题，不展示摘要 | Mechanical | P3 (pragmatic) | 标题满足 80% 需求，摘要增加复杂度 | 同时展示摘要 |
| 9 | Design | 前端用 textContent 渲染标题 | Mechanical | P5 (explicit) | XSS 安全，innerHTML 有风险 | innerHTML |
| 10 | Design | content.json 加载失败时降级渲染 | Mechanical | P1 (completeness) | 页面不能因 content.json 失败而白屏 | Promise.all 失败即停 |

## GSTACK REVIEW REPORT

| Phase | Status | Voices | Findings | Unresolved |
|---|---|---|---|---|
| CEO | DONE | [subagent-only] (Codex auth expired) | 4 high findings → all resolved via user clarification + plan revision | 0 |
| Design | DONE | [subagent-only] | 5 critical/high findings → all addressed in plan | 0 |
| Eng | DONE | [subagent-only] | 11 findings (1 critical, 4 high, 4 medium, 2 low) → all addressed | 0 |
| DX | SKIPPED | — | Not a developer-facing product | — |
