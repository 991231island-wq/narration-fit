#!/usr/bin/env python3
"""按段校验口播字数预算。

v1 只读 timeline.json 与 fit.json，不读视频、不联网、不调用 TTS。
字数与预算一律在本脚本里重算，不采信 fit.json 里预填的 chars / budget。
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any


CHAR_COUNT_RULE = "v1：去掉空白后按字符计数，标点计入。改规则必须升版本。"
BUDGET_RULE = (
    "budget_chars = floor((end_s - start_s) × chars_per_sec)；"
    "用十进制 Decimal 计算后向负无穷取整，避免二进制浮点把 10×4.7 截成 46。"
)
SUGGESTED_COMFORT_CPS = Decimal("4.7")
SUGGESTED_FASTER_CPS = Decimal("5.5")
DEFAULT_CAP = Decimal("6.0")
COMFORT_SOURCE = (
    "舒适语速建议 4.7 字／秒：澄象创空间 README「自然讲课实测」参考值，不是行业标准，脚本不自动套用。"
)
FASTER_SOURCE = "加快语速建议 5.5 字／秒：本 Skill 产品默认，不是口播实测，脚本不自动套用。"
CAP_SOURCE = "语速上限默认 6.0 字／秒：产品默认，防止 keep_faster 无上限加速；该数字是否适合所有口音未核实。"
RANGE_NOTE = "舒适区参考 3.5～4.7 字／秒是否适合所有口音或方言：未核实，不要写成科学结论。"

STRATEGIES = ("trim", "keep_faster", "expand")
DEFAULT_EXPAND_SHORT_RATIO = Decimal("0.7")
FIT_ACTION_LABELS = {
    "expanded": "已扩写",
    "trimmed_first": "先修剪",
    "still_short": "仍偏短",
    "held": "保持",
    "over": "超预算",
}


class InputError(Exception):
    """文件读不到或 JSON 不合法。调用方用退出码 2。"""


def count_chars(text: str) -> int:
    return len("".join(text.split()))


def squash(text: str) -> str:
    return "".join(text.split())


def parse_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InputError(f"{field} 必须是数字，收到 {type(value).__name__}")
    else:
        try:
            number = Decimal(str(value).strip())
        except Exception as exc:  # noqa: BLE001 — 向用户展示字段名
            raise InputError(f"{field} 不是合法数字：{value}") from exc
    if not number.is_finite():
        raise InputError(f"{field} 不是有限数字：{value}")
    return number


def format_decimal(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_rate(number: Decimal) -> str:
    """语速至少保留一位小数，使 6 显示为 6.0，5.50 显示为 5.5。"""
    quantized = number.quantize(Decimal("0.01"))
    whole, frac = format(quantized, "f").split(".")
    frac = frac.rstrip("0") or "0"
    return f"{whole}.{frac}"


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise InputError(f"找不到文件：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"JSON 无法解析：{path}（第 {exc.lineno} 行：{exc.msg}）") from exc


def as_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{name} 必须是 JSON 对象")
    return value


def budget_chars(duration: Decimal, chars_per_sec: Decimal) -> int:
    if duration <= 0 or chars_per_sec <= 0:
        return 0
    product = duration * chars_per_sec
    return int(product.to_integral_value(rounding=ROUND_FLOOR))


def min_cps_to_fit(chars: int, duration: Decimal) -> Decimal | None:
    """返回保留全部改后文字所需的最低语速，精确到 0.01 字／秒。时长无效时返回 None。"""
    if chars <= 0:
        return Decimal("0")
    if duration <= 0:
        return None
    step = Decimal("0.01")
    raw = Decimal(chars) / duration
    cents = (raw / step).to_integral_value(rounding=ROUND_FLOOR)
    if cents < 1:
        cents = Decimal(1)
    # floor 可能让理论值差一点，向上试到放得下为止。
    for _ in range(100000):
        candidate = cents * step
        if budget_chars(duration, candidate) >= chars:
            return candidate
        cents += 1
    return None


def validate_timeline(timeline: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw_segments = timeline.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        return [], ["timeline.segments 必须是非空数组"]

    parsed: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(raw_segments, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {index} 段不是对象")
            continue
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            errors.append(f"第 {index} 段的 segment_id 必须是非空字符串")
            continue
        if segment_id in seen:
            errors.append(f"segment_id 重复：{segment_id}")
        seen[segment_id] = index
        try:
            start = parse_decimal(item.get("start_s"), f"{segment_id}.start_s")
            end = parse_decimal(item.get("end_s"), f"{segment_id}.end_s")
        except InputError as exc:
            errors.append(str(exc))
            continue
        if end <= start:
            errors.append(
                f"{segment_id}: end_s（{format_decimal(end)}）必须大于 start_s（{format_decimal(start)}）"
            )
            continue
        visual = item.get("visual")
        if not isinstance(visual, str) or not visual.strip():
            errors.append(f"{segment_id}: 缺少 visual（画面说明）")
            continue
        label = item.get("label", "")
        if label is None:
            label = ""
        if not isinstance(label, str):
            errors.append(f"{segment_id}: label 必须是字符串")
            continue
        parsed.append(
            {
                "segment_id": segment_id,
                "start_s": start,
                "end_s": end,
                "duration_s": end - start,
                "visual": visual.strip(),
                "label": label.strip(),
            }
        )

    ordered = sorted(parsed, key=lambda row: (row["start_s"], row["end_s"], row["segment_id"]))
    for previous, current in zip(ordered, ordered[1:]):
        if current["start_s"] < previous["end_s"]:
            errors.append(
                "时间轴重叠："
                f"{previous['segment_id']} [{format_decimal(previous['start_s'])}, {format_decimal(previous['end_s'])}] 与 "
                f"{current['segment_id']} [{format_decimal(current['start_s'])}, {format_decimal(current['end_s'])}]"
                "（首尾相接允许，重叠不允许）"
            )
    return parsed, errors


def segment_hint(
    strategy: str,
    segment_id: str,
    chars: int,
    budget: int,
    duration: Decimal,
    chars_per_sec: Decimal,
    cap: Decimal,
) -> str:
    over_by = chars - budget
    if strategy == "expand":
        return (
            f"{segment_id} 超预算 {over_by} 字。"
            "expand 前先修剪超段：改后必须小于等于预算，不能靠加字或加快语速过关。"
            "若有 must_keep，优先保留该句。"
        )
    if strategy == "trim":
        return (
            f"{segment_id} 超预算 {over_by} 字。"
            f"trim 锁定本次语速 {format_rate(chars_per_sec)} 字／秒，不自动加快；请删减或合并句式。"
            "若有 must_keep，优先保留该句、删别的句子。"
        )
    needed = min_cps_to_fit(chars, duration)
    cap_budget = budget_chars(duration, cap)
    if needed is None:
        return f"{segment_id} 时长无效，无法用加快语速消超。"
    if needed > cap:
        still = chars - cap_budget
        return (
            f"{segment_id} 超预算 {over_by} 字。"
            f"要原样念完至少需要 {format_rate(needed)} 字／秒，已超过上限 {format_rate(cap)} 字／秒。"
            f"在上限语速下预算为 {cap_budget} 字，至少再删 {max(still, 0)} 字。"
            "keep_faster 不会无上限加速。"
        )
    return (
        f"{segment_id} 超预算 {over_by} 字。"
        f"还可删 {over_by} 字，或把语速从 {format_rate(chars_per_sec)} 提到至少 {format_rate(needed)} 字／秒"
        f"（上限 {format_rate(cap)} 字／秒，产品默认，未核实是否适合所有口音）。"
    )


def validate_fit(
    timeline: dict[str, Any],
    fit: dict[str, Any],
    *,
    allow_unassigned: bool = False,
) -> dict[str, Any]:
    timeline_rows, timeline_errors = validate_timeline(timeline)
    errors = list(timeline_errors)
    warnings: list[str] = []

    strategy = fit.get("strategy")
    if strategy not in STRATEGIES:
        errors.append("strategy 必须是 trim、keep_faster 或 expand")
        strategy_text = str(strategy)
    else:
        strategy_text = strategy

    try:
        chars_per_sec = parse_decimal(fit.get("chars_per_sec"), "chars_per_sec")
    except InputError as exc:
        errors.append(str(exc) + "。产品不替用户改语速，缺少或非法时直接失败。")
        chars_per_sec = Decimal("0")
    else:
        if chars_per_sec <= 0:
            errors.append(f"chars_per_sec 必须大于 0，收到 {format_rate(chars_per_sec)}")

    cap_raw = fit.get("max_chars_per_sec_cap", DEFAULT_CAP)
    try:
        cap = parse_decimal(cap_raw, "max_chars_per_sec_cap")
    except InputError as exc:
        errors.append(str(exc))
        cap = DEFAULT_CAP
    else:
        if cap <= 0:
            errors.append(f"max_chars_per_sec_cap 必须大于 0，收到 {format_rate(cap)}")

    ratio_raw = fit.get("expand_short_ratio", DEFAULT_EXPAND_SHORT_RATIO)
    try:
        expand_short_ratio = parse_decimal(ratio_raw, "expand_short_ratio")
    except InputError as exc:
        errors.append(str(exc))
        expand_short_ratio = DEFAULT_EXPAND_SHORT_RATIO
    else:
        if expand_short_ratio <= 0 or expand_short_ratio > 1:
            errors.append(
                f"expand_short_ratio 必须在 (0, 1] 内，收到 {format_decimal(expand_short_ratio)}。"
                "默认 0.7 表示原文少于预算的 70% 时，expand 应把该段补到接近预算。"
            )

    if chars_per_sec > 0 and cap > 0 and chars_per_sec > cap:
        errors.append(
            f"本次语速 {format_rate(chars_per_sec)} 字／秒超过上限 {format_rate(cap)} 字／秒。"
            "keep_faster 不允许无上限加速；请删文案或请用户明确提高上限。"
        )

    must_keep_raw = fit.get("must_keep", [])
    must_keep: list[str] = []
    if must_keep_raw is None:
        must_keep_raw = []
    if not isinstance(must_keep_raw, list):
        errors.append("must_keep 必须是字符串数组")
    else:
        for item in must_keep_raw:
            if not isinstance(item, str) or not item.strip():
                errors.append("must_keep 含空字符串或非字符串")
                continue
            must_keep.append(item.strip())

    unassigned = fit.get("unassigned", "")
    if unassigned is None:
        unassigned = ""
    if not isinstance(unassigned, str):
        errors.append("unassigned 必须是字符串")
        unassigned = ""

    fit_segments = fit.get("segments")
    fit_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(fit_segments, list):
        errors.append("fit.segments 必须是数组")
        fit_segments = []
    for index, item in enumerate(fit_segments, start=1):
        if not isinstance(item, dict):
            errors.append(f"fit 第 {index} 段不是对象")
            continue
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            errors.append(f"fit 第 {index} 段缺少 segment_id")
            continue
        if segment_id in fit_by_id:
            errors.append(f"fit.segment_id 重复：{segment_id}")
            continue
        original = item.get("original")
        revised = item.get("revised")
        if not isinstance(original, str) or not isinstance(revised, str):
            errors.append(f"{segment_id}: original 与 revised 都必须是字符串")
            continue
        fit_by_id[segment_id] = item

    timeline_ids = [row["segment_id"] for row in timeline_rows]
    for segment_id in timeline_ids:
        if segment_id not in fit_by_id:
            errors.append(f"fit 缺少时间轴段落：{segment_id}")
    for segment_id in fit_by_id:
        if segment_id not in timeline_ids:
            errors.append(f"fit 含有时间轴中不存在的段落：{segment_id}")

    rows: list[dict[str, Any]] = []
    # 时间轴不合法时不算预算，避免用坏时长给出假通过。
    if timeline_rows and not timeline_errors and chars_per_sec > 0:
        for source in timeline_rows:
            segment_id = source["segment_id"]
            item = fit_by_id.get(segment_id)
            if item is None:
                continue
            original = item["original"]
            revised = item["revised"]
            chars_original = count_chars(original)
            chars_revised = count_chars(revised)
            budget = budget_chars(source["duration_s"], chars_per_sec)
            over_by = max(0, chars_revised - budget)
            under_by = max(0, budget - chars_revised)
            status = "over" if over_by else "ok"
            short_before = Decimal(chars_original) < Decimal(budget) * expand_short_ratio
            if status == "over":
                fit_action = "over"
            elif chars_original > budget and chars_revised <= budget:
                fit_action = "trimmed_first"
            elif (
                strategy_text == "expand"
                and short_before
                and Decimal(chars_revised) < Decimal(budget) * expand_short_ratio
            ):
                fit_action = "still_short"
            elif chars_revised > chars_original:
                fit_action = "expanded"
            elif short_before:
                fit_action = "still_short"
            else:
                fit_action = "held"
            hints: list[str] = []
            if strategy_text == "expand" and fit_action == "still_short":
                warnings.append(
                    f"{segment_id} 仍偏短：原文 {chars_original} 字，改后 {chars_revised} 字，"
                    f"预算 {budget} 字，阈值 {format_decimal(expand_short_ratio)}。"
                    "expand 应在不超预算的前提下把这段补到接近预算；欠预算本身不导致失败。"
                )
            if status == "over":
                hints.append(
                    segment_hint(
                        strategy_text if strategy_text in STRATEGIES else "trim",
                        segment_id,
                        chars_revised,
                        budget,
                        source["duration_s"],
                        chars_per_sec,
                        cap if cap > 0 else DEFAULT_CAP,
                    )
                )
                errors.append(hints[-1])
            for field, computed in (("chars_original", chars_original), ("chars_revised", chars_revised), ("budget", budget)):
                provided = item.get(field)
                if provided is None:
                    continue
                if isinstance(provided, bool) or not isinstance(provided, (int, float)):
                    warnings.append(f"{segment_id}.{field} 预填值不是数字，已忽略，改用脚本重算的 {computed}")
                    continue
                if int(provided) != computed:
                    warnings.append(
                        f"{segment_id}.{field} 预填 {provided} 与脚本重算 {computed} 不一致，以脚本为准"
                    )
            rows.append(
                {
                    "segment_id": segment_id,
                    "start_s": format_decimal(source["start_s"]),
                    "end_s": format_decimal(source["end_s"]),
                    "duration_s": format_decimal(source["duration_s"]),
                    "label": source["label"],
                    "visual": source["visual"],
                    "original": original,
                    "revised": revised,
                    "note": item.get("note") if isinstance(item.get("note"), str) else "",
                    "chars_original": chars_original,
                    "chars_revised": chars_revised,
                    "budget": budget,
                    "over_by": over_by,
                    "under_by": under_by,
                    "status": status,
                    "fit_action": fit_action,
                    "fit_action_label": FIT_ACTION_LABELS[fit_action],
                    "hints": hints,
                }
            )

    must_keep_hits: list[dict[str, str]] = []
    if rows:
        for phrase in must_keep:
            needle = squash(phrase)
            hits = [row["segment_id"] for row in rows if needle and needle in squash(row["revised"])]
            if not hits:
                errors.append(f"必留句未出现在同一段改后文案中：{phrase}")
                must_keep_hits.append({"phrase": phrase, "segment_id": ""})
            else:
                for segment_id in hits:
                    must_keep_hits.append({"phrase": phrase, "segment_id": segment_id})

    unassigned_chars = count_chars(unassigned)
    if unassigned_chars and not allow_unassigned:
        errors.append(
            f"仍有未分配文案 {unassigned_chars} 字。禁止把超预算句子丢进未分配桶假装通过；"
            "请划入某段，或经用户确认后从 unassigned 删除。中间核对可加 --allow-unassigned。"
        )

    totals = {
        "budget": sum(row["budget"] for row in rows),
        "chars_original": sum(row["chars_original"] for row in rows),
        "chars_revised": sum(row["chars_revised"] for row in rows),
        "over_by": sum(row["over_by"] for row in rows),
        "under_by": sum(row["under_by"] for row in rows),
    }
    # 去重，保留顺序。
    deduped: list[str] = []
    for message in errors:
        if message not in deduped:
            deduped.append(message)

    speed_ok = chars_per_sec > 0 and (cap <= 0 or chars_per_sec <= cap)
    ok = not deduped and bool(rows) and speed_ok and strategy_text in STRATEGIES
    return {
        "ok": ok,
        "title": str(timeline.get("title") or fit.get("title") or "口播对照"),
        "strategy": strategy_text if strategy_text in STRATEGIES else "",
        "chars_per_sec": format_rate(chars_per_sec) if chars_per_sec > 0 else "",
        "max_chars_per_sec_cap": format_rate(cap) if cap > 0 else "",
        "char_count_rule": CHAR_COUNT_RULE,
        "budget_rule": BUDGET_RULE,
        "comfort_source": COMFORT_SOURCE,
        "faster_source": FASTER_SOURCE,
        "cap_source": CAP_SOURCE,
        "range_note": RANGE_NOTE,
        "suggested_comfort_cps": format_rate(SUGGESTED_COMFORT_CPS),
        "suggested_faster_cps": format_rate(SUGGESTED_FASTER_CPS),
        "expand_short_ratio": format_decimal(expand_short_ratio) if expand_short_ratio > 0 else "",
        "allow_unassigned": allow_unassigned,
        "unassigned": unassigned,
        "unassigned_chars": unassigned_chars,
        "must_keep": must_keep,
        "must_keep_hits": must_keep_hits,
        "segments": rows,
        "totals": totals,
        "errors": deduped,
        "warnings": warnings,
    }


def markdown_report(report: dict[str, Any]) -> str:
    result = "通过" if report["ok"] else "未通过"
    lines = [
        "# 口播预算校验报告",
        "",
        f"- 结果: {result}",
        f"- 策略: {report['strategy'] or '（无效）'}",
        f"- 本次语速: {report['chars_per_sec'] or '（缺失）'} 字／秒",
        "- 语速来源: 用户输入，脚本原样使用，未改写",
        f"- 字数规则: {report['char_count_rule']}",
        f"- 预算公式: {report['budget_rule']}",
        f"- 语速上限: {report['max_chars_per_sec_cap'] or '（缺失）'} 字／秒",
        f"- 偏短阈值: {report.get('expand_short_ratio') or '（缺失）'}（原文少于预算×该比例时，expand 建议扩写）",
        f"- 未分配字数: {report['unassigned_chars']}",
        "",
        "## 分段",
        "",
        "| segment_id | start_s | end_s | duration_s | budget | chars_original | chars_revised | over_by | under_by | status | 处理 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    if not report["segments"]:
        lines.append("| （无） |  |  |  |  |  |  |  |  |  |  |")
    for row in report["segments"]:
        lines.append(
            "| {segment_id} | {start_s} | {end_s} | {duration_s} | {budget} | {chars_original} | {chars_revised} | {over_by} | {under_by} | {status} | {fit_action_label} |".format(
                **row
            )
        )
    totals = report["totals"]
    lines.extend(
        [
            "",
            "## 合计",
            "",
            f"- 总预算: {totals['budget']}",
            f"- 原文总字数: {totals['chars_original']}",
            f"- 改后总字数: {totals['chars_revised']}",
            f"- 超预算合计: {totals['over_by']}",
            f"- 欠预算合计: {totals['under_by']}",
            "",
            "## 必留句",
            "",
        ]
    )
    if not report["must_keep"]:
        lines.append("- （无）")
    else:
        hits: dict[str, list[str]] = {}
        for hit in report["must_keep_hits"]:
            hits.setdefault(hit["phrase"], [])
            if hit["segment_id"] and hit["segment_id"] not in hits[hit["phrase"]]:
                hits[hit["phrase"]].append(hit["segment_id"])
        for phrase in report["must_keep"]:
            found = "、".join(hits.get(phrase) or [])
            lines.append(f"- {phrase} → {found or '未找到'}")
    lines.extend(["", "## 未分配", ""])
    if report["unassigned_chars"]:
        lines.append(f"未分配 {report['unassigned_chars']} 字，原文如下：")
        lines.append("")
        lines.append("```")
        lines.append(report["unassigned"])
        lines.append("```")
    else:
        lines.append("无未分配文案。")
    lines.extend(["", "## 问题", ""])
    if report["errors"]:
        lines.extend(f"- {message}" for message in report["errors"])
    else:
        lines.append("- 无")
    lines.extend(["", "## 预填数字", ""])
    if report["warnings"]:
        lines.extend(f"- {message}" for message in report["warnings"])
    else:
        lines.append("- 无不一致")
    lines.extend(
        [
            "",
            "## 语速标签（不是本次自动采用的数值）",
            "",
            f"- {report['comfort_source']}",
            f"- {report['faster_source']}",
            f"- {report['cap_source']}",
            f"- {report['range_note']}",
            "",
        ]
    )
    return "\n".join(lines)


def apply_overrides(fit: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    merged = dict(fit)
    if args.strategy:
        merged["strategy"] = args.strategy
    if args.chars_per_sec is not None:
        merged["chars_per_sec"] = args.chars_per_sec
    if args.max_chars_per_sec_cap is not None:
        merged["max_chars_per_sec_cap"] = args.max_chars_per_sec_cap
    if args.must_keep:
        merged["must_keep"] = list(args.must_keep)
    if getattr(args, "expand_short_ratio", None) is not None:
        merged["expand_short_ratio"] = args.expand_short_ratio
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按段时长 × 语速校验口播字数预算")
    parser.add_argument("--timeline", required=True, help="timeline.json 路径")
    parser.add_argument("--fit", required=True, help="fit.json 路径")
    parser.add_argument("--report", help="写出 validation-report.md")
    parser.add_argument("--json-out", help="写出机器可读 JSON")
    parser.add_argument("--strategy", choices=STRATEGIES)
    parser.add_argument("--chars-per-sec", type=float, dest="chars_per_sec")
    parser.add_argument("--max-chars-per-sec-cap", type=float, dest="max_chars_per_sec_cap")
    parser.add_argument("--must-keep", action="append", dest="must_keep", help="可重复。若给出则替换文件中的 must_keep")
    parser.add_argument(
        "--expand-short-ratio",
        type=float,
        dest="expand_short_ratio",
        help="expand 判定偏短的比例，默认 0.7",
    )
    parser.add_argument("--allow-unassigned", action="store_true", help="未分配文案只报告、不失败")
    parser.add_argument("--emit-fit", help="写出带脚本重算字段的 fit JSON")
    return parser


def emit_fit(report: dict[str, Any], fit: dict[str, Any]) -> dict[str, Any]:
    emitted = {
        "version": fit.get("version", "1.1.0"),
        "expand_short_ratio": report.get("expand_short_ratio"),
        "title": report["title"],
        "strategy": report["strategy"],
        "chars_per_sec": report["chars_per_sec"],
        "max_chars_per_sec_cap": report["max_chars_per_sec_cap"],
        "char_count_rule": report["char_count_rule"],
        "must_keep": report["must_keep"],
        "unassigned": report["unassigned"],
        "segments": [],
    }
    for row in report["segments"]:
        emitted["segments"].append(
            {
                "segment_id": row["segment_id"],
                "original": row["original"],
                "revised": row["revised"],
                "note": row["note"],
                "chars_original": row["chars_original"],
                "chars_revised": row["chars_revised"],
                "budget": row["budget"],
                "over_by": row["over_by"],
                "under_by": row["under_by"],
                "status": row["status"],
                "fit_action": row["fit_action"],
            }
        )
    return emitted


def run(args: argparse.Namespace) -> int:
    try:
        timeline = as_object(load_json(Path(args.timeline)), "timeline")
        fit = apply_overrides(as_object(load_json(Path(args.fit)), "fit"), args)
        report = validate_fit(timeline, fit, allow_unassigned=args.allow_unassigned)
    except InputError as exc:
        print(f"narration-fit: 输入错误：{exc}", file=sys.stderr)
        return 2

    text = markdown_report(report)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.emit_fit and report["segments"]:
        Path(args.emit_fit).write_text(
            json.dumps(emit_fit(report, fit), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    state = "通过" if report["ok"] else "未通过"
    print(
        f"narration-fit: {state} strategy={report['strategy'] or '-'} chars_per_sec={report['chars_per_sec'] or '-'}",
        file=sys.stderr,
    )
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
