# 让 AI agent 帮你部署（提示词模板）

把「---」之间的内容整段复制给你的服务器 agent（Claude Code / Codex / 任何 coding agent）。
它写得自包含，不需要 agent 了解本项目的背景。

**先替换掉这几个占位符**，否则 agent 会照着你没改的模板执行：

| 占位符 | 换成什么 |
|---|---|
| `YOUR_ROOM_ID` | 斗鱼房间的**真实 room_id**（不是地址栏里的靓号，见 `DEPLOY.md` 第 1 节） |
| `YOUR_GROUP_ID` | 接收提醒的 QQ 群号 |
| `YOUR_BOT_QQ` | 你用来发消息的 QQ 小号 |
| `YOUR_ONEBOT_TOKEN` | OneBot 的鉴权 token，自己生成：`openssl rand -hex 16` |
| `YOUR_WEBUI_TOKEN` | NapCat WebUI 的登录密码，同样自己生成 |

> 也可以不填，直接把这份提示词丢给 agent，让它先问你要 —— 提示词第 0 阶段就是这么写的。

---

## 任务

在这台 Linux 服务器上部署「斗鱼开播提醒」：
某个斗鱼主播**真人开播**时，用 QQ 小号往指定群里发一条提醒，长期稳定运行。

方案是 **NapCatQQ（Docker）+ OneBot v11 正向 HTTP** + 一个每分钟跑一次的 `watch.py`。
部署包里已经准备好脚本、compose 文件和配置模板，**你负责照单执行，不要自己发挥**。

---

## 五条硬约束（违反会出真问题）

1. **这台机器上可能还跑着别的服务。不要停、不要改、不要删任何现有的容器、systemd 单元或配置文件。**
2. **绝不要执行 `docker compose down -v`** —— `-v` 会删数据卷，QQ 登录态丢失就得重新扫码。
3. **端口只能绑 `127.0.0.1`**。compose 文件已经写好了，别改成 `0.0.0.0`，也别去云安全组放行 3000 / 3001 / 6099。
4. **不要放松任何安全设置来「让它跑通」** —— 具体见文末「出错时的原则」。
5. **扫码登录只能由人完成**，你做不到。到那一步必须停下来交接（见阶段 4）。

---

## 阶段 0 · 先确认这几个值

如果用户没直接给你，先问清楚（**问完再动手**）：

1. **斗鱼房间的真实 room_id** —— 注意不是浏览器地址栏里的靓号，两者经常不一样。
   用户只给了靓号的话，让他打开直播间页面 → 查看网页源代码 → 搜 `room_id`。
   或者部署完用 `python3 watch.py --probe <号>` 试，能打印出主播名就说明对了。
2. **接收提醒的 QQ 群号**
3. **用来发消息的 QQ 小号** —— 提醒用户用小号，别用主号（原因见阶段 4 末尾）

两个 token（OneBot 与 WebUI）**你自己生成**，别用默认值：

```bash
openssl rand -hex 16
```

---

## 阶段 1 · 就位 + 预检（只读，不改任何东西）

把 `deploy/` 目录整个传到服务器，然后：

```bash
# 传的是整个目录：直接进
cd deploy
# 传的是压缩包：
#   unzip -o deploy.zip && cd deploy

chmod +x *.sh
bash preflight-check.sh
```

`preflight-check.sh` 是**只读**的，不写文件、不重启服务。它会查内存、端口占用、
Docker 容器及自启策略、防火墙、目标目录是否被占。

**看完输出后按这三条决定：**

- 提示 `/opt/napcat` 或 `/opt/douyu-live-notify` **已被占用** → **停下**报告，别覆盖别人的目录
- 内存判定为「**不足**」→ 走下面「内存不足怎么处理」，**不要硬上**
- 判定「充足」「勉强」，且目标目录干净 → 继续

**无论哪种情况，都记下输出里 `restart=` 不是 `always` / `unless-stopped` 的容器名** ——
阶段 2 会重启 Docker，之后要核对它们有没有起来。

### 内存不足怎么处理（这是能卡住整个部署的硬门槛）

先纠正一个流传很广的错误数字：NapCat 官网首页写的「内存低至 50~100MB」，
指的是**它自身框架层**的占用，**不含**它承载的 QQ Node 进程。
社区实测「500MB~1GB」又偏高。**按 300~700MB 做容量规划。**

先查清内存到底被谁吃了（只读，不改任何东西）：

```bash
bash mem-report.sh
```

**把这个报告的全文发回给用户，然后停下等指示。不要自己决定加内存还是换方案。**

报告里要重点转达两件事：

1. 第 2 节 —— 谁占了最多内存
2. 第 6 节 —— **历史上有没有被 OOM killer 杀过**

因为接下来的走法取决于这两点，而这属于**产品取舍**，不是执行细节：

| 情况 | 通常的走法 |
|---|---|
| 占用大头是可回收缓存 | 直接继续部署 |
| 可用 400~800MB，且无 OOM 历史 | 路线 A 两步：**A-1 加 swap** + **A-2 收紧容器上限** |
| 可用 < 400MB 且有 OOM 历史，**或用户自己的网页就是最大进程** | 多半得换掉推送通道（不再用 NapCat），必须用户确认 |

路线 A 的两步（**必须都做，只做一样等于没解决**）：

```bash
# A-1  加 swap（加的是「不被杀」的余量）。幂等，可撤销 --remove。
sudo bash add-swap.sh          # 默认 2G

# A-2  收紧容器上限（改的是「出事时谁死」）
#      值 = 宿主机可用内存 × 0.8，**不含 swap**
#      可用 800MB → 700m
sed -i 's/^NAPCAT_MEM_LIMIT=.*/NAPCAT_MEM_LIMIT=700m/' /opt/napcat/.env
cd /opt/napcat && docker compose up -d
```

> ⚠️ 上限若高于宿主机可用内存，这道安全网就完全失效了。**别把 swap 算进上限里。**

**在用户明确说「继续」之前，不要执行 `add-swap.sh`，也不要往下走阶段 2。**
（若用户已经预先批准走路线 A，则直接执行 A-1 + A-2，然后继续阶段 2。）

> 为什么这里要停：`add-swap.sh` 会改动 `/etc/fstab` 和内核参数（`vm.swappiness`），
> 属于系统性变更；而「是否接受在 1.5GB 机器上跑一个 Electron 应用」是产品决策。
> 还有一点反直觉但重要：**全局 OOM killer 是按内存占用从大到小挑牺牲品的** ——
> 如果用户自己的网页比 NapCat 占得多，被杀的会是**网页**，NapCat 反而活下来。
> 这种事必须让用户知道后再决定。

---

## 阶段 2 · 配 Docker 镜像源

国内不少服务器直连 Docker Hub 拉不动镜像，所以先配镜像源：

```bash
sudo bash setup-docker-mirror.sh
```

**这一步会重启 Docker**（`registry-mirrors` 不支持热重载，只能重启）。
脚本已内置保护：重启前记录运行中的容器清单 → 重启 → 逐个核对 → 掉队的自动 `docker start` 拉起。

检查输出：

- 出现「镜像已就绪」→ 继续
- 出现「NapCat 镜像拉取失败」或打印了 Plan B → **停下**，把完整输出发回给用户

**另外**：对照阶段 1 记下的容器名单，确认它们重启后都还在运行（`docker ps`）。
脚本若报「★拉起失败」，立刻停下报告 —— 那说明某个服务真的掉了。

---

## 阶段 3 · 起 NapCat 容器

```bash
mkdir -p /opt/napcat
cp docker-compose.yml /opt/napcat/
# 用阶段 0 生成的 token 替换掉 .env.example 里的占位符
sed "s/YOUR_WEBUI_TOKEN/<阶段0生成的token>/" .env.example > /opt/napcat/.env
cd /opt/napcat
docker compose up -d
docker compose ps
```

**必须验证端口只绑回环：**

```bash
ss -lntp | grep -E ':(3000|6099)'
```

**期望**：`127.0.0.1:3000` 和 `127.0.0.1:6099`。
**如果看到 `0.0.0.0:3000` 或 `*:3000`，立刻停下改回来**，别继续。

**再验证内存安全网生效了**（compose 里那段 `deploy.resources.limits.memory`）：

```bash
docker inspect napcat --format 'Memory={{.HostConfig.Memory}}'
free -m | awk '/^Mem:/{print "available: "$7" MB"}'
```

- 返回字节数与 `.env` 里的 `NAPCAT_MEM_LIMIT` 一致（`700m` → `734003200`）= 生效
- 返回 `0` = 没生效 → 把 compose 里那段 `deploy:` 换成 `mem_limit: 700m`，
  再 `docker compose up -d`，然后重新验证
- **返回值比「可用内存」大** = 安全网白设了 → 把 `NAPCAT_MEM_LIMIT` 收到「可用内存 × 0.8」

> 这道上限的作用：内存失控时内核只在**这个容器内部**杀进程，NapCat 自己重启
> （`restart: always`），同一台机器上其他服务不受牵连。小内存机器上它很重要。
> **关键是上限必须 ≤ 宿主机可用内存** —— 高于可用内存就等于没设。
> 另外记录一下它实际吃多少，回报时要带上：`docker stats napcat --no-stream`

---

## 阶段 4 · 扫码登录（人工步骤，你必须停下）

**扫码只能由人完成。但你的任务不是「让用户开隧道」，而是「把二维码交到用户手上」。**

NapCat 会把二维码打进容器日志，**不需要 SSH 隧道，也不需要让 WebUI 接触网络**。

### 4.1 取二维码

```bash
docker logs napcat 2>&1 | grep -nE '二维码|qrcode|txz\.qq\.com' | tail -20
```

找到类似这样一行（`k=` 和 `f=` 的值都要完整）：

    [warn] 二维码解码URL: https://txz.qq.com/p?k=xxxxxxxxxxxxxxxx&f=xxxxxxxxxxxxx

### 4.2 把这段交给用户，然后**停止执行，等确认**

> 请在你自己的电脑上执行（把 URL 换成上面那行原文）：
>
>     python qr_make.py --url "https://txz.qq.com/p?k=xxxx&f=xxxx"
>
> 它会生成 `qr.png`，用**手机 QQ 扫这个图片** —— 扫的是你准备用来发消息的那个小号。
>
> 注意：**二维码有效期只有约 1~2 分钟**，拿到就马上扫，别拖。
> 扫完告诉我，我再继续下一步。

（如果用户没有 `qr_make.py`，也可以用任意在线二维码工具把那个 URL 转成图片，
但要提醒他：**这个 URL 是一次性登录凭据，别贴到公开地方**。）

### 4.3 二维码过期 / 日志里没有 URL

过期很正常，1~2 分钟就失效。重新生成一次：

```bash
docker restart napcat
sleep 20
docker logs --since 30s napcat 2>&1 | grep -nE '二维码|txz\.qq\.com'
```

此时**还没登录**，重启不丢任何东西。
（但**永远不要** `docker compose down -v` —— `-v` 会删数据卷。）

日志里始终只有文件、没有 URL 时，改为取图片：

```bash
docker exec napcat sh -c 'base64 -w0 /app/napcat/cache/qrcode.png'
```

把那一长串 base64 **完整**发给用户（可能几 KB，**不要省略、不要加 "..."**），
让他用 `python qr_make.py --b64str "<粘贴>"` 还原成图片。

完整说明见同目录的 `SCAN_QR_WITHOUT_SSH.md`。

### 4.4 不要为了扫码去动网络

- ❌ **不要**把 6099 / 3000 / 3001 改绑 `0.0.0.0`
- ❌ **不要**去云安全组放行这几个端口，**不要**开 ufw
- ❌ **不要**改任何 token
- 用户如果抱怨「SSH 隧道连不上、22 端口超时」：告诉他**这一步不需要隧道**，
  按 4.1~4.3 走即可。隧道只是以后看 WebUI 方便，**不是部署前置条件**

### 4.5 确认登录成功

等用户回复「登录好了」之后：

```bash
docker logs napcat 2>&1 | tail -20
```

看到登录成功即可继续。没成功就先排查，别硬往下走。

> 为什么坚持用小号：NapCat 属第三方协议端，违反 QQ 用户协议。
> 2025 年 9 月有人批量扫描暴露在公网、且用默认 token 的 NapCat 实例，
> 操纵它们在大量 QQ 群发违法信息，**导致被波及的账号和群聊被永久封禁**。

---

## 阶段 5 · 配 OneBot HTTP 服务

配置文件路径：`/opt/napcat/napcat/config/onebot11_<机器人QQ号>.json`
（首次启动 NapCat 会生成一个默认的，**直接覆盖成下面这份**）

```json
{
  "network": {
    "httpServers": [
      {
        "name": "douyuWatch",
        "enable": true,
        "host": "0.0.0.0",
        "port": 3000,
        "messagePostFormat": "string",
        "token": "<阶段0生成的 OneBot token>",
        "debug": false,
        "enableCors": true,
        "enableWebsocket": false
      }
    ],
    "httpClients": [],
    "websocketServers": [],
    "websocketClients": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false
}
```

> ⚠️ **反直觉但必须照做**：这里的 `host` 写 `0.0.0.0`，
> 而阶段 3 的宿主机端口映射写的是 `127.0.0.1`。两者**不矛盾** ——
> 容器内的 `0.0.0.0` 表示监听容器所有网卡，Docker 才能把流量转发进去；
> 容器内若填 `127.0.0.1`，转发会打到容器内的 lo 上，报 `Connection refused`。

改完重启并等几秒：

```bash
docker restart napcat && sleep 8
docker logs napcat 2>&1 | tail -20
```

**验证 OneBot 通了**（token 从配置里读，别手抄错）：

```bash
T=$(python3 -c "import json,glob;print(json.load(open(glob.glob('/opt/napcat/napcat/config/onebot11_*.json')[0]))['network']['httpServers'][0]['token'])")

curl -s -H "Authorization: Bearer $T" http://127.0.0.1:3000/get_login_info
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:3000/get_group_list
```

**期望**：
- 第 1 条返回 `"status":"ok"`，`user_id` 是那个小号的 QQ 号
- 第 2 条里**能找到目标群号**

**如果第 2 条里没有目标群号** → 停下报告。这几乎只有一个原因：
小号还没加进那个群。这是本步骤最常见的失败。

---

## 阶段 6 · 部署 watch.py

回到解压出来的 `deploy/` 目录：

```bash
sudo bash install-watch.sh
```

它会：把 `watch.py` / `selftest.py` 放进 `/opt/douyu-live-notify/`、装好 systemd 单元，
但**故意不启动定时器** —— 等验证通过再开。

然后填 `config.json`：

```bash
cd /opt/douyu-live-notify
cp config.example.json config.json     # 若已存在就别覆盖
nano config.json                       # 填 room_id、群号、OneBot token
chmod 600 config.json
```

---

## 阶段 7 · 四道验证（按顺序，一道都不能跳）

```bash
cd /opt/douyu-live-notify

# ① 逻辑自检（不联网、不发消息）
python3 selftest.py && tail -3 selftest_result.txt
```

**期望**：`结果：24 项通过，0 项失败（共 24 项）`
（关键是 **`0 项失败`**；有失败时这条命令会返回非 0 退出码）

```bash
# ② 斗鱼接口体检
python3 watch.py --probe YOUR_ROOM_ID
```

**期望**：`betard` 和 `legacy` 两组都成功，能看到主播名、房间标题和 `videoLoop` 字段。
**如果两个接口都失败** → room_id 多半是靓号或不存在，回阶段 0 重新确认。

```bash
# ③ 当前判定（不入库、不发通知）
python3 watch.py --once
```

**期望**：`判定：未开播` 或 `判定：直播中`，两者都可能正确。

⚠️ **如果房间正在放轮播（`show_status=1` 但 `videoLoop=1`），这里会显示「未开播」——
这是正确结果，不是故障。** 不要为了让这里显示「直播中」去改 `treat_loop_as_live`。

```bash
# ④ 真发一条测试消息到群里
python3 watch.py --test-notify
```

**期望**：控制台提示发送成功，**并且目标 QQ 群里真的出现一条测试消息**。

**必须由用户亲眼在群里确认收到，这一关才算过。** 仅仅控制台显示成功不算。

> 任何一关不过 → **停下**，把**完整原始输出**（含执行的命令）发回给用户，
> 不要自己改代码或配置去凑。

---

## 阶段 8 · 开定时器

只有 1~7 全绿才执行：

```bash
systemctl enable --now douyu-watch.timer
systemctl list-timers douyu-watch.timer
```

看运行日志：

```bash
journalctl -u douyu-watch -n 20 --no-pager
tail -5 /var/log/douyu-watch/tick.log
```

心跳长这样，属正常：

```
[10:31:38] #2 LOOP(轮播) | 某直播间的标题 | 热度 - | loop=1 | via betard
```

`LOOP(轮播)` = 房间在放录播，**不算开播**。`LIVE` = 真人开播，`offline` = 房间关闭。

> **首次运行不发通知，只记录当前状态**（防止一启动就误报）。
> 反过来也要知道：如果启动时主播正好在播，这次开播不会提醒。

---

## 出错时的原则

- **不要反复重试同一条失败的命令**
- **不要为了「让它通过」而放松安全设置**：放开端口、清空 token、把 `host` 改回 `127.0.0.1`、
  改 `treat_loop_as_live` —— 这些都不能做
- **不要删除或重建任何已有的容器 / systemd 服务**（这台机器上可能还有别的服务在跑）
- 失败就把**命令 + 完整原始输出**发回来，越原始越好

---

## 最后回报这六项

1. 阶段 1 的预检输出（全文，尤其**内存**和**容器 `restart=` 策略**）
2. 内存不足时：`mem-report.sh` 的**完整输出**（重点第 2、6 节）
3. 阶段 3 的 `ss -lntp | grep 3000` 结果 —— 确认只绑了 `127.0.0.1`；
   以及 `docker inspect napcat --format 'Memory={{.HostConfig.Memory}}'` 的返回值
4. 阶段 7 第 ④ 关：**群里是否真的收到了测试消息**（需用户确认）
5. 阶段 8 的 `systemctl list-timers douyu-watch.timer` 输出，加上 `docker stats napcat --no-stream`
6. 任何**跳过、失败或你不确定**的地方 —— 直接说，不要掩盖
