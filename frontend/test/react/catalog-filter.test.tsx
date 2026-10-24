/* 首页筛选条岛：空馆藏与首屏等待时三排、标签条摆什么，以及关注那一排摆的是谁、点开去哪儿。
 *
 * 点选、续页、排序、版式与详情往返要真的取数和量布局，由 `frontend/e2e/catalog-filter.test.ts` 在真浏览器里走。 */
import { describe, expect, it, vi } from 'vitest';

import { CatalogFilterPage } from '../../src/react/catalog-filter/catalog-filter-page';
import {
  EMPTY_SLOTS, type CatalogFilterProps, type TierFollowing, type TierPerformer,
} from '../../src/react/catalog-filter/catalog-filter';
import { mount, mountRoot, pending, settle } from './render';

function props(patch: Partial<CatalogFilterProps> = {}): CatalogFilterProps {
  return {
    tiers: { key: 'k', performers: [], studios: [], following: [] }, empty: false, tags: [], tagFirst: 0,
    views: [{ k: '', label: '全部', href: '/' }], state: '', count: { total: 0, shown: 0 }, trash: false,
    sorts: [], layout: null, refreshing: false, offscreen: false, combo: [], comboHidden: true, comboHost: null,
    actions: {
      openEntity: vi.fn(), setView: vi.fn(), toggleTag: vi.fn(), clearFilter: vi.fn(), clearAll: vi.fn(),
      setSort: vi.fn(), reshuffle: vi.fn(async () => {}), setLayout: vi.fn(), moreTops: vi.fn(async () => []),
    },
    helpers: { wireDrag: vi.fn(), wireScroller: vi.fn() },
    ...patch,
  };
}

const count = (host: HTMLElement, selector: string) => host.querySelectorAll(selector).length;

describe('空馆藏', () => {
  it('两排头像与标签条各铺一行静止占位，不挂忙态、不接指针也不进读屏', async () => {
    const host = await mount(<CatalogFilterPage {...props({ empty: true })} />);
    for (const kind of ['performer', 'studio']) {
      expect(count(host, `[data-catalog-placeholder="${kind}"]`)).toBe(EMPTY_SLOTS);
    }
    expect(count(host, '[data-catalog-placeholder="tag"] > span')).toBe(EMPTY_SLOTS);
    expect([...host.querySelectorAll('[data-catalog-placeholder]')].every((node) => node.getAttribute('aria-hidden') === 'true'))
      .toBe(true);
    // 空馆藏说的是「这里将来会有东西」，不是「正在取」：没有微光。
    expect(host.querySelector('[data-loading]')).toBeNull();
    expect(host.querySelector('[data-catalog-tier] [aria-busy="true"]')).toBeNull();
  });

  it('收起时两排头像照留，浮层那两排不画', async () => {
    const host = await mount(<CatalogFilterPage {...props({ empty: true, offscreen: true })} />);
    expect(count(host, '[data-catalog-placeholder="performer"]')).toBe(EMPTY_SLOTS);
    expect(host.querySelector('[data-catalog-frame]')).toBeNull();
  });
});

describe('关注那一排', () => {
  const authors: TierFollowing[] = [
    { key: 'entity:9', name: '有主页的那位', initial: '有', avatar: '/entity-image?kind=creator&id=9&thumb=1&v=abc',
      fallback: '', href: '/creators/9' },
    { key: 'name:还没绑定的那位', name: '还没绑定的那位', initial: '还', avatar: '', fallback: '',
      href: `/follow?author=${encodeURIComponent('name:还没绑定的那位')}` },
  ];

  it('摆在三排的末尾，一枚是去她那页的链接，头像底下垫着首字母', async () => {
    const host = await mount(<CatalogFilterPage {...props({ tiers: {
      key: 'k', performers: [{ name: '某人', ringHtml: '' }], studios: [], following: authors,
    } })} />);
    const rows = [...host.querySelectorAll('[data-catalog-tier]')].map((row) => row.getAttribute('data-catalog-tier'));
    expect(rows).toEqual(['performers', 'following']);
    expect(host.querySelector('[data-tier-caption]')?.textContent).toBe('关注');
    const links = [...host.querySelectorAll('[data-tier-following]')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/creators/9', authors[1]!.href]);
    expect(links.map((link) => link.getAttribute('aria-label'))).toEqual(['关注：有主页的那位', '关注：还没绑定的那位']);
    // 名字那一格说这是谁；圆框只是装饰，取不到图时由全站那条回落把它摘掉，首字母露出来。
    expect(links[0]!.querySelector('[data-tier-name]')?.textContent).toBe('有主页的那位');
    expect(links[0]!.querySelector('[data-tier-ring]')?.getAttribute('aria-hidden')).toBe('true');
    expect(links[0]!.querySelector('img')?.getAttribute('src')).toBe(authors[0]!.avatar);
    expect(links[0]!.querySelector('img')?.getAttribute('data-drop')).toBe('self');
    expect(links[0]!.querySelector('[data-tier-initial]')?.textContent).toBe('有');
    expect(links[1]!.querySelector('img')).toBeNull();
    expect(links[1]!.querySelector('[data-tier-initial]')?.textContent).toBe('还');
  });

  it('没人被关注时这一排不画，三排容器也不为它留空', async () => {
    const host = await mount(<CatalogFilterPage {...props()} />);
    expect(host.querySelector('[data-catalog-tier="following"]')).toBeNull();
    expect(host.querySelector('[data-tier-caption]')).toBeNull();
    expect(host.querySelector('[data-catalog-tiers]')?.hasAttribute('hidden')).toBe(true);
  });
});

describe('读数', () => {  it('取数期间是一条微光；这一趟失败时收起，不留着一直转的微光', async () => {
    const waiting = await mount(<CatalogFilterPage {...props({ count: null })} />);
    expect(count(waiting, '[data-skeleton="count"]')).toBe(1);
    document.body.innerHTML = '';
    const failed = await mount(<CatalogFilterPage {...props({ count: false })} />);
    expect(count(failed, '[data-skeleton="count"]')).toBe(0);
    expect(count(failed, '[data-count-readout]')).toBe(0);
    expect(failed.querySelector('[data-catalog-readout]')?.textContent).toBe('');
  });
});

describe('续页', () => {
  it('上一份名单要的那页还在路上时换了名单，新名单照样续页，旧的那页回来不进这一排', async () => {
    // 一排一枚铺不满一行：没溢出就一直停在右端，每次画完都要下一页。
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(1000);
    const stale = pending<TierPerformer[]>();
    const moreTops = vi.fn<CatalogFilterProps['actions']['moreTops']>()
      .mockReturnValueOnce(stale.answer)
      .mockResolvedValueOnce([{ name: '新二', ringHtml: '' }])
      .mockResolvedValue([]);
    const tiers = (key: string, name: string) =>
      ({ key, performers: [{ name, ringHtml: '' }], studios: [], following: [] });
    const base = props();
    const page = (key: string, name: string) =>
      <CatalogFilterPage {...base} tiers={tiers(key, name)} actions={{ ...base.actions, moreTops }} />;
    const { host, rerender } = await mountRoot(page('旧', '旧一'));
    expect(moreTops).toHaveBeenCalledTimes(1);

    await rerender(page('新', '新一'));
    await settle();
    await stale.release([{ name: '旧二', ringHtml: '' }]);
    await settle();

    const names = [...host.querySelectorAll('[data-tier-performer]')].map((node) => node.getAttribute('data-entity-name'));
    expect(names).toEqual(['新一', '新二']);
    expect(moreTops).toHaveBeenCalledTimes(3);
  });
});
