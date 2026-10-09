# 更新提示词 · 装「下播提醒 + 直播总时长」（含一版看门狗修复）

> **用法**：整份复制，发给服务器上的 agent。
> **适用场景**：这台机器上**已经在跑**（watch.py + 看门狗都装好了、定时器已启用），
> 这次只是**更新代码**，不重装环境。
> 全新部署请看 `AGENT_PROMPT.md`；卡在内存那一步的续跑请看 `RESUME_PROMPT.md`。
>
> ⚠️ **本文件的正文写于「装下播提醒」那一版，之后包又更新过数次**（新增 `esports.py`
> 的 CS2 赛程预告、知名队伍白名单、**图片卡片**、**战果公布**，以及本次的
> **单场战报 + 全天整合版**）。场景没变，
> 但**第 0 步的指纹表已经按当前包更新过 —— 以那张表为准**。`esports.py` 是新增文件，
> 服务器上原本没有，`install-watch.sh` 会直接装上；它的定时器 `douyu-esports.timer`
> **装完是 disabled 的**，是否启用另说，不在本流程范围内。
>
> 本次还会多装六个单元：`douyu-esports-results.{service,timer}`（单场战报）、
> `douyu-esports-daily.{service,timer}`（全天整合版）和
> `douyu-esports-announce.{service,timer}`（开赛提醒），同样是**只装不启用**。
>
> ⚠️ **本次改动了消息版式**（胜方绿名 / 负方红名、单场战报带逐图比分、新增每日整合版），
> 并且**改了配置键默认值**（`results_max_age_hours` 24→36）。服务器上的 `config.json`
> 如果显式写过这一项，要一起改，否则整合版会缺场次。
>
> ⚠️ **最新一次的包还要多做一件事**：全天整合版**第一次**拿到了 1920×1080 的
> HTML 大图（模板 `daily_results_template.html` 是新增文件）。这次更新**必须把
> `esports.py` 和 `daily_results_template.html` 一起装上去** —— 只更新脚本不装模板，
> 整合版会安静退回 880×650 的 Pillow 旧卡（功能正常、版式没变），
> 正是用户这次报的那个问题。装完可以这样验：
> `python3 esports.py --check-daily` 看日志里有没有
> `[info] 整合版战果大图（HTML 1920×1080）`；如果是
> `[info] HTML 整合版战果出不了（缺模板 …）` 就说明模板没装上。

---

## 这次的包比下面这张表更新（先看这里）

| 提交 | 内容 | 你能看到的变化 |
|---|---|---|
| `78f0981` / `3bc7a79` | 新增 `esports.py`：CS2 每日赛程预告 + 知名队伍白名单 | 每天北京 09:30 推一条「今日赛程」（**需要另外启用 `douyu-esports.timer` 才会跑**） |
| `cbffcb0` | 赛程预告改版：**一行文字 + 一张图片卡片**（带队标） | 群里收到的赛程变成长图；**新增 `card_font.otf` 一个文件**，画卡片还要 Pillow（`python3-pil`，装不上会自动退回纯文本） |
| `e87d6f9` / `721dad6` | 占位块改用页面短名；文档同步 | 队标拿不到时，灰块里显示的是页面自带的短名（如 `Spirit`），不再是三个 `TE` |
| `08cbb23`（上一次） | 新增**战果公布**：预告过的比赛打完后补一条比分 | 预告后还会收到战果消息；**新增两个单元** `douyu-esports-results.{service,timer}`（只装不启用）。零新增数据源 —— 还是那一个页面、同一份解析器 |
| **（本次）** | **战果改成两条通道 + 队名按胜负上色** | ① **每场一条**「单场战报」（胜方绿名 / 负方红名，卡片里逐图一行，比分按该图胜负上色）；② 新增 `douyu-esports-daily.{service,timer}`，**次日 09:40** 发上一个赛程日的**全天整合版**（一场一行、不带逐图，**不联网**）。逐图比分来自 Liquipedia **赛事页**（页面路径是链接里自带的，不用维护别名表），抓不到就少画几行、不影响发送。**⚠️ 卡片字体多带了「地」「报」两个字，`card_font.otf` 必须一起更新**，否则卡片会静默退回纯文本 |
| `e3788b3` | **单场战报带逐图选手数据**（csdb.gg）+ 卡片重排版 | 战报卡片下部多两列 5v5 选手数据（K-D/ADR/KAST/Rating，取自 csdb.gg 单场页）；拿不到选手数据时自动少画，不影响发送。新增配置键 `card_players_enabled`（默认开）/ `card_players_per_team`（默认 3）。**⚠️ 该数据源 2026-10-09 失效、抓取层已拆出（见下一行）** |
| `4f24807` | 队标优先取 **darkmode** 变体 | 亮/暗双图队伍（Vitality/G2/NAVI/Spirit…）在深色卡片上恢复彩色版（Vitality 黑蜜蜂→黄蜜蜂）；单图队伍不受影响 |
| `b2527f8` / `b9f237a` | 仓库改名为 `qq-esports-notify`；README 定位改为「QQ 群赛事推送」主线 | **只是仓库名与文案**，服务器安装路径 `/opt/douyu-live-notify` **不动**（unit 文件里全是这个路径）；模板与 UA 里的署名换成新名 |
| **（本次）** `4bdb153` | **全天整合版补上 V2 大图** | 之前**只有这一条流水线没有 HTML 层**，群里收到的「昨天的总战果」一直是 880×650 的 Pillow 旧卡（用户 2026-10-06 反馈「还不是 v2 版」）。现在补齐：**新增 1 个文件 `daily_results_template.html`**，整合版出 1920×1080 深色大图（胜方绿底 chip / 跨午夜标「次日」），并和其余三条一样走三级降级 HTML → 880px Pillow → 纯文本。**这个模板不装包 = 整合版继续发旧卡**，所以 `install-watch.sh` 和打包脚本的必需清单都加了它 |
| **（本次）** | **卡片全部换成 V2 大图（1920×1080 HTML 渲染）** | 战报卡和总预告卡变成用户拍板的深色大图版式。**新增 5 个文件**：`result_template.html`、`daily_template.html`、`fonts/`（3 个 ttf）。渲染优先走 Chromium 截图，没装 Chromium / 渲染失败**自动退回 880px 旧卡**，再不行退纯文本 —— 消息永远照发。**服务器要装浏览器 + 中文字体**（见第 2.5 步），不装就一直是旧 880px 卡 |
| **（本次）** | **新增开赛提醒（STARTING SOON）** | 比赛快开打时推一张 Match Preview 大图（1920×1080）。**新增 3 个文件**：`preview_template.html`、`douyu-esports-announce.{service,timer}`（只装不启用）。**零网络请求** —— 开赛时刻是同赛事前一场结果串场级联估算的（Bo1 80 / Bo3 140 / Bo5 240 分钟 + 中场 30 分钟），timer 每分钟看一眼清单，估算时刻落入未来 5 分钟窗口且没提醒过才发；提醒锁在 `state_esports_announce.json`，同一场只发一次，估算漂移超 10 分钟补发一次「时间有调整」。前提：`douyu-esports.timer` 在跑（预告写清单，提醒才有料） |
| **（本次）** `d069699` 之后的清理 | **拆出选手数据抓取层**（csdb.gg 已失效） | csdb.gg 改成 Next.js 客户端渲染后，原 `fetch_csdb` / `parse_csdb_matches` / `locate_csdb_match` / `parse_csdb_players` / `attach_players` 五个函数 + `CSDB_*` 常量 + `--results` 里的抓取调用点**全部删除**。**展示层原地保留**：`result_template.html` 选段、`build_result_match()` 的 `players`/`mvp` 字段、`_aggregate_players()`、`_draw_player_block()`、`_team_same()` —— 将来接上新数据源只需把逐图选手数据填进 `row["players"]`，模板一行不改。模板页脚把「Liquipedia · csdb.gg」改回「Liquipedia」。配置键 `card_players_enabled` 暂**无消费者**（保留备用）、`card_players_per_team` 展示层仍读。**行为变化：单场战报不再有选段（此前也基本抓不到，等于把空转拆掉），其余一切照旧、零新增网络请求** |

| **（本次）** `bebc639` | **选手数据源换成 Cito API（默认关闭）** | 单场战报卡片底部那段 5v5（K-D / ADR / KAST / Rating + 全场 MVP）**重新有数据了**。csdb.gg 已失效，这次改用 **Cito API**（`citoapi.com`，免费档 500 次/月，实测配对率 15/15）。**默认关闭**：`esports.cito_enabled=false` 时**一次请求都不发**，卡片自动走「居中无统计」形态 —— 行为与上一版完全一致。要开启：在服务器 `config.json` 的 `esports` 段填 `cito_api_key`（形如 `cito_xxx`）并把 `cito_enabled` 改成 `true`；回滚就是把这两处改回去。**四个模板与字体、`watch.py` / `selftest.py` / `watchdog.py` 一个字都没动**；变的是 `esports.py` / `config.example.json`，并新增 `CITO_PLAYERS.md`（设计与实测的唯一权威，`REQUIRED` 成员）。⚠️ 选手数据只出现在**单场战报**，**不进全天整合版**（快照不存 `players`、整合模板也没有选段区块） |

下面那张表是「装下播提醒」那一版的记录，**留作历史说明**，实际以第 0 步的指纹表为准。

---

## 这次更新了两个东西（一次装完）

| 提交 | 内容 | 你能看到的变化 |
|---|---|---|
| `2335ff3` | `watch.py`：新增**下播提醒** + 记录**本次直播总时长** | 一场直播会收到两条消息，下播那条带「直播时长：1 小时 23 分」 |
| `8f90294` | `watchdog.py`：**报平安发失败不再被吞掉** | 每天 10:00 那条微信「报平安」发失败会自动退避重试，不再当天永久丢失 |

第二件是早先修好但**一直没上服务器**的，这次一起装。

---

## 三条硬约束

1. 这台机器上还跑着别的服务（个人网页）。**不要停、不要改、不要删任何现有的容器 / systemd 单元 / 其它配置文件。**
2. **绝不执行 `docker compose down -v`**（`-v` 删数据卷，QQ 登录态没了要重新扫码）；
   **也不要 `docker restart napcat`** —— 它现在是在线可用的状态，别动它。
3. 不要改 `config.json` 里的 token / 群号 / `treat_loop_as_live`。
   **唯一允许动 `config.json` 的地方是第 4 步**（只加两个开关）。

---

## 第 0 步 · 指纹核对（先做，不符就停）

`deploy.zip` 由用户上传。若当前目录没有，先在 `/root`、`/tmp` 下找最近上传的那个；
**找不到就停下问用户**，不要拿仓库里的旧包凑合。

```bash
cd <你放 deploy.zip 的目录>
unzip -o deploy.zip && cd deploy
sha256sum watch.py selftest.py watchdog.py esports.py result_template.html daily_template.html \
  daily_results_template.html preview_template.html config.example.json \
  card_font.otf make_card_font.py fonts/*.ttf | cut -c1-16
wc -c watch.py selftest.py watchdog.py esports.py result_template.html daily_template.html \
  daily_results_template.html preview_template.html config.example.json \
  card_font.otf make_card_font.py fonts/*.ttf
sha256sum ../deploy.zip | cut -c1-16
```

代码与模板必须完全一致，资产（字体）单独对：

| 文件 | sha256 前 16 位 | 字节数 |
|---|---|---|
| `watch.py` | `13243138d39468a9` | 54493 |
| `selftest.py` | `8f053b5f4404ceea` | 34274 |
| `watchdog.py` | `0b66acce3c3d7571` | 85650 |
| `esports.py` | `9d5fb033d8413e53` | 406175 |
| `result_template.html`（V2 单场战报模板） | `b6e87d77330ba710` | 10493 |
| `daily_template.html`（V2 总预告模板） | `0653140883abc5d3` | 8784 |
| `daily_results_template.html`（**V2 全天整合版模板**） | `5218bee78c886056` | 10271 |
| `preview_template.html`（开赛提醒模板） | `5b20c5c517966626` | 8366 |
| `config.example.json`（配置样板，含 Cito 开关与结算超时默认值） | `179328b8a8dffe81` | 16535 |
| `fonts/BebasNeue-Regular.ttf` | `08e4623805102d81` | 61400 |
| `fonts/IBMPlexMono-Regular.ttf` | `6a3412f058c7d8df` | 135580 |
| `fonts/IBMPlexMono-SemiBold.ttf` | `d3c38e55c78f5b0f` | 140216 |
| `card_font.otf`（880px 旧卡字体，降级用） | `15c77181345f84d5` | 68720 |
| `make_card_font.py`（生成字体的脚本） | `b692eada86ff9cf1` | 5403 |

> **本版新增第 7 个单元 `douyu-cmd.service`**（群内命令交互，`esports.py --listen`）——
> 它是本项目**第一个也是唯一一个常驻服务**（`Restart=always`，**没有 `.timer`**）。
> `install-watch.sh` 里加了安装分支，**只装不 enable**（与其余单元一致）；要试就先前台
> `python3 esports.py --listen` 手动跑一次确认行为。
> 指纹：`douyu-cmd.service` = `811364fdd54b7b00` / 1664 字节
> （它**不在上表里** —— 上表只列代码 / 模板 / 配置样板 / 字体，其余 `.service` 也都不在里面）。
> 同时 `CMD_INTERACT.md`（群内 `/赛事` 命令的设计文档）从「纯设计」更新为「已实现」，
> 它和 `douyu-cmd.service` 都是 `REQUIRED` 成员，会随包装上；两个都不是运行必需品 ——
> **不启用新单元 = 推送线行为与上一版完全一致**，老机器上不装也一切照常。

**本版**（commit 见 `git log -1 --oneline`）新增**群内命令交互**（@ 机器人 · `/赛事`）：
`esports.py` 多了 `--listen`（常驻轮询 `get_group_msg_history`，四道闸门 + 水位线幂等，
**复用 09:30 那套渲染栈**），并新增第 7 个单元 `douyu-cmd.service`（唯一常驻服务，**无 timer**）。
**四个模板与字体、`watch.py` / `selftest.py` / `watchdog.py` 一个字都没动**；变的是
`esports.py` / `config.example.json` / `install-watch.sh` / `pack_deploy.py` / `CMD_INTERACT.md`，
并新增 `douyu-cmd.service`。命令的完整设计、难点、坑与**一键回滚**见 `CMD_INTERACT.md`；
这里只讲一句怎么验：**不启用 `douyu-cmd` 就等于没这个功能**（对推送线零影响）。

**上一版**（commit 见 `git log -1 --oneline`）是一次**外部代码复查后的批量修复**：复查报告基于
旧提交 `172b7d1`（落后 11 个提交），8 条里 2 条已过时/不成立，剩下 **6 条**逐条修掉，
每条都带自检断言 + 变异验证。**四个模板与字体文件一个字都没动**；
变的是 `esports.py` / `selftest.py` / `config.example.json` / `douyu-esports-daily.{service,timer}`
/ `.gitignore` / `pack_deploy.py`。按影响面排序：

① **状态文件的并发安全**（复查 #6，最重）。`state_esports.json`（待结算清单）与
   `state_esports_announce.json`（开赛提醒）的「读—改—写」现在走一把**跨进程 `flock`**
   （`file_lock`）。三个要点：锁的是**独立的 `*.lock` 文件**（状态文件是 rename 替换的，
   锁在「被替换掉的那个 inode」上等于没锁）；**必须在锁内重读**再合并，不能拿锁外的旧快照；
   合并按**身份键**（`_item_ident` = 队名键 + 登记 ts，和去重判据同源）。
   原来 `backfill_abandoned` 里 `save_results_pending({"items": items})` 那种
   **把手上旧快照整体覆盖回去**的写法已全部清除（有断言钉住「全文件无此裸调用」）。
   条款节流 `parse_gate` 也改成锁内「重读 → 等待 → 写」。
   没有 `fcntl` 的平台（Windows 开发机）自动退化成「不加锁放行」，自检在两边都能跑。
② **战报的崩溃重复窗口从「整批」缩到「一条」**（复查 #7）。`run_results` 改成
   **每场发送成功后就立刻落盘**（`_persist([row])`），不再等整轮跑完统一保存；
   `run_announce` 同样逐条写锁。进程若正好卡在「已发出」和「写状态」之间被杀，
   最多重复最后一条，而不是整批重发。用 **AST 结构断言**钉住
   「循环里同时有发送与落盘、且落盘在发送之后」——不是靠源码里有没有某个字符串。
③ **同队再交手的第二场不再被吞**（复查 #3）。去重键原来是「排序后的队名」，而
   `_prune_pending` 会把 `reported` 条目留 36 小时 —— 同一对队伍当天打两场时，
   第二场会被当成重复预告丢掉。现在键是 `(队名键, 赛事名, 登记 ts)`，
   另加 `open_pairs` 守卫（同队仍有 `pending` 时保守跳过，防赛事名写法/时间漂移重复登记）。
④ **系列赛没打完不当战果**（复查 #5）。新增 `_bo_clinch` / `series_partial` /
   `series_overshoot`：页面标了「已结束」但比分谁都还没赢下系列赛（Bo3 出现 1:0）时
   **留在待结算**、下一轮再看。三条刻意的克制：**Bo1 永不拦**（1:0 是地图比分）、
   赛制认不出就一律不拦、超上限（Bo3 的 3:0）**只留痕不拦**。
   整合版出图前的补漏 `backfill_abandoned` 显式传 `strict=False` ——
   兜底宁可带上可疑终局，也不能像 2026-10-05 那样丢掉场次。
⑤ **`douyu-esports-daily.timer` 补上 `Asia/Shanghai`**（复查 #4）。09:40 那条原来没写时区，
   系统时区是 UTC 的机器上会跑到北京时间 17:40。`selftest.py` 新增扫描：
   所有**固定钟点**的 `OnCalendar` 必须显式写时区。
⑥ **大图行数上限三处对齐**（复查 #8）。新增尺子 `deploy/measure_img_capacity.py`
   （用无头浏览器量真实 DOM，不是估），**实测**确立 `HTML_IMG_MAX_ROWS = 21`：
   22 行时主体下边缘 1042 越过 1034、溢出 8px、最后一列被切。关键发现：
   **总预告 `daily_template.html` 和整合版是同一套几何、同一个数**
   （页头 96 + 页脚 46 + 主体 `flex:1` + 同一个 `density()` 三档行高），
   所以原来「总预告 22 场」那条守卫同样是错的。
   `daily_max_rows` 默认值 24 → 21、`config.example.json` 同步、
   `douyu-esports-daily.service` 的注释改成 21 行。880px 旧卡的 `card_max_rows`
   （默认 12）是**另一个画布**，刻意不合并（有断言钉住两者是不同的数）。
⑦ **两个自检都得能在「装好的机器」上跑完**（本版自己踩出来的一条）。上面 ⑥ 的断言
   顺手读了 `config.example.json` 和 `douyu-esports-daily.service`，可这两个文件
   `install-watch.sh` **不装**到 `/opt/douyu-live-notify` —— 于是包内全绿、
   服务器上第一条路径就 `FileNotFoundError`，**整个自检在中途断掉**。
   现在两处都改成「先 `os.path.isfile` 挡一道，不在就打 `[skip]` 写明原因」：
   `esports.py --selftest` 在装好的机器上会少 3 条（打 `[skip]`），
   `selftest.py` 是 **92 项**（少 8 条，见第 3 步）。并且用**语法树**加了一条断言，
   钉住「那几个 `open()` 必须落在带 `isfile` 的分支里」——
   **别把守卫删掉**。

> `deploy.zip` 里还多一个 `measure_img_capacity.py`（上面那把尺子，指纹
> `130e41031a4e519c` / 9286）。它**只在开发机/维护时手动跑**，线上任何 timer 都不调用；
> 对不上也不影响运行，不用为它停下来。

**再上一版**（`48051fc`）修的是**整合版少列场次**：结算的放弃时刻锚在
「登记（计划）开赛时刻」上，而前一档打满三图会把下一档拖后 30~60 分钟，
于是「打满三图的 Bo3」经常在放弃时刻前后才出结果 —— 2026-10-05 因此丢掉
`M80 vs TYLOO` 2:1 与 `Aurora Gaming vs BetBoom` 2:1 两场，次日整合版只剩 6 场。
两处修改：① `results_timeout_minutes` 由 `{Bo1:90, Bo3:180, Bo5:270}` 调成
`{Bo1:150, Bo3:270, Bo5:360}`（= 最长时长 + 约 90 分钟迟到/页面延迟余量）；
② 新增 `backfill_abandoned()`：整合版出图前把该赛程日所有 `abandoned` 的场次
再捞一次，配上就改回 `reported` 补进汇总。**只在真有 abandoned 条目时才抓页面**，
正常日仍是 0 网络请求。**更早**（`8c6c4d8`）修的是开赛提醒的级联判据
（「同一赛事」→「同一赛事且共用队伍」）。**再更早**（`4bdb153`）新增
**V2 全天整合版模板** `daily_results_template.html`，并同步 `esports.py`
（新增 `build_daily_results_data` / `render_daily_results_card_html` /
`render_daily_results_card`，`run_daily` 改走三级降级）与 `result_template.html` /
`daily_template.html`（署名随仓库改名）。**再往前**是开赛提醒（`--announce`）；
更早的修复：模板字体 URI 双 `file:///` 前缀（曾致随包字体静默失效）、
`find_chrome` 补 `/snap/bin` 与 `/usr/bin` 绝对路径候选、总预告 HTML 上限 30→22 场、
生僻字断言按 V2 行为拆分、自检含真渲染 PNG 实际尺寸校验。

> **整包指纹故意不写死**：`deploy.zip` 里**包含本文件**，改一次就变一次，
> 写在这里必然对不上。要确认两台机器拿到的是同一个包，
> 直接在两边各跑一次 `sha256sum deploy.zip` 对比即可。
> 判断「是不是新版」请看上面的**逐文件指纹**（那些是稳定的）。

**任何一项不符 → 立刻停下**，把实际输出发回来，不要继续装。
（字体对不上不致命 —— 卡片会自动退回纯文本，但那就白改这一版了。）
> ⚠️ 本次 `card_font.otf` **必须一起更新**：新版卡片要画「选手评分」四个字
> （选手段的表头）。只更新 `esports.py` 不更新字体的话，
> 卡片会**静默退回纯文本** —— 消息照样发得出去，但图片全没了。

> ⚠️ **这张表是权威**。不要把记忆里或旧文档里的值当基准 —— 这版 `watchdog.py` 的指纹
> 和更早的 `UPDATE_PROMPT.md` 副本**已经不一样了**（那时是 `d49b24c4427fb108` / 84789，
> 后来合回了线上手工打的 3 个修复）。看到「不符」先回来对这张表，别自行判断是新是旧。
>
> ⚠️ 别用 `python3 watchdog.py --version` 判断新旧 —— 它的 `VERSION` **一直没跟着改**
> （仍是 `1.0.0`），看不出差别。**只认 sha256 指纹。**

---

## 第 1 步 · 备份 + 记下改前的配置（只读）

```bash
cd /opt/douyu-live-notify
ts=$(date +%Y%m%d-%H%M%S)
sudo cp -a config.json "config.json.bak.$ts"
sudo python3 -c "import json;c=json.load(open('config.json'));print('改前：',{k:c.get(k) for k in ('room_id','channels','treat_loop_as_live','notify_on_end','notify_end_max_hours')})"
```

把「改前：…」那行**原样记下来**，第 4 步要对照。
（`watch.py` / `watchdog.py` 安装脚本自己会先比指纹再备份，不用你管。）

---

## 第 2 步 · 装

```bash
cd <解压目录>/deploy
sudo bash install-watch.sh
```

**逐行读它的输出**，重点两处：

1. 末尾「指纹（sha256 前 16 位）」那几行 —— 必须与第 0 步一致
2. 应出现 `config.json 已存在，保持不动（不会覆盖你的 room_id 和 token）`。
   **如果它说「已放入 …/config.json」，说明原来那份没了 —— 立刻停下报告**：
   那意味着群号和 token 被模板顶掉了。

安装脚本**只放文件 + `daemon-reload`**，不启用、不重启任何定时器，已有的定时器状态不受影响。
所以这一步**不需要**手动重启服务。

---

## 第 2.5 步 · 装浏览器 + 中文字体（V2 大图卡片的前提，**只做一次**）

这一版卡片用无头浏览器截图渲染 1920×1080 大图。**不装的话消息照发**，
但卡片会一直走 880px 旧卡降级 —— 装完才出用户拍板的新版式。

```bash
# ⚠️ 国内 ECS **不要用 snap 装 chromium** —— 实测会卡死在「connect plugs」
#    一个多小时不动（--version 都 0% CPU 阻塞），snapd restart 也救不回来。
#    用 Google Chrome 的 deb 包（dl.google.com 国内可达）：
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
  && sudo apt-get install -y /tmp/chrome.deb    # 约 120MB，耐心等
google-chrome --version                          # 确认可用（如 154.0.x）
sudo apt-get install -y fonts-noto-cjk           # 卡片上有中文（「跨图平均 Rating」等）
```

装完不用重启任何服务 —— 渲染是每次发卡时现起的，下一轮 timer 自动用上。
（esports.py 会自动探测到 `/usr/bin/google-chrome`；也可在 config.json 的
`esports.chrome_bin` 里钉死绝对路径，最稳。）

---

## 第 3 步 · 自检（不联网、不发消息）

```bash
cd /opt/douyu-live-notify
python3 selftest.py; echo "退出码=$?"
tail -3 selftest_result.txt

python3 watchdog.py --selftest; echo "退出码=$?"

python3 esports.py --selftest; echo "退出码=$?"
```

- `selftest.py`：**期望 0 项失败**、退出码 0。项数取决于跑在哪儿（**两种都对**）：
  - **装好的机器 / 解开 deploy.zip 的目录**（`/opt/douyu-live-notify`，
    没有 `deploy/` 子目录）→ **92 项**；
  - **git 仓库根目录**（`selftest.py` 与 `deploy/` 同级）→ **100 项**。
  差的 8 项查的是仓库里才有的东西：3 条扫 `deploy/*.timer` 验证时区、
  5 条查 `.gitignore` 与 `pack_deploy.py`。在装好的机器上它们**无对象可查**，
  会打 `[skip]` 并写明原因（**这是本版新加的**：这 8 条以前是无条件执行的，
  在服务器上必然报红 —— 你可能会在旧日志里看到那种 FAIL，别看错）。
- `watchdog.py --selftest`：**期望 0 项失败**（项数随版本变，不用数）。
- `esports.py --selftest`：**期望 0 项失败**、退出码 0。它**不联网**（队标断言用本地
  fixture），跑得很快。**项数会随环境浮动，都是正常的**：

  | 条件 | 项数 |
  |---|---|
  | 满配（Pillow + `make_card_font.py` + 无头浏览器，正常情况） | **469** |
  | 少了 `make_card_font.py` | 467（少 2 条「两张字符表是否一致」的断言） |
  | 没装无头浏览器 | 少 5 条（HTML 真渲染断言换成「没浏览器返回 None」的降级断言） |
  | 没装 Pillow | 四张 880px 卡片的渲染断言换成降级断言 |
  | **装好的机器上**（没有 `config.example.json` / `*.service`） | **466**（少 3 条，打 `[skip]`） |

  ⚠️ 最后那一行是新加的：`config.example.json` 和 `douyu-esports-daily.service`
  只在仓库和部署包里，`install-watch.sh` 不把它们装到 `/opt/douyu-live-notify`。
  所以那 3 条要**先 `os.path.isfile` 挡一道**再读 —— 否则服务器上第一条路径就
  `FileNotFoundError`，整个自检**在中途断掉**（2026-10-06 首次部署这一版真的崩过一次，
  包内全绿、服务器上直接抛异常）。现在有断言用语法树钉着这个守卫，
  别把它删掉。

  **唯一要盯的是「0 项失败」**。项数对不上就去看上面 Pillow / 浏览器那两行、
  再 `ls card_font.otf make_card_font.py`。

**只要出现 `[FAIL]` 或退出码非 0 → 停下**，把完整输出发回来，不要继续。

顺便确认一下画卡片能不能用（**只读，不发消息**）：

```bash
python3 -c "import PIL; print('Pillow:', PIL.__version__)" || echo "Pillow: 没装（卡片会自动退回纯文本）"
ls -l /opt/douyu-live-notify/card_font.otf /opt/douyu-live-notify/CARD_FONT_LICENSE.txt
```

---

## 第 4 步 · 打开下播提醒（**最容易漏，漏了就白装**）

这是整份提示词里最关键的一条。

`install-watch.sh` **有意不覆盖已有的 `config.json`**（那里面是你的 room_id 和 token）。
所以服务器上那份此刻仍是 `"notify_on_end": false` —— **光更新代码，下播提醒不会生效**，
你会以为装好了，然后白等一整天。

用下面这条就地改（只动这两项，其余字段原样保留）：

```bash
sudo python3 - <<'PY'
import json
p = "/opt/douyu-live-notify/config.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg["notify_on_end"] = True
cfg.setdefault("notify_end_max_hours", 24)
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("改后：", {k: cfg.get(k) for k in
      ("room_id", "channels", "treat_loop_as_live", "notify_on_end", "notify_end_max_hours")})
PY
```

**核对改前/改后**：除了 `notify_on_end`（`false` → `true`）和新增的
`notify_end_max_hours`（`24`），其余字段（`room_id`、`channels`、`treat_loop_as_live`）
**必须一字不变**。对不上就停，用备份还原：

```bash
sudo cp -a /opt/douyu-live-notify/config.json.bak.<时间戳> /opt/douyu-live-notify/config.json
```

---

## 第 5 步 · 离线验证下播消息格式（零风险：不联外网、不发消息）

直接调用装好的 `watch.py`，证明**服务器上跑的确实是新版**、且文案正确：

```bash
cd /opt/douyu-live-notify
python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("w", "watch.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
cfg = {"room_url": "https://www.douyu.com/6657"}
cur = {"room_id": "6979222", "anchor": "某主播", "title": "随便播播", "online": 2830556}
print(w.format_message(cur, cfg, "end", duration_seconds=4980,
                       started_at="2026-09-24T19:30:00+08:00"))
print("---")
print("时长格式:", [w._fmt_duration(s) for s in (0, 60, 3600, 4980, 9000)])
print("下限写法:", w._fmt_duration(4980, at_least=True))
PY
```

**期望输出**（逐字对照）：

```
【斗鱼下播】某主播
标题：随便播播
热度：283.1 万
开播时间：2026-09-24 19:30:00
直播时长：1 小时 23 分
https://www.douyu.com/6657
---
时长格式: ['不到 1 分钟', '1 分钟', '1 小时', '1 小时 23 分', '2 小时 30 分']
下限写法: 至少 1 小时 23 分
```

对不上 → 停下报告（多半第 2 步装到的是旧文件）。

---

## 第 6 步 · 顺带体检那版看门狗修复（只读）

不会发告警、不会重启任何东西：

```bash
sudo python3 /opt/douyu-live-notify/watchdog.py --status
sudo tail -n 6 /var/lib/douyu-watchdog/alerts.log
sudo grep -a 'daily_ok' /var/lib/douyu-watchdog/alerts.log | tail -5
sudo grep -a 'daily_ok' /var/log/douyu-watch/watchdog.log | tail -5
```

要看三件事，**原样回报**：

1. `--status` 里那行「报平安」现在是什么状态（应能直接写出「今天已送达 / 重试中 / 已放弃 / 还没到点」）
2. `alerts.log` 里最近的 `daily_ok` 记录**有没有带失败原因**（带原因说明这版修复已生效）
3. 近几天有没有「报平安」相关报错

> ⚠️ 两个别误判的点：
> - 今天 10:00 那条是**旧代码**发的，可能被错误地记成「已送达」。所以 `--status` 显示
>   「今天已送达」**不能证明**此刻通道是好的。真要看通道，跑 `python3 watchdog.py --test-alert`
>   （会真发一条告警，用户同意后再跑）。
> - 修复的生效时点是**明天 10:00**：那条要是发失败，才会看到退避重试的记录。

---

## 第 7 步 · 确认新代码在跑（不用重启，等一轮即可）

定时器是 `OnUnitActiveSec=1min`（单次执行，跑完再等 1 分钟），所以装完**最多 2 分钟**就会用新代码跑一轮。

```bash
systemctl is-active douyu-watch.timer
systemctl list-timers douyu-watch.timer --no-pager | head -3
systemctl show douyu-watch.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp
sudo tail -n 5 /var/log/douyu-watch/tick.log
sudo cat /opt/douyu-live-notify/state_6979222.json
```

- `is-active` 应为 `active`；`Result` 应为 `success`
- `tick.log` 末尾那行心跳的时间应在**刚过去的 1~2 分钟内**
- 状态文件里现在会多出 `live_started_at` / `live_started_approx` 两个字段：
  主播**正在播**时应有值（前者是时刻、后者 true/false）；**没在播**时为 `null` / `false`。
  **这是正常的，不用改它**，也不用删。

---

## 第 8 步 · 真正的验收（等它自然发生，**不要造数据**）

**不要为了测试去手改状态文件** —— 那会凭空发出一两条假通知。

下一次主播开播时，状态文件里会出现 `live_started_at`；等这次**下播**后，
用户应收到一条**带「直播时长：…」**的群消息。请用户确认并把消息原文发回来。

判断时长是否合理时看这几条：

- 时长是「程序看到的那一段」，程序没跑的时间不计入 → 只会**偏短**，不会凭空变长
- 如果部署时主播正好在播，第一场可能写成 **「至少 X 小时 Y 分」**（只报下限）—— 这是对的，不是 bug
- 时长按 `confirm_rounds`（默认 2）确认后才起算，比真实时长少约 1 轮

---

## 出错时的原则

- **不要反复重试同一条失败的命令**
- **不要为了「让它通过」而放松设置**：不要放开端口、不要清空 token、
  不要把 `treat_loop_as_live` 改掉、也不要把 `notify_on_end` 改回 `false` 去掩盖问题
- **不要删除或重建任何容器 / systemd 单元**（这台机器上还有别的服务在跑）
- 失败就把**命令 + 完整原始输出**发回来，越原始越好

---

## 最后回报这几项

1. 第 0 步**六行** `sha256sum` 输出（原样）
2. 第 2 步 `install-watch.sh` 的**完整输出**（含它打印的指纹、以及 Pillow 那两行结论）；
   确认里面出现过 `已安装 douyu-esports-results.service / .timer（尚未启用）`
   **和** `已安装 douyu-esports-daily.service / .timer（尚未启用）`（**两个都要有**），
   以及 `已安装 douyu-cmd.service（尚未启用）`（本版新增的**常驻**单元）。
   ⚠️ `douyu-cmd` **只装不启用** —— 它会在群里回消息，**先前台** `python3 esports.py --listen`
   手动跑一次确认行为，再决定要不要 `systemctl enable --now douyu-cmd`。
3. 第 3 步三个自检的**最后一行 + 退出码**（有 `[FAIL]` 就附全文），外加 `card_font.otf` 是否就位、Pillow 是否可用
4. 第 4 步改前 / 改后那两行配置对照（**token 不要打印**）
5. 第 5 步离线格式验证的输出原文
6. 第 6 步 `--status` 全文 + 最近几条 `daily_ok` 记录
7. 第 7 步 `is-active` / `Result` / `tick.log` 末行 / 状态文件内容
8. **单场战报**（如果这一步要求做）：`python3 esports.py --check-results` 的完整输出
   （含逐场明细、`逐图比分：抓了 N 个赛事页，M/M 场补上了逐图`、
   以及 `/tmp/esports_result_check_01.png` … 每场一张的路径），
   以及 `systemctl list-timers douyu-esports-results.timer` 的结果。
   ⚠️ 这条命令**会真去抓页面**（赛程页 1 次 + 每个不同赛事 1 次），但**不发消息、不写状态**。
   清单空着也会演练（拿页面上最近打完的 8 场演示），所以这是**部署后唯一能验证版式的手段**。
   因为它要过条款闸门（1 次 / 30 秒），**整轮可能要等 30 秒 × 赛事数才出结果，别以为卡死了**。
   **不要**因为它名字里带 check 就以为它完全不联网。
9. **全天整合版**（如果这一步要求做）：`python3 esports.py --check-daily` 的完整输出
   （含 `上一个赛程日 … → …：清单里 N 场，其中 M 场已结算入册` 和出图路径），
   以及 `systemctl list-timers douyu-esports-daily.timer` 的结果。
   ⚠️ 这一条**完全不联网**；但没有已结算的场次时它会 `[silent]` 退出，这是正常的、不是故障。
10. **任何跳过、失败或你不确定的地方 —— 直接说，不要掩盖**
