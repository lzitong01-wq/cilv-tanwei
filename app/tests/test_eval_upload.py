"""标准化验证集测试：上传 → 逐题作答 → 导出。

对应赛题要求：「提供标准化验证集测试接口，并提供可视化的验证集测试功能，
上传验证集数据，通过调用智能体内部接口依次回复相关问题」。

关键口径：**题面可以不带标准答案**——那种情况下只产出回答、不算分，
三口径记为 None（不是 False）。「没有答案」和「答错了」是两回事。
"""

import csv
import io
import json
import sys
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core import evaluator  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402

QUESTION = (
    "甲：清·费墨娟《临江仙·寄怀阿嫂》（词牌“临江仙”，首句“虫韵风声添寥寂，新寒初透帘前。”）。\n"
    "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。\n"
    "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例；"
    "再计算两篇全文仄声比例相差多少个百分点，并只作形式层面的有限判断。"
)


class ParseDatasetTest(unittest.TestCase):
    def test_jsonl_with_and_without_gold(self):
        rows, report = evaluator.parse_dataset(
            json.dumps(
                [
                    {"题号": "A1", "类别": "C1", "问题": QUESTION, "标准答案": "某个答案"},
                    {"题号": "A2", "类别": "C1", "问题": QUESTION},
                ],
                ensure_ascii=False,
            ),
            "a.json",
        )
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["with_gold"], 1)
        self.assertEqual(report["without_gold"], 1)
        self.assertTrue(report["warnings"])
        self.assertEqual(rows[0]["qid"], "A1")
        self.assertEqual(rows[1]["gold"], "")

    def test_jsonl_format_detected(self):
        """JSONL 首行是 {，不能被当成单个 JSON 对象。"""
        text = "\n".join(
            json.dumps({"题号": "B%d" % i, "问题": QUESTION}, ensure_ascii=False) for i in range(3)
        )
        rows, report = evaluator.parse_dataset(text, "b.jsonl")
        self.assertEqual(report["format"], "jsonl")
        self.assertEqual(report["total"], 3)

    def test_csv_and_field_aliases(self):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["qid", "category", "question", "answer"])
        writer.writeheader()
        writer.writerow({"qid": "C1", "category": "C1", "question": QUESTION, "answer": "x"})
        rows, report = evaluator.parse_dataset(buffer.getvalue(), "c.csv")
        self.assertEqual(report["format"], "csv")
        self.assertEqual(report["total"], 1)
        self.assertEqual(rows[0]["qid"], "C1")
        self.assertEqual(rows[0]["gold"], "x")

    def test_json_array_format(self):
        rows, report = evaluator.parse_dataset(
            json.dumps([{"题号": "D1", "问题": QUESTION}], ensure_ascii=False), "d.json"
        )
        self.assertEqual(report["format"], "json")
        self.assertEqual(rows[0]["qid"], "D1")

    def test_broken_input_is_rejected(self):
        for bad, filename in [("", "x.txt"), ("不是 JSON\n第二行", "x.txt")]:
            with self.assertRaises(ApiError):
                evaluator.parse_dataset(bad, filename)

    def test_missing_question_rows_are_skipped_with_warning(self):
        rows, report = evaluator.parse_dataset(
            json.dumps([{"题号": "E1", "问题": QUESTION}, {"题号": "E2"}], ensure_ascii=False),
            "e.json",
        )
        self.assertEqual(report["total"], 1)
        self.assertTrue(any("缺少问题" in item for item in report["warnings"]))


class UploadEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app(load_settings()).test_client()

    def _wait(self, job_id: str) -> dict:
        for _ in range(100):
            body = self.client.get("/api/eval/%s" % job_id).get_json()
            if body["data"]["state"] != "running":
                return body["data"]
            time.sleep(0.1)
        self.fail("跑分任务未在预期时间内结束")

    def test_upload_runs_and_answers_without_gold(self):
        dataset = json.dumps(
            [{"题号": "U1", "类别": "同调全篇声律比例差", "问题": QUESTION}], ensure_ascii=False
        )
        response = self.client.post(
            "/api/eval/upload",
            data={"file": (io.BytesIO(dataset.encode("utf-8")), "验证集.jsonl")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 202)
        data = response.get_json()["data"]
        self.assertEqual(data["questions"], 1)
        self.assertEqual(data["parsed"]["with_gold"], 0)

        status = self._wait(data["job_id"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["summary"]["scored"], 0)
        self.assertEqual(status["summary"]["unscored"], 1)

        items = self.client.get("/api/eval/%s/items" % data["job_id"]).get_json()["data"]
        self.assertEqual(items["total"], 1)
        item = items["items"][0]
        self.assertEqual(item["status"], "ok")
        self.assertTrue(item["answer"])
        # 没有标准答案 → 三口径是 None 而不是 False
        self.assertIsNone(item["raw"])
        self.assertIsNone(item["normalized"])
        self.assertIsNone(item["proxy"])

    def test_upload_with_gold_is_scored(self):
        dataset = json.dumps(
            [
                {
                    "题号": "U2",
                    "类别": "同调全篇声律比例差",
                    "问题": QUESTION,
                    "标准答案": "某个答案",
                }
            ],
            ensure_ascii=False,
        )
        response = self.client.post(
            "/api/eval/upload",
            data={"file": (io.BytesIO(dataset.encode("utf-8")), "带答案.jsonl")},
            content_type="multipart/form-data",
        )
        data = response.get_json()["data"]
        self.assertEqual(data["parsed"]["with_gold"], 1)
        status = self._wait(data["job_id"])
        self.assertEqual(status["summary"]["scored"], 1)
        self.assertEqual(status["summary"]["unscored"], 0)

    def test_upload_rejects_empty(self):
        response = self.client.post("/api/eval/upload", data=b"", content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    def test_export_answers(self):
        dataset = json.dumps(
            [{"题号": "U3", "类别": "C1", "问题": QUESTION}], ensure_ascii=False
        )
        response = self.client.post(
            "/api/eval/upload",
            data={"file": (io.BytesIO(dataset.encode("utf-8")), "v.jsonl")},
            content_type="multipart/form-data",
        )
        job_id = response.get_json()["data"]["job_id"]
        self._wait(job_id)

        exported = self.client.get("/api/eval/%s/export?format=jsonl" % job_id)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("attachment", exported.headers.get("Content-Disposition", ""))
        rows = [json.loads(line) for line in exported.get_data(as_text=True).splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["回答"])

        csv_export = self.client.get("/api/eval/%s/export?format=csv" % job_id)
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("题号", csv_export.get_data(as_text=True))

    def test_export_rejects_bad_format(self):
        response = self.client.get("/api/eval/whatever/export?format=xml")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
