"""Extract E大 asset-allocation baseline (VERSION A — cleanup) → docs/allocation_baseline_va.json.

V_A improvements over v1:
  1. Blacklist filter: drop percentages whose immediate left context contains
     "跌/回撤/下跌/涨/上涨/收益/赚/亏/回报/下探/跌到/涨到/高点/低点/腰斩/压力测试"
     (these are returns, drawdowns, price targets — NOT allocation rules).
  2. Whitelist: keep ONLY percentages whose immediate context contains
     "配置/仓位/占比/占/持仓/分配/权重/配到/占资产/总仓位/满仓".
  3. Search window tightened: ±1 sentence (was ±3).
  4. Still a heuristic — human review still needed, but signal should be far cleaner.

Output: docs/allocation_baseline_va.json (same schema as v1).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup  # type: ignore
    _HAVE_BS4 = True
except ImportError:
    _HAVE_BS4 = False


REPO_ROOT     = Path(__file__).resolve().parent.parent
CONTENT_PATH  = REPO_ROOT / "docs" / "content.json"
POSTS_DIR     = REPO_ROOT / "posts"
OUTPUT_PATH   = REPO_ROOT / "docs" / "allocation_baseline_va.json"
TZ_CN         = timezone(timedelta(hours=8))


# Axis definitions. id matches frontend conventions; label matches allocation.json
# L1 spellings (plus 可转债 and 红利/低波 which E大 discusses separately).
AXES: List[Dict] = [
    {
        "id": "a_stock",
        "label": "A股",
        "keywords": ["A股", "沪深", "中国股", "国内股市", "国内股票", "沪深300", "中证"],
    },
    {
        "id": "em_overseas",
        "label": "海外新兴",
        "keywords": ["海外新兴", "新兴市场", "港股", "恒生", "中概", "恒指"],
    },
    {
        "id": "dm_overseas",
        "label": "海外成熟",
        "keywords": ["海外成熟", "美股", "纳斯达克", "纳指", "标普", "标普500",
                     "欧洲股", "欧股", "发达市场", "德国DAX"],
    },
    {
        "id": "commodities",
        "label": "商品",
        "keywords": ["黄金", "原油", "石油", "油气", "商品"],
    },
    {
        "id": "bonds",
        "label": "债券",
        "keywords": ["债券", "国债", "债基", "信用债", "纯债", "利率债"],
    },
    {
        "id": "cash",
        "label": "现金",
        "keywords": ["现金", "货币基金", "货基", "现金仓位"],
    },
    {
        "id": "convertibles",
        "label": "可转债",
        "keywords": ["可转债", "转债"],
    },
    {
        "id": "dividend_lowvol",
        "label": "红利/低波",
        "keywords": ["红利", "低波", "红利低波", "中证红利"],
    },
]


# Percentage patterns. Matches e.g. "20%", "20-30%", "20—30%", "20~30%", "20到30%",
# plus "占XX%" / "配置XX%" etc. contexts.
_PCT_RANGE = re.compile(
    r"(\d{1,3})\s*(?:[-—~～至到]|[—~～])\s*(\d{1,3})\s*%"
)
_PCT_SINGLE = re.compile(r"(\d{1,3})\s*%")
# Look for percentage words: 成 (10%) — e.g. "三成" / "五成".
_PCT_CHINESE = re.compile(r"([一二三四五六七八九])\s*成")
_CN_DIGIT = {"一": 10, "二": 20, "三": 30, "四": 40, "五": 50,
             "六": 60, "七": 70, "八": 80, "九": 90}

SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]+")


# Context classification (V_A additions) ----------------------------------
# Patterns checked against a window of chars immediately before a % match.
# If BLACKLIST hits → drop the %. If WHITELIST hits → keep preferentially.
# If neither → drop (strict mode; human can loosen later).
BLACKLIST_CTX = re.compile(
    r"(?:最大)?(?:跌幅|回撤|下跌|跌到|跌至|下探|暴跌|腰斩|"
    r"涨幅|上涨|涨到|涨至|暴涨|"
    r"收益|回报|盈利|赚|亏|浮亏|浮盈|"
    r"高点|低点|新高|新低|止损|止盈|压力测试|股息率)"
)
WHITELIST_CTX = re.compile(
    r"(?:配置|仓位|占比|占资产|占总|占组合|持仓|分配|权重|"
    r"配到|配成|配置到|安排|建议.{0,3}配|计划|目标.{0,3}仓|"
    r"总仓位|满仓|空仓|轻仓|重仓|加仓到|减仓到)"
)
# How many chars before the % to search (typical Chinese window)
CTX_LOOKBEHIND = 14


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def strip_html(html: str) -> str:
    """Strip HTML tags, collapse whitespace."""
    if not html:
        return ""
    if _HAVE_BS4:
        text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    else:
        # Fallback regex strip
        text = re.sub(r"<[^>]+>", "\n", html)
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"'))
    # Normalize whitespace
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def parse_md_front_matter(md: str) -> Tuple[Dict[str, str], str]:
    """Parse a minimal YAML front-matter + return (meta, body)."""
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    header, body = parts[1], parts[2]
    meta: Dict[str, str] = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body.lstrip("\n")


def load_content_json() -> Dict[str, Dict]:
    """Load docs/content.json → {article_id: {title, date, text}}."""
    if not CONTENT_PATH.exists():
        log(f"[baseline] missing {CONTENT_PATH}")
        return {}
    with open(CONTENT_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    out: Dict[str, Dict] = {}
    for aid, entry in raw.items():
        ms = entry.get("createDate")
        if isinstance(ms, (int, float)) and ms > 0:
            date = datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        else:
            date = ""
        text = strip_html(entry.get("content") or "")
        out[str(aid)] = {
            "title": (entry.get("title") or "").strip(),
            "date":  date,
            "text":  text,
            "source": "content.json",
        }
    return out


def load_posts_md() -> Dict[str, Dict]:
    """Load posts/*.md, return {article_id_or_key: {title, date, text}}."""
    out: Dict[str, Dict] = {}
    if not POSTS_DIR.exists():
        return out
    # Only top-level .md (skip posts/weibo/*.md which are different corpus)
    for p in sorted(POSTS_DIR.glob("*.md")):
        name = p.stem  # e.g. 2025-10-22-50893-欢迎各位莅临指导
        m = re.match(r"(\d{4}-\d{2}-\d{2})-([^-]+)-(.*)$", name)
        if not m:
            continue
        date_str, aid, _ = m.group(1), m.group(2), m.group(3)
        # Skip weibo-style ids (wb_xxx) — different content model.
        if aid.startswith("wb_"):
            continue
        try:
            md = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            log(f"[baseline] read fail {p.name}: {e}")
            continue
        meta, body = parse_md_front_matter(md)
        title = (meta.get("title") or "").strip()
        date_final = meta.get("date") or date_str
        # Strip markdown images/links for cleaner sentences.
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
        body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
        out[str(aid)] = {
            "title":  title,
            "date":   date_final,
            "text":   body.strip(),
            "source": "posts_md",
        }
    return out


def merge_corpora(primary: Dict[str, Dict],
                  supplemental: Dict[str, Dict]) -> Dict[str, Dict]:
    """content.json is primary; add missing ids from posts/*.md."""
    merged = dict(primary)
    added = 0
    for aid, entry in supplemental.items():
        if aid in merged:
            # Prefer content.json but fill gaps.
            if not merged[aid].get("text") and entry.get("text"):
                merged[aid]["text"] = entry["text"]
            if not merged[aid].get("title") and entry.get("title"):
                merged[aid]["title"] = entry["title"]
        else:
            merged[aid] = entry
            added += 1
    log(f"[baseline] corpus: {len(primary)} from content.json + {added} new from posts/ = {len(merged)}")
    return merged


def sentence_tokenize(text: str) -> List[str]:
    """Split text into sentences and trim."""
    sents = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    # Drop boilerplate-ish very short lines.
    return [s for s in sents if len(s) >= 4]


def classify_pct_context(sent: str, match_start: int) -> str:
    """Return 'allocation' | 'noise' | 'unknown' by scanning the window before
    the % match. Allocation terms win over noise if both appear."""
    window_start = max(0, match_start - CTX_LOOKBEHIND)
    ctx = sent[window_start:match_start]
    if WHITELIST_CTX.search(ctx):
        return "allocation"
    if BLACKLIST_CTX.search(ctx):
        return "noise"
    return "unknown"


def find_pcts(sent: str) -> List[Tuple[int, Optional[int]]]:
    """Return list of (low, high) percentage pairs (high=None for singletons).
    V_A: Only keep percentages classified as 'allocation' context."""
    hits: List[Tuple[int, Optional[int]]] = []
    used_spans: List[Tuple[int, int]] = []
    for m in _PCT_RANGE.finditer(sent):
        lo, hi = int(m.group(1)), int(m.group(2))
        if not (0 <= lo <= 100 and 0 <= hi <= 100 and lo <= hi):
            continue
        cls = classify_pct_context(sent, m.start())
        if cls != "allocation":
            continue
        hits.append((lo, hi))
        used_spans.append(m.span())
    for m in _PCT_SINGLE.finditer(sent):
        if any(m.start() >= s and m.end() <= e for s, e in used_spans):
            continue
        v = int(m.group(1))
        if not (0 <= v <= 100):
            continue
        cls = classify_pct_context(sent, m.start())
        if cls != "allocation":
            continue
        hits.append((v, None))
    # Chinese "X成" — usually explicit allocation context ("三成仓位"), keep unconditionally.
    for m in _PCT_CHINESE.finditer(sent):
        v = _CN_DIGIT.get(m.group(1))
        if v is not None:
            hits.append((v, None))
    return hits


def extract_candidates_for_axis(articles: Dict[str, Dict],
                                axis: Dict) -> List[Dict]:
    """Scan every article, find sentences containing any axis keyword within
    ±3 sentences of a percentage. Return candidate source dicts.
    """
    kws = axis["keywords"]
    candidates: List[Dict] = []
    for aid, art in articles.items():
        text = art.get("text") or ""
        if not text:
            continue
        if not any(k in text for k in kws):
            continue
        sents = sentence_tokenize(text)
        n = len(sents)
        # Find keyword-bearing indices.
        kw_idx = [i for i, s in enumerate(sents) if any(k in s for k in kws)]
        if not kw_idx:
            continue
        # For each keyword sentence, search ±1 sentence for % patterns (V_A tightened from ±3).
        seen_quotes: set = set()
        for i in kw_idx:
            lo_i = max(0, i - 1)
            hi_i = min(n, i + 2)
            pcts: List[Tuple[int, Optional[int]]] = []
            for j in range(lo_i, hi_i):
                pcts.extend(find_pcts(sents[j]))
            if not pcts:
                continue
            # Build context quote: keyword sentence + ±1 sentence.
            quote_lo = max(0, i - 1)
            quote_hi = min(n, i + 2)
            quote = "".join(
                (sents[j] + "。") for j in range(quote_lo, quote_hi)
            ).strip()
            if len(quote) > 220:
                quote = quote[:217] + "..."
            if quote in seen_quotes:
                continue
            seen_quotes.add(quote)
            # Collapse pcts → rule string.
            rule_parts: List[str] = []
            for lo, hi in pcts:
                if hi is None:
                    rule_parts.append(f"{lo}%")
                else:
                    rule_parts.append(f"{lo}-{hi}%")
            rule = f"{axis['label']} 附近提及: " + ", ".join(rule_parts[:6])
            candidates.append({
                "article_id": aid,
                "date":       art.get("date") or "",
                "title":      art.get("title") or "",
                "quote":      quote,
                "rule_extracted": rule,
                "_pcts":      pcts,  # stripped before output
            })
    return candidates


def aggregate_axis(axis: Dict, candidates: List[Dict]) -> Dict:
    """Compute min/max/target + confidence from candidates."""
    axis_out = {
        "id":         axis["id"],
        "label":      axis["label"],
        "min_pct":    None,
        "max_pct":    None,
        "target_pct": None,
        "confidence": "low",
        "sources":    [],
        "notes":      "",
    }
    if not candidates:
        axis_out["notes"] = "未在扫描范围内发现明确量化规则 — 需人工补全"
        return axis_out

    # Collect lo/hi samples.
    los: List[int] = []
    his: List[int] = []
    for c in candidates:
        for lo, hi in c["_pcts"]:
            # Filter implausibly large allocations for a single asset class.
            if hi is None:
                # Single %: treat as a rough point estimate (contributes to both lo and hi).
                if 0 < lo <= 80:
                    los.append(lo)
                    his.append(lo)
            else:
                if 0 <= lo <= 100 and 0 < hi <= 100 and hi - lo <= 60:
                    los.append(lo)
                    his.append(hi)

    if los and his:
        min_pct = int(round(median(los)))
        max_pct = int(round(median(his)))
        if min_pct > max_pct:
            min_pct, max_pct = max_pct, min_pct
        target_pct = int(round((min_pct + max_pct) / 2))
        axis_out["min_pct"] = min_pct
        axis_out["max_pct"] = max_pct
        axis_out["target_pct"] = target_pct

    # Confidence heuristic based on source agreement.
    # "agree within ±5%" on the midpoint.
    midpoints: List[float] = []
    for c in candidates:
        for lo, hi in c["_pcts"]:
            midpoints.append(lo if hi is None else (lo + hi) / 2)
    n_sources = len({c["article_id"] for c in candidates})
    if n_sources >= 5 and midpoints:
        med = median(midpoints)
        agree = sum(1 for m in midpoints if abs(m - med) <= 5)
        if agree / max(1, len(midpoints)) >= 0.6:
            axis_out["confidence"] = "high"
        else:
            axis_out["confidence"] = "medium"
    elif 2 <= n_sources <= 4:
        axis_out["confidence"] = "medium"
    else:
        axis_out["confidence"] = "low"

    # Sort newest-first by date. Drop internal _pcts.
    def sort_key(c: Dict) -> str:
        return c.get("date") or ""
    sorted_cands = sorted(candidates, key=sort_key, reverse=True)
    # Keep top 15 sources per axis to bound output size.
    trimmed = sorted_cands[:15]
    axis_out["sources"] = [
        {k: v for k, v in c.items() if k != "_pcts"}
        for c in trimmed
    ]
    if len(sorted_cands) > len(trimmed):
        axis_out["notes"] = (
            f"共发现 {len(sorted_cands)} 条候选，展示最新 {len(trimmed)} 条。"
        )
    return axis_out


def main() -> None:
    log("[baseline] loading corpora...")
    primary = load_content_json()
    supplemental = load_posts_md()
    articles = merge_corpora(primary, supplemental)

    articles_with_any_match = 0
    for art in articles.values():
        if any(k in (art.get("text") or "")
               for axis in AXES for k in axis["keywords"]):
            articles_with_any_match += 1

    log(f"[baseline] scanning {len(articles)} articles across {len(AXES)} axes...")

    axes_out: List[Dict] = []
    axes_without_data: List[str] = []
    for axis in AXES:
        cands = extract_candidates_for_axis(articles, axis)
        axis_out = aggregate_axis(axis, cands)
        log(f"[baseline]   axis={axis['label']:<8} "
            f"matches={len(cands):<4} "
            f"sources_articles={len({c['article_id'] for c in cands}):<3} "
            f"range={axis_out['min_pct']}-{axis_out['max_pct']}% "
            f"conf={axis_out['confidence']}")
        if not cands:
            axes_without_data.append(axis["label"])
        axes_out.append(axis_out)

    result = {
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
        "version": "v_a (cleanup: blacklist/whitelist context + ±1 window)",
        "source": f"extracted from {len(articles)} E大 articles via V_A heuristics",
        "disclaimer": (
            "本基线仅为对 E大 公开文章的整理，不构成任何投资建议。"
            "V_A 版本已过滤价格跌幅/涨幅/收益率噪音，但仍需人工审核。"
        ),
        "axes": axes_out,
        "extraction_stats": {
            "articles_scanned":       len(articles),
            "articles_with_matches":  articles_with_any_match,
            "axes_with_data":         len(AXES) - len(axes_without_data),
            "axes_without_data":      axes_without_data,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"[baseline] wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
