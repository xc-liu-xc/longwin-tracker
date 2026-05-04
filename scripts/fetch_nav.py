import tushare as ts
import json
import os
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))


def _load_env():
    """Load .env if present (local dev convenience). GHA injects via secrets."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
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
    """Fetch NAV trying suffixes in order: cached → .OF → .SZ → .SH.

    LOF 场内基金通常需要 .SZ/.SH (例如 161017.SZ, 502010.SH).
    场外公募基金用 .OF.
    """
    if not pro:
        raise ValueError("TUSHARE_TOKEN environment variable not set")

    existing = load_existing_nav(fund_code)

    if existing and existing.get("dates"):
        last_date = max(existing["dates"])
        start_date = last_date.replace("-", "")
    else:
        start_date = "20150101"

    now_cn = datetime.now(TZ_CN)
    end_date = now_cn.strftime("%Y%m%d")

    # Determine suffix order: prefer cached, fallback to common chain
    if existing and existing.get("tsCode"):
        suffixes = [existing["tsCode"].split(".")[-1]]
    else:
        suffixes = ["OF", "SZ", "SH"]

    # Freshness threshold: if .OF returns data but last_date is > 30 days stale
    # (e.g. fund migrated from 场外 to 场内 only), keep trying SZ/SH fallback.
    fresh_cutoff = (now_cn - timedelta(days=30)).strftime("%Y%m%d")

    df = None
    ts_code = None
    last_error = None
    for suffix in suffixes:
        candidate_ts = f"{fund_code}.{suffix}"
        try:
            candidate_df = pro.fund_nav(
                ts_code=candidate_ts,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,nav_date,unit_nav,accum_nav,adj_nav",
            )
            if candidate_df is None or candidate_df.empty:
                continue

            latest = max(candidate_df["nav_date"])
            # Accept this suffix if data is fresh OR no other suffixes to try
            if latest >= fresh_cutoff or suffix == suffixes[-1]:
                df = candidate_df
                ts_code = candidate_ts
                break
            # Stale data — remember as fallback but try next suffix
            if df is None:
                df = candidate_df
                ts_code = candidate_ts
        except Exception as e:
            last_error = e
            continue

    if df is None or df.empty:
        if last_error:
            print(f"[nav] error fetching {fund_code}: {last_error}")

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
    new_adj = []

    for _, row in df.iterrows():
        d = row["nav_date"]
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        new_dates.append(date_str)
        new_unit.append(float(row["unit_nav"]))
        new_acc.append(float(row["accum_nav"]))
        # adj_nav may be NaN — fall back to accum_nav
        adj = row.get("adj_nav")
        if adj is not None and adj == adj:  # NaN check
            new_adj.append(float(adj))
        else:
            new_adj.append(float(row["accum_nav"]))

    if existing and existing.get("dates"):
        existing_set = set(existing["dates"])
        merged_dates = list(existing["dates"])
        merged_unit = list(existing["unitNav"])
        merged_acc = list(existing["accNav"])
        # Backfill adjNav from accNav if existing data lacks it
        merged_adj = list(existing.get("adjNav") or existing["accNav"])

        for i, d in enumerate(new_dates):
            if d not in existing_set:
                merged_dates.append(d)
                merged_unit.append(new_unit[i])
                merged_acc.append(new_acc[i])
                merged_adj.append(new_adj[i])

        pairs = sorted(zip(merged_dates, merged_unit, merged_acc, merged_adj), key=lambda x: x[0])
        merged_dates = [p[0] for p in pairs]
        merged_unit  = [p[1] for p in pairs]
        merged_acc   = [p[2] for p in pairs]
        merged_adj   = [p[3] for p in pairs]
    else:
        merged_dates = new_dates
        merged_unit = new_unit
        merged_acc = new_acc
        merged_adj = new_adj

    nav_data = {
        "fundCode": fund_code,
        "tsCode": ts_code,
        "dates": merged_dates,
        "unitNav": merged_unit,
        "accNav": merged_acc,
        "adjNav": merged_adj,
        "current": {
            "unitNav": merged_unit[-1] if merged_unit else None,
            "accNav": merged_acc[-1] if merged_acc else None,
            "adjNav": merged_adj[-1] if merged_adj else None,
            "date": merged_dates[-1] if merged_dates else None,
        },
        "updatedAt": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M CST"),
    }

    save_nav(fund_code, nav_data)
    added = len(merged_dates) - (len(existing["dates"]) if existing and existing.get("dates") else 0)
    print(f"[nav] {fund_code}: {len(merged_dates)} total, +{added} new")
    return True


def main():
    fund_codes = set()
    for path in ("docs/data.json", "docs/data_s.json"):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for h in data.get("holdings", []):
            fund_codes.add(h["fundCode"])

    if not fund_codes:
        print("[nav] no holdings found in any plan file")
        return

    fund_codes = sorted(fund_codes)
    print(f"[nav] fetching NAV for {len(fund_codes)} unique funds across plans...")

    success = 0
    for code in fund_codes:
        if fetch_fund_nav(code):
            success += 1

    print(f"[nav] done: {success}/{len(fund_codes)} succeeded")


if __name__ == "__main__":
    main()
