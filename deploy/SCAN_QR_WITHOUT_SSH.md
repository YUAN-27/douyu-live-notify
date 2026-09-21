# 扫码登录：不开 SSH 隧道也能做

`DEPLOY.md` 第 5 节默认让你开 SSH 隧道去访问 WebUI 扫码。**这一步其实不是必需的。**

NapCat 会把登录二维码同时输出到三个地方：

| 输出位置 | 怎么取 |
|---|---|
| **日志里的「二维码解码URL」** | `docker logs napcat` —— 一行纯文本，最省事 |
| **`qrcode.png` 图片文件** | 容器内 `/app/napcat/cache/qrcode.png`（少数版本在 `/app/napcat/qrcode.png`） |
| 终端里的 ASCII 二维码 | `docker logs napcat` —— 但复制粘贴到聊天窗口常常会变形，不推荐 |

只要拿到第 1 种或第 2 种，就能在人自己的电脑上把二维码变回可扫的图片，**全程不用让
WebUI 接触网络，也不用开隧道**。

> 二维码有效期很短（约 1~2 分钟）。**拿到链接后要马上用**，超过 1 分钟基本就该重新生成一次了。

> 📁 **`qr_make.py` 不在本部署包里** —— 它在仓库根目录，跑在**你自己的电脑**上，
> 不需要传到服务器。本目录（`deploy/`）是给服务器用的；扫二维码是人做的事，用的是人自己的机器。

---

## 为什么不开隧道更省事

| 做法 | 需要什么 | 风险 |
|---|---|---|
| SSH 隧道 + WebUI | 本机能连服务器的 22 端口 | 无（但 22 常被安全组/公司网挡住） |
| **从日志取二维码** | 只要能在服务器上执行命令 | 无 |
| 把 6099 放行到公网 | 改云安全组 | **危险** —— 2025 年那次批量封号事件的成因 |

第三种绝对不要做。前两种都安全，但第二种依赖更少。

---

## 方式 A：拿「二维码解码URL」（推荐）

在服务器上执行：

```bash
docker logs napcat 2>&1 | grep -nE '二维码|qrcode|txz\.qq\.com' | tail -20
```

会看到类似这样一行：

```
[warn] 二维码解码URL: https://txz.qq.com/p?k=xxxxxxxxxxxxxxxxxxxx&f=xxxxxxxxxxxxx
```

把这一行的 **URL 原文**复制出来（`k=` 和 `f=` 的值都要完整，别截断），
然后在**你自己的电脑**上把它变成二维码图片：

```bash
python qr_make.py --url "https://txz.qq.com/p?k=xxxx&f=xxxx"
# 输出 qr.png
```

用**小号的手机 QQ** 扫 `qr.png` 即可。

> ⚠️ 这个 URL 本质上是**一次性登录凭据**。别贴到公开群里、别发到论坛。
> 用 `qr_make.py` 在本地生成，就不会把它交给任何第三方二维码网站。
> 实在想省事也可以用草料二维码之类的在线工具，但那等于把这个 token 交给了对方 —— 能用本地就用本地。

## 方式 B：把 `qrcode.png` 取出来

日志里没有 URL（有些版本不打印，或已被后续输出冲掉）时用这条。

先找到文件：

```bash
docker exec napcat sh -c 'ls -l /app/napcat/cache/qrcode.png /app/napcat/qrcode.png 2>/dev/null'
```

取出来并转成 base64（方便直接复制）：

```bash
docker exec napcat sh -c 'base64 -w0 /app/napcat/cache/qrcode.png'
```

把输出那一长串完整复制（可能几 KB，**不要省略、不要加 "..."**），然后在本地还原成图片：

```bash
python qr_make.py --b64str "<粘贴那一长串>"
# 或者存成文件：python qr_make.py --b64file qr.b64
```

不想用脚本的话，Windows 上也能直接还原：

```powershell
$s = Get-Content .\qr.b64 -Raw
[IO.File]::WriteAllBytes("$HOME\Desktop\qr.png", [Convert]::FromBase64String($s))
```

## 二维码过期了怎么办

重新生成一次就行。此时还没登录，重启容器不会丢任何东西：

```bash
docker restart napcat
sleep 20
docker logs --since 30s napcat 2>&1 | grep -nE '二维码|txz\.qq\.com'
```

拿到新链接立刻用。**别 `docker compose down -v`** —— `-v` 会删数据卷，
连登录态一起删掉，下次还得重新扫码。

## 登录成功后怎么确认

```bash
docker logs --since 3m napcat 2>&1 | tail -30
curl -s -H "Authorization: Bearer $(python3 -c "import json;print(json.load(open('/opt/napcat/napcat/config/onebot11_<QQ号>.json'))['network']['httpServers'][0]['token'])")" \
  http://127.0.0.1:3000/get_login_info
```

第二条返回 `status: ok` 且 `user_id` 是那个小号，就说明通道已经活了。

登录态持久化在 `/opt/napcat/ntqq`（挂载到容器内 `/app/.config/QQ`），
**以后容器重启不用重新扫码**。

---

## 附：如果还是想把 SSH 隧道修好

以后想用 WebUI 看日志 / 改配置时会方便些。注意修的是 **22 端口（SSH）**，
**不是** 6099 —— 6099 永远不要对公网放行。

先分清超时的性质，这决定了往哪查：

| 报错 | 含义 |
|---|---|
| `Connection timed out` | 包被**静默丢弃** —— 有防火墙/安全组在拦 |
| `Connection refused` | 包到了服务器，但**没人在监听** —— 端口写错或服务没跑 |

`timed out` 说明不是「服务器没开 SSH」，而是**路上被拦了**。按可能性排序：

1. **云安全组没放行 22**，或规则限定来源 IP，而你的公网 IP 变了（家宽动态 IP 很常见）
   → 云控制台看安全组入站规则；顺便查一下自己当前的公网 IP
2. **服务器本地防火墙**拦了：`ufw status` / `iptables -S`
3. **SSH 换了端口**（安全加固常见做法），比如改成 2222
   → `ss -lntp | grep -i sshd`；`grep -Ei '^\s*Port' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf`
4. **你本机所在网络封了出站 22**（公司网、酒店、校园网常见）
   → 换手机热点再试一次；若热点能连，就是这个原因
5. **被 fail2ban 封了**（之前多次密码错误）
   → 服务器上 `fail2ban-client status sshd`

本机（Windows PowerShell）快速判定：

```powershell
# 自己当前的公网 IP —— 和安全组白名单对一下
(Invoke-RestMethod https://api.ipify.org)

# 22 通不通
Test-NetConnection <服务器公网IP> -Port 22

# 对比：网页端口通不通
#   80/443 通、22 不通 → 机器活着，是 22 被单独拦了（安全组或端口改过）
#   都不通           → 本机网络问题
Test-NetConnection <服务器公网IP> -Port 443
Test-NetConnection <服务器公网IP> -Port 80
```

**但是**：修好隧道只是「以后看 WebUI 方便」。扫码、配 OneBot、跑测试
**全都不需要它** —— 配置由 agent 直接写 JSON 文件，测试用 `curl`，日志用 `docker logs`。
所以不用为了这一步卡住整个部署。
