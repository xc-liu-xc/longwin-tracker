"""Aggregate LLM-classified candidates → docs/allocation_baseline_vb.json.

Reads /tmp/_baseline_vb_classified.json produced by an LLM classifier pass
(see Agent prompt in conversation). Each candidate has a `rule_kind`:
target | ceiling | floor | range | current_status | noise.

Aggregation policy (V_B):
  - Use only rule_kind ∈ {target, ceiling, floor, range} for computing
    the baseline range. `current_status` is shown as evidence but doesn't
    set the baseline. `noise` is dropped.
  - For `ceiling`: contributes to max_pct only.
  - For `floor`: contributes to min_pct only.
  - For `target` / `range`: contributes to both endpoints.
  - Final min_pct = min of floor/target/range lows. Final max_pct = max of
    ceiling/target/range highs. target_pct = midpoint.
  - Confidence: high if ≥3 distinct rule-kind sources agree (span < 15pp);
    medium if ≥2; low otherwise.
  - Sources field includes ALL non-noise candidates (target/ceiling/floor/
    range/current_status), newest-first, capped at 20/axis, with the LLM's
    rule_kind + reasoning preserved.

Output: docs/allocation_baseline_vb.json (same top-level schema as v1/v_a,
plus per-source `rule_kind` + `reasoning`).
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSIFIED_PATH = Path("/tmp/_baseline_vb_classified.json")
CAND_INPUT_PATH = Path("/tmp/_baseline_vb_input.json")
OUTPUT_PATH = REPO_ROOT / "docs" / "allocation_baseline.json"
TZ_CN = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def aggregate_axis(axis_id: str, label: str, items: List[Dict]) -> Dict:
    """Compute min/max/target + confidence + sources from classified items."""
    out: Dict = {
        "id": axis_id,
        "label": label,
        "min_pct": None,
        "max_pct": None,
        "target_pct": None,
        "confidence": "low",
        "sources": [],
        "notes": "",
    }

    # Partition by kind.
    ceilings: List[int] = []   # max_pct from ceiling rules
    floors: List[int] = []     # min_pct from floor rules
    targets_lo: List[int] = []
    targets_hi: List[int] = []
    rule_items: List[Dict] = []   # non-noise, non-current_status
    evidence_items: List[Dict] = []  # current_status + rule_items (shown as sources)

    for it in items:
        k = it.get("rule_kind")
        lo = it.get("pct_low")
        hi = it.get("pct_high")
        if k == "noise":
            continue
        if k == "ceiling":
            if hi is not None:
                ceilings.append(hi)
            rule_items.append(it)
        elif k == "floor":
            if lo is not None:
                floors.append(lo)
            rule_items.append(it)
        elif k in ("target", "range"):
            if lo is not None:
                targets_lo.append(lo)
            if hi is not None:
                targets_hi.append(hi)
            rule_items.append(it)
        elif k == "current_status":
            # evidence only
            pass
        evidence_items.append(it)

    # Compute min_pct / max_pct.
    lo_samples = floors + targets_lo
    hi_samples = ceilings + targets_hi
    if lo_samples or hi_samples:
        # min = lowest floor, hi = highest ceiling (conservative wide band);
        # for targets/range inside, take the median for a tighter center.
        if targets_lo:
            min_pct = min(floors + [min(targets_lo)]) if floors else min(targets_lo)
        elif floors:
            min_pct = min(floors)
        else:
            min_pct = None
        if targets_hi:
            max_pct = max(ceilings + [max(targets_hi)]) if ceilings else max(targets_hi)
        elif ceilings:
            max_pct = max(ceilings)
        else:
            max_pct = None
        out["min_pct"] = min_pct
        out["max_pct"] = max_pct
        if min_pct is not None and max_pct is not None:
            out["target_pct"] = int(round((min_pct + max_pct) / 2))
        elif min_pct is not None:
            out["target_pct"] = min_pct
        elif max_pct is not None:
            out["target_pct"] = max_pct

    # Confidence.
    n_rule_sources = len({it.get("article_id") for it in rule_items})
    lo = out["min_pct"]
    hi = out["max_pct"]
    span = (hi - lo) if (lo is not None and hi is not None) else 999
    if n_rule_sources >= 3 and span <= 15:
        out["confidence"] = "high"
    elif n_rule_sources >= 2:
        out["confidence"] = "medium"
    else:
        out["confidence"] = "low"

    # Sort newest-first.
    def k(it: Dict) -> str:
        return it.get("date") or ""
    evidence_items.sort(key=k, reverse=True)
    trimmed = evidence_items[:20]
    out["sources"] = [
        {
            "article_id":     it.get("article_id"),
            "date":           it.get("date") or "",
            "title":          it.get("title") or "",
            "quote":          it.get("quote") or "",
            "rule_kind":      it.get("rule_kind"),
            "reasoning":      it.get("reasoning") or "",
            "pct_low":        it.get("pct_low"),
            "pct_high":       it.get("pct_high"),
            "rule_extracted": summarize(it),
        }
        for it in trimmed
    ]
    if len(evidence_items) > len(trimmed):
        out["notes"] = f"共 {len(evidence_items)} 条非噪音候选，展示最新 {len(trimmed)} 条。"
    if not rule_items:
        out["notes"] = (out["notes"] + " " if out["notes"] else "") + (
            "LLM 未判定任何硬性配置规则（仅有现状快照），基线缺失，需人工补全。"
        ).strip()

    return out


def summarize(it: Dict) -> str:
    k = it.get("rule_kind") or ""
    lo = it.get("pct_low")
    hi = it.get("pct_high")
    if lo is None and hi is None:
        return k
    if k == "ceiling":
        return f"{k} ≤{hi}%"
    if k == "floor":
        return f"{k} ≥{lo}%"
    if k in ("target", "range"):
        if lo is not None and hi is not None and lo != hi:
            return f"{k} {lo}-{hi}%"
        if lo is not None:
            return f"{k} {lo}%"
        if hi is not None:
            return f"{k} {hi}%"
    if k == "current_status":
        if lo == hi and lo is not None:
            return f"status={lo}%"
        if lo is not None or hi is not None:
            return f"status={lo}-{hi}%"
    return k


def main() -> None:
    if not CLASSIFIED_PATH.exists():
        log(f"[v_b] missing {CLASSIFIED_PATH} — run the LLM classifier first.")
        sys.exit(1)
    classified = json.loads(CLASSIFIED_PATH.read_text(encoding="utf-8"))
    cand_input = json.loads(CAND_INPUT_PATH.read_text(encoding="utf-8"))

    # Build axis_id → label + total_candidates map from input.
    axis_meta = {a["id"]: a for a in cand_input["axes"]}

    kind_counts: Dict[str, int] = {}
    axes_out: List[Dict] = []
    for ax in classified["axes"]:
        meta = axis_meta.get(ax["id"], {})
        items = ax.get("classified") or []
        out = aggregate_axis(ax["id"], ax.get("label") or meta.get("label") or ax["id"], items)
        out["total_candidates"] = meta.get("total_candidates", len(items))
        out["classified_count"] = len(items)
        for it in items:
            k = it.get("rule_kind") or "unknown"
            kind_counts[k] = kind_counts.get(k, 0) + 1
        axes_out.append(out)
        log(f"[v_b]  {out['label']:<10} range={out['min_pct']}-{out['max_pct']}% "
            f"conf={out['confidence']:<6} sources={len(out['sources'])}")

    result = {
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
        "version": "v_b (LLM classification of v_a candidates)",
        "source": "V_A heuristic candidates → LLM rule_kind classifier (target/ceiling/floor/range/current_status/noise)",
        "disclaimer": (
            "本基线仅为对 E大 公开文章的启发式提取 + LLM 辅助分类，不构成任何投资建议。"
            "V_B 版本已过滤噪音并区分规则 vs 现状，但仍需人工审核。"
        ),
        "axes": axes_out,
        "extraction_stats": {
            "total_candidates_classified": sum(v for v in kind_counts.values()),
            "rule_kind_distribution": kind_counts,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"[v_b] wrote {OUTPUT_PATH}")
    log(f"[v_b] kind distribution: {kind_counts}")


if __name__ == "__main__":
    main()
