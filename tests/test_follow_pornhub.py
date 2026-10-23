"""Pornhub 人物页连接器的隔离测试。

夹具的字段名与 DOM 结构取自 2026-10-23／2026-10-24 对 `cn.pornhub.com` 的实测响应：
列表卡是 `li.pcVideoListItem`（`data-video-vkey` 是作品身份），详情页把 `uploadDate`
写在 JSON-LD 里、分类与标签各在一个容器里，中文站的分类与标签是中文（标签那侧还带
`data-translated="translated-tags"`，没有 `title`，字在 `<span>` 里）。测试本身不联网：
transport 全部注入。
"""
import unittest
import urllib.parse

from peach import follow_assets, follow_providers, follow_secrets
from peach.follow import FollowHistoryEnd
from peach.follow_sources import (
    PornhubConnector, build_connector, parse_source_url,
)
from peach.http import HttpResponse


def _transport(status=200, body=b"", headers=None, record=None):
    def call(request, timeout, max_bytes):
        if record is not None:
            record.append(request)
        return HttpResponse(status, headers or {}, body)
    return call


def _card(viewkey: str, title: str, *, duration: str = "15:04", added: str = "3 days ago") -> str:
    return (
        '<li class="pcVideoListItem js-pop videoblock" data-video-vkey="' + viewkey + '"'
        ' data-video-id="1">'
        '<div class="phimage"><a class="linkVideoThumb"'
        f' href="/view_video.php?viewkey={viewkey}" title="{title}">'
        '<img src="data:image/gif;base64,R0lGOD"'
        f' data-image="https://pix-cdn77.phncdn.com/videos/{viewkey}.jpg"'
        ' data-mediabook="https://ew.phncdn.com/preview.webm?validto=1"/>'
        "</a></div>"
        '<div class="thumbnail-info">'
        f'<span class="duration"><var class="duration">{duration}</var></span>'
        f'<span class="added">{added}</span><span class="views">1234</span>'
        "</div></li>"
    )


def _page(cards, *, canonical: str, page_title: str = "Videos") -> bytes:
    return (
        '<html><head>'
        f'<title>{page_title}</title>'
        f'<link rel="canonical" href="{canonical}" />'
        "</head><body>"
        '<ul class="videos">' + "".join(cards) + "</ul>"
        "</body></html>"
    ).encode("utf-8")


DETAIL = b"""<html><head>
<meta property="video:duration" content="904" />
<script type="application/ld+json">{"@type": "VideoObject",
 "uploadDate": "2026-09-18T12:47:42+00:00"}</script>
</head><body>
<div class="usernameWrap"><a href="/model/mini-diva" title="Mini Diva">Mini Diva</a></div>
<div class="categoriesWrapper">
  <a href="/categories/amateur" title="Amateur">Amateur</a>
  <a href="/categories/hd-porn" title="HD Porn">HD Porn</a>
</div>
<div class="tagsWrapper">
  <a href="/video?search=tagone">Tag One</a>
  <a href="/video?search=tagtwo">Tag Two</a>
  <a href="/video?search=tagone">Tag One</a>
</div>
</body></html>"""


CHINESE_DETAIL = """<html><head>
<meta charset="utf-8" />
<meta property="video:duration" content="904" />
<script type="application/ld+json">{"@type": "VideoObject",
 "uploadDate": "2026-09-18T12:47:42+00:00"}</script>
</head><body>
<div class="usernameWrap"><a href="/model/mini-diva" title="Mini Diva">Mini Diva</a></div>
<div class="categoriesWrapper">
  <p>分类&nbsp;</p>
  <a href="/categories/teen">18-25歲</a>
  <a href="/video?c=1">亚洲人</a>
</div>
<div class="tagsWrapper">
  <p>标签</p>
  <a data-translated="translated-tags" href="/video/search?search=%E8%82%9B"><span>肛门性爱</span></a>
  <a data-translated="translated-tags" href="/video/search?search=%E5%A4%A7"><span>大屁股</span></a>
</div>
</body></html>""".encode("utf-8")


class PornhubPersonPageParsingTests(unittest.TestCase):
    def setUp(self):
        # 页型是按 slug 记住的：上一个用例的发现不能漏到这一个。
        PornhubConnector.clear_known_bases()

    def test_the_three_person_page_shapes_all_register_the_same_source(self):
        for url in ("https://cn.pornhub.com/model/mini-diva/videos",
                    "https://www.pornhub.com/model/mini-diva",
                    "https://www.pornhub.com/pornstar/lana-rhoades/videos",
                    "https://www.pornhub.com/users/someone"):
            with self.subTest(url=url):
                parsed = parse_source_url(url)
                self.assertEqual(parsed.provider, "pornhub")
                self.assertTrue(parsed.url.startswith("https://cn.pornhub.com/"))
                self.assertTrue(parsed.url.endswith("/videos"))

    def test_the_page_request_declares_chinese(self):
        """中文站的字面取决于请求语言，抓到中文分类的前提是这一跳带上了中文。"""
        requests = []
        body = _page([_card("6aad2df8d45f5", "First work")],
                     canonical="https://cn.pornhub.com/model/mini-diva/videos")
        connector = build_connector("pornhub", transport=_transport(body=body, record=requests))
        connector.fetch("mini-diva")
        list_request = requests[0]
        self.assertEqual(list_request.url, "https://cn.pornhub.com/model/mini-diva/videos")
        language = {key.lower(): value for key, value in list_request.headers.items()}
        self.assertTrue(language["accept-language"].startswith("zh-CN"))

    def test_a_tab_that_is_not_videos_is_refused(self):
        with self.assertRaises(Exception) as caught:
            parse_source_url("https://www.pornhub.com/model/mini-diva/photos")
        self.assertIn("只订阅视频页", str(caught.exception))

    def test_a_listing_keeps_the_view_key_duration_and_approximate_time(self):
        body = _page([_card("6aad2df8d45f5", "First work"),
                      _card("6856a120dcdae", "Second work", duration="7:34")],
                     canonical="https://www.pornhub.com/model/mini-diva/videos")
        connector = build_connector("pornhub", transport=_transport(body=body))
        fetch = connector.fetch("mini-diva")
        self.assertEqual([row.external_id for row in fetch.candidates],
                         ["6aad2df8d45f5", "6856a120dcdae"])
        first = fetch.candidates[0]
        self.assertEqual(first.title, "First work")
        self.assertEqual(first.duration, 904.0)
        self.assertEqual(first.thumb_url, "https://pix-cdn77.phncdn.com/videos/6aad2df8d45f5.jpg")
        self.assertEqual(first.extra["published_precision"], "approximate")
        self.assertIsNotNone(first.published_at)
        # 签名只有几小时的预览片不落库：它不会被当成媒体地址写进账本。
        self.assertIsNone(first.media_url)

    def test_the_last_page_ends_history_instead_of_failing(self):
        connector = build_connector("pornhub", transport=_transport(status=404, body=b"gone"))
        with self.assertRaises(FollowHistoryEnd):
            connector.fetch("mini-diva", page=2)

    def test_a_redirected_person_page_is_refetched_from_its_canonical_address(self):
        """`/model/` 对职业演员只列得出一小部分；canonical 说真实页型在 `/pornstar/`。"""
        requests = []
        bodies = [
            _page([_card("aaaaaaaaaaaa", "Only one")],
                  canonical="https://www.pornhub.com/pornstar/lana-rhoades"),
            _page([_card("bbbbbbbbbbbb", "One"),
                   _card("cccccccccccc", "Two")],
                  canonical="https://www.pornhub.com/pornstar/lana-rhoades/videos"),
        ]

        def transport(request, timeout, max_bytes):
            if "view_video.php" in request.url:
                return HttpResponse(200, {}, DETAIL)
            requests.append(request.url)
            return HttpResponse(200, {}, bodies[min(len(requests) - 1, 1)])

        connector = build_connector("pornhub", transport=transport)
        fetch = connector.fetch("lana-rhoades")
        self.assertEqual(requests[0], "https://cn.pornhub.com/model/lana-rhoades/videos")
        # 夹具的 canonical 落在 `www`：页型认出来之后地址仍然收在本站，主机不跟着走。
        self.assertEqual(requests[1], "https://cn.pornhub.com/pornstar/lana-rhoades/videos")
        self.assertEqual(len(fetch.candidates), 2)
        # 认过之后同一进程里不再多打一次模型页。
        again = connector.fetch("lana-rhoades")
        self.assertEqual(requests[2:], ["https://cn.pornhub.com/pornstar/lana-rhoades/videos"])
        self.assertEqual(len(again.candidates), 2)

    def test_the_detail_page_supplies_the_exact_time_categories_and_tags(self):
        listing = _page([_card("6aad2df8d45f5", "First work")],
                        canonical="https://www.pornhub.com/model/mini-diva/videos")

        def transport(request, timeout, max_bytes):
            body = DETAIL if "view_video.php" in request.url else listing
            return HttpResponse(200, {}, body)

        fetch = build_connector("pornhub", transport=transport).fetch("mini-diva")
        item = fetch.candidates[0]
        self.assertFalse(item.partial)
        self.assertEqual(item.published_at, "2026-09-18T12:47:42Z")
        self.assertEqual(item.extra["published_precision"], "exact")
        self.assertEqual(item.duration, 904.0)
        self.assertEqual(item.extra["author"], "Mini Diva")
        self.assertEqual(item.extra["categories"], ["Amateur", "HD Porn"])
        self.assertEqual(item.extra["tags"], ["Tag One", "Tag Two"])
        self.assertEqual(item.extra["tag_types"]["Tag One"], "general")
        self.assertEqual(item.extra["tag_types"]["Amateur"], "metadata")
        self.assertEqual(item.extra["tag_types"]["Mini Diva"], "artist")

    def test_a_chinese_detail_page_supplies_chinese_categories_and_tags(self):
        """中文站的分类与标签是中文；标签没有 `title`，字在 `<span>` 里。"""
        listing = _page([_card("6aad2df8d45f5", "First work")],
                        canonical="https://cn.pornhub.com/model/mini-diva/videos")

        def transport(request, timeout, max_bytes):
            body = CHINESE_DETAIL if "view_video.php" in request.url else listing
            return HttpResponse(200, {}, body)

        item = build_connector("pornhub", transport=transport).fetch("mini-diva").candidates[0]
        self.assertEqual(item.extra["categories"], ["18-25歲", "亚洲人"])
        self.assertEqual(item.extra["tags"], ["肛门性爱", "大屁股"])
        self.assertEqual(item.extra["tag_types"]["肛门性爱"], "general")
        self.assertEqual(item.extra["tag_types"]["亚洲人"], "metadata")

    def test_a_missing_detail_page_keeps_the_listing_row_and_retries_later(self):
        listing = _page([_card("6aad2df8d45f5", "First work")],
                        canonical="https://www.pornhub.com/model/mini-diva/videos")

        def transport(request, timeout, max_bytes):
            if "view_video.php" in request.url:
                return HttpResponse(404, {}, b"")
            return HttpResponse(200, {}, listing)

        fetch = build_connector("pornhub", transport=transport).fetch("mini-diva")
        item = fetch.candidates[0]
        self.assertTrue(item.partial)
        self.assertNotIn("tag_types", item.extra)
        self.assertEqual(item.extra["published_precision"], "approximate")

    def test_detail_probes_stop_at_the_budget(self):
        cards = [_card(f"vkey{index:09d}", f"Work {index}") for index in range(6)]
        listing = _page(cards, canonical="https://www.pornhub.com/model/mini-diva/videos")
        probes = []

        def transport(request, timeout, max_bytes):
            if "view_video.php" in request.url:
                probes.append(request.url)
                return HttpResponse(200, {}, DETAIL)
            return HttpResponse(200, {}, listing)

        connector = build_connector("pornhub", transport=transport, enrich_budget=2)
        fetch = connector.fetch("mini-diva")
        self.assertEqual(len(probes), 2)
        self.assertEqual(fetch.probed, 2)
        self.assertEqual(sum(1 for row in fetch.candidates if row.partial), 4)


class PornhubRegistrationTests(unittest.TestCase):
    def test_the_provider_is_registered_once_and_projects_everywhere(self):
        self.assertEqual(follow_providers.PROVIDERS["pornhub"].label, "Pornhub")
        self.assertEqual(follow_providers.PROVIDERS["pornhub"].source_url,
                         "https://cn.pornhub.com/model/{ref}/videos")
        self.assertEqual(follow_providers.hosts()["pornhub"], ("phncdn.com",))
        self.assertEqual(follow_providers.page_hosts()["pornhub"], ("pornhub.com",))
        self.assertEqual(follow_providers.provider_for_host("cn.pornhub.com"), "pornhub")
        self.assertIn("pornhub", follow_providers.backfill_providers())
        self.assertIn("pornhub", follow_assets.SOURCE_ICON_URLS)
        # 两条边界：公开页不需要凭据；它不是跨站合流里的主条目来源。
        self.assertEqual(follow_secrets.CREDENTIAL_GUIDE["pornhub"], {"requirement": "none"})

    def test_a_person_page_link_reaches_the_connector(self):
        parsed = parse_source_url("https://www.pornhub.com/model/mini-diva/videos")
        self.assertEqual((parsed.provider, parsed.ref), ("pornhub", "mini-diva"))
        self.assertEqual(parsed.semantics, "work")
        self.assertEqual(urllib.parse.urlsplit(parsed.url).hostname, "cn.pornhub.com")


if __name__ == "__main__":
    unittest.main()
