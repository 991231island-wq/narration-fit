"""第 1 步的分段材料。改稿前收成时间轴 JSON 和按段口播，不改校验脚本。"""

from __future__ import annotations

import json
from typing import Any

from studio_lib.align_and_rewrite import _split_paragraphs
from studio_lib.pipeline import EXAMPLES

VISUAL_FALLBACK = "（这段还没写画面）"


def parse_seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _fmt(value: float) -> str:
    number = round(float(value), 2)
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def sort_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(segments, key=lambda row: (float(row["start_s"]), float(row["end_s"])))


def _copy(segments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    copied = []
    for row in segments or []:
        copied.append(
            {
                "start_s": float(row.get("start_s") or 0),
                "end_s": float(row.get("end_s") or 0),
                "narration": str(row.get("narration") or ""),
                "visual": str(row.get("visual") or ""),
                "label": str(row.get("label") or ""),
            }
        )
    return copied


def overlap_message(segments: list[dict[str, Any]]) -> str:
    ordered = sort_segments(segments)
    for previous, current in zip(ordered, ordered[1:]):
        if float(current["start_s"]) < float(previous["end_s"]) - 0.001:
            return (
                f"{_fmt(previous['start_s'])}–{_fmt(previous['end_s'])} 秒和 "
                f"{_fmt(current['start_s'])}–{_fmt(current['end_s'])} 秒重叠了。"
            )
    return ""


def _index_of(segments: list[dict[str, Any]], start_s: float, end_s: float) -> int | None:
    for index, row in enumerate(segments):
        if abs(float(row["start_s"]) - start_s) < 0.001 and abs(float(row["end_s"]) - end_s) < 0.001:
            return index
    return 0 if segments else None


def add_cut(segments: list[dict[str, Any]] | None, playhead: Any) -> tuple[list[dict[str, Any]], int | None, str]:
    """在播放位置加一个分段点。返回新列表、要打开的下标、说明。"""
    moment = parse_seconds(playhead)
    current = _copy(segments)
    if moment is None:
        return current, None, "播放位置要是大于 0 的秒数。"
    if moment <= 0:
        return current, None, "先填一个大于 0 的秒数。"

    for index, row in enumerate(current):
        start = float(row["start_s"])
        end = float(row["end_s"])
        if abs(moment - start) < 0.05 or abs(moment - end) < 0.05:
            return current, index, "这个时间和已有分段点太近，没有新加。"
        if start < moment < end:
            left = dict(row)
            left["end_s"] = moment
            right = {
                "start_s": moment,
                "end_s": end,
                "narration": "",
                "visual": "",
                "label": "",
            }
            current[index : index + 1] = [left, right]
            current = sort_segments(current)
            active = _index_of(current, moment, end)
            return current, active, f"已在 {_fmt(moment)} 秒切开。"

    if not current:
        current = [
            {
                "start_s": 0.0,
                "end_s": moment,
                "narration": "",
                "visual": "",
                "label": "",
            }
        ]
        return current, 0, f"已加上第 1 段：0–{_fmt(moment)} 秒。"

    last_end = max(float(row["end_s"]) for row in current)
    if moment > last_end + 0.049:
        current.append(
            {
                "start_s": last_end,
                "end_s": moment,
                "narration": "",
                "visual": "",
                "label": "",
            }
        )
        current = sort_segments(current)
        active = _index_of(current, last_end, moment)
        return current, active, f"已加上一段：{_fmt(last_end)}–{_fmt(moment)} 秒。"

    return current, None, "这个时间切不开。直接改起止秒。"


def apply_editor(
    segments: list[dict[str, Any]] | None,
    active: Any,
    start: Any,
    end: Any,
    visual: str,
    narration: str,
) -> tuple[list[dict[str, Any]], int | None, str]:
    """把正在编辑的一段写回列表。时间不合法时列表保持原样。"""
    current = _copy(segments)
    start_s = parse_seconds(start)
    end_s = parse_seconds(end)
    index = _active_index(active, len(current))
    blank_editor = start_s is None and end_s is None and not (narration or "").strip() and not (visual or "").strip()
    if index is None and blank_editor:
        return current, None, ""
    if start_s is None or end_s is None:
        return current, index, "起止时间要是不小于 0 的数字。"
    if end_s <= start_s:
        return current, index, "结束秒要大于开始秒。"

    row = {
        "start_s": start_s,
        "end_s": end_s,
        "visual": (visual or "").strip(),
        "narration": (narration or "").strip(),
        "label": current[index].get("label", "") if index is not None else "",
    }
    proposed = list(current)
    if index is None:
        proposed.append(row)
    else:
        proposed[index] = row
    proposed = sort_segments(proposed)
    problem = overlap_message(proposed)
    if problem:
        return current, index, problem
    new_index = _index_of(proposed, start_s, end_s)
    return proposed, new_index, "这一段已记下。"


def delete_segment(
    segments: list[dict[str, Any]] | None,
    active: Any,
) -> tuple[list[dict[str, Any]], int | None, str]:
    current = _copy(segments)
    index = _active_index(active, len(current))
    if index is None:
        return current, None, "先在列表里点开要删的一段。"
    current.pop(index)
    if not current:
        return [], None, "分段已清空。"
    new_index = min(index, len(current) - 1)
    return current, new_index, f"已删掉一段，还剩 {len(current)} 段。"


def _active_index(active: Any, count: int) -> int | None:
    if active is None or active == "":
        return None
    try:
        index = int(active)
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= count:
        return None
    return index


def segments_ready(segments: list[dict[str, Any]] | None) -> bool:
    rows = _copy(segments)
    if not rows:
        return False
    if overlap_message(rows):
        return False
    for row in rows:
        if float(row["end_s"]) <= float(row["start_s"]):
            return False
        if not row["narration"].strip():
            return False
    return True


def readiness_note(segments: list[dict[str, Any]] | None) -> str:
    rows = _copy(segments)
    if not rows:
        return "还没有分段。"
    problem = overlap_message(rows)
    if problem:
        return problem
    missing = [str(index) for index, row in enumerate(rows, start=1) if not row["narration"].strip()]
    if missing:
        return "还没写口播：" + "、".join(f"第 {item} 段" for item in missing) + "。"
    return "口播已齐。"


def segment_table(segments: list[dict[str, Any]] | None) -> list[list[str]]:
    rows = []
    for index, row in enumerate(_copy(segments), start=1):
        duration = float(row["end_s"]) - float(row["start_s"])
        text = row["narration"].strip()
        if not text:
            preview = "还没写"
        elif len(text) > 28:
            preview = text[:28] + "…"
        else:
            preview = text
        rows.append([f"第 {index} 段", _fmt(row["start_s"]), _fmt(row["end_s"]), _fmt(duration), preview])
    return rows


def segment_choices(segments: list[dict[str, Any]] | None) -> list[tuple[str, str]]:
    choices = []
    for index, row in enumerate(_copy(segments)):
        status = "已写" if row["narration"].strip() else "还没写"
        label = f"第 {index + 1} 段 · {_fmt(row['start_s'])}–{_fmt(row['end_s'])} 秒 · {status}"
        choices.append((label, str(index)))
    return choices


def editor_values(segments: list[dict[str, Any]] | None, active: Any) -> tuple[float | None, float | None, str, str]:
    rows = _copy(segments)
    index = _active_index(active, len(rows))
    if index is None:
        return None, None, "", ""
    row = rows[index]
    return float(row["start_s"]), float(row["end_s"]), row["visual"], row["narration"]


def build_job_inputs(title: str, segments: list[dict[str, Any]] | None) -> tuple[str, str]:
    """收成脚本认识的时间轴 JSON，以及按段用空行隔开的口播。"""
    rows = sort_segments(_copy(segments))
    timeline_rows = []
    paragraphs = []
    for index, row in enumerate(rows, start=1):
        narration = " ".join(part.strip() for part in row["narration"].splitlines() if part.strip())
        paragraphs.append(narration)
        visual = row["visual"].strip() or VISUAL_FALLBACK
        label = row["label"].strip() or f"第{index}段"
        timeline_rows.append(
            {
                "segment_id": f"s{index}",
                "start_s": float(row["start_s"]),
                "end_s": float(row["end_s"]),
                "label": label,
                "visual": visual,
            }
        )
    timeline = {
        "title": (title or "").strip() or "口播对照",
        "segments": timeline_rows,
    }
    return json.dumps(timeline, ensure_ascii=False), "\n\n".join(paragraphs)


def example_materials() -> tuple[str, list[dict[str, Any]], str, float, str, float]:
    """示例视频不附文件。时间和口播来自仓库里的馒头示例。"""
    timeline = json.loads((EXAMPLES / "demo-timeline.json").read_text(encoding="utf-8"))
    draft = (EXAMPLES / "demo-draft-overlong.txt").read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(draft)
    segments = []
    for item, paragraph in zip(timeline["segments"], paragraphs):
        segments.append(
            {
                "start_s": float(item["start_s"]),
                "end_s": float(item["end_s"]),
                "narration": paragraph,
                "visual": str(item.get("visual") or ""),
                "label": str(item.get("label") or ""),
            }
        )
    must_keep = "气孔是发酵留下的，不是后来吹进去的"
    return str(timeline.get("title") or ""), segments, "trim", 4.7, must_keep, 0.7
