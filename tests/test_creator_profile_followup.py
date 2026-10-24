"""补发布账号资料后继：谁被派、跑一趟写下什么、取不到时多久再试。

全程临时账本与临时目录；取页与取图都换成按地址回字节的替身，不联网。
"""
import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from peach import avatar_picker, creator_profile_followup as followup, pornhub_profiles
from peach.entities import normalize_entity_name
from peach.followups import Attempts, attempts_root
from peach.http import HttpResponse
from peach.performer_profiles import read_profile
from peach.repository import LedgerDatabase
from support.ledger import fresh_ledger

STAMP = "2026-10-24T00:00:00.000Z"
SOURCE_URL = "https://cn.pornhub.com/model/mini-diva/videos"
PAGE_URL = "https://cn.pornhub.com/model/mini-diva/videos"
AVATAR = ("https://ei.phncdn.com/pics/users/587/676/961/avatar1598785651/"
          "(m=eidYGCjadOf)(mh=W2VLPIc2a4WcnOlI)200x200.jpg")
PAGE = f"""<html><head><meta charset="utf-8"></head><body>
<div class="previewAvatarPicture"><img id="getAvatar" src="{AVATAR}" alt="Mini Diva"></div>
<div class="name"><h1 itemprop="name">Mini Diva</h1></div>
<section class="aboutMeSection"><div class="title">简介 Mini Diva</div>
  <div>Love being here from time to time.</div></section>
<div class="js-infoText">
  <div class="infoPiece"><span>感情状态:</span><span>单身</span></div>
  <div class="infoPiece"><span>出身地:</span><span>Czech Republic</span></div>
  <div class="infoPiece"><span>三围:</span><span>35-25-36</span></div>
  <div class="infoPiece"><span>身高:</span><span>5' 6" (167cm)</span></div>
  <div class="infoPiece"><span>纹身:</span><span>No</span></div>
</div>
</body></html>"""


def jpeg(width: int = 200, height: int = 200, colour: str = "#7a6f5f") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class Transport:
    """按地址回字节的传输替身：简介页与头像各一条，没登记的 404。"""

    def __init__(self, pages: dict | None = None):
        self.pages = pages if pages is not None else {
            PAGE_URL: (200, PAGE.encode("utf-8")), AVATAR: (200, jpeg())}
        self.calls: list[str] = []

    def __call__(self, request, _timeout, _limit):
        self.calls.append(request.url)
        status, body = self.pages.get(request.url, (404, b""))
        return HttpResponse(status, {}, body, request.url)

    def close(self):
        pass


class Case(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = LedgerDatabase(fresh_ledger(self.root))
        self.avatar_root = self.root / "generated" / "avatars"
        self.generated = self.root / "generated"
        self.busted: list[int] = []
        self.contract = SimpleNamespace(
            database=self.database, avatar_root=self.avatar_root, candidate_root=self.generated,
            cache_bust=lambda: self.busted.append(1))
        self.transport = Transport()
        # 装图那一侧会拿真要跑的人脸模型：这一层的判据与脸无关。
        self.face = mock.patch("peach.avatar_provider.FaceProbe").start()
        self.face.return_value = lambda _path: None
        self.addCleanup(mock.patch.stopall)

    # ── 造数据 ──
    def creator(self, name: str = "mini diva", provider: str = "pornhub",
                url: str = SOURCE_URL, ref: str = "mini-diva") -> int:
        column = normalize_entity_name(name)
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('creator',?,?,?,?)", (name, column, STAMP, STAMP))
            entity_id = int(cursor.lastrowid)
            if provider:
                connection.execute(
                    "INSERT INTO follow_source(entity_id,provider,ref,label,url,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (entity_id, provider, ref, name, url, STAMP, STAMP))
        return entity_id

    def run_once(self, entity_id: int) -> dict:
        handle = SimpleNamespace(run_id=11, progress=lambda **_kwargs: None)
        with mock.patch.object(followup, "HttpxTransport", return_value=self.transport):
            return followup.run(self.contract, followup.followup_key(entity_id), handle)

    def profile(self, entity_id: int) -> dict | None:
        with self.database.read_connection() as connection:
            return read_profile(connection, entity_id)

    def attempts(self, entity_id: int) -> dict:
        """这一条后继留的记性：跑完那一刻的指纹，和（取不到时的）下次可试时间。"""
        path = attempts_root(self.generated) / f"{followup.followup_key(entity_id).replace(':', '-')}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def image_bytes(self, entity_id: int) -> bytes:
        return (self.avatar_root / f"creator-{entity_id}.img").read_bytes()

    def install_own_image(self, entity_id: int) -> bytes:
        """人自己挑过的那一张：装图时记 `user_selected`。"""
        body = jpeg(300, 300, "#204060")
        avatar_picker.install(self.generated / "provider-cache" / "performer-avatars",
                             self.avatar_root, "creator", entity_id, body,
                             {"source": "upload", "source_kind": "user_selected"})
        return body


class PlanTests(Case):
    def declared(self) -> list[dict]:
        with self.database.read_connection() as connection:
            attempts = Attempts(attempts_root(self.generated))
            found = followup.plan(connection, self.avatar_root, attempts)
        return [{"key": item.key, "label": item.label} for item in found]

    def test_a_creator_with_a_source_downloads_its_profile(self):
        entity_id = self.creator()
        self.assertEqual(self.declared(), [
            {"key": f"creator-profile:{entity_id}", "label": "补发布账号资料：mini diva"}])

    def test_a_source_that_carries_no_profile_page_is_left_out(self):
        """别家的连接器只有作品列表：派出去就是一条注定取不到的失败。"""
        self.creator(provider="kemono")
        self.assertEqual(self.declared(), [])

    def test_a_new_creator_is_declared_for_just_that_one(self):
        """建档那一轮只认它刚建出来的那几位，别人的档案不该跟着重跑一遍。"""
        asked = self.creator()
        self.creator("someone else", ref="someone-else")
        self.assertEqual([row["key"] for row in followup.declared_for(self.contract, [asked])],
                         [f"creator-profile:{asked}"])
        self.assertEqual(followup.declared_for(self.contract, []), [])

    def test_a_named_creator_without_a_profile_source_is_left_out(self):
        asked = self.creator(provider="kemono")
        self.assertEqual(followup.declared_for(self.contract, [asked]), [])

    def test_a_profile_that_was_just_written_is_not_asked_for_again(self):
        entity_id = self.creator()
        self.run_once(entity_id)
        self.assertEqual(self.declared(), [])

    def test_a_stale_profile_is_fetched_again(self):
        """体重、兴趣、简介都会变；过了期限再取一次，页面上才跟得上。"""
        entity_id = self.creator()
        self.run_once(entity_id)
        old = datetime.fromtimestamp(time.time() - followup.REFRESH - 3600, timezone.utc)
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE performer_profile SET fetched_at=? WHERE entity_id=?",
                               (old.strftime("%Y-%m-%dT%H:%M:%S.000Z"), entity_id))
        self.assertEqual(len(self.declared()), 1)

    def test_an_avatar_deleted_from_disk_is_asked_for_again(self):
        entity_id = self.creator()
        self.run_once(entity_id)
        for path in self.avatar_root.glob(f"creator-{entity_id}.img*"):
            path.unlink()
        self.assertEqual(len(self.declared()), 1)


class RunTests(Case):
    def test_the_profile_row_lands_under_the_batch_that_wrote_it(self):
        entity_id = self.creator()
        summary = self.run_once(entity_id)
        row = self.profile(entity_id)
        self.assertEqual(summary["outcome"], followup.WRITTEN)
        self.assertEqual(row["source"], "auto:creator-profile@11")
        self.assertEqual(row["source_url"], PAGE_URL)
        self.assertEqual(row["height_cm"], 167)
        self.assertEqual((row["bust_cm"], row["waist_cm"], row["hip_cm"]), (89, 64, 91))
        self.assertEqual(row["birthplace"], "Czech Republic")
        self.assertEqual(row["raw"]["名前"], "Mini Diva")
        self.assertEqual(row["raw"]["简介"], "Love being here from time to time.")
        self.assertEqual([item["label"] for item in row["raw"]["资料"]],
                         ["感情", "出生地", "纹身"])

    def test_the_sites_avatar_is_installed_as_the_entity_image(self):
        entity_id = self.creator()
        summary = self.run_once(entity_id)
        self.assertTrue(summary["avatar_installed"])
        self.assertEqual(self.image_bytes(entity_id), jpeg())
        provenance = (self.avatar_root / f"creator-{entity_id}.img.provenance.json").read_text("utf-8")
        self.assertIn('"source_kind": "official_profile"', provenance)
        self.assertIn('"batch": "auto:creator-profile@11"', provenance)
        self.assertEqual(self.busted, [1])

    def test_an_image_the_user_picked_is_never_replaced(self):
        entity_id = self.creator()
        own = self.install_own_image(entity_id)
        summary = self.run_once(entity_id)
        self.assertEqual(summary["avatar"], "已有头像")
        self.assertFalse(summary["avatar_installed"])
        self.assertEqual(self.image_bytes(entity_id), own)
        self.assertEqual(self.profile(entity_id)["raw"]["名前"], "Mini Diva")

    def test_a_profile_written_by_somebody_else_is_left_alone(self):
        """资料行是别的来源写的（比如人自己填的），这里只补头像，不改资料。"""
        entity_id = self.creator()
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO performer_profile(entity_id,kana,source,source_url,fetched_at)"
                " VALUES(?,?,?,?,?)", (entity_id, "みにでぃーば", "user", "https://example.com/a", STAMP))
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.HELD)
        self.assertFalse(summary["profile_written"])
        self.assertTrue(summary["avatar_installed"])
        self.assertEqual(self.profile(entity_id)["kana"], "みにでぃーば")

    def test_a_page_that_cannot_be_read_writes_nothing_and_retries_within_the_hour(self):
        entity_id = self.creator()
        self.transport.pages = {}
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.FETCHED)
        self.assertTrue(summary["unavailable"])
        self.assertIsNone(self.profile(entity_id))
        until = float(self.attempts(entity_id)["retry_until"])
        self.assertGreater(until, time.time() + followup.RETRY_UNFETCHED - 60)

    def test_a_creator_without_a_source_writes_nothing(self):
        entity_id = self.creator(provider="")
        summary = self.run_once(entity_id)
        self.assertEqual(summary["detail"], "关注来源已解绑")
        self.assertIsNone(self.profile(entity_id))

    def test_a_landed_profile_waits_a_month_before_the_next_fetch(self):
        entity_id = self.creator()
        self.run_once(entity_id)
        until = float(self.attempts(entity_id)["retry_until"])
        self.assertGreater(until, time.time() + followup.REFRESH - 60)

    def test_the_page_is_read_where_the_source_points_and_the_avatar_right_after(self):
        entity_id = self.creator()
        self.run_once(entity_id)
        self.assertEqual(self.transport.calls[0], PAGE_URL)
        self.assertEqual(self.transport.calls[1], AVATAR)


class LookupTests(Case):
    """来源只给作品列表时（xhamster）：按名字去 pornhub 找这位的人物页（ADR-0118）。

    找到的那一页记成档案的站外编号，下一轮起直接按它取；同名不止一条、一条都没有时
    什么都不写，留给用户自己认。
    """

    #: xHamster 的创作者页：只给作品列表，没有人像与简介。
    XHAMSTER = "https://zh.xhamster.com/creators/mini-diva-88745/newest"

    def xhamster_creator(self, name: str = "mini diva") -> int:
        return self.creator(name=name, provider="xhamster", url=self.XHAMSTER,
                            ref="mini-diva-88745")

    def refs(self, entity_id: int) -> list[dict]:
        with self.database.read_connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT provider,external_kind,external_id,metadata_json FROM entity_external_ref"
                " WHERE entity_id=? ORDER BY provider", (entity_id,))]

    def pages_for(self, *kinds: str, status: int = 200) -> dict:
        table = {f"{pornhub_profiles.SITE}/{kind}/mini-diva": (200, PAGE.encode("utf-8"))
                 for kind in kinds}
        if status != 200:
            table = {url: (status, b"busy") for url in table}
        table[AVATAR] = (200, jpeg())
        return table

    def test_a_creator_whose_source_has_no_profile_page_is_asked_for_one(self):
        entity_id = self.xhamster_creator()
        with self.database.read_connection() as connection:
            found = followup.plan(connection, self.avatar_root,
                                  Attempts(attempts_root(self.generated)))
        self.assertEqual([item.key for item in found], [f"creator-profile:{entity_id}"])

    def test_the_page_is_found_by_name_and_recorded_for_the_next_time(self):
        entity_id = self.xhamster_creator()
        self.transport.pages = self.pages_for("model")
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.WRITTEN)
        self.assertEqual(self.profile(entity_id)["source_url"], f"{pornhub_profiles.SITE}/model/mini-diva")
        self.assertEqual(self.refs(entity_id), [{
            "provider": "pornhub", "external_kind": "creator", "external_id": "mini-diva",
            "metadata_json": '{"page_kind": "model"}'}])

    def test_the_recorded_page_is_used_without_looking_the_name_up_again(self):
        entity_id = self.xhamster_creator()
        self.transport.pages = self.pages_for("model")
        self.run_once(entity_id)
        self.transport.calls.clear()
        self.run_once(entity_id)
        self.assertEqual(self.transport.calls, [f"{pornhub_profiles.SITE}/model/mini-diva"])

    def test_two_people_with_the_same_name_write_nothing_and_retry_next_week(self):
        entity_id = self.xhamster_creator()
        self.transport.pages = self.pages_for("model", "users")
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.NOTHING)
        self.assertTrue(summary["missing"])
        self.assertIsNone(self.profile(entity_id))
        self.assertEqual(self.refs(entity_id), [])
        until = float(self.attempts(entity_id)["retry_until"])
        self.assertGreater(until, time.time() + followup.RETRY_MISSING - 60)

    def test_a_page_already_recorded_on_another_creator_is_not_borrowed(self):
        """这一页已经记在别的档案上时接上去就是把两个人的资料混成一份：交给用户去合并。"""
        other = self.creator(name="someone else", ref="someone-else")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
                " VALUES(?,'pornhub','creator','mini-diva')", (other,))
        entity_id = self.xhamster_creator()
        self.transport.pages = self.pages_for("model")
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.NOTHING)
        self.assertIn(f"#{other}", summary["detail"])
        self.assertIsNone(self.profile(entity_id))
        self.assertEqual(self.refs(entity_id), [])

    def test_a_name_nobody_on_the_site_uses_writes_nothing(self):
        entity_id = self.xhamster_creator()
        self.transport.pages = {}
        summary = self.run_once(entity_id)
        self.assertEqual(summary["outcome"], followup.NOTHING)
        self.assertIsNone(self.profile(entity_id))


class KeyTests(unittest.TestCase):
    def test_the_key_carries_the_entity_and_nothing_else(self):
        self.assertEqual(followup.followup_key(7), "creator-profile:7")
        self.assertEqual(followup.parse_key("creator-profile:7"), 7)
        for bad in ("creator-profile:", "creator-profile:x", "performer-profile:7"):
            with self.assertRaises(ValueError):
                followup.parse_key(bad)

    def test_the_page_address_comes_from_the_follow_source(self):
        """地址就是来源自己那个，主机换成中文站：首页、作品页、简介页给的节点是同一批。"""
        self.assertEqual(pornhub_profiles.page_url(SOURCE_URL), PAGE_URL)
        self.assertEqual(pornhub_profiles.page_url("https://www.pornhub.com/model/mini-diva"),
                         "https://cn.pornhub.com/model/mini-diva")


if __name__ == "__main__":
    unittest.main()
