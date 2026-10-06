# douyu-live-notify

**一个自用的 QQ 群推送机器人。** 主线是 **CS2 职业赛程推送** —— 每天赛程预告、逐场战报、
开赛前提醒，全部推到自己的 QQ 群；另外附带一条**斗鱼直播间开播/下播提醒**。

跑在 Linux 服务器上：systemd 定时器驱动，Docker 里的 NapCat（OneBot）负责发群，
装好之后无人值守。核心逻辑只依赖 Python 标准库 ——
开播提醒那条线本身就是单文件、零依赖。

> 仓库名是历史遗留：最早只有开播提醒，后来越加越偏向赛事推送，名字没跟着改。

---

## 两条推送线

五条流水线，各跑各的定时器，互不阻塞：

| 线 | 流水线 | 触发 | 网络请求 | 发什么 |
|---|---|---|---|---|
| **赛事推送** | 赛程预告 | 每天 09:30 | Liquipedia 1 次 | 入选场次 + 总预告大图；顺手写待结算清单 |
| **赛事推送** | 单场战报 | 每 10 分钟 | 有到点场次才抓 | 每场一条：胜绿负红 + 逐图比分 + 选手数据 |
| **赛事推送** | 全天整合版 | 每天 09:40 | **零网络** | 上一个赛程日汇总，一场一行 |
| **赛事推送** | 开赛提醒 | **每分钟** | **零网络** | 开赛前约 5 分钟发 Match Preview 大图 |
| **开播提醒** | 开播 / 下播 | 每 45 秒 | 斗鱼接口 | 开播、下播各一条（带直播时长） |

赛事推送的口径、窗口划分、数据源与排查见 **`deploy/ESPORTS.md`** ——
它是这块的主文档，下面只讲结论。开播提醒的原理见下一节。

---

## 它想解决什么（开播提醒那条线）

斗鱼接口里那个「是否在直播」的字段**不可靠**。

主播下播之后，房间常常会自动转入**轮播**（循环放之前的录播、无人直播）。
这时候接口照样返回「正在直播」。你要是只看这个字段，
就会在主播明明没播的时候，一遍遍收到「开播了」——最后只能把提醒关掉。

这个项目用对照实验找到了更可靠的判据（见下面「两个坑」一节），
默认把轮播排除在「开播」之外。

## 特性

### 赛事推送（主线）

- **每天一条赛程预告**：北京 09:30 抓一次 Liquipedia，只推**窗口内还没开打**、且满足
  四条之一（或）的场次：**有中国队** / **有世界前 15 的队伍**（自动抓 HLTV 排名）/
  **大赛且对阵里有知名队伍** / **同一赛事当天凑够 4 支知名队伍**（整体放行）
- **窗口 = 「现在 → 下一次预告」，不是「今天」这个自然日** ——
  否则次日凌晨的比赛会永远漏掉（今天的预告够不着、明天的还没发）
- **每场打完发一条单场战报**：系列比分（胜绿负红）+ 逐图比分 + 每图每队 rating 前 3 名
  的选手数据（K-D / ADR / 评分）；选手数据拿不到就少发一段，**绝不影响发送**
- **每天一条全天整合版**：上一个赛程日一场一行、只有系列比分，**完全不联网**，
  只读本地清单快照
- **开赛前提醒**：开赛时刻**不抓页面**，由同赛事前一场的结果串场级联估算得出，
  所以能每分钟一拍、不碰 Liquipedia 的「1 次 / 30 秒」节流条款
- **出图多级降级，消息一定发得出去**：HTML 1920×1080（Chromium 截图）→
  PNG（Pillow 直接画）→ 纯文本。缺 Chromium / Pillow / 字体，都只是往下退一级，
  不会让消息发不出去。四条流水线各自能退到哪一级见「部署」一节
- **没比赛就静默**，但连着静默满 7 天会报个平安 ——
  免得「今天没比赛」和「程序挂了」在群里长得一样

### 开播提醒（支线，可单独使用）

- **零依赖**：只用 Python 标准库。Python 3.9+ 就能跑
- **能识别轮播**：不把「循环放录播」误报成开播
- **开播 + 下播都提醒，并报出本次直播总时长**：时长由程序自己记账得出，
  不拿斗鱼接口那个不可信的 `show_time` 硬算（见「坑 3」）
- **发失败不丢消息**：正文落盘后自动退避补发，直到送出去或到上限
- **防抖**：连续 N 次读到同一状态才认定状态切换，避免接口抖动误报
- **多通道**：控制台 / OneBot（NapCat 等）/ QQ 官方机器人，可以同时启用
- **两种跑法**：常驻进程，或每分钟执行一次（给 cron / 青龙面板 / 云函数用）
- **配置错了会明确报错**：最坑的 `room_id` 填错会给可照做的提示，而不是安静地什么都不做

### 两条线共用

- **自带看门狗**：`watch.py` 有两个静默失败（掉线不发、停摆不说），
  看门狗每 2 分钟独立体检一次并告警，能自愈的自己动手。有一条**独立于 QQ 的告警通道**，
  所以掉线时也通知得到你 —— 详见 `deploy/WATCHDOG.md`
- **凭据体检**：`check-secrets.py` 扫已跟踪文件里有没有真实凭据（token / 群号 / webhook），
  CI 每次都跑 —— 凭据一旦推进公开仓库就永久留在 git 历史里，事后删文件也删不掉
- **CI 自检**：`.github/workflows/selftest.yml` 在 Python 3.9 / 3.12 / 3.13 上跑
  `selftest.py` + `watchdog.py --selftest` + `esports.py --selftest`，三条都不联网

---

## 快速开始

两条路，按你要哪条线走：

- **只要开播提醒** → 本机就能跑，照下面 1 → 2 → 3 走，几分钟搞定，不需要服务器。
- **要赛事推送（主线，推荐）** → 它需要常驻服务器 + Docker + systemd，
  直接跳到「部署」一节；或者把 `deploy/AGENT_PROMPT.md` 那份提示词丢给 AI agent 帮你装。

下面先讲开播提醒的最小闭环。

### 1. 拿到真实 room_id（最容易踩的坑，务必先看）

斗鱼房间有**两个号**，而且经常不一样：

| | 长什么样 | 用在哪 |
|---|---|---|
| **靓号** | 地址栏里的短号，例如 `douyu.com/12345` | 给人访问用的 |
| **真实 room_id** | 接口用的整数 ID | **本项目的配置要填这个** |

举个真实例子：某个直播间地址栏是 `douyu.com/6657`，能正常打开；
但接口能查到的 room_id 是 `6979222`。直接去查 `betard/6657`
返回的是「房间已被关闭」的 HTML 报错页。

**更坑的是**：斗鱼上可能存在另一个真号恰好是 `6657` 的**别人的房间** ——
填错不会报错，只会安静地监控一个不相干的直播间。

**怎么找真实 room_id**（二选一）：

1. 打开直播间页面 → 查看网页源代码 → 搜索 `room_id`，取那个数字
2. 用脚本试：`python watch.py --probe <你猜的号>`
   能打印出主播名、房间标题就说明对了；返回报错页说明这是靓号或房间不存在

### 2. 配置

```bash
cp config.example.json config.json
```

最少只需要填两项：

```jsonc
{
  "room_id": "6979222",            // 上面找到的真实 room_id
  "channels": ["console"],         // 先只用控制台，验证判定对不对

  "onebot": {
    "base": "http://127.0.0.1:3000",
    "token": "你自己设的token",
    "target_type": "group",        // group 群聊 / private 私聊
    "target_id": "你的群号或QQ号"
  }
}
```

> 想推到 QQ，把 `channels` 改成 `["onebot"]`；想同时打日志和推 QQ，
> 写成 `["console", "onebot"]`。**建议先只用 `console` 观察一两天**，
> 确认判定准确了再接 QQ。

### 3. 试跑

```bash
python watch.py --once            # 读一次当前状态，不发通知
python watch.py                   # 正式值守
```

接 QQ 之前，先确认整条链路是通的：

```bash
python watch.py --test-notify     # 真的往配置的通道发一条测试消息
```

---

## 命令一览

| 命令 | 作用 |
|---|---|
| `python watch.py` | 正式值守，按配置的间隔轮询 |
| `python watch.py --once` | 读一次当前状态，打印归一化结果（不入库、不通知） |
| `python watch.py --tick` | 只检查一轮就退出（给 cron / 青龙 / 云函数用） |
| `python watch.py --probe <号>` | 体检指定房间，打印两个接口的原始返回 |
| `python watch.py --test-notify` | 往配置的通道发一条测试消息 |
| `python selftest.py` | 不联网的逻辑自检，改完代码先跑它 |
| `python deploy/watchdog.py --status` | **一条命令看健康**：watch.py 在跑吗、NapCat 在线吗（只读） |
| `python deploy/watchdog.py --selftest` | 看门狗离线自检，不联网、不碰 docker |
| `python deploy/watchdog.py --test-alert` | 验证告警通道真的通（部署后必做） |
| `python deploy/watchdog.py --recover-notify` | 手动补一条丢失的开播通知（**兜底**：发送失败通常会自动补发，只有重试到顶才需要它） |
| `python deploy/esports.py --check` | **看今天会推什么赛程**：只抓取 + 打印，不发消息、不写状态（上线前先跑这个） |
| `python deploy/esports.py --teams` | **列出页面上的真实队名**并标出哪些已收录（含 `[知名]` / `[世界前15]` 标记），改白名单前用它抄名字（不发消息、不写状态） |
| `python deploy/esports.py --rank` | **核世界前 15 的队名映射**：打印 HLTV 写法 → Liquipedia 队名，标出没映射上的（会请求一次 HLTV，不发消息、不写状态） |
| `python deploy/esports.py --selftest` | 赛程预告离线自检（正常 297 项；少 `make_card_font.py` 是 295、没装 Pillow 是 266、都没有是 264，**只看有没有 0 失败**），不联网、不发消息 |
| `python deploy/esports.py --check-results` | **看单场战报会发什么**：结算「已经打完、还没发过」的场次并逐场出图（`/tmp/esports_result_check_01.png` …，**一场一张**），不发消息、不写状态。**清单空着也会演练**（拿页面上最近打完的 8 场演示），所以刚装好就能验收。会真的去抓赛事页拿逐图比分，所以整轮可能要等 30 秒 × 赛事数 |
| `python deploy/esports.py --results` | 结算到期场次并发**单场战报**（定时器调的就是它；没到窗口会**一个请求都不发**就退出）。**每两队打完就发这一条**（一场一条，不再合并） |
| `python deploy/esports.py --check-daily` | **看全天整合版长什么样**：出图到 `/tmp/esports_daily_check.png`，不发消息、不写状态。**完全不联网**，只读清单快照 |
| `python deploy/esports.py --daily` | 发上一个赛程日的**全天整合版**（一场一行、只有系列比分），由 `douyu-esports-daily.timer` 每天 09:40 拉起。有幂等标记，同一天重复跑不会重发 |
| `python deploy/esports.py --check-announce` | **看开赛提醒会发什么**：只把 Match Preview 卡渲染到 `$TEMP`，不发消息、不写状态。**零网络请求**，随时可跑 |
| `python deploy/esports.py --announce` | 开赛提醒一轮：估算的开赛时刻落进提醒窗的场次发一张 Match Preview 卡（`douyu-esports-announce.timer` 每分钟拉起的就是它）。**零网络请求**，只读本地清单 + 锁文件防重 |
| `python deploy/esports.py --test-notify` | 验证赛程预告用的推送通道 |
| `python deploy/esports.py --check`（同上） | 顺带把要发的那张卡片渲染到 `/tmp/esports_card_check.png`，可以下载下来看排版 |
| `python pack_deploy.py` | 打部署包 `deploy.zip`（自动带上 `watch.py` / `selftest.py` / `watchdog.py` / `esports.py` / **`card_font.otf`** / `install-watch.sh`，并归一为 LF） |
| `python qr_make.py --url "<日志里的二维码链接>"` | 把 NapCat 登录二维码在本地变成可扫的图片（见下方「扫码登录」） |

---

## 两个坑

### 坑 1：靓号 ≠ room_id

见上面「快速开始」第 1 步。这是新手上手时最容易卡住的地方，
而且症状很隐蔽 —— 脚本不报错，只是监控了一个不相干的房间。

### 坑 2：`show_status = 1` 不等于真人在播

主播下播后房间转入**轮播**时：

```
betard  →  show_status: 1,  videoLoop: 1
```

只看 `show_status` 就会误报。本项目的判据是 `videoLoop`：

**这个结论是实测出来的，不是猜的：**

| 对照 | 结果 |
|---|---|
| 斗鱼「正在直播」列表里抽样的在播房间 | `videoLoop` **全部为 0** |
| 扫描 600+ 个在播房间，找某个正在轮播的房间 | **完全不在直播列表里** |
| 该房间页面内嵌数据 | 有 283 万人气，却是 `videoLoop: 1` |

于是配置里 `treat_loop_as_live` 默认 `false`：**轮播不算开播**。

- 想改回「轮播也提醒」：设成 `true`，消息里会附一行「当前为轮播」提示
- **拿不准的话**：每轮心跳日志都记了 `loop=` 值，对照几次真实开播/下播就能自己确认

### 坑 3：直播时长不能拿接口的 `show_time` 算

`notify_on_end` 默认开着：开播、下播各推一条，下播那条会带**本次直播的总时长**。

时长**不是**从接口的 `show_time` 算出来的 —— 它看着像「开播时间」，实则不可信：

```
轮播中实测：show_time → 2026-09-24 10:08:55   ← 这是**轮播场次**的起点
```

拿它当真人开播时间去减，会得到一个离谱的时长。所以本项目让状态机自己记账：

| 时刻 | 动作 |
|---|---|
| 确认开播 | 把时刻写进状态文件的 `live_started_at` |
| 确认下播 | `now - live_started_at` 就是本次时长 |

这样算出来的数只会**偏小**（程序没跑到的时候不算），绝不会凭空变大 —— 报少了是保守，报多了是撒谎。

只有三种「没自记到时刻」的兜底情形（升级到本功能前就在播 / 程序中断期间开的播 /
有人手改坏了字段）才会去用接口值，且要先过一遍常识检查（能解析、不在未来、
回溯不超过 `notify_end_max_hours` 小时）。检查不过就退回「本轮首次看到在播」，
并把这条时长写成 **「至少 X 小时 Y 分」** —— 只报下限，不假装精确。

下播消息长这样：

```
【斗鱼下播】某主播
标题：随便播播
热度：283.1 万
开播时间：2026-09-24 19:30:00
直播时长：1 小时 23 分
https://www.douyu.com/6979222
```

不想一场收两条消息，就把 `notify_on_end` 改成 `false`。

---

## 文件目录

```
douyu-live-notify/
├── README.md                     本文件
├── LICENSE                       MIT
├── watch.py                      开播提醒主程序（单文件、零依赖）
├── selftest.py                   watch.py 的不联网逻辑自检
├── check-secrets.py              凭据体检：已跟踪文件里不该有任何真实 token / 群号 / webhook
├── pack_deploy.py                打 deploy.zip（自动带上根目录的几个 .py，归一为 LF）
├── qr_make.py                    把 NapCat 登录二维码变成本地可扫的图片
├── config.example.json           开播提醒的配置模板
├── .github/workflows/selftest.yml   CI：3 个 Python 版本 × 三条离线自检 + 凭据体检
│
└── deploy/                       部署包 —— 服务器上要用的全部东西
    ├── esports.py                赛事推送主程序（预告 / 战报 / 整合版 / 开赛提醒四合一）
    ├── watchdog.py               看门狗：体检 + 自愈 + 独立于 QQ 的告警通道
    ├── result_template.html      卡片模板：单场战报
    ├── daily_template.html       卡片模板：总预告大图（文件名是历史包袱，实际喂的是总预告）
    ├── preview_template.html     卡片模板：开赛提醒（Match Preview）
    ├── fonts/                    上面三个模板用的字体（Bebas Neue / IBM Plex Mono ×2）+ OFL 许可证
    ├── card_font.otf             Pillow 降级卡片的字体子集（Noto Sans SC，65 KB）
    ├── CARD_FONT_LICENSE.txt     ↑ 的 SIL OFL 1.1 全文（OFL 要求随字体分发）
    ├── make_card_font.py         重新生成 card_font.otf
    ├── check-esports-net.py      赛事数据源连通性自检（只读）
    ├── docker-compose.yml        NapCat 容器（端口只绑 127.0.0.1，ACCOUNT 必填）
    ├── install-watch.sh          安装主程序 / 看门狗 / 卡片资产 与 systemd 单元
    ├── preflight-check.sh        部署前环境预检（只读，含内存判定）
    ├── setup-docker-mirror.sh    探测可用的 Docker 镜像源
    ├── mem-report.sh             内存被谁占了（只读，含 OOM 历史）
    ├── add-swap.sh               加 / 删 swap（幂等、可撤销，小内存机器用）
    ├── why-no-notify.sh          排查「为什么没收到通知」
    ├── config.example.json       主配置模板（开播 + 赛事两段都在这里）
    ├── .env.example              WebUI token / 机器人 QQ 号 / 容器内存上限
    ├── watchdog.env.example      告警通道与阈值（装到 /etc/default/douyu-watchdog）
    ├── douyu-watch.{service,timer}             每 45 秒跑一次 watch.py --tick
    ├── douyu-watchdog.{service,timer}          每 2 分钟体检，异常时告警 / 自愈
    ├── douyu-esports.{service,timer}           每天 09:30 赛程预告
    ├── douyu-esports-results.{service,timer}   每 10 分钟结算刚打完的场次 → 单场战报
    ├── douyu-esports-daily.{service,timer}     每天 09:40 全天整合版
    ├── douyu-esports-announce.{service,timer}  每分钟看一次要不要发开赛提醒
    ├── douyu-watch.tmpfiles                    日志与状态目录兜底（装到 /etc/tmpfiles.d/）
    ├── DEPLOY.md                 完整部署手册（先看这个）
    ├── ESPORTS.md                赛事推送主文档：口径、窗口、数据源、排查
    ├── WATCHDOG.md               看门狗设计说明 + 「怎么验证它真的会叫」
    ├── SCAN_QR_WITHOUT_SSH.md    扫码登录的替代做法（不必开 SSH 隧道）
    ├── AGENT_PROMPT.md           想让 AI agent 帮你部署？把这份提示词丢给它
    ├── RESUME_PROMPT.md          预检判定内存不足、处理完之后接着部署的续跑提示词
    └── UPDATE_PROMPT.md          旧部署要更新到新版时，丢给 agent 的提示词
```

### 只在本机存在、不进仓库的文件

`.gitignore` 挡在门外的这些**不是可有可无**，其中两个丢了不可恢复：

| 文件 | 是什么 | 丢了会怎样 |
|---|---|---|
| `deploy/config.json`、`deploy/.env`、`deploy/watchdog.env` | **真实配置与凭据** | 得照着 `.example` 重填；凭据本来就绝不该进公开仓库 |
| `HANDOFF.md` | 项目内部交接文档 | **只在本地有，GitHub 上没有** —— 唯一不可重建的一个 |
| `deploy/NotoSansSC-Regular.otf` | 生成 `card_font.otf` 的**源字体**（8 MB） | 无所谓，重跑 `make_card_font.py` 会自动下载 |
| `deploy/logo_cache/` | 队标缓存（文件名 = sha256(url) 前 16 位） | 无所谓，从赛程页自动重建 |
| `deploy.zip` | `pack_deploy.py` 的产物 | 无所谓，重打一次即可 |
| `qr.png` | NapCat 登录二维码 | 无所谓，本质是一次性凭据，生成即作废 |
| `selftest_result.txt`、`state_*.json`、`rank_cache.json` | 运行期产物 | 无所谓 |

服务器上另有一批运行时状态（`state_*.json`、`logo_cache/`、`rank_cache.json`），
都在 `/opt/douyu-live-notify/` 下，同样不进仓库。

---

## 部署

### 方式 A：本机 / 任何有 Python 的机器

用 cron 每分钟跑一次（`--tick` 每次自己读写状态文件，天然适配无状态调度）：

```cron
* * * * * cd /path/to/douyu-live-notify && /usr/bin/python3 watch.py --tick
```

### 方式 B：服务器 + Docker（推荐）

`deploy/` 目录里有整套东西，Ubuntu 22.04 / 24.04 实测通过。
里面**每个文件是干什么的**见上面「文件目录」一节，这里不重复。

> `.env` 里的 **`ACCOUNT`（机器人 QQ 号）是必填的**。镜像靠它给 QQ 传 `-q` 走快速登录；
> 不填的话容器每次重启都可能退回「等你扫码」，做不到长期无人值守。漏填时 compose
> 会直接拒绝启动 —— 这是故意的，好过悄悄退回扫码（那种失败你几天后才会发现）。

**看门狗解决的是「静默失败」**：NapCat 掉线后 `watch.py` 照跑照判定、只是消息发不出去
（日志里一行 `[error]`，程序看起来完全正常）；定时器被禁或进程被 OOM 杀掉则表现为
「什么都没发生」而不是「报错」。看门狗每 2 分钟独立体检，把这两类失败变成一条看得见的
告警，能自愈的自己动手。**它有一条独立于 NapCat 的告警通道，所以掉线时也通知得到你** ——
这也是它唯一需要你花几分钟配置的地方（`ALERT_WEBHOOK`）。详见 `WATCHDOG.md`。

想推微信：优先用 **pushplus**（免费 200 次/天、微信里能看到完整正文），
配法 `ALERT_WEBHOOK=pushplus|https://www.pushplus.plus/send?token=<token>`；
Server酱 免费只有 5 条/天且免费版只显示标题，适合当兜底 —— 两个都用 `;` 连起来写即可。

**CS2 赛事推送（主线功能）**：`esports.py` 一个程序管**四条流水线** ——
每天 09:30 的赛程预告、每 10 分钟的单场战报、每天 09:40 的全天整合版，
以及每分钟一拍的赛前提醒。下面以**赛程预告**为例讲口径，另外三条的时间安排、
数据源与验收方式见 `ESPORTS.md`。

赛程预告每天北京 09:30 抓一次 Liquipedia，
只推**窗口内还没开打**、且满足**四条之一（或）**的场次：

1. **有中国队参赛** —— 无条件发；
2. **有世界前 15 的队伍参赛** —— 自动抓一次 HLTV 排名，无条件发（抓不到就用缓存，只跳过这条）；
3. **大赛 且 对阵里有知名队伍** —— 赛事名命中关键词 **并且** 队名命中知名名单，**两条都满足**才发；
4. **名队云集的赛事** —— 同一赛事在窗口内凑够 4 支「知名 / 世界前 15」的不同队伍，该赛事整体放行。

**窗口 = 「现在 → 下一次预告时刻」，不是「今天」这个自然日。** 每天只发一次，
只认自然日的话**次日 00:00~09:30 的比赛永远没人预告**（今天的预告够不着、明天的还没发），
凌晨开打的比赛正好落进这个真空期。窗口右端点是**开区间**，两期首尾严格相接，既不漏也不重。
右端点由 `esports.preview_run_time`（默认 `09:30`）决定 —— **改了定时器的时刻要同步改它**。

中国队和知名队伍靠**队名白名单**认（精确匹配，大小写不敏感）；大赛靠赛事名关键词认。
名单都在 `config.json` 的 `esports` 段里可改，**整段不写也行**（内置默认值就能跑）。
不知道队名该怎么写就 `python3 esports.py --teams`；怕「世界前 15」映射不全就 `python3 esports.py --rank`。

推送形态是**一行文字 + 一张图片卡片**（手机 QQ 对小屏长文本的换行/缩进处理很差，
8 场挤成一段文字根本扫不动）。那一行文字保证通知栏可见、群聊可搜索：

```
【CS2 赛程】10-05 周一 09:30 → 10-06 周二 09:30 · 共 8 场
```

卡片长这样（总预告版；正文只有「时间 + 对阵」，跨天场次时间前加「次日」）：

```
 CS2 赛程
 10-05 今天 09:30 → 10-06 明天 09:30　共 8 场
 17:00        PARIVISION ▣  vs  ▣ FURIA
 22:00              M80 ▣  vs  ▣ TYLOO
 次日 00:30  Team Vitality ▣  vs  ▣ Team Falcons
 ESL Pro League Season 24 - Round 3 · Bo3  数据来源：Liquipedia
```

出图是**多级降级**的，四条流水线各自能退到哪一级不一样 ——
但**退到最后一定是纯文本，消息永远发得出去**：

| 流水线 | 一级 | 二级 | 三级 |
|---|---|---|---|
| 总预告 | HTML 1920×1080（`daily_template.html`） | 880 px PNG，Pillow | 纯文本 |
| 单场战报 | HTML 1920×1080（`result_template.html`） | PNG，Pillow | 纯文本 |
| 开赛提醒 | HTML 1920×1080（`preview_template.html`） | — | 纯文本 |
| 全天整合版 | —（**没有 HTML 版**） | PNG，Pillow | 纯文本 |

HTML 那级要 Chromium；Pillow 那级要 Pillow + `card_font.otf`；
`card_font.otf` 是**随包发布**的 Noto Sans SC 子集（65 KB），
所以服务器和本地画出来一模一样。HTML 层用 `deploy/fonts/` 里的字体
（字体路径曾有 `file:///` 双前缀导致**静默失效**，已修 + 自检钉死）。
降到哪一级日志里都会留 `[warn]`。

版面上**不带任何「入选标记」**（没有 `← 中国队` / `← 世界前15`，卡片上也没有中国队红标）——
入选口径照旧，想看某场靠什么进来的看 `--check` 的逐场理由。

没有符合条件的比赛就**静默**；连着静默满 7 天会发一条报平安，这样
「今天没比赛」和「程序挂了」在群里长得不一样。
抓取失败会单独告警，并明确写「这不等于今天没有比赛」。
上线前先跑 `python3 esports.py --check` 看清楚会发什么（**只抓不发、不写状态**，
顺带把卡片渲染到 `/tmp/esports_card_check.png` 给你看排版）。
另外三条流水线也各有演练入口：`--check-results`（战报）/ `--check-daily`（整合版）/
`--check-announce`（开赛提醒），同样不发消息、不写状态。

口径、已知限制与排查见 `ESPORTS.md`。

用法：把 `deploy/` 整个目录传到服务器，先跑 `bash preflight-check.sh`
（只读，不改任何东西）看环境，然后照 `DEPLOY.md` 一步步走。
**如果你想让 AI agent 来部署，直接把 `AGENT_PROMPT.md` 里那份提示词丢给它。**
小内存服务器（NapCat 是 Electron 应用，常驻 0.3~0.7GB）要先看预检的内存判定；
判定不足、处理完之后，用 `RESUME_PROMPT.md` 续跑。

### 打部署包（别手打 zip）

```bash
python pack_deploy.py        # 生成 deploy.zip
```

包里必须同时有 `watch.py`、`selftest.py`、`watchdog.py`、`esports.py` 和 `install-watch.sh`，
而且这些文件在同一层 —— `install-watch.sh` 是从自己所在目录往上找源文件的，
少一个就会报「找不到」。手动 `zip -r deploy.zip deploy/` 很容易漏掉仓库根目录的两个 .py，
所以这件事交给脚本做，缺文件时它会直接报错、不生成包。它还会把文本文件统一转成 LF
（Windows 上工作区常是 CRLF，带进包里的 shell 脚本到 Linux 上会报 `$'\r': command not found`）。

装完之后 `install-watch.sh` 会打印 `watch.py` / `selftest.py` / `watchdog.py` / `esports.py`
的 sha256 前 16 位（外加一行 `card_font.otf`），以后怀疑「服务器上是不是旧版」，
和仓库里的对一下即可。

**依赖**：核心逻辑只用 Python 标准库。卡片相关的都是**可选**依赖 ——
缺了不会静默失败，只会按上面「三级降级」往下退一级：

| 依赖 | 谁要 | 缺了会怎样 |
|---|---|---|
| Chromium / Google Chrome | 一级卡片（HTML 1920×1080 截图） | 退到二级（Pillow 出图） |
| `Pillow`（`apt install -y python3-pil`） | 二级卡片（880 px PNG）；`make_card_font.py` 也要它 | 退到三级（纯文本），**赛程照发** |
| `qrcode` + `pillow`（`pip install qrcode pillow`） | `qr_make.py`（本地把 NapCat 登录二维码变成图片） | 只有那个一次性脚本用不了，不影响服务器 |

> 装 Chromium 时**别用 snap 版**：国内 ECS 上 snap 的 `connect plugs` 会卡死一小时以上，
> `--version` 都能 0% CPU 阻塞，`snapd restart` 也救不回来。直接装 Google Chrome 的 deb 包，
> 再把路径钉进 `config.json` 的 `esports.chrome_bin`（`install-watch.sh` 会自动探测并写进去）。

### 扫码登录 QQ（不需要 SSH 隧道）

NapCat 会把登录二维码打进**容器日志**，所以不必开 SSH 隧道、也不必让 WebUI 接触网络：

```bash
docker logs napcat 2>&1 | grep -nE '二维码|txz\.qq\.com' | tail -20
```

找到那行 `二维码解码URL: https://txz.qq.com/p?k=...&f=...`，在**你自己的电脑**上：

```bash
python qr_make.py --url "https://txz.qq.com/p?k=xxxx&f=xxxx"    # 生成 qr.png
```

用手机 QQ 扫它。**二维码只有约 1~2 分钟有效期**，拿到就马上扫。

> 那个 URL 本质是**一次性登录凭据**。用 `qr_make.py` 在本地转换，就不会把它交给第三方二维码网站。
> 日志里没有 URL 时改为取图片：`docker exec napcat sh -c 'base64 -w0 /app/napcat/cache/qrcode.png'`，
> 再用 `python qr_make.py --b64str "<粘贴>"` 还原。完整说明见 `deploy/SCAN_QR_WITHOUT_SSH.md`。

`qr_make.py` 需要额外依赖（`pip install qrcode pillow`），
而且只有「从 URL 生成」这条路径需要 —— `--b64str` 路径纯标准库。
不想装也行：把那个 URL 贴到任意在线二维码工具即可（用完即弃，别留在页面上）。

---

## 排错

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `[error] ... 里没有 room_id` | 没配或配空了 | 填真实 room_id，见「快速开始」第 1 步 |
| `[error] 所有探测接口都失败了` | room_id 是靓号 / 房间不存在 / 网络不通 | `python watch.py --probe <号>` 逐个验证 |
| 一直显示「未开播」但主播在播 | 房间处于轮播，或真开播时 `videoLoop` 行为不同 | 看心跳日志里的 `loop=` 值 |
| `403 Forbidden`（OneBot） | token 两端不一致 | 核对配置与 NapCat 侧的 token |
| `Connection refused`（OneBot） | 容器内 OneBot 的 host 填了 `127.0.0.1` | 改成 `0.0.0.0`，理由见 `DEPLOY.md` |
| `502 Bad Gateway` 打 `127.0.0.1` | 环境里有全局代理，把回环地址也劫持了 | 脚本已内置绕过，若仍出现检查 `HTTP_PROXY` |
| 群里收不到消息 | 机器人不在群里 / 被禁言 / 定时器没开 | `get_group_list` 核对群号 |
| 机器人掉线了但我不知道 | `watch.py` 不做健康检查 | 装看门狗，然后 `python deploy/watchdog.py --status` 一眼看健康；掉线会自动告警 |
| 丢了某条开播通知 | 发送失败时正文会记进状态文件，**后续轮次自动补发**（1、2、4、8… 分钟退避，默认最多 30 次 / 6 小时） | 一般不用管；看到 `放弃自动重试` 才手动 `python deploy/watchdog.py --recover-notify`（主播仍在播时有效） |
| 赛程预告没发，也不确定是没比赛还是坏了 | 两者故意长得不一样，但只看群看不出来 | `tail -n 50 /var/log/douyu-watch/esports.log`：`[silent]` = 窗口内确实没比赛；`[error]` = 抓取/发送失败。想看清覆盖范围就跑 `--check`，第一行 `[info] 预告窗口：…` |
| 凌晨的比赛从来没收过预告 | `esports.preview_run_time` 和定时器时刻不一致（窗口右端点偏早），或者还是老版本「只认今天」 | 把两处改成同一个时刻；`--check` 的 `[info] 预告窗口：…` 直接能看出覆盖到哪 |
| 赛程预告里出现了没中国队参加、也不算大赛的比赛 | 该赛事的对阵里凑够了 4 支知名/前 15 队伍，触发了第 4 条「名队云集的赛事」（整体放行是刻意的） | 想收紧就把 `esports.tournament_min_known_teams` 调大，或设 `0` 关掉这条 |
| 赛程预告该发的比赛没发，日志却是 `[silent]` | 队名**措辞**和白名单对不上 —— 精确匹配差一个词就静默漏发，且不报错 | `python deploy/esports.py --teams` 把页面上的真实队名原样抄进 `cn_teams` / `notable_teams` |
| 有世界前 15 的队伍参赛，预告里却没有 | HLTV 写法（`Spirit` / `G2`）没映射到 Liquipedia 队名（`Team Spirit` / `G2 Esports`） | `python deploy/esports.py --rank` 看谁标了「未映射」，写进 `esports.hltv_aliases` |
| 日志出现 `[warn] 世界排名抓取失败` | HLTV 改版 / 限流 / 断网 | 不是致命错误：用缓存兜底，另外三条照常；下一轮自动重试 |
| 赛程预告抓不到数据（`HTTP 406` / `403` / `429`） | User-Agent 或 gzip 不合格 / 被封 / 限流 | 检查 `esports.ua_contact` 别留空；限流就调大 `parse_min_interval_seconds` |
| 赛程预告报「一个比赛都没解析出来」 | Liquipedia 页面结构变了 | 见 `ESPORTS.md` 第 7 节；**`esports.py` 和 `check-esports-net.py` 里的解析器要一起改** |

### 关于 `at_all`（@全体成员）

默认 `false`，建议保持。QQ 的 @全体成员**一个号一天只有 10 次配额，且所有群共享**，
用在开播提醒上纯属浪费，还容易被群友嫌弃。想 @ 特定的人就填 `at_users`：

```json
"at_users": ["10001", "10002"]
```

---

## 已知边界

- **斗鱼接口是非官方的**。用的是斗鱼网页端自用的端点，**改版就可能失效**。
  脚本做了「主接口 + 备用接口」双层 fallback，但不保证永久可用。
- **延迟 = 轮询间隔 × confirm_rounds**。45 秒一次 + 确认 2 次 ≈ 最慢 1 分半发现开播。
- **消息送达不保证**。QQ 有风控，可能静默丢消息（日志显示成功但收不到），
  别把这个提醒当唯一信息来源。
- **首次运行不发通知**，只记录当前状态（防止一启动就误报）。
  反过来说，如果启动时主播正在播，这次开播不会提醒。
- **直播时长是「程序看到的那一段」**。程序没跑的时候不计入，
  所以换机/重启后的第一场大概率偏短，且会标成「至少 X」。
  另外时长是按 `confirm_rounds` 确认后才起算的，默认配置下会少算 1 轮左右。

## 免责声明

- 斗鱼接口数据抓取自公开网页端点，本项目仅供个人学习与自用。
- 若使用 NapCat 等**第三方 QQ 协议端**：这类项目违反 QQ 用户协议，
  请用**小号**，并**务必不要把管理端口暴露到公网**、不要使用默认 token。
  2025 年 9 月曾有人批量扫描此类暴露实例并操纵其发违法信息，
  导致被波及的账号和群聊被**永久封禁**。
- 使用本项目的风险由使用者自行承担。

## License

[MIT](LICENSE)
