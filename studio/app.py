#!/usr/bin/env python3
"""口播对轴改稿 · 本地 Gradio 工作室。

四步向导：准备材料 → 选怎么改 → 改稿 → 对照下载。
在仓库根目录启动：python studio/app.py
监听 0.0.0.0:7860。令牌只读环境变量 MODELSCOPE_API_TOKEN。
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import warnings
from pathlib import Path
from typing import Any

STUDIO_DIR = Path(__file__).resolve().parent
if str(STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(STUDIO_DIR))

import gradio as gr

from studio_lib.errors import humanize
from studio_lib.materials import (
    add_cut,
    apply_editor,
    delete_segment,
    editor_values,
    example_materials,
    readiness_note,
    segment_choices,
    segment_table,
    segments_ready,
    build_job_inputs,
)
from studio_lib.pipeline import (
    InputError,
    RewritesBlocked,
    revalidate,
    stream_pipeline,
)
from studio_lib.present import (
    EMPTY_DIFF,
    FOOTER_HTML,
    HERO_HTML,
    TABLE_HEADERS,
    clamp_wizard_target,
    diff_preview,
    overview_html,
    result_status,
    segment_fields,
    segment_ids,
    status_block,
    steps_html,
    table_rows,
    wizard_caption,
)

THEME_PATH = STUDIO_DIR / "theme.css"

VIDEO_RELABEL_HEAD = """
<script>
(function () {
  const pairs = [
    ["将视频文件拖放到此处以上传", "把视频拖到这里"],
    ["将视频拖放到此处", "把视频拖到这里"],
    ["点击上传", "选择本地视频"],
    ["Click to Upload", "选择本地视频"],
    ["Drop Video Here", "把视频拖到这里"],
    ["- or -", ""],
    ["- 或 -", ""]
  ];
  function relabel() {
    const root = document.getElementById("clip-video");
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      let text = node.textContent;
      for (const pair of pairs) {
        if (text.includes(pair[0])) text = text.split(pair[0]).join(pair[1]);
      }
      const trimmed = text.trim();
      if (trimmed === "or" || trimmed === "-" || trimmed === "或") text = "";
      if (text !== node.textContent) node.textContent = text;
    }
  }
  function start() {
    relabel();
    new MutationObserver(relabel).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
</script>
"""

EMPTY_OVERVIEW = """
<section class="nf-overview">
  <h2>这次没有对照稿</h2>
  <p>原因在上面。</p>
</section>
""".strip()


def _fresh_state(state: dict | None) -> dict:
    data = dict(state or {})
    data["wizard_step"] = int(data.get("wizard_step") or 1)
    data["max_reached"] = int(data.get("max_reached") or 1)
    data["segments"] = list(data.get("segments") or [])
    data["title"] = str(data.get("title") or "")
    active = data.get("active")
    data["active"] = None if active is None or active == "" else active
    return data


def _button_updates(step: int, max_reached: int):
    updates = []
    for number in (1, 2, 3, 4):
        if number == step:
            updates.append(gr.update(interactive=True, variant="primary"))
        elif number <= max_reached:
            updates.append(gr.update(interactive=True, variant="secondary"))
        else:
            updates.append(gr.update(interactive=False, variant="secondary"))
    return updates


def _panels(step: int):
    return tuple(gr.update(visible=(number == step)) for number in (1, 2, 3, 4))


def view(state: dict, extra: str = "", content: dict | None = None):
    """顶栏、四个面板的显隐，以及可选的结果区更新。"""
    step = int(state.get("wizard_step") or 1)
    max_reached = int(state.get("max_reached") or 1)
    content = content or {}

    def pick(key):
        if key in content:
            return content[key]
        return gr.update()

    return (
        *_button_updates(step, max_reached),
        wizard_caption(step, extra),
        *_panels(step),
        pick("rail"),
        pick("run_status"),
        pick("result_md"),
        pick("overview"),
        pick("table"),
        pick("segment"),
        pick("revised"),
        pick("original"),
        pick("meta"),
        pick("preview"),
        pick("html_file"),
        pick("fit_file"),
        pick("report_file"),
        pick("step1_note"),
        dict(state),
    )


def _result_content(result, lines, *, status: str | None = None, segment_id: str | None = None, phase: str = "done", failed: bool = False, caption: str = ""):
    ids = segment_ids(result.report)
    chosen = segment_id if segment_id in ids else (ids[0] if ids else None)
    revised, original, meta = segment_fields(result.state(), chosen)
    if status is None:
        status = result_status(result.report, lines, source=result.source)
    return {
        "rail": steps_html(phase, segment_id=segment_id or "", failed=failed, caption=caption),
        "run_status": "已结束。",
        "result_md": status,
        "overview": overview_html(result.report, result.source),
        "table": table_rows(result.report),
        "segment": gr.update(choices=ids, value=chosen),
        "revised": revised,
        "original": original,
        "meta": meta,
        "preview": diff_preview(result.html_preview),
        "html_file": result.html_path,
        "fit_file": result.fit_path,
        "report_file": result.report_path,
    }


def _empty_result_content(status: str, *, phase: str, failed: bool, caption: str):
    return {
        "rail": steps_html(phase, failed=failed, caption=caption),
        "run_status": "没有对照。",
        "result_md": status,
        "overview": EMPTY_OVERVIEW,
        "table": [],
        "segment": gr.update(choices=[], value=None),
        "revised": "",
        "original": "",
        "meta": "没有分段。",
        "preview": EMPTY_DIFF,
        "html_file": None,
        "fit_file": None,
        "report_file": None,
    }


def _remember(state: dict, result) -> dict:
    data = result.state()
    data["wizard_step"] = state["wizard_step"]
    data["max_reached"] = state["max_reached"]
    data["segments"] = state.get("segments") or []
    data["active"] = state.get("active")
    data["title"] = state.get("title") or ""
    return data


def _editor_pack(segments, active):
    start, end, visual, narration = editor_values(segments, active)
    value = None if active is None or not segments else str(int(active))
    return (
        segment_table(segments),
        gr.update(choices=segment_choices(segments), value=value),
        start,
        end,
        visual,
        narration,
        gr.update(interactive=segments_ready(segments)),
    )


def go_step(target: int, state):
    state = _fresh_state(state)
    dest = clamp_wizard_target(target, state["max_reached"])
    extra = ""
    if dest is None:
        extra = "还没解锁。"
    else:
        state["wizard_step"] = dest
    return view(state, extra=extra)


def go_back(state):
    state = _fresh_state(state)
    state["wizard_step"] = 1
    return view(state)


def _commit(state, title, active, start, end, visual, narration):
    state = _fresh_state(state)
    state["title"] = (title or "").strip()
    segments, new_active, message = apply_editor(state["segments"], active, start, end, visual, narration)
    state["segments"] = segments
    state["active"] = new_active
    return state, message


def go_next(state, title, active, start, end, visual, narration):
    state, message = _commit(state, title, active, start, end, visual, narration)
    if not segments_ready(state["segments"]):
        state["wizard_step"] = 1
        note = message or readiness_note(state["segments"])
        if message and message not in note:
            note = f"{message} {readiness_note(state['segments'])}"
        return (
            *_editor_pack(state["segments"], state["active"]),
            *view(state, content={"step1_note": note}),
        )
    state["wizard_step"] = 2
    state["max_reached"] = max(state["max_reached"], 2)
    return (
        *_editor_pack(state["segments"], state["active"]),
        *view(state, content={"step1_note": "口播已记下。"}),
    )


def load_demo(state):
    title, segments, strategy, cps, must_keep, ratio = example_materials()
    state = _fresh_state(state)
    state["wizard_step"] = 1
    state["segments"] = segments
    state["active"] = 0 if segments else None
    state["title"] = title
    note = "示例已填好，4 段口播。"
    return (
        gr.update(value=None),
        0,
        title,
        *_editor_pack(segments, state["active"]),
        strategy,
        cps,
        must_keep,
        ratio,
        *view(state, content={"step1_note": note}),
    )


def run_job(
    state,
    title,
    strategy,
    chars_per_sec,
    must_keep,
    expand_short_ratio,
):
    state = _fresh_state(state)
    state["title"] = (title or "").strip()
    lines: list[str] = []
    phase = "align"
    segment_id = ""
    if not segments_ready(state["segments"]):
        state["wizard_step"] = 1
        yield view(
            state,
            content={"step1_note": readiness_note(state["segments"])},
        )
        return
    timeline, draft = build_job_inputs(state["title"], state["segments"])

    state["wizard_step"] = 3
    state["max_reached"] = 3
    lines.append("开始改稿。")
    yield view(
        state,
        content={
            "rail": steps_html("align", caption="进行中"),
            "run_status": status_block(
                title="正在处理",
                lead="",
                lines=lines,
            ),
        },
    )

    try:
        for event in stream_pipeline(
            timeline_text=timeline,
            draft_text=draft,
            strategy=strategy,
            chars_per_sec=chars_per_sec,
            must_keep_text=must_keep or "",
            expand_short_ratio=expand_short_ratio,
        ):
            if event.phase != "done":
                phase = event.phase
            if event.segment_id:
                segment_id = event.segment_id
            if event.message:
                lines.append(event.message)
            if event.result is not None:
                state["wizard_step"] = 4
                state["max_reached"] = 4
                stored = _remember(state, event.result)
                yield view(
                    stored,
                    content=_result_content(
                        event.result,
                        lines,
                        segment_id=segment_id,
                        phase="done",
                        caption="改稿结束。",
                    ),
                )
                return
            state["wizard_step"] = 3
            state["max_reached"] = 3
            yield view(
                state,
                content={
                    "rail": steps_html(phase, segment_id=segment_id, caption="进行中"),
                    "run_status": status_block(
                        title="正在处理",
                        lead="",
                        lines=lines,
                    ),
                },
            )
    except RewritesBlocked as blocked:
        info = humanize(blocked.cause)
        state["wizard_step"] = 4
        state["max_reached"] = 4
        stored = _remember(state, blocked.result)
        status = info.markdown() + "\n\n下面是**对齐之后、还没改写**的校验。字数已经由脚本算过。"
        yield view(
            stored,
            content=_result_content(
                blocked.result,
                lines,
                status=status,
                segment_id=segment_id,
                phase="rewrite",
                failed=True,
                caption="",
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 页面只显示人话
        info = humanize(exc)
        state["wizard_step"] = 4
        state["max_reached"] = 4
        yield view(
            state,
            content=_empty_result_content(
                info.markdown(),
                phase=phase,
                failed=True,
                caption="这一步停住了。",
            ),
        )


def recheck(scope, state, segment_id, revised, strategy, chars_per_sec, must_keep, expand_short_ratio):
    state = _fresh_state(state)
    state["wizard_step"] = 4
    state["max_reached"] = max(state["max_reached"], 4)
    try:
        if not state.get("fit"):
            raise InputError("还没有可重验的稿。请先走完「开始改稿」。")
        result = revalidate(
            timeline=state.get("timeline") or {},
            fit=state.get("fit") or {},
            segment_id=segment_id or None,
            revised=revised,
            strategy=strategy,
            chars_per_sec=chars_per_sec,
            must_keep_text=must_keep or "",
            expand_short_ratio=expand_short_ratio,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        info = humanize(exc)
        return view(state, content={"result_md": info.markdown()})
    if scope == "one":
        line = f"已重算 {segment_id}。"
    elif segment_id:
        line = f"已重算全部段落（写入了 {segment_id}）。"
    else:
        line = "已重算全部段落。"
    status = result_status(
        result.report,
        [line],
        source="recheck",
        headline="重验完成" if result.ok else "重验未通过",
    )
    stored = _remember(state, result)
    return view(
        stored,
        content=_result_content(
            result,
            [line],
            status=status,
            segment_id=segment_id or "",
            phase="done",
            caption="",
        ),
    )


def on_pick(segment_id, state):
    revised, original, meta = segment_fields(state if isinstance(state, dict) else None, segment_id)
    return revised, original, meta


MARK_JS = """
(playhead, session) => {
  const video = document.querySelector("#clip-video video");
  let t = Number(playhead);
  if (!Number.isFinite(t)) t = 0;
  if (video && Number.isFinite(video.currentTime) && video.currentTime > 0.05) {
    t = video.currentTime;
  }
  return [Math.round(t * 100) / 100, session];
}
"""


def on_add_mark(playhead, state):
    state = _fresh_state(state)
    segments, active, message = add_cut(state["segments"], playhead)
    state["segments"] = segments
    state["active"] = active
    try:
        shown = round(float(playhead), 2)
    except (TypeError, ValueError):
        shown = 0
    return (
        shown,
        *_editor_pack(segments, active),
        *view(state, content={"step1_note": message}),
    )


def on_save_segment(state, title, active, start, end, visual, narration):
    state, message = _commit(state, title, active, start, end, visual, narration)
    note = message or readiness_note(state["segments"])
    if message and readiness_note(state["segments"]) not in message:
        note = f"{message} {readiness_note(state['segments'])}"
    return (
        *_editor_pack(state["segments"], state["active"]),
        *view(state, content={"step1_note": note}),
    )


def on_delete_segment(state, active):
    state = _fresh_state(state)
    segments, new_active, message = delete_segment(state["segments"], active)
    state["segments"] = segments
    state["active"] = new_active
    return (
        *_editor_pack(segments, new_active),
        *view(state, content={"step1_note": message}),
    )


def on_open_segment(active, state):
    state = _fresh_state(state)
    start, end, visual, narration = editor_values(state["segments"], active)
    if start is None and end is None and not visual and not narration:
        return gr.update(), gr.update(), gr.update(), gr.update(), state
    state["active"] = active
    return start, end, visual, narration, state


def on_segment_row(evt: gr.SelectData, state):
    state = _fresh_state(state)
    index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    try:
        index = int(index)
    except (TypeError, ValueError):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), state
    if index < 0 or index >= len(state["segments"]):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), state
    state["active"] = str(index)
    start, end, visual, narration = editor_values(state["segments"], index)
    return gr.update(value=str(index)), start, end, visual, narration, state


def on_table_select(evt: gr.SelectData, state):
    if not isinstance(state, dict):
        return gr.update(), gr.update(), gr.update(), gr.update()
    ids = segment_ids(state.get("report"))
    index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    try:
        row = int(index)
    except (TypeError, ValueError):
        return gr.update(), gr.update(), gr.update(), gr.update()
    if row < 0 or row >= len(ids):
        return gr.update(), gr.update(), gr.update(), gr.update()
    revised, original, meta = segment_fields(state, ids[row])
    return ids[row], revised, original, meta


def build_theme() -> gr.Theme:
    """Gradio 主题引擎不吃 CSS 变量，这里只镜像 theme.css 里的 --ck-*。"""
    return gr.themes.Soft(
        primary_hue="purple",
        secondary_hue="purple",
        neutral_hue="slate",
        font=["PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "Segoe UI", "sans-serif"],
    ).set(
        body_background_fill="#f7f7f9",
        body_background_fill_dark="#14131c",
        block_background_fill="#ffffff",
        block_background_fill_dark="#1e1c28",
        block_border_color="#e4e0ee",
        block_border_color_dark="#3a3550",
        block_label_text_color="#69647b",
        block_label_text_color_dark="#b9b3c9",
        block_title_text_color="#25233a",
        block_title_text_color_dark="#f3f1f8",
        block_title_background_fill="transparent",
        block_title_background_fill_dark="transparent",
        block_title_border_width="0px",
        block_title_border_width_dark="0px",
        body_text_color="#25233a",
        body_text_color_dark="#f3f1f8",
        body_text_color_subdued="#69647b",
        body_text_color_subdued_dark="#b9b3c9",
        background_fill_primary="#ffffff",
        background_fill_primary_dark="#1e1c28",
        background_fill_secondary="#faf9fd",
        background_fill_secondary_dark="#18171f",
        border_color_primary="#e4e0ee",
        border_color_primary_dark="#3a3550",
        button_primary_background_fill="#7656ed",
        button_primary_background_fill_dark="#7656ed",
        button_primary_background_fill_hover="#7c6cff",
        button_primary_background_fill_hover_dark="#7c6cff",
        button_primary_text_color="#ffffff",
        button_primary_text_color_dark="#ffffff",
        button_secondary_background_fill="#ffffff",
        button_secondary_background_fill_dark="#1e1c28",
        button_secondary_background_fill_hover="#f8f7ff",
        button_secondary_background_fill_hover_dark="#262333",
        button_secondary_text_color="#7656ed",
        button_secondary_text_color_dark="#c4b8ff",
        button_secondary_border_color="#d9d3f2",
        button_secondary_border_color_dark="#3a3550",
        input_background_fill="#ffffff",
        input_background_fill_dark="#1e1c28",
        input_border_color="#e4e0ee",
        input_border_color_dark="#3a3550",
        shadow_drop="0 8px 26px rgba(50, 40, 90, 0.05)",
        shadow_drop_lg="0 12px 36px rgba(69, 53, 120, 0.08)",
        block_radius="20px",
        button_large_radius="12px",
        button_small_radius="10px",
    )


def _bind_appearance(demo: gr.Blocks, theme: gr.Theme, css: str) -> None:
    """Gradio 5 在构造 Blocks 时收 theme/css，并因此报警。
    构造后再挂上，页面效果相同，且不走即将删除的参数。
    Gradio 6 则在 launch() 接收 theme/css。
    """
    demo.theme = theme
    demo.theme_css = theme._get_theme_css()
    demo.stylesheets = theme._stylesheets
    hasher = hashlib.sha256()
    hasher.update(demo.theme_css.encode("utf-8"))
    demo.theme_hash = hasher.hexdigest()
    demo.css = css


def build_ui() -> gr.Blocks:
    css = THEME_PATH.read_text(encoding="utf-8")
    demo = gr.Blocks(
        title="口播对轴改稿",
        analytics_enabled=False,
        fill_height=False,
        head=VIDEO_RELABEL_HEAD,
    )
    _bind_appearance(demo, build_theme(), css)
    with demo:
        gr.HTML(HERO_HTML, padding=False, elem_id="hero")
        with gr.Row(elem_classes=["nf-wizard"]):
            nav1 = gr.Button("1  准备材料", variant="primary", elem_id="nav-1")
            nav2 = gr.Button("2  选怎么改", variant="secondary", interactive=False, elem_id="nav-2")
            nav3 = gr.Button("3  改稿", variant="secondary", interactive=False, elem_id="nav-3")
            nav4 = gr.Button("4  对照下载", variant="secondary", interactive=False, elem_id="nav-4")
        hint = gr.Markdown(wizard_caption(1), elem_id="wizard-hint")

        with gr.Group(visible=True, elem_id="step-1", elem_classes=["nf-step-panel"]) as panel1:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 01</p><h2 class="nf-section">准备材料</h2></div>',
                padding=False,
            )
            title_box = gr.Textbox(label="这条口播叫什么", placeholder="例如：馒头为什么会胀起来")
            clip = gr.Video(
                label="本地视频（可选）",
                sources=["upload"],
                interactive=True,
                height=320,
                elem_id="clip-video",
            )
            with gr.Row():
                playhead = gr.Number(
                    value=0,
                    label="当前播放位置（秒）",
                    info="无视频时填秒数。",
                    elem_id="playhead",
                    precision=2,
                    minimum=0,
                )
                btn_mark = gr.Button("在当前播放位置添加分段点", variant="secondary")
            seg_table = gr.Dataframe(
                headers=["段", "起（秒）", "止（秒）", "时长", "口播"],
                datatype=["str", "str", "str", "str", "str"],
                value=[],
                interactive=False,
                wrap=True,
                show_label=False,
                max_height=240,
                elem_id="clip-table",
            )
            with gr.Group(elem_classes=["nf-editor"]):
                gr.HTML('<h2 class="nf-section nf-subhead">逐段写口播</h2>', padding=False)
                gr.HTML(
                    '<p class="nf-hint">点一行，写下口播，再保存。</p>',
                    padding=False,
                )
                seg_pick = gr.Dropdown(label="点开一段", choices=[], value=None, elem_id="seg-pick")
                with gr.Row():
                    seg_start = gr.Number(label="开始秒", precision=2, minimum=0)
                    seg_end = gr.Number(label="结束秒", precision=2, minimum=0)
                seg_visual = gr.Textbox(
                    label="画面里有什么",
                    lines=2,
                    placeholder="可选",
                )
                seg_narration = gr.Textbox(
                    label="这一段的口播",
                    lines=4,
                    placeholder="这一段要念的话",
                )
                with gr.Row():
                    btn_save = gr.Button("保存这一段", variant="secondary")
                    btn_delete = gr.Button("删掉这一段", variant="secondary")
            with gr.Row():
                btn_demo = gr.Button("一键演示", variant="secondary")
                btn_next = gr.Button("下一步", variant="primary", interactive=False)
            step1_note = gr.Markdown("还没有分段。")

        with gr.Group(visible=False, elem_id="step-2", elem_classes=["nf-step-panel"]) as panel2:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 02</p><h2 class="nf-section">选怎么改</h2></div>',
                padding=False,
            )
            strategy = gr.Radio(
                choices=["trim", "keep_faster", "expand"],
                value="trim",
                label="策略",
            )
            chars_per_sec = gr.Number(
                value=4.7,
                label="语速（字／秒）",
                minimum=0.1,
                maximum=6.0,
                step=0.1,
            )
            must_keep = gr.Textbox(
                label="必留句",
                lines=3,
                placeholder="每行一句，可选",
            )
            expand_short_ratio = gr.Number(
                value=0.7,
                label="expand 偏短阈值",
                minimum=0.1,
                maximum=1.0,
                step=0.05,
            )
            with gr.Row():
                btn_back = gr.Button("上一步", variant="secondary")
                btn_run = gr.Button("开始改稿", variant="primary")

        with gr.Group(visible=False, elem_id="step-3", elem_classes=["nf-step-panel"]) as panel3:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 03</p><h2 class="nf-section">改稿</h2></div>',
                padding=False,
            )
            rail = gr.HTML(steps_html("idle", caption=""), padding=False, elem_id="steps")
            run_status = gr.Markdown("", elem_id="run-status")

        with gr.Group(visible=False, elem_id="step-4", elem_classes=["nf-step-panel"]) as panel4:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 04</p><h2 class="nf-section">对照下载</h2></div>',
                padding=False,
            )
            result_md = gr.Markdown("")
            overview = gr.HTML(EMPTY_OVERVIEW, padding=False, elem_id="overview")
            table = gr.Dataframe(
                value=[],
                headers=TABLE_HEADERS,
                datatype=["str", "str", "str", "number", "number", "number", "str", "str"],
                interactive=False,
                wrap=True,
                show_label=False,
                max_height=280,
                elem_id="segment-table",
            )
            with gr.Group(elem_classes=["nf-editor"]):
                gr.HTML('<h2 class="nf-section nf-subhead">改一段，立刻重验</h2>', padding=False)
                segment = gr.Dropdown(choices=[], label="选择段落", interactive=True)
                meta = gr.Markdown("选一段。")
                with gr.Row():
                    original = gr.Textbox(label="原文", lines=5, interactive=False)
                    revised = gr.Textbox(label="改后", lines=5)
                with gr.Row():
                    btn_one = gr.Button("重验此段", variant="primary")
                    btn_all = gr.Button("重验全部", variant="secondary")
            gr.HTML('<h2 class="nf-section nf-subhead">对照预览</h2>', padding=False)
            preview = gr.HTML(EMPTY_DIFF, padding=False, elem_id="diff-preview")
            with gr.Row():
                out_html = gr.File(label="下载 diff.html", interactive=False)
                out_fit = gr.File(label="下载 fit.json", interactive=False)
                out_report = gr.File(label="下载 validation-report.md", interactive=False)
            with gr.Row():
                btn_again_materials = gr.Button("再改一次：改材料", variant="secondary")
                btn_again_strategy = gr.Button("再改一次：选怎么改", variant="primary")

        session = gr.State({"wizard_step": 1, "max_reached": 1, "segments": [], "active": None, "title": ""})
        gr.HTML(FOOTER_HTML, padding=False)

        outputs = [
            nav1,
            nav2,
            nav3,
            nav4,
            hint,
            panel1,
            panel2,
            panel3,
            panel4,
            rail,
            run_status,
            result_md,
            overview,
            table,
            segment,
            revised,
            original,
            meta,
            preview,
            out_html,
            out_fit,
            out_report,
            step1_note,
            session,
        ]
        settings_inputs = [strategy, chars_per_sec, must_keep, expand_short_ratio]
        editor_outputs = [seg_table, seg_pick, seg_start, seg_end, seg_visual, seg_narration, btn_next]
        editor_inputs = [session, title_box, seg_pick, seg_start, seg_end, seg_visual, seg_narration]

        nav1.click(lambda state: go_step(1, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav2.click(lambda state: go_step(2, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav3.click(lambda state: go_step(3, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav4.click(lambda state: go_step(4, state), inputs=[session], outputs=outputs, show_progress="hidden")
        btn_back.click(go_back, inputs=[session], outputs=outputs, show_progress="hidden")
        btn_next.click(go_next, inputs=editor_inputs, outputs=[*editor_outputs, *outputs], show_progress="hidden")
        btn_again_materials.click(lambda state: go_step(1, state), inputs=[session], outputs=outputs, show_progress="hidden")
        btn_again_strategy.click(lambda state: go_step(2, state), inputs=[session], outputs=outputs, show_progress="hidden")

        btn_mark.click(
            on_add_mark,
            inputs=[playhead, session],
            outputs=[playhead, *editor_outputs, *outputs],
            js=MARK_JS,
            show_progress="hidden",
        )
        btn_save.click(
            on_save_segment,
            inputs=editor_inputs,
            outputs=[*editor_outputs, *outputs],
            show_progress="hidden",
        )
        btn_delete.click(
            on_delete_segment,
            inputs=[session, seg_pick],
            outputs=[*editor_outputs, *outputs],
            show_progress="hidden",
        )
        seg_pick.change(
            on_open_segment,
            inputs=[seg_pick, session],
            outputs=[seg_start, seg_end, seg_visual, seg_narration, session],
            show_progress="hidden",
        )
        seg_table.select(
            on_segment_row,
            inputs=[session],
            outputs=[seg_pick, seg_start, seg_end, seg_visual, seg_narration, session],
            show_progress="hidden",
        )

        btn_demo.click(
            load_demo,
            inputs=[session],
            outputs=[
                clip,
                playhead,
                title_box,
                *editor_outputs,
                strategy,
                chars_per_sec,
                must_keep,
                expand_short_ratio,
                *outputs,
            ],
            show_progress="hidden",
        )
        btn_run.click(
            run_job,
            inputs=[session, title_box, *settings_inputs],
            outputs=outputs,
            show_progress="hidden",
            concurrency_limit=1,
        )
        btn_one.click(
            lambda *args: recheck("one", *args),
            inputs=[session, segment, revised, *settings_inputs],
            outputs=outputs,
            show_progress="hidden",
        )
        btn_all.click(
            lambda *args: recheck("all", *args),
            inputs=[session, segment, revised, *settings_inputs],
            outputs=outputs,
            show_progress="hidden",
        )
        segment.change(on_pick, inputs=[segment, session], outputs=[revised, original, meta], show_progress="hidden")
        table.select(on_table_select, inputs=[session], outputs=[segment, revised, original, meta], show_progress="hidden")
    return demo


def main() -> None:
    demo = build_ui()
    demo.queue()
    launch_kwargs: dict[str, Any] = {
        "server_name": "0.0.0.0",
        "server_port": 7860,
        "share": False,
        "show_error": False,
        "inbrowser": False,
        "quiet": False,
    }
    params = inspect.signature(demo.launch).parameters
    if "theme" in params and "css" in params:
        launch_kwargs["theme"] = demo.theme
        launch_kwargs["css"] = demo.css
    if "footer_links" in params:
        # 等价于旧的 show_api=False：留 Gradio 与设置，不展示 API 文档。
        launch_kwargs["footer_links"] = ["gradio", "settings"]
    else:
        launch_kwargs["show_api"] = False
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"The 'show_api' parameter in launch\(\) will be removed",
            category=DeprecationWarning,
        )
        demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()
