"""Build docs/allocation_baseline.json end-to-end.

Daily-runnable pipeline that replaces the manual three-step dance:
  1. Extract V_A candidates from content.json + posts/*.md (heuristic filter).
  2. Look up each candidate in scripts/baseline_classifications.json. New
     candidates (never seen before) default to rule_kind="noise" so they
     don't pollute the baseline until a human classifies them.
  3. Aggregate classified items → final baseline (target/ceiling/floor/range/
     current_status → min_pct, max_pct, confidence, sources).

Output: docs/allocation_baseline.json

Run as: `python3 scripts/build_baseline.py`
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Reuse V_A extraction (noise-filtered candidate generation).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_allocation_baseline_va import (  # type: ignore
    load_content_json, load_posts_md, merge_corpora,
    extract_candidates_for_axis, AXES,
)
from aggregate_baseline_vb import aggregate_axis, summarize  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
CLS_PATH = REPO_ROOT / "scripts" / "baseline_classifications.json"
OUTPUT_PATH = REPO_ROOT / "docs" / "allocation_baseline.json"
TZ_CN = timezone(timedelta(hours=8))

QUOTE_KEY_LEN = 40  # chars of quote to use for stable key matching


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_classifications() -> Dict[tuple, Dict]:
    """Load classifications keyed by (axis_id, article_id, quote_prefix)."""
    if not CLS_PATH.exists():
        log(f"[baseline] WARN: {CLS_PATH} missing — all candidates will be 'noise'")
        return {}
    entries = json.loads(CLS_PATH.read_text(encoding="utf-8"))
    return {
        (e["axis_id"], e.get("article_id") or "", e.get("quote_prefix") or ""): e
        for e in entries
    }


def main() -> None:
    log("[baseline] loading corpora...")
    primary = load_content_json()
    supp = load_posts_md()
    articles = merge_corpora(primary, supp)
    classifications = load_classifications()

    log(f"[baseline] loaded {len(classifications)} pre-classified entries")

    axes_out: List[Dict] = []
    kind_counts: Dict[str, int] = {}
    unclassified_new: List[Dict] = []

    for axis in AXES:
        cands = extract_candidates_for_axis(articles, axis)
        classified: List[Dict] = []
        for c in cands:
            aid = c.get("article_id") or ""
            qp = (c.get("quote") or "")[:QUOTE_KEY_LEN]
            key = (axis["id"], aid, qp)
            cls = classifications.get(key)
            if cls is None:
                # Unseen candidate → default to noise but record for human review.
                unclassified_new.append({
                    "axis_id":    axis["id"],
                    "axis_label": axis["label"],
                    "article_id": aid,
                    "date":       c.get("date") or "",
                    "title":      c.get("title") or "",
                    "quote":      c.get("quote") or "",
                    "rule_extracted_heuristic": c.get("rule_extracted") or "",
                })
                classified.append({
                    "article_id": aid,
                    "date":       c.get("date") or "",
                    "title":      c.get("title") or "",
                    "quote":      c.get("quote") or "",
                    "rule_kind":  "noise",
                    "pct_low":    None,
                    "pct_high":   None,
                    "reasoning":  "(未分类新候选，默认噪音)",
                })
            else:
                classified.append({
                    "article_id": aid,
                    "date":       c.get("date") or "",
                    "title":      c.get("title") or "",
                    "quote":      c.get("quote") or "",
                    "rule_kind":  cls["rule_kind"],
                    "pct_low":    cls.get("pct_low"),
                    "pct_high":   cls.get("pct_high"),
                    "reasoning":  cls.get("reasoning") or "",
                })
            kind_counts[classified[-1]["rule_kind"]] = (
                kind_counts.get(classified[-1]["rule_kind"], 0) + 1
            )

        axis_out = aggregate_axis(axis["id"], axis["label"], classified)
        axis_out["total_candidates"] = len(cands)
        axis_out["classified_count"] = len(classified)
        axes_out.append(axis_out)
        log(f"[baseline]  {axis['label']:<10} cands={len(cands):<3} "
            f"range={axis_out['min_pct']}-{axis_out['max_pct']}% "
            f"conf={axis_out['confidence']}")

    if unclassified_new:
        log(f"[baseline] {len(unclassified_new)} NEW candidates default to noise — "
            f"update scripts/baseline_classifications.json to classify them.")
        # Dump pending review list for humans.
        pending_path = REPO_ROOT / "scripts" / "baseline_classifications_pending.json"
        pending_path.write_text(
            json.dumps(unclassified_new, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"[baseline] pending review list: {pending_path}")

    result = {
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
        "version": "v_b (heuristic extraction → pre-classified rule_kind aggregation)",
        "source": (
            f"extracted from {len(articles)} E大 articles via V_A heuristics, "
            f"classifications loaded from scripts/baseline_classifications.json"
        ),
        "disclaimer": (
            "本基线仅为对 E大 公开文章的启发式提取 + 分类整理，"
            "不包含基于市场估值的动态调整，不构成任何投资建议。"
        ),
        "axes": axes_out,
        "extraction_stats": {
            "articles_scanned": len(articles),
            "total_candidates_classified": sum(kind_counts.values()),
            "rule_kind_distribution": kind_counts,
            "new_unclassified_count": len(unclassified_new),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"[baseline] wrote {OUTPUT_PATH}")
    log(f"[baseline] kind distribution: {kind_counts}")


if __name__ == "__main__":
    main()
