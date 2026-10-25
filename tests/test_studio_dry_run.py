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
from studio_lib.present import diff_preview, overview_html, steps_html, table_rows  # noqa: E402


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
