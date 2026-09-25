#!/usr/bin/env python3
"""Build an append-only weekly KOSPI200/KOSDAQ150 history.

The script intentionally uses only the Python standard library so it can run
from the bundled desktop runtime without a package installation step.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import html
import json
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "raw"
REPORT_DIR = ROOT / "reports"
EXPORT_DIR = ROOT / "exports"
DB_PATH = ROOT / "stock_history.db"
SCHEMA_PATH = ROOT / "schema.sql"
UA = "Mozilla/5.0 (Codex weekly stock history)"


def fetch_bytes(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    request = Request(url, data=data, headers=req_headers, method="POST" if data else "GET")
    with urlopen(request, timeout=45) as response:
        return response.read()


def fetch_text(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
    return fetch_bytes(url, data, headers).decode("utf-8", errors="replace")


def fetch_json(url: str, data: bytes | None = None, headers: dict[str, str] | None = None):
    return json.loads(fetch_text(url, data, headers))


def parse_num(value) -> int:
    if value is None or value == "":
        return 0
    return int(str(value).replace(",", "").replace("+", ""))


def parse_float(value) -> float:
    return float(str(value).replace(",", ""))


def parse_constituents(url: str, count: int) -> tuple[str, list[dict]]:
    source = fetch_text(url)
    section_pos = source.index("구성 종목")
    marker_match = re.search(r"20\d{2}년\s*\d{1,2}월\s*\d{1,2}일 기준", source[section_pos:])
    if not marker_match:
        raise RuntimeError(f"구성종목 기준일을 찾지 못했습니다: {url}")
    marker = marker_match.group(0)
    marker_pos = section_pos + marker_match.start()
    table_end = source.index("</table>", marker_pos)
    table = source[marker_pos:table_end]
    as_of = re.search(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일 기준", marker).groups()
    snapshot_date = f"{as_of[0]}-{int(as_of[1]):02d}-{int(as_of[2]):02d}"
    pattern = re.compile(r"<td[^>]*>.*?<strong>(\d+)</strong>.*?<strong>(.*?)</strong>", re.S)
    rows = []
    for match in pattern.finditer(table):
        name = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        if name and not name.isdigit():
            rows.append({"rank": int(match.group(1)), "name": name})
    rows = sorted(rows, key=lambda row: row["rank"])[:count]
    if len(rows) != count:
        raise RuntimeError(f"구성종목 추출 수가 예상과 다릅니다: {url} {len(rows)}/{count}")
    return snapshot_date, rows


def load_stock_master() -> dict[str, str]:
    compressed = fetch_bytes("https://github.com/FinanceData/stock_master/raw/master/stock_master.csv.gz")
    text = gzip.decompress(compressed).decode("utf-8-sig")
    return {row["Name"]: row["Symbol"] for row in csv.DictReader(text.splitlines())}


def autocomplete_code(name: str) -> tuple[str, str | None]:
    url = "https://m.stock.naver.com/front-api/search/autoComplete?" + urlencode(
        {"query": name, "target": "stock,index,marketindicator,coin,ipo"}
    )
    try:
        payload = fetch_json(url)
        items = [item for item in payload.get("result", {}).get("items", []) if item.get("category") == "stock"]
        return name, items[0].get("code") if items else None
    except Exception:
        return name, None


def map_codes(universes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    master = load_stock_master()
    all_names = {row["name"] for rows in universes.values() for row in rows}
    missing = [name for name in all_names if name not in master]
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(autocomplete_code, name) for name in missing]
        for future in as_completed(futures):
            name, code = future.result()
            if code:
                master[name] = code
    unmapped = sorted(name for name in all_names if name not in master)
    if unmapped:
        raise RuntimeError("종목코드 매핑 실패: " + ", ".join(unmapped))
    return {
        universe: [{**row, "code": master[row["name"]]} for row in rows]
        for universe, rows in universes.items()
    }


def fetch_price_history(code: str, start_date: dt.date, end_date: dt.date) -> tuple[str, list[dict], str | None]:
    url = (
        "https://api.finance.naver.com/siseJson.naver?"
        + urlencode(
            {
                "symbol": code,
                "requestType": "1",
                # Keep enough history for the 60-day moving average and its slope.
                # 120 calendar days covers at least 60 Korean trading sessions,
                # including holidays and occasional missing listings.
                "startTime": (start_date - dt.timedelta(days=120)).strftime("%Y%m%d"),
                "endTime": end_date.strftime("%Y%m%d"),
                "timeframe": "day",
            }
        )
    )
    try:
        # Naver's legacy endpoint is JavaScript-like rather than strict JSON:
        # the header uses single quotes and rows are valid JSON arrays.
        source = fetch_text(url)
        payload = [[], *[[date, *values.split(",")] for date, values in re.findall(r'\["(\d{8})",\s*([^\]]+)\]', source)]]
        rows = []
        for raw in payload[1:]:
            if not isinstance(raw, list) or len(raw) < 6:
                continue
            trade_date = dt.datetime.strptime(str(raw[0]), "%Y%m%d").date()
            if trade_date > end_date:
                continue
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date.isoformat(),
                    "open": parse_float(raw[1]),
                    "high": parse_float(raw[2]),
                    "low": parse_float(raw[3]),
                    "close": parse_float(raw[4]),
                    "volume": parse_num(raw[5]),
                    "source": "Naver siseJson",
                }
            )
        return code, rows, None
    except Exception as exc:
        return code, [], str(exc)


def fetch_prices(codes: list[str], start_date: dt.date, end_date: dt.date) -> tuple[dict[str, list[dict]], list[dict]]:
    prices: dict[str, list[dict]] = {}
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_price_history, code, start_date, end_date) for code in codes]
        for future in as_completed(futures):
            code, rows, error = future.result()
            if error:
                errors.append({"code": code, "error": error})
            prices[code] = rows
    return prices, errors


def fetch_krx_flow(market: str, investor_code: str, start_date: dt.date, end_date: dt.date) -> dict:
    body = urlencode(
        {
            "bld": "dbms/MDC_OUT/STAT/standard/MDCSTAT02401_OUT",
            "mktId": market,
            "invstTpCd": investor_code,
            "strtDd": start_date.strftime("%Y%m%d"),
            "endDd": end_date.strftime("%Y%m%d"),
            "share": "1",
            "money": "1",
            "locale": "ko_KR",
        }
    ).encode()
    return fetch_json(
        "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        body,
        {"Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT024"},
    )


def price_period_rows(rows: list[dict], start_date: dt.date, end_date: dt.date, count: int | None = None) -> list[dict]:
    selected = [row for row in rows if start_date.isoformat() <= row["trade_date"] <= end_date.isoformat()]
    selected.sort(key=lambda row: row["trade_date"])
    return selected[-count:] if count else selected


def build_return_rows(universes, prices, period_type: str, start_date: dt.date, end_date: dt.date, count: int | None):
    results = []
    for universe, members in universes.items():
        candidates = []
        for member in members:
            rows = price_period_rows(prices.get(member["code"], []), start_date, end_date, count)
            if len(rows) < 2:
                continue
            first, last = rows[0], rows[-1]
            if not first["open"]:
                continue
            candidates.append(
                {
                    "universe": universe,
                    "code": member["code"],
                    "name": member["name"],
                    "start_date": first["trade_date"],
                    "end_date": last["trade_date"],
                    "start_open": first["open"],
                    "end_close": last["close"],
                    "return_pct": (last["close"] / first["open"] - 1) * 100,
                }
            )
        for direction, ordered in (
            ("up", sorted(candidates, key=lambda row: row["return_pct"], reverse=True)),
            ("down", sorted(candidates, key=lambda row: row["return_pct"])),
        ):
            for rank, row in enumerate(ordered[:15], 1):
                results.append({"period_type": period_type, "direction": direction, "rank": rank, **row})
    return results


def build_flow_rows(flow_payload, universe, investor, member_codes, period_type: str):
    rows = []
    for item in flow_payload.get("output", []):
        code = item.get("ISU_SRT_CD")
        if code not in member_codes:
            continue
        rows.append(
            {
                "period_type": period_type,
                "universe": universe,
                "investor": investor,
                "code": code,
                "name": item.get("ISU_NM", ""),
                "sell_volume": parse_num(item.get("ASK_TRDVOL")),
                "buy_volume": parse_num(item.get("BID_TRDVOL")),
                "net_volume": parse_num(item.get("NETBID_TRDVOL")),
                "sell_value": parse_num(item.get("ASK_TRDVAL")),
                "buy_value": parse_num(item.get("BID_TRDVAL")),
                "net_value": parse_num(item.get("NETBID_TRDVAL")),
                "source": "KRX MDCSTAT02401_OUT",
            }
        )
    return rows


def flow_rankings(flow_rows):
    results = []
    groups = {}
    for row in flow_rows:
        key = (row["period_type"], row["universe"], row["investor"])
        groups.setdefault(key, []).append(row)
    for (period_type, universe, investor), rows in groups.items():
        for direction, ordered in (
            ("buy", sorted(rows, key=lambda row: row["net_value"], reverse=True)),
            ("sell", sorted(rows, key=lambda row: row["net_value"])),
        ):
            for rank, row in enumerate(ordered[:15], 1):
                results.append(
                    {
                        "period_type": period_type,
                        "universe": universe,
                        "investor": investor,
                        "direction": direction,
                        "rank": rank,
                        "code": row["code"],
                        "name": row["name"],
                        "net_value": row["net_value"],
                    }
                )
    return results


def ensure_dirs(end_date: str):
    raw_dir = RAW_DIR / end_date
    report_dir = REPORT_DIR / end_date
    export_dir = EXPORT_DIR / end_date
    for directory in (raw_dir, report_dir, export_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return raw_dir, report_dir, export_dir


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def money_eok(value: int) -> str:
    return f"{value / 100_000_000:+,.1f}억"


def make_flow_html(flow_rank_rows: list[dict], title: str, subtitle: str = "순매수 거래대금 · 단위: 억원") -> str:
    groups = {}
    for row in flow_rank_rows:
        key = (row["universe"], row["investor"], row["direction"])
        groups.setdefault(key, []).append(row)
    cards = []
    for (universe, investor, direction), rows in groups.items():
        universe_label = {"KOSPI200": "코스피200", "KOSDAQ150": "코스닥150"}.get(universe, universe)
        investor_label = {"foreign": "외국인", "institution": "기관"}.get(investor, investor)
        max_abs = max(abs(row["net_value"]) for row in rows) or 1
        bars = []
        for index, row in enumerate(rows, 1):
            width = abs(row["net_value"]) / max_abs * 100
            color = "#168a63" if direction == "buy" else "#d94d5c"
            bars.append(
                f"<div class='row'><span class='name'>{index}. {html.escape(row['name'])}</span>"
                f"<span class='track'><i style='width:{width:.2f}%;background:{color}'></i></span>"
                f"<span class='value'>{money_eok(row['net_value'])}</span></div>"
            )
        cards.append(
            f"<section class='card'><h2>{html.escape(universe_label)} · {html.escape(investor_label)} · "
            f"{'순매수' if direction == 'buy' else '순매도'} TOP15</h2>{''.join(bars)}</section>"
        )
    return f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:24px;color:#20242a;background:#f7f8fa}}h1{{margin:0 0 6px}}.sub{{color:#69727d;margin:0 0 18px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.card{{background:white;border:1px solid #e1e5ea;border-radius:10px;padding:14px 16px}}h2{{font-size:16px;margin:0 0 10px}}.row{{display:grid;grid-template-columns:175px 1fr 78px;gap:8px;align-items:center;height:24px;font-size:12px}}.name{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.track{{height:11px;background:#eef1f4;border-radius:4px;overflow:hidden}}.track i{{display:block;height:100%;border-radius:4px}}.value{{text-align:right;font-variant-numeric:tabular-nums;color:#525b66}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style>
<h1>{html.escape(title)}</h1><p class='sub'>{html.escape(subtitle)}</p><main class='grid'>{''.join(cards)}</main></html>"""


def create_db(run_id, generated_at, end_date, week_start, week_end_trade, recent2_start, universes, prices, returns, flows, flow_ranks, source_note):
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.execute("DELETE FROM return_rankings WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM investor_flows WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM flow_rankings WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM ma60_candidates WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM ma60_candidate_sets WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    connection.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, generated_at, end_date.isoformat(), week_start.isoformat(), week_end_trade, recent2_start, end_date.isoformat(), source_note),
    )
    for universe, members in universes.items():
        snapshot_date = members[0]["snapshot_date"]
        connection.executemany(
            "INSERT OR REPLACE INTO universes VALUES (?, ?, ?, ?, ?)",
            [(universe, snapshot_date, member["rank"], member["code"], member["name"]) for member in members],
        )
    retrieved_at = generated_at
    for code, rows in prices.items():
        connection.executemany(
            "INSERT OR REPLACE INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(row["code"], row["trade_date"], row.get("name"), row["open"], row["high"], row["low"], row["close"], row["volume"], row["source"], retrieved_at) for row in rows],
        )
    connection.executemany(
        "INSERT INTO return_rankings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, row["period_type"], row["universe"], row["rank"], row["code"], row["name"], row["start_date"], row["end_date"], row["start_open"], row["end_close"], row["return_pct"], row["direction"]) for row in returns],
    )
    connection.executemany(
        "INSERT INTO investor_flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, row["period_type"], row["universe"], row["investor"], row["code"], row["name"], row["sell_volume"], row["buy_volume"], row["net_volume"], row["sell_value"], row["buy_value"], row["net_value"], row["source"]) for row in flows],
    )
    connection.executemany(
        "INSERT INTO flow_rankings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, row["period_type"], row["universe"], row["investor"], row["direction"], row["rank"], row["code"], row["name"], row["net_value"]) for row in flow_ranks],
    )
    connection.commit()
    connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", help="마지막 거래일, YYYY-MM-DD")
    args = parser.parse_args()
    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else dt.date.today()
    week_start = end_date - dt.timedelta(days=end_date.weekday())
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    run_id = end_date.isoformat()
    raw_dir, report_dir, export_dir = ensure_dirs(run_id)

    snapshot_kp, kp = parse_constituents("https://m.namu.moe/w/KOSPI200", 200)
    snapshot_kd, kd = parse_constituents("https://m.namu.moe/w/KOSDAQ150", 150)
    universes = {"KOSPI200": kp, "KOSDAQ150": kd}
    universes = map_codes(universes)
    for universe, rows in universes.items():
        snapshot_date = snapshot_kp if universe == "KOSPI200" else snapshot_kd
        for row in rows:
            row["snapshot_date"] = snapshot_date
    all_codes = sorted({row["code"] for row in universes["KOSPI200"] + universes["KOSDAQ150"]})
    prices, price_errors = fetch_prices(all_codes, week_start, end_date)

    weekly_returns = build_return_rows(universes, prices, "weekly", week_start, end_date, None)
    all_trade_dates = sorted({row["trade_date"] for rows in prices.values() for row in rows if row["trade_date"] <= end_date.isoformat()})
    if len(all_trade_dates) < 5:
        raise RuntimeError("최근 5거래일을 확인할 가격 데이터가 부족합니다.")
    flow_end_date = dt.date.fromisoformat(all_trade_dates[-1])
    recent5_start_date = dt.date.fromisoformat(all_trade_dates[-5])
    recent2_start_date = dt.date.fromisoformat(all_trade_dates[-2])
    recent3_start_date = dt.date.fromisoformat(all_trade_dates[-3])
    recent2_returns = build_return_rows(universes, prices, "recent2", recent2_start_date, end_date, 2)
    recent3_returns = build_return_rows(universes, prices, "recent3", recent3_start_date, end_date, 3)
    returns = weekly_returns + recent2_returns + recent3_returns
    week_trade_dates = sorted({row["trade_date"] for rows in prices.values() for row in rows if week_start.isoformat() <= row["trade_date"] <= end_date.isoformat()})
    week_end_trade = week_trade_dates[-1] if week_trade_dates else None

    flow_payloads = {}
    flow_sources = {}
    flow_rows = []
    # Use KRX for every investor-flow period so the full universe is covered
    # consistently. The period windows are based on actual trading dates.
    krx_periods = (
        ("daily", flow_end_date, "KRX exact daily"),
        ("weekly", recent5_start_date, "KRX exact recent5"),
        ("recent2", recent2_start_date, "KRX exact recent2"),
        ("recent3", recent3_start_date, "KRX exact recent3"),
    )
    for period_type, period_start, source_label in krx_periods:
        for market, universe in (("KOSPI", "KOSPI200"), ("KOSDAQ", "KOSDAQ150")):
            member_codes = {row["code"] for row in universes[universe]}
            krx_market = "STK" if market == "KOSPI" else "KSQ"
            for investor_code, investor in (("9000", "foreign"), ("7050", "institution")):
                key = f"{period_type}_{market}_{investor}"
                payload = fetch_krx_flow(krx_market, investor_code, period_start, flow_end_date)
                flow_payloads[key] = payload
                flow_sources[key] = source_label
                flow_rows.extend(build_flow_rows(payload, universe, investor, member_codes, period_type))
    flow_ranks = flow_rankings(flow_rows)
    source_note = "모든 투자자 수급은 KRX MDCSTAT02401_OUT 기준(당일/최근5/최근2/최근3); Naver prices + 구성종목 스냅샷"

    raw_dir.joinpath("constituents.json").write_text(json.dumps(universes, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_dir.joinpath("prices.json").write_text(json.dumps(prices, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_dir.joinpath("investor_flows.json").write_text(json.dumps(flow_payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    for stale_name in ("investor_flows_daum.json", "investor_flows_krx_fallback.json", "investor_flows_krx.json"):
        (raw_dir / stale_name).unlink(missing_ok=True)
    raw_dir.joinpath("investor_flow_sources.json").write_text(json.dumps(flow_sources, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_dir.joinpath("errors.json").write_text(json.dumps({"price_errors": price_errors}, ensure_ascii=False, indent=2), encoding="utf-8")

    write_csv(export_dir / "return_rankings.csv", returns)
    write_csv(export_dir / "investor_flows.csv", flow_rows)
    write_csv(export_dir / "flow_rankings.csv", flow_ranks)
    report_title = f"주식 동향 {end_date.isoformat()}"
    daily_source = f"KRX 기준 당일 ({flow_end_date.isoformat()}) · 전체 구성종목 수급"
    daily_html = make_flow_html(
        [row for row in flow_ranks if row["period_type"] == "daily"],
        report_title + " · 당일 수급",
        f"{daily_source} · 순매수 거래대금 · 단위: 억원",
    )
    (report_dir / "daily-summary.html").write_text(daily_html, encoding="utf-8")
    flow_html = make_flow_html([row for row in flow_ranks if row["period_type"] == "recent2"], report_title + " · 최근 2거래일 수급")
    (report_dir / "investor-flow-chart.html").write_text(flow_html, encoding="utf-8")
    recent3_html = make_flow_html(
        [row for row in flow_ranks if row["period_type"] == "recent3"],
        report_title + " · 최근 3거래일 수급",
        f"KRX 기준 최근 3거래일 ({recent3_start_date.isoformat()}~{flow_end_date.isoformat()}) · 순매수 거래대금 · 단위: 억원",
    )
    (report_dir / "recent3-summary.html").write_text(recent3_html, encoding="utf-8")
    summary_html = make_flow_html(
        [row for row in flow_ranks if row["period_type"] == "weekly"],
        report_title + " · 최근 5거래일 수급",
        f"KRX 기준 최근 5거래일 ({recent5_start_date.isoformat()}~{flow_end_date.isoformat()}) · 순매수 거래대금 · 단위: 억원",
    )
    (report_dir / "five-day-summary.html").write_text(summary_html, encoding="utf-8")
    (report_dir / "weekly-summary.html").write_text(recent3_html, encoding="utf-8")

    create_db(run_id, generated_at, end_date, week_start, week_end_trade, recent2_start_date.isoformat(), universes, prices, returns, flow_rows, flow_ranks, source_note)
    analysis_script = ROOT / "scripts" / "analyze_ma60.py"
    for flow_period, output_suffix in (("daily", "today"), ("recent3", "recent3")):
        subprocess.run(
            [sys.executable, str(analysis_script), "--run-id", run_id, "--flow-period", flow_period, "--output-suffix", output_suffix],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    pair_index = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>오늘·최근 3거래일 리포트 묶음</title>
<style>body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:28px;background:#f7f8fa;color:#20242a}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}section{{background:white;padding:18px;border:1px solid #e1e5ea;border-radius:12px}}h1{{margin-bottom:6px}}p{{color:#69727d}}a{{display:block;margin:12px 0;color:#1769aa}}</style>
<h1>수급·돌파 후보 리포트 ({end_date.isoformat()})</h1><p>당일 기준과 최근 3거래일 기준으로 나눈 두 묶음</p><main>
<section><h2>당일 데이터 pair</h2><a href='daily-summary.html'>당일 수급 리포트</a><a href='ma60-strong-breakouts-today.html'>당일 수급 기준 강한 돌파 후보</a></section>
<section><h2>최근 3거래일 수급 pair</h2><a href='recent3-summary.html'>최근 3거래일 수급 리포트 (KRX exact window)</a><a href='ma60-strong-breakouts-recent3.html'>최근 3거래일 수급 기준 강한 돌파 후보</a></section>
</main></html>"""
    (report_dir / "report-pairs.html").write_text(pair_index, encoding="utf-8")
    print(json.dumps({"run_id": run_id, "db": str(DB_PATH), "price_rows": sum(len(rows) for rows in prices.values()), "price_errors": len(price_errors), "return_rows": len(returns), "flow_rows": len(flow_rows), "flow_sources": flow_sources, "ma60_analysis": "done", "report_dir": str(report_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
