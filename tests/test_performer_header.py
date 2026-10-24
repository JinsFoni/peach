"""女优页头：五项资料与按名义分组的别名（ADR-0069），读的是 `performer_profile`（ADR-0067）。

全程临时账本，不联网。资料行由补女优资料后继的写入函数写下，页面是 minnano-av 504478
（篠田ゆう）与 494354（釈アリス）的真实页面裁剪而成，只留解析会读到的 canonical、`<h1>`
与资料表。
"""
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from peach import minnano_av, performer_header
from peach import web_contract as rm_web
from peach.entities import normalize_entity_name
from peach.genre_decisions import record_genre_decision
from peach.performer_profiles import write_profile
from peach.web_entity import q_entity_shapes
from support.ledger import fresh_ledger

STAMP = "2026-09-24T00:00:00.000Z"
TODAY = date(2026, 9, 25)

SHINODA = """<!doctype html><html lang="ja"><head>
<link rel="canonical" href="https://www.minnano-av.com/actress504478.html">
</head><body>
<h1>篠田ゆう<span>しのだゆう / Shinoda Yu</span></h1>
<div class="act-profile">
<table width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td><h2>篠田ゆう （しのだゆう / Shinoda Yu）</h2></td></tr>
<tr><td><span>別名</span><p>篠崎ゆう子 （しのざきゆうこ / Shinozaki Yuuko）</p></td></tr>
<tr><td><span>別名</span><p>高木早希（ラグジュTV) （たかぎさき / Takagi Saki）</p></td></tr>
<tr><td><span>別名</span><p>桧山彩音（舞ワイフ） （ひやまあやね / Hiyama Ayane）</p></td></tr>
<tr><td><span>別名</span><p>橋本真紀（舞ワイフ） （はしもとまき / Hashimoto Maki）</p></td></tr>
<tr><td><span>別名</span><p>篠田杏奈（舞ワイフ） （しのだあんな / Shinoda Anna）</p></td></tr>
<tr><td><span>生年月日</span><p>1991年07月21日
  （現在 <a href="actress_list.php?birthday=1991-07-21">35歳</a>）かに座</td>		</p></td></tr>
<tr><td><span>サイズ</span><p>T155 / B86(<a href="actress_list.php?cup=F">Fカップ</a>) / W60 / H87 / S22</p></td></tr>
<tr><td><span>血液型</span><p><a href="actress_list.php?blood_type=O">O型</a></p></td></tr>
<tr><td><span>AV出演期間</span><p>2010年～2023年</p></td></tr>
<tr><td><span>デビュー作品</span><p>セキララ 〜今どき世代のゆるい性事情〜 03（2010年12月 02日）</p></td></tr>
<tr valign="top"><td><span>タグ</span><div class="tagarea">
<a href="actress_list.php?tag_a_id=28">美乳</a>
<a href="actress_list.php?tag_a_id=43">美尻</a>
<a href="actress_list.php?tag_a_id=238">レズ</a>
</td></tr>
</table></div></body></html>"""

ALICE = """<!doctype html><html lang="ja"><head>
<link rel="canonical" href="https://www.minnano-av.com/actress494354.html">
</head><body>
<h1>釈アリス<span>しゃくありす / Shaku Alice</span></h1>
<div class="act-profile">
<table width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td><span>サイズ</span><p>T169 / B88(<a href="actress_list.php?cup=E">Eカップ</a>) / W57 / H95 / S</p></td></tr>
<tr><td><span>AV出演期間</span><p>2024年-</p></td></tr>
</table></div></body></html>"""


class NameEntryTests(unittest.TestCase):
    def test_alias_cells_split_names_and_carry_their_channel_note(self):
        self.assertEqual(
            [(entry["name"], entry["note"], entry["reading"]) for entry in
             minnano_av.name_entries("<p>橋本真紀&amp;桧山彩音（舞ワイフ名義）</p>")],
            [("橋本真紀", "舞ワイフ", ""), ("桧山彩音", "舞ワイフ", "")])
        # 读音栏里填的是店名：按注记收，不当读音。
        self.assertEqual(minnano_av.name_entries("<p>あいな （吉原ソープ 薔薇の園 / Aina）</p>"),
                         [{"name": "あいな", "reading": "", "romaji": "Aina",
                           "note": "吉原ソープ 薔薇の園"}])
        self.assertEqual(minnano_av.name_entries("美雲そら【旧名】 （みくもそら / Mikumo Sora）")[0]
                         ["note"], "")

    def test_the_raw_cells_keep_the_main_name_of_the_page(self):
        """现名那一组要页面主名；它不在资料表里，另记在原文的「名前」下。"""
        self.assertEqual(minnano_av.profile(SHINODA)["raw"]["名前"], "篠田ゆう")


class Case(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)

    def entity(self, name: str, *aliases, kind: str = "performer") -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,?,?,?,?)", (kind, name, normalize_entity_name(name), STAMP, STAMP))
            entity_id = int(cursor.lastrowid)
            for written, source in aliases:
                self.connection.execute(
                    "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                    " VALUES(?,?,?,?)", (entity_id, written, normalize_entity_name(written), source))
        return entity_id

    def land(self, entity_id: int, page: str) -> None:
        with self.connection:
            write_profile(self.connection, entity_id, minnano_av.profile(page),
                          source="auto:performer-profile@1", source_url="https://example.invalid/",
                          fetched_at=STAMP)

    def header(self, entity_id: int, canonical: str) -> dict:
        return performer_header.header(self.connection, entity_id, canonical, today=TODAY)


class HeaderTests(Case):
    def test_five_facts_and_the_name_groups_of_the_header(self):
        shinoda = self.entity(
            "篠田优", ("篠田ゆう", "avdb"), ("Yu Shinoda", "r18:performer"),
            ("しのだゆう", "r18:performer"), ("篠田杏奈", "user:alias"), ("城田優子", "auto:x@1"),
            ("ゆうちゃん企画", "javinizer:planning-alias"))
        self.land(shinoda, SHINODA)
        head = self.header(shinoda, "篠田优")
        self.assertEqual(head["profile"], {
            "birth_date": "1991-07-21", "age": 35, "height": 155, "bust": 86, "waist": 60,
            "hip": 87, "cup": "F", "debut_date": "2010-12-02",
            "debut_title": "セキララ 〜今どき世代のゆるい性事情〜 03",
            "active": {"from": 2010, "to": 2023}, "tags": ["美乳", "美臀", "百合"]})
        groups = head["name_groups"]
        self.assertEqual(groups["reading"], "しのだゆう")
        self.assertEqual(groups["shown"], ["篠田ゆう", "篠崎ゆう子", "高木早希"])
        self.assertEqual(groups["total"], 7)
        self.assertEqual([(group["label"], [entry["name"] for entry in group["names"]])
                          for group in groups["groups"]],
                         [("现名", ["篠田ゆう"]), ("旧名义", ["篠崎ゆう子"]),
                          ("舞ワイフ", ["桧山彩音", "橋本真紀", "篠田杏奈"]),
                          ("ラグジュTV", ["高木早希"]), ("其它", ["城田優子"])])
        self.assertEqual(groups["groups"][0]["names"][0]["reading"], "しのだゆう")

    def test_an_open_ended_period_is_still_active(self):
        alice = self.entity("释爱丽丝")
        self.land(alice, ALICE)
        profile = self.header(alice, "释爱丽丝")["profile"]
        self.assertEqual(profile["active"], {"from": 2024, "ongoing": True})
        self.assertEqual((profile["height"], profile["cup"]), (169, "E"))
        self.assertNotIn("birth_date", profile)

    def test_without_a_profile_the_ledger_aliases_form_one_untitled_group(self):
        alice = self.entity("释爱丽丝", ("Alice Shaku", "avdb"), ("しゃくありす", "avdb"),
                            ("釈アリス", "avdb"))
        head = self.header(alice, "释爱丽丝")
        self.assertEqual(head["profile"], {})
        self.assertEqual(head["name_groups"], {
            "reading": "しゃくありす", "shown": ["釈アリス"], "total": 1,
            "groups": [{"label": "", "names": [{"name": "釈アリス"}]}]})

    def test_site_tags_speak_peach_chinese_and_follow_the_user_decisions(self):
        raw = ["美人", "美少女", "清楚", "巨尻", "美尻", "ロリ→ギャル", "美乳美尻",
               "カリビアン", "まだ知らない分類", "  "]
        self.assertEqual(performer_header.site_tags(raw),
                         ["高颜值", "清纯", "巨臀", "美臀", "萝莉→辣妹", "美乳", "まだ知らない分類"])
        with self.connection:
            record_genre_decision(self.connection, "剛毛", "浓密阴毛", STAMP)
            record_genre_decision(self.connection, "美尻", None, STAMP)
        shinoda = self.entity("篠田优")
        self.land(shinoda, SHINODA.replace(">レズ<", ">剛毛<"))
        self.assertEqual(self.header(shinoda, "篠田优")["profile"]["tags"], ["美乳", "浓密阴毛"])

    def test_age_turns_on_the_birthday(self):
        self.assertEqual(performer_header.age_on("1991-07-21", date(2026, 7, 20)), 34)
        self.assertEqual(performer_header.age_on("1991-07-21", date(2026, 7, 21)), 35)
        self.assertIsNone(performer_header.age_on("1991-07", date(2026, 7, 21)))

    def test_a_ledger_before_migration_0036_still_serves_the_header(self):
        shinoda = self.entity("篠田优", ("篠田ゆう", "avdb"))
        with self.connection:
            self.connection.execute("DROP TABLE performer_profile")
        head = self.header(shinoda, "篠田优")
        self.assertEqual(head["profile"], {})
        self.assertEqual(head["name_groups"]["shown"], ["篠田ゆう"])
        self.assertEqual(performer_header.profiled(self.connection), set())


class AccountProfileTests(Case):
    """发布账号在站上那一页（ADR-0115）：资料卡仍旧只有五项，简介与资料表另起一栏。

    资料行由 `pornhub_profiles.record` 规整，这里直接写它交回的那一份。
    """

    def land_account(self, entity_id: int, about, bio: str = "", name: str = "Mini Diva",
                     extra: dict | None = None) -> None:
        with self.connection:
            write_profile(self.connection, entity_id, {
                "tags": [], "height_cm": 167, "bust_cm": 89, "waist_cm": 64, "hip_cm": 91,
                "birthplace": "Czech Republic", "hobbies": "Boring AF.",
                "raw": {"名前": name, "简介": bio, "资料": list(about), **(extra or {})}},
                source="auto:creator-profile@3", source_url="https://example.invalid/about",
                fetched_at=STAMP)

    def test_the_site_profile_sits_beside_the_five_rows(self):
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [
            {"label": "感情", "value": "单身", "glyph": "heart"},
            {"label": "出生地", "value": "Czech Republic", "glyph": "globe"}],
            bio="Love being here from time to time.")
        profile = self.header(mini, "mini diva")["profile"]
        self.assertEqual(profile["about"], [
            {"label": "感情", "value": "单身", "glyph": "heart"},
            {"label": "出生地", "value": "Czech Republic", "glyph": "globe"}])
        self.assertEqual(profile["bio"], "Love being here from time to time.")
        # 卡里那五项还是五项：身高与三围在「身材」那一格里，资料表不重复列。
        self.assertEqual((profile["height"], profile["bust"], profile["waist"], profile["hip"]),
                         (167, 89, 64, 91))

    def test_a_row_without_a_label_or_a_value_is_not_drawn(self):
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [
            {"label": "", "value": "单身"}, {"label": "兴趣", "value": "  "},
            {"label": "取向", "value": "男女通吃"}, {"value": "没有标签"}])
        profile = self.header(mini, "mini diva")["profile"]
        self.assertEqual(profile["about"], [
            {"label": "取向", "value": "男女通吃", "glyph": "info"}])

    def test_a_long_bio_is_cut_where_the_card_stops_reading(self):
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [], bio="好" * (performer_header.BIO_LIMIT + 40))
        bio = self.header(mini, "mini diva")["profile"]["bio"]
        self.assertEqual(len(bio), performer_header.BIO_LIMIT + 1)
        self.assertTrue(bio.endswith("…"))

    def test_the_rank_and_her_accounts_ride_in_the_same_profile(self):
        """站上那颗模特排名与她登记的账号进卡里最右那一栏；播放量那种运营数字不进卡。"""
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [], extra={
            "读数": {"模特排名": "266", "视频播放量": "561,849,851"},
            "社交": [{"label": "官方网址", "url": "https://onlyfans.com/minidivaonly",
                      "glyph": "globe"},
                     {"label": "Twitter", "url": "https://twitter.com/minidivaonly",
                      "glyph": "brand-x"}]})
        profile = self.header(mini, "mini diva")["profile"]
        self.assertEqual(profile["rank"], {"label": "模特排名", "value": "266", "glyph": "crown"})
        self.assertEqual(profile["links"], [
            {"label": "官方网址", "url": "https://onlyfans.com/minidivaonly", "glyph": "globe"},
            {"label": "Twitter", "url": "https://twitter.com/minidivaonly", "glyph": "brand-x"}])

    def test_a_link_that_is_not_https_is_not_drawn(self):
        """点不开的地址不成一栏：只有 http 的、不是网址的、同一地址重复的，那一格都不画。"""
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [], extra={"社交": [
            {"label": "旧地址", "url": "http://example.invalid/mini"},
            {"label": "不是网址", "url": "minidiva"},
            {"label": "Instagram", "url": "https://instagram.com/minidiva", "glyph": "brand-instagram"},
            {"label": "Instagram", "url": "https://instagram.com/minidiva"}]})
        links = self.header(mini, "mini diva")["profile"]["links"]
        self.assertEqual(links, [{"label": "Instagram", "url": "https://instagram.com/minidiva",
                                  "glyph": "brand-instagram"}])

    def test_an_account_without_a_site_profile_keeps_the_plain_header(self):
        mini = self.entity("mini diva", kind="creator")
        self.land_account(mini, [], bio="")
        profile = self.header(mini, "mini diva")["profile"]
        self.assertNotIn("about", profile)
        self.assertNotIn("bio", profile)
        self.assertNotIn("rank", profile)
        self.assertNotIn("links", profile)


class ApiTests(Case):
    def test_the_entity_api_and_the_skeleton_shapes_carry_the_profile(self):
        shinoda = self.entity("篠田优", ("篠田ゆう", "avdb"))
        self.land(shinoda, SHINODA)
        (self.root / "logos").mkdir()
        (self.root / "avatars").mkdir()
        contract = rm_web.WebContract(self.db, avatar_root=self.root / "avatars",
                                      logo_root=self.root / "logos")
        page = rm_web.q_entity(contract, {"kind": "performer", "name": "篠田优"})
        self.assertEqual(page["profile"]["cup"], "F")
        self.assertEqual(page["name_groups"]["shown"][0], "篠田ゆう")
        shapes = q_entity_shapes(contract, {})
        parts = {item["id"]: item["parts"] for item in shapes["entities"]}
        self.assertIn("facts", parts[shinoda])


if __name__ == "__main__":
    unittest.main()
