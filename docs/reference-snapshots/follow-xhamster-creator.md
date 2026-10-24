# xHamster 创作者页与作品页证据

- 取证日期：2026-10-24（中文站 `zh.xhamster.com`）
- 用途：`xhamster` 追更来源选哪一栏、链接形状、列表数据的位置、分页写法与结束信号，以及
  「正片在按下播放时才解析」的地址来源（ADR-0118）。
- 凭据保护：全程匿名（不带 Cookie、不登录），只记录状态码、DOM 结构、主机名与字段名。
  正片地址里的签名段写成 `key=…`，不落任何 token；`data=` 里是一个 IPv6 字面量，同样省略。

## 哪一页

样本来自身份为 `89795` 的创作者 `shinaryen`。抓的是**「全长」那一栏**：

| 地址 | 状态 | 大小 | 列表条数 |
| --- | --- | --- | --- |
| `/creators/shinaryen-89795/full-length/newest` | 200 | 约 391 KB | 46 |
| `/creators/shinaryen-89795/full-length/newest/2` | 200 | — | 46 |
| `/creators/shinaryen-89795/full-length/newest/5` | 200 | — | 21（最后一页） |
| `/creators/shinaryen-89795/full-length/newest/6` | 404 | — | 翻过尽头 |

`?page=2` 被站点忽略：回的 46 条与第一页逐条同 id、同顺序；而 `/full-length/newest/2` 的
46 条与第一页一个都不重叠。
页码只写在路径上，第一页是 `…/full-length/newest` 本身。`/videos` 回 404，`/pornstars/<slug>`
回 404；`/newest/1` 是 301 到 `/newest`。slug 与站内 id 必须对得上（`whatever-89795` 回 404），
所以 ref 一律带着 trailing 的数字 id。

**为什么是这一栏而不是 `/newest`。** 她的更新页不分长短：`/newest` 那一页 46 条的时长是
64–1348 秒，混着竖屏短视频；`/full-length/newest` 的 205 条是 610–2650 秒。全长栏是站点自己
按「有完整正片」筛过的清单，按它取就不必自己按时长或画幅去猜。两栏同一个模板、同一个列表
键，只有路径那一段不同；条数分别是 `/newest` 11 页 485 条、全长栏 5 页 205 条。

## 列表结构

作品清单不是从 DOM 卡片读的，而是页面内嵌的 `window.initials`：

| 位置 | 键／选择器 | 实测内容 |
| --- | --- | --- |
| 这一页的清单 | `window.initials` → `trendingVideoSectionComponent.videoListProps.videoThumbProps` | 46 个对象，顺序＝站点顺序（新的在前） |
| 一条作品 | `id`、`duration`（秒）、`created`（epoch 秒）、`title`、`pageURL`、`thumbURL` | 与 DOM 里那个列表逐项同 id、同顺序（46/46 对齐） |
| 封面 | `thumbURL` | 526×298（`s(w:526,h:298),webp/…`）；`imageURL` 是 2560×1440 原图 |
| 时长 | `duration` | 整数秒（`881`），不是 `14:41` 那种字符串 |
| 发布时间 | `created` | epoch 秒，精确到秒：`1792430526` = 2026-10-19T17:22:06Z |
| 她的动态 | `momentsListComponent.videoThumbProps` | 另一栏，`videoType` 是 `moment` |

**读 DOM 会丢掉大部分时长与封面。** 卡片只把首屏那 9 张的 `[data-role="video-duration"]` 与
`img[data-role="thumb-preview-img"]` 渲染进 HTML，其余 37 张是懒加载占位：同一页 46 张卡里
只有 9 张带这两样，而列表 JSON 里 46 条一条不少。DOM 的日期（`.video-thumb__date-added`，
`24 October 2026`）只到日、一天只印一次；JSON 的 `created` 精确到秒，落库时不必再标近似。

**分页信息在页面 JSON 里给全**：`"paginationComponent":{"currentPageNumber":1,
"lastPageNumber":5,"pageLinkTemplate":"https://zh.xhamster.com/creators/<slug>-<id>/full-length/newest/{#}"}`
——`pageLinkTemplate` 与实测的路径写法一致。

**创作者身份在页面 JSON 里**：`"channelInfo":{"id":89795,"type":"pornstar","slug":"shinaryen-89795",
"tagType":"person","personType":"creator"}`；`<title>` 是 `Shinaryen 成人内容创作者视频`。
创作者页上没有头像与简介的取用点——那两样按 ADR-0118 从 pornhub 的同一位来。

## 作品页与正片地址

`https://zh.xhamster.com/videos/<slug>-xh<base62>` 回 200（约 397–416 KB）。播放器要的东西在
两处：页头 `<link rel="preload">` 上的 HLS 母清单地址，和
`<script id='initials-script'>window.initials={…}</script>` 里那份页面 JSON（合法 JSON，去掉
尾部 `;`）。

### 1080p 与 4K 只在 HLS 里

页头那条 preload 是播放器真正读的来源：

```
<link rel="preload" as="fetch" crossorigin="true"
 href="https://video-nss.xhcdn.com/<token>,<到期时间>/media=hls4/
       multi=256x144:144p:,426x240:240p:,854x480:480p:,1280x720:720p:,
             1920x1080:1080p:,3840x2160:2160p:/031/247/098/_TPL_.av1.mp4.m3u8"/>
```

- `multi=` 列着这一条有哪些档位，写成 `宽x高:档位名` 的序列，写法随 UA 变：Chrome 系（本次
  取证的标准 UA）每项带尾冒号、逗号分隔；Safari 那一路不带尾冒号。
- `_TPL_` 是档位占位符；把它原样取回就是母清单本身（200，约 680 字节）：
  `#EXT-X-STREAM-INF:…,RESOLUTION=3840x2160` 加下一行 `2160p.av1.mp4.m3u8`，一路列到 144p，
  4K 那档 `AVERAGE-BANDWIDTH=13305055`。子清单地址有相对名与站内绝对路径两种写法
  （`video-nss` 给相对名，`video7` 给 `/key=…` 开头的绝对路径）。
- **编解码由 UA 决定，token 与它绑定**：Chrome、Firefox、Edge 拿到 `.av1.mp4.m3u8`，Safari
  拿到 `.h265.mp4.m3u8`。把 token 上的 codec 换成另一种有时能取（2026-10-24 抽 30 条实测
  19 条能换上 `.h264`），换不成的回 `403 Wrong key`——那是签在原来那一档上，不是限流。
- 子清单：av1 那档是分片 MP4（`#EXT-X-MAP` 加 `.m4s`），h264 那档是 TS（`.ts`，
  `#EXT-X-VERSION:3`，4 秒一片，2160p 一片约 641 KB）。首跳 `video*.xhcdn.com`，取的时候
  302 到 `ip*.ahcdn.com`，两个主机都在这个来源的媒体白名单里。
- `videoModel` 里 `resolution: [3840, 2160]`、`isUHD: true`，播放器统计字段写着
  `maxQuality: 2160p`，与母清单对得上。

### 渐进式 mp4 到 720p 为止

`downloadDropdownComponent.sources.mp4` 是下载菜单那四档：`144p`、`240p`、`480p`、`720p`，
主机 `video7.xhcdn.com`，查询串带 `key=…&end=<到期时间>` 与 `data=<IPv6 字面量>`。同一节点上
的 `videoId` 与列表卡上的 `data-video-id` 相同；`sources.download` 是
`/movies/<videoId>/download/<quality>`，要登录。

- 这一份**没有 1080p 与 4K**：把 720p 的地址直接改成 `1080p.h264.mp4`，CDN 回 302 到同一个
  720p 文件（`2160p`、`1440p` 同样如此），取不到更高一档。
- 取这四档里任意一档也是 302 到 `ip241705869.ahcdn.com`，同样要求白名单里有 `ahcdn.com`。
- `videoModel` 另给这一条的 `id`、`title`、`duration` 与精确到秒的 `created`：列表只有日期，
  所以列表落库时标「近似」，精确到秒的那份要进作品页才有。

## 站点图标

`https://zh.xhamster.com/favicon.ico` 回 200 `image/x-icon`（9662 字节）。页头声明的是
`https://static-ah.xhcdn.com/xh-desktop/images/favicon/favicon-v2-256x256.ico?v=1`，
同样 200；`follow_assets.SOURCE_ICON_URLS` 取前者，与其它站一样用站点自己那一份。
