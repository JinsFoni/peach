# ADR-0118：xHamster 只当作品来源，人像与资料仍从 Pornhub 补

- 状态：已采纳
- 日期：2026-10-24
- 相关：ADR-0052（判据确定即落库）、ADR-0096（关注来源绑出创作者）、ADR-0114（只有 HLS 的来源
  播放时才解析）、ADR-0115（补发布账号资料）

## 背景

追更要能跟一位演员在哪个站更新就在哪个站跟。这位演员在 `zh.xhamster.com` 有创作者页
（`/creators/<slug>-<数字 id>/newest`），作品列表、日期、时长、封面都在这一页上；但创作者页
上没有人像，也没有一份能当资料用的简介表——她的头像与资料表在 Pornhub 的人物页上。

两个站对同一个人用同一个名字（`shinaryen`），而 Pornhub 没有任何可用的检索接口：`/api/v1/
search/suggest`、`/pornstars/search` 全是 404，`/pornstars?search=` 直接无视参数列无关模特
（取证见 `docs/reference-snapshots/pornhub-model-profile.md`）。

## 决策

**一、`xhamster` 是一条正常的追更来源。** 登记在 `follow_providers.PROVIDERS`
（`semantics="work"`、`backfill=True`、优先级 56），连接器只做列表：作品身份是站内数字
`data-video-id`，标题取 `title`／`aria-label`，封面取 `srcset` 的卡片尺寸那一档，时长与日期
从卡上读。

**日期是一天的粒度。** 站点一天只在第一条上印 `24 October 2026`，后面的卡沿用那一天；落库标
`approximate`，不当成站点给出的精确发布时间。作品页里其实有精确到秒的 `created`，但那是详情
页才有的东西，为它多打一次请求不值。

**分页只有一种写法。** 页码写在路径上（`/newest`、`/newest/2`……），`?page=2` 被站点忽略、
回的仍是第一页；翻过最后一页回 404，所以 `HISTORY_END_STATUSES = (404,)`。

**二、人像与资料不从这一站取。** 创作者页不给这两样，连接器也不去猜：`creator_profile_followup`
把 `LOOKUP_PROVIDERS = ("xhamster",)` 这几种来源的创作者按名字去 Pornhub 找一条同名人物页，
找到后照 ADR-0115 那一套写 `performer_profile` 并装头像（`source_kind=official_profile`）。

**三、按名字找人的门槛是「页面上的名字一致，而且只有一条」。** 拼 slug 只是找地址的手段
（`Shinaryen` → `shinaryen`），认不认看页面上写的名字；`/model/`、`/pornstar/`、`/users/`
三种页型各问一次，按跟完重定向的最终地址去重——同一个人只有一页。命中不止一条是两个人用了
同一个名字，一条都没有是站上没有这个人，两种都什么都不写，摘要里说清是哪一种。

找到的那一页记进 `entity_external_ref`（`provider=pornhub`、`external_kind=creator`、
`external_id=<slug>`，页型在 `metadata_json` 里）：下一轮起直接按它取，改名也不会找歪。这一页
已经记在别的档案上时不借——那说明账本里有两份档案是同一个人，该由用户去合并，而不是让一条
后继把两个人的资料混成一份。

**四、没写成的等多久。** 站点抽风、改版按 `RETRY_UNFETCHED`（1 小时）重试；「站上没有同名
人物页」按 `RETRY_MISSING`（7 天）再问一次——她可能过几天才在那边建页，但不值得每小时去问。

## 后果

同一部作品在两个站各发一遍时，两条条目按标题归出同一个 `release_key`，成为同一作品的变体，
首选仍是 Pornhub（优先级 55 低于 56）；`xhamster` 那一条不会被当成另一部作品。

按名字找人只在「这位只有 `LOOKUP_PROVIDERS` 里的来源」时才发生，一次最多三次请求，找到即止并
落成编号，所以存量创作者不会每轮都去问；同名但确实是两个人的情况留在用户手里，不会被自动接上。

播放时才解析正片：作品页 preload 上的 HLS 母清单列着这一条的每一档（144p 到 4K），子清单是
分片 MP4（av1）或 TS（h264），取的时候 302 到 `ip*.ahcdn.com`，两个主机都在这条来源的媒体
白名单内。渐进式 mp4（`downloadDropdownComponent.sources.mp4`）只到 720p，不拿它当片源；
清单改写与短键映射复用 ADR-0114 那一套。token 与编解码绑定、由 UA 决定，标准 UA 拿到 av1，
换签成 h264 成功就是所有浏览器都能播的那一档，换不成才用页面自己给的。

## 验收

- 订阅 `https://zh.xhamster.com/creators/shinaryen-89795/newest` 后，首次检查抓到 46 条作品，
  每条有标题、封面、时长与日期（同日沿用）。
- 这位创作者的头像与资料表来自 `cn.pornhub.com/model/shinaryen`，档案上留下
  `provider=pornhub` 的人物页编号。
- 播放解析给出母清单里的全部档位（含 1080p 与 4K），播放器按清单类型挂 HLS。
- `tests/test_follow_xhamster.py` 钉住列表选择器、日期沿用、分页写法、尽头 404、母清单解析、
  换签回退与主机白名单；`tests/test_pornhub_profiles.py` 与
  `tests/test_creator_profile_followup.py` 钉住「一条同名才接上」「两条同名什么都不写」
  「已记在别的档案上不借」。
