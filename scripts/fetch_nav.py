import tushare as ts
import json
import os
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
if TUSHARE_TOKEN:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
else:
    pro = None

NAV_DIR = "docs/nav"


def load_existing_nav(fund_code):
    path = os.path.join(NAV_DIR, f"{fund_code}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_nav(fund_code, nav_data):
    os.makedirs(NAV_DIR, exist_ok=True)
    path = os.path.join(NAV_DIR, f"{fund_code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nav_data, f, ensure_ascii=False)


def fetch_fund_nav(fund_code):
    if not pro:
        raise ValueError("TUSHARE_TOKEN environment variable not set")

    ts_code = f"{fund_code}.OF"
    existing = load_existing_nav(fund_code)

    if existing and existing.get("dates"):
        last_date = max(existing["dates"])
        start_date = last_date.replace("-", "")
    else:
        start_date = "20150101"

    now_cn = datetime.now(TZ_CN)
    end_date = now_cn.strftime("%Y%m%d")

    try:
        df = pro.fund_nav(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,nav_date,unit_nav,accum_nav",
        )
    except Exception as e:
        print(f"[nav] error fetching {fund_code}: {e}")
        return False

    if df is None or df.empty:
        if existing:
            print(f"[nav] {fund_code}: no new data")
            return True
        print(f"[nav] {fund_code}: no data at all")
        return False

    df = df.sort_values("nav_date")

    new_dates = []
    new_unit = []
    new_acc = []

    for _, row in df.iterrows():
        d = row["nav_date"]
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        new_dates.append(date_str)
        new_unit.append(float(row["unit_nav"]))
        new_acc.append(float(row["accum_nav"]))

    if existing and existing.get("dates"):
        existing_set = set(existing["dates"])
        merged_dates = list(existing["dates"])
        merged_unit = list(existing["unitNav"])
        merged_acc = list(existing["accNav"])

        for i, d in enumerate(new_dates):
            if d not in existing_set:
                merged_dates.append(d)
                merged_unit.append(new_unit[i])
                merged_acc.append(new_acc[i])

        pairs = sorted(zip(merged_dates, merged_unit, merged_acc), key=lambda x: x[0])
        merged_dates = [p[0] for p in pairs]
        merged_unit = [p[1] for p in pairs]
        merged_acc = [p[2] for p in pairs]
    else:
        merged_dates = new_dates
        merged_unit = new_unit
        merged_acc = new_acc

    nav_data = {
        "fundCode": fund_code,
        "tsCode": ts_code,
        "dates": merged_dates,
        "unitNav": merged_unit,
        "accNav": merged_acc,
        "current": {
            "unitNav": merged_unit[-1] if merged_unit else None,
            "accNav": merged_acc[-1] if merged_acc else None,
            "date": merged_dates[-1] if merged_dates else None,
        },
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
    }

    save_nav(fund_code, nav_data)
    added = len(merged_dates) - (len(existing["dates"]) if existing and existing.get("dates") else 0)
    print(f"[nav] {fund_code}: {len(merged_dates)} total, +{added} new")
    return True


def main():
    data_path = "docs/data.json"
    if not os.path.exists(data_path):
        print("[nav] docs/data.json not found, run fetch_signals.py first")
        return

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    holdings = data.get("holdings", [])
    if not holdings:
        print("[nav] no holdings found")
        return

    fund_codes = [h["fundCode"] for h in holdings]
    print(f"[nav] fetching NAV for {len(fund_codes)} funds...")

    success = 0
    for code in fund_codes:
        if fetch_fund_nav(code):
            success += 1

    print(f"[nav] done: {success}/{len(fund_codes)} succeeded")


if __name__ == "__main__":
    main()
