"""xHamster 创作者页连接器与播放解析的隔离测试。

夹具的字段名与 DOM 结构取自 2026-10-24 对 `zh.xhamster.com/creators/shinaryen-89795/newest`
的实测响应：她的作品在那个带名字的 `div.thumb-list` 里（`data-video-id` 是作品身份，
`data-video-type="video"`；同一页的「动态」是 `moment`，推广位与推荐小列表也带
`data-video-id`），日期只在当天第一条上印一次（`24 October 2026`），分页写在
`/newest/<页码>` 这一段路径上，翻过最后一页回 404。作品页把播放器要的东西整份放在
`<script id='initials-script'>window.initials=…` 里，`sources.mp4` 是各档渐进式地址。
测试本身不联网：transport 全部注入。
"""
import unittest
from types import SimpleNamespace

from peach import follow_assets, follow_providers, follow_secrets
from peach.follow import FollowHistoryEnd
from peach.follow_sources import (
    FollowSourceError, build_connector, parse_source_url,
)
from peach.follow_stream import (
    FollowMediaResolver, FollowMediaUnavailable, media_type, resolvable_at_play,
    xhamster_media_sources,
)
from peach.http import HttpResponse

CREATOR = "https://zh.xhamster.com/creators/shinaryen-89795/newest"


def _transport(status=200, body=b"", headers=None, record=None):
    def call(request, timeout, max_bytes):
        if record is not None:
            record.append(request)
        return HttpResponse(status, headers or {}, body)
    return call


def _card(video_id: str, title: str, *, slug: str = "a-video", kind: str = "video",
          duration: str = "03:44", added: str | None = "24 October 2026",
          thumb: str = "https://ic-vt-nss.xhcdn.com/a/hash/s(w:526,h:298),webp/v2/526x298.webp") -> str:
    date = (f'<div class="body-bold primary dateAdded video-thumb__date-added">{added}</div>'
            if added else "")
    return (
        '<div class="container thumb-list__item video-thumb video-thumb--type-video"'
        f' data-video-id="{video_id}" data-video-type="{kind}">'
        f"{date}"
        f'<a class="video-thumb__image-container" data-role="thumb-link"'
        f' href="https://zh.xhamster.com/videos/{slug}"'
        f' aria-label="{title}">'
        f'<img data-role="thumb-preview-img" src="https://ic-vt-nss.xhcdn.com/a/big/v2/2560x1440.webp"'
        f' srcset="{thumb} 1x, https://ic-vt-nss.xhcdn.com/a/hash/s(w:1280,h:720)/v2/1280x720.webp 2x"/>'
        "</a>"
        f'<div data-role="video-duration">{duration}</div>'
        "</div>"
    )


def _page(cards: list[str], *, name: str = "shinaryen") -> bytes:
    return (
        "<html><head><title>Shinaryen</title></head><body>"
        '<div class="promo"><div data-video-id="1" data-video-type="video">推广位</div></div>'
        f'<div class="thumb-list" data-ecommerce-list-name="{name}">' + "".join(cards) + "</div>"
        '<div class="thumb-list" data-ecommerce-list-name="recommended">'
        '<div data-video-id="999" data-video-type="video">别人的作品</div>'
        "</div></body></html>"
    ).encode("utf-8")


WATCH = """<html><body><script id='initials-script'>window.initials={"videoSwitchComponent":{},
"downloadDropdownComponent":{"videoId":31389143,"videoTitle":"x",
 "sources":{"mp4":{"144p":"https://video7.xhcdn.com/key=a/144p.h264.mp4?end=1792850400",
                   "480p":"https://video7.xhcdn.com/key=a/480p.h264.mp4?end=1792850400",
                   "720p":"https://video7.xhcdn.com/key=a/720p.h264.mp4?end=1792850400"},
            "hls":{}}}};</script></body></html>"""


class AddressTests(unittest.TestCase):
    def test_the_creator_page_is_the_source(self):
        for url in (CREATOR,
                    "https://zh.xhamster.com/creators/shinaryen-89795",
                    "https://www.xhamster.com/creators/shinaryen-89795/newest/3",
                    "xhamster.com/creators/shinaryen-89795/newest?page=4"):
            with self.subTest(url=url):
                parsed = parse_source_url(url)
                self.assertEqual(parsed.provider, "xhamster")
                self.assertEqual(parsed.ref, "shinaryen-89795")
                self.assertEqual(parsed.url, CREATOR)
                self.assertEqual(parsed.label, "shinaryen")

    def test_another_tab_of_the_same_page_is_refused(self):
        """`/exclusive` 之类是站点另外排的清单，不是她按时间的新作。"""
        for url in ("https://zh.xhamster.com/creators/shinaryen-89795/exclusive",
                    "https://zh.xhamster.com/videos/step-mom-xhXhBHx",
                    "https://zh.xhamster.com/creators/shinaryen",
                    "https://zh.xhamster.com/creators/shinaryen-89795/followers/1"):
            with self.subTest(url=url):
                with self.assertRaises(FollowSourceError):
                    parse_source_url(url)

    def test_a_ref_without_the_creator_id_is_refused(self):
        conn = build_connector("xhamster", transport=_transport())
        for ref in ("shinaryen", "shinaryen-", "", "-89795"):
            with self.subTest(ref=ref):
                with self.assertRaises(FollowSourceError):
                    conn.fetch(ref)


class RegistryTests(unittest.TestCase):
    def test_the_source_is_registered_as_a_work_source(self):
        spec = follow_providers.PROVIDERS["xhamster"]
        self.assertEqual(spec.label, "xHamster")
        self.assertEqual(spec.semantics, "work")
        self.assertTrue(spec.backfill)
        self.assertEqual(spec.url_hosts, ("xhamster.com",))
        # 正片那一跳 302 到 `ip*.ahcdn.com`：少了它，媒体代理整站取不到文件。
        self.assertEqual(set(spec.hosts), {"xhcdn.com", "ahcdn.com"})

    def test_the_page_shows_the_source_icon_and_no_credential_is_needed(self):
        self.assertEqual(follow_assets.SOURCE_ICON_URLS["xhamster"],
                         "https://zh.xhamster.com/favicon.ico")
        self.assertEqual(follow_secrets.CREDENTIAL_GUIDE["xhamster"], {"requirement": "none"})


class FetchTests(unittest.TestCase):
    def fetch(self, body: bytes, *, page: int = 0, status: int = 200) -> tuple:
        seen = []
        conn = build_connector("xhamster", transport=_transport(status, body, record=seen),
                               max_items=100)
        return conn.fetch("shinaryen-89795", page=page), seen

    def test_a_card_carries_the_identity_title_cover_duration_and_day(self):
        body = _page([_card("31389143", "继母教继子如何做爱并中出")])
        fetch, _seen = self.fetch(body)
        candidate = fetch.candidates[0]
        self.assertEqual(fetch.provider, "xhamster")
        self.assertEqual(fetch.ref, "shinaryen-89795")
        self.assertEqual(fetch.request_url, CREATOR)
        self.assertEqual(candidate.external_id, "31389143")
        self.assertEqual(candidate.title, "继母教继子如何做爱并中出")
        self.assertEqual(candidate.url, "https://zh.xhamster.com/videos/a-video")
        self.assertEqual(candidate.duration, 224.0)
        self.assertEqual(candidate.published_at, "2026-10-24T00:00:00+00:00")
        self.assertEqual(candidate.extra["published_precision"], "approximate")
        # 卡片尺寸那一档就够：`src` 是 2560×1440 的原图，一页几十张。
        self.assertEqual(candidate.thumb_url, "https://ic-vt-nss.xhcdn.com/a/hash/s(w:526,h:298),webp/v2/526x298.webp")

    def test_only_her_own_list_is_read(self):
        """推广位、`moments` 与「推荐」小列表也带 `data-video-id`，都不是她的新作。"""
        body = _page([
            _card("31389143", "第一条"),
            _card("31374522", "一条动态", kind="moment", slug="moment/xh1"),
            _card("31370199", "第二条"),
        ])
        fetch, _seen = self.fetch(body)
        self.assertEqual([item.external_id for item in fetch.candidates],
                         ["31389143", "31370199"])

    def test_a_day_is_printed_once_and_carries_down_the_list(self):
        # 2026-10-24 实测：一天只在第一条上印日期，沿用与作品页里的 `created` 对得上。
        body = _page([
            _card("31389143", "第一条", added="22 October 2026"),
            _card("31374522", "第二条", added=None),
            _card("31370199", "第三条", added=None),
            _card("31356749", "第四条", added="21 October 2026"),
        ])
        fetch, _seen = self.fetch(body)
        self.assertEqual([item.published_at for item in fetch.candidates],
                         ["2026-10-22T00:00:00+00:00"] * 3 + ["2026-10-21T00:00:00+00:00"])

    def test_a_card_without_a_readable_day_keeps_the_unknown_mark(self):
        body = _page([_card("31389143", "第一条", added="上周")])
        fetch, _seen = self.fetch(body)
        self.assertIsNone(fetch.candidates[0].published_at)
        self.assertEqual(fetch.candidates[0].extra["published_precision"], "unknown")

    def test_the_second_page_is_a_path_segment(self):
        """`?page=2` 被站点忽略、回的还是第一页那 46 条；只有 `/newest/2` 换页。"""
        _fetch, seen = self.fetch(_page([_card("31389143", "第一条")]), page=1)
        self.assertEqual([request.url for request in seen], [f"{CREATOR}/2"])
        self.assertEqual(seen[0].headers.get("Accept-Language"), "zh-CN,zh;q=0.8")

    def test_the_page_after_the_last_one_is_the_end_of_the_history(self):
        conn = build_connector("xhamster", transport=_transport(404, b"<html>404</html>"))
        with self.assertRaises(FollowHistoryEnd):
            conn.fetch("shinaryen-89795", page=11)

    def test_a_list_that_parses_to_nothing_is_an_error_not_an_empty_page(self):
        """第一页解析不出作品是页面变了，不是这位没有作品。"""
        conn = build_connector("xhamster", transport=_transport(200, b"<html></html>"))
        with self.assertRaises(FollowSourceError):
            conn.fetch("shinaryen-89795")

    def test_the_creator_page_itself_carries_no_avatar_or_bio(self):
        """人像与简介来自 pornhub 的同一位（`creator_profile_followup`），这里不猜。"""
        candidate = self.fetch(_page([_card("31389143", "第一条")]))[0].candidates[0]
        self.assertNotIn("author", candidate.extra)
        self.assertIsNone(candidate.summary)


class MediaTests(unittest.TestCase):
    def test_the_watch_page_lists_every_progressive_quality(self):
        found = xhamster_media_sources(WATCH, "31389143")
        self.assertEqual([height for height, _url in found], [720, 480, 144])
        self.assertTrue(found[0][1].startswith("https://video7.xhcdn.com/"))

    def test_another_video_on_the_same_page_is_not_this_one(self):
        self.assertEqual(xhamster_media_sources(WATCH, "31374522"), ())
        self.assertEqual(xhamster_media_sources("<html></html>", "31389143"), ())

    def _item(self, item_id: int = 5) -> SimpleNamespace:
        return SimpleNamespace(
            id=item_id, provider="xhamster",
            url="https://zh.xhamster.com/videos/step-mom-xhXhBHx",
            media_url=None, metadata={"media_kind": "video"}, external_id="31389143",
        )

    def _resolver(self, body: bytes = WATCH.encode("utf-8")):
        def transport(request, timeout, max_bytes):
            return HttpResponse(200, {}, body)
        return FollowMediaResolver(transport)

    def test_playing_resolves_the_highest_quality_and_keeps_the_choice(self):
        resolver = self._resolver()
        resolved = resolver.resolve(self._item())
        self.assertEqual([height for height, _ in resolved.qualities], [720, 480, 144])
        self.assertTrue(resolved.url.endswith("720p.h264.mp4?end=1792850400"))
        self.assertEqual(resolved.allowed_hosts, ("xhcdn.com", "ahcdn.com"))
        self.assertEqual(resolved.referer, "https://zh.xhamster.com/videos/step-mom-xhXhBHx")
        chosen = resolver.resolve(self._item(), height=480)
        self.assertIn("480p", chosen.url)

    def test_a_page_without_media_reports_unavailable(self):
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(b"<html></html>").resolve(self._item())

    def test_a_media_url_outside_the_source_hosts_is_dropped(self):
        body = WATCH.replace("https://video7.xhcdn.com", "https://elsewhere.example").encode("utf-8")
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(body).resolve(self._item())

    def test_the_player_type_and_playability_follow_the_source(self):
        self.assertEqual(media_type("xhamster", "https://video7.xhcdn.com/a/720p.h264.mp4"),
                         "video/mp4")
        self.assertTrue(resolvable_at_play("xhamster", CREATOR))
        self.assertFalse(resolvable_at_play("xhamster", "https://elsewhere.example/creators/x-1"))
        # 站点地址不可信时不去解析详情页。
        self.assertFalse(resolvable_at_play("kemono", CREATOR))


if __name__ == "__main__":
    unittest.main()
