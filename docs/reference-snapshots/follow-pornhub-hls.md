# Pornhub 人物页、作品页与 HLS 清单证据

- 取证日期：2026-10-23（英文站）、2026-10-24（中文站、中文分类／标签、列表时间与条数）
- 用途：`pornhub` 追更来源的列表解析、详细时间与标签、播放地址解析，以及抓取站为什么是
  `cn.pornhub.com`。
- 凭据保护：全程匿名（不带 Cookie、不登录），只记录状态码、DOM 结构、主机名与字段名。
  签名地址里的查询串一律写成 `?…`，不落任何 token。

## 抓取站：中文站

- `GET https://cn.pornhub.com/...` 与 `GET https://www.pornhub.com/...` 的响应结构一致。
- `www.pornhub.com` 带 `Accept-Language: zh-CN` 时 302 到 `cn.pornhub.com`；不带语言头
  时回 200 的英文页。连接器因此直接取中文站，少一跳。
- 差别只在字面：同一个作品，英文站分类是 `Teen`／`Asian`／`Big Ass`，中文站是
  「18-25歲」／「亚洲人」／「大屁股」；标签同理，且中文标签的 `<a>` 带
  `data-translated="translated-tags"`、没有 `title`，字在 `<span>` 里。
- 标签只有站点有译文的那些是中文，其余保留上传者的原文（2026-10-24 实测同一条作品：
  「震动棒」「亚洲素人」「日本无码」与 `vibrator`、`asian amateur` 并存）。
- 作品标题是上传者自己写的，站点只对一部分给了译文，不能当成语言开关。

## 人物页

请求：`GET https://cn.pornhub.com/model/mini-diva/videos`，标准桌面 Chrome UA。

- HTTP 200，约 1.1 MB HTML，列表**在服务端渲染**：这位模特的 40 条视频直接出现在响应里，
  没有验证码、没有质询页。
- 卡片是 `li.pcVideoListItem`，身份取 `data-video-vkey`（13 位十六进制），另有
  `data-video-id`（站内数字序号）。
- 卡片内字段：`a[href*="view_video.php"]` 的 `title` 是作品标题；`img[data-image]`
  是封面（主机 `pix-cdn77.phncdn.com`，多数只带 `?cache=`，不是签名）；
  `var.duration` 是时长（`15:04`）；`.added` 是**相对**时间，字面随站点语言走
  （英文站 `3 days ago`，中文站 `6年前`）。列表时间怎么取见下一节。
- 卡片还带 `img[data-mediabook]`（签名 webm 预览片，`validto` 只有约两小时）。
  它不入库：过期后就是一条取不到的地址。
- 页面声明 `<meta charset="utf-8">`；按字节交给解析器时才不会把中文读成乱码。

### 只取本人列表：`div.profileVids`

- 站内导航的「最热」「推荐」下拉里，推广卡用的是同一个
  `li.pcVideoListItem` 类名，挂在 `div.headerSubMenu` 下面。2026-10-24 实测
  `mini-diva` 的页面上两种卡一共 44 张，其中 40 张是本人的（在
  `div.profileVids` 里），另 4 张是导航推广卡。
- 导航卡的时间字面写成 `56年前`（英文站 `56 years ago`），内容是别人的作品。
  按类名全取会把别人的作品当成这个人的，条数也对不上主页显示的 40。
- 选择器因此限定在 `div.profileVids li.pcVideoListItem`；取不到时退回全页并排除
  `div.headerSubMenu` 里的卡。

### 列表时间：从 vkey 反推

- `.added` 的字面不能当排序依据：中文站写成 `6年前`／`2年前`，粗到年，英文站才是
  `3 days ago` 这种可解析的写法。
- vkey 的前 8 位是十六进制秒级时间戳（`ph` 开头的旧 vkey 从第 3 位起），反推值与详情页
  `uploadDate` 相差十几分钟：

  | vkey | 前 8 位反推 | 详情页 `uploadDate` |
  | --- | --- | --- |
  | `6ab39e315f82d` | 2026-09-23T09:38:57Z | 2026-09-23T09:51:16Z |
  | `6798a78e9c631` | 2025-01-28T09:46:54Z | 2025-01-28T09:59:19Z |

- 列表因此按 vkey 反推时间入库（标 `approximate`），探到详情页时用 `uploadDate` 覆盖；
  vkey 认不出时间戳（不是十六进制或落在 2007 年之前）时才退回字面换算。
- 本人列表本身就按上传时间倒序，反推值与之逐条吻合。

### 分页

- `?page=N` 从 1 起，站点自己的页码；连接器内部第 0 页即首页。翻过最后一页回 HTTP 404。
- 2026-10-24 实测 `mini-diva` 一页放完全部 40 条，页面没有翻页链接，`?page=2` 回 404。
- 2026-10-23 对一位高产演员实测：第 1–9 页有效，第 10 页起 404。回填因此有界。

### 三种人物页

| 路径 | 说明 |
| --- | --- |
| `/model/<slug>/videos` | 认证模特 |
| `/pornstar/<slug>/videos` | 职业演员 |
| `/users/<slug>/videos` | 普通账号 |

`/model/<slug>` 对职业演员会 301 到 `/pornstar/<slug>`，对普通账号跳到 `/users/<slug>`；
直接请求 `/model/<职业演员>/videos` 会得到 HTTP 200 但**只列 23 条**，而
`/pornstar/<slug>/videos` 同一时刻是 49 条。页型只认 `<link rel="canonical">`：

- `/model/lana-rhoades/videos` → canonical `https://cn.pornhub.com/pornstar/lana-rhoades`
- `/model/mini-diva/videos` → canonical 指向自己
- `/users/<账号>/videos` → canonical 指向自己

canonical 的**主机**不参与判断：落在 `cn`／`www`／别的地区站都只取路径里的页型与
slug，抓取站固定是 `cn.pornhub.com`。

## 作品页

请求：`GET https://cn.pornhub.com/view_video.php?viewkey=<13 位 vkey>`。

- HTTP 200，约 1.6 MB。
- JSON-LD 里 `"uploadDate": "2026-09-18T12:47:42+00:00"`：精确发布时间，列表没有。
- `<meta property="video:duration" content="904">`：秒数。
- `.usernameWrap a`：上传者显示名（作品页的「作者」就是出镜者）。
- `.categoriesWrapper a` 与 `.tagsWrapper a`：分类与内容标签，各自有序、可重复出现。
- 页面没有 `isDownloadable` 之类标记，也没有可用的下载地址。

## 播放地址：只有 HLS

作品页内嵌 `mediaDefinitions` 数组（在 JS 字符串里，引号转义）。2026-10-23 实测：

| format | 字段 | 结果 |
| --- | --- | --- |
| `hls` | 1080 / 720 / 480 / 240 四档，各自一个 `master.m3u8` | HTTP 200，`application/vnd.apple.mpegurl` |
| `mp4` | `remote: true`，指向 `/video/get_media?s=…&v=…` | 匿名请求返回空数组 `[]` |

2026-10-24 在中文站复测同一形状：`mediaDefinitions` 只有四条 `format: "hls"`
（1080／720／480／240，主机 `ev-h.phncdn.com`，`segmentFormats` 是
`mpeg2_ts` + `ts_aac`），没有 mp4 条目。

- 清单地址主机形如 `ev-h.phncdn.com` / `hv-h.phncdn.com`，带 `validfrom`、`validto`
  等签名参数，`validto` 距取回时刻约 1 小时。
- 主清单只有一个 `EXT-X-STREAM-INF`，`CODECS="avc1.640020,mp4a.40.2"`
  （H.264 High + AAC），指向 `index-v1-a1.m3u8`。
- 子清单是 VOD（`EXT-X-PLAYLIST-TYPE:VOD`、有 `EXT-X-ENDLIST`），分片是 `.ts`，
  **没有** `EXT-X-KEY`（不加密），也没有 `EXT-X-MAP`。
- 因而不是 ADR-0016 记录的 HEVC-in-TS 黑屏情形，但仍然是 HLS：Peach 只能按清单转发。

### 清单地址的两种写法

作品页内嵌的 `mediaDefinitions` 有两种签名写法，同一部作品连着取页面会交替出现：

| 写法 | 形如 | 取回 |
| --- | --- | --- |
| 时间戳签名 | `…/master.m3u8?validfrom=…&validto=…&ipa=1&hdl=-1&hash=…` | HTTP 200，`application/vnd.apple.mpegurl` |
| 只剩哈希 | `…/master.m3u8?h=…` | HTTP 410，正文一个字节 |

2026-10-24 在中文站连取二十次同一作品页：按原地址请求多半拿到 `?h=` 那一种；在地址
上带一个没用过的查询参数重取会换一种，约四成能取回 200。换 `User-Agent`、加
`Referer`、`Origin`、`Range` 都改变不了 `?h=` 那一种的 410，同一出口 IP 下也如此。

把 `?h=` 那一种的主机从 `hv-h.phncdn.com` 换成 `ev-h.phncdn.com` 能取回清单，但清单
里的分片在 `ev-h` 上回 404——分片只在页面自己指的那个主机上有，换主机这一路走不通。
浏览器 TLS 指纹（curl-impersonate）取 `?h=` 那一种回 412 `request incorrect`，也不行。

### 未取得

- `get_media` 的有效响应没能取得（匿名恒为空数组），所以没有渐进式 mp4 这一路。
