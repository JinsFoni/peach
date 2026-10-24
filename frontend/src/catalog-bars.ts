/* 首页筛选栏与侧栏读的那几份聚合：`/api/facets`（侧栏各组与标签条）、`/api/tops`（女优、厂牌两排）
 * 和 `/api/follow/authors`（关注那一排）。存在全站那一个 `QueryClient` 里（`@peach/query`），
 * 壳启动时直接取，不等 React 包。
 *
 * 键，参数串都由壳按当前语境算好递进来，查询函数不读壳的变量：
 * - `['facets', 口径串, 代次]`：口径是 JAV、资料页或详情的范围、状态页与那一组筛选。
 * - `['tops', 参数串, 口径串, 代次]`：参数串是请求方传的那一份（条数、种子、JAV、状态页），回退到全库口径
 *   之后键也不变。键里另带口径串：两份是一对，口径一变两份一起重取，侧栏刷新计数时上面那两排也照样取一次。
 * - `['tops', 参数串, 代次]`：续页（参数串带 `page`）。回包只由参数串决定，两排同时要同一页只发一次。
 * - `['following', 代次]`：关注那一排。不带参数——被关注的人与此刻的筛选无关，翻页回来还是同一批。
 *
 * 同一份 30 秒内直接复用，同时要同一份只发一次请求。写操作、换一批、回到首页由壳调 `dropBars()`：
 * 代次加一，下一次一定真的发请求；路上那一趟照样交给正在等它的调用方。 */
import { api } from '@peach/legacy/core';

import { queryClient } from './query';

/** 这几份聚合复用多久：换页回来不重取，半分钟后再进页就按新数据库取。 */
export const BARS_STALE_MS = 30_000;

let revision = 0;

export const facetsKey = (scope: string, generation = revision) => ['facets', scope, generation] as const;
export const topsKey = (params: string, scope: string, generation = revision) =>
  ['tops', params, scope, generation] as const;
export const topsPageKey = (params: string, generation = revision) => ['tops', params, generation] as const;
export const followingKey = (generation = revision) => ['following', generation] as const;

export interface TopsData { performers: unknown[]; studios: unknown[]; [field: string]: unknown }
export type FacetsData = Record<string, unknown>;

/** 关注那一排的一位：`/api/follow/authors` 的一行，只取名字、圆头像与绑定到的实体。
 *  `key` 是身份键（`entity:3` 或作者名），同一人在几个站点上的两条来源在这里是一行。 */
export interface FollowAuthorRow {
  k: string;
  key: string;
  entity_id?: number | null;
  avatar?: string;
  avatar_fallback?: string;
}

/** 这一排摆多少人。它是入口不是名册——完整名单在关注管理里，这里按条目数给最多的那几位。 */
export const FOLLOWING_ROW_LIMIT = 24;

/** 取回关注那一排。关注库读失败只让这一排不画：首页顶部那两排是目录自己的聚合，不该被它拖住。
 *  失败不进缓存（查询失败不落 `staleTime`），下一次 `buildBars` 照样重试。 */
export function loadFollowing(): Promise<FollowAuthorRow[]> {
  return queryClient.fetchQuery({
    queryKey: followingKey(),
    queryFn: async () => {
      const data = await api(`/api/follow/authors?limit=${FOLLOWING_ROW_LIMIT}`) as { items?: FollowAuthorRow[] };
      return data.items || [];
    },
    staleTime: BARS_STALE_MS,
  }).catch(() => []);
}

/* 状态页把顶部两排收窄到本页口径，为的是不列出「在这一页一个作品都没有」的人和厂牌。但集合窄到聚合
   结果为空时（「已标记」常年只有几条），收窄就把整排一并收走了：同一条筛选条上换一格，页面顶上凭空少两层，
   读起来是跳去了另一个页面而不是换了筛选。空了就退回全库口径。这一排点开的是实体页，本来就要离开当前
   状态，它回答的从来不是「这一页里有谁」，而是「接下来去看谁」。续页（壳的 `moreTops`）也走这里。 */
export async function loadTops(params: string | URLSearchParams): Promise<TopsData> {
  const query = String(params);
  const scoped = await api(`/api/tops?${query}`) as TopsData;
  const wide = new URLSearchParams(query);
  if (scoped.performers.length || scoped.studios.length || !wide.has('state')) return scoped;
  wide.delete('state');
  return await api(`/api/tops?${wide}`) as TopsData;
}

/** 两份一起取，各自命中各自的缓存；壳等两份都到了再一次画完。 */
/** 这几份一起取，各自命中各自的缓存；壳等它们都到了再一次画完。 */
export function fetchBars(scope: string, topsParams: string): Promise<[FacetsData, TopsData, FollowAuthorRow[]]> {
  return Promise.all([
    queryClient.fetchQuery({
      queryKey: facetsKey(scope),
      queryFn: () => api(`/api/facets${scope ? `?${scope}` : ''}`) as Promise<FacetsData>,
      staleTime: BARS_STALE_MS,
    }),
    queryClient.fetchQuery({
      queryKey: topsKey(topsParams, scope),
      queryFn: () => loadTops(topsParams),
      staleTime: BARS_STALE_MS,
    }),
    loadFollowing(),
  ]);
}

/** 顶部一排的下一页。两排各记各的页号，翻到同一页时参数串相同，共用一次请求。 */
export function fetchTopsPage(params: string): Promise<TopsData> {
  return queryClient.fetchQuery({
    queryKey: topsPageKey(params),
    queryFn: () => loadTops(params),
    staleTime: BARS_STALE_MS,
  });
}

/** 让这几族已取的那些全部作废：代次加一，下一次 `fetchBars` 换键重取。
 *
 *  不整族移除：移除会静默取消路上那一趟，正在等它的调用方拿到的是一个 `CancelledError`。
 *  也不用 `invalidateQueries`：没有观察者的查询作废以后，路上那一趟会被下一次 `fetchQuery` 接着用，
 *  手动刷新就没有真的发请求。旧代次里已经落定的顺手移除，路上那几条回来以后没人再读，到 `gcTime` 回收。 */
export function dropBars(): void {
  revision += 1;
  const settled = (query: { state: { fetchStatus: string } }) => query.state.fetchStatus === 'idle';
  queryClient.removeQueries({ queryKey: ['facets'], predicate: settled });
  queryClient.removeQueries({ queryKey: ['tops'], predicate: settled });
  queryClient.removeQueries({ queryKey: ['following'], predicate: settled });
}
