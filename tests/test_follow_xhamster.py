"""xHamster 创作者页连接器与播放解析的隔离测试。

夹具的字段名取自 2026-10-24 对 `zh.xhamster.com/creators/shinaryen-89795/full-length/newest`
的实测响应：作品清单在页面内嵌的 `window.initials` 里（`trendingVideoSectionComponent.
videoListProps.videoThumbProps`，每项带 `id`、`duration`（秒）、`created`（epoch 秒）、
`title`、`pageURL`、`thumbURL`），分页写在 `/full-length/newest/<页码>` 这一段路径上，
翻过最后一页回 404。作品页把播放器的 HLS 母清单地址写在 `<link rel="preload">` 上
（`_TPL_` 是档位占位符），母清单一档一行，`RESOLUTION=` 之后跟着子清单地址。
测试本身不联网：transport 全部注入。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from peach import follow_assets, follow_providers, follow_secrets
from peach.follow import FollowHistoryEnd
from peach.follow_sources import (
    FollowSourceError, build_connector, parse_source_url, xhamster_listing,
)
from peach.follow_stream import (
    FollowMediaResolver, FollowMediaUnavailable, ResolvedFollowMedia, media_type,
    resolvable_at_play, xhamster_hls_codecs, xhamster_hls_master, xhamster_hls_variants,
)
from peach.http import HttpResponse

CREATOR = "https://zh.xhamster.com/creators/shinaryen-89795/full-length/newest"
WATCH_URL = "https://zh.xhamster.com/videos/step-mom-xhXhBHx"
#: 母清单的签名地址：`KEY,exp` 前缀、`multi=` 里列着这一条有哪些分辨率、`_TPL_` 占位档位。
FRONT = ("https://video-nss.xhcdn.com/E58FK8v1UvWDYvOG-grtNw==,1792854000/media=hls4/"
         "multi=256x144:144p:,426x240:240p:,854x480:480p:,1280x720:720p:,1920x1080:1080p:,"
         "3840x2160:2160p:/031/247/098")

#: 实测第一条作品的发布时间（`created`）与它对应的 UTC 时刻。
CREATED = 1792430526
CREATED_ISO = "2026-10-19T17:22:06Z"
THUMB = "https://ic-vt-nss.xhcdn.com/a/hash/s(w:526,h:298),webp/031/339/968/v2/526x298.206.webp"

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


def _item(video_id: int, title: str, *, slug: str = "a-video", duration: int = 881,
          created: int = CREATED, kind: str = "video", thumb: str = THUMB) -> dict:
    """页面 JSON 里的一条作品。字段名与实测一致。"""
    return {"id": video_id, "duration": duration, "created": created, "title": title,
            "videoType": kind, "pageURL": f"https://zh.xhamster.com/videos/{slug}",
            "thumbURL": thumb, "views": 7836, "isUHD": True,
            "landing": {"type": "person", "id": 89795}}


#: 地域拦截页：状态 200、四百来字节，页面里没有 `window.initials`。
_BLOCKED = "<html><body>법적 사유로 이용 불가</body></html>".encode("utf-8")


def _page(items: list[dict] | None = None) -> bytes:
    """创作者页：作品清单在 `window.initials` 里，动态另成一栏。"""
    payload: dict = {"channelInfo": {"id": 89795, "type": "pornstar",
                                     "slug": "shinaryen-89795", "personType": "creator"},
                     "momentsListComponent": {"videoThumbProps": [_item(900001, "一条动态", kind="moment")]}}
    if items is not None:
        payload["trendingVideoSectionComponent"] = {
            "videoListProps": {"videoThumbProps": items}}
    return ("<html><head><title>Shinaryen</title></head><body>"
            "<script id='initials-script'>window.initials="
            + json.dumps(payload, ensure_ascii=False) + ";</script></body></html>").encode("utf-8")


class AddressTests(unittest.TestCase):
    def test_the_full_length_tab_is_the_source(self):
        for url in (CREATOR,
                    "https://zh.xhamster.com/creators/shinaryen-89795",
                    "https://www.xhamster.com/creators/shinaryen-89795/full-length/newest/3",
                    "https://zh.xhamster.com/creators/shinaryen-89795/newest",
                    "xhamster.com/creators/shinaryen-89795/newest?page=4"):
            with self.subTest(url=url):
                parsed = parse_source_url(url)
                self.assertEqual(parsed.provider, "xhamster")
                self.assertEqual(parsed.ref, "shinaryen-89795")
                self.assertEqual(parsed.url, CREATOR)
                self.assertEqual(parsed.label, "shinaryen")

    def test_another_tab_of_the_same_page_is_refused(self):
        """`/exclusive` 之类是站点另外排的清单，不是她的正片。"""
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
        self.assertEqual(spec.source_url,
                         "https://zh.xhamster.com/creators/{ref}/full-length/newest")
        # 正片那一跳 302 到 `ip*.ahcdn.com`：少了它，媒体代理整站取不到文件。
        self.assertEqual(set(spec.hosts), {"xhcdn.com", "ahcdn.com"})

    def test_the_page_shows_the_source_icon_and_no_credential_is_needed(self):
        self.assertEqual(follow_assets.SOURCE_ICON_URLS["xhamster"],
                         "https://zh.xhamster.com/favicon.ico")
        self.assertEqual(follow_secrets.CREDENTIAL_GUIDE["xhamster"], {"requirement": "none"})


class ListingTests(unittest.TestCase):
    def test_the_listing_is_read_from_the_page_json(self):
        listing = xhamster_listing(_page([_item(31339968, "出差")]).decode("utf-8"))
        self.assertEqual([row["id"] for row in listing], [31339968])

    def test_a_page_without_the_page_json_gives_nothing(self):
        for html in ("", "<html></html>", "window.initials={\"momentsListComponent\":{}}"):
            with self.subTest(html=html):
                self.assertIsNone(xhamster_listing(html))

    def test_a_brace_inside_a_title_does_not_end_the_payload(self):
        """标题里有花括号：配对必须认字符串边界，否则整个页面 JSON 都解析不出来。"""
        html = _page([_item(31339968, "继母说「没有渗透」，但仍然帮我射精}")]).decode("utf-8")
        self.assertEqual([row["id"] for row in xhamster_listing(html)], [31339968])


class FetchTests(unittest.TestCase):
    def fetch(self, body: bytes | list[bytes], *, page: int = 0, status: int = 200) -> tuple:
        seen = []
        pages = list(body) if isinstance(body, list) else [body]
        def transport(request, timeout, max_bytes):
            seen.append(request)
            return HttpResponse(status, {}, pages.pop(0) if len(pages) > 1 else pages[0])
        # 拦截页的重试之间会等一下换出口；测试里不真等。
        conn = build_connector("xhamster", transport=transport, max_items=100,
                               sleeper=lambda _delay: None)
        return conn.fetch("shinaryen-89795", page=page), seen

    def test_a_list_item_carries_identity_title_cover_duration_and_time(self):
        fetch, _seen = self.fetch(_page([_item(31339968, "出差时被同事发现我在打飞机")]))
        candidate = fetch.candidates[0]
        self.assertEqual(fetch.provider, "xhamster")
        self.assertEqual(fetch.ref, "shinaryen-89795")
        self.assertEqual(fetch.request_url, CREATOR)
        self.assertEqual(candidate.external_id, "31339968")
        self.assertEqual(candidate.title, "出差时被同事发现我在打飞机")
        self.assertEqual(candidate.url, "https://zh.xhamster.com/videos/a-video")
        self.assertEqual(candidate.duration, 881.0)
        self.assertEqual(candidate.published_at, CREATED_ISO)
        self.assertEqual(candidate.extra["published_precision"], "exact")
        # 卡片尺寸那一档就够：整页几十张，原图那个尺寸是浪费。
        self.assertEqual(candidate.thumb_url, THUMB)

    def test_a_moment_in_the_list_is_not_a_work(self):
        fetch, _seen = self.fetch(_page([
            _item(31339968, "第一条"),
            _item(31339969, "一条动态", kind="moment", slug="moments/xh1"),
            _item(31339970, "第二条"),
        ]))
        self.assertEqual([item.external_id for item in fetch.candidates],
                         ["31339968", "31339970"])

    def test_the_second_page_is_a_path_segment(self):
        """`?page=2` 被站点忽略、回的还是第一页那 46 条；只有 `/full-length/newest/2` 换页。"""
        _fetch, seen = self.fetch(_page([_item(31339968, "第一条")]), page=1)
        self.assertEqual([request.url for request in seen], [f"{CREATOR}/2"])
        self.assertEqual(seen[0].headers.get("Accept-Language"), "zh-CN,zh;q=0.8")

    def test_the_page_after_the_last_one_is_the_end_of_the_history(self):
        conn = build_connector("xhamster", transport=_transport(404, b"<html>404</html>"))
        with self.assertRaises(FollowHistoryEnd):
            conn.fetch("shinaryen-89795", page=5)

    def test_an_interception_page_is_fetched_again(self):
        """这条出口上偶尔回的是地域拦截页：状态 200、四百来字节、页面里没有这份清单。"""
        fetch, seen = self.fetch([_BLOCKED, _page([_item(31339968, "第一条")])])
        self.assertEqual([item.external_id for item in fetch.candidates], ["31339968"])
        self.assertEqual(len(seen), 2)
        # 拦截页会把条件请求那两样带歪，重取时不带。
        self.assertNotIn("If-None-Match", seen[1].headers)

    def test_two_interceptions_in_a_row_are_still_retried(self):
        """同一批抽样里八次有五次被拦：只重取一次不够。"""
        fetch, seen = self.fetch([_BLOCKED, _BLOCKED, _page([_item(31339968, "第一条")])])
        self.assertEqual([item.external_id for item in fetch.candidates], ["31339968"])
        self.assertEqual(len(seen), 3)

    def test_a_page_still_without_the_listing_is_an_error(self):
        """重取到头还是拦：说没取到，别说这位没有作品。"""
        conn = build_connector("xhamster", transport=_transport(200, b"<html></html>"),
                               sleeper=lambda _delay: None)
        with self.assertRaises(FollowSourceError):
            conn.fetch("shinaryen-89795")

    def test_a_later_page_without_the_listing_is_not_the_end_of_history(self):
        """尽头是 404；一页拦截不能被读成「没有更多历史」，那会把回填当完成。"""
        conn = build_connector("xhamster", transport=_transport(200, b"<html></html>"),
                               sleeper=lambda _delay: None)
        with self.assertRaises(FollowSourceError):
            conn.fetch("shinaryen-89795", page=3)

    def test_the_creator_page_itself_carries_no_avatar_or_bio(self):
        """人像与简介来自 pornhub 的同一位（`creator_profile_followup`），这里不猜。"""
        candidate = self.fetch(_page([_item(31339968, "第一条")]))[0].candidates[0]
        self.assertNotIn("author", candidate.extra)
        self.assertIsNone(candidate.summary)
        self.assertIsNone(candidate.author)


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

    def test_the_codec_the_browser_names_goes_first_and_h264_is_the_fallback(self):
        """两条梯子分辨率一样，av1 的字节只有 h264 的三分之一上下：浏览器报得出能解就先用它。

        没点名、点名的是 h264、或者报了个认不出的，仍旧落到 h264——换签一次就能成功的那档。
        """
        self.assertEqual(xhamster_hls_codecs("av1", "av1"), ("av1", "h264"))
        self.assertEqual(xhamster_hls_codecs("h264", "av1"), ("av1", "h264"))
        self.assertEqual(xhamster_hls_codecs("h265", "av1"), ("av1", "h264", "h265"))
        self.assertEqual(xhamster_hls_codecs("av1", "h265"), ("h265", "h264", "av1"))
        self.assertEqual(xhamster_hls_codecs("av1"), ("h264", "av1"))
        self.assertEqual(xhamster_hls_codecs("h265"), ("h264", "h265"))
        self.assertEqual(xhamster_hls_codecs("h264"), ("h264",))
        self.assertEqual(xhamster_hls_codecs("av1", "vp9"), ("h264", "av1"))


class MediaTests(unittest.TestCase):
    def _item(self, item_id: int = 5) -> SimpleNamespace:
        return SimpleNamespace(
            id=item_id, provider="xhamster", url=WATCH_URL,
            media_url=None, metadata={"media_kind": "video"}, external_id="31389143",
        )

    def _resolver(self, page: str = WATCH,
                  masters: dict[str, tuple[int, bytes]] | None = None,
                  pages: list[str] | None = None):
        answers = masters or {}
        queue = list(pages or [page])

        def transport(request, timeout, max_bytes):
            for needle, (status, body) in answers.items():
                if needle in request.url:
                    return HttpResponse(status, {}, body)
            body = queue.pop(0) if len(queue) > 1 else queue[0]
            return HttpResponse(200, {}, body.encode("utf-8"))
        return FollowMediaResolver(transport)

    def test_playing_hands_over_the_master_with_every_quality(self):
        """母清单就是交给播放器的地址：四档都在里面，播放器据此自适应升降档。"""
        resolver = self._resolver(masters={"_TPL_.h264.mp4.m3u8": (200, _master())})
        resolved = resolver.resolve(self._item())
        self.assertTrue(resolved.playlist)
        self.assertEqual([height for height, _url in resolved.qualities], [2160, 1080, 480, 144])
        self.assertEqual(resolved.url, f"{FRONT}/_TPL_.h264.mp4.m3u8")
        self.assertEqual(resolved.qualities[0][1], f"{FRONT}/2160p.h264.mp4.m3u8")
        self.assertEqual(resolved.allowed_hosts, ("xhcdn.com", "ahcdn.com"))
        self.assertEqual(resolved.referer, WATCH_URL)
        # 点名要某一档时才换成那一档自己的子清单。
        chosen = resolver.resolve(self._item(), height=1080)
        self.assertEqual(chosen.url, f"{FRONT}/1080p.h264.mp4.m3u8")

    def test_a_refused_codec_swap_falls_back_to_the_page_codec(self):
        """换 codec 换不成时 CDN 回 `403 Wrong key`：那不是限流，这一条仍要能播。"""
        resolver = self._resolver(
            page=WATCH.replace("_TPL_.h264", "_TPL_.av1"),
            masters={"_TPL_.h264.mp4.m3u8": (403, b"Wrong key"),
                     "_TPL_.av1.mp4.m3u8": (200, _master("av1"))})
        resolved = resolver.resolve(self._item())
        self.assertEqual(resolved.url, f"{FRONT}/_TPL_.av1.mp4.m3u8")
        self.assertEqual(resolver.cooling("xhamster"), 0)

    def test_an_interception_page_is_fetched_again(self):
        """这条出口上偶尔回的是地域拦截页：状态 200、几百字节、页面里没有播放器。"""
        blocked = "<html><body>법적 사유로 이용 불가</body></html>"
        resolver = self._resolver(pages=[blocked, WATCH],
                                  masters={"_TPL_.h264.mp4.m3u8": (200, _master())})
        self.assertEqual(resolver.resolve(self._item()).url, f"{FRONT}/_TPL_.h264.mp4.m3u8")

    def test_a_page_without_a_playlist_reports_unavailable(self):
        """渐进式 mp4 只到 720p；没有母清单就不拿它充数，宁可说取不到。"""
        with self.assertRaises(FollowMediaUnavailable):
            self._resolver(page=WATCH_WITHOUT_PLAYLIST).resolve(self._item())

    def test_the_browser_named_codec_resolves_to_its_own_ladder(self):
        """页面给 Chrome 签的是 av1：点名 av1 就解析那条梯子，两条各占一格、互不顶掉。"""
        page = WATCH.replace("_TPL_.h264", "_TPL_.av1")
        asked = []

        def transport(request, timeout, max_bytes):
            asked.append(request.url)
            if "_TPL_.av1.mp4.m3u8" in request.url:
                return HttpResponse(200, {}, _master("av1"))
            if "_TPL_.h264.mp4.m3u8" in request.url:
                return HttpResponse(200, {}, _master("h264"))
            return HttpResponse(200, {}, page.encode("utf-8"))

        resolver = FollowMediaResolver(transport)
        item = self._item()
        self.assertEqual(resolver.resolve(item, prefer_codec="av1").url,
                         f"{FRONT}/_TPL_.av1.mp4.m3u8")
        self.assertEqual(resolver.resolve(item).url, f"{FRONT}/_TPL_.h264.mp4.m3u8")
        settled = len(asked)
        self.assertEqual(resolver.resolve(item, prefer_codec="av1").url,
                         f"{FRONT}/_TPL_.av1.mp4.m3u8")
        self.assertEqual(resolver.resolve(item).url, f"{FRONT}/_TPL_.h264.mp4.m3u8")
        self.assertEqual(len(asked), settled)

    def test_a_refused_swap_for_the_named_codec_falls_back_to_h264(self):
        """点名 av1 但换签换不成（`403 Wrong key`）：落到 h264，这一条照常能播。"""
        resolver = self._resolver(masters={"_TPL_.av1.mp4.m3u8": (403, b"Wrong key"),
                                           "_TPL_.h264.mp4.m3u8": (200, _master())})
        self.assertEqual(resolver.resolve(self._item(), prefer_codec="av1").url,
                         f"{FRONT}/_TPL_.h264.mp4.m3u8")

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


class PlaybackPersistenceTests(unittest.TestCase):
    """到期时刻写在路径里的那种地址（`…==,1792926000/media=hls4/…`）也要落盘。

    只认 `validto=`／`end=` 的那一版认不出它，于是每打开一次都要重抓一次作品页，
    起播白等两秒——这条来源的全部地址都是这个写法。
    """

    def _front(self, seconds: int = 3600) -> str:
        return FRONT.replace("1792854000", str(int(time.time()) + seconds))

    def _item(self) -> SimpleNamespace:
        return SimpleNamespace(id=5, provider="xhamster", url=WATCH_URL,
                               media_url=None, metadata={"media_kind": "video"},
                               external_id="31389143")

    def _entries(self, root: str) -> list[str]:
        return sorted(path.name for path
                      in (Path(root) / FollowMediaResolver.PLAYBACK_DIR).glob("*.json"))

    def _expiry(self, front: str) -> int:
        """地址里 token 后面那个逗号跟着的就是到期时刻。"""
        return int(front.split("==", 1)[1].split(",", 1)[1].split("/", 1)[0])

    def test_the_expiry_stamped_in_the_path_is_read(self):
        front = self._front()
        expiry = self._expiry(front)
        resolved = ResolvedFollowMedia(f"{front}/_TPL_.h264.mp4.m3u8", WATCH_URL,
                                       qualities=((2160, f"{front}/2160p.h264.mp4.m3u8"),),
                                       allowed_hosts=("xhcdn.com",), playlist=True)
        self.assertEqual(FollowMediaResolver.playback_expiry(resolved), expiry)

    def test_the_master_is_written_down_and_reused_without_a_page_fetch(self):
        front = self._front()
        page = WATCH.replace(FRONT, front)
        asked = []

        def transport(request, timeout, max_bytes):
            asked.append(request.url)
            if "_TPL_" in request.url:
                return HttpResponse(200, {}, _master())
            return HttpResponse(200, {}, page.encode("utf-8"))

        with tempfile.TemporaryDirectory() as root:
            resolved = FollowMediaResolver(transport, state_root=root).resolve(self._item())
            self.assertEqual(resolved.url, f"{front}/_TPL_.h264.mp4.m3u8")
            self.assertEqual(self._entries(root), ["5.json"])

            later = []

            def warm(request, timeout, max_bytes):
                later.append(request.url)
                return HttpResponse(500, {}, b"")

            again = FollowMediaResolver(warm, state_root=root).resolve(self._item())
        # 一个请求都没发：地址还没过期，盘上那份直接能用。
        self.assertEqual(later, [])
        self.assertEqual(again.url, f"{front}/_TPL_.h264.mp4.m3u8")
        self.assertEqual([height for height, _url in again.qualities], [2160, 1080, 480, 144])

    def test_the_two_ladders_keep_their_own_file(self):
        front = self._front()
        page = WATCH.replace("_TPL_.h264", "_TPL_.av1").replace(FRONT, front)

        def transport(request, timeout, max_bytes):
            if "_TPL_.av1.mp4.m3u8" in request.url:
                return HttpResponse(200, {}, _master("av1"))
            if "_TPL_.h264.mp4.m3u8" in request.url:
                return HttpResponse(200, {}, _master("h264"))
            return HttpResponse(200, {}, page.encode("utf-8"))

        with tempfile.TemporaryDirectory() as root:
            resolver = FollowMediaResolver(transport, state_root=root)
            resolver.resolve(self._item(), prefer_codec="av1")
            resolver.resolve(self._item())
            self.assertEqual(self._entries(root), ["5.av1.json", "5.json"])
            # 上游拒收清单时按条目 id 丢：调用方不知道这次是哪个浏览器在放，两条都丢。
            self.assertTrue(resolver.forget(5))
            self.assertEqual(self._entries(root), [])
            self.assertFalse(resolver.forget(5))


if __name__ == "__main__":
    unittest.main()
