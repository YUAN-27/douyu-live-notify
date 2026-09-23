#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斗鱼直播间开播提醒 —— 单个 Python 文件，只用标准库。

监控**任意一个**斗鱼房间；主播真人开播时，通过 OneBot（NapCat）或 QQ 官方机器人
把提醒推送到 QQ 群 / 私聊，也可以只打本地日志。

用法
----
  cp config.example.json config.json     # 先填 room_id 和推送目标
  python watch.py --probe 12345          # 体检：两个接口各打一次，打印原始数据
  python watch.py --once                 # 读一次当前状态（不入库、不通知）
  python watch.py --tick                 # 只检查一轮就退出（给 cron / 青龙 / 云函数用）
  python watch.py --test-notify          # 往配置的通道发一条测试消息（部署完先跑这个）
  python watch.py                        # 正式值守，按 config.json 的间隔轮询

依赖：仅标准库。Python 3.9+ 即可。

关于轮播（重要）
----------------
`show_status == 1` 不等于「真人在播」—— 主播下播后房间常常转入**轮播**
（循环放录播、无人直播），此时 `show_status` 仍是 1，而 `videoLoop` 变成 1。

判断依据（实测对照，不是猜的）：
  * 斗鱼「正在直播」列表里抽样的在播房间，`videoLoop` **全为 0**
  * 某房间处于轮播时 `videoLoop == 1`，且**完全不出现在直播列表里**

所以 `treat_loop_as_live` 默认 False —— 轮播不算开播，
免得主播明明下播了你还一直收到「开播了」。
每轮心跳日志都会打印 `loop=` 值，方便你日后自己复验。

关于 room_id（重要，新手最容易踩的坑）
--------------------------------------
斗鱼房间有**两个号**：
  * **靓号**：给人看的短号，例如 https://www.douyu.com/12345
  * **真实 room_id**：接口查询必须用这个

靓号 ≠ room_id，两者经常不一样。举个真实例子：浏览器里打开 `douyu.com/6657`
能正常看到直播间，但接口能查到的 room_id 是 `6979222`；
直接查 `betard/6657` 返回的是「房间已被关闭」的 HTML 报错页。
更坑的是，斗鱼上可能存在另一个真号恰好是 `6657` 的**别人的房间** ——
填错就会安静地监控到不相干的直播间。

**怎么找到真实 room_id**（二选一）：
  1. 打开直播间页面 → 查看网页源代码 → 搜索 `room_id`，取那个数字
  2. 用本脚本试：`python watch.py --probe <你猜的号>`
     能打印出主播名、房间标题就说明对了；返回报错页说明这是靓号或房间不存在

配置里请填真实 room_id。`--probe` 只做体检，不读配置。

关于「发送失败」（重要）
------------------------
通知发失败**不等于没事**。以前的做法是：失败只打一行 `[error]`，而状态照样落盘，
于是下一轮「无状态切换」→ 永远不会再发 → **那条通知永久丢失**。
2026-09-23 实盘就是这么丢掉一条开播提醒的（NapCat 的 QQ 登录态半死，
接口还答着 `ok`，消息却发不出去）。

现在：失败的正文会连同重试次数写进状态文件的 `notify` 字段，之后每轮自动补发，
间隔按 1、2、4、8… 分钟退避，直到送出去或到达上限
（`notify_retry_max` 次 / `notify_retry_max_age_hours` 小时）。
补发成功会打 `[ok]`，一直失败会打 `[warn]`，到顶放弃会打 `[error]`。

两个容易搞错的地方：
  * **console 通道的成功不算「送达」**（`counts_as_delivery = False`）。
    否则 `channels: ["onebot", "console"]` 里 console 永远成功，
    onebot 发不出去也会被判成「已推送」，重试永不触发。
  * `--tick` 的 `判定：...` 那行现在**如实**区分「已推送 / 未送出待补发」。
    以前不管有没有发出去都写「已推送」，日志撒谎比没日志更坏。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")

DEFAULT_CFG = {
    # 斗鱼房间的真实 room_id（不是靓号）—— 必填，见文件头「关于 room_id」
    "room_id": "",
    # 推送消息里展示用的链接。留空会自动用 room_id 拼；
    # 想让消息里显示靓号短链，就填这里，例如 "https://www.douyu.com/6657"
    "room_url": "",
    # 轮询间隔（秒）。斗鱼接口没有严格限频，但不要低于 20 秒，没必要也不礼貌
    "interval_seconds": 45,
    # 每次轮询的随机抖动上限（秒），避免整点齐刷刷请求
    "jitter_seconds": 8,
    # 连续 N 次读到同一状态才认定状态切换，防接口抖动误报
    "confirm_rounds": 2,
    # 是否推送“下播”通知
    "notify_on_end": False,
    # 把「视频轮播」（无人直播、循环放录播）也当作开播。
    # 实测结论：videoLoop == 1 就是「房间在放轮播、不是真人直播」。
    #   对照证据 1：斗鱼「正在直播」列表里抽样的在播房间，videoLoop 全为 0
    #   对照证据 2：房间轮播时 videoLoop == 1，且完全不出现在直播列表里
    # 所以默认 false —— 轮播不算开播，避免主播下播后一直被误报「开播了」。
    "treat_loop_as_live": False,
    # 每轮都打一行心跳日志，便于确认程序还活着
    "log_heartbeat": True,
    # 通知通道：console / onebot / qq_official，可多选
    "channels": ["console"],
    # ---- 发送失败要不要重试（重要，默认开）----
    # 关掉的话，那条通知就**永久丢失**：发送失败只打一行 [error]，而状态照样落盘，
    # 下一轮因为「无状态切换」不会再发第二次。2026-09-23 实盘踩到过这个坑。
    "retry_failed_notify": True,
    # 最多尝试几次（含首次）。到顶就放弃，并留一条 [error] 交给人处理
    "notify_retry_max": 30,
    # 超过这么多小时还没送出去也放弃，防止无限重试
    "notify_retry_max_age_hours": 6,
    # 重试间隔按 1、2、4、8… 分钟翻倍退避，上限这么多分钟。
    # 退避是为了 NapCat 挂掉时别每分钟都去撞一次、把日志刷满
    "notify_retry_backoff_cap_minutes": 10,
    "onebot": {
        "base": "http://127.0.0.1:3000",
        "token": "",
        "target_type": "group",   # group（群聊）或 private（私聊）
        "target_id": "123456789",
        # 群聊里是否 @全体成员。默认 False —— 见 README「关于 @全体成员」一节，那不划算
        "at_all": False,
        # 想 @ 特定的人，填 QQ 号数组，例如 ["10001", "10002"]
        "at_users": []
    },
    "qq_official": {
        "app_id": "",
        "client_secret": "",
        # 二选一：群聊填 group_openid，单聊填 user_openid
        "target_type": "group",   # group 或 c2c
        "group_openid": "",
        "user_openid": ""
    }
}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

# 本机地址一律直连，绝不走代理。
# 原因：如果环境里设了 HTTP_PROXY / HTTPS_PROXY（国内服务器和容器里很常见），
# Python 的 urllib 会把 http://127.0.0.1:3000 这种 OneBot 地址也塞进代理，
# 表现为 502 Bad Gateway 或 URLError，排查起来非常费劲。实测踩到过。
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_local_url(url):
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS or host.startswith("127.")


def http_request(url, headers=None, data=None, timeout=15, method=None):
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity", "Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    opener = _NO_PROXY_OPENER if _is_local_url(url) else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def http_get_text(url, headers=None, timeout=15):
    return http_request(url, headers=headers, timeout=timeout)


def get_json(url, headers=None, timeout=15):
    text = http_get_text(url, headers=headers, timeout=timeout).strip()
    if not text.startswith("{") and not text.startswith("["):
        raise ValueError("响应不是 JSON（多半被反爬拦了或房间号不存在）：" + text[:120].replace("\n", " "))
    return json.loads(text)


# --------------------------------------------------------------------------
# 斗鱼状态探测：两个接口，任一个成功即可
# --------------------------------------------------------------------------

def probe_betard(room_id):
    """主接口。斗鱼网页端自己在用，字段最全。实测需要带 Referer。"""
    url = "https://www.douyu.com/betard/" + str(room_id)
    payload = get_json(url, headers={"Referer": "https://www.douyu.com/" + str(room_id)})
    room = payload.get("room")
    if not room:
        raise ValueError("betard 返回里没有 room 字段")

    loop_flag = room.get("videoLoop")
    if loop_flag is None:
        loop_flag = (room.get("room_biz_all") or {}).get("videoLoop")

    show_time = room.get("show_time")

    return {
        "source": "betard",
        "is_live": int(room.get("show_status") or 0) == 1,
        "room_id": str(room.get("room_id") or room_id),
        "anchor": room.get("nickname") or room.get("owner_name") or "",
        "title": room.get("room_name") or "",
        "online": None,
        "start_time": ts_to_cst(show_time) if isinstance(show_time, (int, float)) else None,
        "loop_flag": loop_flag,
        "raw": room,
    }


def probe_legacy(room_id):
    """备用接口（老 RoomApi）。字段少但稳定，且带热度值。"""
    url = "https://open.douyucdn.cn/api/RoomApi/room/" + str(room_id)
    payload = get_json(url, headers={"Referer": "https://www.douyu.com/" + str(room_id)})
    d = payload.get("data") or {}
    room_status = str(d.get("room_status") or "0")
    online = d.get("online") or 0
    return {
        "source": "legacy",
        "is_live": room_status == "1",
        "room_id": str(d.get("room_id") or room_id),
        "anchor": d.get("owner_name") or "",
        "title": d.get("room_name") or "",
        "online": online if isinstance(online, int) else None,
        "start_time": d.get("start_time"),
        "loop_flag": None,
        "raw": d,
    }


def ts_to_cst(ts):
    return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M:%S")


def read_state(room_id, methods=(probe_betard, probe_legacy)):
    errors = []
    for probe in methods:
        try:
            return probe(room_id)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s -> %s" % (probe.__name__, exc))
    raise RuntimeError("所有探测接口都失败了：\n  " + "\n  ".join(errors))


# --------------------------------------------------------------------------
# 通知通道
# --------------------------------------------------------------------------

class ConsoleNotifier:
    name = "console"

    # 控制台只算「本地留痕」，**不算送达**。
    # 否则一个永远成功的 console 会把 onebot 的失败掩盖掉：
    # channels: ["onebot", "console"] 的配置下，onebot 发不出去也会被判成
    # 「已推送」，重试永远不会触发 —— 实测踩过这个设计陷阱。
    counts_as_delivery = False

    def send(self, text):
        stamp = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        print("[%s] 控制台通知\n%s" % (stamp, text), flush=True)


class OneBotNotifier:
    """OneBot v11 正向 HTTP。适配 NapCatQQ / LLOneBot 等协议端。

    target_type = group   → 发到群（target_id 填群号）
    target_type = private → 发到私聊（target_id 填对方 QQ 号）

    私聊不需要对方是好友吗？OneBot 的 send_private_msg 通常能直接发给好友；
    如果对方不是这个机器人号的好友，NapCat 会返回失败。
    """

    name = "onebot"

    def __init__(self, cfg):
        self.cfg = cfg or {}
        base = (self.cfg.get("base") or "").rstrip("/")
        if not base:
            raise ValueError("onebot.base 未配置")
        self.base = base

    def _at_prefix(self, target_type):
        """群聊里可选的 @ 前缀。

        注意：@全体成员（CQ:at,qq=all）要求机器人号是本群管理员，
        且一个 QQ 号一天只有 10 次 @全体 配额（所有群共享）。详见 README。
        """
        if target_type != "group":
            return ""
        parts = []
        if self.cfg.get("at_all"):
            parts.append("[CQ:at,qq=all]")
        for uid in (self.cfg.get("at_users") or []):
            parts.append("[CQ:at,qq=%s]" % uid)
        return ("".join(parts) + " ") if parts else ""

    def send(self, text):
        target_type = self.cfg.get("target_type") or "group"
        if target_type == "private":
            action, key = "send_private_msg", "user_id"
        else:
            action, key = "send_group_msg", "group_id"
        url = "%s/%s" % (self.base, action)
        headers = {}
        token = self.cfg.get("token")
        if token:
            headers["Authorization"] = "Bearer " + token
        body = {key: self.cfg.get("target_id"), "message": self._at_prefix(target_type) + text}
        resp = _post_json(url, body, headers)
        if str(resp.get("status")) != "ok":
            raise RuntimeError("OneBot 返回异常：%s" % json.dumps(resp, ensure_ascii=False)[:200])


class QQOfficialNotifier:
    """QQ 开放平台官方机器人。群聊用 group_openid，单聊用 user_openid。

    单聊（C2C）有两个前置条件，少一个都会报「消息发送失败，无好友关系」：
      1. 机器人后台【沙箱配置】→【在消息列表配置】里加上对方的 QQ 号
         （只有加了，对方资料卡上才会出现「发消息」按钮）；
      2. 对方在 QQ 里打开机器人资料卡，点「发消息」，先给机器人发一条。
    """

    name = "qq_official"

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.app_id = self.cfg.get("app_id") or ""
        self.client_secret = self.cfg.get("client_secret") or ""
        self.group_openid = self.cfg.get("group_openid") or ""
        self.user_openid = self.cfg.get("user_openid") or ""

        target_type = str(self.cfg.get("target_type") or "").lower()
        if target_type not in ("group", "c2c", "private", "user"):
            # 没写就按填了哪个 openid 来推断
            target_type = "c2c" if self.user_openid else "group"
        self.target_type = "c2c" if target_type in ("c2c", "private", "user") else "group"

        if not (self.app_id and self.client_secret):
            raise ValueError("qq_official 需要 app_id / client_secret")
        if self.target_type == "c2c" and not self.user_openid:
            raise ValueError("qq_official 单聊模式需要 user_openid（不是 QQ 号，是 openid）")
        if self.target_type == "group" and not self.group_openid:
            raise ValueError("qq_official 群聊模式需要 group_openid（不是群号）")

        self._token = None
        self._token_expire_at = 0

    def _access_token(self):
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token
        data = _post_json(
            "https://bots.qq.com/app/getAppAccessToken",
            {"appId": self.app_id, "clientSecret": self.client_secret},
            {},
        )
        token = data.get("access_token")
        if not token:
            raise RuntimeError("获取 access_token 失败：%s" % json.dumps(data, ensure_ascii=False)[:200])
        self._token = token
        try:
            self._token_expire_at = now + int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            self._token_expire_at = now + 7200
        return token

    @staticmethod
    def _hint(resp):
        """把官方那几个高频报错翻译成人话。"""
        blob = json.dumps(resp, ensure_ascii=False)
        if "好友关系" in blob or "11244" in blob:
            return ("\n  提示：单聊要先建立好友关系 —— 后台【沙箱配置】→【消息列表配置】加上对方 QQ，"
                    "再让对方打开机器人资料卡点「发消息」发一条。")
        if "40034102" in blob or "主动消息" in blob:
            return ("\n  提示：主动消息权限不足。官方机器人需完成「发布上架审核」，"
                    "且对方没在 QQ 客户端把「允许主动发送」关掉。")
        if "频率" in blob or "频控" in blob or "22009" in blob:
            return ("\n  提示：触发频控。单关系维度 20 条/分钟、每个好友每天最多收 1000 条；"
                    "未认证机器人整体 30 条/分钟。开播提醒的量级远够。")
        return ""

    def send(self, text):
        token = self._access_token()
        if self.target_type == "c2c":
            url = "https://api.sgroup.qq.com/v2/users/%s/messages" % self.user_openid
        else:
            url = "https://api.sgroup.qq.com/v2/groups/%s/messages" % self.group_openid
        headers = {
            "Authorization": "QQBot " + token,
            "X-Union-Appid": self.app_id,
        }
        resp = _post_json(url, {"content": text, "msg_type": 0}, headers)
        if resp.get("id"):
            return
        raise RuntimeError("QQ 官方机器人发送失败：%s%s"
                           % (json.dumps(resp, ensure_ascii=False)[:300], self._hint(resp)))


def _post_json(url, body, headers=None):
    hdrs = {"Accept": "application/json", "User-Agent": UA}
    if headers:
        hdrs.update(headers)
    hdrs["Content-Type"] = "application/json"
    raw = http_request(url, headers=hdrs, data=body, timeout=20)
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("响应不是 JSON：%s" % text[:200]) from exc


def build_notifiers(cfg):
    out = []
    for name in cfg.get("channels") or ["console"]:
        name = str(name).strip()
        if name == "console":
            out.append(ConsoleNotifier())
        elif name == "onebot":
            out.append(OneBotNotifier(cfg.get("onebot")))
        elif name == "qq_official":
            out.append(QQOfficialNotifier(cfg.get("qq_official")))
        else:
            raise ValueError("未知通知通道：%s" % name)
    if not out:
        raise ValueError("channels 为空，至少要配一个通道")
    return out


# --------------------------------------------------------------------------
# 状态持久化 + 主循环
# --------------------------------------------------------------------------

def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CFG))  # 深拷贝
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fp:
            user_cfg = json.load(fp)
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    else:
        print("[warn] 没找到 %s" % path, flush=True)
        print("       首次使用先复制模板：cp config.example.json config.json", flush=True)
    return cfg


def validate_cfg(cfg, path):
    """检查必填项，报错信息尽量能直接照做。

    这些检查值得单独存在，因为配置错了的表现往往是「什么都没发生」——
    不报错、不推送，用户完全不知道卡在哪一步。宁可啰嗦一点。
    """
    room_id = str(cfg.get("room_id") or "").strip()
    if not room_id:
        print("[error] %s 里没有 room_id。" % path, flush=True)
        print("        到直播间页面源码里搜 room_id，拿真实号填进来。", flush=True)
        print("        别填地址栏里的靓号 —— 两者经常不一样，详见 watch.py 文件头。", flush=True)
        print("        拿不准可以先试：python watch.py --probe <号>", flush=True)
        return False
    if not room_id.isdigit():
        print("[error] room_id 得是纯数字，当前是 %r。" % room_id, flush=True)
        return False

    channels = cfg.get("channels") or []
    if not channels:
        print("[error] channels 是空的，至少要有一个：console / onebot / qq_official", flush=True)
        return False

    if "onebot" in channels:
        ob = cfg.get("onebot") or {}
        target = str(ob.get("target_id") or "").strip()
        if not target.isdigit():
            print("[error] 启用了 onebot 通道，但 onebot.target_id 没填对。", flush=True)
            print("        target_type=group 时填群号，=private 时填对方 QQ 号，都是纯数字。", flush=True)
            print("        当前值：%r" % target, flush=True)
            return False
        if not str(ob.get("token") or "").strip():
            print("[warn] onebot.token 是空的。NapCat 侧不设 token 也能通，但不建议：", flush=True)
            print("       端口一旦意外暴露，谁都能拿它发消息。", flush=True)

    if "qq_official" in channels:
        qq = cfg.get("qq_official") or {}
        if not str(qq.get("app_id") or "").strip() or not str(qq.get("client_secret") or "").strip():
            print("[error] 启用了 qq_official 通道，但 app_id / client_secret 没填。", flush=True)
            return False

    return True


def state_path(cfg):
    return os.path.join(HERE, "state_%s.json" % cfg["room_id"])


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, json.JSONDecodeError):
            pass
    return {"is_live": None, "pending": None, "pending_n": 0,
            "last_change_at": None, "notify": None}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def format_message(cur, cfg, action):
    lines = []
    if action == "up":
        head = "【斗鱼开播】"
    else:
        head = "【斗鱼下播】"
    lines.append(head + (cur.get("anchor") or "未知主播"))
    title = (cur.get("title") or "").strip()
    if title:
        lines.append("标题：" + title)
    if cur.get("online"):
        lines.append("热度：%s 万" % round(cur["online"] / 10000.0, 1))
    if action == "up":
        if cur.get("start_time"):
            lines.append("开播时间：" + str(cur["start_time"]))
        if cur.get("loop_flag") == 1:
            lines.append("提示：接口显示该房间为视频轮播，可能不是真人直播")
    lines.append(cfg.get("room_url") or ("https://www.douyu.com/" + str(cur.get("room_id"))))
    return "\n".join(lines)


def notify_all(notifiers, text):
    """逐个通道发送，返回 (是否已送达真实通道, 失败的通道名列表)。

    「送达」只认 counts_as_delivery 的通道（见 ConsoleNotifier 上的说明）：
    console 的成功不算数，否则它的「永远成功」会把 onebot 的失败吞掉，
    重试逻辑就形同虚设。一个只配了 console 的配置不存在「送达」概念，
    直接算成功（没什么可重试的）。
    """
    delivered = False
    has_delivery_channel = False
    failed = []
    for notifier in notifiers:
        if not getattr(notifier, "counts_as_delivery", True):
            try:
                notifier.send(text)
            except Exception as exc:  # noqa: BLE001
                print("[error] %s 通知失败：%s" % (notifier.name, exc), flush=True)
            continue
        has_delivery_channel = True
        try:
            notifier.send(text)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            failed.append(notifier.name)
            print("[error] %s 通知失败：%s" % (notifier.name, exc), flush=True)
    return (delivered or not has_delivery_channel), failed


def _kind_cn(kind):
    return "开播" if kind == "up" else "下播"


def _parse_iso(value):
    """解析状态文件里的时间戳。

    容忍脏数据（无时区 / 乱写 / 类型不对），**绝不抛异常** ——
    状态文件是允许用户手改的（本项目文档就建议过 `--recover-notify` 那种做法），
    一个畸形时间戳不该把整轮检查搞崩：那样 systemd 每轮都失败，
    监控会**安静地**死掉，比当场报个错严重得多。
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)      # 没有时区的按北京时间算
    return dt


def _age_seconds(value, now):
    """距今多少秒。值不可解析就返回 None —— 调用方据此保守处理。"""
    dt = _parse_iso(value)
    if dt is None:
        return None
    try:
        return (now - dt).total_seconds()
    except (TypeError, ValueError):
        return None


def _num(value, default):
    """配置/状态里的数字容错：写错了就用默认值，别让一个笔误搞崩整轮检查。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _attempts(pend):
    return int(_num(pend.get("attempts"), 0) or 0)


def _retry_due(pend, cfg, now):
    """现在该不该重试。返回 (bool, 原因)，原因只用于日志。"""
    attempts = _attempts(pend)
    if attempts <= 0:
        return True, ""
    age = _age_seconds(pend.get("last_attempt_at"), now)
    cap = max(1, int(_num(cfg.get("notify_retry_backoff_cap_minutes"), 10)))
    # 指数前先夹住，免得状态文件里一个天文数字把 2**n 算成大整数
    wait = min(2 ** min(attempts - 1, 20), cap) * 60
    if age is not None and age < wait:
        return False, "退避中，还要等 %d 秒" % int(wait - age)
    return True, ""


def _retry_give_up(pend, cfg, now):
    """到顶了没有？到顶就返回原因字符串，否则返回空串。"""
    attempts = _attempts(pend)
    if attempts >= max(1, int(_num(cfg.get("notify_retry_max"), 30))):
        return "已重试 %d 次" % attempts
    hours = _num(cfg.get("notify_retry_max_age_hours"), 6)
    age = _age_seconds(pend.get("first_failed_at"), now)
    if age is not None and age > hours * 3600:
        return "距首次失败已超过 %g 小时" % hours
    return ""


def fill_online(room_id, cur):
    """betard 接口不返回热度值，推送前用备用接口补一次（失败就算了，不阻塞通知）。"""
    if cur.get("online"):
        return cur
    try:
        alt = probe_legacy(room_id)
    except Exception:  # noqa: BLE001
        return cur
    merged = dict(cur)
    if alt.get("online"):
        merged["online"] = alt["online"]
    if not merged.get("start_time") and alt.get("start_time"):
        merged["start_time"] = alt["start_time"]
    return merged


def cmd_probe(room_id):
    print("探测 room_id = %s" % room_id)
    for probe in (probe_betard, probe_legacy):
        print("\n=== %s ===" % probe.__name__)
        try:
            result = probe(room_id)
        except Exception as exc:  # noqa: BLE001
            print("失败：%s" % exc)
            continue
        brief = {k: v for k, v in result.items() if k != "raw"}
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        print("raw: " + json.dumps(result.get("raw"), ensure_ascii=False)[:400])


def cmd_once(room_id, cfg):
    cur = read_state(room_id)
    brief = {k: v for k, v in cur.items() if k != "raw"}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    effective = cur["is_live"] and (cfg["treat_loop_as_live"] or cur.get("loop_flag") != 1)
    print("判定：%s" % ("直播中" if effective else "未开播"))


def tick(cfg, notifiers=None, verbose=True, heartbeat=False):
    """执行一轮检查：读状态 → 判定 → 必要时通知 → 补发漏掉的 → 写回状态文件。

    刻意做成"单次执行 + 落盘"的形态，这样它既能被常驻循环调用，
    也能被 cron / 青龙面板 / 云函数每分钟拉起来跑 —— 后者就不需要常驻进程。

    发送失败不会丢通知：失败的正文连同重试次数写进状态文件的 `notify` 字段，
    之后每一轮都会尝试补发（间隔 1、2、4、8… 分钟退避），
    直到送出、或到达 notify_retry_max / notify_retry_max_age_hours 上限。

    返回 dict，含 is_live / action / cur / state，
    外加 notify（本轮实际做过的通知动作，可能为 None）
    和 pending（本轮结束后仍未送出的那条，可能为 None）。
    """
    room_id = str(cfg["room_id"])
    path = state_path(cfg)
    state = load_state(path)
    if notifiers is None:
        notifiers = build_notifiers(cfg)

    confirm = max(1, int(cfg["confirm_rounds"]))
    treat_loop = bool(cfg.get("treat_loop_as_live", True))

    cur = read_state(room_id)
    raw_live = bool(cur["is_live"])
    looping = cur.get("loop_flag") == 1
    is_live = raw_live
    if raw_live and looping and not treat_loop:
        # videoLoop == 1 表示房间在循环播放录播，不是真人直播（见文件头 DEFAULT_CFG 的说明）
        is_live = False

    state["rounds"] = int(state.get("rounds") or 0) + 1

    if heartbeat:
        if is_live:
            label = "LIVE" if not looping else "LIVE(轮播)"
        elif raw_live and looping:
            label = "LOOP(轮播)"
        else:
            label = "offline"
        # loop 每轮都打出来：留档用于复验「真人开播时 videoLoop 是否回到 0」
        print("[%s] #%d %s | %s | 热度 %s | loop=%s | via %s"
              % (datetime.now(CST).strftime("%H:%M:%S"), state["rounds"],
                 label,
                 (cur.get("title") or "")[:28],
                 cur.get("online") or "-",
                 cur.get("loop_flag") if cur.get("loop_flag") is not None else "-",
                 cur.get("source")), flush=True)

    # 本轮开始时就挂着的待补发记录。只补发「上几轮遗留的」——
    # 免得同一轮里刚发失败就立刻再撞一次（那等于把同一个动作做两遍）。
    stale = state.get("notify")
    now = datetime.now(CST)
    notify_info = None

    action = None
    if state["is_live"] is None:
        # 首次运行只记录当前状态，不发通知，避免程序一启动就误报一条
        state["is_live"] = is_live
        state["last_change_at"] = now.isoformat()
        if verbose:
            print("[init] 首次运行，记录当前状态为 %s，不发送通知"
                  % ("直播中" if is_live else "未开播"), flush=True)
    elif is_live != state["is_live"]:
        # 连续 confirm 次读到同一状态才认定切换，防接口抖动误报
        if state.get("pending") == is_live:
            state["pending_n"] = int(state.get("pending_n") or 0) + 1
        else:
            state["pending"] = is_live
            state["pending_n"] = 1
        if state["pending_n"] >= confirm:
            action = "up" if is_live else "end"
            # 状态先落盘、再发送：发送失败也不会把「状态」这件事弄丢
            state["is_live"] = is_live
            state["pending"] = None
            state["pending_n"] = 0
            state["last_change_at"] = now.isoformat()
            if action == "up" or cfg.get("notify_on_end"):
                text = format_message(fill_online(room_id, cur), cfg, action)
                delivered, failed = notify_all(notifiers, text)
                if delivered:
                    state["notify"] = None
                else:
                    # 没送出去 → 记下来，下一轮起自动补发（这才是根治）
                    state["notify"] = {
                        "kind": action,
                        "text": text,
                        "attempts": 1,
                        "failed_channels": failed,
                        "first_failed_at": now.isoformat(),
                        "last_attempt_at": now.isoformat(),
                    }
                    if not cfg.get("retry_failed_notify", True):
                        state["notify"]["gave_up_at"] = now.isoformat()
                        state["notify"]["gave_up_why"] = "配置里关掉了重试"
                notify_info = {"kind": action, "stage": "first",
                               "delivered": delivered, "failed": failed, "attempts": 1}
            else:
                # 这次不需要通知（例如 notify_on_end=false 的下播）：
                # 旧记录一并清掉，别让它继续挂着
                state["notify"] = None
            if verbose:
                print("[change] 状态切换 -> %s" % ("直播中" if is_live else "未开播"), flush=True)
    else:
        if state.get("pending") is not None:
            state["pending"] = None
            state["pending_n"] = 0

    # ---- 补发上一轮（或更早）没送出去的通知 ----
    pend = state.get("notify") if state.get("notify") is stale else None
    if pend is not None:
        want_kind = "up" if state["is_live"] else "end"
        if pend.get("kind") != want_kind:
            # 状态已经翻面、或有人手改了状态文件 —— 这条旧通知作废。
            # （--recover-notify 正是靠这个把卡住的记录清掉的）
            if verbose:
                print("[warn] 状态已变为「%s」，放弃补发旧的「%s」通知"
                      % ("直播中" if state["is_live"] else "未开播",
                         _kind_cn(pend.get("kind"))), flush=True)
            state["notify"] = None
        elif pend.get("gave_up_at") or not cfg.get("retry_failed_notify", True):
            pass
        else:
            due, why = _retry_due(pend, cfg, now)
            give_up_why = _retry_give_up(pend, cfg, now)
            if give_up_why:
                pend["gave_up_at"] = now.isoformat()
                pend["gave_up_why"] = give_up_why
                print("[error] 「%s」通知%s仍未送出，放弃自动重试。"
                      "通道恢复后手动补一次：python3 watchdog.py --recover-notify"
                      % (_kind_cn(pend.get("kind")), give_up_why), flush=True)
                notify_info = {"kind": pend.get("kind"), "stage": "giveup",
                               "delivered": False,
                               "failed": pend.get("failed_channels") or [],
                               "attempts": _attempts(pend)}
            elif not due:
                if verbose:
                    print("[warn] 「%s」通知待补发：%s"
                          % (_kind_cn(pend.get("kind")), why), flush=True)
            else:
                delivered, failed = notify_all(notifiers, pend.get("text") or "")
                pend["attempts"] = _attempts(pend) + 1
                pend["last_attempt_at"] = now.isoformat()
                pend["failed_channels"] = failed
                if delivered:
                    if verbose:
                        print("[ok] 「%s」通知补发成功（第 %d 次尝试）"
                              % (_kind_cn(pend.get("kind")), pend["attempts"]), flush=True)
                    state["notify"] = None
                else:
                    if verbose:
                        print("[warn] 「%s」通知第 %d 次补发仍失败，稍后继续重试"
                              % (_kind_cn(pend.get("kind")), pend["attempts"]), flush=True)
                notify_info = {"kind": pend.get("kind"), "stage": "retry",
                               "delivered": delivered, "failed": failed,
                               "attempts": pend["attempts"]}

    save_state(path, state)
    return {"is_live": is_live, "action": action, "cur": cur, "state": state,
            "notify": notify_info, "pending": state.get("notify")}


def cmd_tick(cfg):
    """只检查一轮就退出。给 cron / 青龙面板 / 云函数用，不必常驻进程。

    注意：这里也要尊重 log_heartbeat。定时任务模式下没有常驻日志，
    这行心跳就是事后复验（比如确认 videoLoop 到底什么时候变 0）的唯一依据。
    """
    result = tick(cfg, heartbeat=bool(cfg.get("log_heartbeat")))
    info = result.get("notify")
    pending = result.get("pending")
    # 这一步以前会说「已推送」——哪怕发送其实失败了。日志撒谎比没日志更坏：
    # 你会以为链路是通的。现在如实分开说。
    if info:
        kind = _kind_cn(info["kind"])
        if info["stage"] == "first":
            if info["delivered"]:
                tail = "，已推送「%s」通知" % kind
            else:
                tail = ("，「%s」通知未送出（%s），已记下并在后续轮次自动补发"
                        % (kind, "、".join(info["failed"]) or "全部通道"))
        elif info["stage"] == "retry":
            if info["delivered"]:
                tail = "，补发「%s」通知成功（第 %d 次尝试）" % (kind, info["attempts"])
            else:
                tail = "，补发「%s」通知仍失败（第 %d 次），稍后继续" % (kind, info["attempts"])
        else:  # giveup
            tail = "，「%s」通知重试到顶仍未送出，已放弃自动重试（需人工处理）" % kind
    elif result["action"]:
        tail = "，状态切换为「%s」，按配置不发通知" % _kind_cn(result["action"])
    elif pending:
        tail = "，无状态切换（有 1 条「%s」通知待补发）" % _kind_cn(pending.get("kind"))
    else:
        tail = "，无状态切换"
    print("判定：%s%s" % ("直播中" if result["is_live"] else "未开播", tail), flush=True)
    return 0


def cmd_test_notify(cfg):
    """给配置里的每个通道各发一条测试消息，用来验证推送链路是否打通。

    部署完 NapCat / 机器人后先跑这个 —— 比等主播开播快得多，也更容易定位问题
    （是 QQ 链路不通，还是斗鱼侧没判定出来）。
    """
    notifiers = build_notifiers(cfg)
    stamp = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    text = ("【斗鱼开播提醒·测试消息】\n"
            "看到这条说明推送链路已经打通。\n"
            "发出时间：" + stamp)
    print("[test] 使用通道：%s" % ", ".join(n.name for n in notifiers), flush=True)
    failed = 0
    for notifier in notifiers:
        try:
            notifier.send(text)
            print("[test] %s 发送成功" % notifier.name, flush=True)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("[test] %s 发送失败：%s" % (notifier.name, exc), flush=True)
    if failed:
        print("[test] 有 %d 个通道失败，请按上面的报错排查" % failed, flush=True)
        return 1
    print("[test] 全部通道发送成功", flush=True)
    return 0


def cmd_run(cfg):
    """常驻模式：一直在前台跑，窗口关了就不监控了。"""
    room_id = str(cfg["room_id"])
    notifiers = build_notifiers(cfg)
    interval = max(15, int(cfg["interval_seconds"]))
    jitter = max(0, int(cfg.get("jitter_seconds") or 0))

    print("[start] 监控 room_id=%s，间隔 %ds±%ds，通道：%s"
          % (room_id, interval, jitter, ", ".join(n.name for n in notifiers)), flush=True)
    print("[start] 常驻模式，关掉窗口就停了。不想挂着请改用 --tick + 定时任务", flush=True)

    while True:
        try:
            tick(cfg, notifiers, verbose=True, heartbeat=bool(cfg.get("log_heartbeat")))
        except Exception as exc:  # noqa: BLE001
            print("[error] 本轮出错：%s" % exc, flush=True)

        time.sleep(interval + random.randint(0, jitter) if jitter else interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="斗鱼直播间开播提醒")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--probe", metavar="ROOM_ID", help="体检指定房间号，打印两个接口的原始返回")
    parser.add_argument("--once", action="store_true", help="只读一次当前状态，不通知")
    parser.add_argument("--tick", action="store_true",
                        help="只检查一轮就退出（配合 cron / 青龙面板 / 云函数，不必常驻进程）")
    parser.add_argument("--test-notify", action="store_true",
                        help="给配置的每个通道发一条测试消息，验证推送链路（部署后先跑这个）")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.probe:
        cmd_probe(args.probe)
        return 0
    # --probe 之外的模式都要读配置，先校验，免得跑起来一片安静却什么都没做
    if not validate_cfg(cfg, args.config):
        return 2
    if args.once:
        cmd_once(str(cfg["room_id"]), cfg)
        return 0
    if args.tick:
        return cmd_tick(cfg)
    if args.test_notify:
        return cmd_test_notify(cfg)
    cmd_run(cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[stop] 已手动停止", flush=True)
    except RuntimeError as e:
        # read_state() 在两个探测接口都失败时抛这个。最常见的原因是
        # room_id 填成了靓号，或者填了一个根本不存在的房间 —— 这是新手
        # 最容易踩的坑，所以给能直接照做的提示，别甩一整屏 Traceback。
        print("[error] %s" % e, flush=True)
        print("        多半是 room_id 不对。注意要填接口用的真实号，不是地址栏里的靓号。", flush=True)
        print("        先这样验证一下：python watch.py --probe <room_id>", flush=True)
        sys.exit(1)
