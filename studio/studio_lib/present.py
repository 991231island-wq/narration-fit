"""把校验报告收成页面上的总览、步骤、简表和内嵌对照。"""

from __future__ import annotations

import html
from typing import Any

TABLE_HEADERS = ["段", "标签", "时长", "预算", "原文", "改后", "超 / 欠", "状态"]

SOURCE_LABELS = {
    "preset": "预置通过结果 · 未调用模型",
    "model": "模型改稿",
    "recheck": "脚本重验 · 未调用模型",
    "aligned": "已对齐 · 尚未改稿",
}

STEP_ORDER = ("align", "validate", "rewrite", "recheck")
STEP_LABELS = {
    "align": "对齐",
    "validate": "校验",
    "rewrite": "改稿",
    "recheck": "再校验",
}


def delta_text(over_by: int, under_by: int) -> str:
    if over_by:
        return f"超 {over_by}"
    if under_by:
        return f"欠 {under_by}"
    return "正好"


def table_rows(report: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for seg in report.get("segments") or []:
        rows.append(
            [
                seg.get("segment_id") or "",
                seg.get("label") or "",
                f"{seg.get('start_s', '')}–{seg.get('end_s', '')}s",
                seg.get("budget", ""),
                seg.get("chars_original", ""),
                seg.get("chars_revised", ""),
                delta_text(int(seg.get("over_by") or 0), int(seg.get("under_by") or 0)),
                seg.get("fit_action_label") or seg.get("status") or "",
            ]
        )
    return rows


def segment_ids(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    return [str(seg["segment_id"]) for seg in report.get("segments") or [] if seg.get("segment_id")]


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def overview_html(report: dict[str, Any], source: str) -> str:
    totals = report.get("totals") or {}
    ok = bool(report.get("ok"))
    badge = "ok" if ok else "bad"
    state = "通过" if ok else "未通过"
    over = int(totals.get("over_by") or 0)
    under = int(totals.get("under_by") or 0)
    speed = report.get("chars_per_sec") or "—"
    strategy = report.get("strategy") or "—"
    source_label = SOURCE_LABELS.get(source, source or "结果")
    title = report.get("title") or "口播对照"
    stats = [
        ("总预算", totals.get("budget", 0), False),
        ("原文", totals.get("chars_original", 0), False),
        ("改后", totals.get("chars_revised", 0), False),
        ("超", over, over > 0),
        ("欠", under, False),
        ("未分配", report.get("unassigned_chars", 0), int(report.get("unassigned_chars") or 0) > 0),
    ]
    cells = []
    for label, value, danger in stats:
        klass = "nf-stat danger" if danger else "nf-stat"
        cells.append(f'<div class="{klass}"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></div>')
    return (
        '<section class="nf-overview">'
        '<div class="nf-overview-top">'
        "<div>"
        '<p class="nf-kicker">结果总览</p>'
        f"<h2>{_esc(title)}</h2>"
        "</div>"
        '<div class="nf-badges">'
        f'<span class="nf-badge {badge}">{state}</span>'
        f'<span class="nf-badge">{_esc(source_label)}</span>'
        f'<span class="nf-badge">{_esc(strategy)}</span>'
        f'<span class="nf-badge">{_esc(speed)} 字／秒</span>'
        "</div>"
        "</div>"
        f'<div class="nf-stats">{"".join(cells)}</div>'
        "</section>"
    )


def steps_html(
    phase: str,
    *,
    segment_id: str = "",
    failed: bool = False,
    caption: str = "",
) -> str:
    """phase: align / validate / rewrite / recheck / done。"""
    labels = dict(STEP_LABELS)
    if segment_id and phase in {"rewrite", "done", "recheck"}:
        labels["rewrite"] = f"改稿 · {segment_id}" if phase == "rewrite" else STEP_LABELS["rewrite"]
    if phase == "rewrite" and segment_id:
        labels["rewrite"] = f"改稿 · {segment_id}"

    if phase == "idle":
        reached = -1
        active = ""
    elif phase == "done":
        reached = len(STEP_ORDER)
        active = ""
    else:
        reached = STEP_ORDER.index(phase) if phase in STEP_ORDER else 0
        active = phase if phase in STEP_ORDER else ""

    items = []
    for index, key in enumerate(STEP_ORDER, start=1):
        if failed and key == active:
            klass = "error"
        elif phase == "done" or index - 1 < reached:
            klass = "done"
        elif key == active:
            klass = "active"
        else:
            klass = "wait"
        items.append(
            f'<li class="{klass}"><span class="idx">0{index}</span><strong>{_esc(labels[key])}</strong></li>'
        )
    note = f'<p class="nf-rail-note">{_esc(caption)}</p>' if caption else ""
    return f'<ol class="nf-rail">{"".join(items)}</ol>{note}'


EMPTY_DIFF = (
    '<div id="nf-diff"><p class="empty">对照会在改稿结束后出现在这里。</p></div>'
)


def diff_preview(page: str) -> str:
    """嵌入 render_diff 生成的对照页正文。样式由工作室 CSS 画在 #nf-diff 里。"""
    start = page.find("<main")
    end = page.rfind("</main>")
    if start >= 0 and end > start:
        body = page[start : end + len("</main>")]
    else:
        body = f"<p>{_esc(page[:500])}</p>" if page else "<p class='empty'>还没有对照。</p>"
    return f'<div id="nf-diff">{body}</div>'


WIZARD_STEPS = (
    (1, "准备材料", "填好时间轴和口播，点「下一步」。"),
    (2, "怎么改", "选好策略和语速，点「开始改稿」。"),
    (3, "改稿中", "正在对齐、校验和改稿，结束后会自动打开第 4 步。"),
    (4, "看结果", "看总览和对照。要重跑，点「再改一次」。"),
)


def clamp_wizard_target(target: int, max_reached: int) -> int | None:
    """未完成的步骤不能跳。能去才返回步号。"""
    try:
        step = int(target)
        unlocked = int(max_reached)
    except (TypeError, ValueError):
        return None
    if step < 1 or step > 4 or step > unlocked:
        return None
    return step


def materials_present(timeline: str, draft: str) -> bool:
    return bool((timeline or "").strip()) and bool((draft or "").strip())


def wizard_caption(step: int, extra: str = "") -> str:
    step = min(4, max(1, int(step)))
    _, name, action = WIZARD_STEPS[step - 1]
    text = f"现在是第 {step} 步，共 4 步 · {name}。{action}"
    if extra:
        text = f"{text} {extra}"
    return text


def segment_fields(state: dict[str, Any] | None, segment_id: str | None) -> tuple[str, str, str]:
    """返回 revised、original、给编辑区看的一行说明。"""
    if not state or not segment_id:
        return "", "", "改稿跑完后，在这里选一段改「改后」，再重验。"
    report = state.get("report") or {}
    fit = state.get("fit") or {}
    row = None
    for seg in report.get("segments") or []:
        if seg.get("segment_id") == segment_id:
            row = seg
            break
    revised = ""
    original = ""
    if row is not None:
        revised = row.get("revised") or ""
        original = row.get("original") or ""
    else:
        for seg in fit.get("segments") or []:
            if seg.get("segment_id") == segment_id:
                revised = seg.get("revised") or ""
                original = seg.get("original") or ""
                break
    if row is None:
        return revised, original, f"{segment_id} · 还没有脚本数字，重验后会补上。"
    label = f" · {row['label']}" if row.get("label") else ""
    meta = (
        f"{row['segment_id']}{label} · {row['start_s']}–{row['end_s']} 秒"
        f" · 预算 {row['budget']} · 原文 {row['chars_original']} · 改后 {row['chars_revised']}"
        f" · {delta_text(int(row['over_by']), int(row['under_by']))}"
        f" · {row.get('fit_action_label') or row.get('status')}"
    )
    keeps = report.get("must_keep") or []
    if keeps:
        meta += " · 必留句：" + "；".join(keeps)
    return revised, original, meta


def status_block(
    *,
    title: str,
    lead: str,
    lines: list[str] | None = None,
    errors: list[str] | None = None,
    advice: str = "",
) -> str:
    parts = [f"### {title}", "", lead]
    if lines:
        parts.extend(["", "**过程**", ""])
        parts.extend(f"- {line}" for line in lines[-16:])
    if errors:
        parts.extend(["", "**问题**", ""])
        shown = errors[:12]
        parts.extend(f"- {item}" for item in shown)
        if len(errors) > 12:
            parts.append(f"- …另有 {len(errors) - 12} 条")
    if advice:
        parts.extend(["", "**怎么修**", "", advice])
    return "\n".join(parts)


def result_status(report: dict[str, Any], lines: list[str], *, source: str, headline: str | None = None) -> str:
    from studio_lib.errors import advice_for_report

    totals = report.get("totals") or {}
    ok = bool(report.get("ok"))
    speed = report.get("chars_per_sec") or "—"
    strategy = report.get("strategy") or "—"
    lead = (
        f"{SOURCE_LABELS.get(source, source)} · {strategy} · {speed} 字／秒。"
        f"总预算 {totals.get('budget', 0)}，原文 {totals.get('chars_original', 0)}，"
        f"改后 {totals.get('chars_revised', 0)}，超 {totals.get('over_by', 0)}，欠 {totals.get('under_by', 0)}。"
        "这些数字是本地脚本刚重算的。"
    )
    errors = list(report.get("errors") or [])
    warnings = list(report.get("warnings") or [])
    extra = list(lines)
    if warnings:
        extra.extend(f"提示：{item}" for item in warnings[:6])
    if ok:
        title = headline or "预算通过"
        advice = ""
    else:
        title = headline or "预算未通过"
        advice = advice_for_report(errors, strategy=str(report.get("strategy") or ""))
    return status_block(title=title, lead=lead, lines=extra, errors=errors, advice=advice)


HERO_HTML = """
<header class="nf-hero">
  <p class="nf-kicker">创空间 · 口播</p>
  <h1>口播对轴改稿</h1>
  <p class="nf-lede">成片时间已经定了，口播却对不齐画面。按语速给每段一个字数预算，把现有稿子改到能对着画面念完。</p>
  <ol class="nf-flow">
    <li><span>时间轴稿</span></li>
    <li><span>选策略</span></li>
    <li><span>改稿</span></li>
    <li><span>对照下载</span></li>
  </ol>
</header>
""".strip()


FOOTER_HTML = """
<footer class="nf-footer">
  <p>创空间 · 口播对轴改稿。示例文案仅供演示。字数预算是检查值，不等于真实发声时长。不读视频，也不做配音。</p>
  <p>推理只在点「开始改稿」时发生：<code>https://api-inference.modelscope.cn/v1</code> · <code>Qwen/Qwen3.5-35B-A3B</code>。令牌只读环境变量 <code>MODELSCOPE_API_TOKEN</code>，不会写进页面或代码。</p>
</footer>
""".strip()
