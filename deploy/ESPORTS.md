# CS2 每日赛程预告（esports.py）

> 独立功能，和主播开播提醒**互不影响**。关掉它、删掉它，斗鱼那边照常工作。
> 每天由 `douyu-esports.timer` 在北京时间 **09:30** 拉起一次。

---

## 1. 它做什么

每天抓一次 [Liquipedia](https://liquipedia.net/counterstrike/Liquipedia:Matches) 的 CS2 赛程，
挑出**今天还没开打**、且满足下面任一条的比赛，推一条到群里（通道沿用 `config.json` 的 `channels`）：

| # | 条件 | 关系 |
|---|---|---|
| 1 | **有中国队参赛** —— 队名精确命中 `esports.cn_teams` | 无条件发 |
| 2 | **有世界前 N 的队伍参赛** —— 队名精确命中从 HLTV 抓来的排名 | 无条件发 |
| 3 | **大赛 且 有知名队伍** —— 赛事名命中 `major_keywords`，**并且** 队名命中 `notable_teams` | **两条都满足**才发 |
| 4 | **名队云集的赛事** —— 同一赛事当天凑够 `tournament_min_known_teams` 支「已知队伍」 | 该赛事当天的比赛整体放行 |

四条是**或**的关系（1、2 是「无条件」，3 内部是「与」）。同一场同时满足多条时，
日志只记按 **中国队 → 世界前 N → 大赛+知名 → 名队云集的赛事** 顺序命中的**第一条**
（`[info] 入选依据…`），方便回头查「它到底是怎么进来的」。

> 第 3 条是 2026-10-05 按用户要求**收紧**的：改成「与」之后，只满足大赛
> （比如某赛事的地区预选赛、对阵里没有名队）或只满足有知名队伍（小赛事）都**不再单独推送**。
> 结果就是噪音明显下降 —— 同一天从「6 场里混着预选赛」变成「清一色 ESL Pro League」。

### 「名队云集的赛事」怎么数

`tournament_min_known_teams`（默认 4）指的是：**同一个赛事名下**、**今天还没开打**的场次里，
出现过多少支**不同的**「已知队伍」。已知队伍 = `notable_teams` ∪ 世界前 N。

- 同一支队在同一个赛事里打两场，**只算一支**。
- **不跨赛事凑数** —— 两个赛事各 2 支，不会凑成 4 支。
- 中国队**不计入**这个数（中国队有自己那条「无条件发」，两件事分开才好解释）。
- 凑够之后，**该赛事当天的所有比赛**都会推，包括一对无名队那场 —— 这是刻意的：
  「名队云集的赛事整体捞进来」比「只挑有名队的场次」更符合看比赛的习惯。

推送样子（2026-10-05 实测输出）：

```
【CS2 今日赛程】2026-10-05

17:00  PARIVISION vs FURIA · Bo3 · ESL Pro League Season 24 - Round 3
17:00  ShindeN vs G2 Esports · Bo3 · ESL Pro League Season 24 - Round 3
19:30  9z Team vs Natus Vincere · Bo3 · ESL Pro League Season 24 - Round 3
19:30  Legacy vs 1w Team · Bo3 · ESL Pro League Season 24 - Round 3
22:00  Team Spirit vs MOUZ · Bo3 · ESL Pro League Season 24 - Round 3
22:00  M80 vs TYLOO · Bo3 · ESL Pro League Season 24 - Round 3

共 6 场。
数据来源：Liquipedia
```

对照日志：`中国队 1 场 / 世界前 N 5 场 / 大赛+知名 0 场 / 名队云集的赛事 0 场` ——
6 场里 5 场是因为有前 15 队伍（对上 G2 / NaVi / Legacy / FURIA / Spirit），
`M80 vs TYLOO` 按优先级记在中国队名下（它两边都不在前 15）。
「大赛+知名」是 0 是因为这几个队都已经由前 15 那条先捞走了 —— **不是这条坏了**。

### 不做什么

- **不报比分、不报结果**。这是一条「今天有什么可看」的预告，不是赛果播报。
- **不做高频轮询**。Liquipedia 的条款限制 `action=parse` ≤ 1 次 / 30 秒，
  所以每天只请求 1 次，失败重试也至少隔 30 秒。**别把它接进每分钟的定时器。**
- **不猜赛事分级**。页面上真的拿不到 S/A/B 分级（见下面「已知限制」）。
- **不抓 HLTV 的比赛页**，只抓它的排名页（`robots.txt` 明确允许 `/ranking/teams/`，
  被禁的是 `/valve-ranking/teams/details/*` 等）。而且排名一天最多取一次。

---

## 2. 什么时候发、什么时候静默

这三种状态在群里长得**刻意不一样**，就是为了让你一眼分出「没事」和「出事」：

| 情况 | 行为 | 日志 |
|---|---|---|
| 今天有符合条件的比赛 | 发赛程预告 | `[sent]` |
| 今天没有符合条件的比赛 | **静默**（什么都不发） | `[silent]` |
| 静默连续满 7 天（7、14、21…） | 发一条**报平安**，说明「不是故障」 | `[info] 连续静默满 N 天` |
| 抓取失败（重试后仍失败） | 发**抓取失败告警**，明确写「这不等于今天没有比赛」 | `[error]` |
| 页面结构变了、解析出 0 场 | 不发消息，退出码 1，日志说清要重新对选择器 | `[error]` |
| 世界排名抓取失败 | **不影响推送**：另三条依据照常工作，只是少一条。打 `[warn]`，并退回上次的排名缓存 | `[warn] 世界排名抓取失败` |

⚠️ 最后两行的区别很重要：**「抓取失败」和「今天没比赛」绝不能长得一样**，
否则数据源一挂，你会以为「最近真安静」。

还有一条：**发送失败时不写状态**。写进去就等于「今天已经交代过了」，
而消息其实没到任何人手上 —— 那是更糟的静默。

> 世界排名那条为什么只降级、不报错：它只是四条依据之一。HLTV 挂了就让整条
> 赛程预告一起停摆，是拿一个次要依赖去否定一个主要功能。所以它降级 + 留痕，
> 而且**抓不到时不会静默**——日志里一定有一行 `[warn]`。

---

## 3. 定时方式

```ini
# deploy/douyu-esports.timer
OnCalendar=*-*-* 09:30:00 Asia/Shanghai
Persistent=true
```

- **带时区后缀** `Asia/Shanghai`：服务器时区是 UTC 也准点在北京 09:30 跑。
  不带后缀时 systemd 按本机时区解释，改时区会整体平移。
  （时区后缀需要 systemd ≥ 212，Ubuntu 20.04+ 都满足。）
- **`Persistent=true`**：那一刻机器没开着就开机后补跑。
  Daily 定时器错过就是整天不发，而「静默」在这个功能里是有含义的，不能和「没开机」混淆。

想换时间就改这一行，然后：

```bash
systemctl daemon-reload && systemctl restart douyu-esports.timer
systemctl list-timers douyu-esports.timer        # 确认 NEXT 是你要的时刻
```

---

## 4. 配置

**整个 `esports` 段可以不写** —— 不写就全用 `esports.py` 里的内置默认值，功能照常工作。
想改口径时才在 `config.json` 里加上其中某几项（`config.example.json` 里有完整示例）：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关。`false` 时脚本立刻退出，systemd 不会当成失败 |
| `ua_contact` | 项目地址 + 邮箱 | Liquipedia 要求在 User-Agent 里写联系方式，**别删** |
| `major_keywords` | 17 个 | 子串匹配赛事名，大小写不敏感。这是「算不算大赛」的**全部依据** |
| `cn_teams` | 2 个 | **精确匹配**队名。有它就无条件发 |
| `notable_teams` | 42 个 | **精确匹配**队名。要和 `major_keywords` 一起满足第 3 条 |
| `rank_top_n` | `15` | 抓 HLTV 世界排名的前几名做第 2 条。`0` = 整条关掉（连 HLTV 都不请求） |
| `hltv_aliases` | 8 条 | HLTV 队名 → Liquipedia 队名。**只有写法不一致的才要写**，见下面 4.2 |
| `rank_ttl_hours` | `168` | 排名缓存的保鲜期。只在抓取失败时当兜底用，正常每天都重新抓 |
| `tournament_min_known_teams` | `4` | 第 4 条的门槛：同一赛事当天凑够几支已知队伍就整体放行。`0` = 关掉这条 |
| `fold_hint` | `15` | 超过这么多场就只列前 N 场（0 = 不折叠） |
| `calm_after_empty_days` | `7` | 连续静默多少天后发报平安（0 = 从不发） |
| `fetch_retry_max` | `3` | 抓取重试次数（含首次） |
| `parse_min_interval_seconds` | `30` | 重试间隔，**这是条款要求，别往下调** |
| `notify_retry_max` | `3` | 发送失败补发次数 |
| `notify_retry_backoff_seconds` | `15` | 补发间隔 |
| `http_timeout` | `25` | 单次请求超时（秒） |

### ⚠️ 关于 `cn_teams`

**必须是精确队名，不是子串。** 用子串匹配会把
`The MongolZ` / `IHC` / `ATOX` / `NKT` / `Chinggis Warriors` 这类**蒙古国队伍**误判成中国队 ——
这是这个功能最容易搞错的一点。

当前白名单**只有 2 支**：`TYLOO`、`Lynn Vision Gaming`。
（早期版本还收过 `Rare Atom`、`Wings Up Gaming`、`Steel Helmet`、`NewHappy`、
`The Huns`、`Talon Esports` —— 2026-09-30 按用户要求收窄，需要时按 Liquipedia 上的
**全名**加回来，别用缩写。）
发现有漏的中国队，同样按全名加进 `esports.cn_teams`。

⛔ 2026-10-05 实测页面上还有 `JiJieHao` 这类中国队伍不在白名单里。
**用户已明确拍板：不需要**（就保持 `TYLOO` / `Lynn Vision Gaming` 两支）。
以后别再提「要不要加 JiJieHao」——已有决定，别自作主张加回去。

### ⚠️ 关于 `notable_teams`

**同样是精确匹配队名**。它现在**只和第 3 条联动**：必须**同时**是大赛，
才会因为这个名单被推送。单独一场「有知名队伍的小比赛」不再推。

规则和 `cn_teams` 完全一样：**大小写无所谓，措辞必须和页面上一致**。
`Team Liquid` 不等于 `Liquid`，`Ninjas in Pyjamas` 不等于 `NIP`，`PaiN Gaming` 不等于 `PaiN`，
`Natus Vincere` 不等于 `NAVI`。写错了不会报错，**只会静默漏掉比赛** —— 这是这个名单
唯一危险的失败方式。

**学院队是独立队名**，精确匹配下不会误收：`MOUZ NXT` 不命中 `MOUZ`，
`Natus Vincere Junior` 不命中 `Natus Vincere`，`Falcons Force` 也不命中 `Team Falcons`。
（自检里有断言盯着这几条。）

42 支**全部**用 Liquipedia 的 `prop=categories` 核对过：页面类里带「… Teams」的才是战队页。
之所以要这么核，是因为 Liquipedia 上存在**和战队同名、但其实是选手**的页面 ——
`Spirit` 和 `Aurora` 就是（详见 4.2）。

> 2026-10-05 核出并修掉一个真 bug：名单里原来写的是 `FURIA Esports`，
> 而 Liquipedia 上它是**重定向**到 `FURIA` 的，比赛页显示的是 `FURIA` ——
> 写着等于永远匹配不上。**改名不会报错，只会静默漏发**，所以只能靠逐个核对。

---

## 4.1 核队名：`--teams`

改名单前先跑这个，它把页面上的队名**原样**打出来，并标出哪些已收录：

```bash
cd /opt/douyu-live-notify && python3 esports.py --teams
```

输出样子：

```
页面共 63 场，出现 46 个不同队名。
  [知名][世界前15]  Astralis                     出场 4 次
  [知名][世界前15]  FURIA                        出场 3 次
  [世界前15]      BetBoom Team                 出场 3 次
  [中国队]        Lynn Vision Gaming           出场 2 次
  [未收录]        MOUZ NXT                     出场 1 次
  ...
未收录 29 个 —— 其中觉得算「知名」的，抄进 notable_teams 即可。
```

标记可以是叠加的（`[知名][世界前15]` 就是两条都命中）。它**不发送、不写状态、
也不写排名缓存**，而且不需要 `config.json` 配好（`room_id`、`onebot` 没填也能跑）。
想把某队加进去，把打印出来的名字**原样**抄进 `config.json` 即可 ——
这正是为了避免「手打队名打错一个词，静默漏一场」。

> 另注：`TBD` 会作为队名出现在列表里（对阵还没定的场次），`--teams` 里忽略它就行，
> 筛选阶段本来就会把 `TBD vs TBD` 整场丢掉。

---

## 4.2 核世界排名：`--rank`

世界排名从 HLTV 抓，比赛页在 Liquipedia，**两边的队名写法不一样**。
HLTV 写 `Spirit`，Liquipedia 的**战队页**叫 `Team Spirit`。照原样拿 HLTV 的名字去匹配，
会**全部匹配不上、而且不报错**。

所以有一张手工核对的映射表 `esports.hltv_aliases`。改排名相关的东西之前先跑：

```bash
cd /opt/douyu-live-notify && python3 esports.py --rank
```

输出样子：

```
HLTV 世界前 15（来源：hltv）

  名次    HLTV 写法            → Liquipedia 队名
  #1    Spirit             → Team Spirit   （别名表）
  #9    Aurora             → Aurora Gaming   （别名表）
  #13   FaZe               → FaZe Clan   （别名表）
  #15   BETBOOM            → BetBoom Team   （别名表）
  #3    FURIA              → FURIA   （同名，已知名单里核对过）
  ...
本轮实际用于匹配的队名（15 个）：Team Spirit、Team Vitality、…
```

每行末尾会说明「这个映射是怎么来的」：别名表 / 同名已核对 / ⚠️ 没核对过。
**出现 ⚠️ 就说明有队伍可能漏发**，这时按它的 HLTV 写法去 Liquipedia 查真实队名，
补进 `esports.hltv_aliases` 即可。运行时也有同样的告警：

```
[warn] 世界前 15 里有 2 支队伍的写法没核对过，可能匹配不上 Liquipedia：
        第 7 名 FUT
        第 15 名 BETBOOM
        核对办法：`--teams` 看页面真实队名；确认后写进 esports.hltv_aliases。
```

### ⚠️ 千万别用「查重定向自动补全」的办法

看起来很聪明：HLTV 给 `Spirit`，去 Liquipedia 查一下 `Spirit` 重定向到哪，就自动得到
`Team Spirit`。**但这条路是错的**，实测过：

| HLTV 写法 | Liquipedia 上的 `Spirit` / `Aurora` 其实是什么 | 正确的战队页 |
|---|---|---|
| `Spirit` | **选手页** —— 「Dmitriy "spirit" Veko，白俄罗斯教练」 | `Team Spirit` |
| `Aurora` | **选手页** —— 「Aurora "aurora" Lyngdal，挪威选手」 | `Aurora Gaming` |

自动补全会把「世界第一」映射到一个选手身上，然后**静默失效** ——
看似规则在跑，实际一支队都匹配不到。所以这张表只能**逐个核对后手工维护**，
核对手段是 Liquipedia 的 `prop=categories`：类里带「… Teams」的才是战队页。

同理，`FURIA Esports` 是 `FURIA` 的重定向，`Team Spirit` 和 `Spirit` 是两个不同的页 ——
**「页存在」不等于「这个写法是对的」**。

---

## 5. 已知限制（先知道，免得白排查）

| 限制 | 影响 | 怎么办 |
|---|---|---|
| 页面上**没有赛事分级**（S/A/B） | 「是不是大赛」只能靠赛事名认关键词，可能漏掉没收录名字的大赛；反过来，认了某个关键词就会连它的**各区预选赛**一起捞进来（例如 `eXTREMESLAND 2026: OCE Qual` 是大洋洲预选，里面并没有中国队） | 往 `major_keywords` 加/删关键词。不过自从第 3 条改成「与」之后，光认关键词已经不会误发了 —— 预选赛里没有知名/前 N 队伍就进不来 |
| 页面上**没有国籍 / 地区字段** | 中国队只能靠手维护白名单，新队伍要手工加 | 同上，往 `cn_teams` 加 |
| 队名**必须措辞完全一致** | 手打队名差一个词就**静默漏掉**，且不报错 | 用 `esports.py --teams` 把页面上的名字原样抄出来 |
| **HLTV 与 Liquipedia 队名写法不同**（`Spirit` vs `Team Spirit`、`G2` vs `G2 Esports`…） | 「世界前 N」这条要么匹配不上（漏发），要么被**自动映射**到一个同名的**选手页**上（更糟：静默漏发） | **别用查重定向自动补全**。手工维护 `hltv_aliases`，用 `esports.py --rank` 核对（见 [4.2](#42-核世界排名--rank)） |
| **HLTV 排名页抓不到**（对方改版 / 限流 / 断网） | 「世界前 N」这条当轮失效 —— 但**只丢这一条** | 抓取失败只打 `[warn]`，另外三条照常；另有最多 7 天的本地缓存兜底 |
| 数据源只往后看约 **5 天** | 「今天没比赛」是准的，但**不能据此推断「本周都没比赛」** | 报平安的文案里没写「本周无赛事」就是这个原因 |
| 只推「还没开打」的场次 | 09:30 发出去时，今天凌晨那几场不会列 | 这是刻意的：预告列已开打的场次只会让人误以为还有得看 |
| 页面混着**已结束**的比赛 | 不过滤会刷一堆旧比分 | 已按「今天 00:00 之后 + 此刻之后」两道过滤 |
| 同时开打的两场**时间戳完全相同** | 按时间戳去重会**静默吞掉**比赛（早期版本漏掉 11 场） | 解析器刻意**不去重** |

---

## 6. 排查

先看日志，它把每一步都打出来了：

```bash
tail -n 50 /var/log/douyu-watch/esports.log
```

| 症状 | 真实原因 | 对策 |
|---|---|---|
| 没收到消息，日志是 `[silent]` | 今天确实没有符合口径的比赛 | 正常。想放宽就加关键词/队伍 |
| 该发的比赛没发，日志却是 `[silent]` | 队名**措辞**和白名单对不上（不报错的静默漏发） | 跑 `esports.py --teams` 核对真实队名，原样抄进白名单 |
| 有世界前 N 的队伍参赛，却没发 | HLTV 的写法没映射到 Liquipedia 队名 | 跑 `esports.py --rank`，看谁后面标了「未映射」；确认真实队名后写进 `esports.hltv_aliases` |
| 日志有 `[warn] 世界排名抓取失败` | HLTV 抓不到（改版 / 限流 / 断网） | 不是致命错误，另外三条照常；下一轮会自动重试，也可用缓存兜底 |
| 某个赛事明明「名队云集」却没进 | 已知队伍数没到 `tournament_min_known_teams`（默认 4） | 跑 `--check` 看那行 `[info] 名队云集…`；确属漏判就调低阈值或往 `notable_teams` 补队 |
| 没收到消息，日志有 `[error]` | 抓取失败或发送失败 | 见下一行 |
| `HTTP 406` | User-Agent 或 gzip 不合格 | 检查 `esports.ua_contact` 别留空 |
| `HTTP 403` / `429` | 被封或限流 | 提高 `parse_min_interval_seconds`，等一会儿 |
| `HTTP 5xx` | 对方服务端问题 | 会自动重试；持续失败就等 |
| `一个比赛都没解析出来` | **页面结构变了** | 见下面「解析器」 |
| 消息发不出去 | NapCat 挂了（和开播提醒同一个前提） | 用看门狗那套通道排查 |
| 报平安从没发过 | 连续静默还没满 7 天 | 正常；想验证就临时把 `calm_after_empty_days` 设成 1 跑一次 |

### 手工看今天会发什么（**不发消息、不写状态**）

```bash
cd /opt/douyu-live-notify && python3 esports.py --check
```

这是上线前唯一该做的验证，也是出问题时的第一诊断手段。日志里的 `[info] 页面共 N 场，
其中……M 场` 直接把「抓到了多少」和「筛完剩多少」分开报，一眼看出是哪一步不对。

紧接着两行说明这 M 场各自是怎么进来的：

```
[info] 世界前 15：Team Spirit / Team Vitality / …（来源：hltv）
[info] 入选依据（每场只记第一条命中的）：中国队 1 场 / 世界前 N 5 场 / 大赛+知名 0 场 / 名队云集的赛事 0 场
[info] 名队云集（已知队伍 >= 4 支）的赛事：ESL Pro League Season 24 - Round 3（入选 6 场）
```

- 第一行是当轮**世界排名**的解析结果和来源（`hltv` = 当场抓的，`cache` = 用的缓存）。
- 第二行四个桶按**优先级**排（中国队 → 世界前 N → 大赛+知名 → 名队云集的赛事），
  **每场只记第一条命中的**，所以四个数加起来正好等于 M。
- 第三行单独列出「名队云集」的赛事 —— 因为那条是**兜底**：命中它的场次往往已经被
  更靠前的依据捞走了，所以上面那个桶可能显示 0，但第三行照样 6 场（说明这 6 场里
  有几场其实是靠「赛事整体放行」才该进来的）。

`--check` 还会**逐场**打印每场的入选理由，比只看汇总更直接。

调白名单用 `--teams`（见 [4.1](#41-核队名--teams)），校世界排名别名用 `--rank`（见 [4.2](#42-核世界排名--rank)）。

---

## 7. 解析器（改选择器时看这里）

`parse_matches()` 把页面的渲染 HTML 切成一堆比赛。三条实测踩出来的规矩：

1. **锚点是 `<div class="match-info"`**，不是 `data-timestamp`。
   一个页面里计时器有 125 个，比赛只有 75 场；用时间戳当锚点会多切出一堆东西。
2. **不能按 `data-timestamp` 去重**。同时开打的两场时间戳一模一样，去重会静默吞比赛。
3. **队名取 `<a title="...">` 的全名**（内层文本是 `FLY`、`NAVI Jr.` 这类缩写），
   并剥掉无独立词条队伍的 `" (page does not exist)"` 后缀；
   **赛事名取 `.match-info-tournament-name` 里的可见文本**，
   而不是 `title` 属性那种 `EXTREMESLAND/2026/Oceania#Group_A` 页面路径。

⚠️ **同一份解析器也在 `check-esports-net.py` 里**（那是上线前的连通性自检，
独立可跑所以没 import 本文件）。**改选择器时两个文件都要改。**

---

## 8. 自检

```bash
python3 esports.py --selftest    # 106 项：解析器 / 四条筛选 / 时间窗边界 / 折叠 / 静默计数 / 白名单 / 排名与别名 / 文案
```

离线、不联网、不发消息、不写状态。覆盖了几个容易写错的边界：

- 恰好此刻开打 → 保留；此刻前 1 秒 → 丢掉
- `TBD vs TBD`、队名没解析全 → 都丢掉
- 蒙古队**不**算中国队
- 精确匹配不误收学院队：`MOUZ` ≠ `MOUZ NXT`、`Natus Vincere` ≠ `Natus Vincere Junior`
- 措辞差异算不匹配（`PaiN` ≠ `PaiN Gaming`），但大小写差异**不算**（`heroic` = `HEROIC`）
- 空日在第 7、14 天各发一次报平安（不是满 7 天后天天发）
- 有比赛时静默计数归零
- 四条依据的优先级：中国队 → 世界前 N → 大赛+知名 → 名队云集的赛事
- **只有大赛、没有知名队伍 → 丢掉**（第 3 条是「与」不是「或」）
- 「名队云集的赛事」计数：同队去重、不跨赛事累加、中国队不计入、`min_known=0` 可关闭
- HLTV 排名解析：只取前 N、空 HTML 不炸、别名大小写不敏感、未映射的名字会被报出来
- **别名指向选手页的坑有断言盯着**（`Spirit` 必须映射到 `Team Spirit`，绝不能是选手 `spirit`）
- 推送正文末尾**必须**署名 Liquipedia

---

## 9. 合规（对 Liquipedia 和 HLTV）

### Liquipedia

内容 CC-BY-SA 3.0，用之前请确认这三条都满足：

1. **推送正文末尾署名「数据来源：Liquipedia」** —— `format_daily` / `format_calm` 已内置，
   改文案时别把这行删了（自检里有断言盯着）。
2. **`User-Agent` 带项目名 + 联系方式** —— `esports.ua_contact`。
3. **`action=parse` ≤ 1 次 / 30 秒，且只走 `api.php`、不抓渲染好的 HTML 页面** ——
   每天 1 次、重试隔 30 秒，已满足。**别把 `--check` 放进循环里跑。**

### HLTV

「世界前 N」那条要抓一次 `https://www.hltv.org/ranking/teams/`（会 302 到当期排名页）。两件事：

1. **只抓排名页，不抓比赛页。** `robots.txt` 明确允许 `/ranking/teams/`；
   被禁的是 `/valve-ranking/teams/details/*` 这类细节页 —— 别去碰。
2. **一天最多取一次，并落本地缓存**（`rank_cache.json`，默认 7 天有效）。
   抓取时会带项目自己的 `User-Agent`；**别把 `--rank` 放进循环里跑**（它也真的去请求 HLTV）。

排名抓取的失败**不影响**正常推送：拿不到就用缓存，缓存也没有就只跳过「世界前 N」
这一条，日志打 `[warn]`，另外三条照常。
