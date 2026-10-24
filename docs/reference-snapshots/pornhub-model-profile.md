# Pornhub 人物页上的头像、简介与资料表证据

- 取证日期：2026-10-24（中文站）
- 用途：`pornhub` 追更来源绑出来的创作者补头像与资料（ADR-0115）时，读哪一页、读哪些节点、
  哪一格能换算成账本的列。
- 凭据保护：全程匿名（不带 Cookie、不登录），只记录状态码、DOM 结构、主机名与字段名。
  头像地址里的签名段一律写成 `(m=…)`、`(mh=…)`，不落任何 token。

## 哪一页

带 `Accept-Language: zh-CN` 取这三页，都是 HTTP 200，**头像、名字、简介、资料表、本人账号与
读数三页都有**（2026-10-24 逐页核对过 `#getAvatar`、`div.js-infoText .infoPiece`、
`section.aboutMeSection`、`div.infoBoxes .infoBox` 四处节点）：

| 地址 | 大小 | 四处节点 |
| --- | --- | --- |
| `/model/mini-diva`（首页） | 约 1.2 MB | 齐全 |
| `/model/mini-diva/videos`（作品页） | 约 1.1 MB | 齐全 |
| `/model/mini-diva/about`（简介页签） | 约 0.9 MB | 齐全 |

所以补资料取的是**来源自己指向的那一页**（只把主机换成中文站），不必另猜页签。页型由路径里
那一段决定：`/model/`、`/pornstar/`、`/users/` 三种都在，三者之间会 301 互跳，取页跟完
重定向即可。

## 能读到什么

| 位置 | 选择器 | 实测内容 |
| --- | --- | --- |
| 头像 | `#getAvatar`（在 `div#avatarPicture > div.previewAvatarPicture` 里） | `src` 是 `https://ei.phncdn.com/pics/users/…/avatar…/(m=…)(mh=…)200x200.jpg`，`alt` 是她的名字 |
| 名字 | `div.nameSubscribe h1[itemprop="name"]` | `Mini Diva` |
| 认证与奖杯 | `.verifiedPornstar`、`.trophyChannel` | 有这两个节点，没有文字 |
| 简介 | `section.aboutMeSection` 里 `.title` 的下一个 `div` | `Love being here from time to time. …`（`.title` 自己是 `简介 Mini Diva`） |
| 资料表 | `div.js-infoText div.infoPiece` 里的两个 `<span>` | 见下表，标签带尾冒号（`感情状态:`） |
| 本人账号 | `div.socialsColumn ul.socialList a[href]` 里的 `span.socialText` | `官方网址`（onlyfans）、`Twitter`、`Instagram` |
| 读数 | `div.infoBoxes div.infoBox[data-title]` | `视频播放量: 561,849,851`、`订阅者: 1,430,676`；排名那几格没有 `data-title` |

资料表按页面顺序，实测 18 项（`mini-diva`，2026-10-24）：

```
感情状态: 单身          喜欢: 男女通吃            性别: 女性
出身地: Czech Republic  三围: 35-25-36            身高: 5' 6" (167cm)
体重: 111lbs. (50kg)    种族: White               发色: Brunette
假奶子: 否              纹身: No                 是否有穿环: Yes
兴趣爱好: Boring AF.    会勾起我性欲的是: When you watching me, …
会浇灭我性欲的是: Dunno what can turn me off.
视频播放量: 561,849,851  主页浏览量: 174,425,083    已看视频: 707
```

- **标签是中文，值是站上原文。** 英文站同一页的标签是 `Relationship:`、`Ethnicity:`；值
  （`Czech Republic`、`White`、`Brunette`）两边一样，不翻译。
- **是否题两边写法不同**：中文站写「是／否」，英文站写 `Yes`／`No`。同一个人的
  「假奶子」是「否」，英文站是 `No`。
- **身高、三围是英制**：身高那一格括号里有厘米（`(167cm)`），三围只有英寸（`35-25-36`），
  体重给两套（`111lbs. (50kg)`）。
- 页面没有标签列表（`tagList`／`tagsWrap` 都不存在）；她的标签在作品页上。
- 资料表是服务端渲染的，没有 JavaScript 也读得到。

## 头像地址只能用页面给的那一条

头像主机 `ei.phncdn.com`，路径里那两段括号是签名。实测（2026-10-24，同一份 `src` 逐字取用）：

| 试的地址 | 结果 |
| --- | --- |
| 页面上那一条 `…200x200.jpg` | 200，13,726 字节，`image/jpeg` |
| 同一条改成 `…500x500.jpg`／`720x720.jpg`／`original.jpg` | 401 |
| 同一条换两次签名的顺序、去掉 `(mh=…)` | 401 |
| 页面那一条，不带 `Referer` 直接取 | 200（不需要 Referer） |

所以：地址必须照页面原样取、取页之后紧接着取图（签名随每次页面加载变化），
不能自己拼尺寸，也不能把地址存进账本当长期可用的源。

## 这一页不给什么

- 出生日期、出道日期、出演时间：没有这几格，页头那五项里的生日、出道、生涯因此空着。
- 站上的标签：见上。
- 作品数：`0 个视频` 是 Peach 自己的馆藏计数，与站上的 `40 条视频` 不是一回事。

## 没有地址时按名字找人

别的站的创作者（`xhamster` 只给作品列表，不带人像与简介）要借这一页时没有地址可用，
只能按名字找（ADR-0118）。站上没有可用的检索接口，实测（2026-10-24，匿名，`shinaryen`）：

| 试的地址 | 状态 | 结果 |
| --- | --- | --- |
| `/api/v1/search/suggest?q=shinaryen` | 404 | 没这个页 |
| `/pornstars/search?search=shinaryen` | 404 | 正文是「无搜索结果」 |
| `/pornstars?search=shinaryen` | 200 | 列的是无关模特，搜索参数被忽略 |
| `/search?search=shinaryen` | 404 | 没这个页 |
| `/pornstar/search` | 301 | 转到 `/pornstars` 名录首页 |
| `/model/search` | 301 | 转到一个叫 `search` 的账号页 |

站内检索这条路走不通，所以找法只能是拼 slug 再核对页面上的名字（下表地址都相对
`https://cn.pornhub.com`）：

| 试的地址 | 状态 | 结果 |
| --- | --- | --- |
| `model/shinaryen` | 200 | `<title>` 是 `Shinaryen的色情片`，有 `#getAvatar`，页内 `h1` 是 `Shinaryen` |
| `users/shinaryen` | 301 | 转到 `model/shinaryen`：同一个人只有一页 |
| `pornstar/shinaryen` | 301 | 转到 `pornstars`（名录首页），不是人物页 |
| `pornstars/shinaryen` | 404 | 这个路径不是人物页 |

三种页型之间会互相 301，所以「几次命中」要按跟完重定向的最终地址去重。`page_for_name`
因此按「页面上写的名字一致」而不是「地址像」来认，并把最终地址里那一段（页型与 slug）
记下来。

