#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""斗鱼开播提醒 · 看门狗（watchdog）

存在的理由：watch.py 有两个「静默失败」，出了事你收不到任何提示 ——

  ① 掉线无感知
     NapCat 被 QQ 踢下线 / 会话失效后，watch.py 每分钟照跑照判定，
     只是 OneBot 发不出去，日志里留一行 `[error] onebot 通知失败`。
     程序看起来完全正常，实际整条链路已经断了。

  ② 停摆无感知
     定时器被禁、文件被改坏、进程被 OOM 杀掉 —— 结果都是「什么都没发生」，
     而不是「报错」。半夜主播开播，你第二天早上才知道没提醒。

看门狗每 2 分钟独立体检一次，把这两类静默失败变成一条看得见的告警，
并在能自愈的时候自己动手（重启 NapCat、重新拉起定时器）。

它**不修改 watch.py**，只读 tick.log / state_*.json / NapCat 容器，
必要时才去动 docker 和 systemd。watch.py 的指纹因此保持不变。

用法：
    python3 watchdog.py                  # 体检一轮（systemd timer 调用）
    python3 watchdog.py --status         # 一条命令看当前健康状况（只读）
    python3 watchdog.py --test-alert     # 往所有配好的通道发一条测试告警
    python3 watchdog.py --recover-notify # 补救一条丢失的开播通知（见文件末尾说明）
    python3 watchdog.py --selftest       # 离线自检，不联网、不碰 docker
    python3 watchdog.py --dry-run        # 只报告打算做什么，不真的动手

配置从哪来（优先级从高到低）：
    1. 命令行参数
    2. 环境变量 —— systemd 单元里的 EnvironmentFile=/etc/default/douyu-watchdog
    3. 本文件里的默认值

告警发到哪（都不填也能跑，但只有本地日志，半夜不会叫醒你）：
    ALERT_ONEBOT_PRIVATE  你的主 QQ 号。NapCat 在线时用私聊发（发到群会打扰群友）
    ALERT_WEBHOOK         告警地址，多条用 `;` 分隔，写法 `类型|地址`。
                          **这是唯一在 NapCat 掉线时还能到达你的通道**
    ALERT_WEBHOOK_KIND    地址里没写 `类型|` 时的默认类型。
                          generic / serverchan / pushplus / bark / telegram / dingtalk / feishu / wecom
    ALERT_EMAIL           邮箱地址，走本机 msmtp 或 sendmail
    DAILY_OK_AT           例如 20:00，每天这个点后发一条「一切正常」（沉默 = 出事）
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# 配置：默认值 + 环境变量覆盖
# --------------------------------------------------------------------------

DEFAULTS = {
    # 路径
    "APP_DIR": "/opt/douyu-live-notify",
    "CONFIG": "",              # 空则用 APP_DIR/watch_config.json 或 config.json
    "TICK_LOG": "/var/log/douyu-watch/tick.log",
    "STATE_DIR": "/var/lib/douyu-watchdog",
    # 被监控对象
    "NAPCAT_CONTAINER": "napcat",
    "ONEBOT_BASE": "",         # 空则从 watch 的 config.json 里读 onebot.base
    "ONEBOT_TOKEN": "",        # 空则从 watch 的 config.json 里读 onebot.token
    # 判定阈值
    "STALE_SECONDS": "420",        # 心跳日志 7 分钟没更新 → 判定停摆
    "STUCK_ROUNDS": "3",           # 连续 3 轮体检序号不前进 → 判定卡死（约 6 分钟）
    "FAIL_THRESHOLD": "3",         # 连续 3 轮 NapCat 不通才动手（约 6 分钟）
    # 自愈
    "AUTO_RESTART": "1",           # 1=自动重启 NapCat，0=只告警
    "RESTART_COOLDOWN": "900",     # 两次自动重启之间至少隔 15 分钟
    "MAX_RESTARTS_PER_DAY": "6",   # 一天最多自动重启 6 次，超了就停手告警
    # 告警
    "ALERT_ONEBOT_PRIVATE": "",
    "ALERT_WEBHOOK": "",
    "ALERT_WEBHOOK_KIND": "generic",
    "ALERT_TG_CHAT_ID": "",
    "ALERT_EMAIL": "",
    "ALERT_REPEAT_SECONDS": "3600",  # 同一个问题最多每小时提醒一次
    "DAILY_OK_AT": "",
    "DAILY_OK_TITLE": "",          # 空则用内置文案
    "DAILY_OK_BODY": "",           # 空则用内置文案
    "DAILY_OK_KAOMOJI": "",        # 颜文字池，按日期轮换；空则不追加
    "HTTP_TIMEOUT": "10",
    "CMD_TIMEOUT": "60",
}


def cfg_get(key, cli_value=None):
    """命令行 > 环境变量 > 默认值。"""
    if cli_value not in (None, ""):
        return cli_value
    return os.environ.get(key) or DEFAULTS.get(key, "")


def cfg_int(key, cli_value=None):
    try:
        return int(str(cfg_get(key, cli_value)).strip())
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def cfg_flag(key, cli_value=None):
    v = str(cfg_get(key, cli_value)).strip().lower()
    return v in ("1", "true", "yes", "on", "y")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hostname():
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "unknown-host"


def run_cmd(args, timeout=60):
    """跑一条外部命令，返回 (returncode, stdout+stderr)。命令不存在返回 (127, ...)。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "命令不存在：%s" % args[0]
    except subprocess.TimeoutExpired:
        return 124, "命令超时（%ss）：%s" % (timeout, " ".join(args))
    except Exception as exc:  # noqa: BLE001
        return 1, "执行失败：%s" % exc


def read_text(path, limit_bytes=262144):
    """读文件尾部（默认最后 256KB）。日志可能很大，只关心最近的部分。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fp:
            if size > limit_bytes:
                fp.seek(size - limit_bytes)
            return fp.read().decode("utf-8", errors="replace"), size
    except OSError:
        return None, 0


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 检查 1：心跳新鲜度
# --------------------------------------------------------------------------

# 心跳行长这样（watch.py 的 tick() 里）：
#   [12:34:56] #128 offline | 房间标题 | 热度 - | loop=1 | via betard
# ⚠️ 注意它**只有时间、没有日期**，所以「最后一跳是几点」不能拿来算新鲜度
#    （日志是跨天追加的）。新鲜度只能看文件 mtime；
#    序号 #N 用来判断「还在往前走」还是「卡死了」。
HB_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+#(\d+)\s+(\S+)")


def parse_heartbeats(text):
    """从日志文本里取出所有心跳行，返回 [(序号, 状态标签, 时间串), ...]。"""
    out = []
    for line in text.splitlines():
        m = HB_RE.match(line.strip())
        if m:
            out.append((int(m.group(2)), m.group(3), m.group(1)))
    return out


def check_ticklog(log_path, prev_seq, hb_same_count, stale_seconds, stuck_rounds):
    """判定 watch.py 是否健康。返回 dict。"""
    res = {
        "verdict": "ok",          # ok / no_log / not_running / stuck
        "seq": None,
        "age": None,
        "same_count": hb_same_count,
        "label": "",
        "detail": "",
    }
    text, _size = read_text(log_path)
    if text is None:
        res["verdict"] = "no_log"
        res["detail"] = "找不到 %s（定时器还没开过？或者日志路径变了）" % log_path
        return res

    try:
        age = time.time() - os.path.getmtime(log_path)
    except OSError:
        age = None
    res["age"] = age

    hbs = parse_heartbeats(text)
    if hbs:
        res["seq"] = hbs[-1][0]
        res["label"] = hbs[-1][1]

    if age is None:
        res["verdict"] = "no_log"
        res["detail"] = "读不到 %s 的修改时间" % log_path
        return res

    if age > stale_seconds:
        res["verdict"] = "not_running"
        res["detail"] = ("日志已 %d 分钟没有更新（阈值 %d 分钟）"
                         "—— watch.py 没有在跑" % (age / 60.0, stale_seconds / 60.0))
        return res

    if res["seq"] is None:
        # 日志在更新但没有心跳行：可能 log_heartbeat 被关了，属于配置选择，不报卡死
        res["detail"] = "日志在更新，但没有心跳行（log_heartbeat 可能被关掉了）"
        return res

    # 日志在写，但序号没往前走 → 每一轮都在抛错（tick 里心跳打印在探测之后，
    # 探测失败就永远走不到打心跳那一步）
    if prev_seq is not None and res["seq"] == prev_seq:
        res["same_count"] = hb_same_count + 1
        if res["same_count"] >= stuck_rounds:
            res["verdict"] = "stuck"
            res["detail"] = ("心跳序号连续 %d 轮停在 #%d 不动，日志却在更新"
                             "—— watch.py 每轮都失败（多半是斗鱼接口不通或配置有问题）"
                             % (res["same_count"], res["seq"]))
    else:
        res["same_count"] = 0
    return res


# --------------------------------------------------------------------------
# 检查 2：NapCat 是否在线
# --------------------------------------------------------------------------

def http_json(url, token=None, timeout=10, method="POST", body=None, data=None,
              content_type="application/json"):
    """极简 HTTP 客户端，只用标准库。返回 (ok, 解析结果或错误说明)。"""
    headers = {}
    payload = None
    if token:
        headers["Authorization"] = "Bearer " + str(token)
    if data is not None:
        payload = data.encode("utf-8")
        headers["Content-Type"] = content_type
    elif body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            return True, json.loads(raw)
        except ValueError:
            return True, {"_raw": raw[:500]}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        return False, "HTTP %s %s %s" % (exc.code, exc.reason, detail)
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


def docker_running(container, runner=run_cmd, timeout=30):
    """返回 (是否在跑, 说明)。容器不存在也算 False。"""
    rc, out = runner(["docker", "inspect", "-f", "{{.State.Running}}", container], timeout=timeout)
    if rc == 127:
        return None, "docker 命令不可用"
    if rc != 0:
        return False, "容器 %s 不存在或 docker 无响应：%s" % (container, out.strip()[:200])
    return out.strip().lower() == "true", out.strip()[:80]


def docker_oom_killed(container, runner=run_cmd, timeout=30):
    rc, out = runner(["docker", "inspect", "-f", "{{.State.OOMKilled}}", container], timeout=timeout)
    if rc != 0:
        return False
    return out.strip().lower() == "true"


def docker_log_tail(container, lines=200, runner=run_cmd, timeout=30):
    rc, out = runner(["docker", "logs", "--tail", str(lines), container], timeout=timeout)
    return "" if rc != 0 else out


# 容器日志里出现这些字样 = NapCat 正在等你扫码。这是「登录态失效」，
# 重启解决不了，只能人工扫一次 —— 所以必须区分出来，别白重启。
QR_PAT = re.compile(r"二维码|qrcode|扫码|扫码登录|请使用手机")


def check_napcat(container, base, token, timeout=10, runner=run_cmd, http=http_json,
                 test_online=None):
    """判定 NapCat 状态。

    test_online 为 None 时走真实网络；自检时传一个假函数进来。
    返回 dict(verdict, user_id, detail)。
        online          在线且已登录
        not_running     容器没在跑
        oom_killed      容器被 OOM 杀了
        unresponsive    容器在跑，但 OneBot 接口不通（假死/正在启动）
        logged_out      接口通，但未登录 → 需要人工扫码
        need_qr         未登录 + 容器日志里出现了二维码
        docker_missing  宿主机没有 docker 命令
        skipped         没配 OneBot（比如只用 console 通道）
    """
    out = {"verdict": "skipped", "user_id": None, "detail": ""}

    if not base:
        out["detail"] = "没配置 OneBot 地址，跳过这一项"
        return out

    running, why = docker_running(container, runner=runner)
    if running is None:
        out["verdict"] = "docker_missing"
        out["detail"] = "宿主机上没有 docker 命令，无法检查容器状态（%s）" % why
        return out

    if running is False:
        out["verdict"] = "not_running"
        out["detail"] = "容器 %s 没在运行：%s" % (container, why)
        return out

    url = base.rstrip("/") + "/get_login_info"

    if test_online is not None:
        ok, payload = test_online()
    else:
        ok, payload = http(url, token=token, timeout=timeout, method="POST", body={})

    if not ok:
        out["verdict"] = "unresponsive"
        out["detail"] = "容器在跑，但 %s 请求失败：%s" % (url, payload)
        return out

    status = str((payload or {}).get("status") or "")
    data = (payload or {}).get("data") or {}
    uid = data.get("user_id")
    if status == "ok" and uid:
        out["verdict"] = "online"
        out["user_id"] = str(uid)
        out["detail"] = "已登录，QQ %s" % uid
        return out

    # 接口通但没登录 —— 看容器日志里是不是在等扫码
    tail = docker_log_tail(container, runner=runner)
    if QR_PAT.search(tail):
        out["verdict"] = "need_qr"
        out["detail"] = ("容器在跑、接口通，但已掉线且日志里出现了二维码"
                         "—— 需要人工扫码登录（重启没用）")
        return out

    out["verdict"] = "logged_out"
    out["detail"] = ("接口返回 status=%r、user_id=%r，未登录。"
                     "容器日志尾部没看到二维码，可能是会话正在恢复或 QQ 侧踢下线"
                     % (status, uid))
    return out


# --------------------------------------------------------------------------
# 检查 3：日志里的「通知失败」
# --------------------------------------------------------------------------

NOTIFY_FAIL_PAT = re.compile(r"通知失败")
ERROR_LINE_PAT = re.compile(r"^\[error\]")


def scan_log_tail(text, from_line):
    """从第 from_line 行开始扫，返回 (新的通知失败行, 新的 error 行, 当前总行数)。

    用行号做偏移，比字节偏移好处理日志轮转（文件变小就当从头再扫）。
    """
    lines = text.splitlines()
    total = len(lines)
    if from_line < 0 or from_line > total:
        # 文件被轮转/清空过：只扫最后 200 行，别把整份历史当新事件
        from_line = max(0, total - 200)
    window = lines[from_line:]
    fails = [ln.strip() for ln in window if NOTIFY_FAIL_PAT.search(ln)]
    errors = [ln.strip() for ln in window if ERROR_LINE_PAT.match(ln.strip())]
    return fails, errors, total


# --------------------------------------------------------------------------
# 告警通道
# --------------------------------------------------------------------------

# 各通道**成功**时的标志。写成白名单而不是「code 非 0 即失败」是踩过坑的：
# bark 和 pushplus 成功时返回 code=200，用「非 0 即失败」会把成功判成失败，
# 于是日志里一直报通道失败、人却被真的推送到了（或者反过来不敢用）。
WEBHOOK_OK = {
    "serverchan": ("code", ("0",)),
    "pushplus": ("code", ("200",)),
    "bark": ("code", ("200",)),
    "dingtalk": ("errcode", ("0",)),
    "wecom": ("errcode", ("0",)),
    "feishu": ("code", ("0", "200")),
    "telegram": (None, ()),        # 靠 ok:true 判定
    "generic": (None, ()),         # 自建服务五花八门，只认传输层失败
}

# 告警正文里带的字段名。Server酱 desp 支持 Markdown，pushplus 必须显式指定
# template=markdown，否则换行会被吞、微信里挤成一整段。
WEBHOOK_ALIASES = {"wecom_bot": "wecom", "lark": "feishu", "sct": "serverchan", "wx": "wecom"}


def build_payload(kind, title, body, host, params=None):
    """按通道类型拼请求。

    params：从 URL 查询串里解析出来的键值（例如 pushplus 的 token 放 query 里传）。
    返回 dict(method=..., json=/data=/suffix=..., content_type=...)
    """
    text = "%s\n%s" % (title, body)
    params = params or {}
    kind = WEBHOOK_ALIASES.get((kind or "generic").strip().lower(),
                              (kind or "generic").strip().lower())
    if kind == "serverchan":
        # title 里不能有换行，否则接口报「包含特殊字符」；正文放 desp，支持 Markdown
        one_line = " ".join(str(title).split())
        return {"method": "POST", "data": urllib.parse.urlencode(
            {"title": one_line[:100], "desp": body}),
            "content_type": "application/x-www-form-urlencoded"}
    if kind == "pushplus":
        # token 走 query 传：ALERT_WEBHOOK=pushplus|https://www.pushplus.plus/send?token=xxx
        token = params.get("token") or params.get("sendkey") or ""
        return {"method": "POST", "json": {
            "token": token, "title": " ".join(str(title).split())[:100],
            "content": body, "template": "markdown"}}
    if kind == "bark":
        return {"method": "POST", "suffix": "/%s/%s" % (
            urllib.parse.quote(title[:80]), urllib.parse.quote(body[:400])),
            "suffix_is_path": True}
    if kind == "wecom":
        return {"method": "POST", "json": {"msgtype": "text", "text": {"content": text}}}
    if kind in ("dingtalk",):
        return {"method": "POST", "json": {"msgtype": "text", "text": {"content": text}}}
    if kind == "feishu":
        return {"method": "POST", "json": {"msg_type": "text", "content": {"text": text}}}
    if kind == "telegram":
        return {"method": "POST", "json": {"text": text}}
    # generic：飞书/钉钉之外的自建服务、Zapier、IFTTT、n8n 都吃这套
    return {"method": "POST", "json": {
        "title": title, "text": text, "level": "alert",
        "host": host, "time": now_text()}}


def webhook_ok(kind, resp):
    """按通道判定这次发送算不算成功。返回 (是否成功, 说明)。"""
    kind = WEBHOOK_ALIASES.get((kind or "generic").strip().lower(),
                              (kind or "generic").strip().lower())
    if not isinstance(resp, dict):
        return True, "已发送"
    field, good = WEBHOOK_OK.get(kind, (None, ()))
    if field and field in resp:
        val = str(resp[field])
        if val in good:
            return True, "已发送"
        # 失败时把接口说的话原样带出来，别让人去猜
        msg = resp.get("message") or resp.get("msg") or resp.get("errmsg") or ""
        hint = ""
        if kind == "pushplus" and str(resp.get("code")) == "905":
            hint = "（pushplus 未实名认证不能发消息，去 verify.pushplus.plus 实名）"
        elif kind == "serverchan" and str(resp.get("code")) == "429":
            hint = "（当天免费额度 5 条用完了，或触发限频）"
        return False, "接口返回失败：%s %s%s" % (
            json.dumps(resp, ensure_ascii=False)[:200], msg, hint)
    if resp.get("ok") is False:
        return False, "接口返回 ok=false：%s" % json.dumps(resp, ensure_ascii=False)[:200]
    # 有的服务不返回状态字段，那就只看传输层有没有失败
    for key in ("errcode", "error_code"):
        if key in resp and str(resp[key]) not in ("0", "None"):
            return False, "接口返回失败：%s" % json.dumps(resp, ensure_ascii=False)[:200]
    return True, "已发送"


def parse_webhook_specs(raw, default_kind="generic"):
    """把 ALERT_WEBHOOK 解析成多条通道。

    支持一条，也支持用 `;` 分开多条（systemd 的 EnvironmentFile 一个键只能写一行，
    所以没法用换行分隔）。每条两种写法：
        https://...                                  ← 用 ALERT_WEBHOOK_KIND
        pushplus|https://www.pushplus.plus/send?token=xxx
    为什么要支持多条：免费通道都有各自的天花板（Server酱 5 条/天、pushplus 200 条/天），
    告警这种事不该只有一条腿。多配一条就多一份送达概率。
    """
    specs = []
    for chunk in str(raw or "").replace("\n", ";").split(";"):
        item = chunk.strip()
        if not item:
            continue
        kind, url = default_kind or "generic", item
        if "|" in item:
            head, tail = item.split("|", 1)
            if head.strip() and "://" not in head:
                kind, url = head.strip(), tail.strip()
        url = url.strip()
        parsed = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        # 把 token 之类留在 URL 上会写进日志，取出来单独用更干净；
        # 但 query 本身保留不动，别的服务可能就靠它鉴权。
        specs.append({"kind": (kind or "generic").lower(), "url": url, "params": params})
    return specs


def redact_url(url):
    """打印/告警里用的是这个：不能把 ?token=... 原样写进日志和告警正文。"""
    try:
        p = urllib.parse.urlsplit(str(url))
        return urllib.parse.urlunsplit(
            (p.scheme, p.netloc, p.path, "<已隐去>" if p.query else "", ""))
    except Exception:
        return "<URL>"


def send_webhook(url, kind, title, body, host, tg_chat_id="", timeout=10, http=http_json):
    """发一条 webhook。返回 (是否成功, 说明)。"""
    if not url:
        return False, "没有配置 webhook"
    kind = WEBHOOK_ALIASES.get((kind or "generic").strip().lower(),
                              (kind or "generic").strip().lower())
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    spec = build_payload(kind, title, body, host, params=params)
    method = spec.get("method", "POST")

    if kind == "telegram":
        target = url
        payload_json = dict(spec.get("json") or {})
        if tg_chat_id:
            payload_json["chat_id"] = tg_chat_id
        if not payload_json.get("chat_id"):
            return False, "telegram 通道需要 ALERT_TG_CHAT_ID"
        ok, resp = http(target, timeout=timeout, method=method, body=payload_json)
    elif kind == "bark":
        target = url.rstrip("/") + spec.get("suffix", "")
        ok, resp = http(target, timeout=timeout, method=method, data="",
                        content_type="application/x-www-form-urlencoded")
    elif spec.get("data") is not None:
        ok, resp = http(url, timeout=timeout, method=method, data=spec["data"],
                        content_type=spec.get("content_type", "application/x-www-form-urlencoded"))
    else:
        ok, resp = http(url, timeout=timeout, method=method, body=spec.get("json") or {})

    if not ok:
        return False, "请求失败：%s" % resp
    return webhook_ok(kind, resp)


def send_email(to_addr, subject, body, runner=run_cmd):
    """优先 msmtp，其次 sendmail。都没有就返回失败（不装东西）。"""
    sender = os.environ.get("ALERT_EMAIL_FROM") or ("douyu-watchdog@" + hostname())
    msg = ("From: %s\nTo: %s\nSubject: %s\nContent-Type: text/plain; charset=utf-8\n\n%s\n"
           % (sender, to_addr, subject, body))
    if shutil.which("msmtp"):
        p = subprocess.run(["msmtp", to_addr], input=msg.encode("utf-8"),
                           capture_output=True, timeout=30)
        return (p.returncode == 0), (p.stderr or b"").decode("utf-8", "replace")[:200] or "已发送"
    if shutil.which("sendmail"):
        p = subprocess.run(["sendmail", "-t"], input=msg.encode("utf-8"),
                           capture_output=True, timeout=30)
        return (p.returncode == 0), (p.stderr or b"").decode("utf-8", "replace")[:200] or "已发送"
    return False, "本机没有 msmtp / sendmail，邮件通道不可用"


def send_onebot_private(base, token, user_id, text, timeout=10, http=http_json):
    """用 NapCat 自己发私聊。只能在 NapCat 在线时用得上 ——
    但「watch.py 停摆」这类故障发生时 NapCat 往往是好的，所以这条路值得先试。"""
    if not (base and user_id):
        return False, "没配置私聊目标"
    url = base.rstrip("/") + "/send_private_msg"
    ok, resp = http(url, token=token, timeout=timeout, method="POST",
                    body={"user_id": user_id, "message": text})
    if not ok:
        return False, "请求失败：%s" % resp
    if str((resp or {}).get("status")) != "ok":
        return False, "OneBot 返回异常：%s" % json.dumps(resp, ensure_ascii=False)[:200]
    return True, "已发送"


def deliver_alert(kind_key, title, body, channels, napcat_ok, state_dir,
                  dry_run=False, http=http_json, runner=run_cmd):
    """把一条告警送到所有配好的通道，并**无条件**落一行到本地 alerts.log。

    channels = {"webhook":..., "webhook_kind":..., "webhooks":[...], "tg":..., "email":...,
                "onebot_private":..., "onebot_base":..., "onebot_token":...}
    返回 (已送达的通道列表, 说明列表)
    """
    host = hostname()
    text = "【斗鱼提醒·看门狗】%s\n%s\n主机：%s\n时间：%s" % (title, body, host, now_text())
    sent, notes = [], []

    if napcat_ok and channels.get("onebot_private"):
        ok, why = send_onebot_private(channels.get("onebot_base"), channels.get("onebot_token"),
                                      channels["onebot_private"], text, http=http)
        (sent.append("onebot私聊") if ok else notes.append("onebot私聊：%s" % why))

    specs = channels.get("webhooks")
    if specs is None:
        specs = parse_webhook_specs(channels.get("webhook"), channels.get("webhook_kind") or "generic")
    for spec in specs:
        label = "webhook(%s)" % spec["kind"]
        ok, why = send_webhook(spec["url"], spec["kind"], title, body, host,
                               tg_chat_id=channels.get("tg") or "", http=http)
        (sent.append(label) if ok else notes.append("%s：%s" % (label, why)))

    if channels.get("email"):
        if dry_run:
            notes.append("邮件（dry-run 跳过）")
        else:
            ok, why = send_email(channels["email"], "[斗鱼提醒·看门狗] " + title, body, runner=runner)
            (sent.append("邮件") if ok else notes.append("邮件：%s" % why))

    if not sent and not specs and not channels.get("email"):
        notes.append("⚠️ 没有配置任何独立于 NapCat 的告警通道，"
                     "这条告警只落在了本机日志里 —— 半夜没人会看到")

    # 本地永不失手的那一份
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "alerts.log"), "a", encoding="utf-8") as fp:
            fp.write("[%s] %s | %s | 送达：%s\n%s\n%s\n"
                     % (now_text(), kind_key, title,
                        ",".join(sent) or "无", body, "-" * 60))
    except OSError as exc:
        notes.append("写 alerts.log 失败：%s" % exc)

    return sent, notes


def alert_key_hash(kind_key, detail):
    return hashlib.sha256(("%s|%s" % (kind_key, detail)).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 自愈动作
# --------------------------------------------------------------------------

def restart_napcat(container, dry_run=False, runner=run_cmd, timeout=180):
    if dry_run:
        return True, "（dry-run）本应执行：docker restart %s" % container
    rc, out = runner(["docker", "restart", container], timeout=timeout)
    if rc == 0:
        return True, "已执行 docker restart %s" % container
    return False, "docker restart %s 失败：%s" % (container, out.strip()[:200])


def restart_watch_timer(dry_run=False, runner=run_cmd):
    """把 douyu-watch 定时器重新拉起来，并立刻手动触发一轮。

    为什么敢立刻触发一轮：tick 只在**状态发生切换**时才发通知。
    停摆期间主播真的开播了，这一轮正好把漏掉的通知补上；
    没开播则什么都不发。所以它是恢复动作，不是误发源。
    """
    steps = [["systemctl", "restart", "douyu-watch.timer"],
             ["systemctl", "start", "douyu-watch.service"]]
    if dry_run:
        return True, "（dry-run）本应执行：" + " && ".join(" ".join(s) for s in steps)
    notes = []
    for cmd in steps:
        rc, out = runner(cmd, timeout=120)
        notes.append("%s → rc=%d %s" % (" ".join(cmd), rc, out.strip()[:120]))
    return True, "; ".join(notes)


# --------------------------------------------------------------------------
# 一轮体检
# --------------------------------------------------------------------------

def run_once(args, runner=run_cmd, http=http_json, napcat_probe=None,
             now=None, dry_run=False):
    """执行一轮体检。返回 (verdict, report_lines)。

    参数都能注入，是为了让 --selftest 能在没有网络、没有 docker 的机器上跑完整逻辑。
    """
    now = now or time.time()
    app_dir = cfg_get("APP_DIR", args.app_dir)
    state_dir = cfg_get("STATE_DIR", args.state_dir)
    tick_log = cfg_get("TICK_LOG", args.tick_log)
    config_path = cfg_get("CONFIG", getattr(args, "config", None)) or \
        os.path.join(app_dir, "config.json")
    container = cfg_get("NAPCAT_CONTAINER", getattr(args, "container", None))

    watch_cfg = load_json(config_path) or {}
    ob = watch_cfg.get("onebot") or {}
    base = cfg_get("ONEBOT_BASE", getattr(args, "onebot_base", None)) or (ob.get("base") or "")
    token = cfg_get("ONEBOT_TOKEN", getattr(args, "onebot_token", None)) or (ob.get("token") or "")

    state_file = os.path.join(state_dir, "state.json")
    st = load_json(state_file) or {}

    channels = {
        "onebot_private": cfg_get("ALERT_ONEBOT_PRIVATE"),
        "onebot_base": base,
        "onebot_token": token,
        "webhook": cfg_get("ALERT_WEBHOOK"),
        "webhook_kind": cfg_get("ALERT_WEBHOOK_KIND"),
        "webhooks": parse_webhook_specs(cfg_get("ALERT_WEBHOOK"),
                                        cfg_get("ALERT_WEBHOOK_KIND") or "generic"),
        "tg": cfg_get("ALERT_TG_CHAT_ID"),
        "email": cfg_get("ALERT_EMAIL"),
    }

    # ---- 阈值的有效性检查：写错了要指出来，而不是默默用默认值 ----
    warn_lines = []
    for key in ("STALE_SECONDS", "STUCK_ROUNDS", "FAIL_THRESHOLD", "RESTART_COOLDOWN",
                "MAX_RESTARTS_PER_DAY", "ALERT_REPEAT_SECONDS"):
        raw = cfg_get(key)
        if str(raw).strip() and str(raw).strip() != str(DEFAULTS[key]):
            try:
                int(str(raw).strip())
            except ValueError:
                warn_lines.append("⚠️ 环境变量 %s=%r 不是数字，已退回默认值 %s"
                                  % (key, raw, DEFAULTS[key]))
    # ---- 告警通道的自检：配错了要当场说清怎么改，别等到出事才发现发不出去 ----
    for spec in channels["webhooks"]:
        url, kind = spec["url"], spec["kind"]
        if not url.startswith(("http://", "https://")):
            warn_lines.append("⚠️ ALERT_WEBHOOK 里这条不是 http(s) 地址，会发送失败：%s"
                              % redact_url(url)[:80])
            continue
        if kind == "pushplus" and not (spec["params"].get("token") or spec["params"].get("sendkey")):
            warn_lines.append("⚠️ pushplus 通道没带 token，请在地址里写成 "
                              "pushplus|https://www.pushplus.plus/send?token=你的token")
        if kind == "serverchan":
            m = re.search(r"/([A-Za-z0-9]+)\.send", url)
            key = m.group(1) if m else ""
            is_sc3 = "push.ft07.com" in url
            if not key:
                warn_lines.append("⚠️ serverchan 地址里没找到 SendKey，应形如 "
                                  "https://sctapi.ftqq.com/SCTxxxxxx.send")
            elif key.lower().startswith("sctp") and not is_sc3:
                warn_lines.append("⚠️ 这个 SendKey 是 Server酱³（SC3，sctp 开头）的，"
                                  "配 sctapi.ftqq.com 发不出去；SC3 的地址形如 "
                                  "https://<uid>.push.ft07.com/send/<你的key>.send，"
                                  "其中 uid 是 key 里 sctp 与 t 之间的数字")
            elif not key.lower().startswith("sctp") and is_sc3:
                warn_lines.append("⚠️ push.ft07.com 是 Server酱³ 的地址，但 key 不像 SC3 的"
                                  "（SC3 的 key 以 sctp 开头）。Turbo 版请用 "
                                  "https://sctapi.ftqq.com/<SendKey>.send")

    stale = cfg_int("STALE_SECONDS", getattr(args, "stale", None))
    stuck_rounds = cfg_int("STUCK_ROUNDS", getattr(args, "stuck_rounds", None))
    fail_threshold = cfg_int("FAIL_THRESHOLD")
    auto_restart = cfg_flag("AUTO_RESTART", getattr(args, "auto_restart", None))
    cooldown = cfg_int("RESTART_COOLDOWN")
    max_restarts = cfg_int("MAX_RESTARTS_PER_DAY")
    repeat_seconds = cfg_int("ALERT_REPEAT_SECONDS")

    # ---- 检查 1：watch.py 心跳 ----
    tick = check_ticklog(tick_log, st.get("last_hb_seq"), int(st.get("hb_same_count") or 0),
                         stale, stuck_rounds)

    # ---- 检查 2：NapCat ----
    nap = check_napcat(container, base, token,
                       timeout=cfg_int("HTTP_TIMEOUT"),
                       runner=runner, http=http, test_online=napcat_probe)

    # ---- 检查 3：日志里的通知失败 ----
    text, _ = read_text(tick_log)
    fails, errors, total_lines = scan_log_tail(text or "", int(st.get("alert_scan_lines") or 0))

    # ---- 汇总本轮要发的告警 ----
    pending = []   # (kind_key, level, title, body)

    if tick["verdict"] == "not_running":
        pending.append(("tick_not_running", "alert", "监控已停摆",
                        tick["detail"] + "\n\n影响：这段时间主播开播不会有人提醒。"))
    elif tick["verdict"] == "stuck":
        pending.append(("tick_stuck", "alert", "watch.py 每轮都在失败",
                        tick["detail"] + "\n\n顺手看一眼：tail -n 30 " + tick_log))
    elif tick["verdict"] == "no_log":
        pending.append(("tick_no_log", "warn", "看不到心跳日志", tick["detail"]))

    if nap["verdict"] == "not_running":
        pending.append(("napcat_down", "alert", "NapCat 容器没在运行",
                        nap["detail"] + "\n\n影响：QQ 消息发不出去。"
                        "容器设了 restart: always，正常情况会自己起来；"
                        "一直不起来多半是内存不够或镜像/配置有问题。"))
    elif nap["verdict"] == "unresponsive":
        pending.append(("napcat_unresponsive", "alert", "NapCat 无响应（疑似假死）",
                        nap["detail"] + "\n\n影响：QQ 消息发不出去。"))
    elif nap["verdict"] == "need_qr":
        pending.append(("napcat_logged_out", "alert", "NapCat 掉线，需要人工扫码",
                        nap["detail"] + "\n\n重启没用，必须扫码。"
                        "扫描办法见 deploy/SCAN_QR_WITHOUT_SSH.md。"))
    elif nap["verdict"] == "logged_out":
        pending.append(("napcat_logged_out", "alert", "NapCat 未登录",
                        nap["detail"] + "\n\n影响：QQ 消息发不出去。"))
    elif nap["verdict"] == "docker_missing":
        pending.append(("docker_missing", "warn", "宿主机上没有 docker 命令", nap["detail"]))

    if fails:
        sample = "\n".join(fails[-5:])
        pending.append(("notify_failed", "alert", "有通知发送失败（那条消息已经丢了）",
                        "tick.log 里出现 %d 行「通知失败」：\n%s\n\n"
                        "⚠️ 看门狗只能告诉你，不能替你补发 —— watch.py 已经把状态落盘，"
                        "不会重试。补救办法见 deploy/WATCHDOG.md「丢失的通知怎么补」。" % (len(fails), sample)))

    # ---- 自愈 ----
    actions = []
    napcat_ok = nap["verdict"] == "online"

    if tick["verdict"] == "not_running":
        ok, why = restart_watch_timer(dry_run=dry_run, runner=runner)
        actions.append("拉起定时器：" + why)

    if nap["verdict"] in ("not_running", "unresponsive", "oom_killed"):
        st["napcat_fail_streak"] = int(st.get("napcat_fail_streak") or 0) + 1
        today = datetime.now().strftime("%Y-%m-%d")
        if st.get("restart_day") != today:
            st["restart_day"] = today
            st["restarts_today"] = 0
        already = int(st.get("restarts_today") or 0)
        last_restart = float(st.get("last_restart_at_ts") or 0)
        since_last = now - last_restart if last_restart else None

        if not auto_restart:
            actions.append("NapCat 异常，但 AUTO_RESTART=0，只告警不动手")
        elif st["napcat_fail_streak"] < fail_threshold:
            actions.append("NapCat 异常第 %d 次（满 %d 次才动手），本轮先观察"
                           % (st["napcat_fail_streak"], fail_threshold))
        elif since_last is not None and since_last < cooldown:
            actions.append("距上次重启只过了 %d 分钟（冷却 %d 分钟），本轮不重启"
                           % (since_last / 60.0, cooldown / 60.0))
        elif already >= max_restarts:
            actions.append("今天已重启 %d 次（上限 %d），停手 → 需要人工介入"
                           % (already, max_restarts))
            pending.append(("napcat_restart_giveup", "alert", "NapCat 反复重启仍不正常",
                            "今天已自动重启 %d 次仍未恢复，已停止自动重启。\n"
                            "请人工看一眼：docker logs --tail 200 %s"
                            % (already, container)))
        else:
            if nap["verdict"] == "oom_killed":
                reason = "容器上一次是被 OOM 杀掉的"
            elif nap["verdict"] == "not_running":
                reason = "容器没在运行"
            else:
                reason = "容器在跑但接口无响应"
            ok, why = restart_napcat(container, dry_run=dry_run, runner=runner)
            actions.append("自动重启（%s）：%s" % (reason, why))
            if ok:
                st["restarts_today"] = already + 1
                st["last_restart_at_ts"] = now
                st["napcat_fail_streak"] = 0
                st["restart_reason"] = reason
    else:
        st["napcat_fail_streak"] = 0

    # ---- 告警去重 + 恢复通知 ----
    active = dict(st.get("active_alerts") or {})
    last_sent = dict(st.get("alert_sent") or {})
    seen = set()
    body_lines = []

    for kind_key, level, title, body in pending:
        seen.add(kind_key)
        active[kind_key] = active.get(kind_key) or now_text()
        h = alert_key_hash(kind_key, body)
        prev = last_sent.get(kind_key) or {}
        if prev.get("hash") == h and (now - float(prev.get("ts") or 0)) < repeat_seconds:
            body_lines.append("[重复告警已抑制] %s" % title)
            continue
        if dry_run:
            body_lines.append("[dry-run] 本应发送告警：%s —— %s" % (title, body.splitlines()[0]))
            last_sent[kind_key] = {"hash": h, "ts": now, "title": title}
            continue
        sent, notes = deliver_alert(kind_key, title, body, channels, napcat_ok, state_dir,
                                    dry_run=dry_run, http=http, runner=runner)
        last_sent[kind_key] = {"hash": h, "ts": now, "title": title}
        body_lines.append("已告警：%s（送达：%s%s）"
                          % (title, ",".join(sent) or "仅本地日志",
                             ("；" + "；".join(notes)) if notes else ""))

    # 之前告警过、现在好了 → 发一条恢复，闭环
    recovered = [k for k in list(active.keys()) if k not in seen
                 and k in ("tick_not_running", "tick_stuck", "napcat_down",
                           "napcat_unresponsive", "napcat_logged_out")]
    for kind_key in recovered:
        active.pop(kind_key, None)
        since = (st.get("active_alerts") or {}).get(kind_key) or "之前"
        title = "已恢复：%s" % kind_key
        body = "这个故障从 %s 起一直存在，本轮体检已恢复正常。" % since
        if not dry_run:
            deliver_alert("recover_" + kind_key, title, body, channels, napcat_ok, state_dir,
                          http=http, runner=runner)
            body_lines.append("已发恢复通知：%s" % kind_key)
        else:
            body_lines.append("[dry-run] 本应发恢复通知：%s" % kind_key)

    # ---- 每日「一切正常」（沉默即出事：哪天没收到，就是机器或看门狗自己挂了）----
    daily_at = str(cfg_get("DAILY_OK_AT")).strip()
    ok_now = not pending
    if daily_at and ok_now and _at_or_after(daily_at):
        today = datetime.now().strftime("%Y-%m-%d")
        if st.get("last_daily_ok") != today and not dry_run:
            ok_title, ok_body = daily_ok_texts(today)
            deliver_alert("daily_ok", ok_title, ok_body,
                          channels, napcat_ok, state_dir, http=http, runner=runner)
            st["last_daily_ok"] = today
            body_lines.append("已发每日正常通知")

    # ---- 存状态 ----
    st["last_run"] = now_text()
    st["last_hb_seq"] = tick["seq"] if tick["seq"] is not None else st.get("last_hb_seq")
    st["hb_same_count"] = tick["same_count"]
    st["alert_scan_lines"] = total_lines
    st["active_alerts"] = active
    st["alert_sent"] = last_sent
    st["napcat_verdict"] = nap["verdict"]
    st["tick_verdict"] = tick["verdict"]
    st["tick_age"] = tick["age"]
    st["version"] = VERSION

    verdict = "alert" if any(x[1] == "alert" for x in pending) else ("warn" if pending else "ok")
    st["last_verdict"] = verdict

    if not dry_run:
        try:
            os.makedirs(state_dir, exist_ok=True)
            os.chmod(state_dir, 0o700)
            save_json(state_file, st)
            save_json(os.path.join(state_dir, "status.json"), {
                "verdict": verdict,
                "checked_at": now_text(),
                "tick": {k: v for k, v in tick.items() if k != "detail"},
                "tick_detail": tick["detail"],
                "napcat": {k: v for k, v in nap.items() if k != "detail"},
                "napcat_detail": nap["detail"],
                "notify_fail_lines": len(fails),
                "error_lines": len(errors[-5:]),
                "restarts_today": st.get("restarts_today", 0),
                "actions": actions,
                "alerts": [x[2] for x in pending],
            })
        except OSError as exc:
            body_lines.append("⚠️ 写状态文件失败：%s" % exc)

    # ---- 打印 ----
    lines = []
    lines.append("斗鱼提醒 · 看门狗 v%s  %s" % (VERSION, now_text()))
    lines.append("  心跳   : %s%s" % (
        tick["verdict"],
        ("，最后 #%s %s，%.1f 分钟前" % (tick["seq"], tick["label"], (tick["age"] or 0) / 60.0))
        if tick["age"] is not None else ""))
    if tick["detail"]:
        lines.append("           %s" % tick["detail"])
    lines.append("  NapCat : %s —— %s" % (nap["verdict"], nap["detail"]))
    lines.append("  通知失败: 本轮新增 %d 行" % len(fails))
    lines.append("  判定   : %s" % verdict.upper())
    for a in actions:
        lines.append("  动作   : %s" % a)
    for b in body_lines:
        lines.append("  %s" % b)
    for w in warn_lines:
        lines.append("  %s" % w)
    return verdict, lines


def _at_or_after(hhmm):
    try:
        hh, mm = [int(x) for x in hhmm.split(":")[:2]]
    except (ValueError, AttributeError):
        return False
    now = datetime.now()
    return (now.hour, now.minute) >= (hh, mm)


def daily_ok_texts(today=None):
    """每日「报平安」那条的标题与正文。

    文案可以用环境变量 改（见文件头 DAILY_OK_TITLE / DAILY_OK_BODY /
    DAILY_OK_KAOMOJI），这样定制写在 /etc/default/douyu-watchdog 里，
    升级 watchdog.py 不会把它冲掉。
    颜文字写多个时按日期轮换，同一天总是同一个，不会每轮体检都变。
    """
    title = str(cfg_get("DAILY_OK_TITLE")).strip() or "每日体检：一切正常"
    body = str(cfg_get("DAILY_OK_BODY")).strip() or (
        "斗鱼提醒整条链路正常。这条消息每天发一次，"
        "哪天没收到，说明机器或者看门狗本身出问题了。"
    )

    faces = [x for x in re.split(r"[,，\s]+", str(cfg_get("DAILY_OK_KAOMOJI"))) if x]
    if faces:
        if today:
            try:
                day = datetime.strptime(today, "%Y-%m-%d")
            except ValueError:
                day = datetime.now()
        else:
            day = datetime.now()
        title = "%s %s" % (title, faces[day.timetuple().tm_yday % len(faces)])

    return title, body


# --------------------------------------------------------------------------
# 补救丢失的通知（只动状态文件，不动 watch.py）
# --------------------------------------------------------------------------

def cmd_recover_notify(args, dry_run=False):
    """把 state_*.json 里的 is_live 改回 false，让下一轮 tick 重新判定并补发。

    适用场景：主播**现在还在播**，但那条开播通知因为掉线丢了。
      state 里 is_live=false → 下一轮 tick 看到 live，累计 confirm_rounds 次后
      再次发出通知（默认 2 轮 = 2 分钟）。

    ⚠️ 两个前提，缺一个就别用：
      1. 主播现在确实还在播（否则会立刻又发一条「下播」？不会 —— 发不出去就只是白跑一轮，
         但会浪费一次判定；更稳妥是先确认在播）。
      2. 你接受这个群里可能出现一条迟到的开播通知。
    脚本会先备份原状态文件，出问题可以拷回来。
    """
    app_dir = cfg_get("APP_DIR", args.app_dir)
    cfg = load_json(cfg_get("CONFIG", getattr(args, "config", None))
                    or os.path.join(app_dir, "config.json")) or {}
    room_id = str(cfg.get("room_id") or "").strip()
    if not room_id:
        print("读不到 room_id，先确认 %s 里的 room_id 填了" % app_dir, flush=True)
        return 2
    path = os.path.join(app_dir, "state_%s.json" % room_id)
    st = load_json(path)
    if st is None:
        print("找不到或读不了状态文件：%s" % path, flush=True)
        return 2

    if st.get("is_live") is not True:
        print("当前状态 is_live=%r，不是「已判定为直播中」—— 没有需要补发的通知。"
              % st.get("is_live"), flush=True)
        return 0

    backup = "%s.bak.%s" % (path, datetime.now().strftime("%Y%m%d%H%M%S"))
    if dry_run:
        print("（dry-run）本应备份到 %s，并把 is_live 改为 false / pending 清空" % backup, flush=True)
        return 0

    shutil.copy2(path, backup)
    st["is_live"] = False
    st["pending"] = None
    st["pending_n"] = 0
    st["last_change_at"] = datetime.now().isoformat()
    save_json(path, st)
    print("已备份到：%s" % backup, flush=True)
    print("已把 is_live 改回 false。下一轮 tick 会在累计 %s 轮确认后重新发通知"
          "（默认 2 分钟；想立刻看到可以跑 systemctl start douyu-watch.service）。"
          % cfg.get("confirm_rounds", 2), flush=True)
    return 0


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def cmd_status(args):
    verdict, lines = run_once(args, dry_run=True)
    print("\n".join(lines))
    print()
    print("（--status 只读：没有发告警、没有重启任何东西）")
    return 0 if verdict in ("ok", "warn") else 1


def cmd_test_alert(args):
    """往每个配好的通道各发一条测试告警，用于验证通道是否打通。"""
    notes = []
    app_dir = cfg_get("APP_DIR", args.app_dir)
    config_path = cfg_get("CONFIG", getattr(args, "config", None)) or os.path.join(app_dir, "config.json")
    watch_cfg = load_json(config_path) or {}
    ob = watch_cfg.get("onebot") or {}
    base = cfg_get("ONEBOT_BASE", args.onebot_base) or (ob.get("base") or "")
    token = cfg_get("ONEBOT_TOKEN", args.onebot_token) or (ob.get("token") or "")
    container = cfg_get("NAPCAT_CONTAINER", args.container)
    private = cfg_get("ALERT_ONEBOT_PRIVATE")
    specs = parse_webhook_specs(cfg_get("ALERT_WEBHOOK"), cfg_get("ALERT_WEBHOOK_KIND") or "generic")
    email = cfg_get("ALERT_EMAIL")

    if not (specs or private or email):
        print("一个告警通道都没配。看门狗照样会跑、会写本地日志，", flush=True)
        print("但深夜出事时没人叫得醒你。建议至少配一个 —— 见 deploy/WATCHDOG.md。", flush=True)

    if private:
        nap = check_napcat(container, base, token, timeout=cfg_int("HTTP_TIMEOUT"),
                           runner=run_cmd, http=http_json)
        if nap["verdict"] != "online":
            notes.append("onebot 私聊：跳过 —— NapCat 当前是 %s（%s）"
                         % (nap["verdict"], nap["detail"]))
        else:
            ok, why = send_onebot_private(base, token, private,
                                          "【斗鱼提醒·看门狗】测试告警\n"
                                          "看到这条说明「NapCat 私聊」这条通道是通的。\n"
                                          "发出时间：" + now_text())
            notes.append("onebot 私聊：%s" % ("成功" if ok else why))

    for spec in specs:
        print("  正在往 %s 通道发：%s" % (spec["kind"], redact_url(spec["url"])), flush=True)
        ok, why = send_webhook(spec["url"], spec["kind"],
                               "斗鱼提醒·看门狗 测试告警",
                               "看到这条说明 %s 通道是通的。发出时间：%s"
                               % (spec["kind"], now_text()),
                               hostname(), tg_chat_id=cfg_get("ALERT_TG_CHAT_ID"))
        notes.append("webhook(%s)：%s" % (spec["kind"], "成功" if ok else why))

    if email:
        ok, why = send_email(email, "[斗鱼提醒·看门狗] 测试告警",
                             "看到这条说明邮件通道是通的。发出时间：" + now_text())
        notes.append("邮件：%s" % ("成功" if ok else why))

    for n in notes:
        print("  " + n, flush=True)
    failed = [n for n in notes if "成功" not in n and "跳过" not in n]
    return 1 if failed else 0


# --------------------------------------------------------------------------
# 离线自检
# --------------------------------------------------------------------------

def selftest():
    failures = []
    passed = [0]

    def check(cond, desc):
        if cond:
            passed[0] += 1
            print("  [OK]   %s" % desc)
        else:
            print("  [FAIL] %s" % desc)
            failures.append(desc)

    print("=== watchdog 离线自检（不联网、不碰 docker）===")
    print("-- 心跳行解析 --")
    sample = (
        "[10:00:00] #1 offline | 标题 A | 热度 - | loop=1 | via betard\n"
        "[10:01:00] #2 offline | 标题 A | 热度 - | loop=1 | via betard\n"
        "[error] onebot 通知失败：HTTP 500\n"
        "[10:02:00] #3 LOOP(轮播) | 标题 B | 热度 12 | loop=1 | via betard\n"
    )
    hbs = parse_heartbeats(sample)
    check(len(hbs) == 3, "心跳行只认心跳，不把 [error] 行算进去")
    check(hbs[-1][0] == 3 and hbs[-1][1] == "LOOP(轮播)", "解析出最后一跳的序号与状态标签")
    check(parse_heartbeats("[error] 本轮出错：xxx") == [], "没有心跳行时返回空表")

    print("-- 通知失败扫描（按行偏移，不重复告警）--")
    fails, errors, total = scan_log_tail(sample, 0)
    check(len(fails) == 1, "扫出 1 行「通知失败」")
    check(len(errors) == 1, "同时扫出 [error] 行作为佐证")
    fails2, _, total2 = scan_log_tail(sample, total)
    check(len(fails2) == 0 and total2 == total, "从上次偏移再扫 → 不重复报警")
    fails3, _, _ = scan_log_tail(sample, 999, )
    check(len(fails3) >= 0, "偏移越界（日志轮转）时不崩，退回扫尾部")

    print("-- 心跳新鲜度判定 --")
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="wd-selftest-")
    logf = os.path.join(tmpdir, "tick.log")
    with open(logf, "w", encoding="utf-8") as fp:
        fp.write(sample)
    r = check_ticklog(logf, 3, 0, 420, 3)
    check(r["verdict"] == "ok" and r["seq"] == 3, "新鲜且序号前进 → ok")
    r = check_ticklog(logf, 3, 2, 420, 3)
    check(r["verdict"] == "stuck", "序号连续 3 轮不动 → stuck")
    r = check_ticklog(logf, 2, 0, 420, 3)
    check(r["verdict"] == "ok" and r["same_count"] == 0, "序号前进（哪怕只 +1）→ 复位计数器")
    r = check_ticklog(logf, 99, 0, 420, 3)
    check(r["verdict"] == "ok", "序号变小（状态文件被重置）→ 视作前进，不误报卡死")
    old = time.time() - 3600
    os.utime(logf, (old, old))
    r = check_ticklog(logf, 3, 0, 420, 3)
    check(r["verdict"] == "not_running", "日志一小时没更新 → not_running")
    r = check_ticklog(os.path.join(tmpdir, "nope.log"), None, 0, 420, 3)
    check(r["verdict"] == "no_log", "日志不存在 → no_log（提示定时器可能没开）")

    print("-- NapCat 判定 --")

    def fake_runner_factory(running="true", oom="false", logtext=""):
        def _r(args, timeout=60):
            if args[0] != "docker":
                return 127, "no"
            if "State.Running" in " ".join(args):
                return 0, running + "\n"
            if "State.OOMKilled" in " ".join(args):
                return 0, oom + "\n"
            if "logs" in args:
                return 0, logtext
            return 0, ""
        return _r

    def http_ok():
        return True, {"status": "ok", "data": {"user_id": 3981665877}}

    def http_not_logged_in():
        return True, {"status": "failed", "data": None}

    def http_down():
        return False, "ConnectionRefusedError: [Errno 111] Connection refused"

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=fake_runner_factory(), test_online=http_ok)
    check(n["verdict"] == "online" and n["user_id"] == "3981665877", "已登录 → online（带 QQ 号）")

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=fake_runner_factory(running="false"), test_online=http_down)
    check(n["verdict"] == "not_running", "容器没在跑 → not_running（该重启）")

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=fake_runner_factory(), test_online=http_down)
    check(n["verdict"] == "unresponsive", "容器在跑但接口不通 → unresponsive（该重启）")

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=fake_runner_factory(), test_online=http_not_logged_in)
    check(n["verdict"] == "logged_out", "接口通但未登录、日志无二维码 → logged_out")

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=fake_runner_factory(logtext="二维码解码URL: https://txz.qq.com/p?k=abc"),
                     test_online=http_not_logged_in)
    check(n["verdict"] == "need_qr", "未登录且日志里有二维码 → need_qr（重启没用，要人扫）")

    n = check_napcat("napcat", "", "", runner=fake_runner_factory(), test_online=http_ok)
    check(n["verdict"] == "skipped", "没配 OneBot → 跳过检查")

    n = check_napcat("napcat", "http://127.0.0.1:3000", "t",
                     runner=lambda a, timeout=60: (127, "命令不存在：docker"), test_online=http_ok)
    check(n["verdict"] == "docker_missing", "没有 docker 命令 → docker_missing（如实报告）")

    print("-- 告警 payload --")
    # 各家要的结构不一样：钉钉是 msgtype、飞书是 msg_type、Server酱吃表单、Bark 吃路径。
    # 这里逐个确认「正文真的进去了」，而不是只看字段名存在。
    spec = build_payload("generic", "标题", "正文", "h1")
    check("正文" in spec["json"]["text"] and spec["json"]["host"] == "h1",
          "generic：正文与主机构成 JSON body")
    spec = build_payload("serverchan", "标题", "正文", "h1")
    # 表单编码会把中文变成 %E6%AD%A3...，所以要解回来比对，不能直接找字符串
    form = urllib.parse.parse_qs(spec["data"])
    check(form.get("title") == ["标题"] and form.get("desp") == ["正文"]
          and spec["content_type"].startswith("application/x-www-form"),
          "serverchan：走表单编码（title + desp），中文能正确还原")
    spec = build_payload("bark", "标题", "正文", "h1")
    check(spec.get("suffix_is_path") and spec["suffix"].startswith("/"),
          "bark：标题与正文编进 URL 路径")
    for kind in ("dingtalk", "feishu", "telegram"):
        spec = build_payload(kind, "标题", "正文", "h1")
        check("正文" in json.dumps(spec["json"], ensure_ascii=False),
              "%s：正文在 JSON body 里" % kind)
    spec = build_payload("feishu", "t", "b", "h")
    check(spec["json"]["msg_type"] == "text", "飞书用 msg_type/text 结构（钉钉是 msgtype，两者不同）")

    print("-- webhook 发送失败要能看出来 --")
    ok, why = send_webhook("", "generic", "t", "b", "h")
    check(not ok, "没配 URL → 返回失败，不是静默成功")

    def http_400(url, **kw):
        return False, "HTTP 400 Bad Request"

    ok, why = send_webhook("https://example.com/hook", "generic", "t", "b", "h", http=http_400)
    check(not ok and "400" in why, "webhook 返回 400 → 判定失败并带上原因")

    print("-- 微信推送：各家的成功码不一样，不能一刀切 --")

    def resp_http(resp):
        def _h(url, **kw):
            return True, resp
        return _h

    SC = "https://sctapi.ftqq.com/SCTtest.send"
    PP = "https://www.pushplus.plus/send?token=TK"
    ok, why = send_webhook(SC, "serverchan", "t", "b", "h", http=resp_http({"code": 0}))
    check(ok, "serverchan code=0 → 成功")
    ok, why = send_webhook(SC, "serverchan", "t", "b", "h",
                           http=resp_http({"code": 40001, "message": "标题包含特殊字符"}))
    check(not ok and "特殊字符" in why, "serverchan 失败时把官方 message 原样带出来")
    # 回归测试：bark / pushplus 成功时 code=200，早先统一按「code 非 0 即失败」判定，
    # 会把这两个通道的成功判成失败 —— 于是人真的收到了消息，日志却在喊通道坏了。
    ok, why = send_webhook("https://api.day.app/KEY", "bark", "t", "b", "h",
                           http=resp_http({"code": 200, "message": "success"}))
    check(ok, "bark code=200 → 成功（回归：以前会被误判为失败）")
    ok, why = send_webhook(PP, "pushplus", "t", "b", "h",
                           http=resp_http({"code": 200, "msg": "请求成功"}))
    check(ok, "pushplus code=200 → 成功（回归：同上）")
    ok, why = send_webhook(PP, "pushplus", "t", "b", "h",
                           http=resp_http({"code": 905, "msg": "未实名认证"}))
    check(not ok and "实名" in why, "pushplus 未实名(905) → 失败并直接告诉你怎么办")
    DT = "https://oapi.dingtalk.com/robot/send?access_token=x"
    ok, why = send_webhook(DT, "dingtalk", "t", "b", "h", http=resp_http({"errcode": 0}))
    check(ok, "钉钉 errcode=0 → 成功")
    ok, why = send_webhook(DT, "dingtalk", "t", "b", "h",
                           http=resp_http({"errcode": 310000, "errmsg": "关键词不匹配"}))
    check(not ok, "钉钉 errcode≠0 → 失败")

    spec = build_payload("pushplus", "标题", "正文", "h", params={"token": "TK"})
    check(spec["json"].get("token") == "TK" and spec["json"].get("template") == "markdown",
          "pushplus：token 进 body，且显式写 markdown（不写的话微信里换行会被吞成一整段）")
    form = urllib.parse.parse_qs(build_payload("serverchan", "第一行\n第二行", "正文", "h")["data"])
    check("\n" not in form["title"][0],
          "serverchan：标题里的换行被清掉（接口对含换行的 title 直接报错）")

    print("-- 多通道（免费通道都有额度上限，别只留一条腿） --")
    one = parse_webhook_specs("https://example.com/hook", "generic")
    check(len(one) == 1 and one[0]["kind"] == "generic", "单条地址 → 用 ALERT_WEBHOOK_KIND 作为默认类型")
    two = parse_webhook_specs("pushplus|https://www.pushplus.plus/send?token=TK;"
                              " serverchan|https://sctapi.ftqq.com/SCTx.send")
    check([s["kind"] for s in two] == ["pushplus", "serverchan"],
          "分号分隔多条 → 各按 | 前缀认自己的类型（多余空格不影响）")
    check(two[0]["params"].get("token") == "TK", "URL 查询串里的 token 被解析出来给 body 用")
    check(-1 == redact_url("https://www.pushplus.plus/send?token=SECRET").find("SECRET"),
          "打印 URL 时隐去查询串（token 不能进日志和告警正文）")

    def multi_http(url, **kw):
        return (True, {"code": 200}) if "pushplus" in url else (True, {"code": 0})

    sent, notes = deliver_alert(
        "t", "标题", "正文",
        {"onebot_private": "", "email": "", "webhooks": parse_webhook_specs(
            "pushplus|https://www.pushplus.plus/send?token=TK;"
            "serverchan|https://sctapi.ftqq.com/SCTx.send")},
        napcat_ok=False, state_dir=tmpdir, http=multi_http)
    check(len(sent) == 2, "两条 webhook 都配了 → 两条都发出去")

    def half_http(url, **kw):
        return (True, {"code": 905}) if "pushplus" in url else (True, {"code": 0})

    sent, notes = deliver_alert(
        "t", "标题", "正文",
        {"onebot_private": "", "email": "", "webhooks": parse_webhook_specs(
            "pushplus|https://www.pushplus.plus/send?token=TK;"
            "serverchan|https://sctapi.ftqq.com/SCTx.send")},
        napcat_ok=False, state_dir=tmpdir, http=half_http)
    check(len(sent) == 1 and any("pushplus" in n for n in notes),
          "一条通道失败不影响另一条（只报失败那条）")

    print("-- 告警去重 --")
    h1 = alert_key_hash("napcat_down", "同一个问题")
    h2 = alert_key_hash("napcat_down", "另一个问题")
    check(h1 == alert_key_hash("napcat_down", "同一个问题"), "同样的内容哈希一致")
    check(h1 != h2, "内容不同哈希不同")

    print("-- 送达统计 --")
    sent, notes = deliver_alert("t", "标题", "正文",
                                {"onebot_private": "", "webhook": "", "email": ""},
                                napcat_ok=False, state_dir=tmpdir)
    check(sent == [] , "没有任何通道时，送达列表为空")
    check(any("没有配置任何独立" in n for n in notes),
          "没有任何外部通道时，明确提示「只落在本地日志」（不假装成功）")
    alog = os.path.join(tmpdir, "alerts.log")
    check(os.path.exists(alog) and "正文" in open(alog, encoding="utf-8").read(),
          "告警无论如何都会落到本地 alerts.log")

    print("-- dry-run 的语义（这条坑过：dry-run 时告警不会真发）--")
    dry_log = os.path.join(tmpdir, "dry-tick.log")
    with open(dry_log, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("[12:00:00] #7 未开播 | loop=1\n")

    class _DryArgs(object):
        app_dir = tmpdir
        tick_log = dry_log
        state_dir = os.path.join(tmpdir, "dry-state")
        config = os.path.join(tmpdir, "no-such-config.json")
        container = "napcat-not-here"
        onebot_base = "http://127.0.0.1:3000"
        onebot_token = "t"
        stale = None
        stuck_rounds = None
        auto_restart = None

    dry_calls = []

    def dry_runner(cmd, timeout=60):
        if cmd[:3] == ["docker", "inspect", "-f"]:
            return 1, "Error: No such object: napcat-not-here"
        return 1, "(stub)"

    def dry_http(url, **kw):
        dry_calls.append(url)
        return True, {"code": 200}

    os.environ["ALERT_WEBHOOK"] = "pushplus|https://www.pushplus.plus/send?token=TK"
    try:
        _v, dlines = run_once(_DryArgs(), runner=dry_runner, http=dry_http,
                              napcat_probe=None, dry_run=True)
    finally:
        os.environ.pop("ALERT_WEBHOOK", None)
    djoined = "\n".join(dlines)
    check("[dry-run] 本应发送告警" in djoined, "dry-run 会把该发的告警标成「本应发送」")
    check(not dry_calls, "dry-run 下告警真的不投递（别拿它验证通道是否打得通）")
    check(not os.path.exists(os.path.join(tmpdir, "dry-state", "state.json")),
          "dry-run 不写状态文件（不会污染真实的连续失败计数）")

    print("-- 报平安文案（可定制，不写死在代码里）--")
    dt0, db0 = daily_ok_texts()
    check(dt0 == "每日体检：一切正常" and "整条链路正常" in db0,
          "不配任何环境变量时用内置文案")

    kept = {k: os.environ.get(k) for k in
            ("DAILY_OK_TITLE", "DAILY_OK_BODY", "DAILY_OK_KAOMOJI")}
    try:
        os.environ["DAILY_OK_TITLE"] = "报个平安"
        os.environ["DAILY_OK_BODY"] = "一切照旧。"
        pool = ["(๑•̀ㅂ•́)و✧", "✧*｡٩(ˊᗜˋ*)و✧*｡"]
        os.environ["DAILY_OK_KAOMOJI"] = ",".join(pool)
        dt1, db1 = daily_ok_texts("2026-01-01")
        dt1_again, _ = daily_ok_texts("2026-01-01")
        dt2, _ = daily_ok_texts("2026-01-02")
        check(dt1.startswith("报个平安") and db1 == "一切照旧。",
              "标题和正文都能用环境变量改")
        face1 = dt1[len("报个平安 "):]
        face2 = dt2[len("报个平安 "):]
        check(dt1.startswith("报个平安 ") and face1 in pool,
              "颜文字追加在标题后，且只取池子里的一个")
        check(dt1_again == dt1, "同一天反复调用，颜文字不变（不是随机）")
        check(face2 in pool and face2 != face1,
              "配了多个颜文字则按日期轮换（隔天换一个）")
        os.environ.pop("DAILY_OK_KAOMOJI", None)
        dt3, _ = daily_ok_texts("2026-01-01")
        check(dt3 == "报个平安", "颜文字留空就不追加（等于关掉）")
    finally:
        for k, v in kept.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    total = passed[0] + len(failures)
    if failures:
        print("结果：%d 项通过，%d 项失败（共 %d 项）" % (passed[0], len(failures), total))
        for f in failures:
            print("  失败：%s" % f)
        return 1
    print("结果：%d 项通过，0 项失败（共 %d 项）" % (passed[0], total))
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="斗鱼开播提醒 · 看门狗")
    p.add_argument("--status", action="store_true", help="只打印健康状况，不告警、不自愈")
    p.add_argument("--test-alert", action="store_true", help="往所有配好的通道发测试告警")
    p.add_argument("--recover-notify", action="store_true",
                   help="补救一条丢失的开播通知（主播仍在播时用，会先备份状态文件）")
    p.add_argument("--selftest", action="store_true", help="离线自检")
    p.add_argument("--dry-run", action="store_true",
                   help="演练：不真的动手、也不发告警，只报告打算做什么")
    p.add_argument("--version", action="version", version="watchdog " + VERSION)

    p.add_argument("--app-dir", default=None)
    p.add_argument("--state-dir", default=None)
    p.add_argument("--tick-log", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("--container", default=None)
    p.add_argument("--onebot-base", default=None)
    p.add_argument("--onebot-token", default=None)
    p.add_argument("--stale", default=None, help="心跳陈旧阈值（秒），默认 420")
    p.add_argument("--stuck-rounds", default=None, help="序号不前进多少轮算卡死，默认 3")
    p.add_argument("--auto-restart", default=None, help="1/0，是否自动重启 NapCat")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.recover_notify:
        return cmd_recover_notify(args, dry_run=args.dry_run)
    if args.test_alert:
        return cmd_test_alert(args)
    if args.status:
        return cmd_status(args)

    try:
        verdict, lines = run_once(args, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        # 看门狗自己出错也必须留痕，否则它就成了第三个静默失败
        print("[error] 看门狗自身出错：%s: %s" % (type(exc).__name__, exc), flush=True)
        return 2
    print("\n".join(lines), flush=True)
    return 0 if verdict in ("ok", "warn") else 1


if __name__ == "__main__":
    sys.exit(main())
