"""串起对齐、校验、改稿、再校验，以及不调模型的重验。

字数与预算只调用仓库 scripts/。本模块不复制校验器。
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

STUDIO_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = STUDIO_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"
ASSETS = REPO_ROOT / "assets"
EXAMPLES = REPO_ROOT / "examples"

if str(STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(STUDIO_DIR))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from render_diff import render_html  # noqa: E402
from validate_narration_fit import (  # noqa: E402
    STRATEGIES,
    InputError,
    emit_fit,
    markdown_report,
    validate_fit,
    validate_timeline,
)

from studio_lib.align_and_rewrite import (  # noqa: E402
    DEFAULT_MODEL,
    align_draft_to_timeline,
    make_client,
    rewrite_segment,
    segments_needing_rewrite,
    try_paragraph_align,
)
from studio_lib.errors import ModelJSONError, TokenMissingError  # noqa: E402


@dataclass
class PipelineResult:
    ok: bool
    summary: str
    source: str
    table_rows: list[list[Any]] = field(default_factory=list)
    html_path: str | None = None
    fit_path: str | None = None
    report_path: str | None = None
    html_preview: str = ""
    fit: dict[str, Any] = field(default_factory=dict)
    timeline: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    work_dir: str = ""

    def state(self) -> dict[str, Any]:
        return {
            "timeline": self.timeline,
            "fit": self.fit,
            "report": self.report,
            "source": self.source,
        }


@dataclass
class StreamEvent:
    phase: str
    message: str
    segment_id: str = ""
    result: PipelineResult | None = None
    failed: bool = False


class RewritesBlocked(Exception):
    """对齐和校验已经完成，但改稿没能调用模型。"""

    def __init__(self, cause: BaseException, result: PipelineResult):
        super().__init__(str(cause))
        self.cause = cause
        self.result = result


def parse_timeline_text(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise InputError("时间轴为空：请上传 timeline.json 或粘贴 JSON")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"timeline JSON 无法解析（第 {exc.lineno} 行：{exc.msg}）") from exc
    if not isinstance(data, dict):
        raise InputError("timeline 必须是 JSON 对象")
    return data


def parse_must_keep(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def normalize_settings(
    strategy: str,
    chars_per_sec: Any,
    expand_short_ratio: Any,
) -> tuple[str, float, float]:
    strategy = (strategy or "").strip()
    if strategy not in STRATEGIES:
        raise InputError("strategy 必须是 trim、keep_faster 或 expand")
    try:
        cps = float(chars_per_sec)
    except (TypeError, ValueError) as exc:
        raise InputError("chars_per_sec 必须是数字") from exc
    if cps <= 0:
        raise InputError("chars_per_sec 必须大于 0")
    try:
        ratio = float(expand_short_ratio)
    except (TypeError, ValueError) as exc:
        raise InputError("expand_short_ratio 必须是数字") from exc
    if ratio <= 0 or ratio > 1:
        raise InputError("expand_short_ratio 必须在 (0, 1] 内")
    return strategy, cps, ratio


def load_example_inputs() -> tuple[str, str, str, float, str, float]:
    """timeline 文本、原稿、策略、语速、必留句、偏短阈值。"""
    timeline = (EXAMPLES / "demo-timeline.json").read_text(encoding="utf-8")
    draft = (EXAMPLES / "demo-draft-overlong.txt").read_text(encoding="utf-8")
    must_keep = "气孔是发酵留下的，不是后来吹进去的"
    return timeline, draft, "trim", 4.7, must_keep, 0.7


def load_example_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    timeline = json.loads((EXAMPLES / "demo-timeline.json").read_text(encoding="utf-8"))
    fit = json.loads((EXAMPLES / "demo-fit-trim.json").read_text(encoding="utf-8"))
    return timeline, fit


def _order_fit(fit: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    ordered = copy.deepcopy(fit)
    by_id = {}
    for item in ordered.get("segments") or []:
        if isinstance(item, dict) and item.get("segment_id"):
            by_id[item["segment_id"]] = item
    segments = []
    for row in report.get("segments") or []:
        item = by_id.get(row["segment_id"])
        if item is not None:
            segments.append(item)
    if segments:
        ordered["segments"] = segments
    return ordered


def _write_artifacts(
    work: Path,
    fit: dict[str, Any],
    report: dict[str, Any],
) -> tuple[Path, Path, Path, str]:
    fit_out = emit_fit(report, fit) if report.get("segments") else fit
    fit_path = work / "fit.json"
    report_path = work / "validation-report.md"
    html_path = work / "diff.html"
    fit_path.write_text(json.dumps(fit_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = markdown_report(report)
    report_path.write_text(md, encoding="utf-8")
    template = (ASSETS / "diff-template.html").read_text(encoding="utf-8")
    page = render_html(report, template)
    html_path.write_text(page, encoding="utf-8")
    return fit_path, report_path, html_path, page


def _result(
    *,
    timeline: dict[str, Any],
    fit: dict[str, Any],
    report: dict[str, Any],
    source: str,
    work: Path | None = None,
) -> PipelineResult:
    from studio_lib.present import table_rows

    folder = work or Path(tempfile.mkdtemp(prefix="nf-studio-"))
    folder.mkdir(parents=True, exist_ok=True)
    stored = _order_fit(fit, report)
    fit_path, report_path, html_path, page = _write_artifacts(folder, stored, report)
    return PipelineResult(
        ok=bool(report.get("ok")),
        summary="",
        source=source,
        table_rows=table_rows(report),
        html_path=str(html_path),
        fit_path=str(fit_path),
        report_path=str(report_path),
        html_preview=page,
        fit=stored,
        timeline=timeline,
        report=report,
        work_dir=str(folder),
    )


def run_scripts_only(
    timeline: dict[str, Any],
    fit: dict[str, Any],
    *,
    source: str = "recheck",
    work_dir: Path | None = None,
) -> PipelineResult:
    """只跑校验和对照渲染，不调用模型。"""
    rows, tl_errors = validate_timeline(timeline)
    if tl_errors or not rows:
        raise InputError("时间轴不合法：\n" + "\n".join(tl_errors or ["无有效段落"]))
    report = validate_fit(timeline, fit)
    return _result(timeline=timeline, fit=fit, report=report, source=source, work=work_dir)


def revalidate(
    *,
    timeline: dict[str, Any],
    fit: dict[str, Any],
    segment_id: str | None,
    revised: str | None,
    strategy: str,
    chars_per_sec: Any,
    must_keep_text: str,
    expand_short_ratio: Any,
    scope: str,
) -> PipelineResult:
    """把编辑框里的 revised 写回 fit，再只跑脚本。scope 为 one 或 all。"""
    if not timeline or not fit or not fit.get("segments"):
        raise InputError("还没有可重验的稿。请先点「一键演示」或「开始改稿」。")
    strategy, cps, ratio = normalize_settings(strategy, chars_per_sec, expand_short_ratio)
    updated = copy.deepcopy(fit)
    updated["strategy"] = strategy
    updated["chars_per_sec"] = cps
    updated["expand_short_ratio"] = ratio
    updated["must_keep"] = parse_must_keep(must_keep_text)

    if scope == "one":
        if not segment_id:
            raise InputError("请先选择要重验的段落")
        if revised is None:
            raise InputError("请先选择要重验的段落")
        _replace_revised(updated, segment_id, revised)
    elif scope == "all":
        if segment_id and revised is not None:
            _replace_revised(updated, segment_id, revised)
    else:
        raise InputError("重验范围只能是此段或全部")
    return run_scripts_only(timeline, updated, source="recheck")


def _replace_revised(fit: dict[str, Any], segment_id: str, revised: str) -> None:
    for item in fit.get("segments") or []:
        if item.get("segment_id") == segment_id:
            item["revised"] = revised if revised is not None else ""
            return
    raise InputError(f"找不到段落 {segment_id}")


def _apply_settings(fit: dict[str, Any], strategy: str, cps: float, ratio: float, must_keep: list[str]) -> dict[str, Any]:
    updated = copy.deepcopy(fit)
    updated["strategy"] = strategy
    updated["chars_per_sec"] = cps
    updated["expand_short_ratio"] = ratio
    updated["must_keep"] = list(must_keep)
    if "max_chars_per_sec_cap" not in updated:
        updated["max_chars_per_sec_cap"] = 6.0
    return updated


def stream_pipeline(
    *,
    timeline_text: str,
    draft_text: str,
    strategy: str,
    chars_per_sec: Any,
    must_keep_text: str = "",
    expand_short_ratio: Any = 0.7,
    model: str = DEFAULT_MODEL,
    dry_run_scripts: bool = False,
    prebuilt_fit: dict[str, Any] | None = None,
    max_rewrite_rounds: int = 2,
) -> Iterator[StreamEvent]:
    """逐步产出进度。结束时事件带 result。改稿被令牌挡住时抛 RewritesBlocked。"""
    strategy, cps, ratio = normalize_settings(strategy, chars_per_sec, expand_short_ratio)
    must_keep = parse_must_keep(must_keep_text)
    timeline = parse_timeline_text(timeline_text)
    rows, tl_errors = validate_timeline(timeline)
    if tl_errors or not rows:
        raise InputError("时间轴不合法，已停止（不改稿）：\n" + "\n".join(tl_errors or ["无有效段落"]))

    if dry_run_scripts:
        if prebuilt_fit is None:
            raise InputError("dry_run_scripts 需要提供 prebuilt_fit")
        yield StreamEvent("validate", "跳过模型，只跑本地脚本。")
        result = run_scripts_only(timeline, prebuilt_fit, source="preset")
        yield StreamEvent("done", "脚本校验完成。", result=result)
        return

    draft = (draft_text or "").strip()
    if not draft:
        raise InputError("口播全文为空：请上传或粘贴 draft")

    yield StreamEvent("align", f"正在把全文对齐到 {len(rows)} 段。")
    heuristic = try_paragraph_align(
        timeline=timeline,
        draft=draft,
        strategy=strategy,
        chars_per_sec=cps,
        must_keep=must_keep,
        expand_short_ratio=ratio,
    )
    client = None
    if heuristic is not None:
        fit = heuristic
        yield StreamEvent("align", "空行段数和时间轴一致，已按顺序对齐。这一步没有调用模型。")
    else:
        yield StreamEvent("align", "段数对不上，改由模型按画面说明切段。")
        client = make_client()
        fit = align_draft_to_timeline(
            client,
            timeline=timeline,
            draft=draft,
            strategy=strategy,
            chars_per_sec=cps,
            must_keep=must_keep,
            expand_short_ratio=ratio,
            model=model,
        )
        yield StreamEvent("align", "模型已把全文切进各段。原文先原样放在改后，还没改写。")

    fit = _apply_settings(fit, strategy, cps, ratio, must_keep)
    yield StreamEvent("validate", "对齐完成，正在按段重算字数预算。")
    report = validate_fit(timeline, fit)
    state_word = "通过" if report.get("ok") else "未通过"
    yield StreamEvent("validate", f"第一次校验{state_word}。超 {report['totals']['over_by']}，欠 {report['totals']['under_by']}。")

    failures: list[str] = []
    last_segment = ""
    for round_i in range(1, max_rewrite_rounds + 1):
        need = segments_needing_rewrite(report, strategy=strategy, expand_short_ratio=ratio)
        if not need:
            note = "没有需要改写的段。" if round_i == 1 else f"第 {round_i} 轮之后没有剩余要改的段。"
            yield StreamEvent("rewrite", note)
            break
        if client is None:
            try:
                client = make_client()
            except TokenMissingError as exc:
                partial = _result(timeline=timeline, fit=fit, report=report, source="aligned")
                raise RewritesBlocked(exc, partial) from exc
        names = "、".join(row["segment_id"] for row in need)
        yield StreamEvent("rewrite", f"第 {round_i} 轮要改 {len(need)} 段：{names}")
        fit_by_id = {item["segment_id"]: item for item in fit["segments"]}
        for index, row in enumerate(need, start=1):
            sid = row["segment_id"]
            last_segment = sid
            yield StreamEvent(
                "rewrite",
                f"改稿第 {index} 段 · {sid}（预算 {row['budget']}，当前 {row['chars_revised']}）。",
                segment_id=sid,
            )
            try:
                updated = rewrite_segment(
                    client,
                    segment_id=sid,
                    visual=row.get("visual") or "",
                    label=row.get("label") or "",
                    budget=int(row["budget"]),
                    chars_current=int(row["chars_revised"]),
                    strategy=strategy,
                    must_keep=must_keep,
                    original=row.get("original") or "",
                    revised=row.get("revised") or "",
                    expand_short_ratio=ratio,
                    model=model,
                )
            except ModelJSONError as exc:
                failures.append(f"{sid} 的模型回复没法解析，这一段先保持改前文字。{exc}")
                yield StreamEvent("rewrite", failures[-1], segment_id=sid)
                continue
            fit_by_id[sid]["revised"] = updated["revised"]
            if updated.get("note"):
                fit_by_id[sid]["note"] = updated["note"]
        fit["segments"] = [fit_by_id[item["segment_id"]] for item in fit["segments"] if item["segment_id"] in fit_by_id]
        yield StreamEvent("recheck", f"第 {round_i} 轮改完，正在再校验。", segment_id=last_segment)
        report = validate_fit(timeline, fit)
        again = "通过" if report.get("ok") else "未通过"
        yield StreamEvent("recheck", f"再校验{again}。超 {report['totals']['over_by']}。", segment_id=last_segment)

    if failures:
        for item in failures:
            yield StreamEvent("recheck", item, segment_id=last_segment)
    yield StreamEvent("recheck", "正在写出对照页。", segment_id=last_segment)
    result = _result(timeline=timeline, fit=fit, report=report, source="model")
    yield StreamEvent("done", "改稿流程结束。", segment_id=last_segment, result=result)


def run_pipeline(
    *,
    timeline_text: str,
    draft_text: str,
    strategy: str,
    chars_per_sec: Any,
    must_keep_text: str = "",
    expand_short_ratio: Any = 0.7,
    model: str = DEFAULT_MODEL,
    dry_run_scripts: bool = False,
    prebuilt_fit: dict[str, Any] | None = None,
    max_rewrite_rounds: int = 2,
) -> PipelineResult:
    """跑完整个流程并返回最后一次结果。供命令行自测。"""
    result: PipelineResult | None = None
    for event in stream_pipeline(
        timeline_text=timeline_text,
        draft_text=draft_text,
        strategy=strategy,
        chars_per_sec=chars_per_sec,
        must_keep_text=must_keep_text,
        expand_short_ratio=expand_short_ratio,
        model=model,
        dry_run_scripts=dry_run_scripts,
        prebuilt_fit=prebuilt_fit,
        max_rewrite_rounds=max_rewrite_rounds,
    ):
        if event.result is not None:
            result = event.result
    if result is None:
        raise InputError("流程没有产出结果")
    return result


def run_cli_dry_demo() -> int:
    """不调模型：用示例 timeline + demo-fit-trim 跑脚本链路。"""
    timeline_text, fit = load_example_documents()
    result = run_pipeline(
        timeline_text=json.dumps(timeline_text, ensure_ascii=False),
        draft_text="（dry-run 不用）",
        strategy=str(fit.get("strategy") or "trim"),
        chars_per_sec=fit.get("chars_per_sec") or 4.7,
        must_keep_text="\n".join(fit.get("must_keep") or []),
        dry_run_scripts=True,
        prebuilt_fit=fit,
    )
    print("ok" if result.ok else "failed")
    print("strategy", result.report.get("strategy"), "cps", result.report.get("chars_per_sec"))
    print("budget", result.report.get("totals", {}).get("budget"), "over", result.report.get("totals", {}).get("over_by"))
    print("fit", result.fit_path)
    print("html", result.html_path)
    print("report", result.report_path)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(run_cli_dry_demo())
