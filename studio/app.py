#!/usr/bin/env python3
"""口播对轴改稿 · 本地 Gradio 工作室。

四步向导：准备材料 → 怎么改 → 改稿中 → 看结果。
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
from studio_lib.pipeline import (
    InputError,
    RewritesBlocked,
    load_example_inputs,
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
    materials_present,
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

EMPTY_OVERVIEW = """
<section class="nf-overview">
  <p class="nf-kicker">第 4 步 · 看结果</p>
  <h2>这次没有对照稿</h2>
  <p>原因写在上面。点「再改一次」可以回去改材料或策略，已填的内容还在。</p>
</section>
""".strip()


def _read_upload(file_obj: Any) -> str:
    if file_obj is None:
        return ""
    if isinstance(file_obj, (list, tuple)):
        file_obj = file_obj[0] if file_obj else None
    if file_obj is None:
        return ""
    path = file_obj if isinstance(file_obj, str) else getattr(file_obj, "name", "")
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        raise InputError("上传的文件读不到。请重新选择，或把内容贴到文本框。")
    data = file_path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("文件不是 UTF-8。请用 UTF-8 另存后再上传，或直接粘贴文本。") from exc


def _choose_text(typed: str, uploaded: Any) -> str:
    text = (typed or "").strip()
    if text:
        return text
    return _read_upload(uploaded).strip()


def _fresh_state(state: dict | None) -> dict:
    data = dict(state or {})
    data["wizard_step"] = int(data.get("wizard_step") or 1)
    data["max_reached"] = int(data.get("max_reached") or 1)
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
        "run_status": "这一轮已经结束。结果在第 4 步。",
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
        "run_status": "这一轮没有写出对照。原因在第 4 步。",
        "result_md": status,
        "overview": EMPTY_OVERVIEW,
        "table": [],
        "segment": gr.update(choices=[], value=None),
        "revised": "",
        "original": "",
        "meta": "这次没有分段结果。",
        "preview": EMPTY_DIFF,
        "html_file": None,
        "fit_file": None,
        "report_file": None,
    }


def _remember(state: dict, result) -> dict:
    data = result.state()
    data["wizard_step"] = state["wizard_step"]
    data["max_reached"] = state["max_reached"]
    return data


def go_step(target: int, state):
    state = _fresh_state(state)
    dest = clamp_wizard_target(target, state["max_reached"])
    extra = ""
    if dest is None:
        extra = "这一步还没解锁。先做完前面的步骤。"
    else:
        state["wizard_step"] = dest
    return view(state, extra=extra)


def go_back(state):
    state = _fresh_state(state)
    state["wizard_step"] = 1
    return view(state)


def go_next(state, timeline_text, draft_text, timeline_file, draft_file):
    state = _fresh_state(state)
    try:
        timeline = _choose_text(timeline_text, timeline_file)
        draft = _choose_text(draft_text, draft_file)
    except InputError as exc:
        info = humanize(exc)
        state["wizard_step"] = 1
        return view(state, extra="材料还读不了。", content={"step1_note": info.markdown()})
    if not materials_present(timeline, draft):
        state["wizard_step"] = 1
        return view(
            state,
            extra="时间轴和口播都要有内容。",
            content={"step1_note": "先粘贴或上传时间轴和口播，也可以点「一键演示」。"},
        )
    state["wizard_step"] = 2
    state["max_reached"] = max(state["max_reached"], 2)
    return view(state, content={"step1_note": "材料已记下。要改的话，点顶栏回到这一步。"})


def toggle_next(timeline_text, draft_text, timeline_file, draft_file):
    try:
        ready = materials_present(
            _choose_text(timeline_text, timeline_file),
            _choose_text(draft_text, draft_file),
        )
    except InputError:
        ready = False
    return gr.update(interactive=ready)


def load_demo(state):
    timeline_text, draft_text, strategy, cps, must_keep, ratio = load_example_inputs()
    state = _fresh_state(state)
    state["wizard_step"] = 1
    note = "示例《馒头为什么会胀起来》已填入。检查材料后点「下一步」。这一步还不展示改稿结果。"
    return (
        None,
        timeline_text,
        None,
        draft_text,
        strategy,
        cps,
        must_keep,
        ratio,
        gr.update(interactive=True),
        *view(state, extra="示例已填入。", content={"step1_note": note}),
    )


def run_job(
    state,
    timeline_file,
    timeline_text,
    draft_file,
    draft_text,
    strategy,
    chars_per_sec,
    must_keep,
    expand_short_ratio,
):
    state = _fresh_state(state)
    lines: list[str] = []
    phase = "align"
    segment_id = ""
    try:
        timeline = _choose_text(timeline_text, timeline_file)
        draft = _choose_text(draft_text, draft_file)
    except InputError as exc:
        info = humanize(exc)
        state["wizard_step"] = 1
        yield view(state, extra="材料还读不了。", content={"step1_note": info.markdown()})
        return
    if not materials_present(timeline, draft):
        state["wizard_step"] = 1
        yield view(
            state,
            extra="时间轴和口播都要有内容。",
            content={"step1_note": "先回到第 1 步把材料补齐。"},
        )
        return

    state["wizard_step"] = 3
    state["max_reached"] = 3
    lines.append("开始改稿。先对齐全文，再按段校验预算。")
    yield view(
        state,
        content={
            "rail": steps_html("align", caption="进行中"),
            "run_status": status_block(
                title="正在处理",
                lead="对齐、校验和再校验都在本地完成。只有改写文字才会调用模型。",
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
                        caption="改稿结束。数字以脚本重算为准。",
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
                        lead="对齐、校验和再校验都在本地完成。只有改写文字才会调用模型。",
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
                caption="改稿停在调用模型之前。",
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
        line = f"已把 {segment_id} 的改后文案交给脚本重算。其它段的文字没动，也没有调用模型。"
    elif segment_id:
        line = f"已写入 {segment_id} 的改后文案，并按当前策略和语速重算全部段落。没有调用模型。"
    else:
        line = "已按当前策略、语速和必留句重算全部段落。没有调用模型。"
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
            caption="只跑了本地脚本。",
        ),
    )


def on_pick(segment_id, state):
    revised, original, meta = segment_fields(state if isinstance(state, dict) else None, segment_id)
    return revised, original, meta


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
    )
    _bind_appearance(demo, build_theme(), css)
    with demo:
        gr.HTML(HERO_HTML, padding=False, elem_id="hero")
        with gr.Row(elem_classes=["nf-wizard"]):
            nav1 = gr.Button("1  准备材料", variant="primary", elem_id="nav-1")
            nav2 = gr.Button("2  怎么改", variant="secondary", interactive=False, elem_id="nav-2")
            nav3 = gr.Button("3  改稿中", variant="secondary", interactive=False, elem_id="nav-3")
            nav4 = gr.Button("4  看结果", variant="secondary", interactive=False, elem_id="nav-4")
        hint = gr.Markdown(wizard_caption(1), elem_id="wizard-hint")

        with gr.Group(visible=True, elem_id="step-1", elem_classes=["nf-step-panel"]) as panel1:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 01</p><h2 class="nf-section">准备材料</h2></div>',
                padding=False,
            )
            gr.HTML(
                '<p class="nf-note">先给出已剪时间轴，和现在的口播全文。这一步只收材料，不展示改稿结果。</p>',
                padding=False,
            )
            timeline_text = gr.Textbox(
                label="时间轴 JSON",
                lines=8,
                max_lines=18,
                placeholder='{"title":"…","segments":[{"segment_id":"s1","start_s":0,"end_s":6,"visual":"画面说明"}]}',
                info="有文本时优先用文本。每段需要 segment_id、start_s、end_s、visual。",
            )
            draft_text = gr.Textbox(
                label="口播全文",
                lines=8,
                max_lines=18,
                placeholder="整篇口播。段落之间空一行。",
                info="空行分段若和时间轴段数一致，对齐时会按顺序切开，这一步不调用模型。",
            )
            with gr.Accordion("或者上传文件", open=False):
                timeline_file = gr.File(label="时间轴 timeline.json", file_types=[".json"])
                draft_file = gr.File(label="口播全文 .txt / .md", file_types=[".txt", ".md"])
            with gr.Row():
                btn_demo = gr.Button("一键演示", variant="secondary")
                btn_next = gr.Button("下一步", variant="primary", interactive=False)
            step1_note = gr.Markdown("把时间轴和口播放进来，或点「一键演示」填入示例。")

        with gr.Group(visible=False, elem_id="step-2", elem_classes=["nf-step-panel"]) as panel2:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 02</p><h2 class="nf-section">怎么改</h2></div>',
                padding=False,
            )
            gr.HTML(
                '<p class="nf-note">策略决定怎么改字。语速决定每段预算。脚本按你填的数字算，不会改它。</p>',
                padding=False,
            )
            strategy = gr.Radio(
                choices=["trim", "keep_faster", "expand"],
                value="trim",
                label="策略",
                info="trim 删到能念完。keep_faster 用你接受的更快语速尽量留字。expand 在预算内把偏短的段补上。",
            )
            chars_per_sec = gr.Number(
                value=4.7,
                label="语速（字／秒）",
                info="舒适档可参考 4.7，加快档可参考 5.5。",
                minimum=0.1,
                maximum=6.0,
                step=0.1,
            )
            must_keep = gr.Textbox(
                label="必留句",
                lines=3,
                info="每行一句，可选。该句必须整句留在同一段的改后文案里。",
            )
            expand_short_ratio = gr.Number(
                value=0.7,
                label="expand 偏短阈值",
                info="原文少于预算乘这个比例时，expand 会考虑补写。常用 0.7。",
                minimum=0.1,
                maximum=1.0,
                step=0.05,
            )
            with gr.Row():
                btn_back = gr.Button("上一步", variant="secondary")
                btn_run = gr.Button("开始改稿", variant="primary")

        with gr.Group(visible=False, elem_id="step-3", elem_classes=["nf-step-panel"]) as panel3:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 03</p><h2 class="nf-section">改稿中</h2></div>',
                padding=False,
            )
            gr.HTML(
                '<p class="nf-note">顺序是对齐、校验、改稿、再校验。跑完会自动进入第 4 步，成功和失败都会进。</p>',
                padding=False,
            )
            rail = gr.HTML(steps_html("idle", caption="还没开始。"), padding=False, elem_id="steps")
            run_status = gr.Markdown("点第 2 步的「开始改稿」后，进度会出现在这里。", elem_id="run-status")

        with gr.Group(visible=False, elem_id="step-4", elem_classes=["nf-step-panel"]) as panel4:
            gr.HTML(
                '<div class="nf-step-head"><p class="nf-step-index">STEP 04</p><h2 class="nf-section">看结果</h2></div>',
                padding=False,
            )
            result_md = gr.Markdown("")
            overview = gr.HTML(EMPTY_OVERVIEW, padding=False, elem_id="overview")
            gr.HTML(
                '<p class="nf-note">表里只留段、时长和超欠。逐字差异在下面的对照里。</p>',
                padding=False,
            )
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
                gr.HTML(
                    '<p class="nf-hint">只改「改后」。重验此段或全部都只跑本地脚本，不调用模型。换段之前先重验。</p>',
                    padding=False,
                )
                segment = gr.Dropdown(choices=[], label="选择段落", interactive=True)
                meta = gr.Markdown("改稿跑完后，在这里选一段。")
                with gr.Row():
                    original = gr.Textbox(label="原文", lines=5, interactive=False)
                    revised = gr.Textbox(label="改后", lines=5)
                with gr.Row():
                    btn_one = gr.Button("重验此段", variant="primary")
                    btn_all = gr.Button("重验全部", variant="secondary")
            gr.HTML('<h2 class="nf-section nf-subhead">对照预览</h2>', padding=False)
            gr.HTML(
                '<p class="nf-note">删除线是拿掉的字，绿色底是新增的字。下载的 diff.html 用同一份渲染。</p>',
                padding=False,
            )
            preview = gr.HTML(EMPTY_DIFF, padding=False, elem_id="diff-preview")
            with gr.Row():
                out_html = gr.File(label="下载 diff.html", interactive=False)
                out_fit = gr.File(label="下载 fit.json", interactive=False)
                out_report = gr.File(label="下载 validation-report.md", interactive=False)
            gr.HTML('<p class="nf-note">再改一次会保留已经填好的材料和策略。</p>', padding=False)
            with gr.Row():
                btn_again_materials = gr.Button("再改一次：改材料", variant="secondary")
                btn_again_strategy = gr.Button("再改一次：改策略", variant="primary")

        session = gr.State({"wizard_step": 1, "max_reached": 1})
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
        material_inputs = [timeline_text, draft_text, timeline_file, draft_file]
        settings_inputs = [strategy, chars_per_sec, must_keep, expand_short_ratio]

        nav1.click(lambda state: go_step(1, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav2.click(lambda state: go_step(2, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav3.click(lambda state: go_step(3, state), inputs=[session], outputs=outputs, show_progress="hidden")
        nav4.click(lambda state: go_step(4, state), inputs=[session], outputs=outputs, show_progress="hidden")
        btn_back.click(go_back, inputs=[session], outputs=outputs, show_progress="hidden")
        btn_next.click(go_next, inputs=[session, *material_inputs], outputs=outputs, show_progress="hidden")
        btn_again_materials.click(lambda state: go_step(1, state), inputs=[session], outputs=outputs, show_progress="hidden")
        btn_again_strategy.click(lambda state: go_step(2, state), inputs=[session], outputs=outputs, show_progress="hidden")

        for box in (timeline_text, draft_text, timeline_file, draft_file):
            box.change(toggle_next, inputs=material_inputs, outputs=[btn_next], show_progress="hidden")

        btn_demo.click(
            load_demo,
            inputs=[session],
            outputs=[
                timeline_file,
                timeline_text,
                draft_file,
                draft_text,
                strategy,
                chars_per_sec,
                must_keep,
                expand_short_ratio,
                btn_next,
                *outputs,
            ],
            show_progress="hidden",
        )
        btn_run.click(
            run_job,
            inputs=[session, timeline_file, timeline_text, draft_file, draft_text, *settings_inputs],
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
