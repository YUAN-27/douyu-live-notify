# 部署运行手册（Linux 服务器 · Docker 版）

> **适用**：一台能上网的 Linux 服务器（Ubuntu 22.04 / 24.04 实测通过）。
> **前置**：Python 3.9+、Docker、Docker Compose。
> **目标**：某个斗鱼主播**真人开播**时，用 QQ 小号往指定群发一条提醒，长期稳定运行。

---

## 0. 部署前先做两件事

### 0.1 跑一次环境预检（只读，不改任何东西）

```bash
bash preflight-check.sh
```

它会查：内存、端口占用、已有 Docker 容器及自启策略、防火墙、目标目录是否被占。

**重点看三项：**

- **内存判定为「不足」** → 这是个**能卡住整个部署的硬门槛**，不要硬上。走 0.3 节。
- **`/opt/napcat` 或 `/opt/douyu-live-notify` 已被占用** → 换个目录名，别覆盖别人的东西。
- **容器自启策略不是 `always` / `unless-stopped`** → 记下容器名。第 2 步要重启 Docker，
  这类容器不会自己起来（脚本会自动拉起，但你要留意它的报告）。

> **如果服务器上还跑着别的服务**（网站、数据库等），上面这一项尤其重要：
> 本次部署唯一会「外溢」到其他服务的动作就是**重启 Docker**，原因和应对见第 2 步。

### 0.2 把占位符换成你自己的值

部署包里凡出现下面这些占位符的地方，都要替换：

| 占位符 | 换成什么 | 填在哪 |
|---|---|---|
| `YOUR_ROOM_ID` | 斗鱼房间的**真实 room_id**（不是靓号，见第 1 节） | `config.json` → `room_id` |
| `YOUR_GROUP_ID` | 接收提醒的 QQ 群号 | `config.json` → `onebot.target_id` |
| `YOUR_BOT_QQ` | 你用来发消息的 QQ 小号 | `.env` → `ACCOUNT`（**必填**，漏填容器直接拒绝启动） |
| `YOUR_ONEBOT_TOKEN` | OneBot 的鉴权 token，自己生成一个随机串 | `config.json` → `onebot.token` |
| `YOUR_WEBUI_TOKEN` | NapCat WebUI 的登录密码 | `.env` → `WEBUI_TOKEN` |

生成随机 token：

```bash
openssl rand -hex 16
```

`config.json` 和 `.env` 的模板分别是 `config.example.json`、`.env.example`，
复制一份再改（脚本也会自动处理）。

---

## 0.3 内存判定「不足」怎么办（能卡住整个部署的硬门槛）

先把流传的两个极端数字纠正掉，别照抄任何一个：

| 说法 | 出处 | 可不可信 |
|---|---|---|
| 「内存低至 50~100MB」 | NapCat 官方首页 | ❌ 那是 NapCat **自身框架层**的占用，**不含**它承载的 QQ NT 进程 |
| 「跑起来 500MB~1GB」 | 社区实测博客（含同机 NoneBot2） | ⚠️ 偏高，但比 50MB 接近现实 |
| **300~700MB** | 综合判断：Docker 镜像本身 570MB，内含完整无头 QQ NT | ✅ **按这个做容量规划** |

**结论：可用内存低于 ~800MB 就是真有风险，不是保守估计。**

但别只看「可用」这一个数 —— **swap 也算，只是要打折**：

```
NapCat 预算 = 可用内存 + swap可用量 × 0.5  （需要 0.3~0.7 GB）
```

swap 只算半个，因为它慢一个数量级：能兜住不被杀，但高峰时 NapCat 会明显变慢。
`preflight-check.sh` 会直接把这个预算算给你。

> 加了 swap 之后**别急着把容器上限也放宽** —— 上限的取法是
> 「可用内存 × 0.8」，**不含 swap**。原因见下面第二节。

### 第一步：查清是谁占了内存

```bash
bash mem-report.sh
```

只读，不写任何东西。它会给出：谁在占内存、有没有 swap、**历史上有没有被 OOM 杀过**、
容器有没有内存上限、以及 PSI 内存压力值。**重点看它第 2 节和第 6 节。**

### 第二步：按结果选一条路

**路线 A · 加 swap（最省事，多数情况够用）**

```bash
sudo bash add-swap.sh          # 默认 2G
```

脚本会创建 swapfile、**验证真的生效**、写进 `/etc/fstab`（重启后仍在），并显式设置
`vm.swappiness`。幂等，重复跑安全。撤销：`sudo bash add-swap.sh --remove`。

> **为什么 swappiness 用 60，而不是网上到处传的 10？**
> swappiness 越低，内核越**不愿意**换出匿名页（= 进程真正占着的内存），转而去丢文件缓存。
> 那些教程让你设成 10，针对的是「数据库要最低延迟」的场景。
> 你这台机器正相反：最大的一块匿名内存是**长期空闲的 NapCat**（只在开播时醒一下），
> 而真正需要低延迟的是**同机的个人网页**。所以我们要的恰恰是「把空闲的 NapCat 换出去，
> 把物理内存让给网页」。方向搞反会更糟。

**路线 B · 换掉推送通道，让内存问题直接消失**

如果接收提醒的群**不一定非得是 QQ**，最干净的解法是根本不在本机跑 NapCat：
企业微信 / 钉钉 / 飞书 群机器人、Telegram Bot、Bark、ntfy —— 这些全是**纯 HTTP webhook**，
内存占用 ≈ 0，本质上就是一条 `curl`。`watch.py` 的通知器是可插拔的（`channels` 配置项），
加一个通道约 20 行代码，能彻底绕开小内存限制。**代价**：收消息的群得换个地方。

> 顺带提一句另一个方向：更「纯协议」的 QQ 实现如 Lagrange.Core（C#，不依赖 Electron）
> 内存确实更低，但它需要 .NET 10 运行时 + 签名服务，且刚经历 V1 退役、正在做协议迁移，
> 运维复杂度反而更高。本项目的定位是「部署简单」，所以不默认推荐 —— 但值得知道它存在。

**路线 C · 先把占用压下来再决定**

如果 `mem-report.sh` 第 2 节显示占用大头是**可以调的东西**（Node 应用没设 heap 上限、
跑着用不上的数据库、日志缓冲过大……），把它压下来再走路线 A，效果最好。

### 决策速查

| mem-report 的结果 | 建议 |
|---|---|
| 可用 ≥ 900MB，或大头是**可回收缓存** | 直接部署，不必加 swap；上限按「可用 × 0.8」设 |
| 可用 400~800MB，且**没有** OOM 历史 | 路线 A 两步都做（A-1 加 swap + A-2 上限收到 ~700m） |
| 可用 < 400MB，且**已有** OOM 历史 | 先路线 C 查大头；压不下来就走路线 B |
| **个人网页本身就是这台机器 RSS 最大的进程** | **务必走路线 B** —— 硬塞 NapCat 很可能让 OOM killer 挑中你的网页 |

> 最后一种情况值得单独强调，因为它反直觉：全局 OOM killer 是**按内存占用从大到小挑牺牲品**的。
> 如果网页占得比 NapCat 多，**被杀的会是网页**，NapCat 反而活下来。
> compose 文件里那道 `deploy.resources.limits.memory` 就是为了防这个 —— 见第 4 节。

---

## 1. 先读：三个前提

1. **`room_id` 要填「真实号」，不是地址栏里的「靓号」。**
   斗鱼房间有两个号，而且经常不一样。举个真实例子：浏览器打开 `https://www.douyu.com/6657`
   能正常看到直播间，但接口能查到的 room_id 是 `6979222`；
   直接查 `betard/6657` 返回的是「房间已被关闭」的 HTML 报错页。
   更坑的是，斗鱼上可能存在另一个真号恰好是 `6657` 的**别人的房间** ——
   填错就会安静地监控到不相干的直播间。

   **怎么找真实号**：打开直播间页面 → 查看网页源代码 → 搜 `room_id`，取那个数字。
   也可以试：`python3 watch.py --probe <你猜的号>`，能打印出主播名、房间标题就说明对了。

2. **`show_status=1` 不等于真人在播。**
   主播下播后房间常常转入**轮播**（循环放录播），此时 `show_status` 仍是 1、`videoLoop` 变 1。
   `treat_loop_as_live` 默认 `false` —— 轮播不算开播。
   依据（实测对照）：斗鱼「正在直播」列表里抽样的在播房间 `videoLoop` **全为 0**；
   而轮播中的房间 `videoLoop=1` 且**完全不出现在直播列表里**。

3. **NapCat 是第三方协议端，违反 QQ 用户协议。**
   用**小号**，不要用主号。**绝不要**把 3000 / 3001 / 6099 暴露到公网。

---

## 2. 安全红线（出事是永久封号，必须照做）

> 背景：2025 年 9 月，攻击者批量扫描**暴露在公网的 NapCat WebUI**（默认端口 + 默认 token），
> 操纵其 API 在大量 QQ 群发违法信息，**导致被波及的账号和群聊被永久封禁**。

| 必须 | 做法 |
|---|---|
| 端口只绑回环 | compose 里写 `127.0.0.1:3000:3000` / `127.0.0.1:6099:6099`，**绝不写 `0.0.0.0` 或裸 `3000:3000`** |
| token 必须非空 | `WEBUI_TOKEN` 和 OneBot `token` 都已经生成好，别清空 |
| 云安全组**不要**放行 | 3000 / 3001 / 6099 三条规则全部不开 |
| 用**小号** | 已用 `YOUR_BOT_QQ` |
| 看 WebUI 走 SSH 隧道 | `ssh -N -L 6099:127.0.0.1:6099 root@服务器IP`，本地浏览器开 `http://127.0.0.1:6099/webui` |

**一个反直觉的坑（照抄就不会错）**：
- 宿主机端口映射写 `127.0.0.1:3000:3000`（对外只绑回环）✅
- 但 **NapCat 容器内部**的 OneBot HTTP 服务，`host` 必须填 **`0.0.0.0`**
- 容器内若填 `127.0.0.1`，Docker 端口转发**连不进去**，watch.py 报 `Connection refused`

看似矛盾，实际是对的：Docker 转发打到容器网卡，不是容器内的 lo。

---

## 2.5 与服务器上已有的服务共存

> 如果这台服务器上还跑着**别的服务**（网站、数据库、其他容器），本节是给你看的。
> 裸机部署可以跳过，但建议还是扫一眼「Docker 会被重启」那一段。

### 先说不会影响到的东西

| 本次部署做的事 | 会不会影响其他服务 |
|---|---|
| 用端口 3000 / 6099，且都只绑 `127.0.0.1` | 不影响。网站通常占 80/443；这两个端口由预检确认空闲 |
| 新建 `/opt/napcat`、`/opt/douyu-live-notify` | 不影响。全新目录，不碰任何现有路径 |
| 装 `douyu-watch.service` / `.timer` | 不影响。只新增单元，不改动、不停止其他 systemd 服务 |
| 往 `/etc/docker/daemon.json` 加 `registry-mirrors` | 不影响。脚本用 python 合并写入，**其他配置项不会被覆盖**，且写前先备份 |
| 云安全组不放行任何新端口 | 不新增任何对公网的暴露面 |
| `watch.py` 每分钟发 1 个 HTTP 请求 | 不吃 CPU、不吃内存，可忽略 |

### 唯一会外溢的动作：Docker 会被重启

`setup-docker-mirror.sh` 里有一步 `systemctl restart docker`。

**为什么必须重启**：`registry-mirrors` 这个配置项**不支持热重载**。
`systemctl reload docker` 改不动它，只能重启。

**后果**：**机器上所有正在运行的容器会短暂中断**（通常几秒到几十秒）。
尤其注意 —— **没配 `restart: always` / `unless-stopped` 的容器，重启后不会自己起来**。
如果你的网站是用 Docker 部署的、又没配自启策略，**它就会就此下线**。

**脚本已经加了保护**，会自动完成这一套：

```
重启前记录运行中的容器清单
   → 重启 Docker
   → 逐个核对谁没自己起来
   → 掉队的自动 docker start 拉起
   → 报告「已拉起」或「★拉起失败，需要手动查」
```

所以正常情况不需要你额外操作。但有两点要知道：

- **挑个低峰期做。** 网站正在服务用户时，那几秒中断会被感知到。
- **如果脚本最后报了 `★拉起失败`，别忽略** —— 那说明某个服务真的掉了，先查
  `docker logs <容器名>`。**回滚 daemon.json**：
  ```bash
  cp -a /etc/docker/daemon.json.bak.* /etc/docker/daemon.json
  systemctl restart docker
  ```

> 如果你的网站**不是 Docker 部署的**（直接跑 nginx 二进制 / systemd 服务），
> 那这次重启 Docker 对它**完全没有影响**。

### 附：如果脚本报语法错

在 Windows 上编辑过的 `.sh` 可能带 CRLF 行尾，Linux 执行会报 `$'\r': command not found`。
遇到就跑一次：

```bash
sed -i 's/\r$//' *.sh
```

---

## 3. 第一步：配 Docker 镜像源（必做，否则拉不动镜像）

把部署包里的文件传到服务器同一个目录，然后：

```bash
sudo bash setup-docker-mirror.sh
```

这个脚本会：
1. 探测 11 个候选镜像源（强制 IPv4，超时 6 秒），只留下当前实测活着的
2. 合并写入 `/etc/docker/daemon.json`（已有的先备份，其他配置项不覆盖）
3. 重启 Docker
4. 先拉 `hello-world` 试水，**再拉 `mlikiowa/napcat-docker:latest`**（约 1GB，耐心等）

**期望**：最后输出「镜像已就绪」。
**如果所有源都不可用**，脚本会打印 Plan B（改用 NapCat 的 Shell 一键安装脚本，走国内 CDN），把它给的输出发我。

> 为什么先做这步：这台服务器实测直连 Docker Hub 不通，不配镜像源的话第 4 步必定失败。

---

## 4. 第二步：起 NapCat 容器

```bash
mkdir -p /opt/napcat && cd /opt/napcat
```

把部署包里的 **`docker-compose.yml`** 和 **`.env`** 放到 `/opt/napcat/`（`.env` 就是同名文件，注意别被改名）。

`.env` 里的 **`ACCOUNT` 必须填成机器人 QQ 号**，不能留空 —— 镜像的 entrypoint
靠它给 QQ 传 `-q` 走快速登录，不填就会每次重启退回「等你扫码」：

```bash
grep '^ACCOUNT=' /opt/napcat/.env      # 必须是你的机器人 QQ 号，不能是空
```

```bash
cd /opt/napcat
docker compose up -d
docker compose ps
docker compose logs --tail=50
```

**确认 ACCOUNT 真的传进容器了**（填了没生效等于没填）：

```bash
docker inspect napcat --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ACCOUNT='
```

**检查端口绑定**：

```bash
ss -lntp | grep -E '3000|6099'
```

**必须是** `127.0.0.1:3000` 和 `127.0.0.1:6099`。
如果看到 `0.0.0.0:3000` 或 `*:3000`，说明端口发布写错了，**立刻停下改 compose 文件**。

**顺手验证内存安全网真的生效了**（compose 里那段 `deploy.resources.limits.memory`）：

```bash
docker inspect napcat --format 'Memory={{.HostConfig.Memory}}'
# 期望等于 .env 里 NAPCAT_MEM_LIMIT 的字节数：700m → 734003200
# 顺便确认它 ≤ 宿主机当前可用内存：free -m | awk '/^Mem:/{print $7" MB available"}'
```

- 返回的字节数与 `NAPCAT_MEM_LIMIT` **一致** = **生效**
- 返回 `0` = **没生效**（多半是 compose 版本旧）。把 compose 里那段 `deploy:` 换成
  旧写法 `mem_limit: 700m`，再 `docker compose up -d`
- 返回的数**比可用内存还大** = 安全网白设了，把 `NAPCAT_MEM_LIMIT` 收到「可用内存 × 0.8」

> 这道上限的作用在第 0.3 节讲过：内存失控时，内核只在**容器内部**杀进程，
> NapCat 自己重启，同一台机器上的网页不受牵连。小内存机器上它是刚需。
> 机器内存 ≥4GB 的话，把那段删掉即可。

**观察它实际吃多少**（部署当天看一次，之后偶尔看）：

```bash
docker stats napcat --no-stream
```

---

## 5. 第三步：扫码登录 QQ

**不需要 SSH 隧道。** NapCat 会把二维码打进容器日志，取出来在本地生成图片扫即可。
（想走 WebUI 也行，见本节末尾的方式 B。）

### 方式 A · 从日志取二维码（推荐，依赖最少）

```bash
docker logs napcat 2>&1 | grep -nE '二维码|qrcode|txz\.qq\.com' | tail -20
```

会看到类似这样一行：

```
[warn] 二维码解码URL: https://txz.qq.com/p?k=xxxxxxxxxxxxxxxx&f=xxxxxxxxxxxxx
```

把 **URL 原文**复制到**你自己的电脑**上（`k=` / `f=` 的值都要完整，别截断），生成二维码图片：

> `qr_make.py` 在**仓库根目录**，不在本部署包里 —— 它跑在你自己的机器上，不用传到服务器。

```bash
python qr_make.py --url "https://txz.qq.com/p?k=xxxx&f=xxxx"
# 得到 qr.png → 用**小号 YOUR_BOT_QQ** 的手机 QQ 扫它
```

> ⏱ 二维码有效期只有约 1~2 分钟，**拿到就马上扫**。过期就重新生成一次：
> `docker restart napcat && sleep 20`，再取一次日志（此时还没登录，重启不丢任何东西）。
> ⚠️ 这个 URL 本质是**一次性登录凭据**，别贴到公开群或论坛。

**日志里没有 URL** 时，改为取图片文件（路径可能是 `/app/napcat/cache/qrcode.png`）：

```bash
docker exec napcat sh -c 'base64 -w0 /app/napcat/cache/qrcode.png'   # 输出很长，要完整复制
python qr_make.py --b64str "<把上面那一长串粘进来>"
```

完整说明、其他取图方式与排错见 **`SCAN_QR_WITHOUT_SSH.md`**。

### 方式 B · SSH 隧道 + WebUI（可选，以后看配置方便些）

1. 本地电脑开 SSH 隧道（另开一个终端，让它挂着）：
   ```bash
   ssh -N -L 6099:127.0.0.1:6099 root@服务器IP
   ```
2. 本地浏览器打开 `http://127.0.0.1:6099/webui`
3. 用 WebUI token `YOUR_WEBUI_TOKEN` 登录
4. 进入「登录」页面 → 用**小号 `YOUR_BOT_QQ`** 的手机 QQ 扫码
5. 日志出现「登录成功」即完成

> **22 端口连不上时不要卡在这里**，直接走方式 A。
> 这个隧道是可选的便利，**不是部署的前置条件** ——
> 扫码、配 OneBot、跑测试都不需要它。排查清单见 `SCAN_QR_WITHOUT_SSH.md` 附录。

### 确认登录成功

```bash
docker logs napcat 2>&1 | tail -20
```

> 登录态持久化在 `/opt/napcat/ntqq`，加上 `.env` 里的 `ACCOUNT`，
> **容器重启会自动快速登录，不用重新扫码**。部署完务必实测一次：
> `docker restart napcat` → 等 30~60 秒 → `get_login_info` 返回你的 QQ 号，
> 且日志里**没有**新的二维码。这一步是在你在场、能扫码的时候做的，
> 别等到主播真开播那晚才发现登录态恢复不了。
>
> 自动登录救不了两种情况（见第 12 节的边界说明）：**手机端登录同一个号**把服务器顶下线、
> 或 QQ 侧会话被作废（换设备 / 改密）。这两种都要重新扫码。

---

## 6. 第四步：配 OneBot HTTP 服务

### 推荐：用 WebUI（不容易写错 JSON）

WebUI → **网络配置** → 新建 **HTTP 服务端 / OneBot 11 HTTP Server**：

| 字段 | 值 |
|---|---|
| 名称 | `douyuWatch` |
| 启用 | ✅ 开 |
| Host | **`0.0.0.0`** ⚠️ 必须，填 127.0.0.1 会连不上 |
| Port | `3000` |
| Token | `YOUR_ONEBOT_TOKEN` |
| 消息格式 | `string` |
| 调试 | 关 |

保存后日志应出现 `HTTP服务: 0.0.0.0:3000 已启动`。

### 或者直接写配置文件

宿主机路径：`/opt/napcat/napcat/config/onebot11_YOUR_BOT_QQ.json`

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
        "token": "YOUR_ONEBOT_TOKEN",
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

改完 `docker restart napcat`。

### 验证 OneBot 通了

```bash
T=YOUR_ONEBOT_TOKEN

# 1) 通道是否通 + 机器人身份
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:3000/get_login_info

# 2) 机器人加了哪些群 —— 必须能看到 YOUR_GROUP_ID
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:3000/get_group_list
```

**期望**：第 1 条返回 `"status":"ok"` 且 `user_id` 是 `YOUR_BOT_QQ`；第 2 条里能找到 `YOUR_GROUP_ID`。

**如果第 2 条里没有 `YOUR_GROUP_ID`**，先用小号 `YOUR_BOT_QQ` 加进那个群 —— 这是这一步最常见的失败原因。
另外确认群里没开「禁止机器人发言」之类的限制。

| 报错 | 原因 | 解法 |
|---|---|---|
| `403 Forbidden` | token 不匹配 | 核对两端 token 是否为 `YOUR_ONEBOT_TOKEN` |
| `Connection refused` | 容器内 host 填了 `127.0.0.1` | 改成 `0.0.0.0` |
| `502 Bad Gateway` | 有全局代理劫持回环（本机体检显示无代理，理论上不会） | `env \| grep -i proxy`；curl 加 `--noproxy '*'` |

---

## 7. 第五步：部署 watch.py

把 `deploy.zip` 解开，`deploy/` 里已经同时有 `watch.py` / `selftest.py` / `config.json` / `douyu-watch.service` / `douyu-watch.timer` / `install-watch.sh`，**它们必须在同一层**（脚本是从自己所在目录往上找源文件的）。直接：

```bash
unzip -o deploy.zip && cd deploy
sudo bash install-watch.sh
```

脚本会：
- 检查 python3
- 放 `watch.py` / `selftest.py` / `watchdog.py` 到 `/opt/douyu-live-notify/`（已有同名文件先备份）
- 打印这三个文件的 sha256 前 16 位（用来确认不是旧版）
- 放 `config.json`（**若已存在绝不覆盖**）
- 装好 systemd 单元（含看门狗），但**不启动任何定时器**（等你验证通过再开）
- 放一份 `/etc/default/douyu-watchdog`（看门狗告警配置模板，权限 600，**已存在不覆盖**）
- 最后打印后续步骤清单

**`config.json` 里 `treat_loop_as_live` 保持 `false`**（轮播不算开播，理由见第 1 节）。
群号和 token 记得换成你自己的，见 0.2。想 @ 特定的人就往 `at_users` 里加 QQ 号，例如 `["你的QQ号"]`。

> `at_all` 保持 `false`。@全体成员一个 QQ 号**一天只有 10 次配额且所有群共享**，用它纯属浪费还会惹人烦。

---

## 8. 第六步：验证（按顺序做，别跳）

```bash
cd /opt/douyu-live-notify

# ① 自检：逻辑检查，不联网、不发消息
python3 selftest.py && tail -3 selftest_result.txt
```
**期望**：`结果：24 项通过，0 项失败（共 24 项）`。

> 显示 **18 项** 就说明 `/opt/douyu-live-notify/` 里躺着的是旧版
> （少了「配置校验」那 6 项）。重新解压最新的 `deploy.zip`，
> 在 `deploy/` 里跑一次 `sudo bash install-watch.sh` 换掉文件 ——
> 它不会覆盖你填好的 `config.json` —— 然后再重跑 ①。

```bash
# ② 斗鱼接口体检：两个接口各打一次，打印原始数据
python3 watch.py --probe YOUR_ROOM_ID
```
**期望**：`betard` 和 `legacy` 两组都成功，能看到主播名和 `videoLoop` 字段。

```bash
# ③ 看当前判定（不入库、不发通知）
python3 watch.py --once
```
**期望**：`判定：未开播`（因为现在 `show_status=1` 但 `videoLoop=1`，属轮播）。
**如果显示「直播中」，说明 `treat_loop_as_live` 没生效，停下检查配置。**

```bash
# ④ 链路验证：真的往群里发一条测试消息
python3 watch.py --test-notify
```
**期望**：控制台 `onebot 发送成功`，**QQ 群 YOUR_GROUP_ID 里立刻收到一条测试消息**。

**必须亲眼在群里看到这条消息，才算这一步通过。**

```bash
# ⑤ 全绿之后，才开定时器
systemctl enable --now douyu-watch.timer
systemctl list-timers douyu-watch.timer
```

---

## 9. 第八步：装看门狗（强烈建议，别跳）

到这一步你已经有一个「每分钟检查一次」的东西了。但它有两个**静默失败**：

- **掉线无感知**：NapCat 被踢下线后，`watch.py` 每分钟照跑照判定，只是消息发不出去，
  日志里留一行 `[error]`。程序看起来完全正常，整条链路已经断了。
- **停摆无感知**：定时器被禁、文件被改坏、进程被 OOM 杀掉 —— 结果都是「什么都没发生」，
  而不是「报错」。半夜开播，你第二天才知道没提醒。

看门狗每 2 分钟独立体检一次，把这两类静默失败变成一条能看见的告警，能自愈的自己动手。
完整设计见 **`WATCHDOG.md`**。最短路径：

```bash
cd /opt/douyu-live-notify

# a) 先离线自检（不联网、不碰 docker）
python3 watchdog.py --selftest          # 期望：34 项通过，0 项失败（共 34 项）

# b) 配告警通道 —— 唯一需要你花几分钟决定的事
nano /etc/default/douyu-watchdog
```

**必须至少配一条「不经过 NapCat」的通道**，否则「掉线告警」等于没说
（NapCat 掉线时它自己发不出消息）。推荐二选一：

| 选哪个 | 怎么填 |
|---|---|
| Bark（iOS）/ Server酱（微信） | `ALERT_WEBHOOK=<App里给你的地址>` + `ALERT_WEBHOOK_KIND=bark`（或 `serverchan`） |
| 飞书 / 钉钉 / 企业微信群机器人 | `ALERT_WEBHOOK=<机器人地址>` + `ALERT_WEBHOOK_KIND=feishu`（或 `dingtalk` / `wecom`） |

```bash
# c) 验证通道真的通（手机/群里应该收到一条）
python3 watchdog.py --test-alert

# d) 只读看一眼当前是否健康（不告警、不重启任何东西）
python3 watchdog.py --status

# e) 确认无误后启用
systemctl enable --now douyu-watchdog.timer
tail -f /var/log/douyu-watch/watchdog.log
```

> ⚠️ **`AUTO_RESTART` 默认是 1**（掉线会自动 `docker restart napcat`）。
> 敢开它的前提是下面「重启免扫码验收」已经通过。**没验过就先设 `AUTO_RESTART=0`。**
> 反复重启有硬闸门：连续 3 轮不通才动手、两次之间隔 15 分钟、一天最多 6 次，到顶转人工。

### 重启免扫码验收（必做，一次就够）

这是「长期无人值守」这个假设的**唯一验证点**，别等主播开播那晚才发现不成立：

```bash
docker restart napcat && sleep 45
docker logs napcat 2>&1 | tail -40        # 关键：不该再出现二维码
curl -s -H "Authorization: Bearer YOUR_ONEBOT_TOKEN" http://127.0.0.1:3000/get_login_info
```

**判过** = 返回 `"status":"ok"` + 你的 QQ 号，且日志里没有新二维码。
**若又出二维码** → `ACCOUNT` 没生效或登录态没落到 `/opt/napcat/ntqq`，停下排查：

```bash
docker inspect napcat --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ACCOUNT
```

### 看门狗装完必须验证它真的会叫

**不验证的看门狗等于没加** —— 它可能在最需要的时候哑掉。最少做验证 A（不影响 QQ）：

```bash
systemctl stop douyu-watch.timer      # 故意让它停摆
# 等 8 分钟以上（阈值 7 分钟）
tail -20 /var/log/douyu-watch/watchdog.log
python3 watchdog.py --status
```

**应该看到**：一条「监控已停摆」告警 → 自动把定时器拉起来 → 下一轮发「已恢复」。
验证 B（`docker stop napcat`，会短暂断线）和验证 C 见 `WATCHDOG.md` 第五节。

### 一条命令看健康

```bash
cd /opt/douyu-live-notify && python3 watchdog.py --status
```

---

## 10. 第九步：观察运行状态

```bash
systemctl status douyu-watch.timer
journalctl -u douyu-watch -n 50 --no-pager
tail -f /var/log/douyu-watch/tick.log     # 推荐常看这个
```

心跳长这样：

```
[10:31:38] #2 LOOP(轮播) | 某直播间的标题 | 热度 - | loop=1 | via betard
```

- `LOOP(轮播)` = 房间在放录播，**不算开播**（现在就是这个状态，属正常）
- `LIVE` = 真人开播
- `offline` = 房间关闭
- `loop=` **每轮都记** —— 这是复验「真人开播时 videoLoop 是否变回 0」的唯一依据

> **首次运行不发通知**，只记录当前状态（防止一启动就误报）。
> 反过来也要知道：**如果启动时主播正好在播，这次开播不会提醒**（没有状态切换）。
> 想强制重置：`rm -f /opt/douyu-live-notify/state_YOUR_ROOM_ID.json`

---

## 11. 日常运维

| 想做什么 | 命令 |
|---|---|
| 停掉提醒 | `systemctl disable --now douyu-watch.timer` |
| 临时看一次 | `cd /opt/douyu-live-notify && python3 watch.py --tick` |
| 改配置 | 编辑 `config.json`，不用重启（每次 tick 都重读） |
| 重置状态 | `rm -f /opt/douyu-live-notify/state_YOUR_ROOM_ID.json` |
| 看 NapCat 日志 | `docker logs napcat -f --tail=50` |
| QQ 掉线 | 先 `docker restart napcat`（多数情况自动快速登录回来）；日志里又出现二维码才需要重扫 |
| **一条命令看健康** | `cd /opt/douyu-live-notify && python3 watchdog.py --status` |
| **看看门狗日志** | `tail -f /var/log/douyu-watch/watchdog.log` |
| **看告警历史（永不丢的那份）** | `tail -50 /var/lib/douyu-watchdog/alerts.log` |
| **改告警通道 / 阈值** | 编辑 `/etc/default/douyu-watchdog` → `systemctl restart douyu-watchdog.timer` |
| **验证告警通道通不通** | `python3 watchdog.py --test-alert` |
| **补一条丢失的开播通知** | `python3 watchdog.py --recover-notify`（主播仍在播时用，会先备份状态文件） |
| 服务报 `209/STDOUT` + `Failed at step STDOUT` | `/var/log/douyu-watch` 不存在，systemd 打开输出文件失败 —— 报错完全不提日志目录，极易误判成 python 问题。修：`systemd-tmpfiles --create /etc/tmpfiles.d/douyu-watch.conf`（install-watch.sh 已装该规则，开机自动重建） |
| 更新 watch.py | 覆盖后跑 `python3 selftest.py` 确认没坏 |
| 清空日志 | `truncate -s 0 /var/log/douyu-watch/tick.log` |
| **查内存被谁占了** | `bash mem-report.sh`（只读，包含历史 OOM 记录） |
| **看 swap 用量** | `free -h; swapon --show` |
| **加 / 撤 swap** | `sudo bash add-swap.sh` / `sudo bash add-swap.sh --remove` |
| **看 NapCat 实际吃多少内存** | `docker stats napcat --no-stream` |
| **机器人莫名掉线** | 多半是 OOM，先 `bash mem-report.sh` 看第 6 节 |

日志一个月几 MB，不着急清理。

---

## 12. 排错速查表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 拉镜像超时 | Docker Hub 不通 / 镜像源失效 | 重跑 `sudo bash setup-docker-mirror.sh` |
| `502 Bad Gateway` 打 127.0.0.1:3000 | 全局代理劫持回环。**端口没在监听却返回 502 就一定是代理** | `env \| grep -i proxy`；客户端对本机地址走空代理 |
| `Connection refused` | NapCat 没跑 / 容器内 host 填了 `127.0.0.1` | `docker compose ps`；host 改 `0.0.0.0` |
| `403 Forbidden` | token 对不上 | 两端核对 `YOUR_ONEBOT_TOKEN` |
| `OneBot 返回异常：status failed` | 群号错 / 机器人不在群里 / 被禁言 | `get_group_list` 核对 `YOUR_GROUP_ID` |
| `[error] 所有探测接口都失败了` | 服务器访问不了斗鱼 | `curl -I https://www.douyu.com/`（体检时是 200，若变了说明网络有变） |
| 判定一直「直播中」 | `treat_loop_as_live` 被改回 true | 改回 `false` |
| 群里一直没消息 | 定时器没开 / 主播没真开播 | `systemctl list-timers`；看 `tick.log` 的 `loop=` 值 |
| 日志里报 `[error] onebot 通知失败` | NapCat 挂了或配置被改 | `docker logs napcat` |
| **机器人掉线，通知静默丢了** | 本项目**没有掉线检测**；且发送失败只打印一行 `[error]`，而状态已落盘 → **那条开播通知不会补发** | 见第 12 节。每天扫一眼：`grep -c '\[error\]' /var/log/douyu-watch/tick.log` |
| **机器人莫名掉线，重启又好了** | 被内核 OOM killer 杀掉。它**不写应用日志**，所以看起来毫无征兆 | `bash mem-report.sh` 看第 6 节；`sudo bash add-swap.sh` |
| **同机的网页突然挂了，自身日志无异常** | 很可能也是 OOM killer 杀的 —— 全局 OOM **按 RSS 从大到小挑牺牲品**，网页占得多就先死 | `journalctl -k \| grep -i oom`；给 NapCat 加内存上限（第 4 步已默认加）；或改走零内存推送通道（0.3 路线 B） |
| `docker inspect napcat --format '{{.HostConfig.Memory}}'` 返回 0 | compose 的 `deploy.resources.limits` 没被识别 | 换成旧写法 `mem_limit: 900m`，再 `docker compose up -d` |

---

## 12. 已知边界（别指望它做到的事）

- **斗鱼接口是非官方的**。这两个端点是斗鱼网页端自用的，**改版就可能失效**。脚本用「主接口 + 备用接口」双层 fallback 提高存活率，但不保证永久可用。
- **延迟 = 定时器间隔 × confirm_rounds**。每分钟一次 + 确认 2 次 → 最慢约 2 分钟发现开播。
- **轮播判定还有个小缺口**：还没在**真人开播时**亲眼确认 `videoLoop` 变回 `0`。证据很硬（三组对照），但严格说仍是推断。**心跳日志记着 `loop=` 值，下次真人开播看一眼即可定案。**
- **消息送达不保证**。QQ 风控可能静默丢消息（日志显示成功但收不到），**别把这个提醒当唯一信息来源**。
- **掉线检测靠看门狗，补发仍要手动一次**。掉线本身分三种，「能不能自动恢复」是根因决定的，
  看门狗只能把「掉过线」这件事告诉你、并在能自愈时自愈：

  | 情形 | 能自动恢复吗 |
  |---|---|
  | 容器 / 进程崩了 | 能。`restart: always` + 登录态 + `ACCOUNT` 快速登录 |
  | 幽灵假死（腾讯侧断链，本地不报错也不出二维码） | **看门狗能发现并自动重启**（容器在跑但接口无响应） |
  | 登录态失效（换设备 / 改密 / 手机端把服务器顶下线） | 不能，必须重新扫码。看门狗会识别出「容器日志里出现二维码」，**不会白重启**，直接告警要人处理 |

  没装看门狗时，掉线你收不到任何提示。装了之后，另一种静默失败也一并解决：
  **通知发送失败**只打印一行 `[error]`、状态却已落盘 —— 看门狗会立刻告警，
  但那条消息**仍然需要你手动补**（`python3 watchdog.py --recover-notify`，主播仍在播时有效）。

  自查是否在线：`curl -s -H "Authorization: Bearer YOUR_ONEBOT_TOKEN" http://127.0.0.1:3000/get_login_info`
  （返回你的 QQ 号才算真在线）。**别在手机 QQ 上登录这个小号**，那会把服务器端顶下线。
- **协议端合规风险自负**。脚本只是把消息 POST 给 OneBot 端点。
