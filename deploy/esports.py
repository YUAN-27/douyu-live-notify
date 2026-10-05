#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 每日赛程推送（跟斗鱼开播提醒共用一套通知通道）

做什么
------
每天固定时刻（由 douyu-esports.timer 拉起，默认北京 09:30）抓一次 Liquipedia 的
CS2 赛程，挑出**窗口内还没开打**、且满足下面任一条件的比赛，推一条到群里：

  · 大赛（赛事名命中 esports.major_keywords 里的关键词）
  · 有中国队参赛（队名精确命中 esports.cn_teams 白名单）
  · 有知名队伍参赛（队名精确命中 esports.notable_teams 白名单）

**窗口 = 「现在 → 下一次预告时刻」，不是「今天这个自然日」。**
因为每天只发一次，只认自然日会让**次日 00:00~09:30 的比赛永远没人预告**
（今天的预告够不着、明天的预告还没发），凌晨开打的比赛正好落进这个真空期。
改成首尾相接的窗口后，既不漏也不重复。窗口末端由 esports.preview_run_time 决定，
**改了定时器的时刻记得同步改它**。

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
import base64
import hashlib
import html as html_mod
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

# Pillow 是**可选依赖**：只用来画「图片卡片」。
# 没装也不影响功能 —— render_card() 会返回 None，上层自动退回纯文本。
# 之所以要这样写：这是本功能唯一一个非标准库依赖，服务器上装不上
# （apt 源不通、pip 被 --break-system-packages 挡住之类）不该让整个预告发不出去。
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 取决于运行环境
    Image = ImageDraw = ImageFont = None

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

# --------------------------------------------------------------------------
# 图片卡片（可选能力）
# --------------------------------------------------------------------------
# 为什么要出图：手机 QQ 对纯文本的换行/缩进/字体处理很不一致，
# 「一天 8 场」这种长文本在小屏上会挤成一团、两列对不齐。
# 出成一张图就彻底免疫 —— 排版在我们手里，客户端只负责显示。
#
# 三个文件/目录：
#   card_font.otf  —— 中文字体（Noto Sans SC 子集，64 KB）。**随包发布**，
#                     因为服务器和开发机字体不同，不锁死字体就没法本地复现线上效果。
#                     生成脚本见 deploy/make_card_font.py。
#   logo_cache/    —— 队标缓存（按 URL 哈希命名，带 TTL）
CARD_FONT_FILE = os.path.join(HERE, "card_font.otf")
LOGO_DIR = os.path.join(HERE, "logo_cache")

# 卡片上会画的**全部固定中文**。
# ⚠️ 必须和 deploy/make_card_font.py 里的 UI_CHARS 完全一致，
#    否则新加的字不在字体子集里 → 静默退回纯文本。自检里有一条专门钉这件事。
#
# 这份包含了**动态拼出来的**文案里的字，不只是硬编码的固定串：
#   「CS2 赛程」/「今天」「明天」/「共 N 场」/「次日」/「数据来源：」
#   「N 个赛事」/「（图里只列前 N 场）」/「…」（截断用）
# 漏一个字的后果是「那条卡片悄悄没了」，不是报错 —— 加文案时务必一并加到这里。
#
# 注：曾经有个红色「中」标（中国队），按用户 2026-10-05 的要求去掉了 ——
#     只保留「中国队会入选推送」这个口径，不再在版面上打标记。
CARD_UI_CHARS = "赛程今天明共场次日数据来源：个事图里只列前·→　"

# card_font.otf 覆盖的 Unicode 区间，同样要和 make_card_font.py 对齐。
# 出图前逐个字符核对：只要有一个字不在里面就**退回纯文本**，
# 而不是画出一排豆腐块 —— 豆腐块比不发图难看得多，而且没人会去查为什么。
CARD_COVER_RANGES = (
    (0x0020, 0x007E),   # ASCII
    (0x00A0, 0x00FF),   # Latin-1
    (0x0100, 0x024F),   # Latin Extended-A/B
    (0x0370, 0x03FF),   # 希腊字母
    (0x0400, 0x04FF),   # 西里尔字母
    (0x2000, 0x206F),   # 通用标点
    (0x2190, 0x21FF),   # 箭头
    (0x2500, 0x257F),   # 制表符
    (0x25A0, 0x25FF),   # 几何图形
    (0x3000, 0x303F),   # CJK 标点
    (0xFF00, 0xFFEF),   # 全角
)

# 版式常量。单位是像素，整体按「2 倍图」设计：
# 880 px 宽的图在手机上铺满聊天宽度（约 400pt），正文 27 px 折合约 13~14pt。
CARD_W = 880
CARD_PAD = 30
CARD_HDR_H = 104
CARD_ROW_H = 78
CARD_FTR_H = 78
CARD_TIME_W = 136
CARD_VS_W = 66
CARD_LOGO = 52

C_BG = (255, 255, 255)
C_INK = (26, 26, 26)
C_MUTED = (107, 106, 100)
C_FAINT = (179, 177, 169)
C_LINE = (230, 227, 221)
C_ROWLINE = (241, 239, 234)
C_PLACE = (235, 233, 228)
C_DAY = (192, 57, 43)       # 「次日」

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
    # 每天几点跑 —— **必须和 douyu-esports.timer 的 OnCalendar 一致**。
    # 预告窗口 = 「现在 → 下一次这个时刻」，所以凌晨的比赛由上一期负责预告。
    "preview_run_time": "09:30",
    # ---- 图片卡片 ----
    # 正文发「一行文字 + 一张图」而不是纯文本：手机 QQ 上图片排版不会崩。
    # 关掉它 / 缺 Pillow / 缺字体 / 有画不出来的字 → 自动退回纯文本，不会不发。
    "card_enabled": True,
    # 队标缓存多久（小时）。队标常年不变，默认 30 天；到期会重新下。
    "logo_ttl_hours": 720,
    # 单轮最多新下载几张队标（每天最多 16 张，正常一张都不用下）。
    # 设上限是为了某天页面结构变了、队标 URL 全变时，别在一轮里下几百张。
    "logo_max_new_per_run": 24,
    # 队标下载超时（秒）。队标很小（2~20 KB），不该占太久。
    "logo_timeout": 15,
    # 卡片最多列几场。再多就只列前 N 场、在标题里说明（图太长手机上也看不清）。
    "card_max_rows": 12,
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
OPPONENT_MARK = 'class="match-info-header-opponent'
# ⚠️ 队标 class **必须用前缀匹配，不能写完整的等号形式**：
#    只上传了一张图的队伍是 `class="team-template-image-icon"`（图名 *_allmode.png），
#    而**同时有亮/暗两套图**的队伍会多两个 class（实测 87 / 39 / 39）：
#        <span class="team-template-image-icon team-template-lightmode">  ← 白底用这张
#        <span class="team-template-image-icon team-template-darkmode">   ← 深色用这张
#    写成等号形式就只认得第一类，G2 / NAVI / Vitality / Spirit 这些名队
#    会**静默地**变成灰色占位块（不报错、日志里也没有 warn），很难发现。
#    前缀匹配后 lightmode 在文档里天然排在 darkmode 之前，取到的就是白底那张；
#    卡片是白底，正好。
TEAM_ICON_MARK = 'class="team-template-image-icon'
TEAM_ICON_LIGHT_MARK = 'class="team-template-image-icon team-template-lightmode"'


def _logo_urls(seg):
    """按左、右顺序取出一场比赛的两个队标 URL（取 srcset 里最大的那个分辨率）。

    好消息：**赛程页本身就带每支队的队标**，抓赛程时顺手就有，
    不需要额外请求、也不需要维护「HLTV 队名 → 队标文件」这种映射表。
    页面结构（2026-10 实测）：

        <div class="match-info-header-opponent match-info-header-opponent-left">
          <div class="block-team flipped">
            <span class="team-template-image-icon">
              <a href="/counterstrike/PARIVISION" title="PARIVISION">
                <img src="/commons/images/thumb/9/9d/PARIVISION_allmode.png/56px-....png"
                     srcset=".../85px-....png 1.5x, .../113px-....png 2x" /></a></span>
            <span class="name" ...><a title="PARIVISION">PV</a></span>
          </div></div>

    ⚠️ 三个坑：
      1. **必须先锚定 team-template-image-icon 再找 img**。右侧对手块后面紧跟
         `match-info-tournament`，那里也有一个 <img>（赛事图标）；直接抓第一个
         img 会把赛事图标当成队标。
      2. 相对路径要补上 https://liquipedia.net 前缀，否则没法下载。
      3. **队标 class 是前缀匹配**（见 TEAM_ICON_MARK 的注释）：只认
         `team-template-image-icon"` 这种带等号的写法，会让有亮/暗两套图的队伍
         （G2、NAVI、Vitality、Spirit…）整支变成占位块，而且是静默的 ——
         2026-10-05 修过一次，自检里钉着 fixture 里的双图分支。
    """
    parts = seg.split(OPPONENT_MARK)[1:]
    out = []
    for part in parts[:2]:
        # 先认「亮色版」；认不到再退回前缀匹配（单图队伍 / 只有暗色图的队伍）
        m = (re.search(re.escape(TEAM_ICON_LIGHT_MARK), part)
             or re.search(re.escape(TEAM_ICON_MARK), part))
        tag = ""
        if m:
            mt = re.search(r"<img[^>]*>", part[m.start():m.start() + 2000])
            if mt:
                tag = html_mod.unescape(mt.group(0))
        url = ""
        if tag:
            src = re.search(r'\ssrc="([^"]+)"', tag)
            url = src.group(1) if src else ""
            srcset = re.search(r'\ssrcset="([^"]+)"', tag)
            if srcset:
                best = None
                for cand, scale in re.findall(r"(\S+)\s+([\d.]+)x", srcset.group(1)):
                    try:
                        s = float(scale)
                    except ValueError:
                        continue
                    if best is None or s > best[0]:
                        best = (s, cand)
                if best:
                    url = best[1]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://liquipedia.net" + url
        out.append(url)
    while len(out) < 2:
        out.append("")
    return out


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
        logos = _logo_urls(seg)
        out.append({
            "ts": int(ts.group(1)),
            "teams": teams,
            "logos": logos if len(logos) == len(teams) else ["", ""],
            "bo": bo.group(1) if bo else "",
            "tour": tidy(re.sub(r"#.*$", "", tour.group(1))) if tour else "",
            "tbd": len(teams) >= 2 and all(t.upper() == "TBD" for t in teams),
        })

    out.sort(key=lambda x: x["ts"])
    return out


# ==========================================================================
# 队标缓存
# ==========================================================================

def _logo_cache_path(url):
    """队标缓存文件名 = URL 的 sha256 前 16 位 + 原后缀。

    用**整个 URL** 而不是队名做 key：Liquipedia 的图片地址带 `?s=<哈希>`，
    队标换版式时 URL 就变了 → 自然变成新文件，不会拿到旧图。
    用哈希而不是队名，是因为队名里可能有 `(`、`&`、空格甚至中文，直接当文件名会出幺蛾子。
    """
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".png"
    return os.path.join(LOGO_DIR, h + ext)


def fetch_logo_bytes(url, es):
    """下载队标原始字节。队标只有 2~20 KB，不重试 —— 失败就画占位。"""
    timeout = max(5, int(es.get("logo_timeout") or 15))
    req = urllib.request.Request(url, headers={
        "User-Agent": build_ua(es),
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    })
    opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
    return data or None


def ensure_logo(url, es, budget):
    """确保队标在本地缓存里，返回本地路径；拿不到返回 None（上层画占位圆）。

    budget 是单轮新下载次数的「余额」，用单元素 list 传进来好跨调用递减 ——
    防的是「页面结构一变、队标 URL 全换」那天，一轮里下几百张图把时间耗光。
    """
    if not url:
        return None
    path = _logo_cache_path(url)
    ttl = float(es.get("logo_ttl_hours") or 0)
    try:
        st = os.stat(path)
        if st.st_size > 0 and (ttl <= 0 or (time.time() - st.st_mtime) < ttl * 3600):
            return path
    except OSError:
        pass

    # 过期或没有。预算用完时：宁可用过期的旧图，也别空着。
    if budget[0] <= 0:
        return path if os.path.exists(path) and os.path.getsize(path) else None
    budget[0] -= 1

    try:
        data = fetch_logo_bytes(url, es)
    except Exception as exc:  # noqa: BLE001
        log("[warn] 队标下载失败（%s）：%s" % (url.rsplit("/", 1)[-1][:48], exc))
        data = None
    if not data:
        return path if os.path.exists(path) and os.path.getsize(path) else None

    try:
        os.makedirs(LOGO_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fp:
            fp.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        log("[warn] 队标写缓存失败（%s）：%s" % (path, exc))
        return None
    return path


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


def parse_run_time(text):
    """把 `"HH:MM"` 解析成 (时, 分)。写坏了就退回 09:30。

    宁可窗口算得保守一点，也不要因为一个配置笔误让预告直接不发。
    """
    m = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$", str(text or ""))
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    return 9, 30


def preview_window(now, run_time=None):
    """预告窗口 = **[现在, 下一次预告时刻)**，返回 (start, end)。

    为什么不是「今天 00:00 ~ 明天 00:00」：每天 09:30 才发一次，如果只认「今天」
    这个自然日，那么**次日 00:00~09:30 的比赛永远没人预告** —— 今天的预告够不着它
    （那会儿还没到今天结束），明天的预告又还没发（它已经不是「明天」了），
    正好落进一个固定 9.5 小时的真空期。凌晨开打的比赛首当其冲。

    改成「现在 → 下一次该发预告的时刻」之后，两期**首尾严格相接**：既不漏，也不重复。

    下一次时刻是**严格晚于 now** 的那个（同一时刻取次日）——
    所以 09:30 准点跑时窗口正好 24 小时；而手工在 08:00 跑，窗口就只到当天 09:30，
    因为 09:30 那一期马上会接手，没必要越权。
    """
    h, mi = parse_run_time(run_time)
    end = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    return now, end


def select_with_reasons(matches, now, es, rank_names=None):
    """返回 (入选场次, {赛事名: 已知队伍数}, 计数)。

    拆出这一层是为了让筛选和统计**用同一份中间结果**，不会出现
    「列表里有这场、但理由统计说没有」这种自相矛盾。
    """
    _start, end = preview_window(now, es.get("preview_run_time"))
    t0, t1 = now.timestamp(), end.timestamp()

    todo = []
    for m in matches:
        if m.get("tbd"):                       # TBD vs TBD：对阵都没定
            continue
        if len(m.get("teams") or []) < 2:       # 队名没解析全，不敢推
            continue
        if not (t0 <= m["ts"] < t1):            # 窗口内：现在 → 下一次预告
            continue
        todo.append(m)

    min_known = int(es.get("tournament_min_known_teams") or 0)
    known = known_team_set(es, rank_names)
    tour_known = {}
    if min_known > 0 and known:
        # 只数「窗口内还没开打」的场次；同一支队在同一个赛事里打两场只算一支。
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


def select_upcoming(matches, now, es, rank_names=None):
    """挑出「窗口内还没开打、对阵已定，且满足入选口径」的比赛（只要场次列表）。

    口径见 why_selected、窗口见 preview_window。为什么只要「还没开打」：
    这是一条**赛程预告**，09:30 发出去的时候，今天凌晨那几场早就打完了，
    列出来只会让人以为还有比赛可看。
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
    """`10-05 周一` —— 年份写出来只占地方。"""
    return "%02d-%02d %s" % (d.month, d.day, WEEKDAYS_CN[d.weekday()])


def _window_label(now, es):
    """`10-05 周一 09:30 → 10-06 周二 09:30`；起止同一天就省掉末尾的日期。

    标题必须把**窗口**写出来：预告不再只覆盖「今天」，而是「现在 → 下一次预告」，
    不写清楚读者会以为跨夜那几场是今天的（然后发现时间已经过了）。
    """
    start, end = preview_window(now, (es or {}).get("preview_run_time"))
    tail = end.strftime("%H:%M")
    if end.date() != start.date():
        tail = "%s %s" % (_day_label(end), tail)
    return "%s %s → %s" % (_day_label(start), start.strftime("%H:%M"), tail)


def _day_prefix(ts, start_date):
    """跨天的场次在时间前加「次日」。

    加了这个前缀，`次日 02:00` 才不会被误读成「今天凌晨 2 点」（那早就过去了）。
    窗口最长 24 小时，所以实际上只会出现「次日」。
    """
    delta = (datetime.fromtimestamp(ts, CST).date() - start_date).days
    if delta <= 0:
        return ""
    return "次日 " if delta == 1 else "%d 天后 " % delta


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


def format_caption(picked, now, es):
    """图片卡片上面那一行文字。

    为什么有图还要带一行字：
      · 手机通知栏会显示这行，只发图的话通知栏只有「[图片]」两个字；
      · 群聊记录能搜到队名/日期，纯图搜不到；
      · 图片发失败时，这行还在（虽然信息少），至少知道今天有几场。
    """
    _start, end = preview_window(now, (es or {}).get("preview_run_time"))
    tail = end.strftime("%H:%M")
    if end.date() != now.date():
        tail = "%s %s" % (_day_label(end), tail)
    return "【CS2 赛程】%s %s → %s · 共 %d 场" % (
        _day_label(now), now.strftime("%H:%M"), tail, len(picked))


def format_daily(picked, now, es, rank_names=None):
    """纯文本版赛程预告（**图片发不出来时的兜底**）。末尾必须署名 Liquipedia。

    为什么单独有这一版、而且刻意比卡片「瘦」：
      手机 QQ 对长文本的换行/缩进处理很不一致，一行里塞太多字段（Bo3、赛事名、
      「← 世界前15」）在小屏上会挤成一团。所以这版只留**时间 + 对阵**：
        · 同时间的两场并成一组，用空行把组隔开（一屏能扫完）
        · 去掉 `· Bo3`、去掉每行重复的赛事名（卡片里有，文本里不值得占位置）
        · 去掉 `← 世界前15`（入选的 8 场里 7 场都命中，等于噪音）
    正常的展示形态是图片卡片，这一版只在出图失败时才用，见 render_card。
    """
    if not picked:
        return ""
    es = es or {}
    shown, _grouped = _group_by_tour(picked, es.get("fold_hint"))
    rank_names = rank_names or []
    start_date = now.date()

    lines = ["【CS2 赛程】%s" % _window_label(now, es), ""]
    # 同一个开赛时刻的几场并成一组：两场同一时间很常见，
    # 分组之后「17:00」只写一次，第二场缩进对齐。
    group_ts = None
    for m in shown:
        d = datetime.fromtimestamp(m["ts"], CST)
        head = "%s%s  " % (_day_prefix(m["ts"], start_date), d.strftime("%H:%M"))
        seg = " vs ".join(m["teams"])
        if m["ts"] != group_ts:
            if group_ts is not None:
                lines.append("")          # 组间空行 —— 一场一行读起来更清楚
            group_ts = m["ts"]
            lines.append(head + seg)
        else:
            lines.append(" " * len(head) + seg)
    lines.append("")

    if len(shown) < len(picked):
        lines.append("共 %d 场，只列了前 %d 场（后面还有）· 数据来源：Liquipedia"
                     % (len(picked), len(shown)))
    else:
        lines.append("共 %d 场 · 数据来源：Liquipedia" % len(picked))
    return "\n".join(lines)


# ==========================================================================
# 图片卡片渲染
# ==========================================================================
#
# 版式（880 px 宽，2 倍图；手机上铺满聊天宽度后正文约 13~14 pt）：
#
#   ┌──────────────────────────────────────────────────┐
#   │ CS2 赛程                                          │
#   │ 10-05 今天 17:00 → 10-06 明天 09:30　共 8 场       │
#   ├──────────────────────────────────────────────────┤
#   │ 17:00        PARIVISION [标]  vs  [标] FURIA      │
#   │ 次日 00:30  Team Vitality [标] vs [标] Falcons    │
#   ├──────────────────────────────────────────────────┤
#   │ ESL Pro League Season 24 · Bo3   数据来源：Liquipedia │
#   └──────────────────────────────────────────────────┘
#
# 所有坐标都由上面那组 CARD_* 常量推出来，改宽度只要改 CARD_W 一处。

_FONT_CACHE = {}


def load_card_font(size):
    """加载卡片字体。一张卡片要取十几次字号，缓存住免得反复读文件。"""
    key = int(size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(CARD_FONT_FILE, key)
    return _FONT_CACHE[key]


def card_missing_chars(texts):
    """返回「字体子集里没有」的字符集合（空集 = 全都画得出来）。

    故意**不**在运行时引 fontTools 去读字体 cmap —— 那会变成第二个第三方依赖，
    而服务器上能装什么不由我们说了算。这里改用「和 deploy/make_card_font.py
    完全相同的区间表」来判断，自检里有一条断言两边的表必须一致，所以不会悄悄漂移。
    """
    miss = set()
    for s in texts:
        for ch in str(s):
            if ch in CARD_UI_CHARS:
                continue
            code = ord(ch)
            if not any(lo <= code <= hi for lo, hi in CARD_COVER_RANGES):
                miss.add(ch)
    return miss


def _card_fit(draw, text, sizes, max_w):
    """从大到小挑一个塞得下的字号；都塞不下就截断加省略号。

    队名长度差很多（`M80` vs `Natus Vincere`），固定字号要么放不下、
    要么把短名字衬得很小。缩一档字号比截断好 —— 截断会丢信息。
    """
    for size in sizes:
        f = load_card_font(size)
        if draw.textlength(text, font=f) <= max_w:
            return text, f
    f = load_card_font(sizes[-1])
    out = text
    while out and draw.textlength(out + "…", font=f) > max_w:
        out = out[:-1]
    return ((out + "…") if out else ""), f


def _card_logo(img, draw, path, box_x, cy, name, f_ph):
    """贴队标。读不出来就画灰底占位 —— 一张图坏掉不该毁掉整张卡片。"""
    box = CARD_LOGO
    if path:
        try:
            with Image.open(path) as raw:
                im = raw.convert("RGBA")
            im.thumbnail((box, box), Image.LANCZOS)
            img.paste(im, (box_x + (box - im.width) // 2, cy - im.height // 2), im)
            return
        except Exception as exc:  # noqa: BLE001
            log("[warn] 队标读不出来（%s）：%s" % (os.path.basename(path), exc))
    draw.rounded_rectangle([box_x, cy - box // 2, box_x + box, cy + box // 2],
                           radius=8, fill=C_PLACE)
    draw.text((box_x + box // 2, cy - 1), (name or "?")[:2].upper(),
              font=f_ph, fill=C_MUTED, anchor="mm")


def _card_window(now, es):
    """`10-05 今天 17:00 → 10-06 明天 09:30`

    卡片是给手机扫的，「今天/明天」比「周一/周二」好认。
    窗口最长 24 小时，所以末尾那天只可能是今天或明天。
    """
    start, end = preview_window(now, (es or {}).get("preview_run_time"))
    return "%s %s %s → %s %s %s" % (
        start.strftime("%m-%d"), "今天", start.strftime("%H:%M"),
        end.strftime("%m-%d"), ("明天" if end.date() != start.date() else "今天"),
        end.strftime("%H:%M"))


def _card_tour(grouped):
    """页脚左边那句：只有一个赛事就写全名（带 Bo），多个就报个数。"""
    if not grouped:
        return ""
    if len(grouped) > 1:
        return "%d 个赛事" % len(grouped)
    name, group = grouped[0]
    bo = _group_bo(group)
    return name + (" · " + bo if bo else "")


def render_card(picked, now, es, rank_names=None):
    """把赛程画成一张 PNG 返回 bytes。**任何一项前置条件不满足就返回 None。**

    为什么是「返回 None」而不是抛异常：图片只是锦上添花，没图也必须把赛程发出去。
    所以下面每条 return None 都对应一种**安静降级**：
      没装 Pillow / 没字体文件 / 有画不出来的字 / 画的途中出任何错
    → 上层拿不到 bytes 就改发纯文本（format_daily），功能不会因此消失。
    """
    if Image is None:
        log("[info] 没装 Pillow，跳过图片卡片（本次发纯文本）")
        return None
    if not os.path.isfile(CARD_FONT_FILE):
        log("[warn] 找不到字体 %s，跳过图片卡片（本次发纯文本）" % CARD_FONT_FILE)
        return None
    if not picked:
        return None
    try:
        return _render_card_inner(picked, now, es, rank_names)
    except Exception as exc:  # noqa: BLE001
        # 画图是纯装饰环节，绝不允许它把整轮预告搞失败
        log("[warn] 画卡片出错（%s: %s），本次改发纯文本" % (type(exc).__name__, exc))
        return None


def _render_card_inner(picked, now, es, rank_names=None):
    es = es or {}
    shown, grouped = _group_by_tour(picked, es.get("fold_hint"))
    max_rows = max(1, int(es.get("card_max_rows") or 12))
    rows = shown[:max_rows]
    start_date = now.date()

    tour = _card_tour(grouped)
    count = "共 %d 场" % len(picked)
    if len(rows) < len(picked):
        count += "（图里只列前 %d 场）" % len(rows)
    sub = "%s　%s" % (_card_window(now, es), count)

    # 出图之前先把要画的字全核一遍：缺字就整张不出，绝不画豆腐块
    texts = ["CS2 赛程", sub, tour, "数据来源：Liquipedia", "次日", "…"]
    for m in rows:
        texts += list(m.get("teams") or [])
    miss = card_missing_chars(texts)
    if miss:
        log("[warn] 字体子集里没有这些字：%s，本次改发纯文本" % "".join(sorted(miss))[:60])
        log("       要出图就往 deploy/make_card_font.py 的 UI_CHARS 补字并重跑它。")
        return None

    n = len(rows)
    height = CARD_HDR_H + CARD_ROW_H * n + CARD_FTR_H
    if height > 2400:
        log("[warn] 卡片会高达 %d px（%d 场），改用纯文本" % (height, n))
        return None

    img = Image.new("RGB", (CARD_W, height), C_BG)
    d = ImageDraw.Draw(img)
    f_title = load_card_font(34)
    f_sub = load_card_font(22)
    f_time = load_card_font(26)
    f_day = load_card_font(19)
    f_name = load_card_font(27)
    f_vs = load_card_font(20)
    f_ft = load_card_font(19)
    f_ph = load_card_font(15)

    # ---- 页头 ----
    # stroke_width=1 是「伪粗体」：子集里只带了 Regular 一个字重，
    # 标题不加粗会和大字号的正文分不出层次。
    d.text((CARD_PAD, 24), "CS2 赛程", font=f_title, fill=C_INK,
           stroke_width=1, stroke_fill=C_INK)
    d.text((CARD_PAD, 70), sub, font=f_sub, fill=C_MUTED)
    d.line([(CARD_PAD, CARD_HDR_H - 1), (CARD_W - CARD_PAD, CARD_HDR_H - 1)],
           fill=C_LINE, width=1)

    # ---- 列位置（改 CARD_W 时这里会跟着重算）----
    side_w = (CARD_W - 2 * CARD_PAD - CARD_TIME_W - CARD_VS_W) // 2
    left_x1 = CARD_PAD + CARD_TIME_W + side_w        # 左侧队伍的右边界
    right_x0 = left_x1 + CARD_VS_W                   # 右侧队伍的左边界
    vs_cx = left_x1 + CARD_VS_W // 2
    time_right = CARD_PAD + CARD_TIME_W - 14

    # 队标先一次性下齐（有缓存，通常一张都不用下）
    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    logos = []
    for m in rows:
        urls = list(m.get("logos") or [])
        while len(urls) < 2:
            urls.append("")
        logos.append([ensure_logo(u, es, budget) for u in urls[:2]])

    # ---- 逐场一行 ----
    for i, m in enumerate(rows):
        top = CARD_HDR_H + CARD_ROW_H * i
        cy = top + CARD_ROW_H // 2
        base = cy + 9        # 统一基线：26px 的时间列和 27px 的队名才不会各飘各的
        if i:
            d.line([(CARD_PAD, top), (CARD_W - CARD_PAD, top)], fill=C_ROWLINE, width=1)

        # 时间列（右对齐；「次日」用红字，免得被读成「今天凌晨」）
        pre = _day_prefix(m["ts"], start_date).strip()
        segs = ([(pre, f_day, C_DAY)] if pre else []) + \
               [(datetime.fromtimestamp(m["ts"], CST).strftime("%H:%M"), f_time, C_MUTED)]
        x = time_right - sum(d.textlength(s, font=f) for s, f, _ in segs)
        for s, f, col in segs:
            d.text((int(x), base), s, font=f, fill=col, anchor="ls")
            x += d.textlength(s, font=f)

        d.text((vs_cx, base), "vs", font=f_vs, fill=C_FAINT, anchor="ms")

        # 两侧队伍：左侧「队名 队标」右对齐，右侧「队标 队名」左对齐
        for side, name in enumerate((m.get("teams") or [])[:2]):
            room = side_w - CARD_LOGO - 14
            nm, f = _card_fit(d, name, (27, 25, 23, 21, 20), max(60, room))
            if side == 0:
                _card_logo(img, d, logos[i][0], left_x1 - CARD_LOGO, cy, name, f_ph)
                x = left_x1 - CARD_LOGO - 14
                d.text((int(x - d.textlength(nm, font=f)), base), nm, font=f,
                       fill=C_INK, anchor="ls")
            else:
                _card_logo(img, d, logos[i][1], right_x0, cy, name, f_ph)
                x = right_x0 + CARD_LOGO + 14
                d.text((int(x), base), nm, font=f, fill=C_INK, anchor="ls")

    # ---- 页脚（署名必须留着，Liquipedia 是 CC-BY-SA）----
    fy = CARD_HDR_H + CARD_ROW_H * n
    d.line([(CARD_PAD, fy), (CARD_W - CARD_PAD, fy)], fill=C_LINE, width=1)
    fbase = fy + 46
    credit = "数据来源：Liquipedia"
    cw = d.textlength(credit, font=f_ft)
    d.text((CARD_W - CARD_PAD, fbase), credit, font=f_ft, fill=C_MUTED, anchor="rs")
    lt, lf = _card_fit(d, tour, (19, 18, 17, 16),
                       max(40, (CARD_W - 2 * CARD_PAD) - cw - 24))
    if lt:
        d.text((CARD_PAD, fbase), lt, font=lf, fill=C_MUTED, anchor="ls")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log("[info] 卡片：%d×%d，PNG %.1f KB，%d 场"
        % (CARD_W, height, len(data) / 1024.0, n))
    return data


def format_calm(empty_days):
    """连续静默满一周时的「报平安」。

    这条存在的唯一理由：让「今天没比赛」和「程序挂了」在群里能被区分开。
    """
    return "\n".join([
        "【CS2 赛程】连续 %d 天没有可推送的比赛" % empty_days,
        "",
        "这不是故障，是近期确实没有符合推送条件的比赛（大赛、中国队、世界强队都没排上）。",
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

def cq_image(png):
    """把 PNG 包成 OneBot 的图片段。

    用 `base64://` 内联，而不是本机路径：OneBot 服务端（NapCat）跑在 Docker 里，
    我们写的 `/opt/douyu-live-notify/...` 它看不见（除非额外挂载卷）。
    base64 是唯一不依赖挂载、也不需要我们对外提供 HTTP 的方式。
    """
    return "[CQ:image,file=base64://%s]" % base64.b64encode(png).decode("ascii")


class _CardNotifier:
    """把「一行文字 + 一张图」这条复合消息**只**塞给 onebot 通道。

    为什么不直接改 watch.notify_all：那是斗鱼开播监控共用的通知层，
    为了赛程卡片去动它，风险和收益不成比例。这里用一层薄包装就够了 ——
    notify_all 只用到 `.name` / `.counts_as_delivery` / `.send()`，包住这三样即可。

    为什么不给 console 也塞图片：PNG 转 base64 有几百 KB，原样打进日志
    （还有 journald）会把日志冲爆，而且排查时一个字都用不上。
    """

    def __init__(self, inner, image_cq):
        self._inner = inner
        self._image = image_cq
        self.name = inner.name
        self.counts_as_delivery = getattr(inner, "counts_as_delivery", True)

    def send(self, text):
        return self._inner.send(text + "\n" + self._image)


def with_card_image(notifiers, png):
    """给 onebot 通道挂上图片段；其他通道原样返回（只发文字）。"""
    if not png:
        return notifiers
    cq = cq_image(png)
    return [_CardNotifier(n, cq) if getattr(n, "name", "") == "onebot" else n
            for n in notifiers]


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
    _ws, _we = preview_window(now, es.get("preview_run_time"))
    log("[info] 预告窗口：%s → %s（含跨夜，避免凌晨的比赛没人预告）"
        % (_ws.strftime("%m-%d %H:%M"), _we.strftime("%m-%d %H:%M")))
    log("[info] 页面共 %d 场，其中窗口内还没开打且符合条件的有 %d 场"
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
    card = None
    body = ""

    _st, want_calm = streak_step(state, bool(picked), es.get("calm_after_empty_days"), now)

    if picked:
        # 首选「一行文字 + 一张卡片图」。出不了图（没装 Pillow / 没字体 /
        # 有画不出来的字）就退回纯文本 —— 各条降级路径见 render_card。
        if es.get("card_enabled", True):
            card = render_card(picked, now, es, rank["names"])
        if card:
            body = format_caption(picked, now, es)
            log("[info] 本轮发「一行文字 + 一张卡片图」")
        else:
            body = format_daily(picked, now, es, rank["names"])
            log("[info] 本轮发纯文本（没有出图）")
    else:
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
        if card:
            # 出图效果只有肉眼能判，所以 --check 顺便把图落一份到临时目录，
            # 方便 scp/workbench download 回来核对（字体不同的机器画出来会不一样）。
            path = os.path.join(tempfile.gettempdir(), "esports_card_check.png")
            try:
                with open(path, "wb") as fp:
                    fp.write(card)
                log("[check] 这次会发的卡片图已存到：%s" % path)
            except OSError as exc:
                log("[warn] 卡片预览图写不出来：%s" % exc)
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
    delivered, failed = send_with_retry(with_card_image(notifiers, card), body, es)
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
  <span class="match-info-countdown">
    <span class="timer-object" data-format="full" data-timestamp="1790000000">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left">
      <div class="block-team flipped">
        <span class="team-template-image-icon">
          <a href="/counterstrike/TYLOO" title="TYLOO"><img alt="" src="/commons/images/thumb/1/1a/TyLoo_2016_allmode.png/56px-TyLoo_2016_allmode.png" srcset="/commons/images/thumb/1/1a/TyLoo_2016_allmode.png/85px-TyLoo_2016_allmode.png 1.5x, /commons/images/thumb/1/1a/TyLoo_2016_allmode.png/113px-TyLoo_2016_allmode.png 2x" /></a></span>
        <span class="name" style="x"><a href="/counterstrike/TYLOO" title="TYLOO">TYL</a></span>
      </div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-upper">vs</span>
      <span class="match-info-header-scoreholder-lower">(Bo3)</span></div>
    <div class="match-info-header-opponent">
      <div class="block-team">
        <span class="team-template-image-icon">
          <a href="/counterstrike/Lynn_Vision" title="Lynn Vision Gaming"><img alt="" src="/commons/images/thumb/b/bf/Lynn_Vision_Gaming_allmode.png/51px-Lynn_Vision_Gaming_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/Lynn_Vision" title="Lynn Vision Gaming">LVG</a></span>
      </div></div>
  </div>
  <div class="match-info-tournament">
    <span class="league-icon-small-image">
      <a href="/counterstrike/BLAST/Premier" title="BLAST/Premier"><img alt="BLAST/Premier" src="/commons/images/thumb/2/2b/BLAST_Premier_icon.png/50px-BLAST_Premier_icon.png" /></a></span>
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/BLAST/Premier" title="BLAST/Premier"><span>BLAST Premier Fall 2026 - Group A</span></a></span></span>
  </div>
</div>
<div class="match-info">
  <span class="match-info-countdown">
    <span class="timer-object" data-timestamp="1790000100">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left">
      <div class="block-team flipped">
        <span class="team-template-image-icon">
          <a href="/counterstrike/A" title="Alpha (page does not exist)"><img alt="" src="/commons/images/thumb/a/aa/Alpha_allmode.png/50px-Alpha_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/A" title="Alpha (page does not exist)">ALP</a></span>
      </div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-lower">(Bo1)</span></div>
    <div class="match-info-header-opponent">
      <div class="block-team">
        <span class="team-template-image-icon team-template-lightmode">
          <a href="/counterstrike/Beta" title="Beta"><img alt="" src="/commons/images/thumb/b/bb/Beta_lightmode.png/50px-Beta_lightmode.png" /></a></span>
        <span class="team-template-image-icon team-template-darkmode">
          <a href="/counterstrike/Beta" title="Beta"><img alt="" src="/commons/images/thumb/d/dd/Beta_darkmode.png/50px-Beta_darkmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/B" title="Beta">BET</a></span>
      </div></div>
  </div>
  <div class="match-info-tournament">
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/Stake_Ranked" title="Stake/Ranked"><span>Stake Ranked Season 7</span></a></span></span>
  </div>
</div>
<div class="match-info">
  <span class="match-info-countdown">
    <span class="timer-object" data-timestamp="1790000200">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left">
      <div class="block-team flipped"><span class="name">TBD</span></div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-lower">(Bo3)</span></div>
    <div class="match-info-header-opponent">
      <div class="block-team"><span class="name">TBD</span></div></div>
  </div>
  <div class="match-info-tournament">
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/IEM" title="IEM"><span>IEM Cologne 2026</span></a></span></span>
  </div>
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


def _mk(ts, teams, tour, bo="Bo3", tbd=False, logos=None):
    return {"ts": ts, "teams": list(teams), "bo": bo, "tour": tour, "tbd": tbd,
            # 默认给空 URL：自检**绝不能联网**去下队标，
            # ensure_logo("") 在上面就返回 None，卡片会画占位圆。
            "logos": list(logos) if logos else ["", ""]}


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

        # 队标：赛程页自带，抓赛程时顺手就抽出来，不需要额外请求
        t.check("左侧队标取自 team-template-image-icon，且补成绝对地址",
                ms[0]["logos"][0].startswith("https://liquipedia.net/commons/images/"),
                ms[0]["logos"][0])
        t.check("srcset 里有多倍图时取最大那个（2x 优先于 1.5x）",
                "113px-TyLoo_2016_allmode.png" in ms[0]["logos"][0],
                ms[0]["logos"][0])
        t.check("没有 srcset 时退回 src",
                ms[0]["logos"][1].endswith("51px-Lynn_Vision_Gaming_allmode.png"),
                ms[0]["logos"][1])
        t.check("⚑ 不会把紧跟其后的赛事图标当成队标",
                "BLAST_Premier_icon" not in ms[0]["logos"][1]
                and "commons" in ms[0]["logos"][1],
                ms[0]["logos"][1])
        t.check("⚑ 亮/暗两套图的队伍取「亮色版」（卡片是白底）",
                ms[1]["logos"][1].endswith("50px-Beta_lightmode.png"),
                ms[1]["logos"][1])
        t.check("⚑ 队标 class 用前缀匹配，同时带 mode 后缀也认得（否则名队会变占位块）",
                ms[1]["logos"][1] != ""
                and "Beta_darkmode" not in ms[1]["logos"][1],
                ms[1]["logos"][1])
        t.check("左/右队标各就各位，不串位",
                ms[1]["logos"] == [
                    "https://liquipedia.net/commons/images/thumb/a/aa/"
                    "Alpha_allmode.png/50px-Alpha_allmode.png",
                    "https://liquipedia.net/commons/images/thumb/b/bb/"
                    "Beta_lightmode.png/50px-Beta_lightmode.png"],
                ms[1]["logos"])
        t.check("TBD 场次没有队标", ms[2]["logos"] == ["", ""], ms[2]["logos"])
        t.check("两个队标始终是 2 个（对齐 teams，缺也给空串）",
                all(len(m["logos"]) == 2 for m in ms))
    t.check("空输入不炸", parse_matches("") == [])

    # ---- 2. 筛选 ----
    print("\n-- 2. 筛选（中国队 / 世界前N / 大赛且知名 / 时间窗）--")
    base = datetime(2026, 9, 30, 9, 30, tzinfo=CST)
    ts = lambda h, m=0: base.replace(hour=h, minute=m).timestamp()
    # 次日的某个时刻 —— 用来验「跨夜窗口」，这是本轮修的重点
    next_day = lambda h, m=0: (base + timedelta(days=1)).replace(
        hour=h, minute=m).timestamp()

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
        ("下一期预告那一刻（窗口右端点，左闭右开）→ 丢掉",
         mk(next_day(9, 30), ["TYLOO", "B"], "IEM"), None, False),
        ("赛事名为空不算大赛，但中国队仍发", mk(ts(14), ["TYLOO", "B"], ""), None, True),
        ("赛事名为空 + 没中国队 → 丢掉", mk(ts(14), ["A", "B"], ""), None, False),
    ]
    for name, m, top, want in cases:
        got = select_upcoming([m], base, es, top)
        t.check(name, bool(got) == want, "want=%s got=%s" % (want, bool(got)))

    t.check("蒙古队不算中国队（直接断言 has_team）",
            not has_team({"teams": ["The MongolZ", "IHC"]}, es["cn_teams"]))

    t.check("边界：恰好此刻开打 → 保留",
            bool(select_upcoming([mk(ts(9, 30), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("边界：此刻前 1 秒 → 丢掉",
            not select_upcoming([mk(ts(9, 30) - 1, ["TYLOO", "B"], "IEM")], base, es))
    t.check("边界：今天 23:59 → 保留",
            bool(select_upcoming([mk(ts(23, 59), ["TYLOO", "B"], "IEM")], base, es)))

    # ---- 2a. 跨夜窗口：不能漏掉次日凌晨的比赛 ----
    print("\n-- 2a. 预告窗口（现在 → 下一次预告，跨夜）--")
    t.check("窗口起点就是 now", preview_window(base, "09:30")[0] == base)
    t.check("09:30 跑 → 窗口正好到次日 09:30",
            preview_window(base, "09:30")[1] == base + timedelta(days=1))
    t.check("窗口正好 24 小时", preview_window(base, "09:30")[1] - base
            == timedelta(days=1))
    t.check("同一天的窗口右端点（now 早于 run_time）",
            preview_window(base.replace(hour=8), "09:30")[1] == base)
    t.check("手工在 23:00 跑 → 窗口到次日 09:30",
            preview_window(base.replace(hour=23), "09:30")[1]
            == base + timedelta(days=1))
    t.check("run_time 写坏时退回 09:30", parse_run_time("乱七八糟") == (9, 30))
    t.check("run_time 兼容中文冒号与单数字", parse_run_time("9：5") == (9, 5))
    t.check("run_time 越界时退回 09:30", parse_run_time("25:00") == (9, 30))

    t.check("次日 00:00 的比赛 → 保留（这就是原来漏掉的那种）",
            bool(select_upcoming([mk(next_day(0, 0), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("次日 02:00 → 保留",
            bool(select_upcoming([mk(next_day(2), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("次日 09:29 → 保留（下一期开跑前一分钟）",
            bool(select_upcoming([mk(next_day(9, 29), ["TYLOO", "B"], "IEM")], base, es)))
    t.check("次日 12:00 → 丢掉（留给下一期，免重复）",
            not select_upcoming([mk(next_day(12), ["TYLOO", "B"], "IEM")], base, es))
    t.check("两期首尾相接、既不漏也不重（端点是开区间）",
            select_upcoming([mk(next_day(9, 30) - 1, ["TYLOO", "B"], "IEM")], base, es)
            and not select_upcoming(
                [mk(next_day(9, 30), ["TYLOO", "B"], "IEM")], base, es))
    t.check("窗口右端点跟着 preview_run_time 走（改成 12:00 → 窗口只到当天 12:00）",
            bool(select_upcoming([mk(ts(11), ["TYLOO", "B"], "IEM")],
                                 base, dict(es, preview_run_time="12:00")))
            and not select_upcoming([mk(ts(13), ["TYLOO", "B"], "IEM")],
                                    base, dict(es, preview_run_time="12:00")))
    t.check("名队云集只在窗口内计数（次日 12:00 那场不算进去）",
            len(select_upcoming(
                [mk(ts(10), ["FaZe Clan", "M80"], "赛事甲"),
                 mk(ts(11), ["Team Liquid", "Wildcard"], "赛事甲"),
                 mk(next_day(12), ["Nobody A", "Nobody B"], "赛事甲")], base, es)) == 2)
    t.check("空列表安全", select_upcoming([], base, es) == [])

    # ---- 2b. 「名队云集的赛事」整体放行 ----
    print("\n-- 2b. 名队云集的赛事（同一赛事当天 >= 4 支已知队伍）--")
    tour = "Stake Ranked Season 9"
    crowd = [
        mk(ts(10), ["FaZe Clan", "M80"], tour),          # 2 支知名
        mk(ts(12), ["Team Liquid", "Wildcard"], tour),   # 再 2 支 → 共 4 支
        mk(ts(14), ["Nobody A", "Nobody B"], tour),      # 一对无名队
    ]
    t.check("凑够 4 支 → 该赛事当天的比赛都发（含无名队那场）",
            len(select_upcoming(crowd, base, es)) == 3,
            "实际 %d 场" % len(select_upcoming(crowd, base, es)))
    t.check("同一支队打两场只算一支（3 支 → 不放行）",
            select_upcoming([mk(ts(10), ["FaZe Clan", "M80"], tour),
                          mk(ts(12), ["FaZe Clan", "Wildcard"], tour),
                          mk(ts(14), ["Nobody A", "Nobody B"], tour)], base, es) == [])
    t.check("只有 2 支时不放行", select_upcoming(crowd[:1], base, es) == [])
    t.check("tournament_min_known_teams = 0 时这条整条关掉",
            select_upcoming(crowd, base, dict(es, tournament_min_known_teams=0)) == [])
    t.check("世界前 N 的队也计入「已知队伍」",
            len(select_upcoming([mk(ts(10), ["Team Spirit", "M80"], tour),
                              mk(ts(12), ["Team Liquid", "Wildcard"], tour),
                              mk(ts(14), ["Nobody A", "Nobody B"], tour)],
                             base, es, TOP)) == 3)
    t.check("不同赛事各算各的（不跨赛事凑数）",
            select_upcoming([mk(ts(10), ["FaZe Clan", "M80"], "赛事甲"),
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

    # ---- 3. 纯文本正文（图片出不来时的兜底版式）----
    print("\n-- 3. 纯文本正文（兜底版式）与折叠 --")
    many = [mk(ts(10) + i * 60, ["TYLOO", "X%d" % i], "IEM Cologne 2026") for i in range(20)]
    body = format_daily(many, base, es)
    t.check("超过 fold_hint 会折叠", "只列了前 15 场" in body)
    t.check("折叠后仍写明总场次", "共 20 场" in body)
    t.check("正文末尾署名 Liquipedia", body.rstrip().endswith("数据来源：Liquipedia"))
    t.check("未超阈值时不折叠", "只列了前" not in format_daily(many[:3], base, es))
    t.check("空列表返回空串（调用方据此静默）", format_daily([], base, es) == "")

    one = format_daily(many[:1], base, es)
    t.check("单场正文含时间与对阵", "10:00" in one and "TYLOO vs X0" in one)

    head = body.splitlines()[0]
    t.check("标题写明窗口（现在 → 下一次预告），且不写年份",
            head == "【CS2 赛程】09-30 周三 09:30 → 10-01 周四 09:30"
            and str(base.year) not in head)
    t.check("起止同一天时标题不重复日期",
            _window_label(base.replace(hour=8, minute=0), es)
            == "09-30 周三 08:00 → 09:30")
    t.check("窗口标签跟着 preview_run_time 走（08:00 早于 now → 顺延到次日）",
            _window_label(base, dict(es, preview_run_time="08:00"))
            == "09-30 周三 09:30 → 10-01 周四 08:00")

    # 瘦身：这三样都改由卡片承载，正文里不再出现
    #（手机 QQ 上长文本换行很乱，这正是本轮改版的起因）
    t.check("正文里不再重复赛事名（改由卡片页脚承载）",
            "IEM Cologne 2026" not in body)
    t.check("正文里不再写 Bo", "Bo3" not in body)
    t.check("正文里不再标「世界前 N」（入选场次大多命中，等于噪音）",
            "世界前" not in format_daily(
                [mk(ts(14), ["Unknown", "G2 Esports"], "T")], base, es, TOP))

    t.check("对阵行是「时间两空格对阵」，不缩进",
            "10:00  TYLOO vs X0" in body)
    t.check("跨天的场次时间前加「次日」",
            "次日 02:00  TYLOO vs B" in format_daily(
                [mk(next_day(2), ["TYLOO", "B"], "IEM")], base, es))
    t.check("当天场次不加「次日」前缀", _day_prefix(ts(23, 59), base.date()) == "")
    t.check("次日场次加「次日」前缀", _day_prefix(next_day(0), base.date()) == "次日 ")

    # 同一开赛时刻的两场并成一组：第二行缩进对齐、不重复时间；组与组之间空一行
    grouped_body = format_daily([
        mk(ts(14), ["A", "B"], "T"),
        mk(ts(14), ["C", "D"], "T"),
        mk(ts(19), ["E", "F"], "T")], base, es)
    t.check("同一时刻只写一次时间", grouped_body.count("14:00") == 1)
    t.check("同一时刻的第二场缩进对齐（对齐到对阵列）",
            "\n       C vs D" in grouped_body, grouped_body)
    t.check("不同时刻的组之间留一个空行",
            "\n\n19:00  E vs F" in grouped_body)

    # 按用户 2026-10-05 的要求：**版面上一律不打中国队标记**。
    # 注意「中国队会入选推送」这条口径没变 —— 变的只是不在消息里标出来。
    cn_body = format_daily([mk(ts(14), ["M80", "TYLOO"], "T")], base, es, TOP)
    t.check("中国队那场照发，但不再加标记",
            "M80 vs TYLOO" in cn_body and "[中]" not in cn_body, cn_body)
    t.check("正文里一个「中」字都不出现（整条标记已移除）",
            "中" not in cn_body, cn_body)
    t.check("中国队照样能入选（去标记不影响筛选口径）",
            len(select_upcoming([mk(ts(14), ["M80", "TYLOO"], "Stake Ranked")],
                                base, es, TOP)) == 1)

    # 卡片页脚要用到的「赛事名」聚合：赛事名只出现一次、Bo 提到上面
    t.check("一组只有一场时 Bo 也能取到",
            _group_bo([mk(ts(14), ["A", "B"], "T", bo="Bo5")]) == "Bo5")
    t.check("组内 Bo 不一致时返回 None（改为不写 Bo）", _group_bo([
        mk(ts(14), ["A", "B"], "T", bo="Bo3"),
        mk(ts(15), ["C", "D"], "T", bo="Bo1")]) is None)
    t.check("没有赛事名时用兜底标题，不出现空标题行",
            _group_by_tour([mk(ts(14), ["A", "B"], "")], 0)[1][0][0] == NO_TOUR)
    t.check("单赛事时页脚写全名 + Bo",
            _card_tour(_group_by_tour(many[:2], 0)[1])
            == "IEM Cologne 2026 · Bo3")
    t.check("多赛事时页脚只报个数",
            _card_tour(_group_by_tour([
                mk(ts(14), ["A", "B"], "T1"), mk(ts(15), ["C", "D"], "T2")], 0)[1])
            == "2 个赛事")

    # ---- 3b. caption + 图片卡片 ----
    print("\n-- 3b. 图片卡片（渲染 / 降级）--")
    cap = format_caption(many[:2], base, es)
    t.check("caption 是「一行」：标题 + 窗口 + 场次",
            cap == "【CS2 赛程】09-30 周三 09:30 → 10-01 周四 09:30 · 共 2 场", cap)
    t.check("caption 里不含换行（它就是给通知栏看的）", "\n" not in cap)

    t.check("字体子集覆盖卡片上的固定中文", card_missing_chars(
        [CARD_UI_CHARS, "CS2 赛程", "10-05 今天 17:00 → 10-06 明天 09:30　共 8 场",
         "次日", "数据来源：Liquipedia"]) == set())
    t.check("「中」已从卡片固定字符表里移出（中国队标记不会再画出来）",
            "中" not in CARD_UI_CHARS)
    t.check("拉丁/重音/破折号都在覆盖范围内",
            card_missing_chars(["Natus Vincere – Émile Života"]) == set())
    t.check("子集外的汉字会被识别出来（触发退回纯文本）",
            card_missing_chars(["測試隊"]) == {"測", "試", "隊"})
    t.check("半角空格不算缺失", card_missing_chars(["a b"]) == set())

    # 卡片覆盖区间表必须和 make_card_font.py 保持一致，否则会出现
    # 「以为画得出来、实际是豆腐块」或者反过来「明明能画却退回文本」
    try:
        sys.path.insert(0, HERE)
        import make_card_font as _mcf
        t.check("覆盖区间表与 make_card_font.py 一致",
                tuple(_mcf.UNICODE_RANGES)
                == tuple("U+%04X-%04X" % r for r in CARD_COVER_RANGES),
                _mcf.UNICODE_RANGES)
        t.check("固定字符表与 make_card_font.py 一致",
                _mcf.UI_CHARS == CARD_UI_CHARS, _mcf.UI_CHARS)
    except ImportError:
        print("   （没有 make_card_font.py，跳过区间表一致性断言）")

    # 发送层：只有 onebot 需要图片段，console 绝不能被塞进 base64
    class _FakeNotifier:
        def __init__(self, name, delivery=True):
            self.name, self.counts_as_delivery, self.got = name, delivery, []

        def send(self, text):
            self.got.append(text)

    fake_one = _FakeNotifier("onebot")
    fake_con = _FakeNotifier("console", False)
    wrapped = with_card_image([fake_one, fake_con], b"abc")
    delivered, failed = watch.notify_all(wrapped, "正文")
    t.check("包装后仍能被 notify_all 正常驱动（送达判定沿用原通道）",
            delivered and not failed, failed)
    t.check("onebot 收到「正文 + base64 图片段」",
            fake_one.got and fake_one.got[0] == "正文\n[CQ:image,file=base64://YWJj]",
            fake_one.got)
    t.check("console 只收到正文（几百 KB 的 base64 不能进日志）",
            fake_con.got == ["正文"], fake_con.got)
    t.check("没有图时不包装，原样返回",
            with_card_image([fake_one], None) == [fake_one]
            and with_card_image([fake_one], b"") == [fake_one])
    t.check("没有 onebot 通道时不硬塞图片",
            len(with_card_image([fake_con], b"abc")) == 1)

    if Image is None:
        t.check("本机没有 Pillow → 不出图，返回 None（上层据此退回纯文本）",
                render_card(many[:2], base, es) is None)
        print("   （本机没装 Pillow，跳过渲染断言）")
    else:
        png = render_card(many[:2], base, es)
        t.check("有 Pillow + 有字体时能出 PNG",
                isinstance(png, (bytes, bytearray))
                and bytes(png[:8]) == b"\x89PNG\r\n\x1a\n", type(png))
        if png:
            im = Image.open(io.BytesIO(png))
            t.check("卡片高度 = 页头 + 行高 × 场次 + 页脚",
                    im.size == (CARD_W, CARD_HDR_H + CARD_ROW_H * 2 + CARD_FTR_H),
                    im.size)
            t.check("卡片宽度 = CARD_W", im.width == CARD_W, im.size)
            t.check("出图是好几十 KB 的真图，不是空壳", len(png) > 5000, len(png))

        png3 = render_card(many, base, dict(es, card_max_rows=3))
        if png3:
            t.check("超过 card_max_rows 时按上限截断",
                    Image.open(io.BytesIO(png3)).height
                    == CARD_HDR_H + CARD_ROW_H * 3 + CARD_FTR_H)

        # 字体文件缺失 → 安静降级（把模块级常量临时指到一个不存在的路径）
        _keep = globals()["CARD_FONT_FILE"]
        try:
            globals()["CARD_FONT_FILE"] = os.path.join(HERE, "__no-such-font__.otf")
            t.check("找不到字体文件时返回 None（不抛异常）",
                    render_card(many[:2], base, es) is None)
        finally:
            globals()["CARD_FONT_FILE"] = _keep
        t.check("恢复字体路径后又能出图",
                isinstance(render_card(many[:2], base, es), (bytes, bytearray)))

        # 队标预算为 0 → 不去联网，画占位圆照旧出图
        png_b = render_card(
            [mk(ts(14), ["A", "B"], "T",
                logos=["https://example.invalid/never.png", ""])],
            base, dict(es, logo_max_new_per_run=0))
        t.check("队标下载预算为 0 时不出网、照样出图",
                isinstance(png_b, (bytes, bytearray)), type(png_b))

        # 有画不出来的字 → 整张不出，退回纯文本（不能画豆腐块）
        t.check("有子集外的字就整张不出图",
                render_card([mk(ts(14), ["測試隊", "B"], "T")], base, es) is None)
        t.check("同一个有生僻字的场次，纯文本兜底照样能出",
                "測試隊 vs B" in format_daily(
                    [mk(ts(14), ["測試隊", "B"], "T")], base, es))

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
