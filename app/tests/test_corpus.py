"""作品库两层语料（Tier 1 官方 / Tier 2a 同源清词）与字段中文化的测试。

为什么要有这组用例：
- 「知识库只有 400 多篇」是评审现场的真实疑问——Tier 2a 的 26,742 首一直在包里，
  但没接到界面上。这组用例锁住「两层都在、且口径不混算」。
- 字段名是机器标识（`甲_ratio`、`diff`），用户看不懂；`field_meta` 的翻译不能回退。
"""

import sys
import unittest
import urllib.parse
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core import corpus, rules, store, tune_norm  # noqa: E402
from core.config import load_settings  # noqa: E402


class CorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.client = create_app(cls.settings).test_client()
        cls.available = corpus.tier2_available(cls.settings)

    def test_tier2_counts_are_real(self):
        counts = corpus.tier2_counts(self.settings)
        if not counts["available"]:
            self.skipTest("Tier 2 索引未就位")
        # 清词 + 宋词合计 43,191 首
        self.assertGreaterEqual(counts["works"], 43000)
        self.assertGreaterEqual(counts["poets"], 2000)
        # 词牌数是**归一后**的真实值：归一前是 5,782（含 2,675 个「词牌+题注」的伪词牌），
        # 归一后落在 2,000 上下。这个断言卡的是「真词牌的数量级」，
        # 太低说明归一把词牌切碎了，太高说明没归一。
        self.assertGreaterEqual(counts["tunes"], 1500)
        self.assertLessEqual(counts["tunes"], 3000)
        layers = {item["tier"]: item["works"] for item in counts["layers"]}
        self.assertGreaterEqual(layers.get("Tier 2a", 0), 26000)
        self.assertGreaterEqual(layers.get("Tier 2b", 0), 16000)

    def test_meta_reports_both_layers(self):
        body = self.client.get("/api/meta").get_json()["data"]
        self.assertIn("tier2", body)
        self.assertIn("tiers", body)
        official = [item for item in body["tiers"] if item["tier"] == "Tier 1"][0]
        self.assertTrue(official["official"])
        self.assertEqual(official["works"], 423)
        if body["tier2"]["available"]:
            self.assertEqual(
                body["corpus_total"], official["works"] + body["tier2"]["works"]
            )
            self.assertGreater(body["corpus_total"], 43000)
            tiers = [item["tier"] for item in body["tiers"]]
            # 官方层在前，其余按 tier 值排；新增朝代只加 Tier 2c，不写死层数
            self.assertEqual(tiers[0], "Tier 1")
            self.assertEqual(tiers, sorted(tiers, key=lambda name: (name != "Tier 1", name)))
            self.assertTrue(official["official"])
            self.assertFalse(any(item.get("official") for item in body["tiers"][1:]))

    def test_works_tier2_lists_and_totals(self):
        if not self.available:
            self.skipTest("Tier 2a 索引未就位")
        body = self.client.get("/api/works?tier=2a&page_size=5").get_json()["data"]
        self.assertEqual(body["tier"], "2a")
        self.assertGreater(body["total"], 26000)
        self.assertEqual(len(body["items"]), 5)
        for item in body["items"]:
            self.assertTrue(str(item["id"]).startswith(("t2a_", "t2b_")))
            self.assertEqual(item["source"], "Tier 2a")
            self.assertIn("非官方", item["tier_label"])

    def test_works_all_layers_total_is_not_truncated(self):
        """`tier=all` 的 total 必须是真实总数，不能被 page_size 截断。

        踩过的坑：同源层只取前几条拼进 items，`total = len(rows)` 跟着被截断——
        page_size=20 时曾报「共 483 篇」，真实是 43,614 篇。
        """
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        big = self.client.get("/api/works?tier=all&page_size=20").get_json()["data"]
        small = self.client.get("/api/works?tier=all&page_size=5").get_json()["data"]
        self.assertEqual(big["total"], small["total"])
        # 不能写死 43,614：语料扩容（新增 Tier 2c 历代词）后总数会变。
        # 用 /api/meta 报的分层规模相加，再与 tier=all 的 total 对账。
        tiers = self.client.get("/api/meta").get_json()["data"]["tiers"]
        expected = sum(item["works"] for item in tiers)
        self.assertEqual(big["total"], expected)
        self.assertEqual(small["total"], expected)

    def test_works_accepts_page_size_120(self):
        """单篇解析页一次取 120 条——上限曾经是 100，直接 400，列表整个是空的。

        前端曾把这个 400 显示成「显示 0 / 0」，静态逐页检查只验「正文有内容」，
        验不出来，所以要单独锁一条。
        """
        body = self.client.get("/api/works?tier=all&page_size=120").get_json()["data"]
        self.assertEqual(len(body["items"]), 120)
        self.assertEqual(body["page_size"], 120)
        # 超过上限要明确拒绝，不能静默截断
        bad = self.client.get("/api/works?tier=all&page_size=500")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.get_json()["error"]["code"], "INVALID_INPUT")

    def test_works_all_layers_pagination_does_not_repeat(self):
        """跨层分页不能重复也不能漏——官方层在前，同源层按全局位置接着排。"""
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        seen = set()
        for page in (1, 21, 22, 23, 24, 25):
            body = self.client.get("/api/works?tier=all&page=%d&page_size=20" % page).get_json()["data"]
            ids = [item["id"] for item in body["items"]]
            self.assertEqual(len(ids), 20, "第 %d 页条数不对" % page)
            for work_id in ids:
                self.assertNotIn(work_id, seen, "第 %d 页出现重复作品 %s" % (page, work_id))
                seen.add(work_id)
        # 官方 423 篇在前面，第 22 页起应进入同源层
        body = self.client.get("/api/works?tier=all&page=22&page_size=20").get_json()["data"]
        self.assertTrue(any(str(item["id"]).startswith(("t2a_", "t2b_")) for item in body["items"]))

    def test_tier2_work_detail_lines_and_chars(self):
        if not self.available:
            self.skipTest("Tier 2a 索引未就位")
        listing = self.client.get("/api/works?tier=2a&page_size=1").get_json()["data"]
        work_id = listing["items"][0]["id"]
        detail = self.client.get("/api/works/%s" % work_id).get_json()["data"]
        self.assertTrue(detail["text"])
        self.assertEqual(detail["source"], "Tier 2a")
        self.assertIn("warning", detail)

        lines = self.client.get("/api/works/%s/lines" % work_id).get_json()["data"]
        self.assertTrue(lines["lines"])
        for line in lines["lines"]:
            self.assertEqual(line["line_no"], lines["lines"].index(line) + 1)
            self.assertGreaterEqual(line["ping_count"] + line["ze_count"], 0)
            self.assertLessEqual(line["ping_count"] + line["ze_count"], line["character_count"])

        chars = self.client.get("/api/works/%s/chars" % work_id).get_json()["data"]
        self.assertTrue(chars["lines"])
        for item in chars["lines"]:
            for entry in item["chars"]:
                self.assertEqual(entry["source"], "tier2_pinyin")
                self.assertIn("非官方", entry["tier_label"])

    def test_tier2_search_finds_hits(self):
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        body = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=2a&limit=5").get_json()["data"]
        self.assertEqual(body["tier"], "2a")
        self.assertGreater(body["total"], 100)
        self.assertTrue(body["items"])
        for item in body["items"]:
            self.assertTrue(item["matched"])
            self.assertEqual(item["source"], "Tier 2a")

    def test_tier2b_song_layer(self):
        """宋词层：能列、能搜、能看逐句，且标着「非官方口径」。"""
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        listing = self.client.get("/api/works?tier=2b&page_size=3").get_json()["data"]
        self.assertEqual(listing["tier"], "2b")
        self.assertGreater(listing["total"], 16000)
        work_id = listing["items"][0]["id"]
        self.assertTrue(work_id.startswith("t2b_"))

        detail = self.client.get("/api/works/%s" % work_id).get_json()["data"]
        self.assertEqual(detail["source"], "Tier 2b")
        self.assertIn("非官方", detail["tier_label"])

        lines = self.client.get("/api/works/%s/lines" % work_id).get_json()["data"]
        self.assertTrue(lines["lines"])

        hits = self.client.get("/api/search?q=%E6%9D%A8%E6%9F%B3&tier=2b&limit=5").get_json()["data"]
        self.assertEqual(hits["tier"], "2b")
        self.assertGreater(hits["total"], 0)

    def test_search_all_layers_merges(self):
        """tier=all：官方 + 同源合并，命中数相加。"""
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        official = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=1&limit=5").get_json()["data"]
        merged = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=all&limit=5").get_json()["data"]
        self.assertEqual(merged["tier"], "all")
        self.assertGreater(merged["total"], official["total"])
        self.assertIn("tier2_total", merged)

    def test_authors_endpoint(self):
        """作者候选：姓名 + 官方/同源作品数，供检索页按作者筛选用。"""
        data = self.client.get("/api/authors?q=%E6%9F%B3&limit=10").get_json()["data"]
        self.assertTrue(data["items"])
        first = data["items"][0]
        self.assertEqual(first["name"], "柳永")
        self.assertGreater(first["corpus_works"], 0)

    def test_search_by_author_without_keyword(self):
        """只给作者、不给关键词 → 按条件浏览，返回该作者的全部作品。"""
        data = self.client.get("/api/search?poet=%E6%9F%B3%E6%B0%B8&limit=10").get_json()["data"]
        self.assertEqual(data["mode"], "browse")
        self.assertGreater(data["total"], 0)
        self.assertTrue(data["items"])
        for item in data["items"]:
            self.assertIn("柳永", item["poet"])

    def test_search_defaults_to_all_layers(self):
        """默认 tier=all：只搜官方 423 篇会让「检索不到」被误解成「不存在」。"""
        data = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E&limit=5").get_json()["data"]
        self.assertGreater(data["total"], 100)

    def test_search_items_carry_tier_label(self):
        """每条结果都要标层级，官方层与同源层要分得清。"""
        data = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=all&limit=5").get_json()["data"]
        labels = {item.get("tier_label") for item in data["items"]}
        self.assertTrue(all(labels), "有结果没有层级标签：%s" % labels)
        self.assertTrue(any("官方" in (label or "") for label in labels))

    def test_works_rejects_bad_tier(self):
        response = self.client.get("/api/works?tier=nope")
        self.assertEqual(response.status_code, 400)

    def test_tier_all_merges_official_and_corpus(self):
        """tier=all：官方标注与同源层**都要出现在 items 里**。

        之前只把官方层的命中放进 items、同源层只算总数，
        于是「官方层没有这个词」会被误解成「这个作品不存在」。
        """
        keyword = urllib.parse.quote("忆江南")
        data = self.client.get(
            "/api/works?q=%s&tier=all&page_size=20" % keyword
        ).get_json()["data"]
        self.assertGreater(data["total"], 0)
        self.assertTrue(data["items"])
        sources = {item["source"] for item in data["items"]}
        self.assertTrue(
            any(str(item["id"]).startswith("t2") for item in data["items"]),
            "同源层的命中没有出现在 items 里：%s" % sources,
        )

    def test_corpus_work_fact_card(self):
        """同源层作品也要能建事实卡（逐句平仄按带调拼音统计，标非官方口径）。"""
        from core import sidecar

        settings = load_settings()
        local = store.Store(settings.snapshot_path, settings.index_path)
        block = corpus.list_tier2(settings, tier="2a", page=1, page_size=1)
        if not block["items"]:
            self.skipTest("同源层索引未就位")
        work_id = block["items"][0]["id"]
        card = sidecar.build_fact_card(local, work_id, settings)
        self.assertTrue(card["facts"]["lines"])
        self.assertIn("非官方", card["tier_label"])
        # 声律不变量：ping + ze == character_count（与官方口径一致）
        self.assertEqual(
            card["facts"]["ping"] + card["facts"]["ze"],
            card["facts"]["characters"],
        )

    def test_qa_accepts_corpus_work_id(self):
        """问词接口要能接受同源层的字符串 id。"""
        settings = load_settings()
        block = corpus.list_tier2(settings, tier="2b", page=1, page_size=1)
        if not block["items"]:
            self.skipTest("同源层索引未就位")
        work_id = block["items"][0]["id"]
        response = self.client.post(
            "/api/qa", json={"work_id": work_id, "question": "这首词有什么特点？"}
        )
        self.assertEqual(response.status_code, 202)
        card = response.get_json()["data"]["fact_card"]
        self.assertIn("非官方", card["tier_label"])

    def test_tune_stats_per_layer(self):
        """同调统计分层：官方层与同源层分开，数字对得上各自的语料。"""
        official = self.client.get("/api/search/stats?tune=%E4%B8%B4%E6%B1%9F%E4%BB%99&tier=1").get_json()["data"]
        self.assertIn("官方标注", official["tier_label"])
        self.assertGreater(official["works"], 0)
        self.assertTrue(official["sentence_counts"])

        corpus = self.client.get("/api/search/stats?tune=%E4%B8%B4%E6%B1%9F%E4%BB%99&tier=2a").get_json()["data"]
        self.assertIn("非官方", corpus["tier_label"])
        # 同源层的同调作品数应当远多于官方层
        self.assertGreater(corpus["works"], official["works"])
        self.assertGreater(corpus["poets"], 0)

    def test_tune_stats_unknown_tune(self):
        response = self.client.get("/api/search/stats?tune=%E4%B8%8D%E5%AD%98%E5%9C%A8%E7%9A%84%E8%B0%83")
        self.assertEqual(response.status_code, 404)

    def test_tunes_overview_includes_corpus_layer(self):
        """词调身份层要能看到同源层的规模（近两千个词牌）。"""
        body = self.client.get("/api/tunes").get_json()["data"]
        self.assertIn("corpus", body)
        corpus = body["corpus"]
        if not corpus["available"]:
            self.skipTest("Tier 2 索引未就位")
        self.assertGreater(corpus["tunes"], 1000)
        self.assertGreater(corpus["works"], 40000)
        self.assertTrue(corpus["layers"])
        # 概览只给热门榜，不给全量体式详情（近 2,000 个词调全量返回是几兆 JSON）
        self.assertLessEqual(len(corpus["top"]), 100)
        # 但词牌名清单要全给——检索页的「按词牌筛选」下拉得能选到同源层的调
        self.assertGreaterEqual(len(corpus.get("names") or []), corpus["tunes"])
        self.assertGreaterEqual(len(corpus.get("all") or []), corpus["tunes"])

    def test_browse_and_search_items_share_the_same_shape(self):
        """浏览模式（无关键词）与检索模式的条目字段必须一致。

        踩过的坑：browse 模式没返回 `matched` / `hits`，
        前端按 `item.matched.map(...)` 渲染时整页崩掉（错误边界兜住了，但页面废了）。
        """
        browse = self.client.get("/api/search?poet=%E6%9F%B3%E6%B0%B8").get_json()["data"]
        self.assertEqual(browse.get("mode"), "browse")
        self.assertTrue(browse["items"])
        for item in browse["items"]:
            self.assertIn("matched", item)
            self.assertIn("hits", item)
            self.assertIsInstance(item["matched"], list)
            self.assertIsInstance(item["hits"], int)

        keyword = self.client.get("/api/search?q=%E8%A5%BF%E9%A3%8E").get_json()["data"]
        self.assertTrue(keyword["items"])
        for item in keyword["items"]:
            self.assertIn("matched", item)
            self.assertIn("hits", item)
        # 两种模式的字段集合必须一样，否则前端要写两套渲染
        self.assertEqual(
            set(keyword["items"][0].keys()) & {"matched", "hits", "tier_label", "title", "poet"},
            set(browse["items"][0].keys()) & {"matched", "hits", "tier_label", "title", "poet"},
        )

    def test_tune_detail_corpus_layer(self):
        """`?tier=corpus` 能取到同源层的词调详情。"""
        body = self.client.get("/api/tunes").get_json()["data"]
        corpus = body.get("corpus") or {}
        if not corpus.get("available") or not corpus.get("top"):
            self.skipTest("Tier 2 索引未就位")
        name = corpus["top"][0]["name"]
        detail = self.client.get("/api/tunes/tune_%s?tier=corpus" % name).get_json()
        self.assertTrue(detail["ok"], detail.get("error"))
        self.assertEqual(detail["data"]["name"], name)
        self.assertTrue(detail["data"]["forms"])

class FieldLabelTest(unittest.TestCase):
    def test_every_solved_field_has_a_chinese_name(self):
        """求解结果的每个字段都要有中文名；未登记的也要显式标注，不能静默。"""
        meta = rules.field_meta(
            {
                "甲_n": 5,
                "甲_ratio": 43.3,
                "乙_max_lines": "1、4",
                "diff": 8.3,
                "higher": "乙",
                "order": "甲",
                "boundary_present": True,
                "status_present": True,
                "没登记的字段": 1,
            }
        )
        self.assertEqual(meta["甲_n"]["label"], "甲·句数")
        self.assertEqual(meta["甲_ratio"]["label"], "甲·仄声比例")
        self.assertEqual(meta["乙_max_lines"]["label"], "乙·最长句句序")
        self.assertEqual(meta["diff"]["label"], "差值")
        self.assertEqual(meta["higher"]["label"], "较高者")
        self.assertEqual(meta["order"]["label"], "后段排序")
        self.assertTrue(meta["甲_n"]["desc"])
        self.assertTrue(meta["diff"]["desc"])
        # 未登记字段：保留原名并说明，不隐藏
        self.assertEqual(meta["没登记的字段"]["label"], "没登记的字段")
        self.assertIn("未登记", meta["没登记的字段"]["desc"])

    def test_solve_returns_field_meta(self):
        client = create_app(load_settings()).test_client()
        response = client.post(
            "/api/solve",
            json={"question": "请比较最长句的句序"},
        )
        # 题面不完整时应该报错而不是半成品
        if response.status_code != 200:
            self.assertEqual(response.status_code, 400)
            return
        data = response.get_json()["data"]
        self.assertIn("field_meta", data)
        self.assertEqual(set(data["field_meta"]), set(data["fields"]))


class TuneNormTest(unittest.TestCase):
    """词牌归一：把「词牌 + 题注」切回规范词牌名。

    为什么要有这组用例：
    - 同源语料标题多为「壶中天题公曼仇史，即以为赠」这类形态，早期构建只按「·」切一刀，
      于是 5,771 个「词牌」里有 2,675 个是题注串——按词牌筛选时这些作品永远筛不出来。
    - 归一必须**幂等**（跑第二遍结果不变），也必须**不丢信息**（题注仍留在 title 里）。
    """

    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.available = corpus.tier2_available(cls.settings)

    def test_cut_tune_from_title(self):
        vocab = {"壶中天", "百字令", "望江南", "忆江南"}
        self.assertEqual(tune_norm.canonicalize("壶中天题公曼仇史，即以为赠", vocab)[0], "壶中天")
        self.assertEqual(tune_norm.canonicalize("百字令题陈慎庵宣南话旧图", vocab)[0], "百字令")
        self.assertEqual(tune_norm.canonicalize("望江南/忆江南", vocab)[0], "望江南")

    def test_alias_only_counts_real_tune_names(self):
        vocab = {"望江南", "忆江南", "木兰花", "玉楼春"}
        canonical, aliases = tune_norm.canonicalize("望江南/忆江南", vocab)
        self.assertEqual(canonical, "望江南")
        self.assertEqual(aliases, ["忆江南"])
        # 题注串不是别名
        _canonical, aliases = tune_norm.canonicalize("南楼令临春阁", vocab)
        self.assertEqual(aliases, [])

    def test_unmatched_tune_is_kept_verbatim(self):
        canonical, aliases = tune_norm.canonicalize("雪月交光山矾，俗名七里香", set())
        self.assertEqual(canonical, "雪月交光山矾，俗名七里香")
        self.assertEqual(aliases, [])

    def test_normalize_is_idempotent(self):
        pairs = [
            (1, "壶中天题公曼仇史，即以为赠", "壶中天题公曼仇史，即以为赠"),
            (2, "望江南/忆江南", "望江南/忆江南"),
            (3, "浣溪沙·其一", "浣溪沙"),
        ]
        once = tune_norm.normalize(pairs)
        twice = tune_norm.normalize(
            [(wid, title, once["canonical"][wid]) for wid, title, _tune in pairs]
        )
        self.assertEqual(once["canonical"], twice["canonical"])

    def test_tier2_tune_filter_hits_normalised_tunes(self):
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        # 「壶中天」这批作品的标题带题注，归一前按 tune 精确筛是 0 篇
        result = corpus.list_tier2(self.settings, tune="壶中天", page_size=5)
        self.assertGreater(result["total"], 0)
        for item in result["items"]:
            self.assertEqual(item["tune"], "壶中天")

    def test_tier2_alias_filter_finds_parallel_tunes(self):
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        by_alias = corpus.list_tier2(self.settings, tune="忆江南", page_size=5)
        if by_alias["total"] == 0:
            self.skipTest("本机语料里没有斜杠并列的调")
        for item in by_alias["items"]:
            self.assertIn(item["tune"], ("望江南", "忆江南"))

    def test_tune_field_caption_residue_is_bounded(self):
        """归一后 tune 字段里「词牌+题注」的残留必须很小。

        同源语料里确有**题记体**作品（作者只留题记、没给词牌），
        这类保留原值是诚实的——不知道就说不知道。断言卡的是**占比不许扩散**：
        归一前这类占 4.8%（2,075/43,191），归一后应降到 0.5% 以下。
        """
        if not self.available:
            self.skipTest("Tier 2 索引未就位")
        connection = corpus._connect(self.settings)
        try:
            total = connection.execute("SELECT count(*) FROM works").fetchone()[0]
            dirty = connection.execute(
                "SELECT count(*) FROM works WHERE tune LIKE '%，%' OR tune LIKE '%、%' "
                "OR tune LIKE '%。%' OR tune LIKE '%：%'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertLess(dirty / float(total), 0.005)


if __name__ == "__main__":
    unittest.main(verbosity=2)
