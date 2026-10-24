/* 首页筛选栏的几份聚合（`src/catalog-bars.ts`）：键、回退、30 秒复用、口径变了重取、刷新强制重取、续页，
 * 以及关注那一排自成一个键、读不出来时只让这一排空着。 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import {
  BARS_STALE_MS, dropBars, facetsKey, fetchBars, fetchTopsPage, followingKey, loadFollowing, loadTops, topsKey,
  topsPageKey,
} from '../src/catalog-bars';
import { queryClient } from '../src/query';

const EMPTY = { performers: [], studios: [] };
const FULL = { performers: [{ k: '某人' }], studios: [] };
const FACETS = { tags: [], locations: [] };
const FOLLOWING = [{ k: '演示关注者', key: 'entity:9', entity_id: 9, avatar: '', avatar_fallback: '' }];

/** 按地址回包的 fetch；`hold` 为真时先不回，等 `release()`。 */
function serve(answer: (url: string) => unknown, hold = false) {
  const waiting: (() => void)[] = [];
  const fetcher = vi.fn((url: string) => new Promise((resolve) => {
    const reply = () => resolve({ ok: true, status: 200, json: async () => answer(url) });
    if (hold) waiting.push(reply); else reply();
  }));
  vi.stubGlobal('fetch', fetcher);
  return { fetcher, urls: () => fetcher.mock.calls.map(([url]) => url), release: () => waiting.splice(0).forEach((run) => run()) };
}
const answer = (tops: unknown) => (url: string) => url.startsWith('/api/facets') ? FACETS
  : url.startsWith('/api/follow/authors') ? { items: FOLLOWING } : tops;
/** 只数女优与厂牌那两族：关注那一排每一趟也跟着发，但它的键与口径无关。 */
const barsUrls = (urls: unknown[]) => urls.filter((url) => !String(url).startsWith('/api/follow/authors'));
const followingUrls = (urls: unknown[]) => urls.filter((url) => String(url).startsWith('/api/follow/authors'));

beforeEach(() => { vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(0) });
afterEach(() => { queryClient.clear(); vi.useRealTimers(); vi.unstubAllGlobals() });

it('各存在自己的键下：facets 按口径，tops 按请求方传的参数与口径，关注那一排不带参数', async () => {
  serve(answer(FULL));
  await fetchBars('state=flagged', 'n=60&seed=s&state=flagged');
  expect(queryClient.getQueryData(facetsKey('state=flagged'))).toEqual(FACETS);
  expect(queryClient.getQueryData(topsKey('n=60&seed=s&state=flagged', 'state=flagged'))).toEqual(FULL);
  expect(queryClient.getQueryData(followingKey())).toEqual(FOLLOWING);
  expect(facetsKey('a').slice(0, 2)).toEqual(['facets', 'a']);
  expect(topsKey('p', 'a').slice(0, 3)).toEqual(['tops', 'p', 'a']);
  expect(followingKey().slice(0, 1)).toEqual(['following']);
});

it('状态页两排都空时退回全库口径再取一次，键仍按请求方传的那一份', async () => {
  const net = serve((url) => (url.includes('state=') ? EMPTY : FULL));
  const [, tops] = await fetchBars('state=flagged', 'n=60&seed=s&state=flagged');
  expect(barsUrls(net.urls())).toEqual(
    ['/api/facets?state=flagged', '/api/tops?n=60&seed=s&state=flagged', '/api/tops?n=60&seed=s']);
  expect(tops).toEqual(FULL);
  expect(queryClient.getQueryData(topsKey('n=60&seed=s&state=flagged', 'state=flagged'))).toEqual(FULL);
});

it('没有状态页参数、或者收窄后仍有人时不回退', async () => {
  const empty = serve(() => EMPTY);
  expect(await loadTops('n=60&seed=s')).toEqual(EMPTY);
  expect(empty.urls()).toEqual(['/api/tops?n=60&seed=s']);
  const full = serve(() => FULL);
  expect(await loadTops(new URLSearchParams({ n: '60', state: 'flagged' }))).toEqual(FULL);
  expect(full.urls()).toEqual(['/api/tops?n=60&state=flagged']);
});

it('同一份口径 30 秒内复用，过了 30 秒重取', async () => {
  const net = serve(answer(FULL));
  await fetchBars('', 'n=60&seed=s');
  vi.setSystemTime(BARS_STALE_MS - 1);
  await fetchBars('', 'n=60&seed=s');
  expect(barsUrls(net.urls())).toEqual(['/api/facets', '/api/tops?n=60&seed=s']);
  expect(followingUrls(net.urls())).toHaveLength(1);
  vi.setSystemTime(BARS_STALE_MS + 1);
  await fetchBars('', 'n=60&seed=s');
  expect(barsUrls(net.urls())).toHaveLength(4);
  expect(followingUrls(net.urls())).toHaveLength(2);
});

it('口径变了两份都重取：tops 的参数没变也跟着取；关注那一排不跟着取', async () => {
  const net = serve(answer(FULL));
  await fetchBars('', 'n=60&seed=s');
  await fetchBars('tag=a', 'n=60&seed=s');
  expect(barsUrls(net.urls()).slice(2)).toEqual(['/api/facets?tag=a', '/api/tops?n=60&seed=s']);
  expect(followingUrls(net.urls())).toHaveLength(1);
});

it('同时要同一份只发一次请求', async () => {
  const net = serve(answer(FULL), true);
  const both = Promise.all([fetchBars('', 'n=60'), fetchBars('', 'n=60')]);
  await vi.waitFor(() => expect(net.fetcher).toHaveBeenCalledTimes(3));
  net.release();
  const [first, second] = await both;
  expect(second).toEqual(first);
  expect(net.fetcher).toHaveBeenCalledTimes(3);
});

it('dropBars 之后 30 秒内也真的重取，关注那一排同样重取', async () => {
  const net = serve(answer(FULL));
  await fetchBars('', 'n=60&seed=s');
  dropBars();
  await fetchBars('', 'n=60&seed=s');
  expect(barsUrls(net.urls())).toEqual(
    ['/api/facets', '/api/tops?n=60&seed=s', '/api/facets', '/api/tops?n=60&seed=s']);
  expect(followingUrls(net.urls())).toHaveLength(2);
});

it('关注那一排读不出来只让这一排空着，两排头像照常', async () => {
  const fetcher = vi.fn((url: string) => Promise.resolve(String(url).startsWith('/api/follow/authors')
    ? { ok: false, status: 500, json: async () => ({ detail: '关注库读不出来' }) }
    : { ok: true, status: 200, json: async () => (String(url).startsWith('/api/facets') ? FACETS : FULL) }));
  vi.stubGlobal('fetch', fetcher);
  await expect(fetchBars('', 'n=60')).resolves.toEqual([FACETS, FULL, []]);
  /* 失败不进缓存：下一次照样再问一趟。 */
  expect(await loadFollowing()).toEqual([]);
  expect(followingUrls(fetcher.mock.calls.map(([url]) => url))).toHaveLength(2);
});

it('路上那一趟碰上 dropBars：等它的调用方照样拿到数据，下一次另发请求', async () => {
  const net = serve(answer(FULL), true);
  const before = fetchBars('', 'n=60');
  await vi.waitFor(() => expect(net.fetcher).toHaveBeenCalledTimes(3));
  dropBars();
  const after = fetchBars('', 'n=60');
  await vi.waitFor(() => expect(net.fetcher).toHaveBeenCalledTimes(6));
  net.release();
  await expect(before).resolves.toEqual([FACETS, FULL, FOLLOWING]);
  await expect(after).resolves.toEqual([FACETS, FULL, FOLLOWING]);
});

it('两排同时翻到同一页只发一次请求，与第一页的键分开', async () => {
  const net = serve(() => FULL, true);
  const both = Promise.all([fetchTopsPage('n=60&page=1'), fetchTopsPage('n=60&page=1')]);
  await vi.waitFor(() => expect(net.fetcher).toHaveBeenCalledTimes(1));
  net.release();
  const [performers, studios] = await both;
  expect(studios).toBe(performers);
  expect(net.urls()).toEqual(['/api/tops?n=60&page=1']);
  expect(queryClient.getQueryData(topsPageKey('n=60&page=1'))).toEqual(FULL);
  expect(topsPageKey('p')).not.toEqual(topsKey('p', ''));
});

it('续页 30 秒内复用，dropBars 之后重取，状态页空页照样退回全库口径', async () => {
  const net = serve((url) => (url.includes('state=') ? EMPTY : FULL));
  await fetchTopsPage('n=60&page=2&state=flagged');
  await fetchTopsPage('n=60&page=2&state=flagged');
  expect(net.urls()).toEqual(['/api/tops?n=60&page=2&state=flagged', '/api/tops?n=60&page=2']);
  dropBars();
  expect(await fetchTopsPage('n=60&page=2&state=flagged')).toEqual(FULL);
  expect(net.fetcher).toHaveBeenCalledTimes(4);
});
