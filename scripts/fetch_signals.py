import requests
import json
import os
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

GRAPHQL_URL = "https://qieman.com/alfa/v1/graphql"

QUERY = """query LongWinSignal($poCode: String!) {
  longWin(poCode: $poCode) {
    poName
    adjustedCount
    createdDate
    totalUnit
    investedUnit
    adjustments {
      adjustmentId
      articleLink
      buyOrders {
        fund {
          fundName
          fundCode
          __typename
        }
        tradeUnit
        variety
        __typename
      }
      date
      redeemOrders {
        fund {
          fundName
          fundCode
          __typename
        }
        tradeUnit
        variety
        __typename
      }
      __typename
    }
    __typename
  }
}"""


def fetch_data():
    token = os.environ.get("QIEMAN_TOKEN")
    if not token:
        raise ValueError("QIEMAN_TOKEN environment variable not set")

    sign = os.environ.get("QIEMAN_SIGN", "")

    headers = {
        "Content-Type": "application/json",
        "Authorization": token,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://qieman.com/alfa/portfolio/LONG_WIN/signal",
        "Origin": "https://qieman.com",
        "x-broker": "0008",
    }
    if sign:
        headers["x-sign"] = sign

    payload = {
        "operationName": "LongWinSignal",
        "query": QUERY,
        "variables": {"poCode": "LONG_WIN"},
    }

    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    if "errors" in data:
        raise ValueError(f"GraphQL error: {data['errors']}")

    long_win = data["data"]["longWin"]
    adjustments = long_win["adjustments"]

    holdings = {}

    for adj in adjustments:
        for order in adj.get("buyOrders", []):
            code = order["fund"]["fundCode"]
            name = order["fund"]["fundName"]
            unit = order["tradeUnit"]
            if code not in holdings:
                holdings[code] = {
                    "fundCode": code,
                    "fundName": name,
                    "bought": 0,
                    "sold": 0,
                    "history": [],
                }
            holdings[code]["bought"] += unit
            holdings[code]["history"].append(
                {
                    "date": adj["date"][:10],
                    "action": "buy",
                    "unit": unit,
                    "articleLink": adj.get("articleLink"),
                }
            )

        for order in adj.get("redeemOrders", []):
            code = order["fund"]["fundCode"]
            name = order["fund"]["fundName"]
            unit = order["tradeUnit"]
            if code not in holdings:
                holdings[code] = {
                    "fundCode": code,
                    "fundName": name,
                    "bought": 0,
                    "sold": 0,
                    "history": [],
                }
            holdings[code]["sold"] += unit
            holdings[code]["history"].append(
                {
                    "date": adj["date"][:10],
                    "action": "sell",
                    "unit": unit,
                    "articleLink": adj.get("articleLink"),
                }
            )

    holdings_list = []
    for h in holdings.values():
        h["currentUnit"] = h["bought"] - h["sold"]
        h["history"].sort(key=lambda x: x["date"], reverse=True)
        h["latestDate"] = h["history"][0]["date"] if h["history"] else ""
        holdings_list.append(h)

    holdings_list.sort(key=lambda x: (-x["currentUnit"], x["latestDate"] or ""))

    recent = []
    for adj in adjustments[:10]:
        date = adj["date"][:10]
        link = adj.get("articleLink")
        for order in adj.get("buyOrders", []):
            recent.append(
                {
                    "date": date,
                    "action": "buy",
                    "fundCode": order["fund"]["fundCode"],
                    "fundName": order["fund"]["fundName"],
                    "unit": order["tradeUnit"],
                    "articleLink": link,
                }
            )
        for order in adj.get("redeemOrders", []):
            recent.append(
                {
                    "date": date,
                    "action": "sell",
                    "fundCode": order["fund"]["fundCode"],
                    "fundName": order["fund"]["fundName"],
                    "unit": order["tradeUnit"],
                    "articleLink": link,
                }
            )

    total_buy = sum(
        o["tradeUnit"] for adj in adjustments for o in adj.get("buyOrders", [])
    )
    total_sell = sum(
        o["tradeUnit"] for adj in adjustments for o in adj.get("redeemOrders", [])
    )

    now_cn = datetime.now(TZ_CN)

    result = {
        "updatedAt": now_cn.strftime("%Y-%m-%d %H:%M CST"),
        "poName": long_win["poName"],
        "totalUnit": long_win["totalUnit"],
        "investedUnit": long_win["investedUnit"],
        "adjustedCount": long_win["adjustedCount"],
        "totalBuy": total_buy,
        "totalSell": total_sell,
        "recentSignals": recent,
        "holdings": holdings_list,
    }

    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[signals] updated: {result['updatedAt']}")
    print(f"[signals] holdings: {len(holdings_list)}, recent: {len(recent)}")


if __name__ == "__main__":
    fetch_data()
