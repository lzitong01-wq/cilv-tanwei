"""验证集 Excel 上传回归：真实文件要能传，不能传要说清原因。

线上实测缺陷：用户上传 `200条测试集.xlsx` 只看到「Excel 验证集读取失败」，
后端真实原因是 `BadZipFile('File is not a zip file')`——文件不是 OOXML 工作簿
（老 .xls 改后缀 / CSV 另存成 .xlsx / 下载损坏），且前端把 detail 吞了。
本组用例锁住：标准 xlsx 可用、常见变体可用、非工作簿给可操作提示、
上传 → 作答 → 导出 Excel 全链路可用。
"""

import csv
import io
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
from openpyxl import Workbook, load_workbook  # noqa: E402

QUESTION = "甲：清·纳兰性德《长相思》。乙：宋·柳永《雨霖铃》。两篇的上下阕仄声比例有什么变化？"
GOLD = "参考答案"


def make_xlsx(rows: list[list], sheet_names=None) -> bytes:
    book = Workbook()
    if sheet_names:
        book.active.title = sheet_names[0]
        for name in sheet_names[1:]:
            book.create_sheet(name)
    target = book.active
    for row in rows:
        target.append(row)
    for name in (sheet_names or [])[1:]:
        book[name].append(["说明页", "不是题目"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def make_question_rows(count: int) -> list[list]:
    return [["问题", "预期答案"]] + [["%s（第 %d 题）" % (QUESTION, i + 1), GOLD] for i in range(count)]


def table_text(delimiter: str, count: int, encoding: str = "utf-8-sig") -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(["问题", "预期答案"])
    for i in range(count):
        writer.writerow(["%s（第 %d 题）" % (QUESTION, i + 1), GOLD])
    return buffer.getvalue().encode(encoding)


class XlsxUploadTest(unittest.TestCase):
    def _parse(self, data: bytes):
        return evaluator.parse_dataset(evaluator.xlsx_to_csv(data), "验证集.csv")

    def test_standard_200_questions(self):
        rows, report = self._parse(make_xlsx(make_question_rows(200)))
        self.assertEqual(report["total"], 200)
        self.assertEqual(report["with_gold"], 200)
        self.assertEqual(rows[0]["question"].startswith("甲："), True)

    def test_four_column_layout(self):
        rows = [["题号", "类别", "问题", "标准答案"]]
        rows += [[i, "C1", "%s（第 %d 题）" % (QUESTION, i), GOLD] for i in range(1, 21)]
        _rows, report = self._parse(make_xlsx(rows))
        self.assertEqual(report["total"], 20)
        self.assertEqual(report["with_gold"], 20)

    def test_header_not_on_first_row(self):
        rows = [["验证集说明"], ["问题", "预期答案"]]
        rows += [["%s（第 %d 题）" % (QUESTION, i), GOLD] for i in range(1, 11)]
        _rows, report = self._parse(make_xlsx(rows))
        self.assertEqual(report["total"], 10)

    def test_picks_sheet_with_question_header(self):
        book = Workbook()
        book.active.title = "说明"
        book.active.append(["这是说明页"])
        sheet = book.create_sheet("题目", 0)
        for row in make_question_rows(5):
            sheet.append(row)
        buffer = io.BytesIO()
        book.save(buffer)
        _rows, report = self._parse(buffer.getvalue())
        self.assertEqual(report["total"], 5)

    def test_csv_text_saved_as_xlsx(self):
        _rows, report = self._parse(table_text(",", 5))
        self.assertEqual(report["total"], 5)

    def test_tsv_text_saved_as_xlsx(self):
        _rows, report = self._parse(table_text("\t", 5))
        self.assertEqual(report["total"], 5)

    def test_gbk_csv_text_saved_as_xlsx(self):
        _rows, report = self._parse(table_text(",", 5, encoding="gb18030"))
        self.assertEqual(report["total"], 5)

    def test_legacy_xls_gives_actionable_error(self):
        data = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 512
        with self.assertRaises(ApiError) as ctx:
            evaluator.xlsx_to_csv(data)
        self.assertIn(".xls", ctx.exception.message)
        self.assertIn("另存为", ctx.exception.message)

    def test_corrupt_bytes_give_actionable_error(self):
        with self.assertRaises(ApiError) as ctx:
            evaluator.xlsx_to_csv(b"\x00\x01\x02not-a-workbook\xff")
        self.assertIn("不是 Excel 工作簿", ctx.exception.message)
        self.assertIn("first_bytes", str(ctx.exception.detail))

    def test_html_error_page_is_not_mistaken_for_csv(self):
        html = b"<!DOCTYPE html><html><body>404, error, not found</body></html>"
        with self.assertRaises(ApiError) as ctx:
            evaluator.xlsx_to_csv(html)
        self.assertIn("不是 Excel 工作簿", ctx.exception.message)

    def test_empty_file(self):
        with self.assertRaises(ApiError) as ctx:
            evaluator.xlsx_to_csv(b"")
        self.assertIn("空的", ctx.exception.message)


class UploadEndToEndTest(unittest.TestCase):
    """评分要点：上传 → 逐题作答 → 导出模板5 Excel。"""

    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.client = create_app(cls.settings).test_client()

    def test_upload_run_export_xlsx(self):
        payload = make_xlsx(make_question_rows(20))
        response = self.client.post(
            "/api/eval/upload",
            data={"file": (io.BytesIO(payload), "200条测试集.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 202, response.get_json())
        body = response.get_json()["data"]
        self.assertEqual(body["parsed"]["total"], 20)
        self.assertEqual(body["parsed"]["with_gold"], 20)
        job_id = body["job_id"]

        status = None
        for _ in range(100):
            status = self.client.get("/api/eval/%s" % job_id).get_json()["data"]
            if status["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(status["state"], "done", status.get("error"))
        self.assertEqual(status["summary"]["questions"], 20)

        exported = self.client.get("/api/eval/%s/export?format=xlsx" % job_id)
        self.assertEqual(exported.status_code, 200)
        book = load_workbook(io.BytesIO(exported.data), data_only=True)
        rows = list(book.active.iter_rows(values_only=True))
        self.assertEqual(
            list(rows[0]),
            ["问题", "预期答案", "智能体实际回复", "耗时(秒)", "状态"],
        )
        self.assertEqual(len(rows), 21)
        self.assertIsInstance(rows[1][3], (int, float))
        book.close()


if __name__ == "__main__":
    unittest.main()
