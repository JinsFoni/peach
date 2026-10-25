/* 宿主面上那三枚漂动的光晕（外观设置里的「光晕」）：侧栏抽屉和实体资料卡各挂一枚。
 *
 * 画法只有一份，在 `web/css/01-base.css` 的 `[data-glow-layer]` 上：三枚椭圆光斑、颗粒层、两条相位
 * 不同的李萨如漂移，颜色与浓淡全部读 `--glow-*` 变量。这里只负责挂上那一枚空 div，并在它出现时把
 * 当前设置写进去——壳启动时画的那些层早于本组件挂载，进到资料页才挂上来的这一枚得自己补一次。
 * 之后改配色由壳的 `applyHomeGlow` 统一重画所有层，组件不再订阅。
 *
 * 宿主自己给两样东西：`position:relative`（`inset:0` 要贴着宿主那块面）与 `isolation:isolate`
 * （负层号才落在宿主自己的填充之上、正文之下）；圆角与溢出由宿主的 `overflow:hidden` 连圆角一起裁，
 * 这一层不自己再写一遍。 */
import { useEffect } from 'react';

import { paintHomeGlowNow } from '@peach/appearance';

/** 光晕层。宿主必须是定位元素且自成一个层叠上下文。 */
export function GlowLayer() {
  useEffect(() => { paintHomeGlowNow() }, []);
  return <div data-glow-layer="" aria-hidden="true" />;
}
