"""
Build docs/allocation.json:
- current snapshot: per-asset-class 持仓份数 / 占比 / 累计收益率 + per-fund detail
- historical timeseries: per-day asset class %s for stacked area chart

Caliber (与且慢截图一致):
- Each "份" = 1 normalized cost unit
- Fund X market value at date D = sum over each held slot (FIFO):
    current_adj_nav(D) / buy_adj_nav(slot)
- Cash (现金) = (totalUnit - currently_held_units) × 1.0
- Total portfolio = sum(fund MVs) + cash
- 占比 = component MV / total MV
"""

import json
import os
import bisect
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

# ── Asset class taxonomy (L1 keys = Chinese names, source of truth: docs/taxonomy.json) ──
# Color = Tailwind 500-shade for legend / stacked bar (sunburst uses its own 4-step ramp)
ASSET_CLASS_ORDER = [
    "A股",
    "海外新兴",
    "海外成熟",
    "商品",
    "债券",
    "房地产",
    "外汇",
    "现金",
    "其他",
]

ASSET_COLORS = {
    # Stacked-bar / legend uses middle shade (palette[1]) of sunburst ramps for visual consistency.
    "A股":          "#CC8987",  # dusty rose mid
    "海外新兴": "#6FA0AF",  # slate teal mid (deep family)
    "海外成熟": "#84B0BF",  # slate teal mid (light family — 同族 1 档)
    "商品":         "#CDA77D",  # ochre mid
    "债券":         "#9494B5",  # slate violet mid
    "房地产":       "#A4B58A",  # sage mid
    "外汇":         "#88AAA3",  # teal-green mid
    "现金":         "#BDB8AD",  # warm taupe mid
    "其他":         "#B0B0B0",  # neutral mid
}


def load_taxonomy():
    """Load docs/taxonomy.json, return {code: {l1, l2, l3, display_name}}."""
    p = "docs/taxonomy.json"
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("funds", {})


def load_qieman_l1_map():
    """Build {code: l1} from qieman_allocation.json — authoritative source.
    Merges both 150 and S plans; later plans override earlier on conflict (rare)."""
    p = "docs/qieman_allocation.json"
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        qie = json.load(f)
    out = {}
    for plan in (qie.get("plans") or {}).values():
        by_l1 = ((plan.get("composition") or {}).get("by_l1")) or {}
        for l1, bucket in by_l1.items():
            for fund in (bucket.get("funds") or []):
                code = fund.get("code")
                if code:
                    out[code] = l1
    return out


_TAXONOMY = load_taxonomy()
_QIEMAN_L1 = load_qieman_l1_map()


def class_for(code):
    """L1 lookup: qieman authoritative → taxonomy fallback → '其他'."""
    if code in _QIEMAN_L1:
        return _QIEMAN_L1[code]
    return (_TAXONOMY.get(code) or {}).get("l1", "其他")




def load_nav(fund_code):
    """Returns (sorted_dates, adj_nav_series) or (None, None)."""
    path = f"docs/nav/{fund_code}.json"
    if not os.path.exists(path):
        return None, None
    with open(path) as f:
        nav = json.load(f)
    dates = nav.get("dates") or []
    series = nav.get("adjNav") or nav.get("accNav") or nav.get("unitNav") or []
    if not dates or not series:
        return None, None
    return dates, series


def lookup_nav(dates, series, target_date):
    """Forward-fill: returns nav at or before target_date, or None."""
    if not dates:
        return None
    idx = bisect.bisect_right(dates, target_date)
    if idx == 0:
        return None
    return series[idx - 1]


def gather_events(holdings):
    """Flatten holdings.history into sorted (date, code, action, units) list."""
    events = []
    for h in holdings:
        code = h["fundCode"]
        for ev in h.get("history", []):
            d = ev.get("date")
            action = ev.get("action")
            units = ev.get("unit", 1)
            if d and action in ("buy", "sell") and units > 0:
                events.append((d, code, action, units))
    events.sort()
    return events


def replay_to_final(events, nav_cache):
    """Replay all events; return final {code: [buy_nav, ...]} (FIFO slots)."""
    slots = defaultdict(list)
    for d, code, action, units in events:
        dates, series = nav_cache.get(code, (None, None))
        nav_at = lookup_nav(dates, series, d) if dates else None
        if action == "buy":
            for _ in range(units):
                slots[code].append(nav_at if nav_at else 1.0)
        else:  # sell, FIFO pop
            for _ in range(units):
                if slots[code]:
                    slots[code].pop(0)
    return slots


def build_current_snapshot(holdings, total_unit, nav_cache, funds_meta, plan_id):
    """Build current asset composition: by_category list with active+cleared funds."""
    name_lookup = {h["fundCode"]: h["fundName"] for h in holdings}
    bought_map = {h["fundCode"]: h.get("bought", 0) for h in holdings}
    events = gather_events(holdings)
    final_slots = replay_to_final(events, nav_cache)

    # Per-fund current MV
    fund_mv = {}
    for code, buy_navs in final_slots.items():
        if not buy_navs:
            continue
        dates, series = nav_cache.get(code, (None, None))
        if not dates:
            continue
        cur_nav = series[-1]
        mv = sum(cur_nav / bn for bn in buy_navs if bn)
        fund_mv[code] = {"mv": mv, "units": len(buy_navs), "current_nav": cur_nav}

    held_units = sum(len(s) for s in final_slots.values())
    cash_units = max(0, total_unit - held_units)
    cash_mv = cash_units * 1.0

    total_mv = sum(d["mv"] for d in fund_mv.values()) + cash_mv
    if total_mv <= 0:
        total_mv = 1.0  # avoid div0

    # Group by class
    cat_buckets = defaultdict(lambda: {"funds": [], "units": 0, "mv": 0.0})
    cleared_buckets = defaultdict(list)  # cleared funds per class

    for h in holdings:
        code = h["fundCode"]
        cls = class_for(code)
        meta = funds_meta.get(code, {})
        plan_meta = meta.get("plans", {}).get(plan_id, {})
        ret = plan_meta.get("return_avg_compound")

        # Last trade date — for display next to fund name
        last_trade = None
        history = h.get("history", [])
        if history:
            last_trade = max((e.get("date") for e in history), default=None)

        if code in fund_mv:
            info = fund_mv[code]
            cat_buckets[cls]["funds"].append({
                "code": code,
                "name": name_lookup[code],
                "units": info["units"],
                "mv": info["mv"],
                "share_pct": round(info["mv"] / total_mv * 100, 2),
                "return_pct": round(ret, 2) if ret is not None else None,
                "last_trade": last_trade,
                "status": "active",
            })
            cat_buckets[cls]["units"] += info["units"]
            cat_buckets[cls]["mv"] += info["mv"]
        else:
            # Cleared (had history but currentUnit == 0)
            if bought_map.get(code, 0) > 0:
                cleared_buckets[cls].append({
                    "code": code,
                    "name": name_lookup[code],
                    "units": 0,
                    "mv": 0,
                    "share_pct": 0,
                    "return_pct": round(ret, 2) if ret is not None else None,
                    "last_trade": last_trade,
                    "status": "cleared",
                })

    # Build ordered output
    by_category = []
    for cls in ASSET_CLASS_ORDER:
        if cls == "现金":
            by_category.append({
                "class": cls,
                "label": cls,
                "color": ASSET_COLORS[cls],
                "units": cash_units,
                "share_pct": round(cash_mv / total_mv * 100, 2),
                "return_pct": None,
                "funds": [],
            })
            continue
        if cls not in cat_buckets and cls not in cleared_buckets:
            continue
        d = cat_buckets.get(cls, {"funds": [], "units": 0, "mv": 0.0})
        funds_active = sorted(d["funds"], key=lambda x: -x["mv"])
        funds_cleared = sorted(cleared_buckets.get(cls, []),
                               key=lambda x: (x.get("last_trade") or ""), reverse=True)
        # Category-level return: NAV-weighted average of active fund returns;
        # if no active, use simple avg of cleared returns
        if funds_active:
            tot_mv = sum(f["mv"] for f in funds_active) or 1
            cat_ret = sum((f["return_pct"] or 0) * f["mv"] for f in funds_active) / tot_mv
        else:
            ret_list = [f["return_pct"] for f in funds_cleared if f["return_pct"] is not None]
            cat_ret = sum(ret_list) / len(ret_list) if ret_list else 0
        by_category.append({
            "class": cls,
            "label": cls,
            "color": ASSET_COLORS[cls],
            "units": d["units"],
            "share_pct": round(d["mv"] / total_mv * 100, 2),
            "return_pct": round(cat_ret, 2),
            "funds": funds_active + funds_cleared,
        })

    # ── Sunburst tree: L1 → L2 → L3 → fund ──
    # Aggregates active+cleared MV/units; cleared funds contribute 0 MV but appear as leaves.
    tax_tree = build_taxonomy_tree(holdings, fund_mv, bought_map, name_lookup,
                                    funds_meta, plan_id, total_mv)

    return {
        "total_unit": total_unit,
        "invested_unit": held_units,
        "cash_unit": cash_units,
        "by_category": by_category,
        "by_taxonomy": tax_tree,
    }


def build_taxonomy_tree(holdings, fund_mv, bought_map, name_lookup,
                        funds_meta, plan_id, total_mv):
    """3-layer sunburst data: L1 → L2 → L3 → fund leaf.

    Each node: {name, value (MV), units, share_pct, return_pct, children?}
    Leaf (fund): {name (display_name), code, value, units, share_pct, return_pct, status}
    Cleared funds appear as leaves with value=0 (sunburst won't render zero, but
    we keep return_pct so frontend can display them in a side panel).
    """
    tree = {}  # {l1: {meta, l2: {meta, l3: {meta, funds: []}}}}

    for h in holdings:
        code = h["fundCode"]
        if bought_map.get(code, 0) <= 0:
            continue  # never bought, skip
        tax = _TAXONOMY.get(code) or {}
        l1 = tax.get("l1", "其他")
        l2 = tax.get("l2") or "(其他)"
        l3 = tax.get("l3") or "(其他)"
        disp = tax.get("display_name") or name_lookup.get(code, code)

        info = fund_mv.get(code)
        units = info["units"] if info else 0
        mv    = info["mv"] if info else 0
        active = info is not None

        meta = funds_meta.get(code, {})
        plan_meta = meta.get("plans", {}).get(plan_id, {})
        ret = plan_meta.get("return_avg_compound")
        ret_round = round(ret, 2) if ret is not None else None

        l1_node = tree.setdefault(l1, {"_units": 0, "_mv": 0.0, "children": {}})
        l2_node = l1_node["children"].setdefault(l2, {"_units": 0, "_mv": 0.0, "children": {}})
        l3_node = l2_node["children"].setdefault(l3, {"_units": 0, "_mv": 0.0, "funds": []})

        l3_node["funds"].append({
            "name":       disp,
            "code":       code,
            "value":      round(mv, 4),
            "units":      units,
            "share_pct":  round(mv / total_mv * 100, 2) if mv else 0,
            "return_pct": ret_round,
            "status":     "active" if active else "cleared",
        })
        if active:
            l3_node["_units"] += units
            l3_node["_mv"]    += mv
            l2_node["_units"] += units
            l2_node["_mv"]    += mv
            l1_node["_units"] += units
            l1_node["_mv"]    += mv

    # Convert nested dicts → ordered ECharts-friendly tree
    def fmt_l3(name, node):
        return {
            "name":       name,
            "value":      round(node["_mv"], 4),
            "units":      node["_units"],
            "share_pct":  round(node["_mv"] / total_mv * 100, 2) if node["_mv"] else 0,
            "children":   sorted(node["funds"], key=lambda f: -f["value"]),
        }
    def fmt_l2(name, node):
        return {
            "name":       name,
            "value":      round(node["_mv"], 4),
            "units":      node["_units"],
            "share_pct":  round(node["_mv"] / total_mv * 100, 2) if node["_mv"] else 0,
            "children":   sorted([fmt_l3(k, v) for k, v in node["children"].items()],
                                  key=lambda x: -x["value"]),
        }
    def fmt_l1(name, node):
        return {
            "name":       name,
            "value":      round(node["_mv"], 4),
            "units":      node["_units"],
            "share_pct":  round(node["_mv"] / total_mv * 100, 2) if node["_mv"] else 0,
            "color":      ASSET_COLORS.get(name, "#999"),
            "children":   sorted([fmt_l2(k, v) for k, v in node["children"].items()],
                                  key=lambda x: -x["value"]),
        }

    out = []
    for cls in ASSET_CLASS_ORDER:
        if cls in tree:
            out.append(fmt_l1(cls, tree[cls]))
    # Append any L1 not in canonical order (defensive)
    for k, v in tree.items():
        if k not in ASSET_CLASS_ORDER:
            out.append(fmt_l1(k, v))
    return out


def build_timeseries(holdings, total_unit, nav_cache, max_points=600):
    """Daily asset class % over time, downsampled to ~max_points."""
    events = gather_events(holdings)
    if not events:
        return []
    first_trade = events[0][0]

    # Union of nav dates ≥ first_trade
    all_dates = set()
    for code, (dates, _) in nav_cache.items():
        if dates:
            for d in dates:
                if d >= first_trade:
                    all_dates.add(d)
    all_dates = sorted(all_dates)
    if not all_dates:
        return []

    # Downsample step
    step = max(1, len(all_dates) // max_points)
    sample_dates = set(all_dates[::step]) | {all_dates[-1]}

    timeseries = []
    slots = defaultdict(list)
    event_idx = 0

    for d in all_dates:
        # Apply events with date ≤ d
        while event_idx < len(events) and events[event_idx][0] <= d:
            ed, code, action, units = events[event_idx]
            dates, series = nav_cache.get(code, (None, None))
            nav_at = lookup_nav(dates, series, ed) if dates else None
            if action == "buy":
                for _ in range(units):
                    slots[code].append(nav_at if nav_at else 1.0)
            else:
                for _ in range(units):
                    if slots[code]:
                        slots[code].pop(0)
            event_idx += 1

        if d not in sample_dates:
            continue

        cat_mv = defaultdict(float)
        held = 0
        for code, buy_navs in slots.items():
            if not buy_navs:
                continue
            dates, series = nav_cache.get(code, (None, None))
            if not dates:
                continue
            cur_nav = lookup_nav(dates, series, d)
            if not cur_nav:
                continue
            mv = sum(cur_nav / bn for bn in buy_navs if bn)
            cls = class_for(code)
            cat_mv[cls] += mv
            held += len(buy_navs)

        cash_at = max(0, total_unit - held) * 1.0
        cat_mv["现金"] = cash_at
        total_at = sum(cat_mv.values())
        if total_at <= 0:
            continue

        row = {"date": d, "nav": round(total_at / total_unit, 4)}
        for cls in ASSET_CLASS_ORDER:
            row[cls] = round(cat_mv.get(cls, 0) / total_at * 100, 2)
        timeseries.append(row)

    return timeseries


def build_for_plan(data_path, plan_id, funds_meta):
    with open(data_path) as f:
        data = json.load(f)
    holdings = data.get("holdings", [])
    total_unit = data.get("totalUnit") or 150

    nav_cache = {h["fundCode"]: load_nav(h["fundCode"]) for h in holdings}

    current = build_current_snapshot(holdings, total_unit, nav_cache, funds_meta, plan_id)
    timeseries = build_timeseries(holdings, total_unit, nav_cache)

    return {
        "current": current,
        "timeseries": timeseries,
        "updatedAt": data.get("updatedAt", ""),
    }


def tag_data_with_l1(data_path):
    """Inject `l1` field into data[*].recentSignals and data.holdings[].history.
    Idempotent — overwrites any existing l1 each run.
    Skips silently if file missing."""
    if not os.path.exists(data_path):
        return 0
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    n = 0
    for sig in data.get("recentSignals") or []:
        code = sig.get("fundCode")
        if code:
            sig["l1"] = class_for(code)
            n += 1
    for h in data.get("holdings") or []:
        code = h.get("fundCode")
        l1 = class_for(code) if code else "其他"
        h["l1"] = l1
        for ev in h.get("history") or []:
            ev["l1"] = l1
            n += 1

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return n


def save_daily_snapshot():
    """Copy docs/qieman_allocation.json → docs/allocation-daily/{YYYY-MM-DD}.json
    using Asia/Shanghai date. Idempotent — same-day reruns overwrite."""
    src = "docs/qieman_allocation.json"
    if not os.path.exists(src):
        print("[allocation-daily] qieman_allocation.json missing, skip", file=sys.stderr)
        return None
    today = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    out_dir = "docs/allocation-daily"
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"{today}.json")
    shutil.copyfile(src, dst)
    return dst


def main():
    funds_meta = {}
    if os.path.exists("docs/funds.json"):
        with open("docs/funds.json") as f:
            funds_meta = json.load(f).get("funds", {})

    out = {
        "generatedAt": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "asset_classes": [
            {"key": cls, "label": cls, "color": ASSET_COLORS[cls]}
            for cls in ASSET_CLASS_ORDER
        ],
        "plans": {},
    }

    plans = [("150", "docs/data.json"), ("s", "docs/data_s.json")]
    for plan_id, path in plans:
        if not os.path.exists(path):
            print(f"[allocation] {plan_id}: {path} missing, skip", file=sys.stderr)
            continue
        print(f"[allocation] processing {plan_id}...")
        result = build_for_plan(path, plan_id, funds_meta)
        out["plans"][plan_id] = result
        cur = result["current"]
        ts = result["timeseries"]
        print(f"[allocation] {plan_id}: invested={cur['invested_unit']}/{cur['total_unit']}, "
              f"categories={len(cur['by_category'])}, timeseries={len(ts)} pts")
        # Verification per category
        for c in cur["by_category"]:
            print(f"  {c['label']:<14s} {c['units']:>3d}份  占比 {c['share_pct']:>5.2f}%  "
                  f"累计 {c['return_pct'] if c['return_pct'] is not None else '-':>6}")

    with open("docs/allocation.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[allocation] saved docs/allocation.json")

    # Tag signals/history with L1 (uses qieman → taxonomy fallback)
    for plan_id, path in plans:
        n = tag_data_with_l1(path)
        if n:
            print(f"[allocation] {plan_id}: tagged {n} signal/history entries with l1")

    # Daily snapshot of qieman authoritative composition (CN date)
    snap = save_daily_snapshot()
    if snap:
        print(f"[allocation] daily snapshot saved: {snap}")


if __name__ == "__main__":
    main()
