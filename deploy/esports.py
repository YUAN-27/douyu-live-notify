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
  · 有知名队伍参赛（队名精确命中 esports.notable_teams 白名单）

后两条是**精确匹配**队名，不是子串：`The MongolZ` 这种别国队伍不会被归成中国队，
`MOUZ NXT` 也不会因为主队 `MOUZ` 在白名单里就跟着混进来。大小写无所谓，
但**措辞必须和页面上一致**（`Team Liquid` ≠ `Liquid`）—— 写错了不报错、只会静默漏发，
所以改名单前先用 `--teams` 把页面上的真实队名打出来照着抄。

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
    python3 esports.py --teams         # 列出页面上的真实队名 + 是否已收录，用来校白名单
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

# 「知名队伍」白名单（同样是**精确匹配**队名）。
#
# 为什么需要它：真正的一线强队经常在 Stake Ranked / iBUYPOWER Masters / Leon.bet
# 这类小赛事里出场，光靠赛事名关键词会把它们全漏掉。用「有没有名队在打」兜底。
#
# ⚠️ 名字必须和 Liquipedia 页面上**完全一致**（大小写无所谓，措辞必须对）。
#    实测踩到的例子：
#      `Ninjas in Pyjamas` —— **不是** `NIP`
#      `PaiN Gaming`       —— **不是** `PaiN`
#      `Team Liquid`       —— **不是** `Liquid`
#      `FaZe Clan`         —— **不是** `FaZe`
#      `Natus Vincere`     —— **不是** `NAVI`
#      `FURIA`             —— **不是** `FURIA Esports`（这个曾在名单里待过，等于永远匹配不上）
#    大小写是忽略的：`HEROIC` / `heroic`、`Fnatic` / `fnatic` 都能匹配。
#    拿不准就跑 `python3 esports.py --teams`，把页面上的真实队名打出来照着抄
#    （它会同时标出哪些已收录、哪些还没收录）。
#
# 注：**学院队是独立队名**，精确匹配下不会误收。例如 `Natus Vincere Junior`
#     和 `MOUZ NXT` 与主队 `Natus Vincere`、`MOUZ` 互不影响。
#     `Falcons Force` 也不是 `Team Falcons`，同理不会被误收。
DEFAULT_NOTABLE_TEAMS = [
    # —— 2026-09-30 从 Liquipedia 实际页面上核对过 ————
    "100 Thieves", "3DMAX", "Alliance", "Astralis", "B8", "BIG", "FaZe Clan",
    "FlyQuest", "Fnatic", "GamerLegion", "HEROIC", "Imperial Esports",
    "Luminosity Gaming", "M80", "Nemiga Gaming", "Ninjas in Pyjamas", "NRG",
    "PaiN Gaming", "SAW", "Sangal Esports", "SINNERS Esports", "Team Liquid",
    "Wildcard",
    # —— 这批是 2026-10-05 用 Liquipedia 的 category 字段逐个验真过的 ——
    #    （`prop=categories` 里带「… Teams」才是战队页；否则可能是同名的选手页）
    "9z Team", "Aurora Gaming", "Cloud9", "ENCE", "Eternal Fire",
    "FURIA", "G2 Esports", "Gaimin Gladiators", "Legacy", "MIBR", "MOUZ",
    "Natus Vincere", "OG", "Passion UA", "Team Falcons", "Team Spirit",
    "Team Vitality", "The MongolZ", "Virtus.pro",
]

# HLTV 队名 → Liquipedia 队名。**只有写法不一致的才需要写进来。**
#
# 为什么需要这张表：世界排名从 HLTV 抓，但比赛页在 Liquipedia，两边**队名写法不一样**。
# HLTV 写 `Spirit`，Liquipedia 的战队页叫 `Team Spirit`。照原样拿 HLTV 的名字去匹配
# Liquipedia 的比赛页，会**全部匹配不上、而且不报错** —— 静默漏发，最难查的那种。
#
# ⚠️ **不要用「查重定向自动补全」的办法**。实测过：Liquipedia 上确实存在
#    标题就叫 `Spirit` 和 `Aurora` 的页面，但它们是**选手个人页**，不是战队。
#    自动补全会把「HLTV 世界第 1」映射到一个选手身上，然后静默失效。
#    所以这张表只能**逐个核对后手工维护**（`prop=categories` 里带「… Teams」才是战队页）。
#
# 核对日期 2026-10-05（对应 HLTV 2026-09-28 那期排名）：
#   HLTV 写法         → Liquipedia 战队页
#   Spirit            → Team Spirit          （`Spirit` 是选手页，别搞错）
#   Vitality          → Team Vitality
#   FURIA             → FURIA                （同名，不用写；列出来是为了说明已核对）
#   MOUZ              → MOUZ                 （同名）
#   Falcons           → Team Falcons
#   Legacy            → Legacy               （同名）
#   FUT               → FUT Esports
#   G2                → G2 Esports
#   Aurora            → Aurora Gaming        （`Aurora` 是选手页，别搞错）
#   Natus Vincere     → Natus Vincere        （同名）
#   Astralis          → Astralis             （同名）
#   MIBR              → MIBR                 （同名）
#   FaZe              → FaZe Clan
#   B8                → B8                   （同名）
#   BETBOOM           → BetBoom Team         （Liquipedia 上根本没有 BETBOOM 这个标题）
#
# 没写进这张表、又不是同名的队，会在运行时打 `[warn]` 提醒（见 resolve_rank_names），
# 不会静默漏掉 —— 新队伍进前十时照着上面那行抄一遍加法即可。
DEFAULT_HLTV_ALIASES = {
    "Spirit": "Team Spirit",
    "Vitality": "Team Vitality",
    "Falcons": "Team Falcons",
    "FUT": "FUT Esports",
    "G2": "G2 Esports",
    "Aurora": "Aurora Gaming",
    "FaZe": "FaZe Clan",
    "BETBOOM": "BetBoom Team",
    # 下面这些两边写法一致，不用映射。列出来是为了下次核对时一眼看到「已核过」。
    # FURIA / MOUZ / Legacy / Natus Vincere / Astralis / MIBR / B8
}

ESPORT_DEFAULTS = {
    # 总开关。关掉后本脚本立刻退出，systemd 那边不会当成失败。
    "enabled": True,
    # Liquipedia 要求在 User-Agent 里写联系方式，格式：项目地址 + 邮箱。
    "ua_contact": "https://github.com/YUAN-27/douyu-live-notify; 1249850641@qq.com",
    "major_keywords": DEFAULT_MAJOR_KEYWORDS,
    "cn_teams": DEFAULT_CN_TEAMS,
    "notable_teams": DEFAULT_NOTABLE_TEAMS,
    # ---- 世界排名（HLTV）----
    # 抓 HLTV 世界排名的前几名，用来做「有世界前 N 的队伍参赛」这条。
    "rank_top_n": 15,
    "hltv_aliases": DEFAULT_HLTV_ALIASES,
    # 排名缓存多久（小时）。HLTV 每周一更新一次，所以 7 天足够，别设太小徒增请求。
    "rank_ttl_hours": 168,
    # 同一个赛事当天出现多少支「已知队伍」就把该赛事的比赛整体放行。0 = 关掉这条。
    "tournament_min_known_teams": 4,
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


def fetch_once(url, ua, timeout=25, accept="application/json"):
    """发一次请求。返回 dict：ok / status / text / error。

    http_request 已经处理了 gzip 解压和自定义头覆盖，这里只要把要求的头传进去
    （它的默认 Accept-Encoding 是 identity，会被我们覆盖）。
    """
    try:
        text = watch.http_request(url, headers={
            "User-Agent": ua,
            "Accept": accept,
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
# 世界排名（HLTV）
# ==========================================================================

# HLTV 的排名页。请求 `/ranking/teams/` 会 302 到当前那一期的带日期 URL，
# urllib 默认跟随重定向，所以直接请求这个短地址即可，不用自己算日期。
HLTV_RANK_URL = "https://www.hltv.org/ranking/teams/"
RANK_BLOCK_MARK = '<div class="ranked-team standard-box">'
RANK_CACHE_FILE = os.path.join(HERE, "rank_cache.json")


def parse_hltv_rank(html_text, top_n=15):
    """从 HLTV 排名页里切出前 top_n 名。返回 [{"pos": 1, "name": "Spirit"}, ...]。

    锚点是 `<div class="ranked-team standard-box">`（实测一页 251 个块）。
    每块的结构（2026-10 实测）：
        <span class="position wide-position">#1</span>
        <span class="name">Spirit</span>

    ⚠️ 取的是 `.name` 里的**显示名**，不是 `img alt` —— 两者实测一致，
    但 `.name` 更贴近用户看到的写法，万一将来分叉，跟人能对得上的那个更该信。
    """
    if not html_text:
        return []
    blocks = re.findall(
        re.escape(RANK_BLOCK_MARK) + r"(.*?)(?=" + re.escape(RANK_BLOCK_MARK) + r"|\Z)",
        html_text, re.S)
    out = []
    for b in blocks:
        mname = re.search(r'class="name">([^<]+)<', b)
        if not mname:
            continue
        name = clean(mname.group(1))
        if not name:
            continue
        mpos = re.search(r'class="position[^"]*">\s*#(\d+)', b)
        out.append({"pos": int(mpos.group(1)) if mpos else len(out) + 1, "name": name})
        if top_n and len(out) >= top_n:
            break
    return out


def resolve_rank_names(rank, es):
    """把 HLTV 的队名翻成 Liquipedia 队名。返回 (names, unmapped)。

    翻不出来的**原样保留**，同时记进 unmapped 交给调用方打 `[warn]` ——
    故意不静默丢弃：静默丢掉就会变成「这条规则看着在跑、实际漏掉一半」，
    正是这个功能最难查的故障形态。
    """
    raw_alias = es.get("hltv_aliases")
    alias_lc = {}
    if isinstance(raw_alias, dict):
        for k, v in raw_alias.items():
            if k and v:
                alias_lc[str(k).strip().lower()] = str(v).strip()
    known_lc = {str(t).strip().lower() for t in (es.get("notable_teams") or []) if t}

    names, unmapped = [], []
    for it in rank:
        n = str(it.get("name") or "").strip()
        if not n:
            continue
        mapped = alias_lc.get(n.lower())
        if mapped:
            out = mapped
        else:
            out = n
            # 写法已经在知名名单里出现过 → 说明这个写法是对的，不用提醒；
            # 两边都没见过 → 很可能写法不一致，提醒去核对。
            if n.lower() not in known_lc:
                unmapped.append(it)
        if out not in names:
            names.append(out)
    return names, unmapped


def load_rank_cache(path=RANK_CACHE_FILE):
    """读排名缓存。只在抓取失败时当兜底用，正常路径不读它。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        if isinstance(d, dict) and isinstance(d.get("names"), list) and d["names"]:
            return d
    except (OSError, ValueError):
        pass
    return None


def save_rank_cache(rank, names, path=RANK_CACHE_FILE):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump({
                "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
                "rank": rank,
                "names": names,
            }, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log("[warn] 排名缓存写不进去（不影响本轮）：%s" % exc)


def get_rank(es, write_cache=True):
    """取世界排名，返回 dict：

        ok / disabled / rank / names / unmapped / source / error

    `source` 是 "hltv" / "cache" / ""，用来解释这批名字是哪来的。
    抓取失败**不是**致命错误 —— 世界排名只是四条入选依据之一，
    另外三条（中国队 / 大赛+知名 / 名队云集的赛事）照常工作。所以这里只打 [warn]。
    """
    top_n = max(0, int(es.get("rank_top_n") or 0))
    if top_n <= 0:
        log("[info] esports.rank_top_n = 0，本轮不做「世界前 N」这条筛选")
        return {"ok": False, "disabled": True, "rank": [], "names": [],
                "unmapped": [], "source": "", "error": ""}

    ua = build_ua(es)
    timeout = max(5, int(es.get("http_timeout") or 25))
    r = fetch_once(HLTV_RANK_URL, ua, timeout=timeout, accept="text/html")
    rank = parse_hltv_rank(r["text"], top_n) if r["ok"] else []
    if not rank:
        why = r["error"] or "页面结构可能变了，一个排名块都没切出来"
        cached = load_rank_cache()
        if cached:
            names, unmapped = resolve_rank_names(
                [{"pos": None, "name": x} for x in cached["names"]], es)
            log("[warn] 世界排名抓取失败（%s），改用 %s 的缓存"
                % (why, cached.get("fetched_at") or "上次"))
            return {"ok": True, "disabled": False, "rank": [], "names": names,
                    "unmapped": unmapped, "source": "cache", "error": why}
        log("[warn] 世界排名抓取失败（%s），本轮**没有**「世界前 %d」这条依据"
            % (why, top_n))
        log("        另外三条（中国队 / 大赛+知名队伍 / 名队云集的赛事）不受影响。")
        return {"ok": False, "disabled": False, "rank": [], "names": [],
                "unmapped": [], "source": "", "error": why}

    names, unmapped = resolve_rank_names(rank, es)
    if unmapped:
        log("[warn] 世界前 %d 里有 %d 支队伍的写法没核对过，可能匹配不上 Liquipedia："
            % (top_n, len(unmapped)))
        for it in unmapped:
            log("        第 %s 名 %s" % (it.get("pos"), it.get("name")))
        log("        核对办法：`--teams` 看页面真实队名；确认后写进 esports.hltv_aliases。")
    if write_cache:
        save_rank_cache(rank, names)
    return {"ok": True, "disabled": False, "rank": rank, "names": names,
            "unmapped": unmapped, "source": "hltv", "error": ""}


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


def has_team(m, names):
    """队名精确命中白名单（大小写不敏感，两端空白已由 tidy 去掉）。

    **是精确匹配、不是子串匹配**，两个理由：
      · 子串匹配会把 `The MongolZ` 这类别国队伍当成中国队；
      · 也会让 `MOUZ` 命中学院队 `MOUZ NXT` —— 那是另一支队。
    """
    allow = {(t or "").strip().lower() for t in (names or [])}
    if not allow:
        return False
    return any((t or "").strip().lower() in allow for t in (m.get("teams") or []))


def known_team_set(es, rank_names=None):
    """「已知队伍」的集合（小写）= 知名名单 ∪ 世界前 N。

    只用于**赛事维度的计数**（同个赛事凑够几支算「名队云集」）。
    中国队另有一套「无条件发」的规则，不并进来 —— 两件事分开才好解释。
    """
    s = {str(t).strip().lower() for t in (es.get("notable_teams") or []) if t}
    s |= {str(t).strip().lower() for t in (rank_names or []) if t}
    return s


# 入选原因 → 中文标签。日志和 --check 都用它，免得两处文案走偏。
REASON_LABELS = {
    "cn": "中国队",
    "top15": "世界前 N",
    "major+notable": "大赛+知名",
    "tournament": "名队云集的赛事",
}


def why_selected(m, es, rank_names=None, tour_known=None, min_known=0):
    """这场比赛凭哪一条被选中。四条是**或**关系，按「越具体越优先」只记第一条：

      'cn'            有中国队参赛            → 无条件发
      'top15'         有世界前 N 的队伍参赛    → 无条件发
      'major+notable' 大赛 **且** 有知名队伍   → **两条同时满足**才发
      'tournament'    同一赛事当天凑够 N 支已知队伍 → 该赛事当天的比赛整体放行

    ⚠️ 第三条是「与」不是「或」：只满足大赛、或只满足有知名队伍，都**不算**。
    这是 2026-10-05 按用户要求收紧的 —— 之前是三条或关系，太吵。
    """
    if has_team(m, es.get("cn_teams")):
        return "cn"
    if rank_names and has_team(m, rank_names):
        return "top15"
    if is_major(m, es.get("major_keywords")) and has_team(m, es.get("notable_teams")):
        return "major+notable"
    if min_known > 0 and tour_known:
        tour = (m.get("tour") or "").strip()
        if tour and (tour_known.get(tour) or 0) >= min_known:
            return "tournament"
    return None


def select_with_reasons(matches, now, es, rank_names=None):
    """返回 (入选场次, {赛事名: 已知队伍数}, 计数)。

    拆出这一层是为了让筛选和统计**用同一份中间结果**，不会出现
    「列表里有这场、但理由统计说没有」这种自相矛盾。
    """
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    t0, t1 = day0.timestamp(), day1.timestamp()
    nowts = now.timestamp()

    todo = []
    for m in matches:
        if m.get("tbd"):                       # TBD vs TBD：对阵都没定
            continue
        if len(m.get("teams") or []) < 2:       # 队名没解析全，不敢推
            continue
        if not (t0 <= m["ts"] < t1):            # 只看今天（北京时间）
            continue
        if m["ts"] < nowts:                     # 只预告还没开打的
            continue
        todo.append(m)

    min_known = int(es.get("tournament_min_known_teams") or 0)
    known = known_team_set(es, rank_names)
    tour_known = {}
    if min_known > 0 and known:
        # 只数「今天还没开打」的场次；同一支队在同一个赛事里打两场只算一支。
        for m in todo:
            tour = (m.get("tour") or "").strip()
            if not tour:
                continue
            slot = tour_known.setdefault(tour, set())
            for t in m["teams"]:
                if (t or "").strip().lower() in known:
                    slot.add(t.strip().lower())
        tour_known = {k: len(v) for k, v in tour_known.items()}

    picked, counts = [], {"cn": 0, "top15": 0, "major+notable": 0, "tournament": 0}
    for m in todo:
        r = why_selected(m, es, rank_names, tour_known, min_known)
        if r:
            picked.append(m)
            counts[r] += 1
    return picked, tour_known, counts


def select_today(matches, now, es, rank_names=None):
    """挑出「今天还没开打、对阵已定，且满足入选口径」的比赛（只要场次列表）。

    口径见 why_selected。为什么只要「还没开打」：这是一条**赛程预告**，
    09:30 发出去的时候，今天凌晨那几场早就打完了，列出来只会让人以为还有比赛可看。
    """
    return select_with_reasons(matches, now, es, rank_names)[0]


def pick_reasons(picked, es, rank_names=None, tour_known=None, min_known=0):
    """统计这些场次各靠什么进来的，供 --check 打印。"""
    counts = {"cn": 0, "top15": 0, "major+notable": 0, "tournament": 0}
    for m in picked:
        r = why_selected(m, es, rank_names, tour_known, min_known)
        if r:
            counts[r] += 1
    return counts


def reasons_summary(counts):
    """把计数拼成一行，顺序固定（方便对比两次运行的差异）。"""
    return " / ".join("%s %d 场" % (REASON_LABELS[k], counts.get(k, 0))
                      for k in ("cn", "top15", "major+notable", "tournament"))


# ==========================================================================
# 消息
# ==========================================================================

WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 赛事名缺失时的分组兜底，免得冒出一行空白标题
NO_TOUR = "（未标注赛事）"


def _day_label(d):
    """`10-05 周一` —— 这是「今天」的预告，年份写出来只占地方。"""
    return "%02d-%02d %s" % (d.month, d.day, WEEKDAYS_CN[d.weekday()])


def _group_by_tour(picked, fold_hint):
    """先按 fold_hint 截断，再按赛事名分组；返回 (截断后的列表, [(赛事名, [比赛…]), …])。

    为什么要分组：同一天的比赛常常是同一个赛事的连续几场，逐场重复赛事名会把
    「时间 + 对阵」这个真正的信息挤到一边（实测 6 场里赛事名一模一样重复 6 次）。
    分组后赛事名只写一次，作为小标题。保持首次出现顺序（= 时间顺序）。
    """
    shown = picked
    if fold_hint and fold_hint > 0 and len(picked) > fold_hint:
        shown = picked[:fold_hint]

    order, groups = [], {}
    for m in shown:
        key = (m.get("tour") or "").strip() or NO_TOUR
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)
    return shown, [(k, groups[k]) for k in order]


def _group_bo(group):
    """组内 Bo 是否一致。一致就提到小标题上 —— 每行都写「Bo3」是纯噪音。"""
    bos = {(m.get("bo") or "").strip() for m in group}
    if len(bos) == 1:
        return bos.pop() or None
    return None


def _marks(m, cn_teams, rank_names, rank_n):
    """行尾标记，让「中国队 / 世界前 N」一眼可见。两条都命中的就都标。"""
    out = []
    if cn_teams and has_team(m, cn_teams):
        out.append("中国队")
    if rank_names and has_team(m, rank_names):
        out.append("世界前%d" % rank_n if rank_n else "世界前列")
    return out


def format_daily(picked, now, es, rank_names=None):
    """拼「今日赛程」正文。末尾必须署名 Liquipedia（CC-BY-SA 3.0 的要求）。

    版式：赛事名当小标题只出现一次，下面每场一行「时间  对阵」；
    同赛事内 Bo 一致就写在小标题上，不一致才逐场标。
    """
    if not picked:
        return ""
    es = es or {}
    shown, grouped = _group_by_tour(picked, es.get("fold_hint"))
    cn_teams = es.get("cn_teams")
    rank_n = es.get("rank_top_n") or 0

    lines = ["【CS2 今日赛程】%s" % _day_label(now), ""]
    for name, group in grouped:
        bo = _group_bo(group)
        lines.append(name + (" · " + bo if bo else ""))
        # Bo 不一致时逐场标，免得读者以为整组都是同一个 Bo
        per_line = bo is None and any((m.get("bo") or "").strip() for m in group)
        for m in group:
            d = datetime.fromtimestamp(m["ts"], CST)
            seg = "  %s  %s" % (d.strftime("%H:%M"), " vs ".join(m["teams"]))
            if per_line and (m.get("bo") or "").strip():
                seg += " · " + m["bo"]
            mk = _marks(m, cn_teams, rank_names, rank_n)
            if mk:
                seg += "  ← " + "+".join(mk)
            lines.append(seg)
        lines.append("")

    if len(shown) < len(picked):
        lines.append("共 %d 场，只列了前 %d 场（后面还有）· 数据来源：Liquipedia"
                     % (len(picked), len(shown)))
    else:
        lines.append("共 %d 场 · 数据来源：Liquipedia" % len(picked))
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

    # ---- 世界排名（HLTV，可选依据）----
    # --check 不写缓存：它承诺「不发送、不写状态」，那就不该留下任何痕迹。
    rank = get_rank(es, write_cache=not args.check)
    log("[info] 世界前 %s：%s"
        % (es.get("rank_top_n"),
           ("取到 %d 支（来源：%s）" % (len(rank["names"]), rank["source"]))
           if rank["ok"] else ("已关闭" if rank["disabled"] else "本轮不可用")))

    picked, tour_known, rc = select_with_reasons(matches, now, es, rank["names"])
    log("[info] 页面共 %d 场，其中今天还没开打且符合条件的有 %d 场"
        % (len(matches), len(picked)))
    if picked:
        log("[info] 入选依据（每场只记第一条命中的）：%s" % reasons_summary(rc))
        # 「名队云集的赛事」是兜底那条，命中它的场次往往已经被更靠前的依据捞进来了，
        # 所以在计数里会显示 0 场 —— 但它的贡献其实不小。这里单独把它说清楚。
        min_known = int(es.get("tournament_min_known_teams") or 0)
        if min_known > 0:
            crowded = {}
            for m in picked:
                tour = (m.get("tour") or "").strip()
                if tour and (tour_known.get(tour) or 0) >= min_known:
                    crowded[tour] = crowded.get(tour, 0) + 1
            if crowded:
                log("[info] 名队云集（已知队伍 >= %d 支）的赛事：%s"
                    % (min_known, "；".join("%s（入选 %d 场）" % (k, v)
                                            for k, v in sorted(crowded.items()))))

    # ---- 组装 ----
    state = load_state()
    body = format_daily(picked, now, es, rank["names"])

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
        if picked:
            log("")
            log("[check] 逐场入选依据（每场只记第一条命中的）：")
            min_known = int(es.get("tournament_min_known_teams") or 0)
            for m in picked:
                r = why_selected(m, es, rank["names"], tour_known, min_known)
                d = datetime.fromtimestamp(m["ts"], CST)
                log("        %s  %-10s %s"
                    % (d.strftime("%H:%M"), REASON_LABELS.get(r, r or "?"),
                       " vs ".join(m["teams"])))
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

def cmd_list_teams(cfg):
    """把页面上出现的队名全打出来，并标出它命中哪个名单 —— 维护白名单用。

    只读：不发消息、不写状态（连排名缓存都不写）。但**会真的联网抓一次**，
    所以别放进循环里跑。
    """
    es = resolve_config(cfg)
    rank = get_rank(es, write_cache=False)
    rank_names = rank["names"]

    r = fetch_matches(es)
    if r is None or not r["ok"]:
        log("[error] 抓取失败：%s" % ((r or {}).get("error") or "未知错误"))
        return 1
    html_text, err = extract_html(r["text"])
    if html_text is None:
        log("[error] %s" % err)
        return 1
    matches = parse_matches(html_text)
    if not matches:
        log("[error] 一个比赛都没解析出来 —— 页面结构可能变了")
        return 1

    seen = {}
    for m in matches:
        for name in m["teams"]:
            slot = seen.setdefault(
                name, {"cn": False, "notable": False, "top15": False, "n": 0})
            slot["n"] += 1
            if has_team({"teams": [name]}, es.get("cn_teams")):
                slot["cn"] = True
            if has_team({"teams": [name]}, es.get("notable_teams")):
                slot["notable"] = True
            if rank_names and has_team({"teams": [name]}, rank_names):
                slot["top15"] = True

    log("页面共 %d 场，出现 %d 个不同队名。" % (len(matches), len(seen)))
    log("标记：[中国队] / [知名] / [世界前%s]。想把某队加进白名单，"
        "把名字**原样**抄进 config.json 的 esports.cn_teams / notable_teams。"
        % es.get("rank_top_n"))
    log("")
    for name in sorted(seen, key=lambda x: x.lower()):
        it = seen[name]
        tags = (("[中国队]" if it["cn"] else "")
                + ("[知名]" if it["notable"] else "")
                + ("[世界前%s]" % es.get("rank_top_n") if it["top15"] else ""))
        log("  %-12s %-28s 出场 %d 次" % (tags or "[未收录]", name, it["n"]))

    blanks = [n for n in seen
              if not seen[n]["cn"] and not seen[n]["notable"] and not seen[n]["top15"]]
    log("")
    log("未收录 %d 个 —— 其中觉得算「知名」的，抄进 notable_teams 即可。" % len(blanks))
    log("⚠️ 精确匹配：大小写无所谓，但**措辞必须原样**。"
        "`Team Liquid` 和 `Liquid`、`NIP` 和 `Ninjas in Pyjamas` 不是一回事。")
    return 0


def cmd_show_rank(cfg):
    """打印 HLTV 世界前 N，以及它翻成 Liquipedia 队名后的结果 —— 核对别名用。

    只读：不发消息、不写状态（不写排名缓存）。这是排查
    「世界前 15 那条怎么没生效」的第一手段：一眼看出哪个名字没映射上。
    """
    es = resolve_config(cfg)
    rank = get_rank(es, write_cache=False)
    top_n = es.get("rank_top_n")
    if rank["disabled"]:
        log("[info] esports.rank_top_n = 0，「世界前 N」这条已关闭，没什么可看的")
        return 0
    if not rank["ok"]:
        log("[error] 拿不到世界排名：%s" % rank["error"])
        return 1

    log("HLTV 世界前 %s（来源：%s）" % (top_n, rank["source"]))
    log("")
    log("  %-5s %-18s %s" % ("名次", "HLTV 写法", "→ Liquipedia 队名"))
    log("  " + "-" * 56)

    raw_alias = es.get("hltv_aliases")
    alias_lc = {}
    if isinstance(raw_alias, dict):
        for k, v in raw_alias.items():
            if k and v:
                alias_lc[str(k).strip().lower()] = str(v).strip()
    known_lc = {str(t).strip().lower() for t in (es.get("notable_teams") or []) if t}

    for it in rank["rank"]:
        n = it["name"]
        mapped = alias_lc.get(n.lower())
        if mapped:
            note = "→ %s   （别名表）" % mapped
        elif n.lower() in known_lc:
            note = "→ %s   （同名，已知名单里核对过）" % n
        else:
            note = "→ %s   ⚠️ 写法没核对过，可能匹配不上" % n
        log("  #%-4s %-18s %s" % (it.get("pos"), n, note))

    log("")
    log("本轮实际用于匹配的队名（%d 个）：%s" % (len(rank["names"]), "、".join(rank["names"])))
    if rank["unmapped"]:
        log("")
        log("⚠️ 有 %d 支的写法没核对过。核对办法：`--teams` 看页面上的真实队名，"
            "确认后写进 config.json 的 esports.hltv_aliases（HLTV 写法 → Liquipedia 写法）。"
            % len(rank["unmapped"]))
    log("")
    log("⚠️ 别用「查重定向自动补全」的办法：Liquipedia 上 `Spirit`、`Aurora` 是**选手页**，"
        "自动补全会把世界第一映射到选手身上，然后静默失效。")
    return 0


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

# HLTV 排名页的骨架。结构照 2026-10-05 实测的样子裁：
#   外层 <div class="ranked-team standard-box">，里面 .position 是名次、.name 是队名。
# 故意留了「同一块里出现两次 .name 之外的东西」，用来验证我们取的是 .name 而不是别的。
FIXTURE_RANK_HTML = """
<div class="ranked-team standard-box">
  <div class="bg-holder">
    <div class="ranking-header">
      <span class="position wide-position">#1</span>
      <span class="team-logo"><img alt="Spirit" title="Spirit"></span>
      <div class="relative">
        <div class="teamLine sectionTeamPlayers"><span class="name">Spirit</span><span class="points">(1000 HLTV points)</span></div>
      </div>
    </div>
  </div>
</div>
<div class="ranked-team standard-box">
  <div class="bg-holder">
    <div class="ranking-header">
      <span class="position wide-position">#2</span>
      <span class="team-logo"><img alt="Vitality" title="Vitality"></span>
      <div class="relative">
        <div class="teamLine sectionTeamPlayers"><span class="name">Vitality</span><span class="points">(900 HLTV points)</span></div>
      </div>
    </div>
  </div>
</div>
<div class="ranked-team standard-box">
  <div class="bg-holder">
    <div class="ranking-header">
      <span class="position wide-position">#3</span>
      <span class="team-logo"><img alt="FURIA" title="FURIA"></span>
      <div class="relative">
        <div class="teamLine sectionTeamPlayers"><span class="name">FURIA</span><span class="points">(870 HLTV points)</span></div>
      </div>
    </div>
  </div>
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
    print("\n-- 2. 筛选（中国队 / 世界前N / 大赛且知名 / 时间窗）--")
    base = datetime(2026, 9, 30, 9, 30, tzinfo=CST)
    ts = lambda h, m=0: base.replace(hour=h, minute=m).timestamp()

    mk = _mk
    TOP = ["Team Spirit", "Team Vitality", "G2 Esports"]   # 假装的世界前 N

    cases = [
        # 1) 有中国队 —— 无条件发，赛事名和对手都不沾也发
        ("中国队 → 无条件发", mk(ts(14), ["TYLOO", "X"], "Stake Ranked"), None, True),
        ("中国队大小写不敏感", mk(ts(14), ["tyloo", "x"], "Stake Ranked"), None, True),
        # 2) 有世界前 N —— 无条件发
        ("世界前N → 无条件发", mk(ts(14), ["Team Spirit", "X"], "Stake Ranked"), TOP, True),
        # 3) 大赛 **且** 有知名队伍 —— 两条同时满足才发
        ("大赛 + 知名 → 发", mk(ts(14), ["FaZe Clan", "X"], "BLAST Premier Fall"), None, True),
        ("只有大赛、没知名队伍 → 丢掉", mk(ts(14), ["A", "B"], "BLAST Premier Fall"), None, False),
        ("只有知名队伍、不是大赛 → 丢掉",
         mk(ts(14), ["FaZe Clan", "X"], "Stake Ranked"), None, False),
        ("大小写不敏感（blast premier + faze clan）",
         mk(ts(14), ["faze clan", "X"], "blast premier"), None, True),
        # 4) 其它边界
        ("四条都不沾 → 丢掉", mk(ts(14), ["A", "B"], "Stake Ranked"), None, False),
        ("蒙古队既不算中国队也不在知名名单里",
         mk(ts(14), ["IHC", "ATOX"], "Stake Ranked"), None, False),
        ("学院队不算主队（MOUZ NXT）",
         mk(ts(14), ["MOUZ NXT", "X"], "BLAST Premier Fall"), None, False),
        ("TBD 丢掉", mk(ts(14), ["TBD", "TBD"], "BLAST Premier", tbd=True), None, False),
        ("队名不全丢掉", mk(ts(14), ["TYLOO"], "BLAST Premier"), None, False),
        ("已开打的丢掉", mk(ts(8), ["TYLOO", "B"], "BLAST Premier"), None, False),
        ("明天的丢掉",
         mk((base + timedelta(days=1)).timestamp(), ["TYLOO", "B"], "IEM"), None, False),
        ("赛事名为空不算大赛，但中国队仍发", mk(ts(14), ["TYLOO", "B"], ""), None, True),
        ("赛事名为空 + 没中国队 → 丢掉", mk(ts(14), ["A", "B"], ""), None, False),
    ]
    for name, m, top, want in cases:
        got = select_today([m], base, es, top)
        t.check(name, bool(got) == want, "want=%s got=%s" % (want, bool(got)))

    t.check("蒙古队不算中国队（直接断言 has_team）",
            not has_team({"teams": ["The MongolZ", "IHC"]}, es["cn_teams"]))

    t.check("边界：恰好此刻开打 → 保留",
            bool(select_today([mk(ts(9, 30), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("边界：此刻前 1 秒 → 丢掉",
            not select_today([mk(ts(9, 30) - 1, ["TYLOO", "B"], "IEM")], base, es))
    t.check("边界：今天 23:59 → 保留",
            bool(select_today([mk(ts(23, 59), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("空列表安全", select_today([], base, es) == [])

    # ---- 2b. 「名队云集的赛事」整体放行 ----
    print("\n-- 2b. 名队云集的赛事（同一赛事当天 >= 4 支已知队伍）--")
    tour = "Stake Ranked Season 9"
    crowd = [
        mk(ts(10), ["FaZe Clan", "M80"], tour),          # 2 支知名
        mk(ts(12), ["Team Liquid", "Wildcard"], tour),   # 再 2 支 → 共 4 支
        mk(ts(14), ["Nobody A", "Nobody B"], tour),      # 一对无名队
    ]
    t.check("凑够 4 支 → 该赛事当天的比赛都发（含无名队那场）",
            len(select_today(crowd, base, es)) == 3,
            "实际 %d 场" % len(select_today(crowd, base, es)))
    t.check("同一支队打两场只算一支（3 支 → 不放行）",
            select_today([mk(ts(10), ["FaZe Clan", "M80"], tour),
                          mk(ts(12), ["FaZe Clan", "Wildcard"], tour),
                          mk(ts(14), ["Nobody A", "Nobody B"], tour)], base, es) == [])
    t.check("只有 2 支时不放行", select_today(crowd[:1], base, es) == [])
    t.check("tournament_min_known_teams = 0 时这条整条关掉",
            select_today(crowd, base, dict(es, tournament_min_known_teams=0)) == [])
    t.check("世界前 N 的队也计入「已知队伍」",
            len(select_today([mk(ts(10), ["Team Spirit", "M80"], tour),
                              mk(ts(12), ["Team Liquid", "Wildcard"], tour),
                              mk(ts(14), ["Nobody A", "Nobody B"], tour)],
                             base, es, TOP)) == 3)
    t.check("不同赛事各算各的（不跨赛事凑数）",
            select_today([mk(ts(10), ["FaZe Clan", "M80"], "赛事甲"),
                          mk(ts(12), ["Team Liquid", "Wildcard"], "赛事乙")],
                         base, es) == [])

    # ---- 2c. 世界排名：解析 + 别名映射 ----
    print("\n-- 2c. 世界排名（HLTV）解析与别名映射 --")
    rk = parse_hltv_rank(FIXTURE_RANK_HTML, 15)
    t.check("排名块按锚点切出 3 支", len(rk) == 3, rk)
    if len(rk) == 3:
        t.check("名次与队名都取对", rk[0] == {"pos": 1, "name": "Spirit"}, rk[0])
        t.check("名次顺序正确", [x["pos"] for x in rk] == [1, 2, 3])
    t.check("top_n 截断生效", len(parse_hltv_rank(FIXTURE_RANK_HTML, 2)) == 2)
    t.check("排名页为空时不炸", parse_hltv_rank("", 15) == [])

    names, unmapped = resolve_rank_names(
        [{"pos": 1, "name": "Spirit"}, {"pos": 2, "name": "Astralis"},
         {"pos": 3, "name": "Weird New Team"}], es)
    t.check("别名表把 Spirit 翻成 Team Spirit", "Team Spirit" in names, names)
    t.check("两边写法相同的队原样保留", "Astralis" in names, names)
    t.check("没映射过又没核对过的 → 记进 unmapped",
            [u["name"] for u in unmapped] == ["Weird New Team"], unmapped)
    t.check("别名查表不区分大小写",
            resolve_rank_names([{"pos": 1, "name": "spirit"}], es)[0] == ["Team Spirit"])
    t.check("写法已在知名名单里出现过的，不算 unmapped",
            resolve_rank_names([{"pos": 1, "name": "MOUZ"}], es)[1] == [])
    t.check("别名表把 Spirit 指向战队页而不是同名的选手页",
            DEFAULT_HLTV_ALIASES["Spirit"] == "Team Spirit")
    t.check("别名表把 Aurora 指向战队页而不是同名的选手页",
            DEFAULT_HLTV_ALIASES["Aurora"] == "Aurora Gaming")
    t.check("别名表收录 HLTV 的 BETBOOM 写法",
            DEFAULT_HLTV_ALIASES.get("BETBOOM") == "BetBoom Team")
    t.check("rank_top_n = 0 时这条整体关闭",
            get_rank(dict(es, rank_top_n=0), write_cache=False)["disabled"] is True)
    t.check("排名拿不到时不是致命错误（ok=False 但不抛）",
            get_rank(dict(es, rank_top_n=0), write_cache=False)["ok"] is False)
    t.check("已知队伍集合 = 知名 ∪ 世界前N",
            known_team_set(es, TOP) >= {"faze clan", "team spirit", "g2 esports"})
    t.check("已知队伍集合不含中国队（两件事分开）",
            known_team_set(dict(es, notable_teams=[]), None) == set())

    # ---- 3. 正文与折叠 ----
    print("\n-- 3. 正文与折叠 --")
    many = [mk(ts(10) + i * 60, ["TYLOO", "X%d" % i], "IEM Cologne 2026") for i in range(20)]
    body = format_daily(many, base, es)
    t.check("超过 fold_hint 会折叠", "只列了前 15 场" in body)
    t.check("折叠后仍写明总场次", "共 20 场" in body)
    t.check("正文末尾署名 Liquipedia", body.rstrip().endswith("数据来源：Liquipedia"))
    t.check("未超阈值时不折叠", "只列了前" not in format_daily(many[:3], base, es))

    one = format_daily(many[:1], base, es)
    t.check("单场正文含时间与对阵", "10:00" in one and "TYLOO vs X0" in one)
    t.check("单场正文含赛事名", "IEM Cologne 2026" in one)
    t.check("空列表返回空串（调用方据此静默）", format_daily([], base, es) == "")

    fb = format_daily([mk(ts(14), ["TYLOO", "B"], "IEM", bo="")], base, es)
    t.check("没有 Bo 信息时不写多余分隔符", "· ·" not in fb and fb.count("·") == 1)

    # 版式：赛事名当小标题只写一次，每场一行「时间  对阵」
    head = body.splitlines()[0]
    t.check("标题是「月-日 周几」，不写年份",
            head == "【CS2 今日赛程】%s" % _day_label(base)
            and str(base.year) not in head)
    t.check("同名赛事只出现一次（分组，不再逐场重复）",
            body.count("IEM Cologne 2026") == 1)
    t.check("对阵行不再重复赛事名",
            all("IEM Cologne 2026" not in ln for ln in body.splitlines()
                if "TYLOO vs" in ln))
    t.check("对阵行有时间、对阵，且缩进两格",
            "  10:00  TYLOO vs X0" in body)
    t.check("组内 Bo 一致时提到小标题上，不逐场写",
            "IEM Cologne 2026 · Bo3" in body
            and all("Bo3" not in ln for ln in body.splitlines() if "TYLOO vs" in ln))
    t.check("一组只有一场也不出岔子",
            _group_bo([mk(ts(14), ["A", "B"], "T", bo="Bo5")]) == "Bo5")
    t.check("组内 Bo 不一致时改为逐场标", _group_bo([
        mk(ts(14), ["A", "B"], "T", bo="Bo3"),
        mk(ts(15), ["C", "D"], "T", bo="Bo1")]) is None)
    mixed = format_daily([
        mk(ts(14), ["A", "B"], "T", bo="Bo3"),
        mk(ts(15), ["C", "D"], "T", bo="Bo1")], base, es)
    t.check("Bo 不一致时每行都带上自己的 Bo",
            "T\n  14:00  A vs B · Bo3" in mixed and "· Bo1" in mixed)
    t.check("没有赛事名时用兜底标题，不出现空标题行",
            _group_by_tour([mk(ts(14), ["A", "B"], "")], 0)[1][0][0] == NO_TOUR)

    # 分组：多个赛事按首次出现顺序排，不跨组混
    two_tours = format_daily([
        mk(ts(14), ["A", "B"], "Tournament One"),
        mk(ts(15), ["C", "D"], "Tournament Two"),
        mk(ts(16), ["E", "F"], "Tournament One")], base, es)
    ordered = [n for n in ("Tournament One", "Tournament Two")
               if n in two_tours]
    t.check("多赛事各自成组、按首次出现顺序排", ordered == ["Tournament One", "Tournament Two"])
    t.check("多赛事时同一个赛事不会拆成两段",
            two_tours.count("Tournament One") == 1
            and two_tours.count("Tournament Two") == 1)

    # 行尾标记：中国队 / 世界前 N
    mk_cn = format_daily([mk(ts(14), ["M80", "TYLOO"], "T")], base, es, TOP)
    t.check("中国队那场行尾标「← 中国队」", "M80 vs TYLOO  ← 中国队" in mk_cn)
    mk_top = format_daily([mk(ts(14), ["Unknown A", "G2 Esports"], "T")], base, es, TOP)
    t.check("世界前 N 那场行尾标「← 世界前15」",
            "Unknown A vs G2 Esports  ← 世界前15" in mk_top)
    mk_both = format_daily([mk(ts(14), ["TYLOO", "Team Spirit"], "T")], base, es, TOP)
    t.check("两条都命中就都标（中国队+世界前15）",
            "TYLOO vs Team Spirit  ← 中国队+世界前15" in mk_both)
    t.check("都不命中就不留标记尾巴（行尾干净）",
            "  14:00  Unknown A vs Unknown B" in
            format_daily([mk(ts(14), ["Unknown A", "Unknown B"], "T")], base, es, TOP))
    t.check("排名拿不到时不会误标世界前 N",
            "←" not in format_daily([mk(ts(14), ["Unknown A", "Unknown B"],
                                        "T")], base, es, None))
    t.check("标记不影响署名", mk_both.rstrip().endswith("数据来源：Liquipedia"))

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
            not has_team({"teams": ["Rare Atom", "X"]}, merged["cn_teams"]))
    t.check("已移出的队伍大小写混写也不算",
            not has_team({"teams": ["rare atom", "X"]}, merged["cn_teams"]))
    t.check("保留的两支大小写混写仍算中国队",
            has_team({"teams": ["tyloo", "X"]}, merged["cn_teams"])
            and has_team({"teams": ["LYNN VISION GAMING", "X"]}, merged["cn_teams"]))

    # ---- 知名队伍白名单 ----
    t.check("知名队伍白名单非空", len(merged["notable_teams"]) >= 20,
            len(merged["notable_teams"]))
    t.check("知名白名单含一线强队",
            all(n in merged["notable_teams"]
                for n in ("FaZe Clan", "Team Liquid", "Astralis", "Natus Vincere")))
    t.check("知名白名单不含中国队（两套名单各管各的）",
            not set(merged["notable_teams"]) & set(merged["cn_teams"]))
    t.check("精确匹配：MOUZ 不会命中学院队 MOUZ NXT",
            not has_team({"teams": ["MOUZ NXT"]}, ["MOUZ"]))
    t.check("精确匹配：Natus Vincere 不会命中青训队 Junior",
            not has_team({"teams": ["Natus Vincere Junior"]}, ["Natus Vincere"]))
    t.check("精确匹配：措辞差一点就不算（PaiN != PaiN Gaming）",
            not has_team({"teams": ["PaiN Gaming"]}, ["PaiN"])
            and not has_team({"teams": ["NIP"]}, ["Ninjas in Pyjamas"]))
    t.check("精确匹配：大小写不算差异（paiN Gaming 仍命中）",
            has_team({"teams": ["paiN Gaming"]}, ["PaiN Gaming"])
            and has_team({"teams": ["heroic"]}, ["HEROIC"]))
    t.check("精确匹配：空白名单不误命中", not has_team({"teams": ["FaZe Clan"]}, []))
    t.check("精确匹配：白名单为 None 不炸", not has_team({"teams": ["FaZe Clan"]}, None))
    t.check("精确匹配：队伍字段缺失不炸", not has_team({}, ["FaZe Clan"]))
    t.check("FURIA 用的是一线队页写法，不是会重定向的 FURIA Esports",
            "FURIA" in merged["notable_teams"]
            and "FURIA Esports" not in merged["notable_teams"])
    t.check("知名名单里没有重定向写法（FURIA Esports 这类）",
            not any("Esports" == n.split()[-1] and n.split()[0] == "FURIA"
                    for n in merged["notable_teams"]))

    # ---- why_selected：说清每场是靠什么进来的，以及优先级 ----
    t.check("为什么入选：中国队 -> cn",
            why_selected(_mk(ts(14), ["TYLOO", "X"], "Stake Ranked"), es) == "cn")
    t.check("为什么入选：世界前N -> top15",
            why_selected(_mk(ts(14), ["G2 Esports", "X"], "Stake Ranked"), es, TOP) == "top15")
    t.check("为什么入选：大赛且知名 -> major+notable",
            why_selected(_mk(ts(14), ["FaZe Clan", "X"], "BLAST Premier Fall"), es)
            == "major+notable")
    t.check("为什么入选：只有大赛不给理由（不是 or）",
            why_selected(_mk(ts(14), ["A", "B"], "BLAST Premier Fall"), es) is None)
    t.check("为什么入选：只有知名不给理由（不是 or）",
            why_selected(_mk(ts(14), ["FaZe Clan", "X"], "Stake Ranked"), es) is None)
    t.check("为什么入选：都不沾 -> None",
            why_selected(_mk(ts(14), ["A", "B"], "Stake Ranked"), es) is None)
    t.check("优先级：中国队 > 世界前N",
            why_selected(_mk(ts(14), ["TYLOO", "Team Spirit"], "BLAST Premier"),
                         es, TOP) == "cn")
    t.check("优先级：世界前N > 大赛+知名",
            why_selected(_mk(ts(14), ["Team Spirit", "FaZe Clan"], "BLAST Premier"),
                         es, TOP) == "top15")
    t.check("为什么入选：名队云集的赛事 -> tournament",
            why_selected(_mk(ts(14), ["Nobody A", "Nobody B"], "Stake Ranked Season 9"),
                         es, None, {"Stake Ranked Season 9": 4}, 4) == "tournament")
    t.check("入选原因的标签都有中文名",
            all(REASON_LABELS.get(k) for k in
                ("cn", "top15", "major+notable", "tournament")))
    t.check("统计行会把四条都列出来",
            reasons_summary({"cn": 1, "top15": 2, "major+notable": 3, "tournament": 4})
            == "中国队 1 场 / 世界前 N 2 场 / 大赛+知名 3 场 / 名队云集的赛事 4 场")

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
    parser.add_argument("--teams", action="store_true",
                        help="列出页面上的所有队名，并标出命中哪个白名单（只读，维护白名单用）")
    parser.add_argument("--rank", action="store_true",
                        help="打印 HLTV 世界前 N 及它翻成 Liquipedia 队名的结果（只读，核对别名用）")
    parser.add_argument("--test-notify", action="store_true",
                        help="往配置的每个通道发一条测试消息（部署后先跑这个）")
    parser.add_argument("--selftest", action="store_true", help="离线自检，不联网")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    cfg = watch.load_config(args.config)

    # --teams / --rank 只用 esports 段，不依赖 room_id / onebot，所以放在 validate_cfg 之前
    if args.teams:
        return cmd_list_teams(cfg)

    if args.rank:
        return cmd_show_rank(cfg)

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
