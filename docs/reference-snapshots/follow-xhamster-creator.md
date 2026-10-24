# xHamster 创作者页与作品页证据

- 取证日期：2026-10-24（中文站 `zh.xhamster.com`）
- 用途：`xhamster` 追更来源的链接形状、列表选择器、分页写法与结束信号，以及「正片在按下
  播放时才解析」的地址来源（ADR-0118）。
- 凭据保护：全程匿名（不带 Cookie、不登录），只记录状态码、DOM 结构、主机名与字段名。
  正片地址里的签名段写成 `key=…`，不落任何 token；`data=` 里是一个 IPv6 字面量，同样省略。

## 哪一页

样本来自身份为 `89795` 的创作者 `shinaryen`：

| 地址 | 状态 | 大小 | 列表条数 |
| --- | --- | --- | --- |
| `/creators/shinaryen-89795/newest` | 200 | 约 388 KB | 46 |
| `/creators/shinaryen-89795/newest/2` | 200 | 约 363 KB | 46 |
| `/creators/shinaryen-89795/newest/11` | 200 | — | 25（最后一页） |
| `/creators/shinaryen-89795/newest/12` | 404 | — | 翻过尽头 |

`?page=2` 被站点忽略：回的 46 条与第一页逐条同 id、同顺序；而 `/newest/2` 的 46 条与第一页
一个都不重叠。
页码只写在路径上，第一页是 `/newest` 本身。`/videos` 回 404，`/pornstars/<slug>` 回 404；
`/newest/1` 是 301 到 `/newest`。slug 与站内 id 必须对得上（`whatever-89795` 回 404），
所以 ref 一律带着 trailing 的数字 id。

## 列表结构

| 位置 | 选择器 | 实测内容 |
| --- | --- | --- |
| 她的作品列表 | `div.thumb-list[data-ecommerce-list-name="newest"]` | 46 个 `[data-video-id]` |
| 一条作品 | `div[data-video-id="<数字>"]`（`data-video-type="video"`） | 站内作品 id，全局递增 |
| 作品地址 | `a[data-role="thumb-link"]@href` | `https://zh.xhamster.com/videos/<slug>-xh<base62>` |
| 标题 | 同一 `a` 上的 `@title`，没有就是 `@aria-label` | 页面语言的中文标题（上传者自己写的，站点不翻译） |
| 封面 | `img[data-role="thumb-preview-img"]@srcset` | 第一档 526×298（`s(w:526,h:298),webp/…`），`@src` 是 2560×1440 原图 |
| 时长 | `[data-role="video-duration"]` | `03:44` |
| 日期 | `.video-thumb__date-added` | `24 October 2026`：中文站也照英文月份印，只到日，没有时分 |

页面别处也有 `data-video-id`：顶部推广位、「推荐」小列表（另一个 `data-ecommerce-list-name`）、
以及她的 `moments`（`data-video-type="moment"`，地址是 `/moments/…`）。整页统计 52 个
`data-video-id`，其中 46 个在那个带名字的列表里，所以选择器必须先落到列表容器上，
再按 `data-video-type` 排掉动态。

**一天只印一次日期。** 第 1 页 46 条里有 12 条没有日期节点，它们属于紧邻上方那条的日期；
逐条核对过与作品页里的 `created` 一致：日期 `22 October 2026` / 没有日期的那条
（id 31370199）在作品页里是 `"created":1792688018` = 2026-10-22T16:53Z。

**分页信息在页面 JSON 里给全**：`"paginationComponent":{"currentPageNumber":2,
"lastPageNumber":11,"pageLinkTemplate":"https://zh.xhamster.com/creators/<slug>-<id>/newest/{#}"}`
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
