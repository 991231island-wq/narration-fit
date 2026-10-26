"""工作室不调模型的路径：预置示例、重验、人话错误。"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "studio"
sys.path.insert(0, str(STUDIO))

from studio_lib.errors import (  # noqa: E402
    TOKEN_ENV,
    HumanError,
    ModelJSONError,
    TokenMissingError,
    humanize,
    redact,
)
from studio_lib.pipeline import (  # noqa: E402
    REPO_ROOT,
    SCRIPTS,
    load_example_documents,
    revalidate,
    run_pipeline,
)
from studio_lib.materials import (  # noqa: E402
    add_cut,
    build_job_inputs,
    example_materials,
    segments_ready,
)
from studio_lib.present import (  # noqa: E402
    FOOTER_HTML,
    HERO_HTML,
    clamp_wizard_target,
    diff_preview,
    overview_html,
    steps_html,
    table_rows,
    wizard_caption,
)


class StudioDryRunTests(unittest.TestCase):
    def test_scripts_come_from_repo_root(self):
        self.assertEqual(REPO_ROOT, ROOT)
        self.assertEqual(SCRIPTS, ROOT / "scripts")
        self.assertTrue((SCRIPTS / "validate_narration_fit.py").is_file())
        self.assertTrue((SCRIPTS / "render_diff.py").is_file())

    def test_dry_run_preset_passes_and_writes_diff(self):
        timeline, fit = load_example_documents()
        result = run_pipeline(
            timeline_text=json.dumps(timeline, ensure_ascii=False),
            draft_text="（dry-run 不用）",
            strategy="trim",
            chars_per_sec=4.7,
            must_keep_text="气孔是发酵留下的，不是后来吹进去的",
            dry_run_scripts=True,
            prebuilt_fit=fit,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.report["chars_per_sec"], "4.7")
        self.assertEqual(result.report["totals"]["over_by"], 0)
        self.assertGreater(result.report["totals"]["budget"], 0)
        self.assertEqual(len(result.table_rows), 4)
        self.assertEqual(result.table_rows[0][0], "s1")
        page = Path(result.html_path).read_text(encoding="utf-8")
        self.assertIn("馒头为什么会胀起来", page)
        self.assertIn("<del>", page)
        preview = diff_preview(page)
        self.assertIn('id="nf-diff"', preview)
        self.assertIn("<del>", preview)
        overview = overview_html(result.report, "preset")
        self.assertIn("通过", overview)
        self.assertIn("总预算", overview)
        self.assertIn(str(result.report["totals"]["budget"]), overview)
        self.assertIn("对齐", steps_html("done", caption="预置"))
        self.assertIn("再校验", steps_html("rewrite", segment_id="s2"))
        self.assertIn("改稿 · s2", steps_html("rewrite", segment_id="s2"))

    def test_revalidate_one_segment_keeps_the_others(self):
        timeline, fit = load_example_documents()
        before = run_pipeline(
            timeline_text=json.dumps(timeline, ensure_ascii=False),
            draft_text="",
            strategy="trim",
            chars_per_sec=4.7,
            must_keep_text="气孔是发酵留下的，不是后来吹进去的",
            dry_run_scripts=True,
            prebuilt_fit=fit,
        )
        kept = before.report["segments"][1]["revised"]
        result = revalidate(
            timeline=timeline,
            fit=before.fit,
            segment_id="s1",
            revised="面" * 80,
            strategy="trim",
            chars_per_sec=4.7,
            must_keep_text="气孔是发酵留下的，不是后来吹进去的",
            expand_short_ratio=0.7,
            scope="one",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "recheck")
        self.assertGreater(result.report["segments"][0]["over_by"], 0)
        self.assertEqual(result.report["segments"][1]["revised"], kept)
        self.assertTrue(any("s1" in item for item in result.report["errors"]))
        self.assertNotIn("Traceback", result.summary + "\n".join(result.report["errors"]))

    def test_revalidate_all_uses_current_speed(self):
        timeline, fit = load_example_documents()
        base = revalidate(
            timeline=timeline,
            fit=fit,
            segment_id=None,
            revised=None,
            strategy="trim",
            chars_per_sec=4.7,
            must_keep_text="气孔是发酵留下的，不是后来吹进去的",
            expand_short_ratio=0.7,
            scope="all",
        )
        faster = revalidate(
            timeline=timeline,
            fit=fit,
            segment_id=None,
            revised=None,
            strategy="keep_faster",
            chars_per_sec=5.5,
            must_keep_text="气孔是发酵留下的，不是后来吹进去的",
            expand_short_ratio=0.7,
            scope="all",
        )
        self.assertEqual(base.report["totals"]["budget"], 28 + 56 + 47 + 37)
        self.assertEqual(faster.report["chars_per_sec"], "5.5")
        self.assertGreater(faster.report["totals"]["budget"], base.report["totals"]["budget"])
        self.assertTrue(faster.ok)

    def test_human_errors_explain_cause_and_fix_without_traceback(self):
        samples = [
            TokenMissingError("未找到环境变量"),
            ModelJSONError("回复中找不到 JSON 对象"),
            type("InputError", (Exception,), {})("timeline JSON 无法解析（第 2 行：Expecting value）"),
            type("AuthenticationError", (Exception,), {})("invalid api key"),
            type("APIConnectionError", (Exception,), {})("connection refused"),
            type("RateLimitError", (Exception,), {})("slow down"),
        ]
        for exc in samples:
            text = humanize(exc).markdown()
            self.assertIsInstance(humanize(exc), HumanError)
            self.assertIn("**原因**", text)
            self.assertIn("**怎么修**", text)
            self.assertNotIn("Traceback", text)
            self.assertNotIn('File "', text)

        leaked = type("RuntimeError", (Exception,), {})(
            'Traceback (most recent call last):\n  File "secret.py", line 1\nboom'
        )
        hidden = humanize(leaked).markdown()
        self.assertNotIn("secret.py", hidden)
        self.assertIn("调用栈已隐藏", hidden)

        previous = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = "sk-supersecretvalue"
        try:
            cleaned = redact("request failed for sk-supersecretvalue")
        finally:
            if previous is None:
                os.environ.pop(TOKEN_ENV, None)
            else:
                os.environ[TOKEN_ENV] = previous
        self.assertNotIn("supersecretvalue", cleaned)

    def test_wizard_blocks_unfinished_steps(self):
        self.assertEqual(clamp_wizard_target(1, 1), 1)
        self.assertIsNone(clamp_wizard_target(2, 1))
        self.assertIsNone(clamp_wizard_target(4, 1))
        self.assertEqual(clamp_wizard_target(1, 4), 1)
        self.assertEqual(clamp_wizard_target(4, 4), 4)
        self.assertIsNone(clamp_wizard_target(0, 4))
        self.assertFalse(segments_ready([]))
        self.assertFalse(segments_ready([{"start_s": 0, "end_s": 4, "narration": "  ", "visual": "", "label": ""}]))
        self.assertTrue(
            segments_ready([{"start_s": 0, "end_s": 4, "narration": "有口播", "visual": "", "label": ""}])
        )
        self.assertEqual(wizard_caption(1), "")
        self.assertEqual(wizard_caption(2), "")
        self.assertEqual(wizard_caption(4), "")
        self.assertEqual(wizard_caption(4, "还没解锁。"), "还没解锁。")
        idle = steps_html("idle", caption="还没开始。")
        self.assertNotIn('class="active"', idle)
        self.assertIn("还没开始", idle)

    def test_wizard_demo_stays_on_step_one_and_missing_token_lands_on_step_four(self):
        import app as studio_app

        state = {"wizard_step": 1, "max_reached": 1, "segments": [], "active": None, "title": ""}
        filled = studio_app.load_demo(state)
        demo_state = filled[-1]
        self.assertEqual(demo_state["wizard_step"], 1)
        self.assertEqual(demo_state["max_reached"], 1)
        self.assertEqual(len(demo_state["segments"]), 4)
        self.assertTrue(all(row["narration"].strip() for row in demo_state["segments"]))
        self.assertIn("4 段", filled[-2])
        self.assertNotIn("还不改稿", filled[-2])
        self.assertTrue(filled[9].get("interactive"))
        app_source = (STUDIO / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("时间轴 JSON", app_source)
        self.assertNotIn("口播全文", app_source)

        blocked = studio_app.go_next(state, "", None, None, None, "", "")
        self.assertEqual(blocked[-1]["wizard_step"], 1)
        self.assertEqual(blocked[11], "")
        self.assertIn("还没有分段", blocked[-2])

        first = demo_state["segments"][0]
        advanced = studio_app.go_next(
            demo_state,
            demo_state["title"],
            0,
            first["start_s"],
            first["end_s"],
            first["visual"],
            first["narration"],
        )
        self.assertEqual(advanced[-1]["wizard_step"], 2)
        self.assertGreaterEqual(advanced[-1]["max_reached"], 2)
        locked = studio_app.go_step(4, advanced[-1])
        self.assertEqual(locked[-1]["wizard_step"], 2)
        self.assertIn("还没解锁", locked[4])

        previous = os.environ.pop(TOKEN_ENV, None)
        try:
            events = list(
                studio_app.run_job(
                    advanced[-1],
                    advanced[-1]["title"],
                    "trim",
                    4.7,
                    "气孔是发酵留下的，不是后来吹进去的",
                    0.7,
                )
            )
        finally:
            if previous is not None:
                os.environ[TOKEN_ENV] = previous
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][-1]["wizard_step"], 3)
        self.assertEqual(events[0][-1]["max_reached"], 3)
        self.assertEqual(events[0][4], "")
        self.assertIn("对齐", events[0][9])
        self.assertNotIn("只有改写", events[0][10])
        self.assertEqual(events[-1][-1]["wizard_step"], 4)
        self.assertEqual(events[-1][-1]["max_reached"], 4)
        self.assertIn("还不能调用模型", events[-1][11])
        self.assertIn("MODELSCOPE_API_TOKEN", events[-1][11])
        self.assertNotIn("Traceback", events[-1][11])
        self.assertIn("结果总览", events[-1][12])

    def test_sources_do_not_embed_a_token(self):
        app_text = (STUDIO / "app.py").read_text(encoding="utf-8")
        align_text = (STUDIO / "studio_lib" / "align_and_rewrite.py").read_text(encoding="utf-8")
        present_text = (STUDIO / "studio_lib" / "present.py").read_text(encoding="utf-8")
        for text in (app_text, align_text, present_text):
            self.assertNotIn("sk-", text)
            self.assertNotIn("api_key=\"", text)
        self.assertIn("MODELSCOPE_API_TOKEN", app_text)
        self.assertIn("MODELSCOPE_API_TOKEN", align_text)
        self.assertIn("Qwen/Qwen3.5-35B-A3B", align_text)
        self.assertIn("https://api-inference.modelscope.cn/v1", align_text)
        self.assertNotIn("Qwen/Qwen3.5-35B-A3B", present_text)
        self.assertNotIn("api-inference.modelscope.cn", present_text)
        self.assertNotIn("MODELSCOPE_API_TOKEN", present_text)

    def test_studio_theme_uses_brand_tokens(self):
        css = (STUDIO / "theme.css").read_text(encoding="utf-8")
        compact = "".join(css.split())
        for token in (
            "--ck-bg:#f7f7f9",
            "--ck-surface:#ffffff",
            "--ck-text:#25233a",
            "--ck-text-soft:#69647b",
            "--ck-accent-a:#7656ed",
            "--ck-accent-mid:#7c6cff",
            "--ck-accent-b:#5f71f2",
            "--ck-radius-xl:28px",
            "linear-gradient(135deg,#fff8ee0%,#f7f2ff55%,#eef8ff100%)",
        ):
            self.assertIn(token, compact)
        self.assertNotIn("#efe6d6", css)
        self.assertNotIn("#9a5322", css)
        self.assertIn("::-webkit-inner-spin-button", css)
        self.assertIn("appearance:textfield", compact)
        self.assertIn(".block.hide-container", css)
        self.assertIn('textarea[rows="1"]', css)
        self.assertIn(".nf-step-panel .styler", css)
        self.assertIn("--block-title-background-fill:transparent", compact)
        self.assertIn("--block-label-background-fill:transparent", compact)
        self.assertIn("nf-hero", HERO_HTML)
        self.assertIn("口播对轴改稿", HERO_HTML)
        self.assertNotIn("nf-lede", HERO_HTML)
        self.assertNotIn("nf-flow", HERO_HTML)
        self.assertNotIn("准备材料", HERO_HTML)
        self.assertNotIn("时间轴稿", HERO_HTML)
        self.assertIn("口播对轴改稿", FOOTER_HTML)
        self.assertNotIn("MODELSCOPE_API_TOKEN", FOOTER_HTML)
        self.assertNotIn("api-inference", FOOTER_HTML)
        app_text = (STUDIO / "app.py").read_text(encoding="utf-8")
        self.assertIn("STEP 01", app_text)
        self.assertIn("STEP 04", app_text)
        self.assertNotIn("#9a5322", app_text)
        self.assertNotIn("theme=build_theme()", app_text)

    def test_segment_marks_become_timeline_and_draft(self):
        segments, active, message = add_cut([], 6)
        self.assertEqual(active, 0)
        self.assertIn("第 1 段", message)
        segments, active, _message = add_cut(segments, 18)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[1]["start_s"], 6)
        self.assertEqual(segments[1]["end_s"], 18)
        segments[0]["narration"] = "第一段口播"
        segments[1]["narration"] = "第二段口播\n仍是这一段"
        self.assertTrue(segments_ready(segments))
        timeline_text, draft = build_job_inputs("试用", segments)
        timeline = json.loads(timeline_text)
        self.assertEqual([row["segment_id"] for row in timeline["segments"]], ["s1", "s2"])
        self.assertEqual(timeline["segments"][0]["visual"], "（这段还没写画面）")
        self.assertEqual(draft.split("\n\n"), ["第一段口播", "第二段口播 仍是这一段"])
        title, example, *_rest = example_materials()
        self.assertEqual(title, "馒头为什么会胀起来")
        self.assertEqual(len(example), 4)
        self.assertTrue(segments_ready(example))


if __name__ == "__main__":
    unittest.main()
