"""女优页头要的两样东西：五项资料（`profile`）与按名义分组的别名（`name_groups`）（ADR-0069）。

资料取自 `performer_profile`（ADR-0067），这里只读不写。页头只给五项：生日与年龄、身材、
出道、生涯（出演年份）、站上的标签。血型、出身地、趣味这些在账本里，不进资料卡：卡里那一栏是
认人用的，一行放不下的东西放进去只会把这五项挤成两行。取不到的项不给，前端也就不画那一格，
不写「未取得」。

发布账号（ADR-0096 建出来的 `creator`）在站上有一整页自我介绍，读数是简介加十几项资料
（感情、取向、出身地、体重、纹身……）。这些走 `about()`，前端排在资料卡下面另起一栏
（ADR-0115）：卡里仍旧只有那五项，多出来的读数不挤它。那一页上还有站给的模特排名与她
自己登记的账号链接，走 `side()`，前端排在卡的最右一栏。

别名分组的口径：

- `r18:performer` 的条目是读音与罗马字，不是艺名；`javinizer:planning-alias` 是作品给她起的
  一次性称呼（ADR-0038 有意登记，检索要用）。两种都不上页头，账本里照留。
- 规范名是中日文写法时，纯拉丁写法不列（与 `display_aliases` 同一条）。
- 读音只出一次：资料页的读音优先，没有资料时取别名里那条纯平假名的写法。与读音、
  与规范名同键的写法都不再列。
- 有资料时按页上的名义分组：现名（资料页主名）、旧名义（没有渠道注记的别名）、各渠道
  名义（按人数从多到少）、其它（账本里有、页上没列的写法）。没有资料时只有一组，不带标题。
  页上的注记在 `raw_json` 的「別名」原文里，读的时候用 `minnano_av.name_entries` 拆。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from .entities import normalize_entity_name
from .genre_decisions import load_genre_decisions
from .genre_taxonomy import resolve_profile_tag
from .kanji import fold_glyphs
from .metadata_alias_resolve import PLANNING_ALIAS_SOURCE
from .minnano_av import name_entries
from .performer_profiles import TABLE, read_profile
from .social_links import name_key

#: 不上页头的别名来源。
HIDDEN_SOURCES = frozenset({"r18:performer", PLANNING_ALIAS_SOURCE})
#: 名字那一行在读音之后先列几个。
SHOWN = 3
CURRENT, FORMER, OTHER = "现名", "旧名义", "其它"
#: `raw` 里那两项的键，与资料站上的写法一致。
_ABOUT, _BIO = "资料", "简介"
#: `raw` 里另外两把钥匙：站上那几格读数与她自己登记的账号。
_READINGS, _SOCIALS = "读数", "社交"
#: 右侧那一栏取哪一格读数，以及它那枚图标（排名是展示那一侧的说法，采集不懂）。
RANK, RANK_GLYPH = "模特排名", "crown"
#: 资料项那几格的长度上限：一格值写四五十个字就不是读数是段落了，简介同理。
LABEL_LIMIT, VALUE_LIMIT, BIO_LIMIT = 8, 120, 280
#: 链接那一栏的名称上限与条数上限：一排外链不该把卡撑成一列。
LINK_LABEL_LIMIT, LINK_LIMIT = 16, 6
#: 图标名缺省值，雪碧图里的那一枚「i」。
DEFAULT_GLYPH = "info"
#: 身材一格的列 → 页头的键。
_BODY = (("height_cm", "height"), ("bust_cm", "bust"), ("waist_cm", "waist"),
         ("hip_cm", "hip"), ("cup", "cup"))

_EAST_ASIAN = re.compile(r"[぀-ヿ㐀-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
_HIRAGANA = re.compile(r"^[ぁ-ゟー\s]+$")


def _key(name: str) -> str:
    return fold_glyphs(name_key(name))


def age_on(birth: str, today: date) -> int | None:
    try:
        born = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def facts(row: dict | None, today: date, decisions: dict[str, str | None] | None = None) -> dict:
    """页头那五项要的值，只放有的。`row` 是 `read_profile` 交回的一行。"""
    row = row or {}
    found: dict = {}
    birth = str(row.get("birth_date") or "")
    age = age_on(birth, today) if birth else None
    if age is not None:
        found.update(birth_date=birth, age=age)
    found.update({key: row[column] for column, key in _BODY if row.get(column)})
    for key in ("debut_date", "debut_title"):
        if row.get(key):
            found[key] = row[key]
    if row.get("debut_year"):
        until = row.get("active_until")
        found["active"] = {"from": row["debut_year"],
                           **({"to": until} if until else {"ongoing": True})}
    tags = site_tags(row.get("tags") or [], decisions)
    if tags:
        found["tags"] = tags
    return found


def about(row: dict | None) -> dict:
    """发布账号在站上那一页：简介一段、资料表若干项。没有这两样的来源不给。

    两者都在 `raw`（站上原文那一栏）里，键是站上的写法：`简介` 是一段话，`资料` 是
    `[{label, value, glyph}]`。图标名由采集那一侧定（`pornhub_profiles.ABOUT_FIELDS`），
    这里是原样下发——页头不认识哪一家站点。值太长按页面的读法截断，整段简介挂在
    `title` 上也读不完。"""
    raw = (row or {}).get("raw") or {}
    found: dict = {}
    items = [item for item in (_about_item(entry) for entry in raw.get(_ABOUT) or []) if item]
    if items:
        found["about"] = items
    bio = " ".join(str(raw.get(_BIO) or "").split())
    if bio:
        found["bio"] = bio[:BIO_LIMIT] + ("…" if len(bio) > BIO_LIMIT else "")
    return found


def _about_item(entry) -> dict | None:
    """资料表里的一行；缺标签或缺值的格子不画，不写「未取得」。"""
    if not isinstance(entry, dict):
        return None
    label = " ".join(str(entry.get("label") or "").split())
    value = " ".join(str(entry.get("value") or "").split())
    if not label or not value:
        return None
    glyph = str(entry.get("glyph") or "").strip() or DEFAULT_GLYPH
    return {"label": label[:LABEL_LIMIT], "value": value[:VALUE_LIMIT], "glyph": glyph}


def side(row: dict | None) -> dict:
    """资料卡右侧那一栏：站上那颗模特排名，与她自己登记的账号。

    两样都在 `raw` 里（`读数`、`社交`），页面用语与图标由采集那一侧定，这里是原样下发。
    排名只取那一格，播放量、订阅者这些是站上的运营数字，不进卡。链接只收 https 直链：
    页上给别的东西时点不开，也就没有画出来的道理。"""
    raw = (row or {}).get("raw") or {}
    found: dict = {}
    rank = " ".join(str((raw.get(_READINGS) or {}).get(RANK) or "").split())
    if rank:
        found["rank"] = {"label": RANK, "value": rank[:LABEL_LIMIT], "glyph": RANK_GLYPH}
    links: list[dict] = []
    seen: set[str] = set()
    for entry in raw.get(_SOCIALS) or []:
        item = _side_link(entry)
        if item and item["url"] not in seen:
            seen.add(item["url"])
            links.append(item)
    if links:
        found["links"] = links[:LINK_LIMIT]
    return found


def _side_link(entry) -> dict | None:
    """右侧那一栏的一条链接：站自己的账号主页，名字照页上写。"""
    if not isinstance(entry, dict):
        return None
    url = str(entry.get("url") or "").strip()
    if not url.casefold().startswith("https://"):
        return None
    label = " ".join(str(entry.get("label") or "").split()) or url.split("//", 1)[-1]
    glyph = str(entry.get("glyph") or "").strip() or DEFAULT_GLYPH
    return {"label": label[:LINK_LABEL_LIMIT], "url": url, "glyph": glyph}


def site_tags(raw: list, decisions: dict[str, str | None] | None = None) -> list[str]:
    """站上的标签换成 Peach 的中文标签，同义的并成一个（`美人`、`美少女` 都是「高颜值」）。

    先查资料专用的那张表，再落回作品标签的表，用户的决定排最前
    （`genre_taxonomy.resolve_profile_tag`）；一格写了几个的拆开，不作为标签的不出。
    表里还没收录的照原文列：那是站上确有的说法，丢掉就少了一项读数。
    """
    tags: list[str] = []
    for tag in raw:
        text = " ".join(str(tag).split())
        if not text:
            continue
        mapped = resolve_profile_tag(text, decisions)
        for shown in (text,) if mapped is None else mapped:
            if shown not in tags:
                tags.append(shown)
    return tags


def page_names(row: dict | None) -> dict:
    """资料页上的名字：{name, reading, aliases}；`aliases` 一个写法一项，带着渠道注记。"""
    if not row:
        return {"name": "", "reading": "", "aliases": []}
    raw = row.get("raw") or {}
    cells = raw.get("別名") or []
    return {"name": str(raw.get("名前") or ""),
            "reading": re.sub(r"\s+", "", str(row.get("kana") or "")),
            "aliases": [entry for cell in cells for entry in name_entries(str(cell))]}


def _alias_sources(connection: sqlite3.Connection, entity_id: int) -> dict[str, tuple[str, set]]:
    """归一键 → (写法, 这个写法登记过的全部来源)。同一个写法按来源分行，这里并回一条。"""
    found: dict[str, tuple[str, set]] = {}
    for alias, source in connection.execute(
            "SELECT alias,source FROM entity_alias WHERE entity_id=? ORDER BY confidence DESC,alias",
            (int(entity_id),)):
        entry = found.setdefault(normalize_entity_name(str(alias)), (str(alias), set()))
        entry[1].add(str(source).partition("@")[0])
    return found


def _reading(page: dict, ledger: dict[str, tuple[str, set]]) -> str:
    """读音：资料页上的那个；没有资料时取别名里纯平假名的那条，`r18:performer` 的在前。"""
    if page["reading"]:
        return page["reading"]
    kana = sorted((name for name, _sources in ledger.values() if _HIRAGANA.match(name)),
                  key=lambda name: "r18:performer" not in ledger[normalize_entity_name(name)][1])
    return kana[0] if kana else ""


def page_sections(page: dict, reading: str) -> list[tuple[str, list[dict]]]:
    """资料页上的名义，按页头的组排好：现名、旧名义、各渠道名义（人多的在前）。"""
    aliases = page["aliases"]
    notes: dict[str, list[dict]] = {}
    for entry in aliases:
        if entry.get("note"):
            notes.setdefault(entry["note"], []).append({"name": entry["name"]})
    return [(CURRENT, [{"name": page["name"], "reading": reading}]),
            (FORMER, [{"name": entry["name"], "reading": entry.get("reading", "")}
                      for entry in aliases if not entry.get("note")]),
            *sorted(notes.items(), key=lambda item: -len(item[1]))]


class _Groups:
    """往页头的组里一个个放名字：同键的只放一次，中日文规范名下不放纯拉丁写法。"""

    def __init__(self, canonical: str, reading: str):
        self.east_asian = bool(_EAST_ASIAN.search(canonical or ""))
        self.taken = {_key(canonical), _key(reading)} - {""}
        self.groups: list[dict] = []

    def keep(self, name: str) -> bool:
        key = _key(name)
        if not name or key in self.taken:
            return False
        if self.east_asian and _LATIN.search(name) and not _EAST_ASIAN.search(name):
            return False
        self.taken.add(key)
        return True

    def add(self, label: str, entries: list[dict]) -> None:
        names = [entry for entry in entries if self.keep(entry["name"])]
        if names:
            self.groups.append({"label": label, "names": names})


def name_groups(connection: sqlite3.Connection, entity_id: int, canonical: str,
                page: dict) -> dict | None:
    """名字那一行与「+N」浮层要的 {reading, shown, total, groups}；一个名字都没有返回 None。

    `shown` 是名字那一行在读音之后列的几个：先按资料页上的顺序（主名、别名栏自上而下），
    再接账本里另有的写法。
    """
    ledger = _alias_sources(connection, entity_id)
    reading = _reading(page, ledger)
    sorter = _Groups(canonical, reading)
    for label, entries in page_sections(page, reading) if page["name"] or page["aliases"] else []:
        sorter.add(label, entries)
    order = {name: index for index, name in enumerate(
        [page["name"], *(entry["name"] for entry in page["aliases"])])}
    on_page = sorted((entry["name"] for group in sorter.groups for entry in group["names"]),
                     key=lambda name: order.get(name, len(order)))
    sorter.add(OTHER if sorter.groups else "",
               [{"name": name} for name, sources in ledger.values() if not sources <= HIDDEN_SOURCES])
    if not sorter.groups and not reading:
        return None
    everyone = on_page + [entry["name"] for group in sorter.groups
                          if group["label"] in (OTHER, "") for entry in group["names"]]
    return {"reading": reading, "shown": everyone[:SHOWN], "total": len(everyone),
            "groups": sorter.groups}


def _has_table(connection: sqlite3.Connection, table: str = TABLE) -> bool:
    """迁移 `0036` 应用之前账本里没有这张表：页头照常出，只是没有资料。"""
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone() is not None


def profiled(connection: sqlite3.Connection) -> set[int]:
    """有资料行的女优；骨架据此给页头右侧留出资料表的位置。"""
    if not _has_table(connection):
        return set()
    return {int(row[0]) for row in connection.execute(
        f"SELECT p.entity_id FROM {TABLE} p JOIN entity e ON e.id=p.entity_id"
        " AND e.kind IN ('performer','creator')")}


def header(connection: sqlite3.Connection, entity_id: int, canonical: str,
           today: date | None = None) -> dict:
    """`q_entity` 给女优页并进去的两项。"""
    row = read_profile(connection, entity_id) if _has_table(connection) else None
    decisions = load_genre_decisions(connection) if row and _has_table(connection, "genre_decision") else None
    return {"profile": {**facts(row, today or date.today(), decisions), **about(row), **side(row)},
            "name_groups": name_groups(connection, entity_id, canonical, page_names(row))}
