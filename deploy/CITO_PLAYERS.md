# 选手段数据源 · Cito API 接入设计

> **状态：设计已验证（2026-10-09）—— 匹配率实测通过（15/15 = 100%），可以实施，尚未写代码。**
> 本文回答「为什么选 Cito、怎么接、哪里会翻车」，末尾是照做清单与回滚步骤。
> 单场战报的版式与三级降级见 `deploy/ESPORTS.md` §3.2；原 csdb.gg 抓取层已于
> 同日（2026-10-09）整体拆除，展示层原地保留（见 `ESPORTS.md` §9 的数据源复核表）。
>
> **一句话结论：可行，而且目前是唯一能同时给出 K/D、ADR、KAST、Rating 的源。**
> 免费档 **500 次/月**、无需绑卡、**比赛刚结束就有数据**。
> 曾经的唯一问号「Liquipedia 的赛程能不能对上 Cito 的 `matchId`」——
> **已实测解决，见 §4**。

---

## 1. 决策链：为什么最后落在 Cito

选手段（单场战报 V2 里那段 5v5）要的是每张图的 **K-D / ADR / KAST / Rating**。
2026-10-09 把能想到的源逐个实测过一遍：

| 源 | 结论 | 证据 |
|---|---|---|
| **csdb.gg**（原实现） | ❌ 失效 | 改成 Next.js 客户端渲染，单场页纯骨架（`Player K D A` / `Rating` 零命中） |
| **Liquipedia 赛事页** | ❌ 无选手数据 | 只有比分与对阵；`Match:` 命名空间全站仅 6 个实验页 |
| **HLTV 直连** | ❌ 403 | Cloudflare 全站拦截 |
| **bo3.gg / escorenews / 5eplay** | ❌ | 403 / WAF / 接口 404 |
| **PandaScore** | ❌ | 免费档无选手统计，Historical 档 €400/月/游戏 |
| **Parse.bot**（HLTV 转售） | ❌ **认证后实测仍不可用** | `get_match_details` 的 `stats` 实测 **0/8 场**有数据（3 场返空 + 4 场超时）；只有聚合端点的 rating/KD，**无 ADR/KAST** |
| **Cito**（`citoapi.com`） | ✅ **可用且已实测闭环** | `player-stats` 9/9 场有数据、含当天刚结束的比赛；K/D/A + ADR + KAST + Rating 齐全；**已跑通到 `_aggregate_players()` 出选手行**（§4/§5） |

> Cito 是一个多游戏电竞数据 API（CS2 标注 **beta**），自称**独立聚合器**（非官方、
> 非 rights-cleared）。它有 OpenAPI spec、官方 SDK（PyPI/npm `cs2-api`）、MCP server。

---

## 2. 现场事实（2026-10-09 用免费 key 实测，不是推断）

### 2.1 调用方式

| 项 | 值 |
|---|---|
| Base URL | `https://api.citoapi.com/api/v1` |
| 认证头 | **`x-api-key: cito_...`**（注意**不是** Parse.bot 那种 `X-API-Key`） |
| CS2 命名空间 | 所有路由以 `/cs2` 开头 |
| 分页 | 列表端点 `limit` + `page`，返回 `meta{count,total,page,totalPages,hasNext}` |
| 限流 | 免费 **10 次/分钟**；超限报错会写明 plan / 限额 / 何时重置 |
| 时间 | 全部 ISO 8601 **UTC**（`startsAt` 形如 `2026-10-09T07:00:00.000Z`） |
| 完整路径表 | `GET /openapi.json`（344 条路径，tag `CS2`）——**权威，优先查它而不是猜** |

### 2.2 三条会用到的端点（实测）

| 端点 | 参数 | 给什么 |
|---|---|---|
| `GET /cs2/matches/recent` | `limit`(max 100) / `page` | 最新战果，每条含 `id` / `team1Name` / `team2Name` / `startsAt` / `bestOf` / `maps[]`（**含 `mapName`**） |
| `GET /cs2/matches/{matchId}/player-stats` | **无参数** | 每图每人一行：`kills`/`deaths`/`assists`/`plusMinus`/`adr`/`kast`/`rating` |
| `GET /cs2/matches/{matchId}/games` | 无 | 每张图的 `id` / `mapNumber` / **`status`**（`completed` vs `map_not_played`） |

辅助：

| 端点 | 用途 |
|---|---|
| `GET /cs2/teams/{teamIdOrSlug}/results?limit=N` | **按队伍查历史结果**（30 天）。不受 `recent` 100 场限制 —— 兜底路径 |
| `GET /cs2/search?q=<名>` | 模糊搜队伍 / 选手 / 赛事 / 比赛；**`teams` 命中最有用**（给 `id`+`slug`，如 `Natus Vincere` → `cs2-team-4608` / `natus-vincere`） |
| `GET /cs2/matches/{matchId}` | 单场详情（含 `maps[]` 带 `mapName`） |
| `GET /cs2/teams/{slug}` | 队伍档案（`slug` 可取 `natus-vincere` 这种；小语种 slug 会去音标，如 `Famalicão` → `famalico`，**不好猜，别拿它当解析手段**） |

### 2.3 `player-stats` 的返回

```json
{
  "playerName": "Kiy0o", "teamName": "Lazer Cats",
  "mapId": "cs2-match-2399031-map-1",
  "kills": 16, "deaths": 17, "assists": 3, "plusMinus": -1,
  "adr": 68.7, "kast": 0.652, "rating": 1.15,
  "ratingVersion": "source_rating_3_0",
  "dataQuality": "partial", "confidence": "...", "lastSyncedAt": "..."
}
```

**给我们的关键点：**

- `kills` / `deaths` / `assists` —— 直接对应模板的 `k` / `d` / `a`（**注意：`a` 在
  `_aggregate_players()` 里会被丢弃**，展示层只出 K-D，见 §5）。
- **`adr`** 浮点（`68.7`）。
- **`kast` 是 0~1 的比例**（`0.652` = 65.2%）→ 渲染前 **×100**（展示层按百分比数字用）。
- **`rating` 是 Rating 3.0**（`ratingVersion: "source_rating_3_0"`）→ 与 HLTV 页面同源，
  **不是** 1.0 基线的老 Rating（实测 16-17 的选手给 1.15，属正常）。
- **没有 `mapName`**：只有 `mapId`（`cs2-match-2399031-map-1`）→
  图名从 `recent` 的 `maps[]` 或 `matches/{id}` 取，用 `-map-N` 的 N 对 `mapNumber`。
- 🔴 **行数 = 10 × 真正打过的图数**，不是固定 30。
  实测 `cs2-match-2399375`（Bo3）只回 **20 行** —— 因为 `/games` 显示
  `map-3: status="map_not_played"`（2:0 横扫，第 3 图没打）。
  **不是 20 行上限**（`limit=500` 被忽略、`meta.limit` 恒为 20，但 `page=2` 也不给新图）。
- `dataQuality` 实测多为 `"partial"`（即便 10 行齐全）→ **不要拿它当「没数据」的判据**。

### 2.4 🔴 `maps[]` 里混着「没打过的图」

`recent` / `matches/{id}` 的 `maps[]` **包含未开打的图**，靠 `status` 区分：

| 字段 | 已打 | 未打 |
|---|---|---|
| `status` | `completed` | **`map_not_played`** |
| `resultType` | `played` | `not_played` |
| `statsEligible` | `true` | `false` |

**→ 组装逐图数据前必须先按「已打」过滤**，否则卡片会画出一张空的第 3 图
（没比分、没选手）。这条是实测踩出来的，务必落实。

### 2.5 新鲜度：**几乎实时**

抽样 9 场，**9/9 全部有数据**，包括当天刚结束的那场：

```
cs2-match-2399375  endsAt=2026-10-09T08:42:49Z  stats.lastSyncedAt=2026-10-09T08:42:47Z  ← 几乎同时
```

→ 完全可以接进「每 10 分钟结算一次」的战报流水线。

### 2.6 成本与额度

| 档 | 价格 | 额度 | 限流 | 历史 |
|---|---|---|---|---|
| **Free** | $0 | **500 次/月** | 10 次/分钟 | 30 天 |
| Starter | $15/月 | 10,000 | 30/分 | 90 天 |
| Pro | $59/月 | 250,000 | 100/分 | 90 天 |

**真实用量（2026-10-09 实测）**：Cito `recent` 在 40 小时里只有 100 场（≈62 场/天），
但**我们只对「会被预告筛选登记的场次」要数据 —— 实测 40 小时里只有 4 场（≈2.4 场/天）**。

→ 消耗 ≈ `1 次 recent/轮有到期的 + 1 次 player-stats/场` ≈ **120~200 次/月**，
**免费档 500 次/月够用**。但要有**额度守卫**：连续 N 次（或月度计数超阈值）就自动降级，
免得大比赛日把额度烧光后连战报都受影响（Cito 超限是**直接拒绝**，不是静默降级）。

### 2.7 三个必须记住的坑

1. 🔴 **`maps[].dataAvailability.playerStats.dataAvailable` 恒为 `false`，完全不可信。**
   实测 5 场**每一场、每一张图**都是 `false`（`unavailableReason: "not_yet_indexed"`），
   而实际全都**有完整 stats**。**判有没有必须直接请求**，绝不能信这个预计算标志位。
2. 🔴 **`Liquipedia:Matches` 页面上根本没有 HLTV 链接。**
   实测该页 295,830 字节 HTML 里 `hltv` 出现 **0 次**、`pageid=188` **0 个**、
   `23xxxxx` 形态数字 **0 个**。→ 原「方案 B：从赛程页读 `|hltv=<系列赛 id>`」**在赛程页上不成立**
   （那条线索只存在于赛事页 / wikitext，要额外解析一层，代价大、收益低，见 §4.5）。
3. 🟡 **CS2 是 beta**。端点可能变；且 Cito 是第三方聚合器，**合规上不是官方源**。

---

## 3. 数据通路：新增在哪一环

现状（`--results`，每 10 分钟由 `douyu-esports-results.timer` 拉起）：

```
读待结算清单 → 有到窗口的场次吗？
   └─ 有 → 抓 Liquipedia 赛程页 → 判定「已结束」→ 抓赛事页拿逐图比分 → 渲染 → 发送 → 落盘
```

**接入后只在最后多一步**（新增为【新】）：

```
读待结算清单 → 抓 Liquipedia 赛程页（不变）→ 判定已结束（不变）
   → 抓赛事页拿逐图比分（不变）
   → 【新】用「队名对 + 时间」定位 Cito matchId（§4）
   → 【新】GET /cs2/matches/{id}/player-stats → 逐图选手行
   → 【新】按「已打的图」分组成 row["players"]（§5）→ 交给现有 _aggregate_players()
   → render_card（不变，展示层自动带上选段）→ 发送 → 落盘
```

**为什么改动这么小**：展示层是「数据源无关」的 —— `_aggregate_players()` 的输入契约
（见 §5）与 `player-stats` 的返回天然同构，`render_card` / 模板 / Pillow 版式**一行都不用动**。

---

## 4. 匹配：唯一的真难点 —— **已实测解决（100%）**

### 4.1 实测数据（2026-10-09，用本机 + 免费 key）

| 口径 | 结果 |
|---|---|
| Liquipedia 赛程页已结束场次 | 50 场（页面共 68 场） |
| 其中落在 Cito `recent` 窗口内 | 15 场 |
| **窗口内匹配命中** | **15 / 15 = 100%** |
| 「会被预告筛选登记」的场次命中 | **4 / 4 = 100%** |

时间差：**14 场 Δ0.0 分钟、1 场 Δ5.0 分钟**（`Team Spirit vs M80`——Liquipedia 报计划时间
18:10，Cito 报 18:05）。→ **±6 小时容差绰绰有余**，甚至 ±1 小时都够。

### 4.2 匹配靠的是「归一化」，不是别名表

第一轮用朴素做法只拿到 **43.8%**（7/16），漏掉的 9 场**全部是同一类原因**：
队名写法差异，而**不是**数据缺失。修正后 **100%**。规则（按有效性排序）：

1. **前缀 + 后缀「组合」剥离**（最容易漏的一条）。
   `FC Famalicão Esports` 要**同时**去掉 `fc ` 和 ` esports` 才能得到 `famalico`
   —— 只剥一层就永远匹配不上 Cito 的 `Famalicão`。
   实测剥离表：前缀 `team ` / `fc ` / `the `；后缀 ` esports` / ` gaming` / ` clan` /
   ` team` / ` organization` / ` org`。
2. **标点与音标归一**：`re.sub(r"[^a-z0-9]+", "", s.lower())`。
   巧合但好用：`Famalicão` → `famalico`，与 Cito 的 slug 一致。
3. **`hltv_aliases` 反向用**（Liquipedia → HLTV）。Cito 的队名与 HLTV 同源，
   如 `Team Spirit` → `Spirit`、`FaZe Clan` → `FaZe`、`Sangal Esports` → `Sangal`。
   （其实 1+2 已能覆盖大部分，反向别名是双保险。）
4. **少量 Cito 专属别名**（无法机械推导，只能列出来）：
   `Natus Vincere Junior` → `NAVI Junior`；`Rebels Gaming` → `RBLS`；`WhiteBIT Team` → `WBT`。
   这张表与 `hltv_aliases` 同性质，**规模小、只在「上镜」的队上维护**。

**判据**（三条同时成立）：
① 两队名**互为同一对**（用变体集合求交，**方向无关**）；
② 时间差在 `cito_match_tolerance_hours`（默认 **6**）内；
③ 多义（同一对打了两场）取时间最近的。

> ⚠️ **不要用子串包含**（`_team_same` 那种）做这里的主判据：`NAVI` 是 `NAVI Junior`
> 的子串，会张冠李戴。用**归一化后的变体集合求交**（集合相等语义，没有子串语义）。

### 4.3 查 matchId 的两条路径

| 路径 | 调用 | 什么时候用 |
|---|---|---|
| **主：扫 `recent`** | `GET /cs2/matches/recent?limit=100` | 结算窗口 ≤ 数小时，目标必然还在最新 100 场里。**1 次调用覆盖本轮所有场次** |
| **兜底：按队伍查** | `GET /cs2/teams/{id}/results?limit=20` | 目标掉出 100 场窗口时（跨天补漏 / `backfill_abandoned`）。先 `search?q=` 或直试 `teams/{slug}` 拿 id |

`recent` 的时间跨度实测 **40 小时/100 场**（≈62 场/天）。战报在开打 +1~3 小时抓，
目标排在新 100 场的**前 10~20 位**，稳。

### 4.4 反面证据（证伪过的东西，别再试）

- ❌ **Liquipedia 页面里的 `hltv.org/?pageid=188&matchid=240555`**：那是 HLTV **单图** stats id，
  `cs2-match-240555` 实测 → `CS2 match not found`。（而且 `Liquipedia:Matches` 页上压根没有这种链接，§2.7 坑 2。）
- ❌ **`search?q=` 当主路径**：它只对**有词条的队**有效。实测 `Rebels Gaming` / `WhiteBIT`
  返回 0 项（Cito 里叫 `RBLS` / `WBT`），反而更不可靠。
- ❌ **靠 slug 猜队伍**：`Famalicão` 的 slug 是 `famalico`（去音标），猜不出来。

### 4.5 决策

**用 §4.3 的「主 + 兜底」两跳，别名表按 §4.2 维护。**
原「方案 B（解析 wikitext 的 `|hltv=`）」**降为不做** —— 赛程页没有这条线索，
要额外抓赛事页 wikitext 才能拿到，代价大、且只覆盖 ~82% 的场次；
而队名匹配已实测 100%，没有理由引入更脆的解析。

---

## 5. 数据结构映射（展示层契约）

现有 `_aggregate_players(row)` 的输入契约（**不要改**）：

```python
row["players"] = [
    {"map": "Dust2", "players": [
        {"name": "makazze", "team": "NAVI", "k": 23, "d": 15, "a": 6,
         "pm": 8, "adr": 104.6, "kast": 78, "rating": 1.96}, …]},
    …  # 每张图一项；players 为空列表的图会被跳过
]
```

`player-stats` → 该结构的映射：

| 目标字段 | 来源 | 备注 |
|---|---|---|
| `map` | `recent`/`matches{id}` 的 `maps[].mapName` | 用 `mapId` 尾号 `-map-N` 对 `mapNumber`；**只取 §2.4 已打的图** |
| `name` | `playerName` | |
| `team` | `teamName` **改写为 Liquipedia 写法** | 🔴 见下 |
| `k` / `d` / `a` | `kills` / `deaths` / `assists` | 整数；`a` 最终会被展示层丢弃 |
| `pm` | `plusMinus` | 聚合后不用，可省 |
| `adr` | `adr` | 直接用 |
| `kast` | `kast` **× 100** | ⚠️ 源是 0~1 比例，展示层要百分比数字 |
| `rating` | `rating` | Rating 3.0 |

`_aggregate_players()` 返回的每行是：
`{"name", "team", "k", "d", "adr"(均值), "kast"(均值), "rating"(均值)}`
—— **只有这几个键**，没有 `a` / `pm`。

### 5.1 🔴 队名必须改写回 Liquipedia 写法

`build_result_match()` 用 **`_team_same()`（双向子串包含）**把选手分成左右两列：

```python
rows_a = [r for r in prows if _team_same(r["team"], teams[0])]
rows_b = [r for r in prows if _team_same(r["team"], teams[1])]
```

而 Cito 的短写名过不了这一关：

| Cito | Liquipedia | `_team_same` |
|---|---|---|
| `Spirit` | `Team Spirit` | ✅（`spirit` ⊂ `team spirit`） |
| `M80` / `SAW` / `Lazer Cats` | 同名 | ✅ |
| `RBLS` | `Rebels Gaming` | ❌ **整列空** |
| `NAVI Junior` | `Natus Vincere Junior` | ❌ 整列空 |
| `WBT` | `WhiteBIT Team` | ❌ 整列空 |

**→ 组装 `row["players"]` 时，把 `team` 直接写成「我们找到的那一场的 Liquipedia 队名」**
（匹配时已知谁对谁），彻底不依赖 `_team_same` 的巧合。**这是必须做的一步。**

### 5.2 已跑通的实证

`cs2-match-2399031`（SAW vs Lazer Cats, Bo1）喂进 `_aggregate_players()` 的真实输出：

```
有数据的图数 = 1
 krazy     SAW        21-15  ADR  97.1  KAST 82.6  Rating 1.62
 tripex17  Lazer Cats 26-17  ADR 100.3  KAST 69.6  Rating 1.25
 NOPEEj    SAW        18-16  ADR  80.9  KAST 78.3  Rating 1.20
 ...
```

**展示层零改动即可消费** —— 这就是整个方案成立的核心依据。

---

## 6. 降级与失败（**绝不能因为选手段而让战报发不出去**）

沿用现有三级降级里「选段可缺」的口径，**任何一步失败都只是少画一段**：

| 情况 | 行为 |
|---|---|
| 匹配不到 matchId | 少画选段，`[info]` 记一笔（含队名与时间，便于事后排查） |
| `player-stats` 返回空 / 报错 | 少画选段，`[warn]` 记一笔 |
| 超限（429 / 额度用尽） | 当成抓取失败，降级；**不要重试刷屏** |
| 拿到了数据 | 按「已打的图」分组填 `row["players"]`，交给现有渲染栈 |

**绝不**：伪造/插值、失败时在群里发错误、因为选段失败而重发战报。

---

## 7. 成本与节流

- **调用预算**：每轮结算 `≤1 次 recent + N 次 player-stats`（N = 该轮到期的场次数）。
  实测 N ≈ **2.4 场/天** → **~200 次/月**（免费 500）。
- **限流**：免费档 10 次/分钟。一轮最多 N+1 次、间隔 10 分钟 → 远低于限流。
- **额度守卫**：`state_cito_usage.json` 记月度计数；超阈值自动降级 + `[warn]`。
- **幂等**：同一场只抓一次（抓到后写进 `state_results_pending.json` 的快照，
  与逐图比分同机制），重复运行不再请求。
- **不要**在预告 / 整合版 / 开赛提醒里调用 Cito —— 那些流水线**保持零网络**。
  实测确认：`row["players"]` 的**唯一消费者**是单场战报的渲染栈
  （`_render_result_card_inner` → `_draw_player_block`）；整合版
  （`build_daily_results_data` / `_render_daily_results_card_inner`）**只画系列比分，
  压根不读 `players`**，所以**不需要**给整合版存选段快照，也不用在次日重查。
  自检有 AST 结构断言钉住「`run_daily` / `run_announce` / `run_once` 里不许出现 Cito 调用」。

---

## 8. 风险分级表

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| 1 | ~~匹配率低~~ | ~~中~~ → **实测 100%，已消解** | — | §4 已实测 |
| 2 | 目标掉出 `recent` 100 场窗口 | 低 | 中 | `teams/{id}/results` 兜底（§4.3） |
| 3 | 免费额度用尽 | 低 | 中 | 实测 ~200/月；额度守卫自动降级 |
| 4 | 误把「未打的图」画进卡片 | **中** | 中 | §2.4 按 `resultType=="played"` 过滤；自检钉住 |
| 5 | 队名没改写回 Liquipedia 写法 → 整列空 | **中** | 中 | §5.1；自检用 `RBLS`/`NAVI Junior` 做样例 |
| 6 | CS2 仍 beta，端点变更 | 低 | 中 | 抓取层独立、失败即降级；不阻塞主流程 |
| 7 | 第三方聚合器合规风险 | 低 | 中 | 仅自用、低频；文档留痕（本文） |
| 8 | `kast` 单位搞错（比例当百分比） | 中 | 低 | §5 映射表 + 自检钉住「×100」 |
| 9 | 误信 `dataAvailability` 标志位 | 中 | 中 | §2.7 坑 1；自检用「直接请求」判定 |

---

## 9. 自检清单（离线，绝不联网）

- [ ] **队名归一**：组合剥离（`FC Famalicão Esports` ↔ `Famalicão`）/ 音标 / 反向 `hltv_aliases` / 3 条 Cito 别名
- [ ] **匹配判据**：同名对命中 / **顺序无关** / 时间超容差不命中 / 多义取最近 / **`NAVI` 不匹配 `NAVI Junior`**
- [ ] **`mapId` → 图名**：`-map-N` 对 `mapNumber`；**未打的图被过滤掉**
- [ ] **`kast` ×100**（`0.652` → `65.2`）
- [ ] **队名改写**：Cito `RBLS` → 输出 `Rebels Gaming`（保证 `_team_same` 能分列）
- [ ] 空 `player-stats` → `row["players"]` 为空 → `_aggregate_players` 返回 0 图（不炸）
- [ ] 字段缺失（无 `adr` / 无 `assists`）→ 按 0 处理，不抛异常
- [ ] **AST 结构断言**：Cito 请求跑在**锁外**（与铁律 ③ 同款）；Cito 请求**不在**预告/整合版/开赛提醒路径里
- [ ] `cito_enabled=false` 时**一次网络请求都不发**
- [ ] 降级：拿不到数据时战报**照发**（纯文本/少一段）
- [ ] 变异测试：把上面每条断言对应的 bug 改回去 → 确认 FAIL

---

## 10. 实施步骤

1. ✅ **匹配率实测 —— 已做，100%（§4.1）。方案成立，继续。**
2. `esports.py` 新增：
   - Cito 客户端（`urllib`，**零新增依赖**）—— `cito_get(path, params)` / 超时 / 降级 / 额度守卫；
   - `cito_name_variants()`（§4.2 归一）+ `cito_match_key()`（§4.2 判据）；
   - `fetch_cito_match_id(...)`（§4.3 主 + 兜底）+ `parse_cito_player_stats(rows, maps)`（§5 → `row["players"]`）；
   - 在 `--results` 的结算点接入（**锁外、失败降级**）；
   - 配置键：`cito_enabled`（默认 **false**）、`cito_api_key`、`cito_match_tolerance_hours`（默认 6）。
3. 自检 + 变异测试（§9）。
4. `pack_deploy.py` / `config.example.json` / `README.md` / `ESPORTS.md` 同步。
5. **先给用户渲染一张真实数据的预览卡片确认版式**（项目纪律：改版式前必须预览）。
6. 三自检 + check-secrets 全绿 → commit → 重打 `deploy.zip` → 部署。

**回滚**：`cito_enabled=false`（一行配置）即回到「无选段」的现状；代码留着不影响任何流水线。

---

## 11. 未验证 / 待确认

| # | 项 | 为什么没验 | 怎么补 |
|---|---|---|---|
| 1 | ~~真实匹配率~~ | — | ✅ 已验 100%（§4.1） |
| 2 | **Bo5 是否给满 50 行** | 样本里没有 Bo5 打完的场次 | 上线后看日志；按 §2.3 推断 = 10 × 已打图数 |
| 3 | 队名别名表的**全量**覆盖率 | 只覆盖近 40h 的 15 场 | 上线后按 `[info]` 漏配日志逐步补表 |
| 4 | 免费额度的真实消耗曲线 | 只估了量级（~200/月） | 上线后看 Cito dashboard 用量 |
| 5 | `search?q=` 对中文/变音队名的召回 | 只试了 5 个队 | 只在兜底路径用，失败即降级，不阻塞 |

---

## 12. 与现有文档的关系

| 想知道 | 看哪 |
|---|---|
| 赛事推送口径、窗口、降级、排查 | `deploy/ESPORTS.md` |
| 选手段的历史与现状 | `ESPORTS.md` §3.2 / §9 |
| **选手段数据源（Cito）设计与实施** | **`deploy/CITO_PLAYERS.md`（本文）** |
| 群内命令交互 | `deploy/CMD_INTERACT.md` |
| 旧部署怎么更新到新版 | `deploy/UPDATE_PROMPT.md` |
