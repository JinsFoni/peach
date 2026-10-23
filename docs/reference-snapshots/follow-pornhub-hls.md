# Pornhub 人物页、作品页与 HLS 清单证据

- 取证日期：2026-10-23
- 用途：新增 `pornhub` 追更来源的列表解析、详细时间与标签、播放地址解析。
- 凭据保护：全程匿名（不带 Cookie、不登录），只记录状态码、DOM 结构、主机名与字段名。
  签名地址里的查询串一律写成 `?…`，不落任何 token。

## 人物页

请求：`GET https://www.pornhub.com/model/mini-diva/videos`，标准桌面 Chrome UA。

- HTTP 200，约 1.1 MB HTML，列表**在服务端渲染**：44 张卡片直接出现在响应里，
  没有验证码、没有质询页。
- 卡片是 `li.pcVideoListItem`，身份取 `data-video-vkey`（13 位十六进制），另有
  `data-video-id`（站内数字序号）。
- 卡片内字段：`a[href*="view_video.php"]` 的 `title` 是作品标题；`img[data-image]`
  是封面（主机 `pix-cdn77.phncdn.com`，多数只带 `?cache=`，不是签名）；
  `var.duration` 是时长（`15:04`）；`.added` 是**相对**时间（`3 days ago`）。
- 卡片还带 `img[data-mediabook]`（签名 webm 预览片，`validto` 只有约两小时）。
  它不入库：过期后就是一条取不到的地址。

### 分页

- `?page=N` 从 1 起。`page=1` 与首页相同；翻过最后一页回 HTTP 404。
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

- `/model/lana-rhoades/videos` → canonical `https://www.pornhub.com/pornstar/lana-rhoades`
- `/model/mini-diva/videos` → canonical 指向自己
- `/users/<账号>/videos` → canonical 指向自己

## 作品页

请求：`GET https://www.pornhub.com/view_video.php?viewkey=<13 位 vkey>`。

- HTTP 200，约 1.5 MB。
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

- 清单地址主机形如 `ev-h.phncdn.com` / `hv-h.phncdn.com`，带 `validfrom`、`validto`
  等签名参数，`validto` 距取回时刻约 1 小时。
- 主清单只有一个 `EXT-X-STREAM-INF`，`CODECS="avc1.640020,mp4a.40.2"`
  （H.264 High + AAC），指向 `index-v1-a1.m3u8`。
- 子清单是 VOD（`EXT-X-PLAYLIST-TYPE:VOD`、有 `EXT-X-ENDLIST`），分片是 `.ts`，
  **没有** `EXT-X-KEY`（不加密），也没有 `EXT-X-MAP`。
- 因而不是 ADR-0016 记录的 HEVC-in-TS 黑屏情形，但仍然是 HLS：Peach 只能按清单转发。

### 未取得

- `get_media` 的有效响应没能取得（匿名恒为空数组），所以没有渐进式 mp4 这一路。
- 短时间连续取同一作品页后，清单地址一度回 HTTP 410；同一地址数分钟后重新取回为
  200。取不到时按「上游这一次没给」处理，不据此改判来源可用性。
