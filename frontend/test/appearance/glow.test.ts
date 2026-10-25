/* 光晕那一层的写入时机（`src/appearance/glow.ts`）。点选写什么由 `test/react/glow-picker.test.tsx` 走。
 * 层怎么画在 `web/css/01-base.css`；卡上那一枚的几何由 `frontend/e2e/design-people.test.ts` 读计算样式断言。 */
import { beforeEach, expect, it, vi } from 'vitest';

import { applyHomeGlow, paintHomeGlowNow, type GlowSettings } from '../../src/appearance/glow';
import { normalizeHomeGlow } from '../../src/appearance/home-glow';
import { SETTINGS_KEY } from '../../src/appearance/settings';
import { createSettingsStore } from '../../src/settings-store';

const store = () => createSettingsStore<GlowSettings>(SETTINGS_KEY, { homeGlow: { ...normalizeHomeGlow(null), strength: 100 }, accent: 'blue' });
const strength = () => document.querySelector<HTMLElement>('[data-glow-layer]')!.style.getPropertyValue('--glow-strength');
const allStrength = () => [...document.querySelectorAll<HTMLElement>('[data-glow-layer]')]
  .map((layer) => layer.style.getPropertyValue('--glow-strength'));

beforeEach(() => { document.body.innerHTML = '<div data-glow-layer></div>' });

it('启动那一次当场写进页面上已有的每一层，不等下一帧', () => {
  paintHomeGlowNow(store());
  expect(strength()).toBe('1');
});

it('页面上每一枚光晕层都写一份，页内后挂上来的那一枚一样补齐', () => {
  document.body.innerHTML = '<div data-glow-layer></div><div data-glow-layer></div>';
  const current = store();
  paintHomeGlowNow(current);
  expect(allStrength()).toEqual(['1', '1']);
  /* 资料卡那一枚是进到资料页才画出来的：它不在启动那一次的名单里，挂上之后自己补一次。 */
  const later = document.body.appendChild(document.createElement('div'));
  later.setAttribute('data-glow-layer', '');
  current.value.homeGlow.strength = 40;
  paintHomeGlowNow(current);
  expect(allStrength()).toEqual(['0.4', '0.4', '0.4']);
  expect(later.style.getPropertyValue('--glow-spot-1-color')).toMatch(/^rgba\(/);
});

it('拖动时一帧只写一次，写的是那一帧时的值', () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => frames.push(run));
  const current = store();
  for (const value of [80, 60, 40]) { current.value.homeGlow.strength = value; applyHomeGlow(current) }
  expect([frames.length, strength()]).toEqual([1, '']);
  frames.shift()!(0);
  expect(strength()).toBe('0.4');
  current.value.homeGlow.strength = 20; applyHomeGlow(current);
  expect(frames.length).toBe(1);
  vi.unstubAllGlobals();
});
