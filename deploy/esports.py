#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 每日赛程推送（跟斗鱼开播提醒共用一套通知通道）

做什么
------
每天固定时刻（由 douyu-esports.timer 拉起，默认北京 09:30）抓一次 Liquipedia 的
CS2 赛程，挑出**今天还没开打**、且满足下面任一条件的比赛，推一条到群里：

  · 大赛（赛事名命中 esports.major_keywords 里的关键词）
  · 有中国队参赛（队名精确命中 esports.cn_teams 白名单）

没有符合条件的比赛就**静默**（不发消息），但连续静默满 7 天会发一条「报平安」——
免得「今天没比赛」和「程序挂了」在群里长得一模一样。

为什么不复用 watch.py 的 tick
-----------------------------
watch.py 是「每 45 秒轮询一个直播间」的常驻逻辑，和「每天抓一次赛事列表」
没有共同点。共用的是**通知层**：load_config / build_notifiers / notify_all，
以及 http_request（它已经处理了 gzip 解压、自定义头、以及「本机地址不走代理」）。
这三样正是最容易写错的部分，直接 import watch 复用，不重写。

关于 Liquipedia 的硬性要求（不满足会被 406/403 挡）
--------------------------------------------------
  1. User-Agent 必须写明项目名 + 联系方式。Python-urllib 这类通用 UA 会被拒（实测 406）。
  2. 必须支持 gzip（Accept-Encoding: gzip）。
  3. 条款：action=parse 类请求 <= 1 次 / 30 秒，且禁止抓渲染好的 HTML 页面，
     只能走 api.php。→ 所以本功能**每天只请求 1 次**，重试也要隔开 30 秒。
  4. 内容 CC-BY-SA 3.0 → 推送正文必须署名「数据来源：Liquipedia」。

用法
----
    python3 esports.py                 # 正常跑一轮（该发就发，该静默就静默）
    python3 esports.py --check         # 只抓 + 打印将要发的内容，**不发消息、不写状态**
    python3 esports.py --test-notify   # 往配置的通道发一条测试消息（验证链路用）
    python3 esports.py --selftest      # 离线自检，不联网、不发消息

关于解析器
----------
parse_matches() 与 deploy/check-esports-net.py 里的那份是**同一份实现**（那份是
上线前的连通性自检，独立可跑所以没有 import 本文件）。改选择器时**两边都要改**。
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

# 服务器上 watch.py 和本文件同在 /opt/douyu-live-notify/，直接 import 就行。
# 但开发机上 watch.py 在仓库根、本文件在 deploy/ 里 —— 补一次 sys.path，
# 免得本地想跑 --selftest 还得先复制文件过去。
try:
    import watch
except ImportError:  # pragma: no cover - 只在仓库布局下走到
    sys.path.insert(0, os.path.dirname(HERE))
    import watch

DEFAULT_CONFIG = os.path.join(HERE, "config.json")
STATE_FILE = os.path.join(HERE, "state_esports.json")

# 状态文件落在这个目录；systemd 单元里用 ProtectSystem=full，只读 /usr /etc，
# /opt 可写，所以和 watch.py 一样直接写在脚本旁边。
CST = watch.CST
VERSION = "1.0.0"

LIQUIPEDIA_API = "https://liquipedia.net/counterstrike/api.php"

# 大赛关键词（子串匹配赛事名，大小写不敏感）。
# ⚠️ 页面上**拿不到**赛事分级（S/A/B）：所有 data-* 属性已普查，没有
#    data-liquipediatier / data-tier 这类字段，分级按钮是客户端行为、没写进 HTML。
#    所以「是不是大赛」只能靠赛事名认，这份名单就是全部依据，可按需增删。
DEFAULT_MAJOR_KEYWORDS = [
    "BLAST", "IEM", "ESL Pro League", "Major", "PGL", "Perfect World",
    "Esports World Cup", "DreamHack", "ESL One", "CCT", "ESL Challenger",
    "eXTREMESLAND", "FISSURE", "Thunderpick", "YaLLa", "BetBoom", "Skyesports",
]

# 中国队白名单（**精确匹配**队名，不模糊 —— 模糊匹配会把「The MongolZ」这类
# 别国队伍误判进来）。
# 2026-09-30 按用户要求收窄到 2 支，只保留最确定的两支。
# 早期版本还收过 Rare Atom / Wings Up Gaming / Steel Helmet / NewHappy /
# The Huns / Talon Esports，需要时按**Liquipedia 上的全名**加回来，别用缩写。
# ⚠️ 蒙古国队伍（The MongolZ / IHC / ATOX / NKT / Chinggis Warriors）**不是中国队**，
#    这是最容易搞错的一点，别加进来。
DEFAULT_CN_TEAMS = [
    "TYLOO",
    "Lynn Vision Gaming",
]

ESPORT_DEFAULTS = {
    # 总开关。关掉后本脚本立刻退出，systemd 那边不会当成失败。
    "enabled": True,
    # Liquipedia 要求在 User-Agent 里写联系方式，格式：项目地址 + 邮箱。
    "ua_contact": "https://github.com/YUAN-27/douyu-live-notify; 1249850641@qq.com",
    "major_keywords": DEFAULT_MAJOR_KEYWORDS,
    "cn_teams": DEFAULT_CN_TEAMS,
    # 超过多少场就开始折叠（只列前 N 场）。0 = 不折叠。
    "fold_hint": 15,
    # 连续静默多少天后发一条「报平安」，之后每满这么多天再发一次。0 = 从不发。
    "calm_after_empty_days": 7,
    # 抓取最多试几次（含首次）。条款限制 1 次/30 秒，所以重试要隔开。
    "fetch_retry_max": 3,
    "parse_min_interval_seconds": 30,
    # 发送失败最多补发几次、每次间隔多少秒。每天只发一条，不值得为它持久化重试状态。
    "notify_retry_max": 3,
    "notify_retry_backoff_seconds": 15,
    "http_timeout": 25,
}

# 网络层抖动可以重试；4xx 重试没有意义（UA 不合格、被封、页面没了，再试还是一样）。
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def log(msg):
    print(msg, flush=True)


# ==========================================================================
# 抓取
# ==========================================================================

def build_url():
    q = urllib.parse.urlencode({
        "action": "parse",
        "page": "Liquipedia:Matches",
        "prop": "text",
        "format": "json",
    })
    return LIQUIPEDIA_API + "?" + q


def build_ua(es):
    contact = str(es.get("ua_contact") or "").strip()
    return "douyu-live-notify/%s (+%s)" % (VERSION, contact or "contact-not-set")


def fetch_once(url, ua, timeout=25):
    """发一次请求。返回 dict：ok / status / text / error。

    http_request 已经处理了 gzip 解压和自定义头覆盖，这里只要把 Liquipedia
    要求的两个头传进去（它的默认 Accept-Encoding 是 identity，会被我们覆盖）。
    """
    try:
        text = watch.http_request(url, headers={
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }, timeout=timeout)
        return {"ok": True, "status": 200, "text": text or "", "error": ""}
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = exc.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "status": exc.code,
                "text": "", "error": "HTTP %s %s%s"
                % (exc.code, exc.reason, ("｜" + snippet) if snippet else "")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "text": "",
                "error": "%s: %s" % (type(exc).__name__, exc)}


def fetch_matches(es):
    """按条款要求节流地抓取，返回最后一次的结果。

    ⚠️ 一定要看条款：action=parse <= 1 次 / 30 秒。所以重试不是「立刻再来一次」，
    而是**至少隔 parse_min_interval_seconds**。也别把这个函数放进高频轮询里。
    """
    url = build_url()
    ua = build_ua(es)
    attempts = max(1, int(es.get("fetch_retry_max") or 1))
    gap = max(0, int(es.get("parse_min_interval_seconds") or 30))
    timeout = max(5, int(es.get("http_timeout") or 25))

    last = None
    for i in range(1, attempts + 1):
        r = fetch_once(url, ua, timeout=timeout)
        if r["ok"]:
            if i > 1:
                log("[info] 第 %d 次尝试成功" % i)
            return r

        last = r
        retryable = r["status"] is None or r["status"] in RETRYABLE_STATUS
        if not retryable:
            log("[error] 抓取失败且不该重试（%s）" % r["error"])
            break
        if i == attempts:
            log("[error] 第 %d/%d 次抓取仍失败：%s" % (i, attempts, r["error"]))
            break
        log("[warn] 第 %d/%d 次抓取失败（%s），%d 秒后重试"
            % (i, attempts, r["error"], gap))
        time.sleep(gap)
    return last


def extract_html(text):
    """从 api.php 的 JSON 里取出渲染好的 HTML。返回 (html, 错误说明)。"""
    try:
        payload = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return None, "响应不是 JSON：%s" % exc
    page = payload.get("parse")
    if not page:
        return None, "JSON 里没有 parse 字段（响应结构变了）"
    html_text = (page.get("text") or {}).get("*") or ""
    if not html_text:
        return None, "parse.text 是空的"
    return html_text, ""


# ==========================================================================
# 解析（与 check-esports-net.py 是同一份实现，改选择器时两边都要改）
# ==========================================================================

def clean(s):
    return re.sub(r"\s+", " ", html_mod.unescape(s or "")).strip()


def tidy(s):
    """清洗队名/赛事名：去实体转义、压空白、剥掉无词条队伍的后缀。"""
    s = clean(s)
    return re.sub(r"\s*\(page does not exist\)\s*$", "", s).strip()


# 每场比赛的锚点。实测这个类名一场一个、且没有附加 class（正好 75 个 / 75 场），
# 比用 data-timestamp 当锚点可靠：一个页面里计时器有 125 个，比赛只有 75 场。
MATCH_MARK = '<div class="match-info"'


def parse_matches(text):
    """把渲染 HTML 切成一堆比赛，逐场抽字段。

    三个实测踩到的坑，写在这里免得以后重复踩：
      1. **不能按 data-timestamp 去重**。同时开打的两场比赛时间戳完全相同，
         去重会静默吞掉比赛（早期版本因此漏掉 11 场）。
      2. **队名取 <a title="..."> 里的全名**，内层文本是 FLY / NAVI Jr. 这类缩写。
         但没有独立词条的队伍，title 会带 " (page does not exist)" 后缀，要剥掉。
      3. **赛事名优先取可见文本**（.match-info-tournament-name 里的 <span>，
         形如 "eXTREMESLAND 2026: OCE Qual - Group A"），
         title 属性那种 "EXTREMESLAND/2026/Oceania#Group_A" 是页面路径，不好看。
    """
    pos = [m.start() for m in re.finditer(re.escape(MATCH_MARK), text)]
    out = []
    for i, start in enumerate(pos):
        seg = text[start: pos[i + 1] if i + 1 < len(pos) else len(text)]

        ts = re.search(r'data-timestamp="(\d{10})"', seg)
        if not ts:
            continue

        teams = [t for t in (tidy(x) for x in re.findall(
            r'class="name"[^>]*>\s*<a[^>]*\stitle="([^"]+)"', seg)) if t]
        if len(teams) < 2:
            # 退化路径：TBD 之类没有词条、也没 title 属性的占位队名
            teams = [t for t in (tidy(x) for x in re.findall(
                r'class="name"[^>]*>\s*(?:<a[^>]*>)?([^<]+)', seg)) if t]

        bo = re.search(r'scoreholder-lower">\s*\(?(Bo\d)\)?', seg)

        tour = re.search(r'match-info-tournament-name.{0,300}?<span>([^<]+)</span>',
                         seg, re.S)
        if not tour:
            tour = re.search(r'match-info-tournament.{0,400}?title="([^"]+)"', seg, re.S)

        teams = teams[:2]
        out.append({
            "ts": int(ts.group(1)),
            "teams": teams,
            "bo": bo.group(1) if bo else "",
            "tour": tidy(re.sub(r"#.*$", "", tour.group(1))) if tour else "",
            "tbd": len(teams) >= 2 and all(t.upper() == "TBD" for t in teams),
        })

    out.sort(key=lambda x: x["ts"])
    return out


# ==========================================================================
# 筛选
# ==========================================================================

def is_major(m, keywords):
    """赛事名命中大赛关键词。子串匹配、大小写不敏感。"""
    tour = (m.get("tour") or "").lower()
    if not tour:
        return False
    return any((k or "").lower() in tour for k in (keywords or []) if k)


def has_cn_team(m, cn_teams):
    """队名精确命中中国队白名单（大小写不敏感，两端空白已由 tidy 去掉）。"""
    allow = {(t or "").strip().lower() for t in (cn_teams or [])}
    return any((t or "").strip().lower() in allow for t in (m.get("teams") or []))


def select_today(matches, now, es):
    """挑出「今天还没开打、对阵已定、且属于大赛或有中国队」的比赛。

    为什么只要「还没开打」：这是一条**赛程预告**，09:30 发出去的时候，
    今天凌晨那几场早就打完了，列出来只会让人以为还有比赛可看。
    """
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    t0, t1 = day0.timestamp(), day1.timestamp()
    nowts = now.timestamp()

    keywords = es.get("major_keywords") or []
    cn_teams = es.get("cn_teams") or []

    out = []
    for m in matches:
        if m.get("tbd"):                       # TBD vs TBD：对阵都没定
            continue
        if len(m.get("teams") or []) < 2:       # 队名没解析全，不敢推
            continue
        if not (t0 <= m["ts"] < t1):            # 只看今天（北京时间）
            continue
        if m["ts"] < nowts:                     # 只预告还没开打的
            continue
        if not (is_major(m, keywords) or has_cn_team(m, cn_teams)):
            continue
        out.append(m)
    return out


# ==========================================================================
# 消息
# ==========================================================================

def format_daily(picked, now, fold_hint):
    """拼「今日赛程」正文。末尾必须署名 Liquipedia（CC-BY-SA 3.0 的要求）。"""
    if not picked:
        return ""
    lines = ["【CS2 今日赛程】%s" % now.strftime("%Y-%m-%d"), ""]

    shown = picked
    if fold_hint and fold_hint > 0 and len(picked) > fold_hint:
        shown = picked[:fold_hint]

    for m in shown:
        d = datetime.fromtimestamp(m["ts"], CST)
        seg = "%s  %s" % (d.strftime("%H:%M"), " vs ".join(m["teams"]))
        if m.get("bo"):
            seg += " · " + m["bo"]
        if m.get("tour"):
            seg += " · " + m["tour"]
        lines.append(seg)

    lines.append("")
    if len(shown) < len(picked):
        lines.append("共 %d 场，只列了前 %d 场（后面还有）。" % (len(picked), len(shown)))
    else:
        lines.append("共 %d 场。" % len(picked))
    lines.append("数据来源：Liquipedia")
    return "\n".join(lines)


def format_calm(empty_days):
    """连续静默满一周时的「报平安」。

    这条存在的唯一理由：让「今天没比赛」和「程序挂了」在群里能被区分开。
    """
    return "\n".join([
        "【CS2 赛程】连续 %d 天没有可推送的比赛" % empty_days,
        "",
        "这不是故障，是近期确实没有大赛、也没有中国队参赛。",
        "抓取链路正常（刚成功拿到 Liquipedia 赛程），有符合条件的比赛会自动恢复推送。",
        "",
        "数据来源：Liquipedia",
    ])


def format_fetch_failed(err, attempts):
    """抓取失败告警。故意**不**写「今天没有比赛」——那是最容易混淆的说法。"""
    return "\n".join([
        "【CS2 赛程】今天没抓到数据",
        "",
        "抓取 Liquipedia 赛程失败（共试 %d 次）：%s" % (attempts, err),
        "",
        "影响：今天的赛程预告发不出来。",
        "⚠️ 这不等于「今天没有比赛」，别把它当成安静的信号。",
        "",
        "排查：cd /opt/douyu-live-notify && python3 esports.py --check",
    ])


def format_test():
    return "\n".join([
        "【CS2 赛程】通道测试",
        "",
        "这是一条测试消息，用于验证推送链路，不是真实赛程。",
        "看到它就说明 esports.py 能正常发消息。",
        "",
        "数据来源：Liquipedia",
    ])


# ==========================================================================
# 发送
# ==========================================================================

def send_with_retry(notifiers, text, es):
    """发送并做短退避重试。返回 (是否送达, 最后一次失败的通道名列表)。

    这里刻意**不**做跨轮持久化重试（watchdog 的「报平安」是那样做的）：
    赛程预告是有时效的，明天再补发一条昨天的赛程没有意义，不如当场重试几次、
    失败就明说。一天只发一条，重试成本可以忽略。
    """
    attempts = max(1, int(es.get("notify_retry_max") or 1))
    backoff = max(0, int(es.get("notify_retry_backoff_seconds") or 15))
    failed = []
    for i in range(1, attempts + 1):
        delivered, failed = watch.notify_all(notifiers, text)
        if delivered and not failed:
            return True, []
        if i == attempts:
            break
        log("[warn] 第 %d/%d 次发送有通道失败（%s），%d 秒后重试"
            % (i, attempts, "、".join(failed) or "?", backoff))
        if backoff:
            time.sleep(backoff)
    return False, failed


# ==========================================================================
# 状态（只用来记「连续静默了几天」）
# ==========================================================================

def load_state(path=STATE_FILE):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"empty_streak": 0, "last_run": None, "last_verdict": None}


def save_state(state, path=STATE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def streak_step(state, had_content, calm_after, now):
    """推进「连续静默天数」。返回 (新状态, 这次要不要发报平安)。

    有内容 → 归零；没内容 → 加一，并在**每满 calm_after 天**时提醒一次
    （7、14、21…），不是满 7 天后天天提醒。
    """
    st = dict(state or {})
    st["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")

    if had_content:
        st["empty_streak"] = 0
        st["last_verdict"] = "sent"
        return st, False

    n = int(st.get("empty_streak") or 0) + 1
    st["empty_streak"] = n
    st["last_verdict"] = "empty"
    calm_after = int(calm_after or 0)
    send = calm_after > 0 and n >= calm_after and n % calm_after == 0
    return st, send


# ==========================================================================
# 主流程
# ==========================================================================

def resolve_config(cfg):
    """把 esports 段的默认值补进去（不动 watch.DEFAULT_CFG，避免影响主监控）。"""
    es = dict(ESPORT_DEFAULTS)
    user = cfg.get("esports")
    if isinstance(user, dict):
        es.update(user)
    return es


def run_once(cfg, args):
    es = resolve_config(cfg)
    now = datetime.now(CST)

    if not es.get("enabled", True):
        log("[skip] esports.enabled = false，本轮什么都不做")
        return 0

    log("[start] CS2 每日赛程 · %s（北京时间）" % now.strftime("%Y-%m-%d %H:%M:%S"))

    # ---- 抓取 ----
    r = fetch_matches(es)
    if r is None or not r["ok"]:
        err = (r or {}).get("error") or "未知错误"
        attempts = max(1, int(es.get("fetch_retry_max") or 1))
        text = format_fetch_failed(err, attempts)
        if args.check:
            log("")
            log(text)
            log("\n[check] 以上是抓取失败时会发的告警（未发送）")
            return 1
        notifiers = watch.build_notifiers(cfg)
        delivered, failed = send_with_retry(notifiers, text, es)
        log("[%s] 抓取失败告警：%s"
            % ("error" if not delivered or failed else "sent",
               "、".join(failed) if failed else "已送达"))
        return 1

    html_text, err = extract_html(r["text"])
    if html_text is None:
        log("[error] %s" % err)
        return 1

    matches = parse_matches(html_text)
    if not matches:
        log("[error] 一个比赛都没解析出来 —— 页面结构可能变了，选择器要重新对")
        log("        核对：https://liquipedia.net/counterstrike/Liquipedia:Matches")
        return 1

    picked = select_today(matches, now, es)
    log("[info] 页面共 %d 场，其中今天还没开打且符合条件的有 %d 场"
        % (len(matches), len(picked)))

    # ---- 组装 ----
    state = load_state()
    body = format_daily(picked, now, es.get("fold_hint"))

    _st, want_calm = streak_step(state, bool(picked), es.get("calm_after_empty_days"), now)

    if not picked:
        log("[silent] 今天没有可推送的比赛（连续第 %d 天）" % _st["empty_streak"])
        if want_calm:
            body = format_calm(_st["empty_streak"]) + "\n"
            log("[info] 连续静默满 %s 天，本轮发一条报平安" % es.get("calm_after_empty_days"))
        else:
            if not args.check:
                save_state(_st)
            return 0

    # ---- 发送 ----
    if args.check:
        log("")
        log(body)
        log("\n[check] 以上是将会发送的内容（未发送、未写状态）")
        return 0

    notifiers = watch.build_notifiers(cfg)
    delivered, failed = send_with_retry(notifiers, body, es)
    if delivered and not failed:
        log("[sent] 已送达（%d 条）" % len(notifiers))
        save_state(_st)
        return 0

    log("[error] 发送失败：%s" % ("、".join(failed) or "未知"))
    # 故意**不**写状态：写进去就等于「今天已经交代过了」，
    # 而消息其实没到任何人手上。
    return 1


# ==========================================================================
# 子命令
# ==========================================================================

def cmd_test_notify(cfg):
    es = resolve_config(cfg)
    notifiers = watch.build_notifiers(cfg)
    text = format_test()
    failed = 0
    for notifier in notifiers:
        try:
            notifier.send(text)
            log("[test] %s 发送成功" % notifier.name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log("[test] %s 发送失败：%s" % (notifier.name, exc))
    if failed:
        log("[test] 有 %d 个通道失败，请按上面的报错排查" % failed)
        return 1
    log("[test] 全部通道发送成功")
    return 0


# ==========================================================================
# 离线自检
# ==========================================================================

FIXTURE_HTML = """
<div class="match-info">
  <div class="match-info-header">
    <div class="match-info-tournament-name">
      <a href="/counterstrike/BLAST/Premier" title="BLAST/Premier">
        <span>BLAST Premier Fall 2026 - Group A</span></a>
    </div>
  </div>
  <div class="match-info-match">
    <span class="timer" data-timestamp="1790000000"></span>
    <div class="name"><a href="/counterstrike/TYLOO" title="TYLOO">TYL</a></div>
    <div class="name"><a href="/counterstrike/Lynn_Vision" title="Lynn Vision Gaming">LVG</a></div>
  </div>
  <div class="scoreholder-lower">(Bo3)</div>
</div>
<div class="match-info">
  <div class="match-info-header">
    <div class="match-info-tournament-name">
      <a href="/counterstrike/Stake_Ranked" title="Stake Ranked"><span>Stake Ranked Season 7</span></a>
    </div>
  </div>
  <div class="match-info-match">
    <span data-timestamp="1790000100"></span>
    <div class="name"><a href="/counterstrike/A" title="Alpha (page does not exist)">ALP</a></div>
    <div class="name"><a href="/counterstrike/B" title="Beta">BET</a></div>
  </div>
  <div class="scoreholder-lower">(Bo1)</div>
</div>
<div class="match-info">
  <div class="match-info-header">
    <div class="match-info-tournament-name">
      <a href="/counterstrike/IEM" title="IEM"><span>IEM Cologne 2026</span></a>
    </div>
  </div>
  <div class="match-info-match">
    <span data-timestamp="1790000200"></span>
    <div class="name">TBD</div>
    <div class="name">TBD</div>
  </div>
  <div class="scoreholder-lower">(Bo3)</div>
</div>
"""


class _T:
    def __init__(self):
        self.n = 0
        self.fail = 0

    def check(self, name, cond, detail=""):
        self.n += 1
        if cond:
            print("  [OK]   %s" % name, flush=True)
        else:
            self.fail += 1
            print("  [FAIL] %s%s" % (name, ("   <- " + str(detail)) if detail else ""),
                  flush=True)

    def done(self, title):
        print("\n结果：%d 项通过，%d 项失败（共 %d 项）"
              % (self.n - self.fail, self.fail, self.n), flush=True)
        return 1 if self.fail else 0


def _mk(ts, teams, tour, bo="Bo3", tbd=False):
    return {"ts": ts, "teams": list(teams), "bo": bo, "tour": tour, "tbd": tbd}


def selftest():
    t = _T()
    es = dict(ESPORT_DEFAULTS)

    print("CS2 每日赛程 · 离线自检（不联网、不发消息、不写状态）")
    print("版本 %s\n" % VERSION)

    # ---- 1. 解析器 ----
    print("-- 1. 解析器 --")
    ms = parse_matches(FIXTURE_HTML)
    t.check("锚点切出 3 场", len(ms) == 3, "实际 %d" % len(ms))
    if len(ms) == 3:
        t.check("队名取 title 全名（LVG -> Lynn Vision Gaming）",
                ms[0]["teams"] == ["TYLOO", "Lynn Vision Gaming"], ms[0]["teams"])
        t.check("剥掉 (page does not exist) 后缀",
                ms[1]["teams"][0] == "Alpha", ms[1]["teams"])
        t.check("赛事名取可见文本而不是 href 路径",
                ms[0]["tour"] == "BLAST Premier Fall 2026 - Group A", ms[0]["tour"])
        t.check("Bo 号解析", ms[0]["bo"] == "Bo3" and ms[1]["bo"] == "Bo1",
                "%s/%s" % (ms[0]["bo"], ms[1]["bo"]))
        t.check("TBD vs TBD 被标成 tbd", ms[2]["tbd"] is True)
        t.check("正常对阵不算 tbd", ms[0]["tbd"] is False)
        t.check("按时间升序", [m["ts"] for m in ms] == sorted(m["ts"] for m in ms))
    t.check("空输入不炸", parse_matches("") == [])

    # ---- 2. 筛选 ----
    print("\n-- 2. 筛选（大赛 / 中国队 / 时间窗）--")
    base = datetime(2026, 9, 30, 9, 30, tzinfo=CST)
    ts = lambda h, m=0: base.replace(hour=h, minute=m).timestamp()

    mk = _mk
    cases = [
        ("大赛关键词命中", mk(ts(14), ["A", "B"], "BLAST Premier Fall"), True),
        ("关键词大小写不敏感", mk(ts(14), ["A", "B"], "blast premier"), True),
        ("中国队命中", mk(ts(14), ["TYLOO", "The MongolZ"], "Stake Ranked"), True),
        ("中国队大小写不敏感", mk(ts(14), ["tyloo", "x"], "Stake Ranked"), True),
        ("既非大赛也非中国队 → 丢掉", mk(ts(14), ["A", "B"], "Stake Ranked"), False),
        ("蒙古队不算中国队", mk(ts(14), ["The MongolZ", "IHC"], "Stake Ranked"), False),
        ("TBD 丢掉", mk(ts(14), ["TBD", "TBD"], "BLAST Premier", tbd=True), False),
        ("队名不全丢掉", mk(ts(14), ["TYLOO"], "BLAST Premier"), False),
        ("已开打的丢掉", mk(ts(8), ["TYLOO", "B"], "BLAST Premier"), False),
        ("明天的丢掉",
         mk((base + timedelta(days=1)).timestamp(), ["TYLOO", "B"], "IEM"), False),
        ("赛事名为空不算大赛", mk(ts(14), ["A", "B"], ""), False),
    ]
    for name, m, want in cases:
        got = select_today([m], base, es)
        t.check(name, bool(got) == want, "want=%s got=%s" % (want, bool(got)))

    t.check("边界：恰好此刻开打 → 保留",
            bool(select_today([mk(ts(9, 30), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("边界：此刻前 1 秒 → 丢掉",
            not select_today([mk(ts(9, 30) - 1, ["TYLOO", "B"], "IEM")], base, es))
    t.check("边界：今天 23:59 → 保留",
            bool(select_today([mk(ts(23, 59), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("空列表安全", select_today([], base, es) == [])

    # ---- 3. 折叠与正文 ----
    print("\n-- 3. 正文与折叠 --")
    many = [mk(ts(10) + i * 60, ["TYLOO", "X%d" % i], "IEM Cologne 2026") for i in range(20)]
    body = format_daily(many, base, 15)
    t.check("超过 fold_hint 会折叠", "只列了前 15 场" in body)
    t.check("折叠后仍写明总场次", "共 20 场" in body)
    t.check("正文末尾署名 Liquipedia", body.rstrip().endswith("数据来源：Liquipedia"))
    t.check("未超阈值时不折叠", "只列了前" not in format_daily(many[:3], base, 15))

    one = format_daily(many[:1], base, 15)
    t.check("单场正文含时间与对阵", "10:00" in one and "TYLOO vs X0" in one)
    t.check("单场正文含赛事名", "IEM Cologne 2026" in one)
    t.check("空列表返回空串（调用方据此静默）", format_daily([], base, 15) == "")

    fb = format_daily([mk(ts(14), ["TYLOO", "B"], "IEM", bo="")], base, 15)
    t.check("没有 Bo 信息时不写多余分隔符", "· ·" not in fb and fb.count("·") == 1)

    # ---- 4. 连续静默与报平安 ----
    print("\n-- 4. 连续静默 → 报平安 --")
    st = load_state(os.path.join(HERE, "__not-exist__.json"))
    t.check("状态文件不存在时给出默认值", st.get("empty_streak") == 0)

    st = {"empty_streak": 0}
    sends = []
    for i in range(1, 16):
        st, want = streak_step(st, False, 7, base)
        if want:
            sends.append(i)
    t.check("空日在第 7、14 天各发一次（不是天天发）", sends == [7, 14], sends)
    t.check("空日计数逐日累加", st["empty_streak"] == 15, st["empty_streak"])

    st2 = {"empty_streak": 6}
    st2, want = streak_step(st2, True, 7, base)
    t.check("有比赛则计数归零", st2["empty_streak"] == 0 and want is False)
    t.check("有比赛时不发报平安", want is False)

    st3, want3 = streak_step({"empty_streak": 3}, False, 0, base)
    t.check("calm_after=0 时永不发报平安", want3 is False)

    calm = format_calm(7)
    t.check("报平安写明「不是故障」", "不是故障" in calm)
    t.check("报平安末尾署名 Liquipedia", calm.rstrip().endswith("数据来源：Liquipedia"))

    # ---- 5. 告警文案与配置 ----
    print("\n-- 5. 告警文案 / 配置合并 --")
    fl = format_fetch_failed("HTTP 503 Service Unavailable", 3)
    t.check("抓取失败告警不写「今天没有比赛」", "不等于" in fl and "今天没有比赛" in fl)
    t.check("抓取失败告警给出排查命令", "esports.py --check" in fl)

    merged = resolve_config({"esports": {"fold_hint": 3}})
    t.check("用户配置覆盖默认值", merged["fold_hint"] == 3)
    t.check("未覆盖项保留默认值", merged["calm_after_empty_days"] == 7)
    t.check("默认白名单非空", bool(merged["major_keywords"]) and bool(merged["cn_teams"]))
    t.check("默认白名单不含蒙古队",
            not any("mongol" in t.lower() for t in merged["cn_teams"]))
    t.check("默认中国队白名单就是约定的 2 支",
            merged["cn_teams"] == ["TYLOO", "Lynn Vision Gaming"], merged["cn_teams"])
    t.check("已移出的队伍不再算中国队",
            not has_cn_team({"teams": ["Rare Atom", "X"]}, merged["cn_teams"]))
    t.check("已移出的队伍大小写混写也不算",
            not has_cn_team({"teams": ["rare atom", "X"]}, merged["cn_teams"]))
    t.check("保留的两支大小写混写仍算中国队",
            has_cn_team({"teams": ["tyloo", "X"]}, merged["cn_teams"])
            and has_cn_team({"teams": ["LYNN VISION GAMING", "X"]}, merged["cn_teams"]))
    t.check("解析空配置不炸", resolve_config({})["fold_hint"] == 15)

    ua = build_ua({"ua_contact": "me@example.com"})
    t.check("UA 含项目名与联系方式", "douyu-live-notify" in ua and "me@example.com" in ua)
    t.check("UA 缺失联系方式时有兜底", "contact-not-set" in build_ua({}))
    t.check("请求地址只用 api.php，不抓渲染页面",
            LIQUIPEDIA_API.endswith("/api.php") and "action=parse" in build_url())

    return t.done("selftest")


# ==========================================================================
# 入口
# ==========================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="CS2 每日赛程推送")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--check", action="store_true",
                        help="只抓取并打印将要发送的内容，不发消息、不写状态")
    parser.add_argument("--test-notify", action="store_true",
                        help="往配置的每个通道发一条测试消息（部署后先跑这个）")
    parser.add_argument("--selftest", action="store_true", help="离线自检，不联网")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    cfg = watch.load_config(args.config)

    if args.test_notify:
        return cmd_test_notify(cfg)

    # 与 watch.py 共用同一份 config.json：channels / onebot 段直接沿用。
    # 这里校验一遍，免得配置错了却「什么都没发生」——那正是最难排查的状态。
    if not watch.validate_cfg(cfg, args.config):
        log("[error] config.json 没配好，赛程预告会发不出去（见上面的报错）")
        return 2

    return run_once(cfg, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[stop] 已手动停止", flush=True)
