"""xHamster 创作者页连接器与播放解析的隔离测试。

夹具的字段名与 DOM 结构取自 2026-10-24 对 `zh.xhamster.com/creators/shinaryen-89795/newest`
的实测响应：她的作品在那个带名字的 `div.thumb-list` 里（`data-video-id` 是作品身份，
`data-video-type="video"`；同一页的「动态」是 `moment`，推广位与推荐小列表也带
`data-video-id`），日期只在当天第一条上印一次（`24 October 2026`），分页写在
`/newest/<页码>` 这一段路径上，翻过最后一页回 404。作品页把播放器的 HLS 母清单地址写在
`<link rel="preload">` 上（`_TPL_` 是档位占位符），母清单一档一行，`RESOLUTION=` 之后
跟着子清单地址。测试本身不联网：transport 全部注入。
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
    xhamster_hls_codecs, xhamster_hls_master, xhamster_hls_variants,
)
from peach.http import HttpResponse

CREATOR = "https://zh.xhamster.com/creators/shinaryen-89795/newest"
WATCH_URL = "https://zh.xhamster.com/videos/step-mom-xhXhBHx"
#: 母清单的签名地址：`KEY,exp` 前缀、`multi=` 里列着这一条有哪些分辨率、`_TPL_` 占位档位。
FRONT = ("https://video-nss.xhcdn.com/E58FK8v1UvWDYvOG-grtNw==,1792854000/media=hls4/"
         "multi=256x144:144p:,426x240:240p:,854x480:480p:,1280x720:720p:,1920x1080:1080p:,"
         "3840x2160:2160p:/031/247/098")

def _master(codec: str = "h264") -> bytes:
    """母清单：一档两行，`RESOLUTION=` 那行给分辨率，下一行是子清单的相对地址。"""
    lines = ["#EXTM3U"]
    for height, bandwidth in ((144, 118132), (480, 544412), (1080, 3289413), (2160, 13305055)):
        lines.append(f"#EXT-X-STREAM-INF:PROGRAM-ID=1,AVERAGE-BANDWIDTH={bandwidth},"
                     f"BANDWIDTH={bandwidth},RESOLUTION={height * 16 // 9}x{height}")
        lines.append(f"{height}p.{codec}.mp4.m3u8")
    return ("\n".join(lines) + "\n").encode("utf-8")


#: 渐进式那一份只有四档、最高 720p：它是下载菜单的内容，不是播放器用的源。
WATCH = (f'<html><head><link rel="preload" href="{FRONT}/_TPL_.h264.mp4.m3u8"'
         ' as="fetch" crossorigin="true"/></head><body>'
         "<script id='initials-script'>window.initials={\"downloadDropdownComponent\":"
         "{\"videoId\":31389143,\"sources\":{\"mp4\":{\"144p\":\"https://video7.xhcdn.com/a/144p.h264.mp4\","
         "\"720p\":\"https://video7.xhcdn.com/a/720p.h264.mp4\"}}}};</script>"
         "</body></html>")
#: 只有渐进式清单、没有母清单的那一版页面。
WATCH_WITHOUT_PLAYLIST = "<html><body>" + WATCH.split("<body>", 1)[1]


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


class PlaylistTests(unittest.TestCase):
    """作品页与母清单的解析：1080p 与 4K 只在这条路上。"""

    def test_the_preload_link_gives_the_master_template_and_its_codec(self):
        self.assertEqual(xhamster_hls_master(WATCH), (FRONT, "h264"))
        for codec in ("av1", "h265"):
            with self.subTest(codec=codec):
                self.assertEqual(xhamster_hls_master(WATCH.replace("_TPL_.h264", f"_TPL_.{codec}")),
                                 (FRONT, codec))
        self.assertIsNone(xhamster_hls_master(WATCH_WITHOUT_PLAYLIST))

    def test_every_variant_is_listed_from_the_highest_down(self):
        found = xhamster_hls_variants(_master().decode(), f"{FRONT}/_TPL_.h264.mp4.m3u8")
        self.assertEqual([height for height, _url in found], [2160, 1080, 480, 144])
        self.assertEqual(found[0][1], f"{FRONT}/2160p.h264.mp4.m3u8")

    def test_a_variant_written_as_a_site_absolute_path_is_resolved(self):
        """`video7` 那一路把子清单写成 `/key=…` 开头的站内绝对路径。"""
        master = ("#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1,RESOLUTION=1920x1080\n"
                  "/key=RJej/media=hls4/x/1080p.av1.mp4.m3u8\n")
        self.assertEqual(
            xhamster_hls_variants(
                master, "https://video7.xhcdn.com/key=a/media=hls4/x/_TPL_.av1.mp4.m3u8"),
            ((1080, "https://video7.xhcdn.com/key=RJej/media=hls4/x/1080p.av1.mp4.m3u8"),))

    def test_h264_is_tried_first_and_the_page_codec_is_the_fallback(self):
        """Safari 解不了 av1：能换签就用 h264，换不成就用页面自己那一档。"""
        self.assertEqual(xhamster_hls_codecs("av1"), ("h264", "av1"))
        self.assertEqual(xhamster_hls_codecs("h265"), ("h264", "h265"))
        self.assertEqual(xhamster_hls_codecs("h264"), ("h264",))


class MediaTests(unittest.TestCase):
    def _item(self, item_id: int = 5) -> SimpleNamespace:
        return SimpleNamespace(
            id=item_id, provider="xhamster", url=WATCH_URL,
            media_url=None, metadata={"media_kind": "video"}, external_id="31389143",
        )

    def _resolver(self, page: str = WATCH,
                  masters: dict[str, tuple[int, bytes]] | None = None):
        answers = masters or {}

        def transport(request, timeout, max_bytes):
            for needle, (status, body) in answers.items():
                if needle in request.url:
                    return HttpResponse(status, {}, body)
            return HttpResponse(200, {}, page.encode("utf-8"))
        return FollowMediaResolver(transport)

    def test_playing_resolves_the_playlist_at_the_highest_quality(self):
        resolver = self._resolver(masters={"_TPL_.h264.mp4.m3u8": (200, _master())})
        resolved = resolver.resolve(self._item())
        self.assertTrue(resolved.playlist)
        self.assertEqual([height for height, _url in resolved.qualities], [2160, 1080, 480, 144])
        self.assertEqual(resolved.url, f"{FRONT}/2160p.h264.mp4.m3u8")
        self.assertEqual(resolved.allowed_hosts, ("xhcdn.com", "ahcdn.com"))
        self.assertEqual(resolved.referer, WATCH_URL)
        chosen = resolver.resolve(self._item(), height=1080)
        self.assertEqual(chosen.url, f"{FRONT}/1080p.h264.mp4.m3u8")

    def test_a_refused_codec_swap_falls_back_to_the_page_codec(self):
        """换 codec 换不成时 CDN 回 `403 Wrong key`：那不是限流，这一条仍要能播。"""
        resolver = self._resolver(
            page=WATCH.replace("_TPL_.h264", "_TPL_.av1"),
            masters={"_TPL_.h264.mp4.m3u8": (403, b"Wrong key"),
                     "_TPL_.av1.mp4.m3u8": (200, _master("av1"))})
        resolved = resolver.resolve(self._item())
        self.assertEqual(resolved.url, f"{FRONT}/2160p.av1.mp4.m3u8")
        self.assertEqual(resolver.cooling("xhamster"), 0)

    def test_a_page_without_a_playlist_reports_unavailable(self):
        """渐进式 mp4 只到 720p；没有母清单就不拿它充数，宁可说取不到。"""
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(page=WATCH_WITHOUT_PLAYLIST).resolve(self._item())

    def test_a_master_without_variants_reports_unavailable(self):
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(masters={"_TPL_.h264.mp4.m3u8": (200, b"#EXTM3U\n")}).resolve(self._item())

    def test_a_variant_outside_the_source_hosts_is_dropped(self):
        off = ("#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1,RESOLUTION=1920x1080\n"
               "https://elsewhere.example/1080p.h264.mp4.m3u8\n").encode("utf-8")
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(masters={"_TPL_.h264.mp4.m3u8": (200, off)}).resolve(self._item())

    def test_the_player_type_and_playability_follow_the_source(self):
        # 片源是清单：播放器要按 HLS 挂 hls.js，当 mp4 交给 `video` 元素就只得到一段解析
        # 不了的清单。
        self.assertEqual(media_type("xhamster", None), "application/vnd.apple.mpegurl")
        self.assertTrue(resolvable_at_play("xhamster", CREATOR))
        self.assertFalse(resolvable_at_play("xhamster", "https://elsewhere.example/creators/x-1"))
        # 站点地址不可信时不去解析详情页。
        self.assertFalse(resolvable_at_play("kemono", CREATOR))


if __name__ == "__main__":
    unittest.main()
