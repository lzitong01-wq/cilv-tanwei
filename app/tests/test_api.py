"""竖切片接口测试：不依赖网络与模型，只验契约与不变量。

运行：cd app/backend && python -m unittest discover -s ../tests -v
题库未就位时相关用例显式 skip，不伪装通过。
"""

import json
import os
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core.config import load_settings  # noqa: E402

QUIZ_DIR = Path(
    os.environ.get(
        "QUIZ_DIR",
        str(Path.home() / "Downloads" / "薪火人文-清词-1000题库-V5版本"),
    )
)


def quiz_rows():
    path = QUIZ_DIR / "公开测试集_700题.jsonl"
    if not path.is_file():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pick(category: str):
    rows = quiz_rows()
    if not rows:
        return None
    for row in rows:
        if row.get("类别") == category:
            return row
    return None


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(load_settings())
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["app"], "ok")
        self.assertEqual(body["data"]["db"], "ok")
        self.assertEqual(body["data"]["works"], 423)
        self.assertTrue(body["trace_id"])

    def test_meta(self):
        body = self.client.get("/api/meta").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["works"], 423)
        self.assertIn("清", body["data"]["periods"])

    def test_works_pagination(self):
        body = self.client.get("/api/works?page=1&page_size=5").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["data"]["items"]), 5)
        self.assertEqual(body["data"]["total"], 423)
        self.assertEqual(body["data"]["items"][0]["tier_label"], "官方标注")

    def test_works_rejects_bad_page(self):
        response = self.client.get("/api/works?page=0")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_INPUT")

    def test_search_requires_query(self):
        response = self.client.get("/api/search")
        self.assertEqual(response.status_code, 400)

    def test_search_two_chars(self):
        body = self.client.get("/api/search?q=西风").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["mode"], "bigram")
        self.assertEqual(body["data"]["index"], "sqlite")
        self.assertGreater(body["data"]["total"], 0)
        first = body["data"]["items"][0]
        self.assertTrue(first["title"], "命中条目必须带题名")
        self.assertTrue(first["poet"], "命中条目必须带作者")
        self.assertTrue(first["matched"], "命中条目必须带命中句")

    def test_search_single_char(self):
        body = self.client.get("/api/search?q=梅").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["mode"], "unigram")
        self.assertGreater(body["data"]["total"], 0)

    def test_search_three_chars_uses_trigram(self):
        body = self.client.get("/api/search?q=桂丛香").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["mode"], "trigram")
        self.assertGreater(body["data"]["total"], 0)

    def test_search_filter_period(self):
        body = self.client.get("/api/search?q=西风&period=清").get_json()
        self.assertTrue(body["ok"])
        for item in body["data"]["items"]:
            self.assertEqual(item["period"], "清")

    def test_search_falls_back_without_index(self):
        from dataclasses import replace

        settings = replace(load_settings(), index_path=Path("__no_such_index__.sqlite3"))
        client = create_app(settings).test_client()
        body = client.get("/api/search?q=西风").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["mode"], "substring")
        self.assertEqual(body["data"]["index"], "memory")

    def test_tier_label_present(self):
        body = self.client.get("/api/works/58633").get_json()
        self.assertEqual(body["data"]["tier_label"], "官方标注")
        self.assertEqual(body["data"]["source"], "Tier 1")

    def test_chars_have_tone_and_pingze(self):
        body = self.client.get("/api/works/58633/chars").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["source_note"], "逐字四声为本地字典计算，官方仅提供逐句平仄计数")
        first = body["data"]["lines"][0]["chars"][0]
        self.assertIn("tone", first)
        self.assertIn("ping_ze", first)
        self.assertEqual(first["source"], "local_dict")
        self.assertEqual(first["tier_label"], "本地计算")

    def test_unknown_tone_is_explicit(self):
        body = self.client.get("/api/works/58633/chars").get_json()
        unknowns = [
            char
            for line in body["data"]["lines"]
            for char in line["chars"]
            if char["ping_ze"] == "未知"
        ]
        for char in unknowns:
            self.assertEqual(char["tone"], 0)
            self.assertIsNotNone(char["unknown_reason"])

    def test_solve_has_evidence(self):
        row = pick("同调全篇声律比例差")
        if not row:
            self.skipTest("题库未就位")
        response = self.client.post("/api/solve", json={"question": row["问题"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["status"], "ok")
        self.assertTrue(body["data"]["evidence"])
        self.assertTrue(body["data"]["evidence"][0]["line_ids"])
        self.assertTrue(body["data"]["trace"])

    def test_solve_is_deterministic(self):
        row = pick("跨篇后段排序与比例差距")
        if not row:
            self.skipTest("题库未就位")
        first = self.client.post("/api/solve", json={"question": row["问题"]}).get_json()
        second = self.client.post("/api/solve", json={"question": row["问题"]}).get_json()
        self.assertEqual(first["data"]["fields"], second["data"]["fields"])
        self.assertEqual(first["data"]["answer"], second["data"]["answer"])

    def test_all_categories_supported(self):
        categories = (
            "同调全篇声律比例差",
            "跨篇分段变幅比较",
            "跨篇长句仄声密度比较",
            "跨篇后段排序与比例差距",
            "跨篇景情节奏互证",
        )
        for category in categories:
            row = pick(category)
            if not row:
                self.skipTest("题库未就位")
            response = self.client.post("/api/solve", json={"question": row["问题"]})
            self.assertEqual(response.status_code, 200, "%s: %s" % (category, response.get_data(as_text=True)))
            data = response.get_json()["data"]
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["category"], category)
            self.assertTrue(data["trace"])
            self.assertTrue(data["evidence"])

    def test_unknown_work_reports_missing(self):
        question = (
            "甲：清·无此人《不存在的词》（词牌“临江仙”，首句“此句绝不存在于冻结快照之中。”）。 "
            "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。 "
            "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例。"
        )
        response = self.client.post("/api/solve", json={"question": question})
        self.assertEqual(response.status_code, 422)
        error = response.get_json()["error"]
        self.assertIn(error["code"], ("INSUFFICIENT_DATA", "INVALID_INPUT"))
        self.assertTrue(error.get("detail"))

    def test_eval_run_and_items(self):
        if not (QUIZ_DIR / "公开测试集_700题.jsonl").is_file():
            self.skipTest("题库未就位")
        settings = replace(load_settings(), quiz_dir=QUIZ_DIR)
        client = create_app(settings).test_client()
        started = client.post("/api/eval/run", json={"limit": 10})
        self.assertEqual(started.status_code, 202)
        job_id = started.get_json()["data"]["job_id"]

        status = None
        for _ in range(100):
            status = client.get("/api/eval/" + job_id).get_json()["data"]
            if status["state"] in ("done", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(status["state"], "done", status)
        self.assertEqual(status["summary"]["questions"], 10)
        self.assertEqual(status["summary"]["errors"], 0)

        page = client.get("/api/eval/%s/items?page=1&page_size=4" % job_id).get_json()["data"]
        self.assertEqual(len(page["items"]), 4)
        self.assertEqual(page["total"], 10)

    def test_eval_rejects_bad_limit(self):
        response = self.client.post("/api/eval/run", json={"limit": 3})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_INPUT")

    def test_eval_unknown_job(self):
        response = self.client.get("/api/eval/job_not_exist")
        self.assertEqual(response.status_code, 404)

    def test_empty_question_rejected(self):
        response = self.client.post("/api/solve", json={"question": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
