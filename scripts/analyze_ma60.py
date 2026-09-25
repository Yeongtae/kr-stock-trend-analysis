#!/usr/bin/env python3
"""Rank this week's 60-day moving-average breakout candidates."""

from __future__ import annotations

import argparse
import csv
import html
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock_history.db"
EXPORT_DIR = ROOT / "exports"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--flow-period", choices=("daily", "recent3"), default="recent3")
    parser.add_argument("--output-suffix", default=None)
    args = parser.parse_args()
    flow_period = args.flow_period
    output_suffix = args.output_suffix or ("today" if flow_period == "daily" else "recent3")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    try:
        con.execute("ALTER TABLE ma60_candidates ADD COLUMN today_return_pct REAL")
    except sqlite3.OperationalError:
        pass
    run = con.execute(
        "SELECT * FROM runs WHERE run_id = COALESCE(?, (SELECT MAX(run_id) FROM runs))",
        (args.run_id,),
    ).fetchone()
    if not run:
        raise SystemExit("분석할 run이 없습니다.")
    run_id = run["run_id"]
    end_date = run["end_date"]
    week_start = run["week_start"]
    recent2_start = run["recent2_start"]

    members = {}
    for row in con.execute("SELECT universe, code, name FROM universes"):
        members.setdefault(row["code"], []).append((row["universe"], row["name"]))

    prices = {}
    for row in con.execute(
        "SELECT code, trade_date, open, close, volume FROM daily_prices ORDER BY code, trade_date"
    ):
        prices.setdefault(row["code"], []).append(dict(row))
    available_price_dates = sorted(
        {row["trade_date"] for rows in prices.values() for row in rows if row["trade_date"] <= end_date}
    )
    price_as_of = available_price_dates[-1] if available_price_dates else end_date

    flows = {}
    for row in con.execute(
        """SELECT code, period_type, investor, net_value
           FROM investor_flows WHERE run_id = ?""",
        (run_id,),
    ):
        flows[(row["code"], row["period_type"], row["investor"])] = row["net_value"] or 0

    available_trade_dates = sorted(
        {row["trade_date"] for rows in prices.values() for row in rows if row["trade_date"] <= end_date}
    )
    flow_start_date = available_trade_dates[-1] if flow_period == "daily" else available_trade_dates[-3]

    universe_period_returns = {}
    for universe in ("KOSPI200", "KOSDAQ150"):
        values = []
        for code, labels in members.items():
            if not any(item[0] == universe for item in labels):
                continue
            code_rows = [r for r in prices.get(code, []) if r["trade_date"] <= end_date]
            rows = [r for r in code_rows if flow_start_date <= r["trade_date"] <= end_date]
            if not rows:
                continue
            if flow_period == "recent3":
                start_price = rows[0]["open"]
                period_return = rows[-1]["close"] / start_price - 1 if start_price else None
            else:
                prior_close = code_rows[-2]["close"] if len(code_rows) >= 2 else 0
                period_return = rows[-1]["close"] / prior_close - 1 if prior_close else None
            if period_return is not None:
                values.append(period_return)
        universe_period_returns[universe] = sum(values) / len(values) if values else 0

    candidates = []
    for code, labels in members.items():
        rows = prices.get(code, [])
        if len(rows) < 80:
            continue
        for index, row in enumerate(rows):
            if index < 79:
                continue
            closes = [r["close"] for r in rows[index - 59 : index + 1]]
            ma60 = sum(closes) / 60
            prior_ma60 = sum([r["close"] for r in rows[index - 79 : index - 19]]) / 60
            row["ma60"] = ma60
            row["slope20_pct"] = (ma60 / prior_ma60 - 1) * 100 if prior_ma60 else 0
            row["distance_pct"] = (row["close"] / ma60 - 1) * 100 if ma60 else 0

        current = rows[-1]
        if "ma60" not in current or current["trade_date"] != price_as_of:
            continue
        current_index = len(rows) - 1
        prior = rows[current_index - 1]
        week_rows = [r for r in rows if week_start <= r["trade_date"] <= end_date]
        if not week_rows:
            continue
        all_cross_dates = []
        for i in range(80, current_index + 1):
            today, yesterday = rows[i], rows[i - 1]
            if today.get("ma60") is not None and yesterday.get("ma60") is not None:
                if today["close"] > today["ma60"] and yesterday["close"] <= yesterday["ma60"]:
                    all_cross_dates.append(today["trade_date"])
        cross_date = all_cross_dates[-1] if all_cross_dates else None
        week_cross_date = next((date for date in all_cross_dates if week_start <= date <= end_date), None)
        days_since_cross = (current_index - next((i for i in range(current_index, 79, -1) if rows[i]["trade_date"] == cross_date), current_index)) if cross_date else None
        last3 = rows[max(0, current_index - 2) : current_index + 1]
        held3 = len(last3) == 3 and all(r["close"] > r["ma60"] for r in last3)
        # The user's primary filter: anything below the 60-day average is out.
        if current["close"] <= current["ma60"]:
            continue

        recent20 = rows[max(0, current_index - 19) : current_index + 1]
        recent5 = rows[max(0, current_index - 4) : current_index + 1]
        avg20_turnover = sum((r["close"] or 0) * (r["volume"] or 0) for r in recent20) / len(recent20)
        avg5_turnover = sum((r["close"] or 0) * (r["volume"] or 0) for r in recent5) / len(recent5)
        turnover_ratio = avg5_turnover / avg20_turnover if avg20_turnover else 0
        weekly_return = week_rows[-1]["close"] / week_rows[0]["close"] - 1 if week_rows[0]["close"] else 0
        today_return = current["close"] / rows[current_index - 1]["close"] - 1 if rows[current_index - 1]["close"] else 0
        if flow_period == "recent3":
            signal_rows = rows[-3:]
            signal_start = signal_rows[0]["open"] if signal_rows else 0
            signal_return = current["close"] / signal_start - 1 if signal_start else weekly_return
        else:
            signal_return = today_return
        universe = labels[0][0]
        name = labels[0][1]
        foreign_week = flows.get((code, "weekly", "foreign"), 0)
        institution_week = flows.get((code, "weekly", "institution"), 0)
        foreign_recent = flows.get((code, "recent2", "foreign"), 0)
        institution_recent = flows.get((code, "recent2", "institution"), 0)
        foreign_flow = flows.get((code, flow_period, "foreign"), 0)
        institution_flow = flows.get((code, flow_period, "institution"), 0)
        positive_count = int(foreign_flow > 0) + int(institution_flow > 0)

        distance_3d_ago = rows[current_index - 3]["distance_pct"] if current_index >= 3 else current["distance_pct"]
        distance_change_3d = current["distance_pct"] - distance_3d_ago
        recent10 = rows[max(0, current_index - 9) : current_index + 1]
        recent10_max_close = max(r["close"] for r in recent10)
        pullback_from_high_pct = (current["close"] / recent10_max_close - 1) * 100 if recent10_max_close else 0
        flow_total = foreign_flow + institution_flow
        flow_confirmation = flow_total > 0 or positive_count == 2
        volume_confirmation = turnover_ratio >= 1.0
        strong_move = (
            signal_return >= 0.05
            and distance_change_3d > 0
            and (flow_confirmation or volume_confirmation)
        ) or (
            current["distance_pct"] >= 5
            and signal_return >= 0.03
            and flow_confirmation
        )

        # A pullback is not automatically rejected. It is healthy while price
        # remains above MA60, the distance contracts gradually, and selling
        # pressure is not accelerating.
        recent_cross = days_since_cross is not None and days_since_cross <= 10
        if strong_move:
            stage = "강한 돌파 추세"
            strong_reason = "주간수익률·60일선 이격·거래대금/수급 확인"
        elif recent_cross and distance_change_3d < -0.5 and current["distance_pct"] > 0.3:
            stage = "돌파 후 눌림목"
            strong_reason = ""
        elif recent_cross and held3:
            stage = "신규 돌파 유지"
            strong_reason = ""
        elif recent_cross:
            stage = "신규 돌파"
            strong_reason = ""
        elif current["distance_pct"] <= 2.0 and distance_change_3d >= -0.3:
            stage = "60일선 근접·재돌파 대기"
            strong_reason = ""
        elif distance_change_3d < -0.8 and current["distance_pct"] > 0.3:
            stage = "돌파 후 눌림목"
            strong_reason = ""
        elif current["distance_pct"] <= 8:
            stage = "60일선 위 추세"
            strong_reason = ""
        else:
            stage = "과열·추격 주의"
            strong_reason = ""

        if stage == "돌파 후 눌림목":
            if current["distance_pct"] >= 0.3 and turnover_ratio <= 1.1 and flow_total >= 0:
                pullback_quality = "건강한 눌림"
            elif turnover_ratio > 1.3 or (foreign_flow < 0 and institution_flow < 0):
                pullback_quality = "매도압력 확인"
            else:
                pullback_quality = "추가 확인"
        else:
            pullback_quality = ""

        stage_priority = {
            "강한 돌파 추세": 60,
            "신규 돌파 유지": 50,
            "신규 돌파": 42,
            "돌파 후 눌림목": 45,
            "60일선 근접·재돌파 대기": 40,
            "60일선 위 추세": 25,
            "과열·추격 주의": 10,
        }[stage]
        proximity_score = clamp(25 - max(current["distance_pct"], 0) * 3, 0, 25)
        flow_score = 10 if positive_count == 2 else 5 if positive_count == 1 else 0
        turnover_score = 5 if turnover_ratio >= 1.2 else 0
        relative_score = 5 if signal_return > universe_period_returns[universe] else 0
        score = stage_priority + proximity_score + flow_score + turnover_score + relative_score
        candidates.append(
            {
                "universe": universe,
                "code": code,
                "name": name,
                "score": round(score, 1),
                "stage": stage,
                "strong_reason": strong_reason,
                "pullback_quality": pullback_quality,
                "current_close": round(current["close"], 2),
                "ma60": round(current["ma60"], 2),
                "slope20_pct": round(current["slope20_pct"], 2),
                "distance_pct": round(current["distance_pct"], 2),
                "distance_change_3d": round(distance_change_3d, 2),
                "pullback_from_high_pct": round(pullback_from_high_pct, 2),
                "cross_date": week_cross_date or "",
                "last_cross_date": cross_date or "",
                "days_since_cross": days_since_cross if days_since_cross is not None else "",
                "held3": "Y" if held3 else "N",
                "turnover_ratio": round(turnover_ratio, 2),
                "weekly_return_pct": round(weekly_return * 100, 2),
                "today_return_pct": round(today_return * 100, 2),
                "signal_return_pct": round(signal_return * 100, 2),
                "flow_period": flow_period,
                "flow_start_date": flow_start_date,
                "foreign_flow_eok": round(foreign_flow / 100_000_000, 1),
                "institution_flow_eok": round(institution_flow / 100_000_000, 1),
                "foreign_flow_value": int(foreign_flow),
                "institution_flow_value": int(institution_flow),
                "foreign_week_eok": round(foreign_week / 100_000_000, 1),
                "institution_week_eok": round(institution_week / 100_000_000, 1),
                "foreign_recent2_eok": round(foreign_recent / 100_000_000, 1),
                "institution_recent2_eok": round(institution_recent / 100_000_000, 1),
                "foreign_week_value": int(foreign_week),
                "institution_week_value": int(institution_week),
                "foreign_recent2_value": int(foreign_recent),
                "institution_recent2_value": int(institution_recent),
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["distance_pct"], -item["signal_return_pct"]))
    con.execute(
        "DELETE FROM ma60_candidate_sets WHERE run_id = ? AND flow_period = ?",
        (run_id, flow_period),
    )
    con.executemany(
        """INSERT INTO ma60_candidate_sets (
            run_id, flow_period, flow_start_date, universe, code, name,
            score, stage, pullback_quality, current_close, ma60, distance_pct,
            weekly_return_pct, signal_return_pct, today_return_pct,
            foreign_flow_value, institution_flow_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                run_id, flow_period, item["flow_start_date"], item["universe"], item["code"], item["name"],
                item["score"], item["stage"], item["pullback_quality"], item["current_close"], item["ma60"],
                item["distance_pct"], item["weekly_return_pct"], item["signal_return_pct"], item["today_return_pct"],
                item["foreign_flow_value"], item["institution_flow_value"],
            )
            for item in candidates
        ],
    )
    if flow_period == "recent3":
        con.execute("DELETE FROM ma60_candidates WHERE run_id = ?", (run_id,))
        con.executemany(
            """INSERT INTO ma60_candidates (
                run_id, universe, code, name, score, stage, pullback_quality,
                current_close, ma60, slope20_pct, distance_pct, distance_change_3d,
                pullback_from_high_pct, cross_date, last_cross_date, days_since_cross,
                held3, turnover_ratio, weekly_return_pct, today_return_pct,
                foreign_week_value, institution_week_value,
                foreign_recent2_value, institution_recent2_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id, item["universe"], item["code"], item["name"], item["score"], item["stage"],
                    item["pullback_quality"], item["current_close"], item["ma60"], item["slope20_pct"],
                    item["distance_pct"], item["distance_change_3d"], item["pullback_from_high_pct"],
                    item["cross_date"], item["last_cross_date"],
                    item["days_since_cross"] if item["days_since_cross"] != "" else None,
                    item["held3"], item["turnover_ratio"], item["weekly_return_pct"], item["today_return_pct"],
                    item["foreign_week_value"], item["institution_week_value"],
                    item["foreign_recent2_value"], item["institution_recent2_value"],
                )
                for item in candidates
            ],
        )
    con.commit()
    report_dir = ROOT / "reports" / run_id
    export_dir = EXPORT_DIR / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in (
        "weekly-summary.html",
        "ma60-candidates-today.html", "ma60-candidates-recent3.html",
        "ma60-strong-breakouts.html",
        "ma60-strong-breakouts-today.html", "ma60-strong-breakouts-recent3.html",
        "recommended-stocks-recent3.html",
        "ma60_candidates_recent3.csv", "ma60_strong_breakouts_recent3.csv",
        "recommended_stocks_recent3.csv",
    ):
        (report_dir / stale_name).unlink(missing_ok=True)
        (export_dir / stale_name).unlink(missing_ok=True)

    strong_candidates = [item for item in candidates if item["stage"] == "강한 돌파 추세"]
    signal_label = "당일" if flow_period == "daily" else "최근 3거래일"
    report_suffix = "당일 수급" if flow_period == "daily" else "최근 3거래일 수급"
    recommendations = []
    for recommendation_rank, item in enumerate(strong_candidates, 1):
        recommendation = dict(item)
        recommendation["recommendation_rank"] = recommendation_rank
        recommendation["recommendation_basis"] = f"{report_suffix} 기준 · 강한 돌파 추세"
        recommendations.append(recommendation)
    table_style = (
        "<style>body{font-family:system-ui;margin:24px;color:#20242a}table{border-collapse:collapse;font-size:13px;min-width:1050px}"
        "th,td{padding:7px 9px;border:1px solid #ddd;text-align:right;white-space:nowrap}th{background:#f1f3f5}"
        "td:nth-child(1),td:nth-child(2){text-align:left}.table-wrap{overflow-x:auto}</style>"
    )

    def write_csv_report(path: Path, items: list[dict], fieldnames: list[str] | None = None) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            columns = fieldnames or (list(items[0].keys()) if items else ["name"])
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(items)

    def write_html_report(path: Path, title: str, subtitle: str, items: list[dict], columns: list[tuple[str, str]]) -> None:
        head = "".join(f"<th>{html.escape(label)}</th>" for label, _ in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(item[key]))}</td>" for _, key in columns) + "</tr>"
            for item in items
        )
        path.write_text(
            "<!doctype html><html lang='ko'><meta charset='utf-8'><title>" + html.escape(title) + "</title>"
            + table_style + "<h1>" + html.escape(title) + "</h1><p>" + html.escape(subtitle)
            + "</p><div class='table-wrap'><table><thead><tr>" + head + "</tr></thead><tbody>"
            + body + "</tbody></table></div></html>",
            encoding="utf-8",
        )

    all_columns = [
        ("시장", "universe"), ("종목", "name"), ("점수", "score"), ("단계", "stage"),
        ("눌림목 질", "pullback_quality"), ("현재가-60선(%)", "distance_pct"),
        ("주간수익률(%)", "weekly_return_pct"), (f"{signal_label}수익률(%)", "signal_return_pct"),
        ("오늘수익률(%)", "today_return_pct"), ("3일 거리변화(%p)", "distance_change_3d"),
        ("돌파일", "cross_date"), ("3일 유지", "held3"), ("거래대금비", "turnover_ratio"),
        (f"외국인 {signal_label}(억)", "foreign_flow_eok"),
        (f"기관 {signal_label}(억)", "institution_flow_eok"),
    ]
    strong_columns = [
        ("시장", "universe"), ("종목", "name"), ("점수", "score"),
        ("주간수익률(%)", "weekly_return_pct"), (f"{signal_label}수익률(%)", "signal_return_pct"),
        ("현재가-60선(%)", "distance_pct"), ("3일 거리변화(%p)", "distance_change_3d"),
        ("돌파일", "cross_date"), ("거래대금비", "turnover_ratio"),
        (f"외국인 {signal_label}(억)", "foreign_flow_eok"),
        (f"기관 {signal_label}(억)", "institution_flow_eok"),
    ]
    recommendation_columns = [
        ("추천순위", "recommendation_rank"), ("추천근거", "recommendation_basis"),
        *strong_columns,
    ]
    recommendation_fields = [key for _, key in recommendation_columns] + [
        "code", "stage", "pullback_quality", "strong_reason", "flow_period", "flow_start_date",
    ]
    subtitle = (
        f"가격 기준일 {price_as_of} · 최근 3거래일 이격 변화 · {report_suffix} 기준 수급 확인 · "
        "60일선 아래 종목 제외 · 거래대금 단위: 억원"
    )
    if flow_period == "recent3":
        candidate_csv = report_dir / "ma60_candidates.csv"
        strong_csv = report_dir / "ma60_strong_breakouts.csv"
        candidate_html = report_dir / "ma60-candidates.html"
        recommendation_csv = report_dir / "recommended_stocks.csv"
        recommendation_html = report_dir / "recommended-stocks.html"
        export_recommendation_csv = export_dir / "recommended_stocks.csv"
    else:
        candidate_csv = report_dir / "ma60_candidates_today.csv"
        strong_csv = report_dir / "ma60_strong_breakouts_today.csv"
        candidate_html = None
        recommendation_csv = report_dir / "recommended_stocks_today.csv"
        recommendation_html = report_dir / "recommended-stocks-today.html"
        export_recommendation_csv = export_dir / "recommended_stocks_today.csv"
    write_csv_report(candidate_csv, candidates)
    write_csv_report(strong_csv, strong_candidates)
    write_csv_report(recommendation_csv, recommendations, recommendation_fields)
    write_csv_report(export_recommendation_csv, recommendations, recommendation_fields)
    if flow_period == "recent3":
        write_html_report(
            candidate_html,
            f"60일선 위치·돌파단계 후보 · {report_suffix} 기준 ({run_id}, 가격 기준일 {price_as_of})",
            subtitle,
            candidates[:20],
            all_columns,
        )
    write_html_report(
        recommendation_html,
        f"추천종목 · {report_suffix} 기준 ({run_id}, 가격 기준일 {price_as_of})",
        subtitle + " · 추천종목 = 강한 돌파 추세",
        recommendations,
        recommendation_columns,
    )
    print(f"run={run_id} price_as_of={price_as_of} flow_period={flow_period} candidates={len(candidates)}")
    print(f"strong_candidates={len(strong_candidates)}")
    print(f"recommendations_csv={recommendation_csv}")
    print(f"recommendations_html={recommendation_html}")
    if candidate_html:
        print(f"candidates_html={candidate_html}")


if __name__ == "__main__":
    main()
