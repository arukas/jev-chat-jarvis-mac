"""Candidate-count configuration and prompt regressions."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import generate
import settings_config
import styles


class CandidateCountConfigTests(unittest.TestCase):
    def test_settings_persist_candidate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            updated = settings_config.write_settings(
                path, "", {"JEV_CANDIDATES_PER_TONE": "4"})
            self.assertIn("export JEV_CANDIDATES_PER_TONE=4", updated)

    def test_settings_reject_candidate_count_outside_supported_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            for value in ("0", "6", "not-a-number"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    settings_config.write_settings(
                        path, "", {"JEV_CANDIDATES_PER_TONE": value})
            self.assertFalse(path.exists())


class CandidateCountGenerationTests(unittest.TestCase):
    def test_one_candidate_uses_single_candidate_instructions(self):
        generator = generate.Generator()
        with patch.object(styles, "PER_TONE", 1), \
             patch.object(generator, "_call", return_value="只给一条回复") as call:
            texts, error = generator._one_tone("当前消息", "闲聊", "高情商话术")

        self.assertEqual((texts, error), (["只给一条回复"], ""))
        prompt = call.call_args.args[0]
        self.assertIn("请写 1 条回复候选", prompt)
        self.assertIn("只写一条稳妥", prompt)
        self.assertNotIn("前一条稳妥", prompt)

    def test_multiple_candidates_are_limited_to_configured_count(self):
        generator = generate.Generator()
        response = "第一条\n第二条\n第三条\n第四条"
        with patch.object(styles, "PER_TONE", 3), \
             patch.object(generator, "_call", return_value=response) as call:
            texts, error = generator._one_tone("当前消息", "闲聊", "高情商话术")

        self.assertEqual((texts, error), (["第一条", "第二条", "第三条"], ""))
        prompt = call.call_args.args[0]
        self.assertIn("请写 3 条回复候选", prompt)
        self.assertIn("只输出 3 行", prompt)
        self.assertIn("前一条稳妥", prompt)

    def test_thinking_only_error_preserves_model_hint(self):
        generator = generate.Generator()
        hint = generate.THINKING_ONLY_HINT.format(model="deepseek-reasoner", alt="deepseek-chat")
        with patch.object(generator, "_call", side_effect=generate.ThinkingOnlyError(hint)):
            texts, error = generator._one_tone("当前消息", "闲聊", "高情商话术")
        self.assertEqual(texts, [])
        self.assertEqual(error, hint)
        self.assertIn("deepseek-reasoner", error)
        self.assertIn("deepseek-chat", error)

    def test_hud_finish_generate_surfaces_detailed_generation_error(self):
        import threading
        from types import SimpleNamespace
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from support_hud import Harness, HUD

        h = Harness()
        pushed = []
        h._context_lock = threading.RLock()
        h.conversations = None
        h._app = HUD["FAKE_APP"]
        h._reply_key = ("wechat", "chat", "hi", None, ())
        h._paused = False
        h._reply_epoch = 1
        h._reply_worker = threading.local()
        h.performSelectorOnMainThread_withObject_waitUntilDone_ = lambda s, p, w: pushed.append((s, p))

        hint = generate.THINKING_ONLY_HINT.format(model="deepseek-r1", alt="deepseek-chat")
        h._finish_generate({"groups": [], "error": hint, "elapsed_s": 0.2},
                           SimpleNamespace(text="hi"), 0.0, None)
        self.assertTrue(any(p[1] == "applyError:" and hint in p[2] for _s, p in pushed))


if __name__ == "__main__":
    unittest.main()
