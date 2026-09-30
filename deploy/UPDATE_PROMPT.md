# 更新提示词 · 装「下播提醒 + 直播总时长」（含一版看门狗修复）

> **用法**：整份复制，发给服务器上的 agent。
> **适用场景**：这台机器上**已经在跑**（watch.py + 看门狗都装好了、定时器已启用），
> 这次只是**更新代码**，不重装环境。
> 全新部署请看 `AGENT_PROMPT.md`；卡在内存那一步的续跑请看 `RESUME_PROMPT.md`。

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
sha256sum watch.py selftest.py watchdog.py | cut -c1-16
wc -c watch.py selftest.py watchdog.py
```

三个值必须完全一致：

| 文件 | sha256 前 16 位 | 字节数 |
|---|---|---|
| `watch.py` | `13243138d39468a9` | 54493 |
| `selftest.py` | `ad142bcd05d58df7` | 28399 |
| `watchdog.py` | `d49b24c4427fb108` | 84789 |

**任何一项不符 → 立刻停下**，把实际输出发回来，不要继续装。

> ⚠️ 别用 `python3 watchdog.py --version` 判断新旧 —— 它的 `VERSION` 这次**没有**跟着改
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

1. 末尾「指纹（sha256 前 16 位）」三行 —— 必须与第 0 步一致
2. 应出现 `config.json 已存在，保持不动（不会覆盖你的 room_id 和 token）`。
   **如果它说「已放入 …/config.json」，说明原来那份没了 —— 立刻停下报告**：
   那意味着群号和 token 被模板顶掉了。

安装脚本**只放文件 + `daemon-reload`**，不启用、不重启任何定时器，已有的定时器状态不受影响。
所以这一步**不需要**手动重启服务。

---

## 第 3 步 · 自检（不联网、不发消息）

```bash
cd /opt/douyu-live-notify
python3 selftest.py; echo "退出码=$?"
tail -3 selftest_result.txt

python3 watchdog.py --selftest; echo "退出码=$?"
```

- `selftest.py`：这一版是 **92 项**，**期望 0 项失败**、退出码 0。
- `watchdog.py --selftest`：**期望 0 项失败**（项数随版本变，不用数）。

**只要出现 `[FAIL]` 或退出码非 0 → 停下**，把 `selftest_result.txt` 全文发回来，不要继续。

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

1. 第 0 步三行 `sha256sum` 输出（原样）
2. 第 2 步 `install-watch.sh` 的**完整输出**
3. 第 3 步两个自检的**最后一行 + 退出码**（有 `[FAIL]` 就附全文）
4. 第 4 步改前 / 改后那两行配置对照（**token 不要打印**）
5. 第 5 步离线格式验证的输出原文
6. 第 6 步 `--status` 全文 + 最近几条 `daily_ok` 记录
7. 第 7 步 `is-active` / `Result` / `tick.log` 末行 / 状态文件内容
8. **任何跳过、失败或你不确定的地方 —— 直接说，不要掩盖**
