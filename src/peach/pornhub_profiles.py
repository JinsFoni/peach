"""Pornhub 人物页上的头像、简介与资料表。

关注来源只带作品列表，绑上来源的创作者在资料页上因此只有一个名字和一个首字母。这位作者
在站上公开的东西比那多得多：人物页里有头像、一段自我介绍，和一张资料表（感情状态、取向、
性别、出身地、三围、身高、体重、种族、发色、胸部、纹身、穿环、兴趣）。首页、作品页、
「简介」页签三处都给同一批节点，所以取哪一页都一样——这里取来源自己那个地址，不多猜一截
路径。

这里只做两件事：把那一页取回来，把上面这些东西读成 `performer_profile` 那一行要的形状
（`record`）。写账本、装头像归 `creator_profile_followup`。

页型（`/model/`、`/pornstar/`、`/users/`）由来源地址里那一段决定，抓取一律换成中文站：
中文站的资料项是中文标签，值仍是站上原文（`Czech Republic`、`White`、`Brunette`）。
头像地址带签名，只能照页面给的那一条原样取；换尺寸、去掉签名都回 401。

身高三围折进资料卡「身材」那一格（`T167 · B89 · W64 · H91`），资料表里不再重复列；
体重、种族、发色这些账本没有对应列的项随 `raw` 的「资料」一起下发。站上的资料项按
`ABOUT_FIELDS` 换成页面用语，表里还没收录的项照原文列——那是站上确有的说法，丢掉就少
一项读数。
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from .follow import FollowSourceError
from .http import HttpRequest, HttpTransport, HttpxTransport
from .user_agent import USER_AGENT

#: 抓取站与人物页地址都落在这里。`www.pornhub.com` 收到 `Accept-Language: zh-CN` 会 302
#: 到中文站，直接换成它省一跳。
SITE = "https://cn.pornhub.com"
#: 链接里认得的三个主机，取页一律换成 `SITE`。
HOSTS = frozenset({"cn.pornhub.com", "www.pornhub.com", "pornhub.com"})
#: 声明中文，站点据此下发中文页面。
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.8"
MAX_PAGE_BYTES = 4 * 1024 * 1024
PAGE_TIMEOUT = 20.0

#: 人物页路径。后面可以跟页签名（`videos`、`about`……），页签名原样留着。
_PATH_RE = re.compile(
    r"^/(model|pornstar|users)/([A-Za-z0-9][A-Za-z0-9_\-]{0,80})(/[a-z]+)?/?$")
#: 头像只在站自己的图床上。
_AVATAR_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)+phncdn\.com$")
#: `5' 6" (167cm)` 里那个厘米。
_HEIGHT_RE = re.compile(r"(\d{2,3})\s*cm\b")
#: `35-25-36` 或 `90-63-91 cm`。没有单位按英寸算——站上这一格是英美码。
_MEASURE_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*[-–]\s*(\d{2,3}(?:\.\d+)?)"
                         r"\s*[-–]\s*(\d{2,3}(?:\.\d+)?)")
#: 体重那一格写 `111lbs. (50kg)`：有公斤就用它，只有磅时按 0.45359237 换。
_WEIGHT_KG_RE = re.compile(r"(\d{2,3})\s*kg\b", re.IGNORECASE)
_WEIGHT_LB_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*lbs?\b", re.IGNORECASE)

#: 站上资料项 → 资料卡上的写法与图标。`身高` 与 `三围` 折进「身材」那一格，不重复列。
ABOUT_FIELDS = {
    "感情状态": ("感情", "heart"),
    "喜欢": ("取向", "venus-and-mars"),
    "性别": ("性别", "venus"),
    "出身地": ("出生地", "globe"),
    "体重": ("体重", "dumbbell"),
    "种族": ("种族", "footprints"),
    "发色": ("发色", "sparkles"),
    "假奶子": ("胸部", "gem"),
    "纹身": ("纹身", "pencil"),
    "是否有穿环": ("穿环", "gem"),
    "兴趣爱好": ("兴趣", "notebook-pen"),
    "会勾起我性欲的是": ("勾起性欲", "flame"),
    "会浇灭我性欲的是": ("浇灭性欲", "droplets"),
}
#: 折进别处、或只是读数：不进资料卡。
ABOUT_SKIPPED = frozenset({"身高", "三围", "视频播放量", "主页浏览量", "已看视频"})
#: 站上这三项写「是／否」，英文站写 `Yes`／`No`；换成页面用语。
ABOUT_FLAGS = {
    "假奶子": {"是": "加工", "否": "天然", "yes": "加工", "no": "天然"},
    "纹身": {"是": "有", "否": "无", "yes": "有", "no": "无"},
    "是否有穿环": {"是": "有", "否": "无", "yes": "有", "no": "无"},
}
#: 表里还没收录的资料项用这一枚图标。
UNKNOWN_GLYPH = "info"


def page_url(source_url: str) -> str:
    """来源地址 → 取资料的那一页。主机换成中文站，路径照来源自己的。"""
    parsed = urllib.parse.urlsplit(str(source_url or ""))
    matched = _PATH_RE.match(parsed.path or "/")
    host = (parsed.hostname or "").casefold()
    if host not in HOSTS or matched is None:
        raise FollowSourceError(f"这条来源不是 pornhub 的人物页：{source_url!r}")
    return f"{SITE}/{matched.group(1)}/{matched.group(2)}{matched.group(3) or ''}"


def fetch_page(url: str, *, transport: HttpTransport | None = None) -> tuple[str, str]:
    """取这一页，返回 (跟完重定向的最终地址, 页面文本)。"""
    request = transport or HttpxTransport()
    response = request(
        HttpRequest("GET", url, {"Accept": "text/html", "Accept-Language": ACCEPT_LANGUAGE,
                                 "User-Agent": USER_AGENT}),
        PAGE_TIMEOUT, MAX_PAGE_BYTES,
    )
    if response.status == 404:
        raise FollowSourceError("站上没有这位的简介页")
    if response.status != 200:
        raise FollowSourceError(f"人物页返回 HTTP {response.status}")
    return response.url or url, response.body.decode("utf-8", "replace")


def _text(node) -> str:
    """一个节点里那点字，空白收成单个空格。"""
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _label(node) -> str:
    """`感情状态:` → `感情状态`。站上中英混排，冒号也有两种写法。"""
    return _text(node).rstrip(":：").strip()


def _avatar_url(node) -> str:
    """头像地址：只收站自己图床上的 https 直链，其余一律当没给。"""
    raw = str((node.get("src") if node is not None else "") or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https" or not _AVATAR_HOST_RE.match(parsed.hostname or ""):
        return ""
    return raw


def _bio(soup) -> str:
    """「简介」那一块的正文：标题（`简介 Mini Diva`）下面那一个 div。"""
    title = soup.select_one("section.aboutMeSection .title")
    return _text(title.find_next_sibling("div")) if title is not None else ""


def _weight(text: str) -> str:
    """体重统一写公斤：`111lbs. (50kg)` → `50 kg`，只有磅时换算。"""
    kg = _WEIGHT_KG_RE.search(text)
    if kg:
        return f"{kg.group(1)} kg"
    pounds = _WEIGHT_LB_RE.search(text)
    if pounds:
        return f"{round(float(pounds.group(1)) * 0.45359237)} kg"
    return text


#: 这几项的值在这里规整成页面用语，其余照站上原文。
_VALUE_NORMALISERS = {"体重": _weight}


def _about(soup) -> tuple[list[dict], dict]:
    """资料表：(资料卡上的几项, 站上原文的 label → value)。"""
    shown: list[dict] = []
    fields: dict[str, str] = {}
    for piece in soup.select("div.js-infoText div.infoPiece"):
        spans = piece.find_all("span", recursive=False)
        label = _label(spans[0]) if spans else ""
        value = _text(spans[1]) if len(spans) > 1 else ""
        if not label or not value:
            continue
        fields[label] = value
        if label in ABOUT_SKIPPED:
            continue
        wording, glyph = ABOUT_FIELDS.get(label, (label, UNKNOWN_GLYPH))
        flag = ABOUT_FLAGS.get(label, {}).get(value.casefold())
        normalise = _VALUE_NORMALISERS.get(label, lambda text: text)
        shown.append({"label": wording, "value": flag or normalise(value), "glyph": glyph})
    return shown, fields


def _socials(soup) -> list[dict]:
    """站上登记的本人账号。名字取站上那行字（`官方网址`、`Twitter`）。"""
    found = []
    for anchor in soup.select("div.socialsColumn .socialList a[href]"):
        url = urllib.parse.urlsplit(str(anchor.get("href") or "").strip())
        if url.scheme != "https" or not url.hostname:
            continue
        found.append({"label": _text(anchor), "url": url.geturl()})
    return found


def _stats(soup) -> dict:
    """头像右边那几格读数：`data-title` 带精确数字，格子里是缩写（`561M`）。"""
    found = {}
    for box in soup.select("div.infoBoxes .infoBox"):
        label, _, exact = str(box.get("data-title") or "").partition(":")
        if label.strip() and exact.strip():
            found[label.strip()] = exact.strip()
    return found


def parse(page: str) -> dict:
    """一页人物页 → 名字、头像地址、简介、资料表、本人账号与读数。

    名字读不出来就抛——页面结构变了，后面每一格都会跟着错。
    """
    soup = BeautifulSoup(page, "html.parser")
    avatar = soup.select_one("#getAvatar")
    name = _text(soup.select_one('h1[itemprop="name"]'))
    if not name and avatar is not None:
        name = str(avatar.get("alt") or "").strip()
    if not name:
        raise FollowSourceError("人物页没有解析出名字：页面结构可能已变")
    about, fields = _about(soup)
    return {"name": name, "avatar_url": _avatar_url(avatar), "bio": _bio(soup),
            "about": about, "fields": fields, "socials": _socials(soup), "stats": _stats(soup)}


def record(parsed: dict) -> dict:
    """`parse` 的结果 → `performer_profile` 那一行。读不出的列留空。

    身高与三围是这里唯一要换算的项：站上给英制，账本按厘米存。三围那一格写英寸时
    换算，写 `cm` 时照收。
    """
    fields = parsed.get("fields") or {}
    profile: dict = {"tags": [],
                     "raw": {"名前": parsed.get("name") or "",
                             "简介": parsed.get("bio") or "",
                             "资料": list(parsed.get("about") or [])}}
    height = _HEIGHT_RE.search(str(fields.get("身高") or ""))
    if height:
        profile["height_cm"] = int(height.group(1))
    measure = str(fields.get("三围") or "")
    sizes = _MEASURE_RE.search(measure)
    if sizes:
        scale = 1.0 if "cm" in measure.casefold() else 2.54
        for key, number in zip(("bust_cm", "waist_cm", "hip_cm"), sizes.groups()):
            profile[key] = round(float(number) * scale)
    for column, label in (("birthplace", "出身地"), ("hobbies", "兴趣爱好")):
        value = str(fields.get(label) or "").strip()
        if value:
            profile[column] = value
    return profile
