"""关注条目的可播地址解析与媒体代理。

浏览器只拿到 Peach 的地址，上游地址留在账本与来源层；签名地址在用户按下播放时才解析，
过期也不会被抄进账本。

两种形态：

- 渐进式媒体（归档站、标签站、附件）：解析出一条直链，代理按 Range 转发。
- 只有 HLS 的来源（Pornhub）：清单里没有可播的渐进式 mp4。播放器拿到一份 Peach 改写过的
  `m3u8`，每一跳再换成分片键回来取（`rewrite_follow_playlist`）。改写复用 STRM 那条路径
  的同一份实现（`preview_cache.rewrite_hls`），区别只在键表挂在本模块的解析器内存里。
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, Mapping

import httpx

from . import follow_providers
from .follow_sources import USER_AGENT, archive_file_url, f95_attachment_media_items
from .follow_secrets import Credential
from .follow_store import FollowItemRow
from .http import HttpRequest, HttpResponse, HttpTransport, public_https_url, resolves_publicly

LOGGER = logging.getLogger(__name__)

class FollowMediaUnavailable(RuntimeError):
    pass


class FollowProxyError(RuntimeError):
    """上游存在但这一次代理不成立：状态码不对、重定向出界或传输失败。

    与 `FollowMediaUnavailable` 分开是因为两者的对外语义不同：条目本身没有可播
    媒体是 404，上游这一次没给出可转发的响应是 502。
    """


@dataclass(frozen=True)
class ResolvedFollowMedia:
    url: str
    referer: str | None = None
    headers: Mapping[str, str] | None = None
    #: (高度, URL) 从高到低。只有 rule34video 给多档；其余来源留空，
    #: 播放器据此只显示「原画」。
    qualities: tuple[tuple[int, str], ...] = ()
    #: 这条媒体允许落到哪些主机后缀。解析时已经按它校验过 `url`，代理层跟重定向
    #: 时还要再按它校验每一跳：`headers` 里可能带 Cookie / Bearer token，一旦上游
    #: 把我们指到别的主机，那些凭据就跟着送出去了。空表示不允许代理。
    allowed_hosts: tuple[str, ...] = ()
    #: 允许落到任意公网主机。给帖子里贴的第三方图床用：图站有几十家，白名单追不上；
    #: 这类媒体不带凭据取，跳到哪个公网主机都泄露不了什么，所以它与 `headers` 互斥。
    #: 边界由 `http.public_https_url` 与 `http.resolves_publicly` 守：明文、IP 字面量、
    #: 本机与局域网名字、解析到内网地址的主机仍然拒收。
    public_hosts: bool = False
    #: `url` 是一份 HLS 清单而不是可整段转发的字节流。代理层据此改走清单改写，
    #: 不能把 `#EXTM3U` 当视频发出去。
    playlist: bool = False

    def __post_init__(self) -> None:
        if self.public_hosts and self.headers:
            raise ValueError("带凭据的媒体不能放行任意公网主机")


#: 媒体代理允许的主机，投影自 follow_providers；不在表里的 provider 一律拒绝。
_PROVIDER_HOSTS = follow_providers.hosts()
#: 作品页主机，同样是投影。媒体白名单与页面主机是两份，判「能不能去取详情」用这一份。
_PROVIDER_PAGE_HOSTS = follow_providers.page_hosts()
#: 直链媒体放行任意公网主机的来源，同样投影自 follow_providers。
_PUBLIC_MEDIA_PROVIDERS = follow_providers.public_media_hosts()
#: rule34video 把每一档清晰度写成独立字段：`video_url` 是最低档，
#: `video_alt_url`、`video_alt_url2`、`video_alt_url3` 依次更高。
#: 2026-08-31 实测 video/4564733 给出 360 / 480p / 720p / 1080p 四档；
#: 只解析 `video_url` 的话永远播最低那一档。
_VIDEO_URL_RE = re.compile(r"\bvideo(?:_alt)?_url\d*\s*:\s*'([^']+)'", re.IGNORECASE)
#: 从文件名尾部读分辨率：`4564733_1080p.mp4` → 1080，`4564733_360.mp4` → 360。
_VIDEO_HEIGHT_RE = re.compile(r"_(\d{3,4})p?\.mp4", re.IGNORECASE)
#: xHamster 把播放器要的 HLS 母清单地址写在作品页的 `<link rel="preload">` 上，形如
#: `…/media=hls4/multi=…/<path>/_TPL_.<codec>.mp4.m3u8`：`_TPL_` 是档位占位符，母清单本身
#: 也从这个地址取回。token 按编解码绑定、换 codec 要重签；渐进式 mp4 只到 720p，1080p 与
#: 4K 只在这份清单里（2026-10-24 实测）。
_XHAMSTER_HLS_RE = re.compile(
    r"""(https://[^"'\s]+?/media=hls4/[^"'\s]+?)/_TPL_\.(av1|h264|h265)\.mp4\.m3u8""")
#: 母清单里一档写成 `#EXT-X-STREAM-INF:…,RESOLUTION=1920x1080`，下一行是子清单地址。
_XHAMSTER_STREAM_INF_RE = re.compile(r"RESOLUTION=\d+x(\d+)", re.IGNORECASE)


def _video_height(url: str) -> int:
    matched = _VIDEO_HEIGHT_RE.search(urllib.parse.urlsplit(url).path)
    return int(matched.group(1)) if matched else 0


def _pick_quality(resolved: ResolvedFollowMedia, height: int | None) -> ResolvedFollowMedia:
    """按高度挑一档。要的那档不在就保持原样（默认是最高档）。"""
    if not height or not resolved.qualities:
        return resolved
    for level, url in resolved.qualities:
        if level == height:
            return replace(resolved, url=url)
    return resolved

def _allowed(provider: str, url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if any(host == suffix or host.endswith("." + suffix)
           for suffix in _PROVIDER_HOSTS.get(provider, ())):
        return True
    return provider in _PUBLIC_MEDIA_PROVIDERS and public_https_url(url)


def _page_allowed(provider: str, url: str) -> bool:
    """这个来源的作品页能不能去取：媒体主机或页面主机任一命中即可。

    rule34video 的详情页与正片在同一个域；Pornhub 的页面在 `pornhub.com`、媒体在
    `phncdn.com`，只看媒体白名单会把每一条都判成取不到。
    """
    if _allowed(provider, url):
        return True
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host:
        return False
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in _PROVIDER_PAGE_HOSTS.get(provider, ()))


def proxyable(provider: str, media_url: str | None) -> bool:
    """一条直链媒体能不能经 `/follow-stream` 代理出去。

    界面上的 `playable` 要和这里同一口径：说「可播」却让代理在白名单那一步拒收，
    详情页就只剩一张打不开的图。归档站的存量行先按 `archive_file_url` 修回可取的
    形式再判，与 `resolve()` 的直链分支一致。
    """
    url = archive_file_url(provider, str(media_url or ""))
    return bool(url) and _allowed(provider, url)


#: 播放时才去详情页解析正片的来源。它们落库时没有可抄的直链：rule34video 的签名地址会
#: 过期，Pornhub 与 xHamster 干脆只有 HLS 清单。`playable` 因此不能只看 `media_url`，
#: 否则这几类条目在界面上是「不可播」，点开却是能播的。
PLAY_AT_RESOLVE = frozenset({"rule34video", "pornhub", "xhamster"})

#: 片源只有清单的来源：播放器要按 HLS 挂 hls.js，交给 `video` 元素它只会去取一个解析不了
#: 的地址。
PLAYLIST_PROVIDERS = frozenset({"pornhub", "xhamster"})


def resolvable_at_play(provider: str, page_url: str | None) -> bool:
    """这一条能不能在按下播放时再去站点解析。判的是作品页地址本身可信。"""
    return provider in PLAY_AT_RESOLVE and _page_allowed(provider, str(page_url or ""))


def media_type(provider: str, media_url: str | None) -> str:
    """播放器该用哪种源类型。只有清单的来源给清单类型，其余按扩展名。"""
    if provider in PLAYLIST_PROVIDERS:
        return "application/vnd.apple.mpegurl"
    path = urllib.parse.urlsplit(str(media_url or "")).path.casefold()
    if path.endswith(".webm"):
        return "video/webm"
    if path.endswith(".mov"):
        return "video/quicktime"
    return "video/mp4"


class FollowMediaResolver:
    """Resolve stable ledger candidates into short-lived upstream playback URLs."""

    #: 缓存里最多留多少条解析结果。按下播放才解析的来源（rule34video 的签名直链、pornhub
    #: 与 xhamster 的清单）都会进这个缓存，一次列表几百条，用户挨个点开就会挨个留下
    #: 一条；只按 TTL 过期的话，进程活多久它就长多久。上限按「一屏反复点开也够用」定，
    #: 超了先丢已过期的，再丢最早写进来的。
    MAX_CACHE_ENTRIES = 256
    #: 详情页没取到（删片、改版、被拒）后，同一条多久内不再回源。播放器起播时
    #: `/follow-stream`、`/follow-qualities` 和之后的每个 Range 请求都会来问一遍，
    #: 不记住失败，一条坏片就是一串详情页请求。
    MISS_TTL = 120.0
    #: 来源回 403 / 429 后整站暂停回源多久。带 `Retry-After` 就按它，但不超过上限。
    #: 被限流时还照常请求只会延长封禁，卡面 poster 与播放解析都先问这里。
    COOLDOWN = 300.0
    MAX_COOLDOWN = 1800.0
    REFUSED_STATUSES = frozenset({403, 429})

    def __init__(self, transport: HttpTransport, *, ttl: float = 300.0,
                 timeout: float = 20.0, max_bytes: int = 2_000_000,
                 max_cache_entries: int | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.transport = transport
        self.ttl = ttl
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_cache_entries = (self.MAX_CACHE_ENTRIES if max_cache_entries is None
                                  else max_cache_entries)
        self.clock = clock
        self._cache: dict[int, tuple[float, ResolvedFollowMedia]] = {}
        self._misses: dict[int, float] = {}
        self._cooldowns: dict[str, float] = {}
        self._item_locks: dict[int, threading.Lock] = {}
        #: HLS 子资源键 → (过期时刻, 条目 id, 清单根地址, 上游地址)。
        self._resources: OrderedDict[str, tuple[float, int, str, str]] = OrderedDict()
        self._lock = threading.Lock()

    def cooling(self, provider: str) -> float:
        """这个来源还要暂停回源多少秒；0 表示可以请求。"""
        with self._lock:
            return max(0.0, self._cooldowns.get(provider, 0.0) - self.clock())

    def fetch(self, provider: str, request: HttpRequest, timeout: float,
              max_bytes: int) -> HttpResponse:
        """经来源冷却闸门发一次请求；来源回 403 / 429 就让整站进入冷却。

        播放解析与卡面 poster 共用这一道闸：同一个站，一边被限流、另一边还在请求，
        冷却就永远结束不了。
        """
        remaining = self.cooling(provider)
        if remaining:
            raise FollowMediaUnavailable(f"{provider} 限流冷却中，{int(remaining) + 1} 秒后再试")
        response = self.transport(request, timeout, max_bytes)
        if response.status in self.REFUSED_STATUSES:
            headers = {key.lower(): value for key, value in response.headers.items()}
            try:
                retry_after = float(headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            seconds = self.pause(provider, retry_after)
            LOGGER.warning("%s refused %s with HTTP %s; pausing upstream requests for %ds",
                           provider, request.url, response.status, seconds)
        return response

    def pause(self, provider: str, retry_after: float | None = None) -> float:
        """让来源进入冷却并返回冷却秒数；没给 `retry_after` 就按默认时长。

        后台检查被限流时也记到这里：浏览与检查打的是同一个站，冷却只能有一份。
        """
        seconds = (self.COOLDOWN if retry_after is None
                   else min(max(retry_after, 1.0), self.MAX_COOLDOWN))
        with self._lock:
            self._cooldowns[provider] = max(self._cooldowns.get(provider, 0.0),
                                            self.clock() + seconds)
        return seconds

    def _item_lock(self, item_id: int) -> threading.Lock:
        """同一条的解析排队：并发进来的请求只让第一个回源，其余等它写进缓存。"""
        with self._lock:
            lock = self._item_locks.get(item_id)
            if lock is None:
                if len(self._item_locks) >= self.max_cache_entries:
                    for key, tracked in list(self._item_locks.items()):
                        if not tracked.locked():
                            del self._item_locks[key]
                lock = self._item_locks[item_id] = threading.Lock()
            return lock

    def _cached(self, item_id: int) -> ResolvedFollowMedia | None:
        """缓存命中返回结果；近期失败过直接抛出，不再回源。调用方持有 `_lock`。"""
        now = self.clock()
        cached = self._cache.get(item_id)
        if cached and cached[0] > now:
            return cached[1]
        missed = self._misses.get(item_id)
        if missed is not None:
            if missed > now:
                raise FollowMediaUnavailable("Rule34Video 详情页刚刚未取得，稍后再试")
            del self._misses[item_id]
        return None

    def _remember_miss(self, item_id: int) -> None:
        with self._lock:
            now = self.clock()
            for key, expiry in list(self._misses.items()):
                if expiry <= now:
                    del self._misses[key]
            while self._misses and len(self._misses) >= self.max_cache_entries:
                self._misses.pop(next(iter(self._misses)))
            self._misses[item_id] = now + self.MISS_TTL

    def _remember(self, item_id: int, resolved: ResolvedFollowMedia,
                  ttl: float | None = None) -> None:
        """写入缓存并保持有界。调用方持有 `_lock`。"""
        now = self.clock()
        for key, (expiry, _) in list(self._cache.items()):
            if expiry <= now:
                del self._cache[key]
        self._cache.pop(item_id, None)
        while self._cache and len(self._cache) >= self.max_cache_entries:
            self._cache.pop(next(iter(self._cache)))
        self._cache[item_id] = (now + (self.ttl if ttl is None else ttl), resolved)

    # ---- HLS 子资源的短键 ------------------------------------------------
    #
    # 清单里的每一跳（子播放列表、分片、密钥）都换成一个不含上游地址的键，浏览器只
    # 看得到 Peach 的 URL。映射只活在本机内存：上游的签名几分钟就过期，落盘只会留下
    # 一批再也取不到的地址。容量按「几屏反复点开」定，超了先丢最早写进来的。

    RESOURCE_TTL = 6 * 3600

    def register_resource(self, item_id: int, root: str, url: str) -> str:
        key = hashlib.sha256(f"{item_id}\n{root}\n{url}".encode()).hexdigest()
        with self._lock:
            self._resources.pop(key, None)
            self._resources[key] = (self.clock() + self.RESOURCE_TTL, item_id, root, url)
            while len(self._resources) > self.max_cache_entries * 64:
                self._resources.popitem(last=False)
        return key

    def resource(self, item_id: int, key: str) -> str:
        """按键取回上游地址。只核对条目归属：清单里的地址会随每次解析换签名，
        拿当前根地址去比会把刚发出的那份清单判成失效。"""
        with self._lock:
            row = self._resources.get(key)
        if row is None or row[0] <= self.clock() or row[1] != item_id:
            raise FollowMediaUnavailable("播放资源已失效，请重新打开这一条")
        return row[3]

    def with_credential_loader(
            self, loader: Callable[[str], Credential | None]) -> "FollowMediaResolver":
        self._credential_loader = loader
        return self

    def _resolve_listed(self, item: FollowItemRow, media_items: list,
                        media_index: int | None) -> ResolvedFollowMedia:
        """媒体清单里的第 N 张：按它自己的 `resource_provider` 挑白名单与请求头。"""
        index = 0 if media_index is None else media_index
        if index < 0 or index >= len(media_items):
            raise FollowMediaUnavailable("媒体序号不存在")
        media = media_items[index]
        if not isinstance(media, dict):
            raise FollowMediaUnavailable("媒体条目格式不符")
        url = str(media.get("url") or "")
        resource_provider = str(media.get("resource_provider") or "")
        if resource_provider == "gofile":
            if not _allowed_resource(url, ("gofile.io",)):
                raise FollowMediaUnavailable("Gofile 返回了不受信任的媒体地址")
            loader = getattr(self, "_credential_loader", None)
            credential = loader("gofile") if loader else None
            token = str(credential.values.get("api_token") or "") if credential else ""
            if not token:
                raise FollowMediaUnavailable("Gofile API token 未配置")
            return ResolvedFollowMedia(
                url, item.url, {"Authorization": f"Bearer {token}"},
                allowed_hosts=("gofile.io",))
        if resource_provider == "fanbox":
            # 正文图在 downloads.fanbox.cc；封面在 pixiv.pximg.net——pixiv 官方
            # CDN，fanbox 封面都从它出，与 fanbox.cc 同属一个运营方。
            if not _allowed_resource(url, ("fanbox.cc", "pixiv.pximg.net")):
                raise FollowMediaUnavailable("FANBOX 返回了不受信任的图片地址")
            return ResolvedFollowMedia(url, item.url,
                                       allowed_hosts=("fanbox.cc", "pixiv.pximg.net"))
        if resource_provider == "f95zone":
            if not _allowed_resource(url, ("attachments.f95zone.to",)):
                raise FollowMediaUnavailable("F95 返回了不受信任的图片地址")
            loader = getattr(self, "_credential_loader", None)
            credential = loader("f95zone") if loader else None
            cookie = str(credential.values.get("cookie") or "") if credential else ""
            if item.metadata.get("media_needs_credential") and not cookie:
                raise FollowMediaUnavailable("F95 附件需要登录会话")
            return ResolvedFollowMedia(
                url, item.url, {"Cookie": cookie} if cookie else None,
                allowed_hosts=("attachments.f95zone.to",))
        if resource_provider and resource_provider == item.provider \
                and _PROVIDER_HOSTS.get(item.provider):
            # 归档站（kemono、coomer、pawchive）帖子的附件清单：媒体就在条目自己的
            # 站上，按该来源的主机白名单放行，与 `resolve` 的直链分支同一口径。
            if not _allowed(item.provider, url):
                raise FollowMediaUnavailable("来源媒体地址不可用")
            return ResolvedFollowMedia(
                url, item.url, allowed_hosts=tuple(_PROVIDER_HOSTS[item.provider]),
                public_hosts=item.provider in _PUBLIC_MEDIA_PROVIDERS)
        raise FollowMediaUnavailable("媒体来源不受支持")

    def resolve(self, item: FollowItemRow, media_index: int | None = None,
                height: int | None = None) -> ResolvedFollowMedia:
        """解析可播地址。`height` 指定清晰度，只有 rule34video、Pornhub 与 xHamster 有多档
        可选；要的那档不存在就退回默认（最高档），不报错——签名 URL 会过期，
        为一次选档失败中断播放不值得。"""
        media_items = item.metadata.get("media_items")
        if (not isinstance(media_items, list) or not media_items) \
                and item.provider == "f95zone":
            media_items = f95_attachment_media_items(item.metadata)
        if isinstance(media_items, list) and media_items:
            return self._resolve_listed(item, media_items, media_index)
        if item.metadata.get("media_needs_credential"):
            raise FollowMediaUnavailable("媒体需要来源登录会话")
        if item.provider not in PLAY_AT_RESOLVE:
            # 归档站的存量行按旧规则拼过 URL（少 /data 前缀、主机也不对），
            # 在这里修回可取的形式；`_allowed` 按后缀匹配，file./n1. 这些子域
            # 仍在原站的白名单内，安全边界没有放宽。
            media_url = archive_file_url(item.provider, str(item.media_url or ""))
            if not media_url or not _allowed(item.provider, media_url):
                raise FollowMediaUnavailable("来源媒体地址不可用")
            return ResolvedFollowMedia(
                media_url, item.url,
                allowed_hosts=tuple(_PROVIDER_HOSTS.get(item.provider, ())),
                public_hosts=item.provider in _PUBLIC_MEDIA_PROVIDERS)

        with self._lock:
            cached = self._cached(item.id)
        if cached:
            return _pick_quality(cached, height)
        if not item.url or not _page_allowed(item.provider, item.url):
            raise FollowMediaUnavailable("详情页地址不可用")
        with self._item_lock(item.id):
            # 排队期间前一个请求可能已经写好缓存或记下失败，先看一眼再决定回不回源。
            with self._lock:
                cached = self._cached(item.id)
            if cached:
                return _pick_quality(cached, height)
            fetcher = {"pornhub": self._fetch_pornhub,
                       "xhamster": self._fetch_xhamster}.get(
                           item.provider, self._fetch_rule34video)
            return _pick_quality(fetcher(item), height)

    def _fetch_rule34video(self, item: FollowItemRow) -> ResolvedFollowMedia:
        """抓一次详情页，解析出各档正片地址并写进缓存；没取到记一次失败。"""
        response = self.fetch(
            item.provider,
            HttpRequest("GET", item.url, {"User-Agent": USER_AGENT,
                                           "Accept": "text/html"}),
            self.timeout, self.max_bytes,
        )
        if response.status != 200 or len(response.body) > self.max_bytes:
            LOGGER.warning("rule34video detail page for follow item %s unavailable: HTTP %s",
                           item.id, response.status)
            self._remember_miss(item.id)
            raise FollowMediaUnavailable("Rule34Video 详情页未取得")
        text = response.body.decode("utf-8", errors="replace")
        found: list[str] = []
        for matched in _VIDEO_URL_RE.finditer(text):
            candidate = html.unescape(matched.group(1)).replace("\\/", "/")
            if candidate not in found and _allowed(item.provider, candidate):
                found.append(candidate)
        if not found:
            LOGGER.warning("rule34video detail page for follow item %s has no video url", item.id)
            self._remember_miss(item.id)
            raise FollowMediaUnavailable("Rule34Video 正片地址未取得")
        # 高清优先：默认播最高一档，其余作为可选清晰度交给播放器。认不出分辨率的
        # 排在最后但不丢——签名 URL 每次都变，丢了就没有回退。
        ordered = sorted(found, key=_video_height, reverse=True)
        resolved = ResolvedFollowMedia(
            ordered[0], item.url,
            qualities=tuple((_video_height(url), url) for url in ordered),
            allowed_hosts=tuple(_PROVIDER_HOSTS.get(item.provider, ())))
        with self._lock:
            self._remember(item.id, resolved)
        return resolved

    #: HLS 清单的有效期比签名短得多：2026-10-23 实测 Pornhub 的 `validto` 只有一个
    #: 小时上下，而清单里叠着好几层签名。缓存只留两分钟，过期后重新解析一次，
    #: 免得把一批已经 410 的地址反复交给播放器。
    PLAYLIST_TTL = 120.0

    #: 作品页会把清单地址写成两种之一：带 `validfrom`／`validto` 的那种取回 200，只剩一个
    #: `?h=` 哈希的那种取回 410。同一条作品连着取页面会换一种，实测取回可用清单的概率约
    #: 四成，所以取回清单验证一遍，验不过就换一次页面重来；每次换页带一个没用过的参数，
    #: 绕开边缘缓存里那一份。六次能把可用清单的概率提到九成五以上。
    PORNHUB_ATTEMPTS = 6

    #: 验证清单只取开头这一截：`#EXTM3U` 加几行 `EXT-X-STREAM-INF` 就够判。
    PLAYLIST_PROBE_BYTES = 64 * 1024

    def _fetch_pornhub(self, item: FollowItemRow) -> ResolvedFollowMedia:
        """抓作品页，从 `mediaDefinitions` 里取 HLS 各档，把能取回的那一种写进缓存。

        这个站不给可播的渐进式 mp4：`format=mp4` 那条指向 `/video/get_media`，
        匿名请求回空数组。所以这里只认 `format=hls`。
        """
        for attempt in range(self.PORNHUB_ATTEMPTS):
            response = self.fetch(
                item.provider,
                HttpRequest("GET", _pornhub_page_attempt(item.url, attempt),
                            {"User-Agent": USER_AGENT, "Accept": "text/html"}),
                self.timeout, self.max_bytes,
            )
            if response.status != 200 or len(response.body) > self.max_bytes:
                LOGGER.warning("pornhub watch page for follow item %s unavailable: HTTP %s",
                               item.id, response.status)
                continue
            found: list[tuple[int, str]] = []
            for entry in pornhub_media_definitions(
                    response.body.decode("utf-8", errors="replace")):
                url = str(entry.get("videoUrl") or "")
                if not url or not _allowed(item.provider, url):
                    continue
                try:
                    height = int(entry.get("height") or 0)
                except (TypeError, ValueError):
                    height = 0
                if all(existing != url for _, existing in found):
                    found.append((height, url))
            if not found:
                LOGGER.warning("pornhub watch page for follow item %s has no hls url", item.id)
                continue
            ordered = sorted(found, key=lambda row: row[0], reverse=True)
            if not self._pornhub_playlist_ready(item, ordered[0][1]):
                LOGGER.warning("pornhub playlist for follow item %s is not playable", item.id)
                continue
            resolved = ResolvedFollowMedia(
                ordered[0][1], item.url,
                qualities=tuple(ordered),
                allowed_hosts=tuple(_PROVIDER_HOSTS.get(item.provider, ())),
                playlist=True)
            with self._lock:
                self._remember(item.id, resolved, ttl=self.PLAYLIST_TTL)
            return resolved
        self._remember_miss(item.id)
        raise FollowMediaUnavailable("Pornhub 正片地址未取得")

    def _fetch_xhamster(self, item: FollowItemRow) -> ResolvedFollowMedia:
        """抓作品页，从页面上那条 HLS 母清单取各档，把能取回的那一档写进缓存。

        这个站的渐进式 mp4 到 720p 为止，1080p 与 4K 只在播放器的 HLS 里，所以这里只认
        母清单。母清单带到期时间（`end=`），只缓存两分钟，过期后重新解析一次；第一跳是
        `video*.xhcdn.com`，取的时候会 302 到 `ip*.ahcdn.com`，两个主机都在这个来源的
        白名单里。页面按 UA 发 token：标准 UA 拿到 av1，只有 Safari 拿到 h265，而
        Safari 解不了 av1——所以先按 h264 换一次签（换成功就是所有浏览器都能播的那一档），
        换不成就用页面自己的。"""
        response = self.fetch(
            item.provider,
            HttpRequest("GET", str(item.url or ""),
                        {"User-Agent": USER_AGENT, "Accept": "text/html"}),
            self.timeout, self.max_bytes,
        )
        if response.status != 200 or len(response.body) > self.max_bytes:
            LOGGER.warning("xhamster watch page for follow item %s unavailable: HTTP %s",
                           item.id, response.status)
            self._remember_miss(item.id)
            raise FollowMediaUnavailable("xHamster 作品页未取得")
        template = xhamster_hls_master(response.body.decode("utf-8", errors="replace"))
        if template is None:
            LOGGER.warning("xhamster watch page for follow item %s has no hls master", item.id)
            self._remember_miss(item.id)
            raise FollowMediaUnavailable("xHamster 正片清单未取得")
        front, codec = template
        for candidate in xhamster_hls_codecs(codec):
            master_url = f"{front}/_TPL_.{candidate}.mp4.m3u8"
            if not _allowed(item.provider, master_url):
                continue
            request = HttpRequest("GET", master_url,
                                  {"User-Agent": USER_AGENT, "Referer": str(item.url or "")})
            if candidate == codec:
                master = self.fetch(item.provider, request, self.timeout,
                                    self.PLAYLIST_PROBE_BYTES)
            else:
                # 换 codec 要重签，换不成时 CDN 回 `403 Wrong key`——那是 token 绑了页面给
                # 的那一档，不是限流。这一跳因此不走冷却闸门，否则一次换签失败会把整站
                # 暂停，后面每一条都取不到。
                master = self.transport(request, self.timeout, self.PLAYLIST_PROBE_BYTES)
            if master.status != 200 or len(master.body) > self.PLAYLIST_PROBE_BYTES:
                LOGGER.warning("xhamster %s master for follow item %s unavailable: HTTP %s",
                               candidate, item.id, master.status)
                continue
            found = tuple(
                (height, url) for height, url in xhamster_hls_variants(
                    master.body.decode("utf-8", errors="replace"), master_url)
                if _allowed(item.provider, url))
            if not found:
                LOGGER.warning("xhamster %s master for follow item %s has no variant",
                               candidate, item.id)
                continue
            resolved = ResolvedFollowMedia(
                found[0][1], item.url,
                qualities=found,
                allowed_hosts=tuple(_PROVIDER_HOSTS.get(item.provider, ())),
                playlist=True)
            with self._lock:
                self._remember(item.id, resolved, ttl=self.PLAYLIST_TTL)
            return resolved
        self._remember_miss(item.id)
        raise FollowMediaUnavailable("xHamster 正片清单未取得")

    def _pornhub_playlist_ready(self, item: FollowItemRow, url: str) -> bool:
        """取回一次清单，确认这一档真的能播。

        换档要用的另外几档是同一份页面给的，签法一致，验一档就够。
        """
        try:
            response = self.fetch(
                item.provider,
                HttpRequest("GET", url, {"User-Agent": USER_AGENT}),
                self.timeout, self.PLAYLIST_PROBE_BYTES,
            )
        except (OSError, httpx.HTTPError) as error:
            LOGGER.warning("pornhub playlist probe for follow item %s failed: %s", item.id, error)
            return False
        return (response.status == 200
                and response.body.lstrip().startswith(b"#EXTM3U"))


def _pornhub_page_attempt(url: str, attempt: int) -> str:
    """第几次取作品页：换一次页面就在地址上带一个没用过的参数，绕开边缘缓存里那一份。"""
    if attempt <= 0:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_", f"{attempt}{int(time.time() * 1000)}"))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def pornhub_media_definitions(text: str) -> tuple[dict, ...]:
    """从作品页里取出 `mediaDefinitions` 那个数组。

    站点把它内嵌在一段 JS 字符串里，引号是转义的（`\\"videoUrl\\":\\"…\\"`）。这里
    先还原转义、再按括号配对截出数组本体交给 JSON 解析：正则会在这份几 MB 的页面
    上被反斜杠和嵌套对象带偏，而这一段本来就是合法 JSON。
    """
    plain = text.replace('\\"', '"').replace("\\/", "/")
    marker = plain.find('"mediaDefinitions"')
    if marker < 0:
        return ()
    start = plain.find("[", marker)
    if start < 0:
        return ()
    depth = 0
    for index in range(start, min(len(plain), start + 200_000)):
        if plain[index] == "[":
            depth += 1
        elif plain[index] == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(plain[start:index + 1])
                except ValueError:
                    return ()
                return tuple(row for row in parsed
                             if isinstance(row, dict) and row.get("format") == "hls")
    return ()


def xhamster_hls_master(text: str) -> tuple[str, str] | None:
    """作品页里 HLS 母清单的地址模板，与页面自己给的那一档编解码。

    `<link rel="preload">` 上的地址以 `_TPL_` 占位档位，母清单本身就从这个地址取回；
    返回 `(前缀, codec)`，取母清单时把 `_TPL_.<codec>.mp4.m3u8` 接在前缀后面。
    """
    matched = _XHAMSTER_HLS_RE.search(text)
    if matched is None:
        return None
    return matched.group(1), matched.group(2).casefold()


def xhamster_hls_codecs(codec: str) -> tuple[str, ...]:
    """按顺序试的编解码。先试 h264——Chrome、Firefox、Safari 都能解；换签不成就用页面自己
    给的那一档（Chrome 系拿到的 token 是 av1，Safari 是 h265）。"""
    return ("h264",) if codec == "h264" else ("h264", codec)


def xhamster_hls_variants(master: str, base: str) -> tuple[tuple[int, str], ...]:
    """母清单里的各档，按高度从高到低。

    `#EXT-X-STREAM-INF` 那行给 `RESOLUTION=1920x1080`，紧跟的下一行是子清单地址，写成站内
    绝对路径或同级相对地址，两种都按 `base`（母清单地址）解析成可取的绝对地址。
    """
    found: list[tuple[int, str]] = []
    height = 0
    for raw in master.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            matched = _XHAMSTER_STREAM_INF_RE.search(line)
            height = int(matched.group(1)) if matched else 0
            continue
        if height:
            url = urllib.parse.urljoin(base, line)
            if all(existing != url for _, existing in found):
                found.append((height, url))
        height = 0
    return tuple(sorted(found, key=lambda row: row[0], reverse=True))


def rewrite_follow_playlist(body: bytes, base: str, *, item_id: int, root: str,
                            provider: str,
                            resolver: FollowMediaResolver) -> bytes:
    """把上游清单里的每一跳换成 Peach 的分片键。

    与 STRM 那条路径共用 `preview_cache.rewrite_hls`：清单模型、相对地址解析和
    `EXT-X-MAP`／密钥／子清单的覆盖范围只该有一份实现。这里额外的两件事是：
    每一跳都要过这个来源的主机白名单，键表挂在解析器上。
    """
    from .preview_cache import rewrite_hls

    def link(target, _nested):
        if not _allowed(provider, target):
            raise FollowProxyError("播放列表包含不可访问的媒体地址")
        key = resolver.register_resource(item_id, root, target)
        return f"/follow-hls?id={item_id}&r={key}"

    return rewrite_hls(body, base, link)


def _allowed_resource(url: str, hosts: tuple[str, ...]) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and any(host == suffix or host.endswith("." + suffix) for suffix in hosts))


def _hop_allowed(target: ResolvedFollowMedia, url: str) -> bool:
    """代理的每一跳（含第一跳）都过这一关：白名单主机直接放行；公网模式还要域名字面
    像公网、解析出来也全是公网地址。解析放在连接前而不是解析时，是因为存量行的
    地址可能躺了几个月，域名早已换主。"""
    if _allowed_resource(url, tuple(target.allowed_hosts)):
        return True
    if not target.public_hosts or not public_https_url(url):
        return False
    return resolves_publicly(urllib.parse.urlsplit(url).hostname or "")


#: 代理层允许跟几跳重定向。归档站的主域会 302 到实际取文件的节点（2026-08-30 实测
#: kemono → n1.、coomer → n4.），所以必须跟；但每一跳都要重新过白名单。
MAX_PROXY_REDIRECTS = 3
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def proxy_request_headers(target: ResolvedFollowMedia,
                          incoming: Mapping[str, str]) -> dict[str, str]:
    """代理请求要带的头。只透传范围请求相关的那两个，别的一律由这里决定。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": incoming.get("accept") or "*/*",
        "Accept-Encoding": "identity",
    }
    if target.referer:
        headers["Referer"] = target.referer
    headers.update(target.headers or {})
    for name in ("range", "if-range"):
        value = incoming.get(name)
        if value:
            headers[name.title()] = value
    return headers


def open_upstream(client: httpx.Client, method: str, target: ResolvedFollowMedia, *,
                  incoming: Mapping[str, str], allowed=None, accepted_statuses=(),
                  strip_credentials=False) -> httpx.Response:
    """打开上游媒体流；只在拿到可转发的 2xx 时返回。

    这里自己跟重定向而不是交给 client 的 `follow_redirects`：`target.headers` 可能
    带着 Cookie 或 Bearer token，httpx 跟跳时会把请求头一路带过去，上游只要回一个
    指向别处的 Location 就能把凭据换走。所以每一跳都重新过 `target.allowed_hosts`，
    出界直接失败，绝不带着凭据再发一次。

    非 2xx 同样在这里终止：上游的 403 页面、限流提示或错误 JSON 转发给播放器毫无
    用处，只会把上游的状态与正文（可能含主机名、提示语）原样交给浏览器。
    """
    if allowed is None and not target.allowed_hosts and not target.public_hosts:
        raise FollowProxyError("这条媒体没有可代理的主机白名单")
    url = target.url
    headers = proxy_request_headers(target, incoming)
    response: httpx.Response | None = None
    for _ in range(MAX_PROXY_REDIRECTS + 1):
        if not (allowed(url) if allowed is not None else _hop_allowed(target, url)):
            raise FollowProxyError("媒体地址不在可代理的主机范围内" if url == target.url
                                   else "上游把媒体重定向到了不受信任的地址")
        request = client.build_request(method, url, headers=headers)
        if strip_credentials:
            for name in ('cookie', 'authorization', 'proxy-authorization'):
                request.headers.pop(name, None)
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in _REDIRECT_STATUSES:
            break
        location = response.headers.get("location") or ""
        response.close()
        response = None
        url = urllib.parse.urljoin(url, location)
    if response is None:
        raise FollowProxyError("上游重定向次数过多")
    if not 200 <= response.status_code < 300 and response.status_code not in accepted_statuses:
        status = response.status_code
        response.close()
        raise FollowProxyError(f"上游返回 HTTP {status}")
    return response
