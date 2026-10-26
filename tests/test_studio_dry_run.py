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
from studio_lib.present import (  # noqa: E402
    clamp_wizard_target,
    diff_preview,
    materials_present,
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
        self.assertFalse(materials_present("", "有口播"))
        self.assertFalse(materials_present("  { }  ", "   "))
        self.assertTrue(materials_present("{ }", "口播"))
        self.assertIn("第 1 步", wizard_caption(1))
        self.assertIn("下一步", wizard_caption(1))
        self.assertIn("开始改稿", wizard_caption(2))
        self.assertIn("第 4 步", wizard_caption(4))
        idle = steps_html("idle", caption="还没开始。")
        self.assertNotIn('class="active"', idle)
        self.assertIn("还没开始", idle)

    def test_wizard_demo_stays_on_step_one_and_missing_token_lands_on_step_four(self):
        import app as studio_app

        state = {"wizard_step": 1, "max_reached": 1}
        filled = studio_app.load_demo(state)
        timeline_text, draft_text = filled[1], filled[3]
        self.assertIn("segments", timeline_text)
        self.assertTrue(draft_text.strip())
        self.assertEqual(filled[-1]["wizard_step"], 1)
        self.assertEqual(filled[-1]["max_reached"], 1)
        self.assertIn("还不展示改稿结果", filled[-2])
        self.assertTrue(filled[8].get("interactive"))

        blocked = studio_app.go_next({"wizard_step": 1, "max_reached": 1}, "", "", None, None)
        self.assertEqual(blocked[-1]["wizard_step"], 1)
        self.assertIn("都要有内容", blocked[4])

        advanced = studio_app.go_next(filled[-1], timeline_text, draft_text, None, None)
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
                    None,
                    timeline_text,
                    None,
                    draft_text,
                    filled[4],
                    filled[5],
                    filled[6],
                    filled[7],
                )
            )
        finally:
            if previous is not None:
                os.environ[TOKEN_ENV] = previous
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][-1]["wizard_step"], 3)
        self.assertEqual(events[0][-1]["max_reached"], 3)
        self.assertIn("第 3 步", events[0][4])
        self.assertIn("对齐", events[0][9])
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
        self.assertIn("Qwen/Qwen3.5-35B-A3B", present_text)


if __name__ == "__main__":
    unittest.main()
