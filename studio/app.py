#!/usr/bin/env python3
"""口播对轴改稿 · 本地 Gradio 工作室。

在仓库根目录启动：python studio/app.py
监听 0.0.0.0:7860。令牌只读环境变量 MODELSCOPE_API_TOKEN。
"""

from __future__ import annotations

import sys
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
    load_example_documents,
    load_example_inputs,
    revalidate,
    run_scripts_only,
    stream_pipeline,
)
from studio_lib.present import (
    FOOTER_HTML,
    HERO_HTML,
    TABLE_HEADERS,
    diff_preview,
    overview_html,
    result_status,
    segment_fields,
    segment_ids,
    status_block,
    steps_html,
    table_rows,
)

THEME_PATH = STUDIO_DIR / "theme.css"


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


def make_preset():
    timeline, fit = load_example_documents()
    return run_scripts_only(timeline, fit, source="preset")


def _pack(
    result,
    lines: list[str],
    *,
    phase: str,
    failed: bool = False,
    caption: str = "",
    segment_id: str | None = None,
    status: str | None = None,
):
    state = result.state()
    ids = segment_ids(result.report)
    chosen = segment_id if segment_id in ids else (ids[0] if ids else None)
    revised, original, meta = segment_fields(state, chosen)
    if status is None:
        status = result_status(result.report, lines, source=result.source)
    return (
        steps_html(phase, segment_id=segment_id or "", failed=failed, caption=caption),
        status,
        overview_html(result.report, result.source),
        table_rows(result.report),
        gr.update(choices=ids, value=chosen),
        revised,
        original,
        meta,
        diff_preview(result.html_preview),
        result.html_path,
        result.fit_path,
        result.report_path,
        state,
    )


def _keep(status: str, phase: str, *, segment_id: str = "", failed: bool = False, caption: str = ""):
    skipped = (gr.update(),) * 11
    return (
        steps_html(phase, segment_id=segment_id, failed=failed, caption=caption),
        status,
        *skipped,
    )


def load_demo():
    timeline_text, draft_text, strategy, cps, must_keep, ratio = load_example_inputs()
    result = make_preset()
    lines = [
        "已载入示例《馒头为什么会胀起来》。",
        "上面的总览和下面的对照，是 trim、4.7 字／秒的预置改稿。",
        "没有调用模型。左侧文本是偏长原稿，点「开始改稿」才会请求魔搭。",
    ]
    view = _pack(
        result,
        lines,
        phase="done",
        caption="预置结果已就绪，没有调用模型。",
    )
    return (None, timeline_text, None, draft_text, strategy, cps, must_keep, ratio, *view)


def run_job(
    timeline_file,
    timeline_text,
    draft_file,
    draft_text,
    strategy,
    chars_per_sec,
    must_keep,
    expand_short_ratio,
):
    lines: list[str] = []
    phase = "align"
    segment_id = ""
    try:
        for event in stream_pipeline(
            timeline_text=_choose_text(timeline_text, timeline_file),
            draft_text=_choose_text(draft_text, draft_file),
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
                yield _pack(
                    event.result,
                    lines,
                    phase="done",
                    segment_id=segment_id,
                    caption="这一轮改稿结束。数字以脚本重算为准。",
                )
                return
            yield _keep(
                status_block(
                    title="正在处理",
                    lead="对齐、校验和再校验都在本地完成。只有改写文字才会调用模型。",
                    lines=lines,
                ),
                phase,
                segment_id=segment_id,
                caption="进行中",
            )
    except RewritesBlocked as blocked:
        info = humanize(blocked.cause)
        status = (
            info.markdown()
            + "\n\n下面换成**这次对齐之后、还没改写**的校验。字数已经由脚本算过，模型没有改字。"
        )
        yield _pack(
            blocked.result,
            lines,
            phase="rewrite",
            failed=True,
            segment_id=segment_id,
            caption="改稿停在调用模型之前。",
            status=status,
        )
    except Exception as exc:  # noqa: BLE001 — 页面只显示人话，不展示调用栈
        info = humanize(exc)
        yield _keep(
            info.markdown(),
            phase,
            segment_id=segment_id,
            failed=True,
            caption="这一步停住了。下面的总览和对照仍是上一轮结果。",
        )


def recheck(scope, state, segment_id, revised, strategy, chars_per_sec, must_keep, expand_short_ratio):
    try:
        if not isinstance(state, dict) or not state.get("fit"):
            raise InputError("还没有可重验的稿。请先点「一键演示」或「开始改稿」。")
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
        return _keep(
            info.markdown(),
            "recheck",
            segment_id=segment_id or "",
            failed=True,
            caption="重验没有跑完，没有调用模型。",
        )
    if scope == "one":
        line = f"已把 {segment_id} 的改后文案交给脚本重算。其它段的文字没动，也没有调用模型。"
    elif segment_id:
        line = f"已写入 {segment_id} 的改后文案，并按当前策略和语速重算全部段落。没有调用模型。"
    else:
        line = "已按当前策略、语速和必留句重算全部段落。没有调用模型。"
    status = result_status(result.report, [line], source="recheck", headline="重验完成" if result.ok else "重验未通过")
    return _pack(
        result,
        [line],
        phase="done",
        segment_id=segment_id or "",
        caption="只跑了本地脚本。",
        status=status,
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
    return gr.themes.Soft(
        primary_hue="stone",
        secondary_hue="stone",
        neutral_hue="stone",
        font=["PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "Segoe UI", "sans-serif"],
    ).set(
        body_background_fill="#efe6d6",
        body_background_fill_dark="#efe6d6",
        block_background_fill="#fffdf8",
        block_background_fill_dark="#fffdf8",
        block_border_color="#e4d5c0",
        block_label_text_color="#5c5348",
        block_title_text_color="#1c1915",
        body_text_color="#1c1915",
        body_text_color_dark="#1c1915",
        body_text_color_subdued="#5c5348",
        body_text_color_subdued_dark="#5c5348",
        background_fill_primary="#fffdf8",
        background_fill_primary_dark="#fffdf8",
        background_fill_secondary="#f6efe4",
        background_fill_secondary_dark="#f6efe4",
        border_color_primary="#e4d5c0",
        border_color_primary_dark="#e4d5c0",
        button_primary_background_fill="#9a5322",
        button_primary_background_fill_dark="#9a5322",
        button_primary_background_fill_hover="#7c411c",
        button_primary_background_fill_hover_dark="#7c411c",
        button_primary_text_color="#fffaf3",
        button_primary_text_color_dark="#fffaf3",
        button_secondary_background_fill="#fffdf8",
        button_secondary_background_fill_dark="#fffdf8",
        button_secondary_background_fill_hover="#f3e7d4",
        button_secondary_background_fill_hover_dark="#f3e7d4",
        button_secondary_text_color="#1c1915",
        button_secondary_text_color_dark="#1c1915",
        button_secondary_border_color="#ddcfbc",
        button_secondary_border_color_dark="#ddcfbc",
        input_background_fill="#fffdf8",
        input_background_fill_dark="#fffdf8",
        input_border_color="#e4d5c0",
        input_border_color_dark="#e4d5c0",
        shadow_drop="none",
        shadow_drop_lg="0 12px 28px rgba(70, 48, 24, 0.06)",
    )


def build_ui() -> gr.Blocks:
    preset = make_preset()
    ids = segment_ids(preset.report)
    first_id = ids[0] if ids else None
    revised0, original0, meta0 = segment_fields(preset.state(), first_id)
    preset_lines = [
        "打开页面时已经放上一份通过的示例，方便没有令牌时先看结果长什么样。",
        "策略 trim，语速 4.7 字／秒。字数是本地脚本重算的。",
    ]
    css = THEME_PATH.read_text(encoding="utf-8")

    with gr.Blocks(
        title="口播对轴改稿",
        theme=build_theme(),
        css=css,
        analytics_enabled=False,
        fill_height=False,
    ) as demo:
        gr.HTML(HERO_HTML, padding=False, elem_id="hero")
        overview = gr.HTML(
            overview_html(preset.report, preset.source),
            padding=False,
            elem_id="overview",
        )
        with gr.Row():
            with gr.Column(scale=3):
                gr.HTML('<h2 class="nf-section">时间轴和口播</h2>', padding=False)
                timeline_text = gr.Textbox(
                    value=(STUDIO_DIR.parent / "examples" / "demo-timeline.json").read_text(encoding="utf-8"),
                    label="时间轴 JSON",
                    lines=8,
                    max_lines=16,
                    info="有文本时优先用文本。需要 segment_id、start_s、end_s、visual。",
                )
                draft_text = gr.Textbox(
                    value=(STUDIO_DIR.parent / "examples" / "demo-draft-overlong.txt").read_text(encoding="utf-8"),
                    label="口播全文",
                    lines=8,
                    max_lines=16,
                    info="这是示例原稿，故意写得偏长。空行分段若和时间轴段数一致，会按顺序对齐。",
                )
                with gr.Accordion("或者上传文件", open=False):
                    timeline_file = gr.File(label="时间轴 timeline.json", file_types=[".json"])
                    draft_file = gr.File(label="口播全文 .txt / .md", file_types=[".txt", ".md"])
            with gr.Column(scale=2):
                gr.HTML('<h2 class="nf-section">怎么改</h2>', padding=False)
                strategy = gr.Radio(
                    choices=["trim", "keep_faster", "expand"],
                    value="trim",
                    label="策略",
                    info="trim 删到能念完。keep_faster 用你接受的更快语速尽量留字。expand 在预算内把偏短的段补上。",
                )
                chars_per_sec = gr.Number(
                    value=4.7,
                    label="语速（字／秒）",
                    info="舒适档可参考 4.7，加快档可参考 5.5。脚本按你填的数字算，不会改它。",
                    minimum=0.1,
                    maximum=6.0,
                    step=0.1,
                )
                must_keep = gr.Textbox(
                    value="气孔是发酵留下的，不是后来吹进去的",
                    label="必留句",
                    lines=3,
                    info="每行一句。该句必须整句留在同一段的改后文案里。",
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
                    btn_demo = gr.Button("一键演示", variant="secondary")
                    btn_run = gr.Button("开始改稿", variant="primary")

        steps = gr.HTML(
            steps_html("done", caption="预置结果已就绪，没有调用模型。"),
            padding=False,
            elem_id="steps",
        )
        status = gr.Markdown(
            value=result_status(preset.report, preset_lines, source="preset"),
            elem_id="run-status",
        )

        gr.HTML('<h2 class="nf-section">分段</h2>', padding=False)
        gr.HTML(
            '<p class="nf-note">表里只留段、时长和超欠。原文和改后的逐字差异在下方对照里。</p>',
            padding=False,
        )
        table = gr.Dataframe(
            value=table_rows(preset.report),
            headers=TABLE_HEADERS,
            datatype=["str", "str", "str", "number", "number", "number", "str", "str"],
            interactive=False,
            wrap=True,
            show_label=False,
            max_height=280,
            elem_id="segment-table",
        )

        with gr.Group(elem_classes=["nf-editor"]):
            gr.HTML('<h2 class="nf-section">改一段，立刻重验</h2>', padding=False)
            gr.HTML(
                '<p class="nf-hint">只改「改后」。重验此段或全部都只跑本地脚本，不调用模型。换段之前先重验，否则框里还没提交的字不会写入。</p>',
                padding=False,
            )
            segment = gr.Dropdown(
                choices=ids,
                value=first_id,
                label="选择段落",
                interactive=True,
            )
            meta = gr.Markdown(meta0)
            with gr.Row():
                original = gr.Textbox(value=original0, label="原文", lines=5, interactive=False)
                revised = gr.Textbox(value=revised0, label="改后", lines=5)
            with gr.Row():
                btn_one = gr.Button("重验此段", variant="primary")
                btn_all = gr.Button("重验全部", variant="secondary")

        gr.HTML('<h2 class="nf-section">对照预览</h2>', padding=False)
        gr.HTML(
            '<p class="nf-note">这里嵌的是脚本渲染的 diff：删除线是拿掉的字，绿色底是新增的字。下载的 diff.html 用同一份渲染。</p>',
            padding=False,
        )
        preview = gr.HTML(diff_preview(preset.html_preview), padding=False, elem_id="diff-preview")
        with gr.Row():
            out_html = gr.File(value=preset.html_path, label="下载 diff.html", interactive=False)
            out_fit = gr.File(value=preset.fit_path, label="下载 fit.json", interactive=False)
            out_report = gr.File(value=preset.report_path, label="下载 validation-report.md", interactive=False)
        session = gr.State(preset.state())
        gr.HTML(FOOTER_HTML, padding=False)

        form_outputs = [
            timeline_file,
            timeline_text,
            draft_file,
            draft_text,
            strategy,
            chars_per_sec,
            must_keep,
            expand_short_ratio,
        ]
        view_outputs = [
            steps,
            status,
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
            session,
        ]
        settings_inputs = [strategy, chars_per_sec, must_keep, expand_short_ratio]

        btn_demo.click(fn=load_demo, outputs=form_outputs + view_outputs, show_progress="hidden")
        btn_run.click(
            fn=run_job,
            inputs=[timeline_file, timeline_text, draft_file, draft_text, *settings_inputs],
            outputs=view_outputs,
            show_progress="hidden",
            concurrency_limit=1,
        )
        btn_one.click(
            fn=lambda *args: recheck("one", *args),
            inputs=[session, segment, revised, *settings_inputs],
            outputs=view_outputs,
            show_progress="hidden",
        )
        btn_all.click(
            fn=lambda *args: recheck("all", *args),
            inputs=[session, segment, revised, *settings_inputs],
            outputs=view_outputs,
            show_progress="hidden",
        )
        segment.change(fn=on_pick, inputs=[segment, session], outputs=[revised, original, meta], show_progress="hidden")
        table.select(fn=on_table_select, inputs=[session], outputs=[segment, revised, original, meta], show_progress="hidden")
    return demo


def main() -> None:
    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_api=False,
        show_error=False,
        inbrowser=False,
        quiet=False,
    )


if __name__ == "__main__":
    main()
