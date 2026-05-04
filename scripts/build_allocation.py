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
import sys
from collections import defaultdict
from datetime import datetime

# ── Asset class taxonomy ──────────────────────────────────────────────────
ASSET_CLASS_ORDER = [
    "a_stock",
    "overseas_emerging",
    "overseas_developed",
    "domestic_bond",
    "overseas_bond",
    "gold",
    "oil",
    "cash",
    "other",
]

ASSET_LABELS = {
    "a_stock": "A股",
    "overseas_emerging": "海外新兴市场股票",
    "overseas_developed": "海外成熟市场股票",
    "domestic_bond": "境内债券",
    "overseas_bond": "海外债券",
    "gold": "黄金",
    "oil": "原油",
    "cash": "现金",
    "other": "其他",
}

ASSET_COLORS = {
    "a_stock": "#ff7875",
    "overseas_emerging": "#95de64",
    "overseas_developed": "#5b8def",
    "domestic_bond": "#b37feb",
    "overseas_bond": "#ff9c6e",
    "gold": "#ffc53d",
    "oil": "#69c0ff",
    "cash": "#bfbfbf",
    "other": "#595959",
}

# Per-user provided categorization (150 计划) + S-only inferred
FUND_CLASS = {
    # ── A股 ──
    "100032": "a_stock",  # 富国中证红利指数增强A
    "100038": "a_stock",  # 富国沪深300指数增强A
    "001180": "a_stock",  # 广发医药卫生联接A (全指医药)
    "001052": "a_stock",  # 华夏中证500ETF联接A
    "000968": "a_stock",  # 广发养老指数A
    "000478": "a_stock",  # 建信中证500指数增强A
    "012323": "a_stock",  # 华宝医疗ETF联接C
    "002708": "a_stock",  # 大摩健康产业混合A
    "001051": "a_stock",  # 华夏上证50ETF联接A
    "519915": "a_stock",  # 富国消费主题混合A
    "011309": "a_stock",  # 富国消费主题混合C
    "162412": "a_stock",  # 华宝医疗ETF联接A
    "000051": "a_stock",  # 华夏沪深300ETF联接A
    "021550": "a_stock",  # 博时中证红利低波动100ETF联接A
    "161017": "a_stock",  # 富国中证500指数增强A LOF
    "110022": "a_stock",  # 易方达消费行业
    "502010": "a_stock",  # 易方达中证全指证券公司指数LOF A
    "000248": "a_stock",  # 汇添富中证主要消费ETF联接A
    "001469": "a_stock",  # 广发中证全指金融地产联接A (全指金融)
    "000727": "a_stock",  # 融通健康产业灵活配置混合A
    "001064": "a_stock",  # 广发中证环保ETF联接A (已清仓)
    "110026": "a_stock",  # 易方达创业板ETF联接A (已清仓)
    "003765": "a_stock",  # 广发创业板ETF联接A (已清仓)
    "004752": "a_stock",  # 广发中证传媒ETF联接A (已清仓)
    "000942": "a_stock",  # 广发信息技术联接A (已清仓)
    "001513": "a_stock",  # 易方达信息产业混合A (已清仓)
    "005658": "a_stock",  # 华夏沪深300ETF联接C (已清仓)
    "002903": "a_stock",  # 广发中证500ETF联接C (已清仓)
    # S 计划 A 股独有
    "004424": "a_stock",  # 汇添富文体娱乐混合A
    "110020": "a_stock",  # 易方达沪深300ETF联接A
    "001552": "a_stock",  # 天弘中证证券保险A
    # ── 海外新兴市场股票 ──
    "000071": "overseas_emerging",  # 华夏恒生ETF联接A
    "012348": "overseas_emerging",  # 天弘恒生科技指数联接(QDII)A
    "164906": "overseas_emerging",  # 交银施罗德中证海外中国互联网指数(QDII-LOF)A
    "006327": "overseas_emerging",  # 易方达中证海外联接人民币A (中概互联)
    "014424": "overseas_emerging",  # 博时恒生医疗保健ETF发起式联接(QDII)A (已清仓)
    # ── 境内债券 ──
    "340001": "domestic_bond",  # 兴全可转债混合
    "110027": "domestic_bond",  # 易方达安心债券A (安心回报)
    "003376": "domestic_bond",  # 广发中债7-10年国开债指数A
    "007562": "domestic_bond",  # 景顺长城景泰纯利债券A
    "006484": "domestic_bond",  # 广发中债1-3年国开债A
    "050027": "domestic_bond",  # 博时信用债纯债债券A (已清仓)
    "270048": "domestic_bond",  # 广发纯债债券A (已清仓)
    "519977": "domestic_bond",  # 长信可转债债券A (已清仓)
    "000563": "domestic_bond",  # 南方通利A (已清仓)
    "000147": "domestic_bond",  # 易方达高等级信用债A (已清仓)
    # ── 海外债券 ──
    "100050": "overseas_bond",  # 富国全球债券(QDII)人民币A
    "004419": "overseas_bond",  # 汇添富美元债债券人民币A
    "019518": "overseas_bond",  # 富国全球债券(QDII)人民币C
    "002286": "overseas_bond",  # 中银美元债债券人民币A
    "001061": "overseas_bond",  # 华夏海外收益债券A (已清仓)
    # ── 海外成熟市场股票 ──
    "000369": "overseas_developed",  # 广发全球医疗保健(QDII)
    "050025": "overseas_developed",  # 博时标普500ETF联接A
    "000614": "overseas_developed",  # 华安德国(DAX)ETF联接A (已清仓)
    "001092": "overseas_developed",  # 广发生物科技指数(QDII)A (已清仓)
    "270042": "overseas_developed",  # 广发纳指100ETF联接(QDII)A (已清仓)
    # S 计划 海外成熟独有
    "019524": "overseas_developed",  # 华泰柏瑞纳斯达克100ETF联接(QDII)A
    # ── 原油 ──
    "160416": "oil",  # 华安标普全球石油指数(LOF)A (已清仓)
    "162411": "oil",  # 华宝标普油气上游股票人民币(LOF)A (已清仓)
    "501018": "oil",  # 南方原油A (已清仓)
    # ── 黄金 ──
    "000216": "gold",  # 华安黄金ETF联接A (已清仓)
}


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
        cls = FUND_CLASS.get(code, "other")
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
        if cls == "cash":
            by_category.append({
                "class": cls,
                "label": ASSET_LABELS[cls],
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
            "label": ASSET_LABELS[cls],
            "color": ASSET_COLORS[cls],
            "units": d["units"],
            "share_pct": round(d["mv"] / total_mv * 100, 2),
            "return_pct": round(cat_ret, 2),
            "funds": funds_active + funds_cleared,
        })

    return {
        "total_unit": total_unit,
        "invested_unit": held_units,
        "cash_unit": cash_units,
        "by_category": by_category,
    }


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
            cls = FUND_CLASS.get(code, "other")
            cat_mv[cls] += mv
            held += len(buy_navs)

        cash_at = max(0, total_unit - held) * 1.0
        cat_mv["cash"] = cash_at
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


def main():
    funds_meta = {}
    if os.path.exists("docs/funds.json"):
        with open("docs/funds.json") as f:
            funds_meta = json.load(f).get("funds", {})

    out = {
        "generatedAt": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "asset_classes": [
            {"key": cls, "label": ASSET_LABELS[cls], "color": ASSET_COLORS[cls]}
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


if __name__ == "__main__":
    main()
