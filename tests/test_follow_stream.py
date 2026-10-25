import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from peach import http as peach_http
from peach.follow_stream import (
    MAX_PROXY_REDIRECTS, FollowMediaResolver, FollowMediaUnavailable,
    FollowProxyError, ResolvedFollowMedia, media_type, open_upstream,
    playlist_variants, pornhub_media_definitions, proxy_request_headers, proxyable,
    resolvable_at_play, rewrite_follow_playlist,
)
from peach.follow_secrets import Credential
from peach.http import HttpResponse


class FollowMediaResolverTests(unittest.TestCase):
    def test_rule34video_resolves_the_current_signed_url_and_caches_it(self):
        requests = []

        def transport(request, timeout, max_bytes):
            requests.append(request)
            return HttpResponse(200, {}, b"<script>video_url: 'https:\\/\\/rule34video.com\\/get_file\\/1.mp4\\/?v-acctoken=fresh'</script>")

        item = SimpleNamespace(
            id=7, provider="rule34video",
            url="https://rule34video.com/video/7/work/",
            media_url="https://rule34video.com/get_file/1.mp4/",
            metadata={},
        )
        resolver = FollowMediaResolver(transport)
        first = resolver.resolve(item)
        second = resolver.resolve(item)
        self.assertEqual(first, second)
        self.assertEqual(len(requests), 1)
        self.assertIn("v-acctoken=fresh", first.url)
        self.assertEqual(first.referer, item.url)

    def test_non_rule34_media_uses_the_recorded_source_without_a_network_probe(self):
        resolver = FollowMediaResolver(lambda *_args: self.fail("unexpected network probe"))
        item = SimpleNamespace(
            id=8, provider="kemono", url="https://kemono.cr/post/8",
            media_url="https://img.kemono.cr/data/8.mp4", metadata={},
        )
        self.assertEqual(resolver.resolve(item).url, item.media_url)

    def test_simpcity_images_on_any_public_image_host_are_proxied_without_a_cookie(self):
        """帖子里的图挂在几十家图站上，白名单追不上；这类媒体不带凭据，放行任意公网主机。"""
        resolver = FollowMediaResolver(lambda *_args: self.fail("unexpected network probe"))
        item = SimpleNamespace(
            id=11, provider="simpcity", url="https://simpcity.cr/threads/17401/post-1",
            media_url="https://jpg5.su/img/abc.jpg", metadata={},
        )
        resolved = resolver.resolve(item)
        self.assertEqual(resolved.url, item.media_url)
        self.assertIsNone(resolved.headers)
        self.assertEqual(resolved.allowed_hosts, ())
        self.assertTrue(resolved.public_hosts)
        for url in ("https://simp6.cuckcapital.cr/images4/b73b.png", "https://jpg5.su/img/abc.jpg",
                    "https://i.ibb.co/x/y.png", "https://images2.imgbox.com/ab/cd/ef.jpg"):
            self.assertTrue(proxyable("simpcity", url), url)
        # 放宽的是域名清单，不是安全边界：明文、IP 字面量、本机与局域网名字、带用户
        # 信息的地址照旧拒收。
        for url in ("http://jpg5.su/img/abc.jpg", "https://169.254.1.1/a.png", "https://[::1]/a.png",
                    "https://localhost/a.png", "https://peach.local/a.png", "https://router.lan/a.png",
                    "https://intranet/a.png", "https://user:pw@jpg5.su/a.png"):
            self.assertFalse(proxyable("simpcity", url), url)
        # 别的来源仍只认自己的白名单。
        self.assertFalse(proxyable("kemono", "https://jpg5.su/img/abc.jpg"))

    def test_public_host_media_never_carries_credentials(self):
        """公网模式成立的前提是这条媒体不带凭据，结构上就不允许两者同时出现。"""
        with self.assertRaises(ValueError):
            ResolvedFollowMedia("https://jpg5.su/a.jpg", headers={"Cookie": "x"},
                                public_hosts=True)

    def test_untrusted_or_credentialed_targets_are_rejected(self):
        resolver = FollowMediaResolver(lambda *_args: self.fail("unexpected network probe"))
        bad = SimpleNamespace(
            id=9, provider="kemono", url="https://kemono.cr/post/9",
            media_url="https://127.0.0.1/private.mp4", metadata={},
        )
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(bad)
        locked = SimpleNamespace(
            id=10, provider="f95zone", url="https://f95zone.to/post/10",
            media_url="https://f95zone.to/file.mp4",
            metadata={"media_needs_credential": True},
        )
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(locked)

    def test_fanbox_gallery_selects_an_image_by_index(self):
        item = SimpleNamespace(
            id=11, provider="fanbox", url="https://creator.fanbox.cc/posts/11",
            media_url=None, metadata={"media_items": [
                {"url": "https://downloads.fanbox.cc/one.jpg",
                 "resource_provider": "fanbox"},
                {"url": "https://downloads.fanbox.cc/two.jpg",
                 "resource_provider": "fanbox"},
            ]},
        )
        target = FollowMediaResolver(lambda *_args: None).resolve(item, 1)
        self.assertEqual(target.url, "https://downloads.fanbox.cc/two.jpg")

    def test_archive_attachment_list_resolves_only_on_the_items_own_site(self):
        """归档站帖子的附件清单按条目自己站点的主机白名单取，别的主机、别的来源一律拒收。"""
        item = SimpleNamespace(
            id=15, provider="pawchive", url="https://pawchive.pw/patreon/user/1/post/15",
            media_url="https://file.pawchive.pw/data/a/clip.mp4", metadata={"media_items": [
                {"url": "https://file.pawchive.pw/data/a/clip.mp4", "resource_provider": "pawchive"},
                {"url": "https://file.pawchive.pw/data/a/page2.jpg", "resource_provider": "pawchive"},
                {"url": "https://127.0.0.1/data/a/private.jpg", "resource_provider": "pawchive"},
                {"url": "https://kemono.cr/data/a/page3.jpg", "resource_provider": "kemono"},
            ]},
        )
        resolver = FollowMediaResolver(lambda *_args: None)
        target = resolver.resolve(item, 1)
        self.assertEqual(target.url, "https://file.pawchive.pw/data/a/page2.jpg")
        self.assertEqual(target.referer, item.url)
        for index in (2, 3):
            with self.assertRaises(FollowMediaUnavailable):
                resolver.resolve(item, index)

    def test_legacy_f95_attachment_is_resolved_as_an_image(self):
        item = SimpleNamespace(
            id=13, provider="f95zone", url="https://f95zone.to/threads/1/post-13",
            media_url="https://pixeldrain.com/l/files", metadata={"attachments": [
                "https://attachments.f95zone.to/2026/08/one.jpg",
            ]},
        )
        target = FollowMediaResolver(lambda *_args: None).resolve(item, 0)
        self.assertEqual(target.url,
                         "https://attachments.f95zone.to/2026/08/one.jpg")
        self.assertEqual(target.referer, item.url)

    def test_f95_attachment_stays_available_while_other_links_need_a_session(self):
        item = SimpleNamespace(
            id=14, provider="f95zone", url="https://f95zone.to/threads/1/post-14",
            media_url="https://f95zone.to/masked/gofile.io/1/locked",
            metadata={"media_needs_credential": True, "attachments": [
                "https://attachments.f95zone.to/2026/08/one.jpg",
            ]},
        )
        resolver = FollowMediaResolver(lambda *_args: None).with_credential_loader(
            lambda provider: Credential(provider, {"cookie": "xf_session=saved"}))
        target = resolver.resolve(item, 0)
        self.assertEqual(target.url,
                         "https://attachments.f95zone.to/2026/08/one.jpg")
        self.assertEqual(target.headers, {"Cookie": "xf_session=saved"})

    def test_gofile_media_keeps_the_token_in_the_proxy_header(self):
        item = SimpleNamespace(
            id=12, provider="fanbox", url="https://creator.fanbox.cc/posts/12",
            media_url=None, metadata={"media_items": [
                {"url": "https://store1.gofile.io/download/one.mp4",
                 "resource_provider": "gofile"},
            ]},
        )
        resolver = FollowMediaResolver(lambda *_args: None).with_credential_loader(
            lambda provider: Credential(provider, {"api_token": "secret"}))
        target = resolver.resolve(item, 0)
        self.assertEqual(target.headers, {"Authorization": "Bearer secret"})
        self.assertNotIn("secret", target.url)

    def test_the_resolution_cache_stays_bounded(self):
        """只有 rule34video 会进这个缓存，一次列表几百条；只按 TTL 过期的话，
        进程活多久它就长多久。"""
        def transport(request, timeout, max_bytes):
            return HttpResponse(200, {}, b"<script>video_url: "
                                b"'https://rule34video.com/get_file/1_720p.mp4/'</script>")

        resolver = FollowMediaResolver(transport, max_cache_entries=2)
        for identifier in range(6):
            resolver.resolve(SimpleNamespace(
                id=identifier, provider="rule34video",
                url=f"https://rule34video.com/video/{identifier}/x/",
                media_url=None, metadata={}))
        self.assertEqual(len(resolver._cache), 2)


class Rule34VideoUpstreamDedupeTests(unittest.TestCase):
    """播放器起播时同时问正片和清晰度，之后每个 Range 再问一遍；这些都只该换来一次详情页。"""

    PAGE = b"<script>video_url: 'https://rule34video.com/get_file/1_720p.mp4/'</script>"

    @staticmethod
    def _item(identifier=7):
        return SimpleNamespace(id=identifier, provider="rule34video",
                               url=f"https://rule34video.com/video/{identifier}/x/",
                               media_url=None, metadata={})

    def test_concurrent_requests_for_one_item_share_a_single_detail_fetch(self):
        requests = []
        entered = threading.Event()
        release = threading.Event()

        def transport(request, timeout, max_bytes):
            requests.append(request.url)
            entered.set()
            release.wait(5)
            return HttpResponse(200, {}, self.PAGE)

        resolver = FollowMediaResolver(transport)
        results = []
        workers = [threading.Thread(target=lambda: results.append(resolver.resolve(self._item())))
                   for _ in range(3)]
        workers[0].start()
        self.assertTrue(entered.wait(5))
        for worker in workers[1:]:
            worker.start()
        release.set()
        for worker in workers:
            worker.join(5)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len({result.url for result in results}), 1)
        self.assertEqual(len(results), 3)

    def test_a_missing_detail_page_is_not_refetched_within_the_ttl(self):
        clock = [1000.0]
        transport = mock.Mock(return_value=HttpResponse(404, {}, b""))
        resolver = FollowMediaResolver(transport, clock=lambda: clock[0])
        for _ in range(3):
            with self.assertRaises(FollowMediaUnavailable):
                resolver.resolve(self._item())
        self.assertEqual(transport.call_count, 1)
        clock[0] += resolver.MISS_TTL + 1
        transport.return_value = HttpResponse(200, {}, self.PAGE)
        self.assertIn("1_720p", resolver.resolve(self._item()).url)
        self.assertEqual(transport.call_count, 2)

    def test_a_rate_limited_source_pauses_every_item_until_retry_after(self):
        clock = [1000.0]
        transport = mock.Mock(return_value=HttpResponse(429, {"retry-after": "90"}, b""))
        resolver = FollowMediaResolver(transport, clock=lambda: clock[0])
        with self.assertLogs("peach.follow_stream", "WARNING"):
            with self.assertRaises(FollowMediaUnavailable):
                resolver.resolve(self._item(7))
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(self._item(8))
        self.assertEqual(transport.call_count, 1)
        self.assertGreater(resolver.cooling("rule34video"), 0)
        clock[0] += 91
        self.assertEqual(resolver.cooling("rule34video"), 0)
        transport.return_value = HttpResponse(200, {}, self.PAGE)
        self.assertIn("1_720p", resolver.resolve(self._item(8)).url)
        self.assertEqual(transport.call_count, 2)

    def test_retry_after_is_capped_and_missing_retry_after_uses_the_default(self):
        clock = [0.0]
        resolver = FollowMediaResolver(
            mock.Mock(return_value=HttpResponse(403, {"Retry-After": "999999"}, b"")),
            clock=lambda: clock[0])
        with self.assertLogs("peach.follow_stream", "WARNING"), self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(self._item(7))
        self.assertEqual(resolver.cooling("rule34video"), resolver.MAX_COOLDOWN)
        resolver = FollowMediaResolver(mock.Mock(return_value=HttpResponse(429, {}, b"")),
                                       clock=lambda: clock[0])
        with self.assertLogs("peach.follow_stream", "WARNING"), self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(self._item(7))
        self.assertEqual(resolver.cooling("rule34video"), resolver.COOLDOWN)


class Rule34VideoQualityTests(unittest.TestCase):
    """rule34video 把每档清晰度写成独立字段，只解析 video_url 会永远播最低那档。

    2026-08-31 实测 video/4564733 的页面给出四档：

        video_url      -> 4564733_360.mp4
        video_alt_url  -> 4564733_480p.mp4
        video_alt_url2 -> 4564733_720p.mp4
        video_alt_url3 -> 4564733_1080p.mp4
    """

    PAGE = (b"<script>"
            b"video_url: 'https://rule34video.com/get_file/51/a/1/1_360.mp4/?v-acctoken=t0';"
            b"video_alt_url: 'https://rule34video.com/get_file/51/a/1/1_480p.mp4/?v-acctoken=t1';"
            b"video_alt_url2: 'https://rule34video.com/get_file/51/a/1/1_720p.mp4/?v-acctoken=t2';"
            b"video_alt_url3: 'https://rule34video.com/get_file/51/a/1/1_1080p.mp4/?v-acctoken=t3';"
            b"</script>")

    def _resolver(self, body=None):
        def transport(request, timeout, max_bytes):
            return HttpResponse(200, {}, self.PAGE if body is None else body)
        return FollowMediaResolver(transport)

    def _item(self):
        return SimpleNamespace(
            id=11, provider="rule34video",
            url="https://rule34video.com/video/11/x/",
            media_url="https://rule34video.com/get_file/1.mp4/",
            metadata={},
        )

    def test_every_quality_is_parsed_and_the_highest_plays_by_default(self):
        resolved = self._resolver().resolve(self._item())
        self.assertEqual([height for height, _ in resolved.qualities],
                         [1080, 720, 480, 360])
        self.assertIn("1_1080p.mp4", resolved.url,
                      "默认应当是最高一档，旧实现固定播 360")

    def test_a_requested_height_is_honoured(self):
        resolved = self._resolver().resolve(self._item(), None, 480)
        self.assertIn("1_480p.mp4", resolved.url)

    def test_an_unavailable_height_falls_back_instead_of_failing(self):
        """签名 URL 会过期、来源也会改版；选档失败不该中断播放。"""
        resolved = self._resolver().resolve(self._item(), None, 4320)
        self.assertIn("1_1080p.mp4", resolved.url)

    def test_other_providers_report_no_quality_choices(self):
        item = SimpleNamespace(
            id=12, provider="pawchive",
            url="https://pawchive.pw/x",
            media_url="https://pawchive.pw/data/a/b/c.mp4",
            metadata={},
        )
        resolved = self._resolver(body=b"").resolve(item)
        self.assertEqual(resolved.qualities, (),
                         "只有 rule34video 给多档，其余来源只显示原画")


class ProxyUpstreamTests(unittest.TestCase):
    """`/follow-stream` 的代理边界：跟哪些跳、转发哪些头、什么时候干脆不转发。

    生产用的那个 client 开着 `follow_redirects=True`，所以这里的替身也开着：
    要证明的正是「按跳自己判」而不是「靠 client 的默认值」。
    """

    def _client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler),
                              follow_redirects=True)
        self.addCleanup(client.close)
        return client

    def test_a_redirect_out_of_the_whitelist_never_carries_the_credential(self):
        """上游只要回一个指向别处的 Location，httpx 就会把 Cookie 一路带过去。"""
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(302, request=request, headers={
                "location": "https://collector.example/steal.jpg"})

        target = ResolvedFollowMedia(
            "https://attachments.f95zone.to/2026/08/one.jpg",
            "https://f95zone.to/threads/1/post-1",
            {"Cookie": "xf_session=fixture"},
            allowed_hosts=("attachments.f95zone.to",))
        with self.assertRaises(FollowProxyError):
            open_upstream(self._client(handler), "GET", target, incoming={})
        self.assertEqual([str(request.url) for request in seen],
                         ["https://attachments.f95zone.to/2026/08/one.jpg"],
                         "出界的那一跳一次都不能发出去")
        self.assertEqual(seen[0].headers["cookie"], "xf_session=fixture",
                         "第一跳确实带着会话，所以拦住的是真的泄露")

    def test_a_redirect_inside_the_source_domain_is_still_followed(self):
        """归档站主域会 302 到取文件的节点（实测 kemono → n1.），必须跟得上。"""

        def handler(request):
            if request.url.host == "kemono.cr":
                return httpx.Response(302, request=request, headers={
                    "location": "https://n1.kemono.cr/data/7.mp4"})
            return httpx.Response(200, request=request,
                                  stream=httpx.ByteStream(b"frames"),
                                  headers={"content-type": "video/mp4"})

        target = ResolvedFollowMedia("https://kemono.cr/data/7.mp4",
                                     allowed_hosts=("kemono.cr",))
        upstream = open_upstream(self._client(handler), "GET", target, incoming={})
        self.addCleanup(upstream.close)
        self.assertEqual(upstream.read(), b"frames")

    def test_rule34video_is_followed_out_to_its_cdn(self):
        """rule34video 的 `/get_file/…` 只是签名入口，正片在 `*.boomio-cdn.com`。

        这一跳没有凭据可泄露（这条媒体不带 `headers`），拦住它的唯一后果是每条
        视频都停在 502。CDN 后缀登记在 `follow_providers` 的 `hosts` 里，代理照样
        逐跳校验。
        """

        def handler(request):
            if request.url.host == "rule34video.com":
                return httpx.Response(302, request=request, headers={
                    "location": "https://eu-cdn05-prem.boomio-cdn.com/x.mp4"})
            return httpx.Response(206, request=request,
                                  stream=httpx.ByteStream(b"frames"),
                                  headers={"content-type": "video/mp4"})

        target = ResolvedFollowMedia(
            "https://rule34video.com/get_file/1/1080p.mp4/?v-acctoken=fresh",
            "https://rule34video.com/video/1/work/",
            allowed_hosts=("rule34video.com", "boomio-cdn.com"))
        upstream = open_upstream(self._client(handler), "GET", target, incoming={})
        self.addCleanup(upstream.close)
        self.assertEqual(upstream.read(), b"frames")

    def test_an_upstream_error_page_is_refused_instead_of_forwarded(self):
        """403 页面、限流提示、错误 JSON 转发给播放器没有用处，只会把上游的
        主机名和提示语抄给浏览器。"""

        def handler(request):
            return httpx.Response(403, request=request,
                                  content=b"<html>blocked by upstream shield</html>")

        target = ResolvedFollowMedia("https://kemono.cr/data/7.mp4",
                                     allowed_hosts=("kemono.cr",))
        with self.assertRaises(FollowProxyError) as raised:
            open_upstream(self._client(handler), "GET", target, incoming={})
        self.assertIn("403", str(raised.exception))
        self.assertNotIn("shield", str(raised.exception))

    def test_a_redirect_loop_is_bounded(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(302, request=request, headers={
                "location": "https://kemono.cr/data/next.mp4"})

        target = ResolvedFollowMedia("https://kemono.cr/data/7.mp4",
                                     allowed_hosts=("kemono.cr",))
        with self.assertRaises(FollowProxyError):
            open_upstream(self._client(handler), "GET", target, incoming={})
        self.assertEqual(len(seen), MAX_PROXY_REDIRECTS + 1)

    def test_media_without_a_whitelist_is_not_proxied_at_all(self):
        target = ResolvedFollowMedia("https://kemono.cr/data/7.mp4")
        with self.assertRaises(FollowProxyError):
            open_upstream(
                self._client(lambda request: self.fail("不该发出请求")),
                "GET", target, incoming={})

    def test_public_host_media_follows_redirects_across_public_hosts(self):
        """图站常把原图 302 到自己的 CDN，公网模式下这一跳要跟得上。"""
        def handler(request):
            if request.url.host == "jpg5.su":
                return httpx.Response(302, request=request, headers={
                    "location": "https://cdn.jpg5cdn.example/a.jpg"})
            return httpx.Response(200, request=request,
                                  stream=httpx.ByteStream(b"pixels"),
                                  headers={"content-type": "image/jpeg"})

        target = ResolvedFollowMedia("https://jpg5.su/a.jpg", public_hosts=True)
        with mock.patch.object(peach_http, "host_addresses", return_value=("93.184.216.34",)):
            upstream = open_upstream(self._client(handler), "GET", target, incoming={})
        self.addCleanup(upstream.close)
        self.assertEqual(upstream.read(), b"pixels")
        self.assertNotIn("cookie", upstream.request.headers)

    def test_public_host_media_never_reaches_into_the_lan(self):
        """域名字面上像公网但解析到内网，或者上游把我们指到内网地址：一次都不发。"""
        seen = []

        def to_lan(request):
            seen.append(request)
            return httpx.Response(302, request=request, headers={
                "location": "https://169.254.169.254/admin.png"})

        target = ResolvedFollowMedia("https://jpg5.su/a.jpg", public_hosts=True)
        with mock.patch.object(peach_http, "host_addresses", return_value=("93.184.216.34",)):
            with self.assertRaises(FollowProxyError):
                open_upstream(self._client(to_lan), "GET", target, incoming={})
        self.assertEqual([str(request.url) for request in seen], ["https://jpg5.su/a.jpg"])
        for addresses in ((), ("127.0.0.1",), ("93.184.216.34", "fd00::1")):
            with mock.patch.object(peach_http, "host_addresses", return_value=addresses):
                with self.assertRaises(FollowProxyError, msg=str(addresses)):
                    open_upstream(self._client(lambda request: self.fail("不该发出请求")),
                                  "GET", target, incoming={})

    def test_only_range_headers_come_in_and_only_media_headers_go_back(self):
        target = ResolvedFollowMedia(
            "https://kemono.cr/data/7.mp4", "https://kemono.cr/post/7",
            {"Cookie": "source_session=fixture"}, allowed_hosts=("kemono.cr",))
        headers = proxy_request_headers(target, {
            "range": "bytes=0-1", "accept": "video/*",
            # 浏览器发给 Peach 的凭据不能跟着请求送到上游去。
            "cookie": "peach=token", "authorization": "Bearer peach",
        })
        self.assertEqual(headers["Range"], "bytes=0-1")
        self.assertEqual(headers["Accept"], "video/*")
        self.assertEqual(headers["Referer"], "https://kemono.cr/post/7")
        self.assertEqual(headers["Cookie"], "source_session=fixture")
        self.assertNotIn("Authorization", headers)


PORNHUB_WATCH_PAGE = b"""<html><body><script>
VIDEO_SHOW = {
  "mediaDefinitions": [
    {"format": "hls", "height": 1080, "width": 1920, "quality": "1080",
     "defaultQuality": false, "videoUrl": "https://ev-h.phncdn.com/hls/1080/master.m3u8?validfrom=1&validto=2"},
    {"format": "hls", "height": 720, "width": 1280, "quality": "720",
     "defaultQuality": true, "videoUrl": "https://ev-h.phncdn.com/hls/720/master.m3u8?validfrom=1&validto=2"},
    {"format": "hls", "height": 480, "width": 852, "quality": "480",
     "defaultQuality": false, "videoUrl": "https://ev-h.phncdn.com/hls/480/master.m3u8?validfrom=1&validto=2"},
    {"format": "mp4", "remote": true, "quality": [],
     "videoUrl": "https://www.pornhub.com/video/get_media?s=opaque&v=6aad2df8d45f5"}
  ]
};
</script></body></html>"""

PORNHUB_MASTER = b"""#EXTM3U
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=2559320,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
index-v1-a1.m3u8
"""

#: 只剩一个 `?h=` 哈希的那种清单地址：作品页会给，取回来一律 410。
PORNHUB_DEAD_PAGE = PORNHUB_WATCH_PAGE.replace(b"?validfrom=1&validto=2", b"?h=hash")


def _pornhub_item(item_id: int = 7, metadata=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id, provider="pornhub",
        url="https://www.pornhub.com/view_video.php?viewkey=6aad2df8d45f5",
        media_url=None, metadata=metadata or {"media_kind": "video"},
    )


def _pornhub_transport(pages=PORNHUB_WATCH_PAGE, *, dead_playlists: bool = False):
    """作品页按顺序发，清单按能不能取回发；`pages` 最后一份会一直用下去。"""
    pages = list(pages) if isinstance(pages, list) else [pages]
    seen: list[str] = []

    def transport(request, timeout, max_bytes):
        seen.append(request.url)
        if ".m3u8" in request.url:
            if dead_playlists or "?h=" in request.url:
                return HttpResponse(410, {}, b"")
            return HttpResponse(200, {}, PORNHUB_MASTER)
        return HttpResponse(200, {}, pages.pop(0) if len(pages) > 1 else pages[0])

    transport.seen = seen
    return transport


class PornhubPlaybackTests(unittest.TestCase):
    """只有 HLS 的来源：正片在按下播放时才解析，分片经 Peach 的键取回。"""

    def test_the_watch_page_yields_a_playlist_with_every_quality(self):
        resolver = FollowMediaResolver(_pornhub_transport())
        resolved = resolver.resolve(_pornhub_item())
        self.assertTrue(resolved.playlist)
        self.assertEqual([height for height, _ in resolved.qualities], [1080, 720, 480])
        self.assertEqual(resolved.url, resolved.qualities[0][1])
        self.assertEqual(resolved.allowed_hosts, ("phncdn.com",))
        self.assertEqual(resolved.referer, "https://www.pornhub.com/view_video.php?viewkey=6aad2df8d45f5")
        # `format=mp4` 那条指向一个匿名取不到内容的接口，不当作可播地址。
        self.assertNotIn("get_media", resolved.url)

    def test_a_requested_height_switches_playlist_without_refetching(self):
        transport = _pornhub_transport()

        resolver = FollowMediaResolver(transport)
        resolver.resolve(_pornhub_item())
        pages = len([url for url in transport.seen if "view_video" in url])
        chosen = resolver.resolve(_pornhub_item(), height=480)
        self.assertIn("/hls/480/", chosen.url)
        self.assertEqual(len([url for url in transport.seen if "view_video" in url]), pages)

    def test_a_dead_hash_playlist_comes_back_playable_from_the_same_round(self):
        """第一页给的清单取回 410：同一轮里取的其它页本来就换了参数，仍能给出可播那档。"""
        transport = _pornhub_transport([PORNHUB_DEAD_PAGE, PORNHUB_WATCH_PAGE])
        resolved = FollowMediaResolver(transport).resolve(_pornhub_item())
        self.assertEqual(resolved.url,
                         "https://ev-h.phncdn.com/hls/1080/master.m3u8?validfrom=1&validto=2")
        pages = [url for url in transport.seen if "view_video" in url]
        self.assertEqual(len(pages), FollowMediaResolver.PORNHUB_PARALLEL)
        # 同一轮的几次取页各带一个没用过的参数：边缘缓存里的那一份才会被绕开。
        self.assertEqual(len(set(pages)), len(pages))

    def test_a_dead_highest_level_falls_back_to_the_next_one_down(self):
        """最高那档偶尔单独失效：它不能留在清单里，否则播放器升上去就断。"""
        page = PORNHUB_WATCH_PAGE.replace(b"/hls/1080/master.m3u8?validfrom=1&validto=2",
                                          b"/hls/1080/master.m3u8?h=hash")

        def transport(request, timeout, max_bytes):
            if ".m3u8" in request.url:
                return HttpResponse(410 if "/hls/1080/" in request.url else 200, {},
                                    b"" if "/hls/1080/" in request.url else PORNHUB_MASTER)
            return HttpResponse(200, {}, page)

        resolved = FollowMediaResolver(transport).resolve(_pornhub_item())
        self.assertIn("/hls/720/", resolved.url)
        self.assertEqual([height for height, _ in resolved.qualities], [720, 480])

    def test_a_page_that_only_offers_dead_playlists_reports_unavailable(self):
        transport = _pornhub_transport(PORNHUB_DEAD_PAGE)
        with self.assertRaises(FollowMediaUnavailable):
            FollowMediaResolver(transport).resolve(_pornhub_item())
        pages = [url for url in transport.seen if "view_video" in url]
        self.assertEqual(len(pages), FollowMediaResolver.PORNHUB_ATTEMPTS)

    def test_playlist_children_become_peach_keys_and_stay_reachable(self):
        resolver = FollowMediaResolver(_pornhub_transport())
        resolved = resolver.resolve(_pornhub_item())
        rewritten = rewrite_follow_playlist(
            PORNHUB_MASTER, "https://ev-h.phncdn.com/hls/720/master.m3u8?validto=2",
            item_id=7, root=resolved.url, provider="pornhub", resolver=resolver).decode()
        children = [line for line in rewritten.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].startswith("/follow-hls?id=7&r="))
        self.assertNotIn("phncdn", rewritten)
        key = children[0].split("r=")[1]
        # 相对地址按 URL 语义解析：基础地址上的查询串不会跟到子清单上。
        self.assertEqual(resolver.resource(7, key),
                         "https://ev-h.phncdn.com/hls/720/index-v1-a1.m3u8")

    def test_a_playlist_child_outside_the_source_hosts_is_refused(self):
        resolver = FollowMediaResolver(_pornhub_transport())
        resolved = resolver.resolve(_pornhub_item())
        body = b"#EXTM3U\n#EXTINF:1,\nhttps://elsewhere.example/seg.ts\n"
        with self.assertRaises(FollowProxyError):
            rewrite_follow_playlist(body, resolved.url, item_id=7, root=resolved.url,
                                    provider="pornhub", resolver=resolver)

    def test_a_key_from_another_item_is_not_readable(self):
        resolver = FollowMediaResolver(_pornhub_transport())
        resolved = resolver.resolve(_pornhub_item(7))
        key = resolver.register_resource(7, resolved.url, "https://ev-h.phncdn.com/hls/720/x.m3u8")
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resource(8, key)

    def test_media_definitions_are_read_out_of_the_escaped_script(self):
        escaped = PORNHUB_WATCH_PAGE.replace(b'"', b'\\"')
        entries = pornhub_media_definitions(escaped.decode("utf-8"))
        self.assertEqual([row["height"] for row in entries], [1080, 720, 480])
        self.assertTrue(all(row["format"] == "hls" for row in entries))

    def test_a_page_without_definitions_resolves_to_nothing(self):
        self.assertEqual(pornhub_media_definitions("<html></html>"), ())
        resolver = FollowMediaResolver(lambda *_args: HttpResponse(200, {}, b"<html></html>"))
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(_pornhub_item())

    def test_playability_of_hls_sources_follows_the_page_url(self):
        self.assertTrue(resolvable_at_play("pornhub", "https://cn.pornhub.com/model/x/videos"))
        self.assertFalse(resolvable_at_play("pornhub", "https://example.com/model/x"))
        self.assertFalse(resolvable_at_play("kemono", "https://kemono.cr/fanbox/user/1"))
        self.assertEqual(media_type("pornhub", None), "application/vnd.apple.mpegurl")
        self.assertEqual(media_type("kemono", "https://img.kemono.cr/a.webm"), "video/webm")


#: 签名还长着的那一版作品页：落盘复用的用例要一个真在有效期内的地址。
PORNHUB_LONG_LIVED = PORNHUB_WATCH_PAGE.replace(b"validfrom=1&validto=2",
                                                b"validfrom=1700000000&validto=4102444800")


class MergedMasterTests(unittest.TestCase):
    """Pornhub 是一档一份母清单：并成一份多档母清单，播放器才有得选。"""

    def _resolve(self, transport=None):
        transport = transport or _pornhub_transport()
        resolver = FollowMediaResolver(transport)
        item = _pornhub_item()
        return resolver, item, resolver.resolve(item)

    def test_every_level_brings_its_own_line_and_its_own_child(self):
        resolver, item, resolved = self._resolve()
        merged = resolver.levels_master(item, resolved, PORNHUB_MASTER, resolved.url)
        entries = playlist_variants(merged.decode("utf-8"))
        self.assertEqual([url for _attributes, url in entries],
                         ["https://ev-h.phncdn.com/hls/1080/index-v1-a1.m3u8",
                          "https://ev-h.phncdn.com/hls/720/index-v1-a1.m3u8",
                          "https://ev-h.phncdn.com/hls/480/index-v1-a1.m3u8"])
        # 带宽、分辨率、编解码都是那一档自己的那行，原样带走。
        self.assertTrue(all(attributes.startswith("#EXT-X-STREAM-INF:")
                            and "CODECS=" in attributes for attributes, _ in entries))

    def test_the_merged_master_is_kept_for_the_playlist_period(self):
        transport = _pornhub_transport()
        resolver, item, resolved = self._resolve(transport)
        resolver.levels_master(item, resolved, PORNHUB_MASTER, resolved.url)
        after_first = len(transport.seen)
        resolver.levels_master(item, resolved, PORNHUB_MASTER, resolved.url)
        self.assertEqual(len(transport.seen), after_first)

    def test_a_source_with_a_single_level_keeps_the_playlist_it_gave(self):
        """本来就一档：没有可并的，交给播放器的仍是来源那份清单。"""
        page = (b'<html><body><script>VIDEO_SHOW = {"mediaDefinitions": ['
                b'{"format": "hls", "height": 720, "videoUrl":'
                b' "https://ev-h.phncdn.com/hls/720/master.m3u8?validfrom=1&validto=2"}]};'
                b"</script></body></html>")
        resolver, item, resolved = self._resolve(_pornhub_transport(page))
        self.assertIsNone(
            resolver.levels_master(item, resolved, PORNHUB_MASTER, resolved.url))

    def test_a_level_that_is_not_a_master_leaves_nothing_to_merge(self):
        def transport(request, timeout, max_bytes):
            if ".m3u8" in request.url:
                if "/hls/1080/" in request.url:
                    return HttpResponse(410, {}, b"")
                return HttpResponse(200, {}, b"#EXTM3U\n")
            return HttpResponse(200, {}, PORNHUB_WATCH_PAGE)

        resolver, item, resolved = self._resolve(transport)
        # 最高的 1080 取不回，剩下的 720 能播但它给的不是母清单。
        self.assertIn("/hls/720/", resolved.url)
        self.assertIsNone(
            resolver.levels_master(item, resolved, PORNHUB_MASTER, resolved.url))


class PlaybackPersistenceTests(unittest.TestCase):
    """已解析的清单地址写进状态目录：重启或缓存淘汰之后重复打开不必再回源。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _entries(self):
        return sorted((self.root / FollowMediaResolver.PLAYBACK_DIR).glob("*.json"))

    def test_a_resolved_playlist_is_reused_by_the_next_process(self):
        transport = _pornhub_transport(PORNHUB_LONG_LIVED)
        item = _pornhub_item()
        FollowMediaResolver(transport, state_root=self.root).resolve(item)
        self.assertEqual(len(self._entries()), 1)
        self.assertEqual(self._entries()[0].stat().st_mode & 0o777, 0o600)

        fresh = _pornhub_transport(PORNHUB_LONG_LIVED)
        again = FollowMediaResolver(fresh, state_root=self.root).resolve(item)
        self.assertEqual(fresh.seen, [])
        self.assertTrue(again.playlist)
        self.assertEqual(again.url, "https://ev-h.phncdn.com/hls/1080/master.m3u8"
                                    "?validfrom=1700000000&validto=4102444800")
        self.assertEqual([height for height, _ in again.qualities], [1080, 720, 480])
        self.assertEqual(again.allowed_hosts, ("phncdn.com",))
        self.assertEqual(again.referer,
                         "https://www.pornhub.com/view_video.php?viewkey=6aad2df8d45f5")

    def test_an_expired_address_is_not_reused(self):
        FollowMediaResolver(_pornhub_transport(PORNHUB_LONG_LIVED),
                            state_root=self.root).resolve(_pornhub_item())
        path = self._entries()[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["expiry"] = 1
        path.write_text(json.dumps(payload), encoding="utf-8")

        transport = _pornhub_transport(PORNHUB_LONG_LIVED)
        FollowMediaResolver(transport, state_root=self.root).resolve(_pornhub_item())
        self.assertTrue([url for url in transport.seen if "view_video" in url])

    def test_an_expired_address_is_dropped_from_disk(self):
        """过期那份留着也取不回：重解析失败时它也不该继续躺在盘上。"""
        FollowMediaResolver(_pornhub_transport(PORNHUB_LONG_LIVED),
                            state_root=self.root).resolve(_pornhub_item())
        path = self._entries()[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["expiry"] = 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        resolver = FollowMediaResolver(lambda *_args: HttpResponse(200, {}, b"<html></html>"),
                                       state_root=self.root)
        with self.assertRaises(FollowMediaUnavailable):
            resolver.resolve(_pornhub_item())
        self.assertEqual(self._entries(), [])

    def test_entries_nobody_opens_are_swept_after_a_week(self):
        FollowMediaResolver(_pornhub_transport(PORNHUB_LONG_LIVED),
                            state_root=self.root).resolve(_pornhub_item())
        stale = self._entries()[0]
        old = time.time() - FollowMediaResolver.PLAYBACK_RETENTION - 60
        os.utime(stale, (old, old))
        # 再写一条，顺带触发清扫。
        FollowMediaResolver(_pornhub_transport(PORNHUB_LONG_LIVED),
                            state_root=self.root).resolve(_pornhub_item(8))
        self.assertEqual([path.name for path in self._entries()], ["8.json"])

    def test_an_address_that_expires_too_soon_is_not_written_down(self):
        """`validto` 已经过去了的那种：写下来也没有一刻用得上。"""
        FollowMediaResolver(_pornhub_transport(), state_root=self.root).resolve(_pornhub_item())
        self.assertEqual(self._entries(), [])

    def test_forgetting_drops_both_copies_and_says_whether_it_did(self):
        transport = _pornhub_transport(PORNHUB_LONG_LIVED)
        resolver = FollowMediaResolver(transport, state_root=self.root)
        item = _pornhub_item()
        resolver.resolve(item)
        self.assertTrue(resolver.forget(item.id))
        self.assertEqual(self._entries(), [])
        self.assertFalse(resolver.forget(item.id))
        # 丢掉之后这一条重新回源，而不是拿内存里那份接着播。
        pages = len([url for url in transport.seen if "view_video" in url])
        resolver.resolve(item)
        self.assertGreater(len([url for url in transport.seen if "view_video" in url]), pages)

