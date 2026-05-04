"""应用 strategy_framework 到历史交易和当前持仓.

读取 docs/funds.json (聚合元数据 + trips + return), 避免重复计算.

Usage:
    python scripts/predict_signals.py        # both plans
    python scripts/predict_signals.py 150    # 150份计划 only
    python scripts/predict_signals.py s      # S计划 only

输出:
  docs/strategy.json    - 150份计划
  docs/strategy_s.json  - S计划
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_framework import (
    evaluate_sell, evaluate_sell_for_tagging,
    evaluate_buy, predict_next_action,
    framework_summary, TradeContext,
)

FUNDS_PATH = "docs/funds.json"
DATA_PATHS = {"150": "docs/data.json", "s": "docs/data_s.json"}

PLANS = {
    "150": {"output": "docs/strategy.json",   "label": "150份计划"},
    "s":   {"output": "docs/strategy_s.json", "label": "S计划"},
}


def load_funds() -> Dict[str, dict]:
    """Load aggregated funds metadata."""
    if not os.path.exists(FUNDS_PATH):
        raise FileNotFoundError(f"{FUNDS_PATH} not found — run scripts/build_funds_json.py first")
    with open(FUNDS_PATH, encoding="utf-8") as f:
        return json.load(f)["funds"]


def market_rally_months_from_funds(funds: dict, target_date: str) -> Optional[int]:
    """估算大盘最近 N 个月连涨, 用 funds.json 中宽基代理 NAV 简化判断.

    简化逻辑: 我们没存完整 NAV history 在 funds.json, 直接用一个保守的固定 0.
    完整 rally detection 需读 nav/{code}.json.
    """
    return 0  # safe default; 历史信号 still get tagged via 收益率 rules


def tag_trades_for_plan(funds: dict, plan: str) -> List[dict]:
    """对历史每笔卖出做策略标签 — 基于 funds.json 的 trips."""
    tagged = []
    for code, f in funds.items():
        plans = f.get("plans", {})
        if plan not in plans:
            continue
        trips = plans[plan].get("trips", [])
        category = f.get("category", "unknown")
        name = f.get("fundName", "")

        sell_streak = 0
        for trip in trips:
            if trip.get("status") != "closed":
                # held trip resets streak
                sell_streak = 0
                continue

            sell_date = trip.get("sell_date")
            buy_date = trip.get("buy_date")
            if not sell_date or not buy_date:
                continue

            ret_pct = trip.get("trip_return_pct") or 0
            try:
                hold_days = (datetime.strptime(sell_date, "%Y-%m-%d")
                             - datetime.strptime(buy_date, "%Y-%m-%d")).days
            except ValueError:
                hold_days = 0

            sell_streak += 1
            ctx = TradeContext(
                fund_name=name,
                category=category,
                return_pct=ret_pct,
                hold_days=hold_days,
                pos_units=1,
                drawdown_from_peak_pct=0,
                market_recent_rally_months=None,
                sell_streak=sell_streak,
            )
            matched = evaluate_sell_for_tagging(ctx)
            tags = [{"rule_id": r.id, "rule_name": r.name, "confidence": round(c, 2)}
                    for r, c in matched]

            tagged.append({
                "date":        sell_date,
                "fund":        name,
                "code":        code,
                "category":    category,
                "action":      "sell",
                "unit":        1,
                "buy_date":    buy_date,
                "buy_adj":     trip.get("buy_adj"),
                "sell_adj":    trip.get("sell_adj"),
                "return_pct":  round(ret_pct, 1),
                "hold_days":   hold_days,
                "tags":        tags,
                "primary_tag": tags[0]["rule_id"] if tags else "UNTAGGED",
                "sell_streak": sell_streak,
            })

    tagged.sort(key=lambda x: x["date"])
    return tagged


def predict_for_plan(funds: dict, plan: str) -> List[dict]:
    """对当前持仓做下一步预测."""
    today = datetime.today()
    predictions = []

    for code, f in funds.items():
        plans = f.get("plans", {})
        if plan not in plans:
            continue
        p = plans[plan]
        units = p.get("currentUnits", 0)
        if units <= 0:
            continue

        category = f.get("category", "unknown")
        name = f.get("fundName", "")

        # 用 avg_compound 作为主收益率指标 (与且慢匹配率最高)
        ret_pct = p.get("return_avg_compound") or p.get("return_m4_irr") or 0

        first_buy = p.get("first_buy")
        last_buy = p.get("last_buy")
        last_sell = p.get("last_sell")

        hold_days = (today - datetime.strptime(first_buy, "%Y-%m-%d")).days if first_buy else None
        days_since_buy = (today - datetime.strptime(last_buy, "%Y-%m-%d")).days if last_buy else None
        days_since_sell = (today - datetime.strptime(last_sell, "%Y-%m-%d")).days if last_sell else None

        ctx = TradeContext(
            fund_name=name,
            category=category,
            return_pct=ret_pct,
            hold_days=hold_days,
            pos_units=units,
            days_since_last_buy=days_since_buy,
            days_since_last_sell=days_since_sell,
            market_recent_rally_months=None,
        )
        pred = predict_next_action(ctx)

        # held shares' avg buy adj (for display)
        held_trips = [t for t in p.get("trips", []) if t.get("status") == "held"]
        avg_buy_adj = (sum(t.get("buy_adj", 0) for t in held_trips) / len(held_trips)) if held_trips else 0

        predictions.append({
            "fund":             name,
            "code":             code,
            "category":         category,
            "category_label":   f.get("category_label", ""),
            "data_quality":     f.get("data_quality", "complete"),
            "units":            units,
            "avg_buy_nav":      round(avg_buy_adj, 4),
            "current_nav":      f.get("current_unit_nav"),
            "current_adj_nav":  f.get("current_adj_nav"),
            "return_pct":       round(ret_pct, 1),
            "return_m4_irr":    p.get("return_m4_irr"),
            "hold_days":        hold_days,
            "days_since_buy":   days_since_buy,
            "days_since_sell":  days_since_sell,
            "primary_signal":   pred["primary_signal"],
            "all_signals":      pred["all_signals"],
            "next_threshold":   pred["next_threshold"],
        })

    def sort_key(p):
        sig = p["primary_signal"]
        action_priority = {"sell": 2, "buy": 1, "hold": 0}.get(sig.get("action", "hold"), 0)
        return (-action_priority, -sig.get("confidence", 0))
    predictions.sort(key=sort_key)
    return predictions


def predict_one_plan(plan_key: str, output_path: str, label: str, funds: dict, framework: dict):
    # Verify plan exists in funds data
    has_plan = any(plan_key in f.get("plans", {}) for f in funds.values())
    if not has_plan:
        print(f"[strategy:{label}] no '{plan_key}' data in funds.json, skipping")
        return

    tagged = tag_trades_for_plan(funds, plan_key)
    predictions = predict_for_plan(funds, plan_key)

    tag_counts = Counter(t["primary_tag"] for t in tagged)
    signal_counts = Counter(
        p["primary_signal"]["rule_id"] if "rule_id" in p["primary_signal"] else "HOLD"
        for p in predictions
    )

    # Pull plan metadata from data.json (totalUnit/poName)
    data_path = DATA_PATHS.get(plan_key, "")
    po_name, total_unit = None, None
    if os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as f:
            d = json.load(f)
        po_name = d.get("poName")
        total_unit = d.get("totalUnit")

    output = {
        "updatedAt":     datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plan":          label,
        "poName":        po_name,
        "totalUnit":     total_unit,
        "return_method": "avg_compound (geometric mean of FIFO trip returns)",
        "framework":     framework,
        "tagged_trades": tagged,
        "predictions":   predictions,
        "stats": {
            "total_tagged":        len(tagged),
            "tag_distribution":    dict(tag_counts),
            "signal_distribution": dict(signal_counts),
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"[strategy:{label}] tagged={len(tagged)} positions={len(predictions)} → {output_path}")


def main():
    funds = load_funds()
    framework = framework_summary()
    plan_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if plan_arg in PLANS:
        plan_keys = [plan_arg]
    else:
        plan_keys = list(PLANS.keys())

    for k in plan_keys:
        predict_one_plan(k, PLANS[k]["output"], PLANS[k]["label"], funds, framework)


if __name__ == "__main__":
    main()
