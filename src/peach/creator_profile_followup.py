"""补发布账号资料：关注来源的人物页把头像、简介与资料表带进创作者档案（ADR-0115）。

关注来源绑出来的创作者（ADR-0096）名下只有作品列表，资料页上因此是一个名字、一个首字母、
零个视频——而她本人在站上公开着头像和一张资料表。这条后继把这些搬进来。

判据全是确定的，按 ADR-0052 直接落库，不产生候选：

1. **补谁**：绑了 `PROVIDERS` 里那几家来源的创作者，一个实体一条后继。取来源自己那个
   地址（`pornhub_profiles.page_url` 只把主机换成中文站），站上改版、这位注销都表现为取不到。
2. **写什么**：简介与资料表按 `pornhub_profiles.record` 的规整写 `performer_profile`，
   `source` 记 `auto:creator-profile@<任务行 id>`；这一行是人写的、或已经由别的来源写成，
   就只补头像，不动资料（`write_profile` 与这里的第一行判据是同一条）。
3. **头像**：取人物页那枚 `#getAvatar`，装成 `creator-<id>.img`，与自动补头像同一路
   （`avatar_picker.install`，`source_kind` 记 `official_profile`）。人自己挑过的图从不覆盖。
   站上这一枚只有 200×200，比自动补头像的像素门槛小——发布账号没有作品可供截脸，
   这一档已经是最清楚的一版。
4. **什么时候派**：关注检查结算时声明一批（ADR-0040）；建档后继刚建出来的那几位由它
   当场再派一次（`declared_for`），所以新关注的作者在这一次检查里就带上头像与资料，不必
   等到下一轮。
5. **多久再派**：写成了保 `REFRESH`（30 天）：体重、兴趣、订阅数这些都会变；一次都没写成
   只保 `RETRY_UNFETCHED`，站点抽风不该等到下个月。

资料行带批次号，`scripts/revert_auto_landing.py --source auto:creator-profile` 按
`performer_profile.source` 整批撤回；头像就是这张档案的普通头像，人可以在头像边车里换成
别的、或按取图那一路换掉。
"""
from __future__ import annotations

import time
from datetime import datetime

from . import avatar_picker, pornhub_profiles
from .follow import FollowSourceError
from .followups import Attempts, Followup, FollowupType, attempts_root, register
from .http import HttpxTransport
from .performer_profiles import read_profile, write_profile

TASK_KEY = "creator-profile"
TASK_LABEL = "补发布账号资料"
#: 这一条写下的一切都带这个归属串，撤回按它认。
SOURCE = "auto:creator-profile"

#: 站上给得出头像与资料表的来源。别家的连接器只有作品列表，加进来就是一条取不到的失败。
PROVIDERS = ("pornhub",)
#: 资料多久算旧：到期再取一次，人换了简介、体重、兴趣时页面上才跟得上。
REFRESH = 30 * 86400
#: 一次都没取到时多久再试。站点抽风、改了版都在这之列，不值得等到下个月。
RETRY_UNFETCHED = 3600
#: 一轮检查最多派这么多条。一个实体一次请求，几十个人同时开查也只是一串前台任务。
DECLARE_LIMIT = 24
#: 判据的版本，进指纹。换了判据就加一，跑过的创作者按新判据各再取一次。
RULE = 1

WRITTEN, HELD, FETCHED, NOTHING = "写入", "已有资料", "未取得", "未命中"


def followup_key(entity_id: int) -> str:
    return f"{TASK_KEY}:{int(entity_id)}"


def parse_key(key: str) -> int:
    prefix, _, raw = str(key).partition(":")
    if prefix != TASK_KEY or not raw.isdigit():
        raise ValueError(f"认不出这条补发布账号资料后继：{key}")
    return int(raw)


def _label(name: str) -> str:
    return f"{TASK_LABEL}：{name}" if name else TASK_LABEL


def sources(connection, entity_id: int) -> list[dict]:
    """这位名下能取资料的来源，按 id 排。"""
    marks = ",".join("?" * len(PROVIDERS))
    return [dict(row) for row in connection.execute(
        "SELECT id,provider,ref,url FROM follow_source WHERE entity_id=?"
        f" AND provider IN ({marks}) ORDER BY id", (int(entity_id), *PROVIDERS))]


def _ours(row: dict | None) -> bool:
    """这一行资料是不是这条后继写的。人写的、别的来源写的一律算别人的。"""
    return str((row or {}).get("source") or "").startswith(SOURCE)


def _stale(row: dict | None, *, now: float | None = None) -> bool:
    """资料取回之后过了 `REFRESH`。读不出时间就当旧了——大不了重取一次。"""
    stamp = str((row or {}).get("fetched_at") or "")
    try:
        fetched = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return True
    return (now if now is not None else time.time()) - fetched >= REFRESH


def needs(connection, avatar_root, entity_id: int, *, now: float | None = None) -> bool:
    """这位此刻缺什么：没有资料行、资料旧了，或盘上没有那张头像。

    头像与资料分开判：头像被删掉、装不上时不必等资料到期，下一轮就能再试一次。
    """
    if not sources(connection, entity_id):
        return False
    row = read_profile(connection, entity_id)
    if row is None or (_ours(row) and _stale(row, now=now)):
        return True
    return not avatar_picker.installed_digest(avatar_root, "creator", int(entity_id))


def fingerprint(connection, avatar_root, entity_id: int) -> str:
    """会让这条后继结论变的量：来源、资料取回的时间、盘上那张头像，加判据版本。"""
    refs = ",".join(f"{source['provider']}:{source['ref']}" for source in sources(connection, entity_id))
    row = read_profile(connection, entity_id) or {}
    installed = "1" if avatar_picker.installed_digest(avatar_root, "creator", int(entity_id)) else "0"
    return f"{refs}:{row.get('fetched_at') or ''}:{row.get('source') or ''}:{installed}:r{RULE}"


def plan(connection, avatar_root, attempts, *, limit: int = DECLARE_LIMIT,
         skip=(), entity_ids=None) -> list[Followup]:
    """此刻需要补资料的创作者，一个实体一条后继。跑过又没变的按指纹跳过。

    `entity_ids` 给定时只看这几位：建档那一轮刚建出来的创作者当场派出去（ADR-0115）。
    """
    if limit <= 0:
        return []
    skip = set(skip)
    if entity_ids is None:
        rows = connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind='creator' ORDER BY id")
    else:
        ids = [int(value) for value in entity_ids]
        if not ids:
            return []
        rows = connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind='creator' AND id IN ("
            + ",".join("?" * len(ids)) + ") ORDER BY id", ids)
    found: list[Followup] = []
    for row in rows:
        entity_id = int(row["id"])
        key = followup_key(entity_id)
        if key in skip or not needs(connection, avatar_root, entity_id):
            continue
        if attempts.settled(key, fingerprint(connection, avatar_root, entity_id)):
            continue
        found.append(Followup(key=key, task_key=TASK_KEY,
                              label=_label(str(row["canonical_name"] or ""))))
        if len(found) >= limit:
            break
    return found


def _declared_rows(found: list[Followup]) -> list[dict]:
    return [{"key": item.key, "task_key": item.task_key, "label": item.label} for item in found]


def declare(contract) -> list[dict]:
    """关注检查结束时声明该补资料的创作者（ADR-0040）。"""
    attempts = Attempts(attempts_root(contract.candidate_root))
    with contract.database.read_connection() as connection:
        found = plan(connection, contract.avatar_root, attempts)
    return _declared_rows(found)


def declared_for(contract, entity_ids) -> list[dict]:
    """刚建出来的创作者当场派一条补资料后继（ADR-0115）。

    关注检查结算时这些实体还不存在——建档本身是一条后继——所以那边的声明要到下一轮检查
    才看得见它们，资料页上就只挂着名字和首字母。建档那一轮把它们交给这里，一条链走完。
    """
    attempts = Attempts(attempts_root(contract.candidate_root))
    with contract.database.read_connection() as connection:
        found = plan(connection, contract.avatar_root, attempts, entity_ids=entity_ids)
    return _declared_rows(found)


def run(contract, key: str, handle) -> dict:
    """跑一条补资料后继，再把实体此刻的指纹记进 `Attempts`。"""
    summary = _run(contract, key, handle)
    entity_id = parse_key(key)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, contract.avatar_root, entity_id)
    landed = bool(summary.get("profile_written") or summary.get("avatar_installed"))
    retry = REFRESH if landed else (RETRY_UNFETCHED if summary.get("unavailable") else None)
    Attempts(attempts_root(contract.candidate_root)).record(
        key, current, str(summary.get("outcome") or ""), retry_after=retry)
    return summary


def _run(contract, key: str, handle) -> dict:
    """取这一页，写资料，装头像。摘要就是活动页上那一行。"""
    entity_id = parse_key(key)
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.read_connection() as connection:
        row = connection.execute("SELECT canonical_name FROM entity WHERE id=? AND kind='creator'",
                                 (entity_id,)).fetchone()
        found = sources(connection, entity_id)
    if row is None:
        return {"outcome": NOTHING, "detail": "档案已不存在"}
    name = str(row[0] or "")
    handle.progress(label=_label(name), throttle=0)
    if not found:
        return {"outcome": NOTHING, "detail": "关注来源已解绑"}
    source = found[0]
    try:
        page_url = pornhub_profiles.page_url(str(source["url"] or ""))
    except FollowSourceError as error:
        return {"outcome": NOTHING, "detail": str(error)}
    transport = HttpxTransport()
    try:
        final_url, page = pornhub_profiles.fetch_page(page_url, transport=transport)
        parsed = pornhub_profiles.parse(page)
        avatar, installed = _avatar(contract, transport, entity_id, parsed, source, batch)
    except FollowSourceError as error:
        return {"outcome": FETCHED, "detail": str(error), "unavailable": True}
    except avatar_picker.PickerError as error:
        avatar, installed = f"未取得：{error}", False
    finally:
        transport.close()

    with contract.database.write_transaction() as connection:
        held = read_profile(connection, entity_id)
        written = False
        if held is None or _ours(held):
            written = write_profile(connection, entity_id, pornhub_profiles.record(parsed),
                                    source=batch, source_url=final_url)
    if written or installed:
        contract.cache_bust()
    return {"name": name, "outcome": WRITTEN if written else HELD,
            "profile_written": written, "avatar_installed": installed,
            "avatar": avatar, "facts": len(parsed["about"]), "bio": bool(parsed["bio"]),
            "page": final_url}


def _avatar(contract, transport, entity_id: int, parsed: dict, source: dict,
            batch: str) -> tuple[str, bool]:
    """装站上那枚头像。盘上已经有图就不动——人挑过的尤其不能碰。"""
    if not parsed["avatar_url"]:
        return "未取得：页面没有给出头像", False
    if avatar_picker.installed_digest(contract.avatar_root, "creator", entity_id):
        return "已有头像", False
    body = avatar_picker.fetch_image(transport, parsed["avatar_url"])
    inspected = avatar_picker.accept_image(body)
    avatar_picker.install(
        contract.candidate_root / "provider-cache" / "performer-avatars",
        contract.avatar_root, "creator", entity_id, body,
        {"source": "pornhub model profile", "provider": source["provider"],
         "source_kind": "official_profile", "matched_name": parsed["name"],
         "name_source": source["provider"], "external_id": source["ref"],
         "batch": batch, "upstream_url": parsed["avatar_url"]},
    )
    return f"已装上 {inspected.width}×{inspected.height}", True


#: 写账本：资料进 `performer_profile`，头像落到盘上。取页在事务外做，写的那一下很短，
#: 照样走写账本那一条串行通道（ADR-0040 第二条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL, writes_ledger=True, run=run))
