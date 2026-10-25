"""调用魔搭 OpenAI 兼容推理：全文对齐 + 按段改稿。

令牌只从环境变量 MODELSCOPE_API_TOKEN 读取，禁止写入代码、日志或产物。
字数与预算一律由 scripts 计算，本模块不口算验收。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from studio_lib.errors import TOKEN_ENV, ModelJSONError, TokenMissingError

DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3.5-35B-A3B"


def require_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise TokenMissingError(f"未找到环境变量 {TOKEN_ENV}")
    return token


def make_client(*, base_url: str = DEFAULT_BASE_URL):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai。请先运行 python -m pip install -r studio/requirements.txt") from exc
    return OpenAI(api_key=require_token(), base_url=base_url)


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型回复里抽出第一个 JSON 对象。允许围栏代码块。"""
    raw = (text or "").strip()
    if not raw:
        raise ModelJSONError("模型返回空内容")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ModelJSONError("回复中找不到 JSON 对象") from None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelJSONError(f"JSON 解析失败：{exc.msg}") from exc

    if not isinstance(value, dict):
        raise ModelJSONError("顶层必须是 JSON 对象")
    return value


def chat_json(
    client,
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_retries: int = 1,
) -> dict[str, Any]:
    """发 Chat Completions，要求只返回 JSON；解析失败再重试 max_retries 次。"""
    last_error: Exception | None = None
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    attempts = max_retries + 1
    for attempt in range(attempts):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""
        try:
            return _extract_json_object(content)
        except ModelJSONError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "上一次输出无法解析为 JSON（"
                        f"{exc}）。请只输出一个合法 JSON 对象，不要 Markdown，不要解释。"
                    ),
                },
            ]
    assert last_error is not None
    raise ModelJSONError(f"模型 JSON 解析失败（已重试）：{last_error}") from last_error


def _split_paragraphs(draft: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", (draft or "").strip()) if p.strip()]
    skip_prefix = ("示例文案", "仅供演示")
    cleaned = []
    for part in parts:
        if any(part.startswith(prefix) or part == prefix for prefix in skip_prefix) or part.startswith("示例文案为本"):
            continue
        cleaned.append(part)
    return cleaned or parts


def try_paragraph_align(
    *,
    timeline: dict[str, Any],
    draft: str,
    strategy: str,
    chars_per_sec: float,
    must_keep: list[str],
    expand_short_ratio: float = 0.7,
) -> dict[str, Any] | None:
    """段数与空行分段数一致时，按顺序硬切，避免模型把正文丢进 unassigned。"""
    segs = timeline.get("segments") or []
    paras = _split_paragraphs(draft)
    if not segs or len(paras) != len(segs):
        return None
    expected_ids = [str(seg.get("segment_id")) for seg in segs]
    segments = []
    for sid, para in zip(expected_ids, paras):
        segments.append(
            {
                "segment_id": sid,
                "original": para,
                "revised": para,
                "note": "按段落顺序对齐（未改写）",
            }
        )
    return {
        "version": "1.0.0",
        "title": str(timeline.get("title") or "口播对照"),
        "strategy": strategy,
        "chars_per_sec": chars_per_sec,
        "max_chars_per_sec_cap": 6.0,
        "expand_short_ratio": expand_short_ratio,
        "must_keep": list(must_keep),
        "unassigned": "",
        "segments": segments,
    }


def align_draft_to_timeline(
    client,
    *,
    timeline: dict[str, Any],
    draft: str,
    strategy: str,
    chars_per_sec: float,
    must_keep: list[str],
    expand_short_ratio: float = 0.7,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """把全文切到各 segment，生成初始 fit（original=切片，revised=同文）。"""
    heuristic = try_paragraph_align(
        timeline=timeline,
        draft=draft,
        strategy=strategy,
        chars_per_sec=chars_per_sec,
        must_keep=must_keep,
        expand_short_ratio=expand_short_ratio,
    )
    if heuristic is not None:
        return heuristic

    segments_meta = []
    for seg in timeline.get("segments") or []:
        segments_meta.append(
            {
                "segment_id": seg.get("segment_id"),
                "start_s": seg.get("start_s"),
                "end_s": seg.get("end_s"),
                "label": seg.get("label") or "",
                "visual": seg.get("visual") or "",
            }
        )
    expected_ids = [item["segment_id"] for item in segments_meta]

    system = (
        "你是口播对轴助手。任务是把用户给的全文按时间轴段落切开，写入 original。"
        "不要改写措辞，不要删句装看不见。优先把正文全部分到各段；只有标题、说明、真对不上的句子才进 unassigned。"
        "四段口播对四段时间轴时按顺序切，不要把整段正文丢进 unassigned。"
        "只输出一个 JSON 对象，不要其它文字。"
    )
    user = json.dumps(
        {
            "task": "align",
            "title": timeline.get("title") or "",
            "strategy": strategy,
            "chars_per_sec": chars_per_sec,
            "must_keep": must_keep,
            "expand_short_ratio": expand_short_ratio,
            "timeline_segments": segments_meta,
            "draft": draft,
            "output_schema": {
                "version": "1.0.0",
                "title": "string",
                "strategy": strategy,
                "chars_per_sec": chars_per_sec,
                "must_keep": must_keep,
                "unassigned": "没划入任何段的原文，可为空字符串",
                "segments": [
                    {
                        "segment_id": "必须覆盖时间轴全部 id，且不得多出",
                        "original": "该段原文切片，保持原句",
                        "revised": "本步与 original 完全相同",
                        "note": "可选说明",
                    }
                ],
            },
            "rules": [
                "original 必须是 draft 的原文切片，禁止先改写再当原文",
                "本步 revised 必须等于 original",
                f"segments 的 segment_id 集合必须恰好等于：{expected_ids}",
                "禁止把超预算句子丢进 unassigned 假装过关；对不上才放 unassigned",
                "不要输出字数、budget、status——那些由脚本计算",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

    data = chat_json(client, system=system, user=user, model=model, temperature=0.1)
    return _normalize_aligned_fit(
        data,
        timeline=timeline,
        strategy=strategy,
        chars_per_sec=chars_per_sec,
        must_keep=must_keep,
        expand_short_ratio=expand_short_ratio,
        expected_ids=expected_ids,
    )


def _normalize_aligned_fit(
    data: dict[str, Any],
    *,
    timeline: dict[str, Any],
    strategy: str,
    chars_per_sec: float,
    must_keep: list[str],
    expand_short_ratio: float,
    expected_ids: list[str],
) -> dict[str, Any]:
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        raise ModelJSONError("对齐结果缺少 segments 数组")

    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        sid = item.get("segment_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        original = item.get("original")
        if not isinstance(original, str):
            original = ""
        revised = item.get("revised")
        if not isinstance(revised, str) or revised == "":
            revised = original
        note = item.get("note") if isinstance(item.get("note"), str) else ""
        by_id[sid.strip()] = {
            "segment_id": sid.strip(),
            "original": original,
            "revised": revised,
            "note": note,
        }

    missing = [sid for sid in expected_ids if sid not in by_id]
    if missing:
        raise ModelJSONError(f"对齐结果缺少段落：{', '.join(missing)}")

    extras = [sid for sid in by_id if sid not in expected_ids]
    if extras:
        orphan_bits = []
        for sid in extras:
            orphan_bits.append(by_id[sid]["original"])
            del by_id[sid]
        extra_text = "\n".join(text for text in orphan_bits if text)
    else:
        extra_text = ""

    unassigned = data.get("unassigned", "")
    if not isinstance(unassigned, str):
        unassigned = ""
    if extra_text:
        unassigned = (unassigned + "\n" + extra_text).strip()

    segments = []
    for sid in expected_ids:
        row = by_id[sid]
        segments.append(
            {
                "segment_id": sid,
                "original": row["original"],
                "revised": row["original"],
                "note": row.get("note") or "对齐切片，尚未改稿",
            }
        )

    return {
        "version": str(data.get("version") or "1.0.0"),
        "title": str(data.get("title") or timeline.get("title") or "口播对照"),
        "strategy": strategy,
        "chars_per_sec": chars_per_sec,
        "max_chars_per_sec_cap": float(data.get("max_chars_per_sec_cap") or 6.0),
        "expand_short_ratio": expand_short_ratio,
        "must_keep": list(must_keep),
        "unassigned": unassigned,
        "segments": segments,
    }


def rewrite_segment(
    client,
    *,
    segment_id: str,
    visual: str,
    label: str,
    budget: int,
    chars_current: int,
    strategy: str,
    must_keep: list[str],
    original: str,
    revised: str,
    expand_short_ratio: float = 0.7,
    model: str = DEFAULT_MODEL,
) -> dict[str, str]:
    """对单段改稿。返回 {revised, note}。字数验收仍由脚本做。"""
    if strategy == "trim":
        goal = (
            f"超预算：当前约 {chars_current} 字，预算 {budget} 字。"
            "trim：删减或合并句式，保住 must_keep；不要靠加快语速过关。"
            "改后字数（去空白、标点计入）必须 ≤ 预算。"
        )
    elif strategy == "keep_faster":
        goal = (
            f"超预算：当前约 {chars_current} 字，预算 {budget} 字（已按用户选定更快语速算）。"
            "keep_faster：尽量留信息；仍超则再删，但必须压回预算内。"
            "不要无上限加速。"
        )
    else:
        if chars_current > budget:
            goal = (
                f"本段已超预算（约 {chars_current} 字 / 预算 {budget}）。"
                "expand 规则：先修剪回预算内，不要加字掩盖超段。"
            )
        else:
            goal = (
                f"本段偏短（约 {chars_current} 字 / 预算 {budget}，偏短阈值 {expand_short_ratio}）。"
                "expand：只补画面里看得到的过程、过渡，以及原文已压缩的信息；"
                "不换文风、不编造与画面或原文无关的事实；补完仍须 ≤ 预算。"
            )

    system = (
        "你是口播改稿助手。只改写指定段落的 revised。"
        "字数规则：去掉空白后按字符计数，标点计入——但你不要自己口算验收，尽量贴近预算即可。"
        "must_keep 中的每一句必须完整出现在本段 revised 里（标点一致，可去掉空白匹配）。"
        "只输出一个 JSON 对象：{\"revised\": \"...\", \"note\": \"...\"}。"
    )
    user = json.dumps(
        {
            "segment_id": segment_id,
            "label": label,
            "visual": visual,
            "budget": budget,
            "chars_current": chars_current,
            "strategy": strategy,
            "must_keep": must_keep,
            "expand_short_ratio": expand_short_ratio,
            "original": original,
            "current_revised": revised,
            "goal": goal,
        },
        ensure_ascii=False,
        indent=2,
    )
    data = chat_json(client, system=system, user=user, model=model, temperature=0.25)
    new_revised = data.get("revised")
    if not isinstance(new_revised, str) or not new_revised.strip():
        raise ModelJSONError(f"{segment_id}: 改稿结果缺少 revised 字符串")
    note = data.get("note") if isinstance(data.get("note"), str) else ""
    return {"revised": new_revised, "note": note}


def segments_needing_rewrite(
    report: dict[str, Any],
    *,
    strategy: str,
    expand_short_ratio: float,
) -> list[dict[str, Any]]:
    """根据校验报告选出需要改稿的段。"""
    need: list[dict[str, Any]] = []
    for row in report.get("segments") or []:
        budget = int(row["budget"])
        chars_revised = int(row["chars_revised"])
        chars_original = int(row["chars_original"])
        if strategy in ("trim", "keep_faster"):
            if chars_revised > budget:
                need.append(row)
        elif strategy == "expand":
            if chars_revised > budget or chars_original < budget * expand_short_ratio:
                need.append(row)
    return need
