# douyu-live-notify

斗鱼直播间开播提醒：主播**真人开播**时，通过 QQ 推一条消息给你。

单个 Python 文件，**只用标准库**，不需要装任何依赖，也不用登录斗鱼账号。

---

## 它想解决什么

斗鱼接口里那个「是否在直播」的字段**不可靠**。

主播下播之后，房间常常会自动转入**轮播**（循环放之前的录播、无人直播）。
这时候接口照样返回「正在直播」。你要是只看这个字段，
就会在主播明明没播的时候，一遍遍收到「开播了」——最后只能把提醒关掉。

这个项目用对照实验找到了更可靠的判据（见下面「两个坑」一节），
默认把轮播排除在「开播」之外。

## 特性

- **零依赖**：只用 Python 标准库。Python 3.9+ 就能跑
- **能识别轮播**：不把「循环放录播」误报成开播
- **防抖**：连续 N 次读到同一状态才认定状态切换，避免接口抖动误报
- **多通道**：控制台 / OneBot（NapCat 等）/ QQ 官方机器人，可以同时启用
- **两种跑法**：常驻进程，或每分钟执行一次（给 cron / 青龙面板 / 云函数用）
- **配置错了会明确报错**：最坑的 `room_id` 填错会给可照做的提示，而不是安静地什么都不做
- **自带看门狗**：`watch.py` 有两个静默失败（掉线不发、停摆不说），
  看门狗每 2 分钟独立体检一次并告警，能自愈的自己动手。有一条**独立于 QQ 的告警通道**，
  所以掉线时也通知得到你 —— 详见 `deploy/WATCHDOG.md`

---

## 快速开始

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
| `python deploy/watchdog.py --recover-notify` | 补一条丢失的开播通知（主播仍在播时用） |
| `python pack_deploy.py` | 打部署包 `deploy.zip`（自动带上 `watch.py` / `selftest.py` / `watchdog.py`，并归一为 LF） |
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

---

## 部署

### 方式 A：本机 / 任何有 Python 的机器

用 cron 每分钟跑一次（`--tick` 每次自己读写状态文件，天然适配无状态调度）：

```cron
* * * * * cd /path/to/douyu-live-notify && /usr/bin/python3 watch.py --tick
```

### 方式 B：服务器 + Docker（推荐）

`deploy/` 目录里有整套东西，Ubuntu 22.04 / 24.04 实测通过：

```
deploy/
├── DEPLOY.md              完整部署手册（先看这个）
├── AGENT_PROMPT.md        想让 AI agent 帮你部署？把这份提示词丢给它
├── RESUME_PROMPT.md       预检判定内存不足、处理完之后接着部署的续跑提示词
├── SCAN_QR_WITHOUT_SSH.md 扫码登录的替代做法（不必开 SSH 隧道）
├── watch.py               主程序（部署包里有副本，源头在仓库根目录）
├── selftest.py            逻辑自检（部署包里有副本，源头在仓库根目录）
├── watchdog.py            看门狗：体检「有没有在跑 / 掉线没有 / 通知丢了没」并自愈
├── WATCHDOG.md            看门狗设计说明 + 「怎么验证它真的会叫」
├── docker-compose.yml     NapCat 容器（端口只绑 127.0.0.1，ACCOUNT 必填）
├── .env.example           WebUI token / 机器人 QQ 号 / 容器内存上限模板
├── config.example.json    配置模板
├── preflight-check.sh     部署前环境预检（只读，含内存判定）
├── setup-docker-mirror.sh 探测可用的 Docker 镜像源
├── mem-report.sh          内存被谁占了（只读，含 OOM 历史）
├── add-swap.sh            加/删 swap（幂等、可撤销，小内存机器用）
├── install-watch.sh       安装 watch.py / watchdog.py 与 systemd 单元
├── douyu-watch.{service,timer}  systemd 每分钟拉起 watch.py --tick
├── douyu-watchdog.{service,timer}  每 2 分钟体检一次，异常时告警 / 自愈
├── watchdog.env.example   告警通道与阈值模板（装到 /etc/default/douyu-watchdog）
└── douyu-watch.tmpfiles   日志与状态目录兜底（装到 /etc/tmpfiles.d/）
```

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

用法：把 `deploy/` 整个目录传到服务器，先跑 `bash preflight-check.sh`
（只读，不改任何东西）看环境，然后照 `DEPLOY.md` 一步步走。
**如果你想让 AI agent 来部署，直接把 `AGENT_PROMPT.md` 里那份提示词丢给它。**
小内存服务器（NapCat 是 Electron 应用，常驻 0.3~0.7GB）要先看预检的内存判定；
判定不足、处理完之后，用 `RESUME_PROMPT.md` 续跑。

### 打部署包（别手打 zip）

```bash
python pack_deploy.py        # 生成 deploy.zip
```

包里必须同时有 `watch.py`、`selftest.py`、`watchdog.py` 和 `install-watch.sh`，
而且这些文件在同一层 —— `install-watch.sh` 是从自己所在目录往上找源文件的，
少一个就会报「找不到」。手动 `zip -r deploy.zip deploy/` 很容易漏掉仓库根目录的两个 .py，
所以这件事交给脚本做，缺文件时它会直接报错、不生成包。它还会把文本文件统一转成 LF
（Windows 上工作区常是 CRLF，带进包里的 shell 脚本到 Linux 上会报 `$'\r': command not found`）。

装完之后 `install-watch.sh` 会打印 `watch.py` / `selftest.py` / `watchdog.py`
的 sha256 前 16 位，以后怀疑「服务器上是不是旧版」，和仓库里的对一下即可。

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

`qr_make.py` 是本项目里**唯一需要额外依赖**的脚本（`pip install qrcode pillow`），
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
| 丢了某条开播通知 | 发送失败只打一行 `[error]`，状态已落盘 → 不重试 | `python deploy/watchdog.py --recover-notify`（主播仍在播时有效） |

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

## 免责声明

- 斗鱼接口数据抓取自公开网页端点，本项目仅供个人学习与自用。
- 若使用 NapCat 等**第三方 QQ 协议端**：这类项目违反 QQ 用户协议，
  请用**小号**，并**务必不要把管理端口暴露到公网**、不要使用默认 token。
  2025 年 9 月曾有人批量扫描此类暴露实例并操纵其发违法信息，
  导致被波及的账号和群聊被**永久封禁**。
- 使用本项目的风险由使用者自行承担。

## License

[MIT](LICENSE)
