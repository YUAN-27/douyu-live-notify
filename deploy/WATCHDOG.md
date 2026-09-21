# 看门狗（watchdog）

`watch.py` 本身逻辑是对的，但它有两个**静默失败** —— 出事的时候，你收不到任何提示，
只会「什么都没发生」：

| # | 静默失败 | 具体表现 | 后果 |
|---|---|---|---|
| ① | 掉线无感知 | NapCat 被 QQ 踢下线 / 会话失效，watch.py 每分钟照跑照判定，只是 OneBot 发不出去，日志里留一行 `[error] onebot 通知失败` | 程序看起来完全正常，整条链路已经断了 |
| ② | 停摆无感知 | 定时器被禁、文件被改坏、进程被 OOM 杀掉 | 结果同样是「什么都没发生」，而不是报错。半夜开播，你第二天才知道没提醒 |

看门狗每 **2 分钟**独立体检一次，把这两类静默失败变成一条看得见的告警，
并在能自愈的时候自己动手。它**不修改 `watch.py`**（指纹保持不变），
只读日志与状态文件，必要时才去动 `docker` 和 `systemd`。

```bash
python3 watchdog.py --status       # 一条命令看当前健康状况（只读）
python3 watchdog.py --selftest     # 离线自检，不联网、不碰 docker
python3 watchdog.py --test-alert   # 往所有配好的通道发一条测试告警
```

---

## 一、它盯四件事

### 1. 心跳新鲜度 —— 「watch.py 还在跑吗」

读 `/var/log/douyu-watch/tick.log`，两个判据组合起来能区分三种情况：

| 日志 mtime | 心跳序号 `#N` | 判定 | 含义 |
|---|---|---|---|
| 陈旧（>7 分钟） | — | `not_running` | **根本没在跑**。定时器被禁 / 服务一直失败 / 机器有问题 |
| 新鲜 | 不前进 | `stuck` | **在跑，但每轮都在失败**。tick 里心跳打印在探测之后，探测抛错就永远走不到打心跳那步 |
| 新鲜 | 前进 | `ok` | 正常 |
| 文件不存在 | — | `no_log` | 定时器还没开过，或日志路径变了（只提示，不当作故障） |

> ⚠️ 为什么新鲜度看**文件修改时间**而不是日志里那行 `[12:34:56]`？
> 因为那行**只有时间没有日期**，日志是跨天追加的 —— 拿它算新鲜度，凌晨会误判。
> 这是实测踩出来的点，别改回去。

### 2. NapCat 是否在线 —— 「消息发得出去吗」

`docker inspect` 看容器，`POST /get_login_info` 看登录态。**关键是要区分出「该重启」和「重启没用」**：

| 判定 | 现象 | 该做什么 |
|---|---|---|
| `online` | `status:ok` 且拿到 QQ 号 | 正常 |
| `not_running` | 容器没在跑 | 可自愈 → 重启 |
| `unresponsive` | 容器在跑，但接口不通（假死 / 正在启动） | 可自愈 → 重启 |
| `logged_out` | 接口通，但未登录 | **重启没用**，要人工介入 |
| `need_qr` | 未登录 + 容器日志里出现了二维码 | **重启没用**，必须人工扫码 |
| `docker_missing` | 宿主机没有 docker 命令 | 如实报告，不假装正常 |
| `skipped` | 没配 OneBot 通道 | 跳过 |

最后两条是这套东西最容易被做错的地方：**掉线和「容器挂了」不是一回事**。
登录态失效时反复重启只是空转，还会制造抖动。

### 3. 日志里的「通知失败」—— 「有没有消息丢掉了」

扫 `tick.log` 里新增的 `通知失败` 行（按行号记偏移，同一行不会重复报警，
日志轮转（文件变小）时退回只扫尾部 200 行）。

**这是唯一一个看门狗只能告诉你、不能替你修的问题** —— 见第三节。

### 4. 昨天报的故障好了没 —— 恢复闭环

之前报过的故障（停摆 / 卡死 / 掉线）本轮恢复正常时，补发一条「已恢复」。
不然你会一直不知道是该继续担心还是已经没事了。

---

## 二、它会自己做什么

| 检测到 | 动作 |
|---|---|
| watch.py 停摆 | `systemctl restart douyu-watch.timer` + 立刻手动触发一轮 |
| NapCat 容器没在跑 / 接口无响应 / 被 OOM 杀 | `docker restart napcat` |
| NapCat 掉线（需扫码） | **不重启**，直接告警要人处理 |
| 通知失败 | 只告警（无法自动补发） |

**为什么敢在「停摆」后立刻手动触发一轮**：`tick` 只在状态**发生切换**时才发通知。
停摆期间主播真的开播了，这一轮正好把漏掉的通知补上；没开播则什么都不发。
所以它是恢复动作，不是误发源。

### 重启风暴保护（三道闸）

反复重启通常意味着内存不足或配置错误，继续重启只会让 NapCat 和你的个人网页**一起雪崩**。所以：

1. `FAIL_THRESHOLD`：连续 3 轮（约 6 分钟）不通才动手 —— 不把慢启动当故障；
2. `RESTART_COOLDOWN`：两次重启至少隔 15 分钟；
3. `MAX_RESTARTS_PER_DAY`：一天最多 6 次，到顶就**停手转人工告警**。

`AUTO_RESTART=0` 可以关掉全部自愈，只告警。
⚠️ 敢开自动重启（默认开）的前提是**「重启免扫码自动登录」已经验收通过**（见 `DEPLOY.md`）——
没验过就先设 `AUTO_RESTART=0`。

---

## 三、丢失的通知怎么补

`watch.py` 的 `notify_all()` 发送失败时只打一行 `[error]`，而状态**已经落盘** ——
所以那条开播通知会**永久丢失、不会重试**。看门狗只负责让你知道，不替你补发。

如果**主播现在还在播**，可以手动补上（只动状态文件，不动代码）：

```bash
cd /opt/douyu-live-notify
python3 watchdog.py --recover-notify        # 会先把原状态文件备份成 .bak.<时间戳>
```

原理：把 `state_<room_id>.json` 里的 `is_live` 改回 `false`，下一轮 tick 就会重新走一遍
「检测到开播 → 累计确认 → 发通知」，默认 2 轮 = 2 分钟。
想立刻看到效果：`systemctl start douyu-watch.service`。

两个前提，缺一个就别用：主播**现在确实还在播**；你接受群里出现一条迟到的开播通知。

---

## 四、告警发到哪（唯一需要你决定的事）

**为什么必须有一条「不经过 NapCat」的通道**：NapCat 掉线时它自己发不出消息。
只配 NapCat 私聊的话，「掉线告警」等于没说。

配 1~3 个都行，配了几个就同时用几个：

| 通道 | 配什么 | 覆盖掉线场景？ | 拿地址的办法 |
|---|---|---|---|
| NapCat 私聊 | `ALERT_ONEBOT_PRIVATE=<你的主QQ号>` | ❌ 依赖 NapCat | 不用申请，填你自己的 QQ 号 |
| **Webhook** | `ALERT_WEBHOOK=<URL>` | ✅ | 见下 |
| 邮件 | `ALERT_EMAIL=you@example.com` | ✅ | 服务器上要已有 msmtp/sendmail |

Webhook 支持这几种（`ALERT_WEBHOOK_KIND`）：

| kind | 适用 | 拿 webhook 地址 |
|---|---|---|
| `pushplus` | **微信推送，首选** | pushplus.plus 微信扫码登录 → **实名认证** → 复制 token，配成 `pushplus\|https://www.pushplus.plus/send?token=<token>` |
| `serverchan` | 微信推送（Server酱） | sct.ftqq.com 扫微信登录 → 拿 `https://sctapi.ftqq.com/<SendKey>.send` |
| `bark` | iOS 手机推送 | 装 Bark App，App 里直接给你一个 `https://api.day.app/<key>` |
| `wecom` | 企业微信群 | 群 → 右键 → 添加群机器人 → 复制 webhook |
| `feishu` | 飞书群 | 群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 webhook |
| `dingtalk` | 钉钉群 | 群 → 群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 webhook |
| `telegram` | Telegram | 找 @BotFather 建 bot 拿 token，`https://api.telegram.org/bot<token>/sendMessage`；另填 `ALERT_TG_CHAT_ID` |
| `generic` | 自建服务 / n8n / IFTTT | 会 POST `{"title","text","level","host","time"}` |

飞书和钉钉的机器人**可能需要先设关键词**（比如「告警」「斗鱼」），
否则会拒收 —— 我们的标题以「斗鱼提醒·看门狗」开头，关键词填「斗鱼」即可。

### 想推到微信，选哪家

两家都免费、都能进微信，差别很实在（2026-09 核实的官方口径）：

| | pushplus 推送加 | Server酱 Turbo |
|---|---|---|
| 免费额度 | 实名后 **200 次/天** | **5 条/天** |
| 微信里能看到 | 标题 + 完整正文 | **只有标题**，正文看不到 |
| 要不要实名 | 要（手机号） | 不要 |

**Server酱 免费版 5 条/天 对告警是不够的**：一次掉线持续 4 小时就发掉 4 条
（同一问题每小时最多提醒一次），再加每天一条「一切正常」正好满额。所以推微信
优先用 pushplus；**两个都配最稳**，一条挂了另一条照发。

### 可以同时配多条

`ALERT_WEBHOOK` 用 `;` 分开就能写多条，写法是 `类型|地址`：

```bash
# 微信双通道：pushplus 主力 + Server酱 兜底
ALERT_WEBHOOK=pushplus|https://www.pushplus.plus/send?token=你的token;serverchan|https://sctapi.ftqq.com/SCTxxxxxx.send
```

token 直接写在地址查询串里即可，看门狗会取出来塞进请求体；
打印日志和写告警正文时会把查询串隐去，不会把 token 漏出去。

> ⚠️ **Server酱有两个产品，SendKey 不通用、地址也不同**：
> - Turbo（`SCT` 开头）→ `https://sctapi.ftqq.com/<SendKey>.send`
> - 自定义域名（`sctp` 开头）→ `https://<uid>.push.ft07.com/send/<SendKey>.send`
>   （`uid` 是 key 里 `sctp` 与 `t` 之间的那串数字）
>
> key 和域名对不上是发不出去的。看门狗在每轮启动检查里会直接指出来该用哪个地址。

配置写在 `/etc/default/douyu-watchdog`（安装脚本会放一份模板，权限 600，**不会覆盖已有文件**）：

```bash
sudo nano /etc/default/douyu-watchdog
sudo python3 /opt/douyu-live-notify/watchdog.py --test-alert   # 验证通道真的通
sudo systemctl restart douyu-watchdog.timer                    # 改完重启定时器
```

> 一个都没配也能跑：告警会无条件写进 `/var/lib/douyu-watchdog/alerts.log`。
> 但那种情况下，程序会**明确告诉你**「没有配置任何独立于 NapCat 的告警通道，
> 这条告警只落在了本机日志里」—— 不假装成功。

### 沉默即出事（`DAILY_OK_AT`）

填一个你每天一定会看手机的时间（例如 `20:00`）。此后每天这个点，一切正常就发一条
「每日体检：一切正常」。**哪天你没收到，说明机器或者看门狗自己挂了** ——
这是唯一能覆盖「看门狗自己死掉」的手段。

---

## 五、部署后必须验证它真的会叫

**大部分人加完看门狗从来不验证。** 不验证的看门狗等于没加 ——
它可能在最需要的时候哑掉，而你到那时才会发现。

### 第 0 步：离线自检 + 通道验证

```bash
cd /opt/douyu-live-notify
python3 watchdog.py --selftest        # 期望：0 项失败、退出码 0
python3 watchdog.py --test-alert      # 手机/群里应该真的收到一条
```

`--test-alert` 收到 ≠ 端到端通。还要**故意制造一次真故障**，看它会不会自己叫你：

### 验证 A：停摆检测（不影响 QQ，最安全）

```bash
systemctl stop douyu-watch.timer      # 故意让它停摆
# 等 10 分钟以上（别只等 7 分钟！见下）
tail -20 /var/log/douyu-watch/watchdog.log
python3 watchdog.py --status
```

> **为什么是 10 分钟而不是「阈值 7 分钟」**：判定停摆需要「tick.log 超过
> `STALE_SECONDS`（默认 420 秒 = 7 分钟）没更新」，而看门狗自己每 2 分钟才跑一轮。
> 两件事叠加，最坏情况要 **7 + 2 = 9 分钟**才轮到那一轮。
> 第 7~8 分钟去查很可能什么都还没有，然后就误以为「验证失败」了。
> 这条踩过：文档原来写「等 8 分钟」，正好压在临界点上。

**应该看到**：一条「监控已停摆」告警 → 自动把定时器拉起来 → 下一轮发「已恢复」通知。
最后确认：`systemctl list-timers douyu-watch.timer` 里定时器又在了。

### 验证 B：掉线检测 + 自动重启（会短暂断线，建议先做 A）

```bash
docker stop napcat
# 等 6~8 分钟
tail -20 /var/log/douyu-watch/watchdog.log
docker inspect -f '{{.State.Running}}' napcat    # 应该已被自动拉起 → true
```

**应该看到**：连续 3 轮不通 → 告警 → `docker restart napcat` → 下一轮恢复。
这一步同时**顺带验证了「重启免扫码」**，一举两得。

> ⚠️ **两个前提，缺一别做**：
> 1. 你手边**能进服务器**（SSH / 云控制台的 VNC / 云助手都行）。
>    `docker stop` 是**手动停止**，Docker 的 `restart: always` 策略不会把它拉起来 ——
>    球完全在看门狗这边；万一自愈失败，容器就一直停着，而它正是你收通知依赖的东西。
> 2. 挑**主播没在播**的时候做。
>
> 兜底：等满 8 分钟若还是 `false`，手动 `docker start napcat` 即可。
> 去不了服务器、或者只是不想承担这个风险 → 直接用**验证 C**。

### 验证 C：零风险版「掉线检测」（**不想动真容器就用这个**）

验证 B 要真的停掉 NapCat。如果你**没有 SSH 通路**（比如云安全组挡了 22 端口）、
或者担心它起不来，就用这个方法：**把看门狗指向一个不存在的容器名**，
状态也写到临时目录。检测、连续确认、告警送达、自愈动作**全都真跑**，
但所有 docker 命令都打在一个不存在的容器上 —— 真实 NapCat 毫发无损。

```bash
cd /opt/douyu-live-notify
# 环境变量（含告警通道）平时由 systemd 注入，手动跑要自己加载
for i in 1 2 3; do
  sudo bash -c 'set -a; . /etc/default/douyu-watchdog; set +a; \
    python3 /opt/douyu-live-notify/watchdog.py \
      --container napcat-does-not-exist --state-dir /tmp/wd-verify'
  echo "--- 第 $i 轮 ---"
done
```

**应该逐轮看到**（这是实测行为，不是推测）：

| 轮次 | 输出 | 含义 |
|---|---|---|
| 第 1 轮 | `动作: NapCat 异常第 1 次（满 3 次才动手），本轮先观察`<br>`已告警：NapCat 容器没在运行（送达：webhook(pushplus)）` | 判定 + **告警真的发出去**（**你微信会收到一条**） |
| 第 2 轮 | `[重复告警已抑制] NapCat 容器没在运行` | 同一问题每小时只提醒一次 |
| 第 3 轮 | `动作: 自动重启（容器没在运行）：docker restart napcat-does-not-exist 失败` | **真的执行了自愈命令**，只是打在不存在的容器上所以失败 |

```bash
sudo rm -rf /tmp/wd-verify        # 验完清掉，别留下假的故障计数
```

> 你收到的那条「NapCat 容器没在运行」是合成出来的，**不用管**。
> 它恰好是个好证据：证明这条通道在真出事时会叫你。

⚠️ **两个容易搞错的地方**（我踩过）：

1. **别加 `--dry-run`**。加了之后告警**根本不会发**（代码里 dry-run 只打印
   「本应发送告警」就跳过），你会白等一条永远不来的消息。想完全不动手、也不要
   那条告警，才用它，但要清楚它验不了投递。
2. **`--state-dir /tmp/wd-verify` 不能省**。省了它会往**真实**的状态文件里写一个假的
   连续失败计数，可能让下一轮真体检提前触发一次不必要的重启。

> 为什么值得做：它验证的是**决策链**（到阈值才动手）＋**告警真能送达**，
> 也就是最容易写错、真出事时才发现的那一段。
> 验证 B 只多验一件事：「`docker restart` 这条命令真的能把容器拉起来」。
> 若你的「重启免扫码」已经单独验过，D 基本可以替代 B。

### 验证 D：掉线告警走的是不是独立通道

做验证 B 时留意：那条告警**只能从 webhook/邮件**到得了你（NapCat 已经停了）。
如果你只在 QQ 里看到了告警，说明外部通道没配好 —— 回去看第四节。

### 日常

```bash
python3 watchdog.py --status                  # 一条命令看健康
tail -f /var/log/douyu-watch/watchdog.log     # 实时日志
tail -50 /var/lib/douyu-watchdog/alerts.log   # 告警历史（永不丢的那一份）
cat /var/lib/douyu-watchdog/status.json       # 机器可读的最近一次结果
```

---

## 六、已知边界（它做不到什么）

- **补发不了丢失的通知**，只能告诉你丢了。补的办法见第三节，需要你手动跑一次。
- **救不了需要扫码的掉线**。QQ 侧会话失效（换设备、改密码、被风控）时只能人工扫一次码，
  这是 NapCat 方案的固有边界，不是看门狗的缺陷。
- **看门狗自己也可能挂**，所以才有 `DAILY_OK_AT`（沉默即出事）。
- **只报不修的东西**：斗鱼接口整体挂了（这时 `stuck` 会报出来）、群被封、
  QQ 风控静默丢消息（日志显示成功但收不到）—— 这些都不是本地能解决的。
- **验证 B 会让机器人短暂掉线**，别在主播正在播、你正等通知的时候做。

---

## 七、文件清单

| 文件 | 装到哪 | 作用 |
|---|---|---|
| `watchdog.py` | `/opt/douyu-live-notify/watchdog.py` | 看门狗本体，纯标准库，无第三方依赖 |
| `douyu-watchdog.service` | `/etc/systemd/system/` | 单次体检（由 timer 拉起） |
| `douyu-watchdog.timer` | `/etc/systemd/system/` | 每 2 分钟一次，开机 5 分钟后开始 |
| `watchdog.env.example` | `/etc/default/douyu-watchdog` | 告警通道与阈值配置（600 权限，已存在不覆盖） |
| `douyu-watch.tmpfiles` | `/etc/tmpfiles.d/douyu-watch.conf` | 开机建好 `/var/log/douyu-watch` 与 `/var/lib/douyu-watchdog` |
| — | `/var/lib/douyu-watchdog/` | 运行状态、告警历史（`alerts.log`），0700 |
