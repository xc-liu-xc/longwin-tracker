"""Fetch authoritative composition + L1 timeseries from qieman.

Two endpoints:
  1. GET /pmdj/v2/long-win/plan?prodCode={LONG_WIN|LONG_WIN_S}
     → current composition: per-L1 + per-fund percent / units / accProfit
  2. GET /pmdj/v2/long-win/plan/clz-distribution?poCode=LONG_WIN
     → historical L1 timeseries (only LONG_WIN; S not supported by this endpoint)

Output: docs/qieman_allocation.json
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

PLAN_URL = "https://qieman.com/pmdj/v2/long-win/plan"
CLZ_URL  = "https://qieman.com/pmdj/v2/long-win/plan/clz-distribution"
NAV_URL  = "https://qieman.com/pmdj/v2/long-win/plan/nav-history"
TZ_CN = timezone(timedelta(hours=8))


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


_load_env()


def gen_x_sign():
    ts = int(time.time() * 1000)
    raw = str(int(1.01 * ts))
    h = hashlib.sha256(raw.encode()).hexdigest().upper()[:32]
    return str(ts) + h


# qieman classCode → our L1 (8-class scheme; some merge)
CLASS_TO_L1 = {
    "CHINA_STOCK":            "A股",
    "OVERSEA_STOCK_EMERGING": "海外新兴",
    "OVERSEA_STOCK_MATURE":   "海外成熟",
    "GOLD":                   "商品",
    "OIL":                    "商品",
    "CHINA_BOND":             "债券",
    "OVERSEA_BOND":           "债券",
    "CASH":                   "现金",
}
# qieman classCode → our L2 within shared L1 (商品/债券)
CLASS_TO_L2 = {
    "GOLD":         "黄金",
    "OIL":          "原油",
    "CHINA_BOND":   "国内债券",
    "OVERSEA_BOND": "海外债券",
}

L1_ORDER = ["A股", "海外新兴", "海外成熟", "商品", "债券", "房地产", "外汇", "现金"]


def headers_for(po_code: str) -> dict:
    token = os.environ.get("QIEMAN_TOKEN")
    if not token:
        raise ValueError("QIEMAN_TOKEN env not set")
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}" if not token.startswith("Bearer") else token,
        "Cookie": f"access_token={token}",
        "Referer": f"https://qieman.com/longwin/compositions/{po_code}",
        "Origin": "https://qieman.com",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "x-broker": "0008",
        "x-sign": gen_x_sign(),
    }


def fetch_plan_composition(po_code: str) -> dict:
    """Returns full plan composition (per-L1 + per-fund detail)."""
    resp = requests.get(PLAN_URL, params={"prodCode": po_code},
                        headers=headers_for(po_code), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_clz_timeseries() -> list:
    """Historical L1 distribution (LONG_WIN only)."""
    resp = requests.get(CLZ_URL, params={"poCode": "LONG_WIN"},
                        headers=headers_for("LONG_WIN"), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_nav_history(po_code: str) -> list:
    """Portfolio NAV history (both LONG_WIN and LONG_WIN_S)."""
    resp = requests.get(NAV_URL, params={"prodCode": po_code},
                        headers=headers_for(po_code), timeout=30)
    resp.raise_for_status()
    return resp.json()


def transform_nav_history(raw: list) -> list:
    out = []
    for entry in raw:
        d = datetime.fromtimestamp(entry["navDate"] / 1000, tz=TZ_CN).strftime("%Y-%m-%d")
        out.append({
            "date": d,
            "nav": entry["nav"],
            "dailyReturn": entry.get("dailyReturn"),
        })
    return out


def transform_composition(raw: dict) -> dict:
    """Convert qieman /plan response → our schema with by_l1 + funds."""
    composition = raw.get("composition", [])
    by_l1 = {l1: {"percent": 0.0, "units": 0, "accProfitRate": None, "funds": []}
             for l1 in L1_ORDER}
    for cls in composition:
        code = cls["classCode"]
        l1 = CLASS_TO_L1.get(code, "其他")
        b = by_l1.setdefault(l1, {"percent": 0.0, "units": 0, "accProfitRate": None, "funds": []})
        b["percent"] += round((cls.get("percent") or 0) * 100, 4)
        b["units"]   += cls.get("unit") or 0
        # accProfitRate: aggregate manually if multiple classCodes feed into one L1
        if cls.get("accProfitRate") is not None:
            # weighted by percent — simpler: just record the qieman value of dominant child
            if b["accProfitRate"] is None:
                b["accProfitRate"] = round(cls["accProfitRate"] * 100, 2)
            else:
                # for merged buckets (e.g. 商品 = GOLD + OIL), take simple average
                b["accProfitRate"] = round((b["accProfitRate"] + cls["accProfitRate"] * 100) / 2, 2)

        for f in cls.get("compList", []):
            if f.get("isCash"):
                # Pure cash bucket has no fund detail — already counted in CASH L1
                continue
            l2 = CLASS_TO_L2.get(code)  # only set for 商品/债券 sub-routing
            b["funds"].append({
                "code":          f["fund"]["fundCode"],
                "name":          f["fund"]["fundName"],
                "variety":       f.get("variety"),  # 且慢简称
                "units":         f.get("planUnit") or 0,
                "percent":       round((f.get("percent") or 0) * 100, 4),
                "accProfit":     round((f.get("accProfit") or 0) * 100, 2),
                "holdingProfit": round((f.get("holdingProfitRate") or 0) * 100, 2)
                                  if f.get("holdingProfitRate") is not None else None,
                "qieman_classCode": code,
                "l2_hint":       l2,  # for 商品/债券 sub-grouping
                "nav":           f.get("nav"),
                "navDate":       f["fund"].get("navDate"),
                "strategyType":  f.get("strategyType"),
            })

    return {
        "by_l1":           by_l1,
        "investedUnit":    raw.get("investedUnit"),
        "tradeLimit":      raw.get("tradeLimit"),
        "nav":             raw.get("nav"),
        "navDate":         datetime.fromtimestamp(raw.get("navDate", 0) / 1000, tz=TZ_CN).strftime("%Y-%m-%d") if raw.get("navDate") else None,
        "dailyReturn":     round((raw.get("dailyReturn") or 0) * 100, 4),
        "fromSetupReturn": round((raw.get("fromSetupReturn") or 0) * 100, 2),
        "annualReturn":    round((raw.get("annualCompoundedReturn") or 0) * 100, 2),
        "maxDrawdown":     round((raw.get("maxDrawdown") or 0) * 100, 2),
        "sharpe":          raw.get("sharpe"),
        "adjustedCount":   raw.get("adjustedCount"),
    }


def transform_timeseries(raw: list) -> list:
    out = []
    for entry in raw:
        d = datetime.fromtimestamp(entry["compDate"] / 1000, tz=TZ_CN).strftime("%Y-%m-%d")
        merged = {l1: 0.0 for l1 in L1_ORDER}
        for qkey, val in entry["distribution"].items():
            l1 = CLASS_TO_L1.get(qkey)
            if l1:
                merged[l1] += val
        row = {"date": d}
        for l1 in L1_ORDER:
            row[l1] = round(merged[l1] * 100, 4)
        out.append(row)
    return out


def main():
    out = {
        "generatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
        "source": "qieman.com /pmdj/v2/long-win/plan + clz-distribution",
        "note": "Authoritative percentages and per-fund 累计收益 from 且慢. L1 timeseries 150 only.",
        "plans": {},
    }

    plans = {"150": "LONG_WIN", "s": "LONG_WIN_S"}
    for plan_id, po_code in plans.items():
        try:
            print(f"[qieman_alloc] fetching {po_code} composition...")
            raw_comp = fetch_plan_composition(po_code)
            comp = transform_composition(raw_comp)
            out["plans"][plan_id] = {"composition": comp}
            l1s = comp["by_l1"]
            print(f"  invested={comp['investedUnit']} nav={comp['nav']} ({comp['navDate']}) "
                  f"setup_ret={comp['fromSetupReturn']}%")
            for l1 in L1_ORDER:
                p = l1s[l1]["percent"]
                if p > 0:
                    print(f"    {l1:14s} {l1s[l1]['units']:>3d}份  {p:>6.2f}%  累计 {l1s[l1]['accProfitRate']}%")
        except Exception as e:
            print(f"  [error] {po_code}: {e}", file=sys.stderr)
            out["plans"][plan_id] = {"error": str(e)}

    try:
        print(f"[qieman_alloc] fetching LONG_WIN timeseries...")
        ts_raw = fetch_clz_timeseries()
        ts = transform_timeseries(ts_raw)
        out["plans"].setdefault("150", {})["timeseries"] = ts
        print(f"  timeseries points: {len(ts)} ({ts[0]['date']} → {ts[-1]['date']})")
    except Exception as e:
        print(f"  [error] timeseries: {e}", file=sys.stderr)

    for plan_id, po_code in plans.items():
        try:
            print(f"[qieman_alloc] fetching {po_code} nav history...")
            nav_raw = fetch_nav_history(po_code)
            nav = transform_nav_history(nav_raw)
            out["plans"].setdefault(plan_id, {})["nav_history"] = nav
            print(f"  nav points: {len(nav)} ({nav[0]['date']} → {nav[-1]['date']})")
        except Exception as e:
            print(f"  [error] nav history {po_code}: {e}", file=sys.stderr)

    os.makedirs("docs", exist_ok=True)
    with open("docs/qieman_allocation.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[qieman_alloc] saved docs/qieman_allocation.json")


if __name__ == "__main__":
    main()
