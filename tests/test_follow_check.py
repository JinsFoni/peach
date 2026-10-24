"""「检查更新」这一段共用流程本身的测试。

Web 与命令行各写一遍必然不等价：其中一份会漏掉往回翻页、漏掉凭据到位后的强制
重取、不学官方渠道的作者别名。这里锁住「同一句检查更新在两处做同样的事」。
"""
import contextlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from peach.follow import FollowHistoryEnd, FollowSourceError, FollowSourceRateLimited
from peach.follow_check import plan_check, run_check
from peach.follow_stream import FollowMediaResolver
from peach.follow_secrets import CredentialError
from peach.follow_sources import FollowCandidate, SourceFetch, _BaseConnector
from peach.follow_store import FollowStore
from support.ledger import fresh_ledger


ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 9, 3, tzinfo=timezone.utc)


class _Credentials:
    """只回答「有没有配」的凭据仓库替身；不带任何真实凭据值。"""

    def __init__(self, providers=()):
        self._providers = set(providers)

    def load(self, provider):
        return object() if provider in self._providers else None


class _Connector:
    """单页替身：默认只有第 1 页，要更早的页就是尽头。首次采集因此会翻一页就停。"""

    def __init__(self, fetch=None, error=None, pages=(0,)):
        self._fetch, self._error = fetch, error
        self.pages = frozenset(pages)
        self.calls = []

    def fetch(self, ref, *, etag=None, last_modified=None, page=0):
        self.calls.append({"ref": ref, "etag": etag,
                           "last_modified": last_modified, "page": page})
        if self._error is not None:
            raise self._error
        if page not in self.pages:
            raise FollowHistoryEnd("没有更多历史内容")
        return self._fetch


def _fetch(provider="fanbox", ref="ffxivinitiala", **kwargs):
    base = dict(provider=provider, ref=ref, semantics="work",
                request_url=f"https://{ref}.fanbox.cc/")
    base.update(kwargs)
    return SourceFetch(**base)


class _CheckCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.connection = sqlite3.connect(fresh_ledger(self.root))
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.store = FollowStore(lambda: self.connection,
                                 sources_root=self.root / "sources")

    @contextlib.contextmanager
    def writer(self):
        yield self.store

    def _register(self, **kwargs):
        base = dict(provider="fanbox", ref="ffxivinitiala", label="Initiala",
                    url="https://ffxivinitiala.fanbox.cc/", semantics="work")
        base.update(kwargs)
        return self.store.register(**base)

    def _row(self, source_id):
        return next(dict(row) for row in self.store.sources()
                    if row["id"] == source_id)

    def _run(self, source_id, connector, **kwargs):
        return run_check(self._row(source_id), credentials=_Credentials(),
                         writer=self.writer,
                         connector_factory=lambda provider, **kw: connector,
                         moment=MOMENT, **kwargs)


class IncrementalCheckTests(_CheckCase):
    @contextlib.contextmanager
    def writer(self):
        with self.connection:
            yield self.store

    def _reader(self):
        database = self.connection.execute('PRAGMA database_list').fetchone()[2]
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return connection

    def _connector(self, enrich_one, pages=(0,)):
        class Connector(_BaseConnector):
            def fetch(self, ref, **kwargs):
                if kwargs.get('page') not in pages:
                    raise FollowHistoryEnd("没有更多历史内容")
                candidates, probed = self.enrich(tuple(
                    FollowCandidate(provider='fanbox', external_id=str(index),
                                    title=f'作品 {index}', partial=True)
                    for index in (1, 2)))
                return _fetch(candidates=candidates, probed=probed, etag='finished', raw_body=b'page')

            def _enrich_one(self, candidate):
                return enrich_one(candidate)

        return Connector(enrich_budget=2)

    def test_a_completed_item_is_visible_while_the_next_item_is_being_enriched(self):
        source = self._register()
        self.connection.commit()
        reader = self._reader()

        def enrich(candidate):
            if candidate.external_id == '1':
                saved = reader.execute('SELECT * FROM follow_item ORDER BY external_id').fetchall()
                self.assertEqual([row['external_id'] for row in saved], ['1', '2'])
                self.assertTrue(all('post_type' not in json.loads(row['metadata_json']) for row in saved))
            if candidate.external_id == '2':
                saved = reader.execute('SELECT * FROM follow_item ORDER BY external_id').fetchall()
                self.assertEqual([row['external_id'] for row in saved], ['1', '2'])
                self.assertEqual(json.loads(saved[0]['metadata_json'])['post_type'], 'image')
                self.assertNotIn('post_type', json.loads(saved[1]['metadata_json']))
                self.assertIsNone(saved[0]['evidence_path'])
                pending = reader.execute('SELECT * FROM follow_source WHERE id=?', (source,)).fetchone()
                self.assertIsNone(pending['last_checked_at'])
                self.assertIsNone(pending['etag'])
                self.assertEqual(pending['backfill_page'], 0)
            return replace(candidate, partial=False, extra={'post_type': 'image'})

        result = self._run(source, self._connector(enrich))
        self.assertEqual((result.outcome.discovered, result.outcome.added, result.outcome.updated),
                         (2, 2, 0))
        self.assertEqual(self._row(source)['etag'], 'finished')
        self.assertTrue(all(row[0] for row in reader.execute('SELECT evidence_path FROM follow_item')))
        again = self._run(source, self._connector(lambda candidate: candidate))
        self.assertEqual((again.outcome.added, again.outcome.updated), (0, 2))

    def test_a_rate_limit_keeps_completed_items_and_the_page_can_be_retried(self):
        source = self._register()
        self.connection.commit()

        def enrich(candidate):
            if candidate.external_id == '2':
                raise FollowSourceRateLimited('稍后再试', 120)
            return replace(candidate, partial=False, extra={'post_type': 'image'})

        result = self._run(source, self._connector(enrich))
        self.assertFalse(result.ok)
        reader = self._reader()
        saved = reader.execute('SELECT * FROM follow_item ORDER BY external_id').fetchall()
        self.assertEqual([row['external_id'] for row in saved], ['1', '2'])
        self.assertEqual(json.loads(saved[0]['metadata_json'])['post_type'], 'image')
        self.assertNotIn('post_type', json.loads(saved[1]['metadata_json']))
        row = self._row(source)
        self.assertEqual(row['last_status'], 'error')
        self.assertIsNone(row['etag'])
        self.assertEqual(row['backfill_page'], 0)
        retry = self._run(source, self._connector(lambda candidate: candidate))
        self.assertEqual((retry.outcome.added, retry.outcome.updated), (0, 2))
        self.assertEqual(len(self.store.items(source_id=source)), 2)

    def test_an_interrupted_history_page_keeps_items_without_advancing_its_cursor(self):
        source = self._register()
        self.connection.commit()

        def enrich(candidate):
            if candidate.external_id == '2':
                raise InterruptedError('任务中断')
            return candidate

        # 往回翻的是第 2 页；翻页途中被打断，已经收下的条目照常落库，游标不动。
        with self.assertRaises(InterruptedError):
            self._run(source, self._connector(enrich, pages=(1,)), older=True)
        self.assertEqual(len(self.store.items(source_id=source)), 2)
        self.assertEqual(self._row(source)['backfill_page'], 0)
        self.assertIsNone(self._row(source)['last_checked_at'])

    def test_detail_dates_enforce_the_persisted_history_boundary(self):
        """行上的日期是「未取得」时，详情给出的准确日期说了算：早于边界就不收。"""
        source = self._register()
        self.store.merge_source_metadata(source, {'initial_history_after': '2026-08-04T00:00:00Z'}, MOMENT)
        self.connection.commit()

        def enrich(candidate):
            self.assertEqual(len(self.store.items(source_id=source)), 0)
            return replace(candidate, partial=False, published_at='2025-01-01T00:00:00Z')

        result = self._run(source, self._connector(enrich))
        self.assertEqual(result.outcome.discovered, 0)
        self.assertEqual(len(self.store.items(source_id=source)), 0)


class SecondPhaseHandoffTests(_CheckCase):
    def test_the_skip_set_reaches_the_connector(self):
        """算出来的跳过集合要真的传给连接器，否则第二阶段照样按条目数付请求。"""
        built = []

        def factory(provider, **kwargs):
            built.append(kwargs)
            return _Connector(_fetch(provider="rule34paheal", ref="initiala"))

        source_id = self._register(provider="rule34paheal", ref="initiala",
                                   url="https://rule34.paheal.net/post/list/initiala/1")
        row = dict(self._row(source_id))
        row["force_media_reparse"] = False
        row["enrich_skip"] = frozenset({"7428820"})
        run_check(row, credentials=_Credentials(), writer=self.writer,
                  connector_factory=factory, moment=MOMENT)
        self.assertEqual(built[0]["enrich_skip"], frozenset({"7428820"}))

    def test_an_empty_skip_set_is_not_passed_at_all(self):
        """空集合不传：连接器的构造签名不该被一个恒为空的参数占着。"""
        built = []

        def factory(provider, **kwargs):
            built.append(kwargs)
            return _Connector(_fetch())

        source_id = self._register()
        row = dict(self._row(source_id))
        row["force_media_reparse"] = False
        row["enrich_skip"] = frozenset()
        run_check(row, credentials=_Credentials(), writer=self.writer,
                  connector_factory=factory, moment=MOMENT)
        self.assertNotIn("enrich_skip", built[0])


class PlanCheckTests(_CheckCase):
    def test_only_enabled_sources_are_planned(self):
        first = self._register()
        second = self._register(ref="other", url="https://other.fanbox.cc/")
        self.store.set_enabled(second, False)
        planned = plan_check(self.store, _Credentials())
        self.assertEqual([row["id"] for row in planned], [first])

    def test_paging_back_is_offered_only_where_it_actually_works(self):
        """官方渠道没有历史分页；把它列进往回翻页只会白打一次请求。"""
        self._register()
        archive = self._register(provider="kemono", ref="fanbox/1",
                                 url="https://kemono.cr/fanbox/user/1")
        planned = plan_check(self.store, _Credentials(), older=True,
                             backfill_providers=frozenset({"kemono"}))
        self.assertEqual([row["id"] for row in planned], [archive])

    def test_a_credential_that_just_arrived_forces_one_unconditional_refetch(self):
        """凭据到位而旧候选还标着 needs_credential 时必须绕过条件请求游标。

        否则上游回 304，旧解析结果永久不变——配了凭据却什么都没变，用户没法自己
        看出原因。命令行和 Web 走同一份实现，这一条两处都成立。
        """
        source_id = self._register(provider="rule34xxx", ref="tag",
                                   url="https://rule34.xxx/?tags=tag")
        self.store.record(source_id, _fetch(
            provider="rule34xxx", ref="tag", candidates=(
                FollowCandidate(provider="rule34xxx", external_id="1", title="a",
                                extra={"media_needs_credential": True}),)),
            moment=MOMENT)
        planned = plan_check(self.store, _Credentials({"rule34xxx"}))
        self.assertTrue(planned[0]["force_media_reparse"])
        without = plan_check(self.store, _Credentials())
        self.assertFalse(without[0]["force_media_reparse"],
                         "没配凭据就没有可生效的重解析，不该白打一次无条件请求")

    def test_paging_back_never_forces_a_refetch(self):
        """往回翻的那一页本来就没有游标，强制重取没有意义。"""
        source_id = self._register(provider="rule34xxx", ref="tag",
                                   url="https://rule34.xxx/?tags=tag")
        self.store.record(source_id, _fetch(
            provider="rule34xxx", ref="tag", candidates=(
                FollowCandidate(provider="rule34xxx", external_id="1", title="a",
                                extra={"media_needs_credential": True}),)),
            moment=MOMENT)
        planned = plan_check(self.store, _Credentials({"rule34xxx"}), older=True,
                             backfill_providers=frozenset({"rule34xxx"}))
        self.assertFalse(planned[0]["force_media_reparse"])

    def test_details_already_taken_are_left_out_of_the_second_phase(self):
        """补齐过的条目不再打详情页。判据按来源各自声明的那一处算。"""
        source_id = self._register(provider="rule34paheal", ref="initiala",
                                   url="https://rule34.paheal.net/post/list/initiala/1")
        self.store.record(source_id, _fetch(
            provider="rule34paheal", ref="initiala", candidates=(
                FollowCandidate(provider="rule34paheal", external_id="1", title="a",
                                published_at="2026-08-26T15:21:00Z"),
                FollowCandidate(provider="rule34paheal", external_id="2", title="b",
                                partial=True),)),
            moment=MOMENT)
        planned = plan_check(self.store, _Credentials())
        self.assertEqual(planned[0]["enrich_skip"], frozenset({"1"}))

    def test_kemono_skips_probing_every_post_it_already_kept(self):
        """kemono 的探测是收录判定：库里有的帖子当初判过保留，不必再探。"""
        source_id = self._register(provider="kemono", ref="fanbox/1",
                                   url="https://kemono.cr/fanbox/user/1")
        self.store.record(source_id, _fetch(
            provider="kemono", ref="fanbox/1", candidates=(
                FollowCandidate(provider="kemono", external_id="1", title="a",
                                published_at="2026-08-26T15:21:00Z"),)),
            moment=MOMENT)
        self.assertEqual(plan_check(self.store, _Credentials())[0]["enrich_skip"],
                         frozenset({"1"}))

    def test_a_source_that_never_opens_detail_pages_never_computes_a_skip_set(self):
        source_id = self._register(provider="subscribestar", ref="creator",
                                   url="https://subscribestar.adult/creator")
        self.store.record(source_id, _fetch(
            provider="subscribestar", ref="creator", candidates=(
                FollowCandidate(provider="subscribestar", external_id="1", title="a",
                                published_at="2026-08-26T15:21:00Z"),)),
            moment=MOMENT)
        self.assertEqual(plan_check(self.store, _Credentials())[0]["enrich_skip"],
                         frozenset())


class RunCheckTests(_CheckCase):
    def test_a_normal_check_reads_the_first_page_with_the_stored_cursors(self):
        source_id = self._register()
        self.store.record(source_id, _fetch(etag='W/"1"', last_modified="Mon"),
                          moment=MOMENT)
        connector = _Connector(_fetch(not_modified=True))
        result = self._run(source_id, connector)
        self.assertTrue(result.ok)
        self.assertEqual(connector.calls, [{"ref": "ffxivinitiala", "etag": 'W/"1"',
                                            "last_modified": "Mon", "page": 0}])
        self.assertTrue(result.outcome.not_modified)

    def test_paging_back_asks_for_the_next_page_and_drops_the_cursors(self):
        source_id = self._register(provider="kemono", ref="fanbox/1",
                                   url="https://kemono.cr/fanbox/user/1")
        page = _fetch(provider="kemono", ref="fanbox/1")
        connector = _PagedConnector({1: page, 2: page})
        result = self._run(source_id, connector, older=True)
        self.assertEqual(result.page, 1)
        self.assertTrue(result.older)
        self.assertEqual(connector.calls[0]["page"], 1)
        # 第二次再往回，接着上一次走到的位置。
        connector = _PagedConnector({1: page, 2: page})
        self.assertEqual(self._run(source_id, connector, older=True).page, 2)

    def test_the_end_of_history_is_recorded_not_reported_as_a_failure(self):
        source_id = self._register(provider="kemono", ref="fanbox/1",
                                   url="https://kemono.cr/fanbox/user/1")
        result = self._run(source_id, _Connector(error=FollowHistoryEnd("400")),
                           older=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.exhausted)
        self.assertEqual(result.message, "没有更多历史内容")
        self.assertEqual(self._row(source_id)["last_status"], "not_modified")
        self.assertEqual(self._row(source_id)["backfill_page"], 0,
                         "翻到尽头不推进游标，否则下一次会跳过真实存在的一页")

    def test_a_missing_credential_is_its_own_status(self):
        source_id = self._register()
        result = self._run(source_id,
                           _Connector(error=CredentialError("需要 user_id 与 api_key")))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unauthorized")
        self.assertEqual(result.error, "需要 user_id 与 api_key")
        self.assertEqual(self._row(source_id)["last_status"], "unauthorized")
        self.assertNotIn("api_key", str(result.fetch or ""))

    def test_a_source_failure_is_a_return_value_not_an_exception(self):
        """逐条独立成败：一个来源被挡住不该让其余来源的更新一起消失。"""
        source_id = self._register()
        result = self._run(source_id, _Connector(error=FollowSourceError("HTTP 503")))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertEqual(self._row(source_id)["last_status"], "error")

    def test_a_rate_limited_source_pauses_the_rest_of_that_site(self):
        """被限的是整站：同一站排在后面的来源不再请求，也不被记成检查失败。"""
        clock = [1000.0]
        cooldown = FollowMediaResolver(transport=None, clock=lambda: clock[0])
        first = self._register()
        second = self._register(ref="another", url="https://another.fanbox.cc/")
        limited = self._run(first, _Connector(error=FollowSourceRateLimited("HTTP 429", 120)),
                            cooldown=cooldown)
        self.assertFalse(limited.ok)
        self.assertEqual(self._row(first)["last_status"], "error")
        connector = _Connector(_fetch(ref="another"))
        skipped = self._run(second, connector, cooldown=cooldown)
        self.assertFalse(skipped.ok)
        self.assertIn("冷却", skipped.error)
        self.assertEqual(connector.calls, [])
        self.assertIsNone(self._row(second)["last_status"])
        clock[0] += 121
        self.assertTrue(self._run(second, connector, cooldown=cooldown).ok)
        self.assertEqual(len(connector.calls), 1)

    def test_an_official_profile_handle_is_learned_from_one_unambiguous_author(self):
        """fanbox 的 ref 就是作者本人的手柄，可以直接学成别名。

        这一条要落在共用的 `run_check` 里而不是 Web 那层：别的调用方抓同一条来源
        就不会学，同一个人会一直显示成两个作者。
        """
        source_id = self._register()
        result = self._run(source_id, _Connector(_fetch(candidates=(
            FollowCandidate(provider="fanbox", external_id="1", title="a",
                            author="Initiala"),
            FollowCandidate(provider="fanbox", external_id="2", title="b",
                            author="Initiala"),
        ))))
        self.assertEqual(result.author_alias_learned,
                         {"canonical": "Initiala", "alias": "ffxivinitiala",
                          "source": "official:fanbox"})
        mapping, _groups = self.store.author_aliases()
        self.assertEqual(mapping["ffxivinitiala"], "initiala")

    def test_two_different_authors_in_one_fetch_teach_nothing(self):
        source_id = self._register()
        result = self._run(source_id, _Connector(_fetch(candidates=(
            FollowCandidate(provider="fanbox", external_id="1", title="a",
                            author="Initiala"),
            FollowCandidate(provider="fanbox", external_id="2", title="b",
                            author="Someone Else"),
        ))))
        self.assertIsNone(result.author_alias_learned)
        self.assertEqual(self.store.author_aliases(), ({}, []))

    def test_an_archive_ref_is_never_learned_as_an_author_name(self):
        """`fanbox/30917150` 里的数字 id 不是名字，学成别名会造出一个假作者。"""
        source_id = self._register(provider="kemono", ref="fanbox/30917150",
                                   url="https://kemono.cr/fanbox/user/30917150")
        result = self._run(source_id, _Connector(_fetch(
            provider="kemono", ref="fanbox/30917150", candidates=(
                FollowCandidate(provider="kemono", external_id="1", title="a",
                                author="Initiala"),))))
        self.assertIsNone(result.author_alias_learned)
        self.assertEqual(self.store.author_aliases(), ({}, []))


class _PagedConnector(_Connector):
    """按页码回不同结果的连接器替身；没登记的页就是翻到了尽头。"""

    def __init__(self, pages):
        super().__init__()
        self.pages = pages

    def fetch(self, ref, *, etag=None, last_modified=None, page=0):
        self.calls.append({"ref": ref, "etag": etag,
                           "last_modified": last_modified, "page": page})
        value = self.pages.get(page, FollowHistoryEnd("没有更多历史内容"))
        if isinstance(value, Exception):
            raise value
        return value


def _dated(prefix, count, start, provider="kemono"):
    """`count` 条从 `start` 起逐日往前的候选，新的在前。"""
    return tuple(FollowCandidate(provider=provider, external_id=f"{prefix}-{n}", title=f"{prefix}-{n}",
                                 published_at=(start - timedelta(days=n)).isoformat())
                 for n in range(count))


class InitialHistoryTests(_CheckCase):
    """首次采集按「采集范围」翻页：范围内的全收，范围外的连一页都不多取。"""

    def sample(self):
        return tuple(FollowCandidate(provider='fanbox', external_id=key, title=key,
                                     published_at=date) for key, date in (
            ('old', '2026-01-01T00:00:00Z'),
            ('boundary', '2026-08-04T00:00:00Z'),
            ('new', '2026-09-02T00:00:00Z'), ('undated', None)))

    def _kemono(self):
        return self._register(provider='kemono', ref='fanbox/1',
                              url='https://kemono.cr/fanbox/user/1')

    def _page(self, prefix, count, start):
        return _fetch(provider='kemono', ref='fanbox/1',
                      candidates=_dated(prefix, count, start))

    def test_the_boundary_is_the_moment_minus_the_range(self):
        """边界是采集那一刻往前 30 天，不跟着收下的最早一条往下滑。"""
        source = self._register()
        connector = _Connector(_fetch(candidates=self.sample()))
        result = self._run(source, connector, initial_days=30)
        self.assertEqual(result.history_skipped, 1)
        stored = {item.external_id for item in self.store.items(source_id=source)}
        self.assertTrue({'boundary', 'new', 'undated'} <= stored)
        self.assertNotIn('old', stored, "整条早于边界的不收")
        self.assertEqual([call['page'] for call in connector.calls], [0, 1],
                         "第一页收完接着往前翻，翻到站点说没有更早的为止")
        self.assertEqual(json.loads(self._row(source)['metadata_json'])['initial_history_after'],
                         (MOMENT - timedelta(days=30)).isoformat())

    def test_an_unlimited_range_pages_to_the_end_of_history(self):
        source = self._kemono()
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        pages = {n: self._page(f'p{n}', 12, start - timedelta(days=20 * n)) for n in range(4)}
        connector = _PagedConnector(pages)
        result = self._run(source, connector, initial_days=0)
        self.assertEqual([call['page'] for call in connector.calls], [0, 1, 2, 3, 4],
                         "一路翻到站点说没有更早的为止")
        self.assertEqual(result.outcome.added, 48, "往前翻的每一页都整页收下")
        row = self._row(source)
        self.assertEqual(row['backfill_page'], 3, "游标停在最后一页")
        self.assertEqual(row['last_status'], 'not_modified', "翻到尽头是一次成功的检查")

    def test_a_day_range_stops_at_the_page_that_is_entirely_before_the_boundary(self):
        source = self._kemono()
        pages = {0: self._page('a', 12, datetime(2026, 9, 2, tzinfo=timezone.utc)),
                 1: self._page('b', 12, datetime(2026, 8, 20, tzinfo=timezone.utc)),
                 2: self._page('c', 12, datetime(2026, 6, 1, tzinfo=timezone.utc))}
        connector = _PagedConnector(pages)
        result = self._run(source, connector, initial_days=30)
        self.assertEqual([call['page'] for call in connector.calls], [0, 1, 2],
                         "第 2 页整页在边界外，不必再往后翻")
        self.assertEqual(result.outcome.added, 24)
        self.assertEqual(result.history_skipped, 12)
        self.assertEqual(self._row(source)['backfill_page'], 2)

    def test_a_range_with_nothing_in_it_collects_nothing(self):
        """范围内一条都没有就是没有：范围由用户定，不拿兜底的一页顶替它。"""
        source = self._kemono()
        connector = _PagedConnector({0: self._page('old', 5, datetime(2026, 1, 1, tzinfo=timezone.utc))})
        result = self._run(source, connector, initial_days=30)
        self.assertEqual([call['page'] for call in connector.calls], [0], "第一页整页在边界外就不往前翻")
        self.assertEqual(result.outcome.added, 0)
        self.assertEqual(result.history_skipped, 5)
        self.assertEqual(len(self.store.items(source_id=source)), 0)

    def test_a_later_check_reads_the_first_page_only(self):
        source = self._kemono()
        self._run(source, _PagedConnector({0: self._page('a', 3, datetime(2026, 9, 2, tzinfo=timezone.utc)),
                                           1: self._page('b', 3, datetime(2026, 8, 20, tzinfo=timezone.utc))}),
                  initial_days=0)
        connector = _PagedConnector({0: self._page('c', 3, datetime(2026, 9, 3, tzinfo=timezone.utc)),
                                     1: self._page('d', 3, datetime(2026, 8, 20, tzinfo=timezone.utc))})
        self._run(source, connector, initial_days=0)
        self.assertEqual([call['page'] for call in connector.calls], [0], "常规检查只看第一页")

    def test_paging_stops_at_the_page_brake(self):
        """分页失控的站点：翻到上限就停，一趟检查不会变成无底洞。"""
        source = self._kemono()
        pages = {n: self._page(f'p{n}', 1, datetime(2026, 9, 2, tzinfo=timezone.utc)) for n in range(6)}
        connector = _PagedConnector(pages)
        with mock.patch('peach.follow_check.MAX_HISTORY_PAGES', 3):
            result = self._run(source, connector, initial_days=0)
        self.assertEqual([call['page'] for call in connector.calls], [0, 1, 2, 3])
        self.assertTrue(result.ok)

    def test_failed_first_check_retries_with_the_same_history_boundary(self):
        source = self._register()
        self._run(source, _Connector(error=FollowSourceError('offline')), initial_days=30)
        result = self._run(source, _Connector(_fetch(candidates=self.sample())), initial_days=0)
        self.assertEqual(result.history_skipped, 1)
        self.assertEqual(json.loads(self._row(source)['metadata_json'])['initial_history_after'],
                         (MOMENT - timedelta(days=30)).isoformat(),
                         "第一次没成的来源，边界照旧沿用第一次定下的那个")

    def test_existing_sources_and_unlimited_initial_checks_keep_all_dates(self):
        source = self._register()
        self.store.record(source, _fetch(), moment=MOMENT)
        result = self._run(source, _Connector(_fetch(candidates=self.sample())), initial_days=7)
        self.assertEqual(result.history_skipped, 0)
        source = self._register(ref='another')
        result = self._run(source, _Connector(_fetch(ref='another', candidates=self.sample())), initial_days=0)
        self.assertEqual(result.history_skipped, 0)

    def test_old_list_entries_do_not_spend_detail_requests(self):
        from peach.follow_sources import _BaseConnector
        connector = _BaseConnector(enrich_budget=10)
        connector.history_after = datetime(2026, 8, 4, tzinfo=timezone.utc)
        visited = []
        connector._enrich_one = lambda candidate: visited.append(candidate.external_id) or candidate
        candidates, probed = connector.enrich(self.sample())
        self.assertEqual(visited, ['boundary', 'new', 'undated'])
        self.assertEqual(probed, 3)
        self.assertEqual(connector.history_skipped, 1)


if __name__ == "__main__":
    unittest.main()
