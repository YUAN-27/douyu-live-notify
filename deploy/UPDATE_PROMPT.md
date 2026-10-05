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
> 本次还会多装四个单元：`douyu-esports-results.{service,timer}`（单场战报）和
> `douyu-esports-daily.{service,timer}`（全天整合版），同样是**只装不启用**。
>
> ⚠️ **本次改动了消息版式**（胜方绿名 / 负方红名、单场战报带逐图比分、新增每日整合版），
> 并且**改了配置键默认值**（`results_max_age_hours` 24→36）。服务器上的 `config.json`
> 如果显式写过这一项，要一起改，否则整合版会缺场次。

---

## 这次的包比下面这张表更新（先看这里）

| 提交 | 内容 | 你能看到的变化 |
|---|---|---|
| `78f0981` / `3bc7a79` | 新增 `esports.py`：CS2 每日赛程预告 + 知名队伍白名单 | 每天北京 09:30 推一条「今日赛程」（**需要另外启用 `douyu-esports.timer` 才会跑**） |
| `cbffcb0` | 赛程预告改版：**一行文字 + 一张图片卡片**（带队标） | 群里收到的赛程变成长图；**新增 `card_font.otf` 一个文件**，画卡片还要 Pillow（`python3-pil`，装不上会自动退回纯文本） |
| `e87d6f9` / `721dad6` | 占位块改用页面短名；文档同步 | 队标拿不到时，灰块里显示的是页面自带的短名（如 `Spirit`），不再是三个 `TE` |
| `08cbb23`（上一次） | 新增**战果公布**：预告过的比赛打完后补一条比分 | 预告后还会收到战果消息；**新增两个单元** `douyu-esports-results.{service,timer}`（只装不启用）。零新增数据源 —— 还是那一个页面、同一份解析器 |
| **（本次）** | **战果改成两条通道 + 队名按胜负上色** | ① **每场一条**「单场战报」（胜方绿名 / 负方红名，卡片里逐图一行，比分按该图胜负上色）；② 新增 `douyu-esports-daily.{service,timer}`，**次日 09:40** 发上一个赛程日的**全天整合版**（一场一行、不带逐图，**不联网**）。逐图比分来自 Liquipedia **赛事页**（页面路径是链接里自带的，不用维护别名表），抓不到就少画几行、不影响发送。**⚠️ 卡片字体多带了「地」「报」两个字，`card_font.otf` 必须一起更新**，否则卡片会静默退回纯文本 |
| `e3788b3` | **单场战报带逐图选手数据**（csdb.gg）+ 卡片重排版 | 战报卡片下部多两列 5v5 选手数据（K-D/ADR/KAST/Rating，取自 csdb.gg 单场页）；拿不到选手数据时自动少画，不影响发送。新增配置键 `card_players_enabled`（默认开）/ `card_players_per_team`（默认 3） |
| `4f24807` | 队标优先取 **darkmode** 变体 | 亮/暗双图队伍（Vitality/G2/NAVI/Spirit…）在深色卡片上恢复彩色版（Vitality 黑蜜蜂→黄蜜蜂）；单图队伍不受影响 |
| **（本次）** | **卡片全部换成 V2 大图（1920×1080 HTML 渲染）** | 战报卡和总预告卡变成用户拍板的深色大图版式。**新增 5 个文件**：`result_template.html`、`daily_template.html`、`fonts/`（3 个 ttf）。渲染优先走 Chromium 截图，没装 Chromium / 渲染失败**自动退回 880px 旧卡**，再不行退纯文本 —— 消息永远照发。**服务器要装 Chromium + 中文字体**（见第 2.5 步），不装就一直是旧 880px 卡 |

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
sha256sum watch.py selftest.py watchdog.py esports.py result_template.html daily_template.html card_font.otf make_card_font.py fonts/*.ttf | cut -c1-16
wc -c watch.py selftest.py watchdog.py esports.py result_template.html daily_template.html card_font.otf make_card_font.py fonts/*.ttf
sha256sum ../deploy.zip | cut -c1-16
```

代码与模板必须完全一致，资产（字体）单独对：

| 文件 | sha256 前 16 位 | 字节数 |
|---|---|---|
| `watch.py` | `13243138d39468a9` | 54493 |
| `selftest.py` | `ad142bcd05d58df7` | 28399 |
| `watchdog.py` | `0b66acce3c3d7571` | 85650 |
| `esports.py` | `a234e2ed65905874` | 261436 |
| `result_template.html`（V2 战报模板） | `461df01be5d89d33` | 10518 |
| `daily_template.html`（V2 总预告模板） | `3d5c9fda90c215e6` | 8808 |
| `fonts/BebasNeue-Regular.ttf` | `08e4623805102d81` | 61400 |
| `fonts/IBMPlexMono-Regular.ttf` | `6a3412f058c7d8df` | 135580 |
| `fonts/IBMPlexMono-SemiBold.ttf` | `d3c38e55c78f5b0f` | 140216 |
| `card_font.otf`（880px 旧卡字体，降级用） | `15c77181345f84d5` | 68720 |
| `make_card_font.py`（生成字体的脚本） | `f93d2269ef85a165` | 5403 |

`deploy.zip` 整包：`9d1787d5e4aacfb7`（479.7 KB，44 个文件；新增文件是 `fonts/LICENSE-OFL.txt` 字体许可证，且 install-watch.sh 已会自动部署两份模板与 fonts/）。

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

## 第 2.5 步 · 装 Chromium + 中文字体（V2 大图卡片的前提，**只做一次**）

这一版卡片用 Chromium 无头截图渲染 1920×1080 大图。**不装的话消息照发**，
但卡片会一直走 880px 旧卡降级 —— 装完才出用户拍板的新版式。

```bash
sudo snap install chromium          # Ubuntu 24.04；约 150MB，耐心等
sudo apt-get install -y fonts-noto-cjk   # 卡片上有中文（「跨图平均 Rating」等）
which chromium || ls /snap/bin/chromium  # 确认可执行文件在 PATH 里
```

装完不用重启任何服务 —— 渲染是每次发卡时现起的，下一轮 timer 自动用上。

---

## 第 3 步 · 自检（不联网、不发消息）

```bash
cd /opt/douyu-live-notify
python3 selftest.py; echo "退出码=$?"
tail -3 selftest_result.txt

python3 watchdog.py --selftest; echo "退出码=$?"

python3 esports.py --selftest; echo "退出码=$?"
```

- `selftest.py`：这一版是 **92 项**，**期望 0 项失败**、退出码 0。
- `watchdog.py --selftest`：**期望 0 项失败**（项数随版本变，不用数）。
- `esports.py --selftest`：**期望 0 项失败**、退出码 0。它**不联网**（队标断言用本地
  fixture），跑得很快。**项数会随两个可选条件浮动，都是正常的**：

  | 条件 | 项数 |
  |---|---|
  | 装了 Pillow **且**有 `make_card_font.py`（正常情况） | **297** |
  | 少了 `make_card_font.py` | 295（少 2 条「两张字符表是否一致」的断言） |
  | 没装 Pillow | 266（四张卡片的 31 条渲染断言换成 1 条「没 Pillow 就返回 None」的降级断言） |
  | 两样都没有 | 264 |

  **唯一要盯的是「0 项失败」**。项数对不上就去看上面 Pillow 那行、
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
   **和** `已安装 douyu-esports-daily.service / .timer（尚未启用）`（**两个都要有**）
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
