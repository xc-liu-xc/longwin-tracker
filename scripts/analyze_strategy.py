"""Analyze E大 LongWin trading strategy → docs/analysis.json (or analysis_s.json).

Reads docs/funds.json (aggregated metadata + trips + return) for consistency
with predict_signals.py. Avoids re-computing buy/sell/return logic.

Usage:
    python scripts/analyze_strategy.py              # both plans
    python scripts/analyze_strategy.py 150          # 150份计划 only
    python scripts/analyze_strategy.py s            # S计划 only
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

FUNDS_PATH = "docs/funds.json"
DATA_PATHS = {"150": "docs/data.json", "s": "docs/data_s.json"}

PLANS = {
    "150": {"output": "docs/analysis.json",   "label": "150份计划"},
    "s":   {"output": "docs/analysis_s.json", "label": "S计划"},
}


def load_funds() -> Dict[str, dict]:
    if not os.path.exists(FUNDS_PATH):
        raise FileNotFoundError(f"{FUNDS_PATH} not found — run scripts/build_funds_json.py first")
    with open(FUNDS_PATH, encoding="utf-8") as f:
        return json.load(f)["funds"]


def collect_sell_trades(funds: dict, plan: str) -> List[dict]:
    """从 funds.json 的 closed trips 提取所有卖出记录."""
    trades = []
    for code, f in funds.items():
        plans = f.get("plans", {})
        if plan not in plans:
            continue
        name = f.get("fundName", "")
        category = f.get("category", "unknown")
        category_label = f.get("category_label", "")
        for trip in plans[plan].get("trips", []):
            if trip.get("status") != "closed":
                continue
            sell_date = trip.get("sell_date")
            buy_date = trip.get("buy_date")
            if not sell_date or not buy_date:
                continue
            try:
                hold_days = (datetime.strptime(sell_date, "%Y-%m-%d")
                             - datetime.strptime(buy_date, "%Y-%m-%d")).days
            except ValueError:
                hold_days = 0
            trades.append({
                "fund":            name,
                "code":            code,
                "category":        category,
                "category_label":  category_label,
                "sell_date":       sell_date,
                "buy_date":        buy_date,
                "return_pct":      trip.get("trip_return_pct") or 0,
                "hold_days":       hold_days,
                "sell_nav":        trip.get("sell_unit"),
                "buy_nav":         trip.get("buy_unit"),
                "sell_adj":        trip.get("sell_adj"),
                "buy_adj":         trip.get("buy_adj"),
            })
    trades.sort(key=lambda x: x["sell_date"])
    return trades


def collect_positions(funds: dict, plan: str) -> List[dict]:
    """从 funds.json 提取当前持仓."""
    positions = []
    today = datetime.today()
    for code, f in funds.items():
        plans = f.get("plans", {})
        if plan not in plans:
            continue
        p = plans[plan]
        units = p.get("currentUnits", 0)
        if units <= 0:
            continue

        # held trips' avg buy adj for cost basis
        held_trips = [t for t in p.get("trips", []) if t.get("status") == "held"]
        avg_buy_adj = (sum(t.get("buy_adj", 0) for t in held_trips) / len(held_trips)) if held_trips else 0
        cur_adj = f.get("current_adj_nav") or 0
        cost = units * avg_buy_adj
        value = units * cur_adj
        ret_pct = p.get("return_avg_compound") or p.get("return_m4_irr") or 0

        first_buy = p.get("first_buy")
        hold_days = (today - datetime.strptime(first_buy, "%Y-%m-%d")).days if first_buy else None

        positions.append({
            "fund":           f.get("fundName", ""),
            "code":           code,
            "category":       f.get("category", "unknown"),
            "category_label": f.get("category_label", ""),
            "data_quality":   f.get("data_quality", "complete"),
            "units":          units,
            "avg_buy_nav":    round(avg_buy_adj, 4),
            "current_nav":    cur_adj,
            "cost":           round(cost, 4),
            "value":          round(value, 4),
            "return_pct":     round(ret_pct, 2),
            "hold_days":      hold_days,
            "first_buy":      first_buy,
        })
    return positions


def compute_monthly_activity(plan: str) -> List[dict]:
    """从 data.json 算月度交易频次."""
    monthly = defaultdict(lambda: {"buy": 0, "sell": 0})
    path = DATA_PATHS.get(plan)
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for h in data.get("holdings", []):
        for t in (h.get("history") or []):
            month = t.get("date", "")[:7]
            action = t.get("action", "")
            if action in ("buy", "sell") and month:
                monthly[month][action] += 1
    return [{"month": m, "buy": v["buy"], "sell": v["sell"]}
            for m, v in sorted(monthly.items())]


def analyze_one_plan(plan_key: str, output_path: str, label: str, funds: dict):
    has_plan = any(plan_key in f.get("plans", {}) for f in funds.values())
    if not has_plan:
        print(f"[analysis:{label}] no '{plan_key}' data in funds.json, skipping")
        return

    sell_trades = collect_sell_trades(funds, plan_key)
    positions   = collect_positions(funds, plan_key)
    monthly     = compute_monthly_activity(plan_key)

    returns = [t["return_pct"] for t in sell_trades]
    hold_days_list = [t["hold_days"] for t in sell_trades if t["hold_days"] >= 0]

    total_cost  = sum(p["cost"] for p in positions)
    total_value = sum(p["value"] for p in positions)

    # Plan metadata from data.json
    po_name, total_unit = None, None
    data_path = DATA_PATHS.get(plan_key)
    if data_path and os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as f:
            d = json.load(f)
        po_name = d.get("poName")
        total_unit = d.get("totalUnit")

    result = {
        "updatedAt":     datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plan":          label,
        "poName":        po_name,
        "totalUnit":     total_unit,
        "return_method": "avg_compound (geometric mean of FIFO trip returns)",
        "summary": {
            "total_sell_trades":     len(sell_trades),
            "win_rate_pct":          round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1) if returns else 0,
            "avg_return_pct":        round(sum(returns) / len(returns), 1) if returns else 0,
            "median_return_pct":     round(sorted(returns)[len(returns) // 2], 1) if returns else 0,
            "avg_hold_days":         round(sum(hold_days_list) / len(hold_days_list)) if hold_days_list else 0,
            "current_positions":     len(positions),
            "current_cost":          round(total_cost, 2),
            "current_value":         round(total_value, 2),
            "unrealized_return_pct": round((total_value - total_cost) / total_cost * 100, 1) if total_cost > 0 else 0,
        },
        "sell_trades":      sell_trades,
        "positions":        sorted(positions, key=lambda p: -(p["return_pct"] or 0)),
        "monthly_activity": monthly,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    s = result["summary"]
    print(f"[analysis:{label}] {s['total_sell_trades']} trades | "
          f"win={s['win_rate_pct']}% | avg={s['avg_return_pct']}% | "
          f"positions={s['current_positions']} | unrealized={s['unrealized_return_pct']}%")
    print(f"[analysis:{label}] → {output_path}")


def main():
    funds = load_funds()
    plan_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if plan_arg in PLANS:
        plan_keys = [plan_arg]
    else:
        plan_keys = list(PLANS.keys())

    for k in plan_keys:
        analyze_one_plan(k, PLANS[k]["output"], PLANS[k]["label"], funds)


if __name__ == "__main__":
    main()
