# CS2 每日赛程预告（esports.py）

> 独立功能，和主播开播提醒**互不影响**。关掉它、删掉它，斗鱼那边照常工作。
> 每天由 `douyu-esports.timer` 在北京时间 **09:30** 拉起一次。

---

## 1. 它做什么

每天抓一次 [Liquipedia](https://liquipedia.net/counterstrike/Liquipedia:Matches) 的 CS2 赛程，
挑出**今天还没开打**、且满足下面任一条件的比赛，推一条到群里（通道沿用 `config.json` 的 `channels`）：

- **大赛** —— 赛事名命中 `esports.major_keywords` 里的关键词
- **有中国队参赛** —— 队名精确命中 `esports.cn_teams` 白名单
- **有知名队伍参赛** —— 队名精确命中 `esports.notable_teams` 白名单

三条是**或**的关系，一场比赛只要沾上一条就会推。同一场同时沾上多条时，
日志里只记按 大赛 → 中国队 → 知名队伍 顺序命中的**第一条**（`[info] 选择依据…`），
方便回头查「它到底是怎么进来的」。

推送样子：

```
【CS2 今日赛程】2026-10-03

14:00  TYLOO vs Lynn Vision Gaming · Bo3 · BLAST Premier Fall
17:30  TYLOO vs Team Liquid · Bo3 · IEM Cologne 2026

共 2 场。
数据来源：Liquipedia
```

### 不做什么

- **不报比分、不报结果**。这是一条「今天有什么可看」的预告，不是赛果播报。
- **不做高频轮询**。Liquipedia 的条款限制 `action=parse` ≤ 1 次 / 30 秒，
  所以每天只请求 1 次，失败重试也至少隔 30 秒。**别把它接进每分钟的定时器。**
- **不猜赛事分级**。页面上真的拿不到 S/A/B 分级（见下面「已知限制」）。

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

⚠️ 最后两行的区别很重要：**「抓取失败」和「今天没比赛」绝不能长得一样**，
否则数据源一挂，你会以为「最近真安静」。

还有一条：**发送失败时不写状态**。写进去就等于「今天已经交代过了」，
而消息其实没到任何人手上 —— 那是更糟的静默。

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
| `cn_teams` | 2 个 | **精确匹配**队名。别用模糊匹配 |
| `notable_teams` | 42 个 | **精确匹配**队名。一线队在小编程赛里的出场靠这条兜底 |
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

### ⚠️ 关于 `notable_teams`

**同样是精确匹配队名**，用途不同：一线强队经常在 `Stake Ranked`、`iBUYPOWER Masters`
这类小赛事里出场，光靠 `major_keywords` 会把它们全漏掉 —— 用「场上有没有名队」兜底。

规则和 `cn_teams` 完全一样：**大小写无所谓，措辞必须和页面上一致**。
`Team Liquid` 不等于 `Liquid`，`Ninjas in Pyjamas` 不等于 `NIP`，`PaiN Gaming` 不等于 `PaiN`，
`Natus Vincere` 不等于 `NAVI`。写错了不会报错，**只会静默漏掉比赛** —— 这是这个名单
唯一危险的失败方式。

**学院队是独立队名**，精确匹配下不会误收：`MOUZ NXT` 不命中 `MOUZ`，
`Natus Vincere Junior` 不命中 `Natus Vincere`。（自检里有断言盯着这两条。）

名单里有 23 支的写法是 2026-09-30 从 Liquipedia 实际页面上核对过的；
另外 19 支按 Liquipedia 惯用写法填的、当时没在本页出现，**首次见到时值得核对一下**。

---

## 4.1 核队名：`--teams`

改名单前先跑这个，它把页面上的队名**原样**打出来，并标出哪些已收录：

```bash
cd /opt/douyu-live-notify && python3 esports.py --teams
```

输出样子：

```
页面共 75 场，出现 60 个不同队名。
  [中国队]      Lynn Vision Gaming           出场 2 次
  [知名]       GamerLegion                  出场 6 次
  [未收录]      MOUZ NXT                     出场 1 次
  ...
未收录 36 个 —— 其中觉得算「知名」的，抄进 notable_teams 即可。
```

它**不联网之外的任何副作用**：不发送、不写状态，也不需要 `config.json` 配好
（`room_id`、`onebot` 没填也能跑）。想把某队加进去，把打印出来的名字**原样**抄进
`config.json` 即可 —— 这正是为了避免「手打队名打错一个词，静默漏一场」。

> 另注：`TBD` 会作为队名出现在列表里（对阵还没定的场次），`--teams` 里忽略它就行，
> 筛选阶段本来就会把 `TBD vs TBD` 整场丢掉。

---

## 5. 已知限制（先知道，免得白排查）

| 限制 | 影响 | 怎么办 |
|---|---|---|
| 页面上**没有赛事分级**（S/A/B） | 「是不是大赛」只能靠赛事名认关键词，可能漏掉没收录名字的大赛；反过来，认了某个关键词就会连它的**各区预选赛**一起捞进来（例如 `eXTREMESLAND 2026: OCE Qual` 是大洋洲预选，里面并没有中国队） | 往 `major_keywords` 加/删关键词 |
| 页面上**没有国籍 / 地区字段** | 中国队只能靠手维护白名单，新队伍要手工加 | 同上，往 `cn_teams` 加 |
| 队名**必须措辞完全一致** | 手打队名差一个词就**静默漏掉**，且不报错 | 用 `esports.py --teams` 把页面上的名字原样抄出来 |
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
紧接着那行 `[info] 选择依据（每场只记第一条命中的）：大赛 X 场 / 中国队 Y 场 / 知名队伍 Z 场`
说明这 M 场各自是怎么进来的 —— 比如发现全靠「大赛」进来的那几场其实都是某赛事的
地区预选赛，就该考虑从 `major_keywords` 里去掉那个关键词。

调白名单用 `--teams`，见上面 [4.1 核队名](#41-核队名--teams)。

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
python3 esports.py --selftest    # 72 项：解析器 / 筛选 / 时间窗边界 / 折叠 / 静默计数 / 白名单 / 文案
```

离线、不联网、不发消息、不写状态。覆盖了几个容易写错的边界：

- 恰好此刻开打 → 保留；此刻前 1 秒 → 丢掉
- `TBD vs TBD`、队名没解析全 → 都丢掉
- 蒙古队**不**算中国队
- 精确匹配不误收学院队：`MOUZ` ≠ `MOUZ NXT`、`Natus Vincere` ≠ `Natus Vincere Junior`
- 措辞差异算不匹配（`PaiN` ≠ `PaiN Gaming`），但大小写差异**不算**（`heroic` = `HEROIC`）
- 空日在第 7、14 天各发一次报平安（不是满 7 天后天天发）
- 有比赛时静默计数归零
- 入选理由的优先级：大赛 → 中国队 → 知名队伍
- 推送正文末尾**必须**署名 Liquipedia

---

## 9. 合规（对 Liquipedia）

内容 CC-BY-SA 3.0，用之前请确认这三条都满足：

1. **推送正文末尾署名「数据来源：Liquipedia」** —— `format_daily` / `format_calm` 已内置，
   改文案时别把这行删了（自检里有断言盯着）。
2. **`User-Agent` 带项目名 + 联系方式** —— `esports.ua_contact`。
3. **`action=parse` ≤ 1 次 / 30 秒，且只走 `api.php`、不抓渲染好的 HTML 页面** ——
   每天 1 次、重试隔 30 秒，已满足。**别把 `--check` 放进循环里跑。**
