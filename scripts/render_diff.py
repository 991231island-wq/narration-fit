#!/usr/bin/env python3
"""把 fit.json 渲染成单文件对照 HTML，并写出校验报告。

HTML 内嵌 CSS，不请求外链字体或图片。校验失败时仍然写出对照，退出码与校验脚本一致。
"""

from __future__ import annotations

import argparse
import html
import sys
from difflib import SequenceMatcher
from pathlib import Path

from validate_narration_fit import (
    InputError,
    apply_overrides,
    as_object,
    build_parser as validate_parser,
    load_json,
    markdown_report,
    validate_fit,
)


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def diff_html(original: str, revised: str) -> tuple[str, str]:
    matcher = SequenceMatcher(a=original, b=revised, autojunk=False)
    left: list[str] = []
    right: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            piece = html.escape(original[i1:i2])
            left.append(piece)
            right.append(piece)
        elif tag == "delete":
            left.append(f"<del>{html.escape(original[i1:i2])}</del>")
        elif tag == "insert":
            right.append(f"<ins>{html.escape(revised[j1:j2])}</ins>")
        elif tag == "replace":
            left.append(f"<del>{html.escape(original[i1:i2])}</del>")
            right.append(f"<ins>{html.escape(revised[j1:j2])}</ins>")
    return "".join(left), "".join(right)


def render_body(report: dict) -> str:
    result = "通过" if report["ok"] else "未通过"
    pill = "ok" if report["ok"] else "over"
    speed = report["chars_per_sec"] or "（缺失）"
    strategy = report["strategy"] or "（无效）"
    totals = report["totals"]
    parts = [
        "<header class=\"top\">",
        "<p class=\"kicker\">NARRATION FIT · 逐段口播对照</p>",
        f"<h1>{html.escape(report['title'])}</h1>",
        "<div class=\"speed\">",
        f"<span class=\"pill {pill}\">{result}</span>",
        f"<span>策略 <strong class=\"strategy\">{html.escape(strategy)}</strong></span>",
        f"<span class=\"speed-readout\">本次语速 <strong>{html.escape(speed)}</strong> 字／秒</span>",
        "</div>",
        "<dl class=\"summary\">",
        f"<div><dt>总预算</dt><dd>{totals['budget']}</dd></div>",
        f"<div><dt>原文</dt><dd>{totals['chars_original']}</dd></div>",
        f"<div><dt>改后</dt><dd>{totals['chars_revised']}</dd></div>",
        f"<div><dt>超预算</dt><dd>{totals['over_by']}</dd></div>",
        f"<div><dt>未分配</dt><dd>{report['unassigned_chars']}</dd></div>",
        "</dl>",
        "<p class=\"legend\"><del>删除线</del>是从原文拿掉的字。<ins>绿色底</ins>是改后新增的字。字数由脚本重算，标点计入。"
        + (
            f" expand 偏短阈值 {html.escape(str(report.get('expand_short_ratio') or ''))}："
            "原文低于预算的这个比例就建议扩写；改后仍不得超过预算。超预算的段要先修剪。"
            if report.get("strategy") == "expand"
            else ""
        )
        + "</p>",
        "</header>",
        "<div class=\"grid\">",
    ]
    if not report["segments"]:
        parts.append("<p class=\"empty\">没有可对照的段落。先看下面的问题列表。</p>")
    for row in report["segments"]:
        left, right = diff_html(row["original"], row["revised"])
        label = f" · {html.escape(row['label'])}" if row["label"] else ""
        note = f"<p class=\"note\">{html.escape(row['note'])}</p>" if row["note"] else ""
        hints = "".join(f"<p class=\"hint\">{html.escape(hint)}</p>" for hint in row["hints"])
        parts.append(
            "\n".join(
                [
                    f"<article class=\"card {row['status']}\" data-segment=\"{html.escape(row['segment_id'])}\">",
                    "<header>",
                    f"<h2>{html.escape(row['segment_id'])}{label}</h2>",
                    f"<p class=\"visual\">{html.escape(row['visual'])}</p>",
                    "</header>",
                    "<p class=\"meta\">",
                    f"<span>{html.escape(row['start_s'])}–{html.escape(row['end_s'])} 秒</span>",
                    f"<span>时长 {html.escape(row['duration_s'])} 秒</span>",
                    f"<span>预算 {row['budget']}</span>",
                    f"<span>原文 {row['chars_original']}</span>",
                    f"<span>改后 {row['chars_revised']}</span>",
                    f"<span>超 {row['over_by']}</span>",
                    f"<span>欠 {row['under_by']}</span>",
                    f"<span class=\"pill {row['status']}\">{html.escape(row.get('fit_action_label') or row['status'])}</span>",
                    "</p>",
                    "<div class=\"cols\">",
                    f"<section class=\"block\"><h3>原文</h3><p class=\"copy\">{left}</p></section>",
                    f"<section class=\"block\"><h3>改后</h3><p class=\"copy\">{right}</p></section>",
                    "</div>",
                    note,
                    hints,
                    "</article>",
                ]
            )
        )
    parts.append("</div>")
    if report["unassigned_chars"]:
        parts.append(
            "<section class=\"unassigned\"><h2>未分配</h2>"
            f"<p>{report['unassigned_chars']} 字，没有划入任何段。脚本不会悄悄丢掉这段文字。</p>"
            f"<pre>{html.escape(report['unassigned'])}</pre></section>"
        )
    if report["must_keep"]:
        items = []
        hits: dict[str, list[str]] = {}
        for hit in report["must_keep_hits"]:
            hits.setdefault(hit["phrase"], [])
            if hit["segment_id"] and hit["segment_id"] not in hits[hit["phrase"]]:
                hits[hit["phrase"]].append(hit["segment_id"])
        for phrase in report["must_keep"]:
            found = "、".join(hits.get(phrase) or []) or "未找到"
            items.append(f"<li>{html.escape(phrase)} → {html.escape(found)}</li>")
        parts.append("<section class=\"keeps\"><h2>必留句</h2><ul>" + "".join(items) + "</ul></section>")
    if report["errors"]:
        items = "".join(f"<li>{html.escape(message)}</li>" for message in report["errors"])
        parts.append(f"<section class=\"errors\"><h2>问题</h2><ul>{items}</ul></section>")
    parts.append(
        "<footer>"
        f"<p>{html.escape(report['char_count_rule'])}</p>"
        f"<p>{html.escape(report['budget_rule'])}</p>"
        f"<p>本次语速 {html.escape(speed)} 字／秒来自用户输入，脚本没有改写。</p>"
        f"<p>{html.escape(report['comfort_source'])}</p>"
        f"<p>{html.escape(report['faster_source'])}</p>"
        f"<p>{html.escape(report['cap_source'])}</p>"
        f"<p>{html.escape(report['range_note'])}</p>"
        "<p>本页只对照文案和字数预算，不代表真人口播已经念过，也不渲染成片。</p>"
        "</footer>"
    )
    return "\n".join(parts)


def render_html(report: dict, template_text: str) -> str:
    if "__NF_TITLE__" not in template_text or "__NF_BODY__" not in template_text:
        raise InputError("模板缺少 __NF_TITLE__ 或 __NF_BODY__")
    title = f"{report['title']} · 口播对照"
    return template_text.replace("__NF_TITLE__", html.escape(title)).replace("__NF_BODY__", render_body(report))


def build_parser() -> argparse.ArgumentParser:
    parser = validate_parser()
    parser.description = "渲染逐段口播对照 HTML，并写出校验报告"
    parser.add_argument("--html", required=True, help="输出 HTML 路径")
    parser.add_argument("--template", help="HTML 模板，默认 assets/diff-template.html")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        timeline = as_object(load_json(Path(args.timeline)), "timeline")
        fit = apply_overrides(as_object(load_json(Path(args.fit)), "fit"), args)
        report = validate_fit(timeline, fit, allow_unassigned=args.allow_unassigned)
        template_path = Path(args.template) if args.template else package_root() / "assets" / "diff-template.html"
        template_text = template_path.read_text(encoding="utf-8")
        page = render_html(report, template_text)
    except InputError as exc:
        print(f"narration-fit: 输入错误：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"narration-fit: 无法读取模板：{exc}", file=sys.stderr)
        return 2

    html_path = Path(args.html)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page, encoding="utf-8")
    text = markdown_report(report)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    state = "通过" if report["ok"] else "未通过"
    print(
        f"narration-fit: {state} html={html_path} strategy={report['strategy'] or '-'} chars_per_sec={report['chars_per_sec'] or '-'}",
        file=sys.stderr,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
