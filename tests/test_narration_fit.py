from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(os.environ.get("NARRATION_FIT_ROOT", Path(__file__).resolve().parents[1]))
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_narration_fit as validator  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def temporary_directory(prefix: str):
    test_root = os.environ.get("NARRATION_FIT_TEST_TMP")
    if test_root:
        Path(test_root).mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=test_root)


class NarrationFitRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.timeline = read_json(ROOT / "examples" / "demo-timeline.json")
        cls.examples = {
            "trim": read_json(ROOT / "examples" / "demo-fit-trim.json"),
            "keep_faster": read_json(ROOT / "examples" / "demo-fit-keep-faster.json"),
            "expand": read_json(ROOT / "examples" / "demo-fit-expand.json"),
        }

    def invoke(self, fit=None, timeline=None, *, allow_unassigned=False, render=False):
        with temporary_directory(prefix="narration-fit-test-") as directory:
            temp = Path(directory)
            timeline_path = temp / "timeline.json"
            fit_path = temp / "fit.json"
            timeline_path.write_text(
                json.dumps(timeline if timeline is not None else self.timeline, ensure_ascii=False),
                encoding="utf-8",
            )
            fit_path.write_text(
                json.dumps(fit if fit is not None else self.examples["trim"], ensure_ascii=False),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            if render:
                html_path = temp / "out" / "diff.html"
                report_path = temp / "out" / "report.md"
                command = [
                    sys.executable,
                    str(SCRIPTS / "render_diff.py"),
                    "--timeline",
                    str(timeline_path),
                    "--fit",
                    str(fit_path),
                    "--html",
                    str(html_path),
                    "--report",
                    str(report_path),
                ]
            else:
                html_path = None
                report_path = temp / "report.md"
                json_path = temp / "report.json"
                command = [
                    sys.executable,
                    str(SCRIPTS / "validate_narration_fit.py"),
                    "--timeline",
                    str(timeline_path),
                    "--fit",
                    str(fit_path),
                    "--report",
                    str(report_path),
                    "--json-out",
                    str(json_path),
                ]
            if allow_unassigned:
                command.append("--allow-unassigned")
            completed = subprocess.run(command, capture_output=True, text=True, env=env)
            report = read_json(json_path) if not render and json_path.exists() else None
            markdown = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
            page = html_path.read_text(encoding="utf-8") if html_path and html_path.exists() else ""
            return completed, report, markdown, page

    def test_three_strategy_fixtures_pass_with_expected_rates_and_budgets(self):
        expected = {
            "trim": ("4.7", [28, 56, 47, 37]),
            "keep_faster": ("5.5", [33, 66, 55, 44]),
            "expand": ("4.7", [28, 56, 47, 37]),
        }
        for strategy, (rate, budgets) in expected.items():
            with self.subTest(strategy=strategy):
                completed, report, _, _ = self.invoke(copy.deepcopy(self.examples[strategy]))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(report["ok"])
                self.assertEqual(report["strategy"], strategy)
                self.assertEqual(report["chars_per_sec"], rate)
                self.assertEqual([row["budget"] for row in report["segments"]], budgets)

    def test_over_budget_revised_text_fails_without_changing_rate(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit["segments"][0]["revised"] = fit["segments"][0]["original"]
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(report["chars_per_sec"], "4.7")
        self.assertEqual(report["segments"][0]["status"], "over")
        self.assertTrue(any("trim 锁定本次语速" in error for error in report["errors"]))

    def test_overlapping_nonpositive_and_duplicate_timeline_segments_fail(self):
        cases = {}
        overlap = copy.deepcopy(self.timeline)
        overlap["segments"][1]["start_s"] = 5
        cases["overlap"] = overlap
        nonpositive = copy.deepcopy(self.timeline)
        nonpositive["segments"][1]["end_s"] = nonpositive["segments"][1]["start_s"]
        cases["nonpositive"] = nonpositive
        duplicate = copy.deepcopy(self.timeline)
        duplicate["segments"][1]["segment_id"] = duplicate["segments"][0]["segment_id"]
        cases["duplicate_id"] = duplicate
        for name, timeline in cases.items():
            with self.subTest(case=name):
                completed, report, _, _ = self.invoke(timeline=timeline)
                self.assertEqual(completed.returncode, 1)
                self.assertFalse(report["ok"])

    def test_touching_and_gapped_timeline_intervals_are_allowed(self):
        base_timeline = {
            "title": "区间边界",
            "segments": [
                {"segment_id": "a", "start_s": 0, "end_s": 1, "visual": "画面甲"},
                {"segment_id": "b", "start_s": 1, "end_s": 2, "visual": "画面乙"},
            ],
        }
        fit = {
            "strategy": "trim",
            "chars_per_sec": 1,
            "must_keep": [],
            "unassigned": "",
            "segments": [
                {"segment_id": "a", "original": "甲", "revised": "甲"},
                {"segment_id": "b", "original": "乙", "revised": "乙"},
            ],
        }
        touching, _, _, _ = self.invoke(fit, base_timeline)
        self.assertEqual(touching.returncode, 0, touching.stderr)
        gapped_timeline = copy.deepcopy(base_timeline)
        gapped_timeline["segments"][1]["start_s"] = 3
        gapped_timeline["segments"][1]["end_s"] = 4
        gapped, _, _, _ = self.invoke(fit, gapped_timeline)
        self.assertEqual(gapped.returncode, 0, gapped.stderr)

    def test_missing_and_extra_fit_segments_fail(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit["segments"].pop()
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 1)
        self.assertTrue(any("fit 缺少时间轴段落" in error for error in report["errors"]))
        fit = copy.deepcopy(self.examples["trim"])
        fit["segments"].append(
            {"segment_id": "not-in-timeline", "original": "甲", "revised": "甲"}
        )
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 1)
        self.assertTrue(any("fit 含有时间轴中不存在的段落" in error for error in report["errors"]))

    def test_required_phrase_must_exist_whole_in_one_revised_segment(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit["must_keep"] = ["甲乙"]
        fit["segments"][0]["revised"] = "甲"
        fit["segments"][1]["revised"] = "乙"
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 1)
        self.assertTrue(any("必留句未出现在同一段" in error for error in report["errors"]))

    def test_unassigned_text_fails_by_default_and_is_reported_when_allowed(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit["unassigned"] = "这句还没有分配到任何时间轴段。"
        blocked, _, _, _ = self.invoke(fit)
        self.assertEqual(blocked.returncode, 1)
        allowed, report, markdown, _ = self.invoke(fit, allow_unassigned=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(report["unassigned"], fit["unassigned"])
        self.assertIn(fit["unassigned"], markdown)

    def test_missing_illegal_and_above_cap_rates_fail(self):
        cases = {}
        missing = copy.deepcopy(self.examples["trim"])
        del missing["chars_per_sec"]
        cases["missing"] = missing
        illegal = copy.deepcopy(self.examples["trim"])
        illegal["chars_per_sec"] = "fast"
        cases["illegal"] = illegal
        above_cap = copy.deepcopy(self.examples["trim"])
        above_cap["chars_per_sec"] = 7
        cases["above_cap"] = above_cap
        for name, fit in cases.items():
            with self.subTest(case=name):
                completed, report, _, _ = self.invoke(fit)
                self.assertEqual(completed.returncode, 1)
                self.assertFalse(report["ok"])

    def test_character_count_and_decimal_budget_rules(self):
        self.assertEqual(validator.count_chars(" 甲\t乙\n！ABC 123 "), 9)
        self.assertEqual(
            validator.budget_chars(Decimal("10"), Decimal("4.7")),
            47,
        )

    def test_expand_warns_if_short_text_grows_but_stays_below_threshold(self):
        fit = copy.deepcopy(self.examples["expand"])
        segment = fit["segments"][1]
        segment["original"] = "短"
        segment["revised"] = "短稿补了几字"
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["segments"][1]["fit_action"], "still_short")
        self.assertTrue(any("仍偏短" in warning for warning in report["warnings"]))

    def test_expand_warns_when_short_text_is_unchanged(self):
        fit = copy.deepcopy(self.examples["expand"])
        segment = fit["segments"][1]
        segment["original"] = "短"
        segment["revised"] = "短"
        completed, report, _, _ = self.invoke(fit)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["segments"][1]["fit_action"], "still_short")
        self.assertTrue(any("仍偏短" in warning for warning in report["warnings"]))

    def test_editing_one_segment_does_not_change_other_segment_numbers(self):
        fit = copy.deepcopy(self.examples["trim"])
        _, before, _, _ = self.invoke(fit)
        fit["segments"][0]["revised"] = "只改这一小段。"
        _, after, _, _ = self.invoke(fit)
        before_rows = {row["segment_id"]: row for row in before["segments"]}
        after_rows = {row["segment_id"]: row for row in after["segments"]}
        for segment_id in ("s2", "s3", "s4"):
            with self.subTest(segment=segment_id):
                for field in ("budget", "chars_original", "chars_revised", "over_by", "under_by"):
                    self.assertEqual(before_rows[segment_id][field], after_rows[segment_id][field])

    def test_missing_file_and_malformed_json_return_exit_code_two(self):
        with temporary_directory(prefix="narration-fit-input-error-") as directory:
            temp = Path(directory)
            fit_path = temp / "fit.json"
            timeline_path = temp / "timeline.json"
            fit_path.write_text(json.dumps(self.examples["trim"], ensure_ascii=False), encoding="utf-8")
            base_command = [
                sys.executable,
                str(SCRIPTS / "validate_narration_fit.py"),
                "--timeline",
                str(timeline_path),
                "--fit",
                str(fit_path),
            ]
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            missing = subprocess.run(base_command, capture_output=True, text=True, env=env)
            self.assertEqual(missing.returncode, 2)
            timeline_path.write_text("{", encoding="utf-8")
            malformed = subprocess.run(base_command, capture_output=True, text=True, env=env)
            self.assertEqual(malformed.returncode, 2)

    def test_html_success_escapes_user_content_and_shows_diff_marks(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit.pop("must_keep", None)
        timeline = copy.deepcopy(self.timeline)
        timeline["title"] = "<script>title</script>"
        fit["segments"][0]["original"] = "原文 <b>old</b>"
        fit["segments"][0]["revised"] = "改稿 <img src=x>"
        completed, _, _, page = self.invoke(fit, timeline, render=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("&lt;script&gt;title&lt;/script&gt;", page)
        self.assertIn("&lt;", page)
        self.assertIn("&gt;", page)
        self.assertNotIn("<img src=x>", page)
        self.assertIn("<del>", page)
        self.assertIn("<ins>", page)
        self.assertIn("本次语速", page)

    def test_failed_validation_still_writes_html_with_matching_exit_code(self):
        fit = copy.deepcopy(self.examples["trim"])
        fit["segments"][0]["revised"] = fit["segments"][0]["original"]
        completed, _, markdown, page = self.invoke(fit, render=True)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("未通过", page)
        self.assertIn("超预算", page)
        self.assertIn("问题", markdown)


if __name__ == "__main__":
    unittest.main()
