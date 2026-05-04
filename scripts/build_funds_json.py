"""聚合每只基金的全量元数据 → docs/funds.json

整合来源:
  - docs/data.json + docs/data_s.json (持仓 + 历史交易)
  - docs/nav/{code}.json (NAV 历史)
  - tushare fund_basic / fund_div (元数据 + 分红记录)

输出:
  - docs/funds.json: 单文件聚合所有基金的元数据 + trip 数据 + return 计算

return 算法: avg_compound (几何平均 trip return), FIFO 匹配卖出
对部分基金会与且慢累计收益率有偏差, 已用 data_quality 字段标注
"""
import json
import os
import sys
import functools
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_framework import categorize_fund, CATEGORIES

NAV_DIR        = "docs/nav"
DATA_PATHS     = {"150": "docs/data.json", "s": "docs/data_s.json"}
OUTPUT_PATH    = "docs/funds.json"
TZ_CN          = timezone(timedelta(hours=8))


def _load_env():
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env):
        return
    with open(env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_nav(code: str) -> Optional[dict]:
    p = f"{NAV_DIR}/{code}.json"
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def nav_at(nav: dict, date_str: str, field: str = "adj"):
    if not nav or not nav.get("dates"):
        return None
    src_key = {"unit": "unitNav", "acc": "accNav", "adj": "adjNav"}[field]
    idx = {d: i for i, d in enumerate(nav["dates"])}
    if date_str in idx:
        return nav[src_key][idx[date_str]]
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for delta in range(1, 6):
        for d in [dt - timedelta(days=delta), dt + timedelta(days=delta)]:
            s = d.strftime("%Y-%m-%d")
            if s in idx:
                return nav[src_key][idx[s]]
    return None


def has_adj_data(nav: dict) -> bool:
    """检查 adj_nav 是否真的有复权处理 (vs 与 unit_nav 全等)."""
    if not nav or not nav.get("dates"):
        return False
    for i in range(len(nav["dates"])):
        if abs(nav["adjNav"][i] - nav["unitNav"][i]) > 0.001:
            return True
    return False


def build_trips(history: list, nav: dict) -> List[dict]:
    """按 FIFO 配对买卖, 输出 trips 列表 (closed + held)."""
    if not history or not nav:
        return []
    events = []
    for t in sorted(history, key=lambda x: x["date"]):
        for _ in range(int(t["unit"])):
            events.append({"date": t["date"], "action": t["action"]})

    open_slots = []  # [{open_date, open_unit, open_acc, open_adj}]
    closed = []
    for e in events:
        u = nav_at(nav, e["date"], "unit")
        a = nav_at(nav, e["date"], "acc")
        adj = nav_at(nav, e["date"], "adj")
        if u is None:
            continue
        if e["action"] == "buy":
            open_slots.append({"open_date": e["date"], "open_unit": u, "open_acc": a, "open_adj": adj})
        else:
            if open_slots:
                slot = open_slots.pop(0)  # FIFO
                ratio = adj / slot["open_adj"] if slot["open_adj"] else None
                ret_pct = (ratio - 1) * 100 if ratio else None
                closed.append({
                    "buy_date":   slot["open_date"],
                    "buy_unit":   slot["open_unit"],
                    "buy_adj":    slot["open_adj"],
                    "sell_date":  e["date"],
                    "sell_unit":  u,
                    "sell_adj":   adj,
                    "trip_return_pct": round(ret_pct, 2) if ret_pct is not None else None,
                    "status":     "closed",
                })

    cur_adj = nav["adjNav"][-1]
    cur_unit = nav["unitNav"][-1]
    held = []
    for s in open_slots:
        ratio = cur_adj / s["open_adj"] if s["open_adj"] else None
        ret_pct = (ratio - 1) * 100 if ratio else None
        held.append({
            "buy_date":         s["open_date"],
            "buy_unit":         s["open_unit"],
            "buy_adj":          s["open_adj"],
            "current_unit":     cur_unit,
            "current_adj":      cur_adj,
            "trip_return_pct":  round(ret_pct, 2) if ret_pct is not None else None,
            "status":           "held",
        })

    return closed + held


def avg_compound_return(trips: List[dict]) -> Optional[float]:
    """几何平均 trip return — 匹配且慢累计收益率."""
    valid = [t["trip_return_pct"] for t in trips if t.get("trip_return_pct") is not None]
    if not valid:
        return None
    factors = [(1 + r / 100) for r in valid]
    product = functools.reduce(lambda a, b: a * b, factors)
    geo = product ** (1 / len(factors))
    return round((geo - 1) * 100, 2)


def m4_full_irr(history: list, nav: dict) -> Optional[float]:
    """完整 IRR with adj_nav (作为对比指标保留)."""
    if not history or not nav:
        return None
    sum_buy = 0
    sum_sell = 0
    held = 0
    for t in sorted(history, key=lambda x: x["date"]):
        a = nav_at(nav, t["date"], "adj")
        if a is None:
            continue
        if t["action"] == "buy":
            sum_buy += a * t["unit"]
            held += t["unit"]
        else:
            sum_sell += a * t["unit"]
            held -= t["unit"]
    if held <= 0 or sum_buy <= 0:
        return None
    cur_adj = nav["adjNav"][-1]
    return round((held * cur_adj + sum_sell - sum_buy) / sum_buy * 100, 2)


def fetch_fund_meta(ts_code: str, pro) -> Dict[str, Any]:
    """fund_basic + fund_div counts."""
    meta = {}
    try:
        df = pro.fund_basic(
            ts_code=ts_code,
            fields="ts_code,name,management,fund_type,benchmark,m_fee,c_fee,p_value,status,invest_type,type,market,purc_startdate"
        )
        if df is not None and not df.empty:
            row = df.iloc[0]
            meta = {
                "name":           str(row.get("name") or ""),
                "management":     str(row.get("management") or ""),
                "fund_type":      str(row.get("fund_type") or ""),
                "invest_type":    str(row.get("invest_type") or ""),
                "type":           str(row.get("type") or ""),
                "market":         str(row.get("market") or ""),
                "benchmark":      str(row.get("benchmark") or ""),
                "m_fee":          float(row["m_fee"]) if row.get("m_fee") == row.get("m_fee") else None,
                "c_fee":          float(row["c_fee"]) if row.get("c_fee") == row.get("c_fee") else None,
                "p_value":        float(row["p_value"]) if row.get("p_value") == row.get("p_value") else None,
                "purc_startdate": str(row.get("purc_startdate") or ""),
                "status":         str(row.get("status") or ""),
            }
    except Exception as e:
        meta["fetch_error"] = str(e)[:80]

    try:
        df = pro.fund_div(ts_code=ts_code)
        meta["dividend_count"] = len(df) if df is not None else 0
    except Exception:
        meta["dividend_count"] = -1

    return meta


def aggregate_holdings():
    """把 150 + S 计划的 holdings 合并."""
    funds = {}
    for plan, path in DATA_PATHS.items():
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for h in data.get("holdings", []):
            code = h["fundCode"]
            if code not in funds:
                funds[code] = {
                    "fundCode": code,
                    "fundName": h["fundName"],
                    "history": {},
                }
            funds[code]["history"][plan] = h.get("history") or []
    return funds


def main():
    _load_env()
    use_tushare = bool(os.environ.get("TUSHARE_TOKEN"))
    pro = None
    if use_tushare:
        try:
            import tushare as ts
            ts.set_token(os.environ["TUSHARE_TOKEN"])
            pro = ts.pro_api()
        except Exception as e:
            print(f"[funds] tushare init failed: {e}")
            pro = None

    funds = aggregate_holdings()
    print(f"[funds] aggregating {len(funds)} unique funds across plans...")

    output = {}
    for code, f in funds.items():
        nav = load_nav(code)
        if not nav or not nav.get("dates"):
            print(f"  [skip] {code}: no NAV")
            continue

        category = categorize_fund(f["fundName"])
        cat_meta = CATEGORIES.get(category, {})

        # Per-plan metrics
        plans = {}
        for plan, history in f["history"].items():
            held = sum(t["unit"] for t in history if t["action"] == "buy") \
                   - sum(t["unit"] for t in history if t["action"] == "sell")
            buy_dates = sorted([t["date"] for t in history if t["action"] == "buy"])
            sell_dates = sorted([t["date"] for t in history if t["action"] == "sell"])
            trips = build_trips(history, nav)
            plans[plan] = {
                "currentUnits":   held,
                "totalBuys":      sum(t["unit"] for t in history if t["action"] == "buy"),
                "totalSells":     sum(t["unit"] for t in history if t["action"] == "sell"),
                "first_buy":      buy_dates[0] if buy_dates else None,
                "last_buy":       buy_dates[-1] if buy_dates else None,
                "last_sell":      sell_dates[-1] if sell_dates else None,
                "trips":          trips,
                "return_avg_compound": avg_compound_return(trips),
                "return_m4_irr":  m4_full_irr(history, nav),
            }

        # Data quality assessment
        adj_present = has_adj_data(nav)
        if adj_present:
            data_quality = "complete"
        else:
            data_quality = "unit_only"  # adj=unit, may underestimate dividend funds

        record = {
            "fundCode":          code,
            "fundName":          f["fundName"],
            "tsCode":            nav.get("tsCode"),
            "category":          category,
            "category_label":    cat_meta.get("label", "其他"),
            "data_quality":      data_quality,
            "has_adj_data":      adj_present,
            "current_unit_nav":  nav["unitNav"][-1],
            "current_acc_nav":   nav["accNav"][-1],
            "current_adj_nav":   nav["adjNav"][-1],
            "current_nav_date":  nav["dates"][-1],
            "nav_history_start": nav["dates"][0],
            "nav_history_end":   nav["dates"][-1],
            "nav_count":         len(nav["dates"]),
            "plans":             plans,
        }

        # Optional: tushare meta (only fetch if pro available)
        if pro and nav.get("tsCode"):
            meta = fetch_fund_meta(nav["tsCode"], pro)
            record.update({
                "tushare_meta": meta,
                "dividend_count": meta.get("dividend_count", 0),
            })
            import time
            time.sleep(0.4)  # rate limit

        output[code] = record

    result = {
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
        "method":    "avg_compound (geometric mean of FIFO trip returns)",
        "funds":     output,
        "summary": {
            "total":          len(output),
            "data_complete":  sum(1 for r in output.values() if r["data_quality"] == "complete"),
            "data_unit_only": sum(1 for r in output.values() if r["data_quality"] == "unit_only"),
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    s = result["summary"]
    print(f"[funds] {s['total']} funds: {s['data_complete']} complete + {s['data_unit_only']} unit-only")
    print(f"[funds] → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
