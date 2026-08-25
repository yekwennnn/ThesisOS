#!/usr/bin/env python3
"""Offline Wind CLI double used by FinanceProvider tests."""

from __future__ import annotations

import json
import re
import sys
import time


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[1:4] != [
        "call",
        "stock_data",
        "get_stock_basicinfo",
    ]:
        print(json.dumps({"ok": False, "code": "BAD_ROUTE", "message": "bad route"}))
        return 2
    try:
        params = json.loads(sys.argv[4])
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "code": "BAD_PARAMS", "message": "bad JSON"}))
        return 2
    if set(params) != {"question"} or not isinstance(params["question"], str):
        print(json.dumps({"ok": False, "code": "BAD_PARAMS", "message": "bad fields"}))
        return 2
    match = re.fullmatch(
        r"查询股票（([A-Z0-9.\-:^]{1,32})）的基本档案",
        params["question"],
    )
    if match is None:
        print(json.dumps({"ok": False, "code": "BAD_QUESTION", "message": "bad question"}))
        return 2
    symbol = match.group(1)

    if symbol == "FAIL":
        print("SUPER_SECRET_WIND_VALUE", file=sys.stderr)
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "AUTH_ERROR",
                    "message": "SUPER_SECRET_WIND_VALUE",
                }
            )
        )
        return 7
    if symbol == "LARGE":
        sys.stdout.write("x" * 200_000)
        return 0
    if symbol == "SLOW":
        time.sleep(2)
    if symbol == "BADJSON":
        sys.stdout.write("not-json")
        return 0

    rows = [
        {
            "wind_code": symbol,
            "short_name": "Example Holdings",
            "exchange": "XHKG",
            "board": "Main Board",
            "industry": "Internet Retail",
            "listing_status": "Listed",
        }
    ]
    if symbol == "AMB":
        rows.append(
            {
                "wind_code": "AMB.B",
                "short_name": "Another Holdings",
                "exchange": "XNAS",
            }
        )
        rows[0]["wind_code"] = "AMB.A"
    inner = {
        "data": {
            "columns": [
                {"key": "wind_code", "name": "Wind代码"},
                {"key": "short_name", "name": "证券简称"},
                {"key": "exchange", "name": "上市交易所"},
                {"key": "board", "name": "上市板块"},
                {"key": "industry", "name": "Wind行业"},
                {"key": "listing_status", "name": "上市状态"},
            ],
            "rows": rows,
        }
    }
    outer = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(inner, ensure_ascii=False),
            }
        ],
        "cli_meta": {
            "server_type": "stock_data",
            "tool_name": "get_stock_basicinfo",
        },
    }
    print(json.dumps(outer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
