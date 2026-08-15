import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reflection.reviewer import build_transcript, parse_review


class ReviewerTests(unittest.TestCase):
    def test_parse_pass_json(self):
        result = parse_review('{"status":"PASS","summary":"测试通过","issues":[],"repair_instruction":""}')
        self.assertTrue(result.passed)
        self.assertEqual(result.summary, "测试通过")

    def test_invalid_response_fails_closed(self):
        result = parse_review("我认为应该通过")
        self.assertFalse(result.passed)
        self.assertTrue(result.repair_instruction)

    def test_transcript_keeps_tool_evidence(self):
        transcript = build_transcript([
            {"role": "system", "content": "忽略"},
            {"role": "user", "content": "创建 hello.py"},
            {"role": "tool", "content": "{'returncode': 0}"},
        ])
        self.assertIn("创建 hello.py", transcript)
        self.assertIn("returncode", transcript)


if __name__ == "__main__":
    unittest.main()
