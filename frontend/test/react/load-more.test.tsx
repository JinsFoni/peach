/* 续页哨兵：还差一屏就把下一页接上，滚到底时接着的卡片与封面已经在手里。
 *
 * 这里量两件事：哨兵挂在多大的提前量上（`rootMargin`），以及一进入那一圈就自己去取。 */
import { act } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { LoadMore } from '../../src/react/catalog-grid/catalog-grid-page';
import { mountRoot } from './render';

/** 记下构造参数的观察器：真浏览器里哨兵什么时候进视口由它说了算，用例自己放行。 */
class CaptureObserver {
  static last: CaptureObserver | null = null;
  readonly options: IntersectionObserverInit | undefined;
  target: Element | null = null;
  constructor(readonly callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    this.options = options;
    CaptureObserver.last = this;
  }
  observe(target: Element) { this.target = target }
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] { return [] }
  enter() {
    this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
}

describe('续页哨兵', () => {
  it('还差一屏就取下一页，滚到那儿时页里已经有货', async () => {
    vi.stubGlobal('IntersectionObserver', CaptureObserver);
    const load = vi.fn(async () => {});
    const root = await mountRoot(<LoadMore entity load={load} enabled={() => true} />);
    const observer = CaptureObserver.last!;
    expect(observer.target).toBe(root.host.querySelector('[data-entity-more]'));
    expect(observer.options?.rootMargin).toBe('1600px');
    expect(load).not.toHaveBeenCalled();
    await act(async () => observer.enter());
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('末尾还有货才接；最后一页之后不再取', async () => {
    vi.stubGlobal('IntersectionObserver', CaptureObserver);
    const load = vi.fn(async () => {});
    await mountRoot(<LoadMore entity load={load} enabled={() => false} />);
    await act(async () => CaptureObserver.last!.enter());
    expect(load).not.toHaveBeenCalled();
  });
});
