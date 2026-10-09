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
     只能走 api.php。→ 所有 action=parse 都必须过 parse_gate() 这道**跨进程**闸门
     （把「上一次请求时刻」落盘），因为现在一轮战果可能要发两次 parse，
     而 timer 有三个（预告 / 结算 / 整合版），光靠错开 OnCalendar 挡不住偶发重叠。
  4. 内容 CC-BY-SA 3.0 → 推送正文必须署名「数据来源：Liquipedia」。

用法
----
    python3 esports.py                 # 正常跑一轮（该发就发，该静默就静默）
    python3 esports.py --check         # 只抓 + 打印将要发的内容，**不发消息、不写状态**
    python3 esports.py --results       # 结算一轮战果（只抓「已到点」的场次，没到点 0 请求）
    python3 esports.py --check-results # 同上但只打印/出图，**不发消息、不写状态**
                                       # 清单空着也会演练（拿页面上最近打完的 8 场），
                                       # 所以刚部署、还没跑过一次预告时也能用它验收
    python3 esports.py --daily         # 发上一个赛程日的**全天整合版**（不联网，只读清单）
    python3 esports.py --check-daily   # 同上但只打印/出图，**不发消息、不写状态**
    python3 esports.py --announce      # 开赛提醒一轮（**零网络**：读清单 + 估算开赛时刻，
                                       # 命中提醒窗口才渲染发图；由 announce.timer 每分钟拉起）
    python3 esports.py --check-announce # 同上但只打印/出图，**不发消息、不写状态**
                                        # （清单空着就没东西可看，等预告写过一轮再验收）
    python3 esports.py --teams         # 列出页面上的真实队名 + 是否已收录，用来校白名单
    python3 esports.py --test-notify   # 往配置的通道发一条测试消息（验证链路用）
    python3 esports.py --selftest      # 离线自检，不联网、不发消息

关于「战果公布」
----------------
预告过的比赛打完之后，把比分发出来。**不需要第二个数据源** ——
同一个页面（Liquipedia:Matches）本来就有两个 ticker 区块：
  · `type=upcoming|limit=50` → 未来场次
  · `type=recent |limit=50` → **最近 50 场已结束的比赛**（实测覆盖约 2 天）
所以战果和预告**共用一次请求、一份解析器、一份限速预算**。

「已结束」有三个独立信号，实测 50 场零冲突，可以互相印证：
  ① 计时器上的 data-finished="finished"   ← 最权威
  ② 对手块多带 match-info-header-winner / -loser
  ③ 比分栏从 "vs" 变成 "2:0" 这类比分
三个**全部**成立才算已结束（见 parse_matches 的 finished 字段）。

**为什么不监控 HLTV**：HLTV 的队名是显示名，实测 103 个队名里 **79 个**与 Liquipedia
不同（`BETBOOM` ↔ `BetBoom Team`、`NAVI` ↔ `Natus Vincere`、`G2` ↔ `G2 Esports`…）。
要手工维护一张近乎全量的别名表，而**写错就是静默漏发** —— 和 hltv_aliases 同一类坑。
更关键的是它**根本抓不到**：2026-10-05 实测，`/results` 和 `/matches/<id>/<slug>` 在本机和
服务器上**都**返回 403，响应体是 HLTV 自己的 Cloudflare worker 页（带
`data-client-country-iso="CN"`）—— 也就是**按来源地区挡**，换 UA / 加 Referer 都没用。
（⚠️ 但**排名页例外**：`/ranking/teams/` 实测 302 → 200、1.1 MB，所以「世界前 N」那条
照常工作。别把这两件事混成「HLTV 全站不可用」。）
**所以「逐图比分」也不用去 HLTV** —— 见下面「单场战报」。

**调度怎么省请求**：`douyu-esports-results.timer` 每 10 分钟唤起，但 run_results()
**先读本地清单、没有「已到结算窗口」的场次就直接退出（0 次网络请求）**。
有到点的场次时才抓：赛程页 1 次 + 每个**不同赛事**的赛事页各 1 次（只为拿逐图比分）。
其余时段一次都不发。（条款上限是 action=parse ≤ 1 次 / 30 秒，余量很大。）

`--check-results` 是**只读演练**（不发消息、不写状态），而且**清单空着也会演示** ——
它改用 drill_items() 拿页面上最近打完的 8 场走一遍完整流程。这是刻意的：
刚到手的服务器清单必然为空，如果演练命令这时候只会回一句「清单里没有到结算时刻的场次」，
那就等于没法验收。

关于「单场战报」与「全天整合版」
--------------------------------
两条通道，用户 2026-10-05 晚定的：

  · **单场战报**（`--results`，一场一条）：每两队一打完就发这一场。
    卡片是版式 A —— 队标 + **胜方绿名 / 负方红名** + 大比分，下面逐图一行
    （左比分 / 地图名 / 右比分，比分按**这一图**的胜负上色）。
  · **全天整合版**（`--daily`，次日早上一条）：上一个赛程日全部战果的汇总。
    卡片是版式 C —— 一场一行、只有系列比分，**不带逐图**（带的版本太臃肿）。

**逐图比分从哪来**：不是 HLTV，是 **Liquipedia 的赛事页**。
`parse_matches` 顺手把赛事的**页面路径**（`tour_page`，如 `ESL/Pro League/Season 24`）
从链接的 title 里抽出来了 —— 这是 Liquipedia 自己给的，**不需要维护任何别名表**。
有了路径就让 `fetch_event_page` 去抓那一页，`parse_event_maps` 解析 bracket 弹窗里的
`brkts-popup-body-grid-row`（实测 ESL S24：16/16 已结束场次都带逐图比分）。

三条硬约束，写在这里免得以后有人踩：
  1. **逐图只用于展示**。判断「打没打完」永远只认赛程页那三个信号，
     赛事页的数据再全也不能参与判定，否则就可能把进行中的比分当战果发出去。
  2. **拿不到就是拿不到，静默降级**。赛事页不存在 / 页面里没有这一场 /
     结构变了 → 那一行 `maps` 就是空的 → 卡片少画几行，**照样发**。
     任何异常都在 fetch_event_page 里消化掉，绝不让它冒泡成「战果发不出去」。
  3. 有些赛事给的页面路径是**系列页**而不是子页（例如 `BLAST/Premier`），
     那种页面里自然找不到这一场，结果同上：退化成只有系列比分。

**整合版为什么不联网**：它是次日早上才发的，那时赛程页的 `recent` 窗口早翻篇了，
队名/短名/队标/赛事名全都不在手上。所以单场战报**发出去的时候顺手把这些存进
state_results_pending.json**（`_prune_pending` 的保留期因此从 24h 放宽到 36h），
整合版只是把这份快照重新排版一次 —— 0 请求，也不可能「汇总出错」。

关于解析器
----------
parse_matches() 与 deploy/check-esports-net.py 里的那份是**同一份实现**（那份是
上线前的连通性自检，独立可跑所以没有 import 本文件）。改选择器时**两边都要改**。
"""

import argparse
import ast
import base64
import contextlib
import hashlib
import html as html_mod
import io
import json
import os
import re
import shutil
import subprocess
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
# 「待结算清单」：预告发出时把入选场次写进来，结算任务读它决定要不要联网。
# 单独一个文件（不和 state_esports.json 混）—— 两者由不同的 timer 读写，
# 混在一起会让「预告」和「结算」互相覆盖对方的字段。
RESULTS_STATE_FILE = os.path.join(HERE, "state_results_pending.json")
# 「上一次 action=parse 是什么时候」。条款规定 action=parse ≤ 1 次 / 30 秒，
# 而现在一轮战果可能要抓两次（① 赛程页找已结束场次 ② 赛事页取逐图比分），
# 两个 timer 还可能撞在一起。所以把「上一次请求时刻」落盘，让节流**跨进程**生效 ——
# 见 parse_gate。单靠进程内 sleep 挡不住「两个 job 同时跑」。
PARSE_STAMP_FILE = os.path.join(HERE, "state_esports_parse.json")
# 全天整合版的幂等标记：记「哪个赛程日的整合版已经发过了」。
# 存在理由：这个 job 是「次日早上固定时刻」触发的，重跑（手动 + timer）不能重发。
DAILY_STATE_FILE = os.path.join(HERE, "state_esports_daily.json")

# 状态文件落在这个目录；systemd 单元里用 ProtectSystem=full，只读 /usr /etc，
# /opt 可写，所以和 watch.py 一样直接写在脚本旁边。
CST = watch.CST
VERSION = "1.0.0"

LIQUIPEDIA_API = "https://liquipedia.net/counterstrike/api.php"

# --------------------------------------------------------------------------
# 选手数据（展示层保留，抓取层已拆出）
# --------------------------------------------------------------------------
# 单场战报 V2 有**选手段**（K-D / ADR / KAST / Rating，含 MVP），这一段是纯渲染
# 能力：模板 `result_template.html`、`build_result_match()` 的 `players` / `mvp`
# 字段、`_aggregate_players()` 聚合、`_draw_player_block()` 的 Pillow 版式。
#
# ⚠️ **当前没有可用的数据源**（2026-10-09 拆掉了最后一条抓取链路）：
#   · Liquipedia **不存**选手数据 —— 逐图比分能从它自己的赛事页拿到，但
#     「选手打得怎么样」它一概不记（2026-10-05 用真实页名验过 4 个赛事页，
#     MVP/Rating/ADR/KAST 全零命中）。
#   · csdb.gg **已失效** —— 原实现（`fetch_csdb` / `parse_csdb_players` 等）在
#     2026-10-09 实测发现它改成 Next.js 客户端渲染：单场页只剩约 60 KB 骨架，
#     `Player K D A` / `Rating` 零命中，RSC payload 里也没有数据，解析器实跑
#     返回 0 张图。它的 `/api/` 被 robots.txt 禁止；探测中还吃到 429。
#   · HLTV（rating 的唯一权威来源）**全站 403**（Cloudflare），任何 UA 都过不去。
#   · PandaScore 官方 API 免费档（Fixtures Only，1000 req/hr）**不含选手统计**，
#     要 K/D + Rating 必须上 Historical 档（400€/月/每个游戏）—— 2026-10-09 评估后
#     放弃。
#
# 所以现在：**渲染层原地保留当接口，抓取层为空**。战果行上不会有 `players` 键，
# `build_result_match()` 就返回空选段、模板自动走「居中无统计」形态 —— 这是刻意
# 设计好的降级路径，不是故障。将来若接上可用数据源，只需新写一个「把逐图选手数据
# 填进 `row["players"]`」的函数（结构见 `_aggregate_players()` 的 docstring），
# 展示层一行都不用动。

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
#
# 「战果」「周一二三四五六日」是 2026-10-05 加「战果公布」时补的
# （战果卡片的标题，以及页头上那个 `10-05 周一`）。
#
# 「地」「报」是 2026-10-05 晚加「单场战报」时补的：
#   「CS2 战报」的**报**、逐图行前缀「地图 1」的**地**。
#
# 「选手评分」是 2026-10-05 深夜加「选手数据」时补的（选手段表头的四个字）。
CARD_UI_CHARS = "赛程今天明共场次日数据来源：个事图里只列前·→　战果周一二三四五六日地报选手评分"

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

# 「单场战报」卡片（版式 A）专用的两段高度。刻意**不**复用 CARD_ROW_H：
# 单场只有一场，行高给大一点才撑得起版面（大比分需要更多垂直留白）。
#   页头 CARD_HDR_H → 对阵行 CARD_FIX_H → 逐图 0~5 行 CARD_MAP_H → 页脚 CARD_FTR_H
# 取值就是用户 2026-10-05 看过并拍板的那张预览图的取值（880×414，3 张地图）。
CARD_FIX_H = 100
CARD_MAP_H = 44
# 选手段（每张图下方）的高度：表头一行 + 每名选手一行。
# 表头 30 = 表头文字（top+1 起）+ 与首行 pill 的间隙（pill 顶 top+21），
# 之前给 26 时首行 pill 会压住「评分」表头的下半截。
CARD_PLAYER_HEAD_H = 30
CARD_PLAYER_ROW_H = 27

C_BG = (255, 255, 255)
C_INK = (26, 26, 26)
C_MUTED = (107, 106, 100)
C_FAINT = (179, 177, 169)
C_LINE = (230, 227, 221)
C_ROWLINE = (241, 239, 234)
C_PLACE = (235, 233, 228)
C_DAY = (192, 57, 43)       # 「次日」
# 胜负配色（用户 2026-10-05 明确要求）：**胜方绿名、负方红名**。
# 之前是「胜方墨色、负方压灰」的一深一浅方案，颜色更好认但分不出「谁赢了」
# 和「谁只是没输」；改成红绿之后一眼能定胜负。
# ⚠️ 红绿对色盲不友好，所以胜方**另外**再加一倍 stroke_width 的假粗体，
#    不靠颜色单打独斗（灰度打印时也还分得开）。
C_WIN = (21, 128, 61)       # 胜方队名
C_LOSE = (185, 28, 28)      # 负方队名

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

# ==========================================================================
# 1920×1080 大图**真的画得下**几行 —— 这个数是**量出来的**，不是估的（2026-10-06）
# ==========================================================================
#
# 两张模板共用这一个数：`daily_template.html`（总预告）和
# `daily_results_template.html`（全天整合版）。它们不是「碰巧相等」——
# 页头 96px、页脚 46px、主体 `.bd` 的 flex:1 + padding、`.col` 两列、
# `.sep` 跨午夜分隔线、以及 `density()` 那三档行高（≤8 行 88px / ≤11 行 70px /
# 否则 58px）是**同一套几何**，所以容量当然一样。谁哪天把其中一个模板改瘦了/改胖了，
# 就重跑尺子、把这个数拆成两个。
#
# 怎么量的：把模板灌上 N 场数据、用 Chrome 无头 `--dump-dom` 把页面上量到的几何值
# 打回来 —— 比较「最后一行的下边缘」和「主体区（.bd）的下边缘」。溢出就是被
# `overflow:hidden` 切掉（截图上根本看不出来，只能这么量）。
# 结果（`deploy/measure_img_capacity.py`，单赛事横幅在 / 跨午夜分隔线在，分别测）：
#
#     N=18  列 [9,9]    下边缘 942  ≤ 主体下边缘 1034   ✓
#     N=20  列 [10,11]  下边缘 1002 ≤ 1034              ✓
#     N=21  列 [11,11]  下边缘 1022 ≤ 1034              ✓（只剩 12px 余量）
#     N=22  列 [11,12]  下边缘 1042 >  1034              ✗ 溢出 8px，最后一列被切
#
# 为什么 22 比 21 差这么多：22 场时两列是 11 + 12 —— 列高由 12 行那一列决定，
# 行高档位一跳（70 → 58）反而让版面更紧。两张模板实测都是这个形状。
# 结论：**21 是上限**，22 起一定切版面。
#
# ⚠️ 别再在别处写 22 / 24 之类的数：`daily_max_rows` 的默认值、这个常量、
#    `config.example.json`、`douyu-esports-daily.service` 的注释、ESPORTS.md
#    必须是同一个数，自检会盯着它们一致。
HTML_IMG_MAX_ROWS = 21

ESPORT_DEFAULTS = {
    # 总开关。关掉后本脚本立刻退出，systemd 那边不会当成失败。
    "enabled": True,
    # Liquipedia 要求在 User-Agent 里写联系方式，格式：项目地址 + 邮箱。
    "ua_contact": "https://github.com/YUAN-27/qq-esports-notify; 1249850641@qq.com",
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
    # ---- 战果公布 ----
    # 预告过的比赛打完之后把比分发出来。关掉它 = 完全回到「只发预告」的行为。
    "results_enabled": True,
    # 战果卡片（复用队标/字体/Pillow 那一整套）。出不了图自动退回纯文本。
    "card_results_enabled": True,
    # 单场战报里要不要带**逐图比分**。
    # 带上它就要多抓一次「赛事页」（parse_event_maps），代价见 ESPORTS.md §3.2；
    # 关掉 / 抓不到 → 卡片自动只画对阵行，不会因此不发。
    "card_results_maps_enabled": True,
    # ⚠️ 单场战报的**选手段**（rating / ADR / KAST / K-D）**当前无可用数据源**，
    # 见文件顶部「选手数据」注释块。渲染层原地保留，但抓取层已拆掉 —— 所以这个
    # 键现在**没有消费者**，战果行上永远不会有 `players` 键，卡片会自动走
    # 「居中无统计」形态。留着它是为了将来接上新源时按原语义（开关选手段）复用，
    # 届时 `attach_players()` 的等价实现读这个键即可。
    "card_players_enabled": True,
    # 每张图、每队最多列几名选手（按 rating 降序）。5 = 全队都列。
    # 展示层仍在读它（`_render_result_card_inner`）；数据源缺失时自然无效果。
    "card_players_per_team": 3,
    # ---- 全天整合版 ----
    # 「每场一条」发完之后，再在次日早上补一条**当天全部战果**的汇总。
    # 关掉它 = 只留每场一条。
    "daily_enabled": True,
    # 每天几点发「上一个赛程日」的整合版。
    # **必须和 douyu-esports-daily.timer 的 OnCalendar 一致**，而且**必须晚于**
    # preview_run_time（09:30）—— 早于它的话 last_schedule_day() 的右端点会退到昨天，
    # 汇总的就变成前天那一场（run_daily 里有一条显式校验挡这件事）。
    # 定 09:40 还有个好处：此时赛程日已经过完 10 分钟，该结算的都结算了。
    "daily_run_time": "09:40",
    # 一个赛程日最多在整合版里列几场。
    # ⚠️ 这个默认值**必须等于** `HTML_IMG_MAX_ROWS`（= 1920×1080 模板实测画得下的
    #    行数）。原来写的是 24 —— 而模板 22 行起就会切版面、23 行起直接拒绝渲染，
    #    三个数各说各话（24 / 22 / 880px 旧卡的 12）。2026-10-06 统一到 21，自检盯着。
    #    超出的场次**仍然会出现在那一行文字里**（文字不限行），只是不进图。
    "daily_max_rows": HTML_IMG_MAX_ROWS,
    # 「开赛多久之后才开始找结果」—— 这是最重要的一个安全阀：
    # 它是**时间下限**，没有它就可能把「进行中」的比分当成战果发出去。
    # 同时它也是节流阀：不到这个点，结算任务连网络请求都不发。
    # 取值参考：Bo1 约 35~60 分钟、Bo3 约 75 分钟~3 小时、Bo5 约 2~4.5 小时。
    "results_grace_minutes": {"Bo1": 50, "Bo3": 100, "Bo5": 170},
    # 「到多久还没结果就放弃」—— 延期/取消的比赛不能让它永远占着清单。
    #
    # ⚠️ 时钟的**锚点**是「登记 ts」，也就是**计划**开赛时刻，不是实际开赛时刻。
    #    同一个场地上前一档打满三图，会把下一档拖后 30~60 分钟（实测 2026-10-05：
    #    Aurora Gaming vs BetBoom 登记 00:30、页面实际 01:20，晚了 50 分钟）。
    #    于是「打满三图的 Bo3」的总耗时 = 迟到 + 最长时长 + 页面更新延迟，
    #    必然超过「登记 ts + 最长时长」。
    #    2026-10-05 就是这么丢掉两场的：M80 vs TYLOO(2:1) 与
    #    Aurora Gaming vs BetBoom(2:1) 都被判成「延期/取消」，次日整合版只剩 6 场；
    #    同一档的另一场 Team Vitality vs Team Falcons(2:1) 是 03:26 才出的结果，
    #    距离当时的放弃时刻 03:30 **只差 4 分钟** —— 说明卡的就是余量。
    #    所以这里 = 最长时长 + 约 90 分钟的「迟到 + 页面延迟」余量。
    "results_timeout_minutes": {"Bo1": 150, "Bo3": 270, "Bo5": 360},
    # 清单里超过这么多小时还没结算的条目直接清掉（兜底，免得文件无限长大）。
    # 取 36 而不是 24：整合版是**次日早上**才发的，窗口最早那场（前一天 09:30）
    # 到发的时候已经 23 小时 50 分，用 24 会被清掉一半 → 汇总缺场次。
    "results_max_age_hours": 36,
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
    return "qq-esports-notify/%s (+%s)" % (VERSION, contact or "contact-not-set")


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


try:
    import fcntl                       # POSIX（线上是 Linux，锁一定生效）
except ImportError:                    # pragma: no cover - Windows 开发机
    fcntl = None                       # 没有 fcntl → file_lock 退化成「无锁放行」


@contextlib.contextmanager
def file_lock(path):
    """给「读—改—写同一个状态文件」加一把**跨进程**排他锁（POSIX: `flock`）。

    为什么原子写不够：`tmp + os.replace` 只保证**文件不会被写坏**，
    不保证**不丢更新** ——
      A 读到 {1}、B 也读到 {1}；A 写回 {1,2}、B 写回 {1,3} → A 加的那条没了。
    本项目里这条路径真实存在（三个**独立进程**都改同一份清单）：
      · `douyu-esports`（每日 09:30）登记待结算；
      · `douyu-esports-results`（每 10 分钟）结算并记战果；
      · `douyu-esports-daily`（每日 09:40）补漏。
    2026-10-06 外部复查提的「状态文件原子写不等于并发安全」就是这一条。

    ⚠️ 锁的持有时间必须是**一次文件读写（毫秒级）**。凡是「读 → 联网/发消息 → 写」
    的长流程，都要改成「锁内重读 + 按身份键合并」（见 `update_pending`），
    否则一个进程能把别的进程堵上几分钟。

    退化：Windows 没有 `fcntl` → 只创建锁文件、**不加锁**（本机自检因此在 Windows 上
    照样跑得动）。锁文件总是会建出来 —— 这样「锁的是独立文件」这件事在两个平台上
    都看得见，自检也就能钉住它。
    拿不到锁（权限、磁盘满之类）也只记一笔 warn 后放行 —— 宁可极小概率丢更新，
    也不能因为加锁失败就不发消息。
    """
    lock_path = path + ".lock"
    fp = None
    try:
        try:
            d = os.path.dirname(lock_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            # 用独立的 .lock 文件而不是锁状态文件本身：状态文件是 rename 替换的，
            # 锁在「被替换掉的那个 inode」上会立刻失效，等于没锁。
            fp = open(lock_path, "a+")
            if fcntl is not None:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            log("[warn] 状态文件加锁失败（%s），本轮退化成无锁：%s" % (lock_path, exc))
        yield
    finally:
        if fp is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                fp.close()
            except OSError:
                pass


def print_lock_note():
    """自检/日志用：本机到底有没有真的上锁。"""
    return "flock 生效" if fcntl is not None else "无 fcntl，退化成无锁"


def parse_gate_delay(last_ts, now_ts, gap):
    """纯函数：距离「允许再发一次 action=parse」还差几秒。

    抽成纯函数是为了能在离线自检里钉住它 —— 这个数字是**条款合规**的唯一依据，
    不能只靠肉眼看 sleep 调用。
    """
    gap = max(0, int(gap))
    if not last_ts or gap <= 0:
        return 0.0
    delta = float(now_ts) - float(last_ts)
    # 上限就是 gap：时钟回拨、或别的机器写了未来时间时，只等一个完整间隔，
    # 不跟着算出一个荒唐的大数（否则这一轮会被卡到 systemd 的 TimeoutStartSec）。
    return min(float(gap), max(0.0, float(gap) - delta))


def _load_parse_stamp(path=PARSE_STAMP_FILE):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return float(json.load(fp).get("last") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _save_parse_stamp(now_ts, path=PARSE_STAMP_FILE):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump({"last": float(now_ts)}, fp)
        os.replace(tmp, path)
    except OSError as exc:
        # 写不进去只是「节流退化成进程内」，不该让整轮失败。
        log("[warn] 节流时间戳写不进去（%s）：%s" % (path, exc))


def parse_gate(es, path=PARSE_STAMP_FILE):
    """发 `action=parse` 前的等待闸门。返回实际等了几秒（浮点，0 = 没等）。

    **为什么需要它**：条款规定 `action=parse` ≤ 1 次 / 30 秒，而
      ① 一轮战果现在可能要发两次 parse（赛程页找战果 + 赛事页取逐图比分）；
      ② `douyu-esports.timer` / `-results.timer` / `-daily.timer` 是三个独立进程，
         单靠「把 OnCalendar 错开」挡不住偶发重叠。
    所以把「上一次请求时刻」落盘，让节流**跨进程**生效：谁先到谁先写。

    ⚠️ 整个「读时间戳 → 等 → 写时间戳」都在**同一把跨进程锁**里（2026-10-06 加）。
       原来只是「读完再写」，两个进程同时读到旧值时都会决定「不用等」、然后各自写 ——
       30 秒的间隔就白留了。加锁之后第二个进程会等第一个走完，**在锁内重读**时间戳，
       于是它算出的是真正的剩余等待时间。这就是节流该有的语义。

    为什么不放进 `fetch_once`：队标走的是 commons 图片，不在 parse 的限制里，
    不该跟着一起等 30 秒。
    """
    gap = max(0, int((es or {}).get("parse_min_interval_seconds") or 30))
    delay = 0.0
    with file_lock(path):
        # ⚠️ 必须在**锁内**重读：锁外读到的是可能已经被别人刷新过的旧值。
        delay = parse_gate_delay(_load_parse_stamp(path), time.time(), gap)
        if delay > 0:
            log("[info] 条款节流：距上一次请求不足 %d 秒，先等 %.1f 秒再抓" % (gap, delay))
            time.sleep(delay)
        _save_parse_stamp(time.time(), path)
    return delay


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
        # 每次尝试前都过闸门 —— 连「第一次」也要过：可能上一轮（或另一个 timer）
        # 刚刚抓过。这也是原来的 time.sleep(gap) 做不到的地方（它只跨得了本轮）。
        parse_gate(es)
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


def build_event_url(page):
    """赛事页和赛程页**同一个 API**，只是 page 参数不同。

    不去抓渲染好的 HTML 页面（`/counterstrike/...`）—— 条款要求只走 api.php，
    而且渲染页会把整个皮肤/导航都下回来（实测 600 KB vs API 的 250 KB）。
    """
    q = urllib.parse.urlencode({
        "action": "parse",
        "page": page,
        "prop": "text",
        "format": "json",
    })
    return LIQUIPEDIA_API + "?" + q


def fetch_event_page(es, page, cache=None):
    """抓一页赛事页，返回渲染 HTML；失败返回 None。**永不抛错。**

    `cache`：调用方传一个 dict，同一轮里两场同赛事的比赛就只抓一次。
    抓不到（页面不存在、被限流、结构变了）一律返回 None —— 逐图比分是**锦上添花**，
    没有它照样要把战果发出去（卡片会自动少画几行）。
    """
    page = (page or "").strip()
    if not page:
        return None
    cache = cache if cache is not None else {}
    if page in cache:
        return cache[page]

    html_text = None
    try:
        parse_gate(es)                      # 和抓赛程页共用同一道条款闸门
        r = fetch_once(build_event_url(page), build_ua(es),
                       timeout=max(5, int(es.get("http_timeout") or 25)))
        if r["ok"]:
            html_text, err = extract_html(r["text"])
            if html_text is None:
                log("[warn] 赛事页 %s 的响应看不懂：%s" % (page, err))
        else:
            log("[warn] 赛事页 %s 抓取失败：%s（本轮不带逐图比分）" % (page, r["error"]))
    except Exception as exc:  # noqa: BLE001
        log("[warn] 赛事页 %s 抓取出错：%s: %s（本轮不带逐图比分）"
            % (page, type(exc).__name__, exc))
    cache[page] = html_text
    return html_text


# 赛事页里一场对阵就是一个 bracket 弹窗。类名三个词连在一起，实测整页唯一。
POPUP_MARK = "brkts-popup brkts-popup-container brkts-match-info-popup"


def parse_event_maps(html_text):
    """从赛事页里抽出「每场对阵的逐图比分」。返回 list[dict]。

    ⚠️ 这份数据**只用于展示**，判断「打没打完」仍然只认赛程页那三个信号。
       所以这里即使解析得不够全，最坏也只是少几行地图，不会误报战果。

    实测（ESL Pro League Season 24）的弹窗结构：
        <div class="brkts-popup …brkts-match-info-popup">
          …计时器 data-timestamp / data-finished…
          <div class="match-info-header">            ← 对阵双方 + 系列比分
          <div class="brkts-popup-body-grid">        ← 逐图正文
            <div class="brkts-popup-body-grid-row">  ← 一张地图一段
                <a href="/counterstrike/Dust_II">Dust II</a>
                <div class="…detailed-scores-main-score">13</div> …>5</div>

    两个踩过的坑：
      1. 队名要优先取 `<div class="team-name">` 的内层文本。直接抓 `title="…"`
         会把同一支队的名字拼三遍（实测拼出过 `AimclubAimclubAimclub`）。
      2. 按**字面量** `<div class="brkts-popup-body-grid-row">` 切段，
         每段到下一条行标签为止。不这么切的话地图数量会算成 0。
    """
    html_text = html_text or ""
    pos = [m.start() for m in re.finditer(re.escape(POPUP_MARK), html_text)]
    pos.append(len(html_text))
    out = []
    for i, start in enumerate(pos[:-1]):
        seg = html_text[start:pos[i + 1]]

        ts = re.search(r'data-timestamp="(\d{9,})"', seg)
        if not ts:
            continue
        fin = re.search(r'data-finished="([^"]*)"', seg)
        body_at = seg.find("brkts-popup-body-grid")
        head = seg[:body_at] if body_at > 0 else seg

        # 对阵双方 + 系列比分：只看弹窗正文之前的那段（head），
        # 免得把逐图行里的数字也当成系列比分。
        sides = []
        for m in re.finditer(
                r'<div class="match-info-header-opponent([^"]*)">(.*?)'
                r'(?=<div class="match-info-header-(?:opponent|scoreholder)|$)', head, re.S):
            nm = re.search(r'<div class="team-name">(.*?)</div>', m.group(2), re.S)
            if nm:
                sides.append(tidy(nm.group(1)))
                continue
            nm = re.search(r'<a[^>]*title="([^"]*)"', m.group(2))
            sides.append(tidy(nm.group(1)) if nm else "")
        if len(sides) < 2 or not all(sides[:2]):
            continue

        series = [tidy(x) for x in re.findall(
            r'match-info-header-scoreholder-score[^"]*">\s*([^<]*?)\s*</span>', head)]

        # 逐图：按 grid-row 切段，每段一张地图
        maps = []
        body = seg[body_at:] if body_at > 0 else ""
        rows = [m.start() for m in
                re.finditer(r'<div class="brkts-popup-body-grid-row">', body)]
        rows.append(len(body))
        for j, s in enumerate(rows[:-1]):
            row = body[s:rows[j + 1]]
            mp = re.search(r'href="/counterstrike/[^"]+"[^>]*>([^<]+)</a>', row)
            if not mp:
                continue
            rounds = [tidy(x) for x in re.findall(
                r'detailed-scores-main-score">([^<]*)</div>', row)]
            # 没打的地图（比分栏是空的）直接丢掉 —— 留着会画出一行「13  Nuke」
            if len(rounds) < 2 or not all(x.isdigit() for x in rounds[:2]):
                continue
            maps.append({"map": tidy(mp.group(1)),
                         "rounds": [rounds[0], rounds[1]]})

        hltv = re.search(r'hltv\.org/matches/(\d+)/', seg)
        out.append({
            "ts": int(ts.group(1)),
            "teams": sides[:2],
            "finished": bool(fin and fin.group(1) == "finished"),
            "series": series[:2],
            "maps": maps[:5],
            "hltv_id": hltv.group(1) if hltv else "",
        })
    return out


def event_maps_index(events):
    """把 parse_event_maps 的结果做成 {(时间戳, 队名键): 逐图列表}。

    键里带时间戳是必须的：同一对队伍在同一个赛事里可能打好几轮
    （实测 `Legacy vs PARIVISION` 这类对阵会在赛程里出现多次），
    只按队名配对会把另一轮的比分贴到这一轮上。
    """
    out = {}
    for e in events or []:
        if not e.get("maps"):
            continue
        key = (int(e.get("ts") or 0), _teams_key(e.get("teams")))
        out[key] = e["maps"]
    return out


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
#        <span class="team-template-image-icon team-template-lightmode">  ← 浅色页面用这张（黑蜜蜂）
#        <span class="team-template-image-icon team-template-darkmode">   ← 深色页面用这张（黄蜜蜂）
#    写成等号形式就只认得第一类，G2 / NAVI / Vitality / Spirit 这些名队
#    会**静默地**变成灰色占位块（不报错、日志里也没有 warn），很难发现。
#    我们所有卡片（Pillow / HTML）都是深色底，所以**先认 darkmode**：
#    darkmode 那张就是为深色背景准备的（Vitality=黄蜜蜂，lightmode 是黑蜜蜂，
#    黑图贴深底=整只蜜蜂凭空消失，2026-10-05 晚真实预览踩到）。
#    只有 lightmode 一张图的队伍极罕见；那种情况退回前缀匹配也能取到图。
TEAM_ICON_MARK = 'class="team-template-image-icon'
TEAM_ICON_DARK_MARK = 'class="team-template-image-icon team-template-darkmode"'


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
         同日晚些时候又改过一次**优先级**：最早「先认 lightmode」是白底卡片时代
         的遗留，转深色卡后黑蜜蜂贴深底直接隐身 —— 现在**先认 darkmode**。
    """
    parts = seg.split(OPPONENT_MARK)[1:]
    out = []
    for part in parts[:2]:
        # 先认「暗色版」（深色卡片专用，如 Vitality 黄蜜蜂）；
        # 认不到再退回前缀匹配（单图 allmode 队伍 / 只有亮色图的队伍）
        m = (re.search(re.escape(TEAM_ICON_DARK_MARK), part)
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

        # 一对 (长名, 短名)：title 是长名，<a> 的内层文本是页面自己的缩写
        #   <span class="name"><a href="/…" title="G2 Esports">G2</a></span>
        # 短名只给「队标拿不到时的灰色占位块」用（见 _card_placeholder）。
        pairs = [(tidy(a), tidy(b)) for a, b in re.findall(
            r'class="name"[^>]*>\s*<a[^>]*\stitle="([^"]+)"[^>]*>([^<]*)</a>', seg)]
        teams = [t for t, _s in pairs if t]
        shorts = [s for t, s in pairs if t]
        if len(teams) < 2:
            # 退化路径：TBD 之类没有词条、也没 title 属性的占位队名
            teams = [t for t in (tidy(x) for x in re.findall(
                r'class="name"[^>]*>\s*(?:<a[^>]*>)?([^<]+)', seg)) if t]
            shorts = []

        bo = re.search(r'scoreholder-lower">\s*\(?(Bo\d)\)?', seg)

        # ---- 「已结束」三件套（战果公布用；预告不看这三个字段）----
        # 三个信号互相独立，实测 50 场已结束场次零冲突：
        #   ① data-finished="finished"（计时器控件上；未开打的场次**根本没有这个属性**）
        #   ② 对手块多带 match-info-header-winner / -loser（未开打时只有 -opponent）
        #   ③ 比分栏出现两个数字（未开打时是 "vs"）
        # 必须三个**全部**成立才算 finished —— 宁可漏报，也不能把进行中的比分当战果。
        fin = re.search(r'data-finished="([^"]*)"', seg)
        sides = ["W" if "winner" in c else ("L" if "loser" in c else "")
                 for c in re.findall(
                     r'<div class="(match-info-header-opponent[^"]*)"', seg)][:2]
        # 比分单独取两个 <span class="…scoreholder-score…">N</span>，不用「从 upper 抓到 lower」
        # 那种正则 —— 未开打时 upper 里面是 "vs"，一路抓到 lower 会连 "(Bo3)" 一起吞进来。
        nums = [tidy(x) for x in re.findall(
            r'scoreholder-score[^"]*">\s*([^<]*?)\s*</span>', seg)]
        finished = bool(fin and fin.group(1) == "finished"
                        and len(nums) >= 2 and sorted(sides) == ["L", "W"])

        tour = re.search(r'match-info-tournament-name.{0,300}?<span>([^<]+)</span>',
                         seg, re.S)
        if not tour:
            tour = re.search(r'match-info-tournament.{0,400}?title="([^"]+)"', seg, re.S)

        # 赛事**页面路径** —— 拿逐图比分要用它去抓「赛事页」（见 fetch_event_page）。
        # 好消息：这个路径是 Liquipedia 自己在链接里给的，**不用我维护任何别名表**：
        #   <a href="/counterstrike/ESL/Pro_League/Season_24#Round_3"
        #      title="ESL/Pro League/Season 24#Round 3">
        # 取 title 而不是 href：href 带 /counterstrike/ 前缀和 URL 转义，title 是裸页面名。
        # 剥掉 `#Round_N` 锚点 —— 我们要的是整页，不是某个小节。
        #
        # ⚠️ 不是所有赛事都会给「子页面」：没有独立子页面的赛事
        #    （实测 fixture 那种 `title="BLAST/Premier"`）给的是**系列页**。
        #    那种情况赛事页里当然找不到这一场对阵 → 逐图数据拿不到 →
        #    卡片自动少画几行（见 render_result_card）。**不会报错、更不会不发。**
        tp = re.search(r'match-info-tournament.{0,400}?title="([^"]+)"', seg, re.S)
        tour_page = tidy(re.sub(r"#.*$", "", tp.group(1))) if tp else ""

        teams = teams[:2]
        # 短名必须和 teams **一一对齐**，否则占位块会写成对手的缩写（比不写更坏）。
        # 少了补空串、多了截掉，长度对齐这一件事只在这一行做。
        shorts = (shorts[:len(teams)] + [""] * len(teams))[:len(teams)]
        logos = _logo_urls(seg)
        out.append({
            "ts": int(ts.group(1)),
            "teams": teams,
            "shorts": shorts,
            "logos": logos if len(logos) == len(teams) else ["", ""],
            "bo": bo.group(1) if bo else "",
            "tour": tidy(re.sub(r"#.*$", "", tour.group(1))) if tour else "",
            # 赛事页路径（`ESL/Pro League/Season 24`）。拿逐图比分用；
            # 空串 = 这条链接没给页面路径，那就只能发系列比分。
            "tour_page": tour_page,
            "tbd": len(teams) >= 2 and all(t.upper() == "TBD" for t in teams),
            # 已结束；sides 与 teams **同序**（左、右）；score 形如 "2:0"。
            # ⚠️ Bo3/Bo5 给的是**系列比分**，Bo1 给的是**地图比分**（13:4）——
            #    展示时照抄即可，但别把 Bo1 的 13:4 说成系列比分。
            "finished": finished,
            "sides": sides if len(sides) == len(teams) else [""] * len(teams),
            "score": "%s:%s" % (nums[0], nums[1]) if len(nums) >= 2 else "",
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


def last_schedule_day(now, run_time=None):
    """**上一个已经过完的赛程日**，返回 (start, end)。

    和 `preview_window` 的区别就一句话：那个给的是「即将开始的 24 小时」，
    这个给的是「刚刚过完的 24 小时」。所以取「**已经不晚于 now 的**那个 run_time
    时刻」当右端点 —— 09:40 跑（预告 09:30）拿到的是「昨天 09:30 → 今天 09:30」。

    两个概念必须分开，不能拿 preview_window 凑：那个函数在 09:40 会返回
    「今天 09:30 → 明天 09:30」，正好把要汇总的那一天整个跳过去。

    ⚠️ 隐含前提：`daily_run_time` 必须**晚于** `preview_run_time`。
       反之（例如 09:20 跑）右端点会落到昨天 09:30，汇总的就变成**前天**那一场。
       `run_daily` 里有一条显式校验挡这件事，自检也钉了。
    """
    h, mi = parse_run_time(run_time)
    end = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    return end - timedelta(days=1), end


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


def _card_logo(img, draw, path, box_x, cy, code):
    """贴队标。读不出来就画灰底占位块 —— 一张图坏掉不该毁掉整张卡片。"""
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
    _card_placeholder(draw, box_x, cy, code)


# 占位块里那行字从大到小试，塞不进方块就缩一档。最长的短名是 "Vitality"（8 字），
# 缩到 11~12 px 正好；再长（页面偶尔会有）就走截断 + 省略号。
_PLACEHOLDER_SIZES = (15, 14, 13, 12, 11, 10, 9)


def _card_placeholder(draw, box_x, cy, code):
    """队标拿不到时的灰底占位块，里面写**页面上那个短名**。

    为什么不用「队名前两个字母」（最初就是这么写的）：
    `Team Spirit` / `Team Vitality` / `Team Falcons` 全都会变成 **`TE`** ——
    同一张卡片上摆三块一模一样的方块，等于什么都没写。
    页面自带 <span class="name"> 的短名（`Spirit` / `Vitality` / `NAVI` / `BB`）
    **整页零重复**（实测 45 支），所以直接用它。

    短名长短差很多（`PV` 2 字 vs `Vitality` 8 字），而方块只有 52 px，
    所以字号跟着缩；缩到最小还放不下才截断。**宁可变小，不要溢出压到队名。**
    """
    box = CARD_LOGO
    draw.rounded_rectangle([box_x, cy - box // 2, box_x + box, cy + box // 2],
                           radius=8, fill=C_PLACE)
    text, f = _placeholder_label(draw, code)
    draw.text((box_x + box // 2, cy - 1), text, font=f, fill=C_MUTED, anchor="mm")


def _placeholder_label(draw, code):
    """挑出占位块里那行字，返回 (文本, 字体)。抽成纯函数是为了能断言它。"""
    room = CARD_LOGO - 8
    text = (code or "").strip() or "?"
    f = load_card_font(_PLACEHOLDER_SIZES[-1])
    for size in _PLACEHOLDER_SIZES:
        f = load_card_font(size)
        if draw.textlength(text, font=f) <= room:
            return text, f
    out = text
    while out and draw.textlength(out + "…", font=f) > room:
        out = out[:-1]
    return (((out + "…") if out else "?"), f)


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


# ==========================================================================
# HTML 大图卡片（1920×1080，Chromium 无头截图）—— 2026-10-05 用户拍板的 V2 版式
# --------------------------------------------------------------------------
# 和上面 880px 的 Pillow 卡是**三级降级**关系：
#   HTML 大图出不了（没装 Chromium / 缺模板 / 截图失败）
#   → 退回 880px Pillow 旧卡 → 再出不了 → 纯文本。
# 每一级都不影响「消息必须发出去」这条底线。开关：esports.card_html_enabled
# （默认开）。esports.chrome_bin 可以钉死浏览器路径；不配就按惯例名找。
# 模板占位符 __FONTDIR__ / __MATCH__ / __DAILY__ 在渲染时替换，模板本体不带数据。
# ==========================================================================

HTML_CARD_W, HTML_CARD_H = 1920, 1080
HTML_FONT_DIR = os.path.join(HERE, "fonts")
RESULT_TEMPLATE_FILE = os.path.join(HERE, "result_template.html")
DAILY_TEMPLATE_FILE = os.path.join(HERE, "daily_template.html")
DAILY_RESULTS_TEMPLATE_FILE = os.path.join(HERE, "daily_results_template.html")
PREVIEW_TEMPLATE_FILE = os.path.join(HERE, "preview_template.html")
ANNOUNCE_STATE_FILE = os.path.join(HERE, "state_esports_announce.json")

# ---- 开赛提醒（STARTING SOON）的估算参数 ----
# 数据源没有「比赛实际开打」的实时信号（Liquipedia 计时器到点变 LIVE 需要轮询，
# 和条款网关冲突），所以 estimatedStart 只能**纯本地**算：自己的计划时刻，
# 与「同赛事里**共用同一支队伍**的更早那场」的推算结束时间取大者。
#
# ⚠️ 判据是「共用队伍」，不是「同赛事」—— 一个赛事可以同时开两条并行流
#    （EPL 一个 Round 就是 3 个时段 × 2 条流、12 支不同队伍）。只有**同一支队伍
#    的两场**才在物理上不可能并行，因此只有那种情况才该级联。
#    2026-10-06 的线上故障就是这么来的：按「同赛事」级联会把并行流串成一条链，
#    20:00 的第二场被推到 22:50、22:30 的推到 01:20、01:00 的推到 07:00/09:50，
#    结果一个 Round 六场里只有第一场按时提醒。
ANNOUNCE_TURNAROUND_MIN = 30      # 前一场打完到下一场开始的最短间隔
ANNOUNCE_DURATE_MIN = {"Bo1": 80, "Bo3": 140, "Bo5": 240}   # fallback 时长模型
ANNOUNCE_REMIND_DRIFT_SEC = 600   # estimated 漂移超过 10 分钟才考虑重提醒
ANNOUNCE_REMIND_MAX = 1           # 最多重提醒 1 次
ANNOUNCE_TOO_LATE_SEC = 300       # estimated 已过 5 分钟 → 过时不候（战报马上来）

IS_WINDOWS = sys.platform.startswith("win")
# 惯例名走 PATH；但 systemd 的 PATH 通常不含 /snap/bin，snap 装的 Chromium
# 必须用绝对路径兜底（which 对含路径分隔符的候选只做可执行性检查，正好够用）。
_CHROME_CANDIDATES = ("chromium", "chromium-browser", "google-chrome",
                      "google-chrome-stable", "chrome",
                      "/snap/bin/chromium", "/snap/bin/chromium-browser",
                      "/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable")
_CHROME_CACHE = []


class HtmlCardError(Exception):
    """HTML 卡片渲染失败（找不到浏览器 / 截图超时 / 输出是空壳）。"""


def _file_uri(path):
    """本地路径 → file:// URI（模板里 @font-face 和 <img> 都用它）。"""
    p = os.path.abspath(path).replace("\\", "/")
    return "file:///" + p.lstrip("/")


def find_chrome(es):
    """找可用的 Chromium/Chrome，返回可执行文件路径；找不到返回 None。

    esports.chrome_bin 指定了就**只用它**（钉死版本 / 测降级路径都靠它）；
    没指定才按惯例名 + Windows 默认安装位置找。结果缓存在模块级：
    一轮最多探测一次（which 不便宜，timer 每 10 分钟都会跑）。
    """
    es = es or {}
    conf = (es.get("chrome_bin") or "").strip()
    if conf:
        if os.path.isfile(conf):
            return conf
        return shutil.which(conf)
    if _CHROME_CACHE:
        return _CHROME_CACHE[0]
    cands = list(_CHROME_CANDIDATES)
    if IS_WINDOWS:
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    for c in cands:
        w = shutil.which(c)
        if w:
            _CHROME_CACHE.append(w)
            return w
    return None


def render_html_png(template_html, data, es):
    """模板 + 数据对象 → 1920×1080 PNG bytes。失败抛 HtmlCardError（上层降级）。"""
    chrome = find_chrome(es)
    if not chrome:
        raise HtmlCardError("找不到 Chromium/Chrome")
    inject = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (template_html
            .replace("__FONTDIR__", _file_uri(HTML_FONT_DIR))
            .replace("__MATCH__", inject)
            .replace("__DAILY__", inject)
            .replace("__RESULTS__", inject))
    work = tempfile.mkdtemp(prefix="htmlcard_")
    try:
        hp = os.path.join(work, "card.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)
        png = os.path.join(work, "out.png")
        # --no-sandbox：systemd 以 root 跑 Chromium 必须加，否则起不来
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--no-first-run", "--hide-scrollbars",
               "--allow-file-access-from-files",
               "--window-size=%d,%d" % (HTML_CARD_W, HTML_CARD_H),
               "--user-data-dir=" + os.path.join(work, "prof"),
               "--screenshot=" + png, _file_uri(hp)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=90, check=False)
        except subprocess.TimeoutExpired:
            raise HtmlCardError("Chromium 截图超时（>90 秒）")
        except OSError as exc:
            raise HtmlCardError("Chromium 起不来：%s" % exc)
        if not os.path.isfile(png) or os.path.getsize(png) < 5000:
            raise HtmlCardError("Chromium 没出图（或图是空壳）")
        with open(png, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _html_logo(url, es, budget):
    """队标 URL → 本地缓存路径的 file:// URI；拿不到返回空串（模板画占位块）。"""
    if not url:
        return ""
    p = ensure_logo(url, es, budget)
    return _file_uri(p) if p else ""


def _aggregate_players(row):
    """把「逐图选手原始结构」聚成整场：K/D 累加，ADR/KAST/Rating 取平均。

    ⚠️ 这是**展示层**的转换器，输入契约是 `row["players"]` 的形状（将来接新数据源
    时按这个填即可）：

        row["players"] = [
            {"map": "Dust2", "players": [
                {"name": "makazze", "team": "NAVI", "k": 23, "d": 15, "a": 6,
                 "pm": 8, "adr": 104.6, "kast": 78, "rating": 1.96}, …]},
            …  # 每张图一项；players 为空列表的图会被跳过
        ]

    `pm`（正负差）可缺省，聚合后不用；`k`/`d` 缺省按 0、`adr`/`kast`/`rating`
    缺省按 0 处理，所以结构不全也不会炸。

    返回 (按 rating 降序的行, 有数据的图数)。只聚合真有数据的图，
    绝不复制填充 —— 和「不伪造数据」的总原则一致。
    """
    agg, n_maps = {}, 0
    for pm in row.get("players") or []:
        ps = pm.get("players") or []
        if not ps:
            continue
        n_maps += 1
        for p in ps:
            e = agg.setdefault(p["name"], {
                "name": p["name"], "team": (p.get("team") or "").strip(),
                "k": 0, "d": 0, "adr": [], "kast": [], "rating": []})
            e["k"] += int(p.get("k") or 0)
            e["d"] += int(p.get("d") or 0)
            e["adr"].append(float(p.get("adr") or 0))
            e["kast"].append(float(p.get("kast") or 0))
            e["rating"].append(float(p.get("rating") or 0))
    rows = [dict(name=e["name"], team=e["team"], k=e["k"], d=e["d"],
                 adr=sum(e["adr"]) / len(e["adr"]),
                 kast=sum(e["kast"]) / len(e["kast"]),
                 rating=sum(e["rating"]) / len(e["rating"]))
            for e in agg.values()]
    rows.sort(key=lambda x: -x["rating"])
    return rows, n_maps


def build_result_match(row, es):
    """单场战报 V2 的数据对象（喂 result_template.html）。"""
    es = es or {}
    teams = list(row.get("teams") or []) + ["", ""]
    shorts = list(row.get("shorts") or []) + ["", ""]
    logos = list(row.get("logos") or []) + ["", ""]
    nums = str(row.get("score") or "").split(":")
    sa = int(nums[0]) if len(nums) == 2 and nums[0].isdigit() else 0
    sb = int(nums[1]) if len(nums) == 2 and nums[1].isdigit() else 0
    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    event, _, stage = str(row.get("tour") or "").partition(" - ")
    maps = []
    for i, m in enumerate(list(row.get("maps") or [])[:5]):
        r = list(m.get("rounds") or [])
        x = int(r[0]) if len(r) > 0 and str(r[0]).isdigit() else 0
        y = int(r[1]) if len(r) > 1 and str(r[1]).isdigit() else 0
        maps.append({"number": i + 1,
                     "name": m.get("map") or "Map %d" % (i + 1),
                     "teamA": x, "teamB": y})
    prows, n_maps = _aggregate_players(row)
    # 分列用 _team_same（双向包含），容忍「全名 vs 缩写」的写法差异；
    # 归不进任何一队的宁可整列空着，也绝不错分到对面
    rows_a = [r for r in prows if _team_same(r["team"], teams[0])]
    rows_b = [r for r in prows if _team_same(r["team"], teams[1])]
    mvp = dict(prows[0]) if prows else None
    if mvp is not None and not mvp.get("team"):
        mvp["team"] = teams[0]
    ts = int(row.get("ts") or 0)
    bo = (row.get("bo") or "").strip().upper()
    return {
        "event": event.strip(), "stage": stage.strip().upper(),
        "date": datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d") if ts else "",
        "format": bo or ("BO%d" % len(maps) if maps else ""),
        "teamA": {"name": teams[0], "short": shorts[0],
                  "logo": _html_logo(logos[0], es, budget), "score": sa},
        "teamB": {"name": teams[1], "short": shorts[1],
                  "logo": _html_logo(logos[1], es, budget), "score": sb},
        "maps": maps, "mvp": mvp, "mvp_basis": n_maps,
        "players": {"teamA": rows_a, "teamB": rows_b},
    }


def build_daily_data(picked, now, es):
    """总预告 V2 的数据对象（喂 daily_template.html）。

    场次太多（> `HTML_IMG_MAX_ROWS`）或空场次返回 None —— 退回 Pillow 旧卡，绝不硬画。
    ⚠️ 以前这里写的是 22（「双列 11 行/列正好压在第二档密度的舒适区」），
       2026-10-06 实测发现 **22 行就溢出 8px、21 行才装得下** —— 和整合版是同一套几何、
       同一个常量。改模板后请重跑 `deploy/measure_img_capacity.py`。
    """
    es = es or {}
    ms = sorted(picked or [], key=lambda m: int(m.get("ts") or 0))
    if not ms or len(ms) > HTML_IMG_MAX_ROWS:
        return None
    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    start = now.date()
    out, events = [], []
    for m in ms:
        ts = int(m.get("ts") or 0)
        dt = datetime.fromtimestamp(ts, CST)
        teams = list(m.get("teams") or []) + ["", ""]
        shorts = list(m.get("shorts") or []) + ["", ""]
        logos = list(m.get("logos") or []) + ["", ""]
        ev = (m.get("tour") or "").strip()
        if ev and ev not in events:
            events.append(ev)
        out.append({
            "ts": ts, "time": dt.strftime("%H:%M"),
            "day": (dt.date() - start).days,
            "dateLabel": dt.strftime("%b %d").upper(),
            "teamA": {"name": teams[0], "short": shorts[0] or teams[0],
                      "logo": _html_logo(logos[0], es, budget)},
            "teamB": {"name": teams[1], "short": shorts[1] or teams[1],
                      "logo": _html_logo(logos[1], es, budget)},
            "event": ev, "bo": (m.get("bo") or "").strip().upper(),
            "status": "UPCOMING",
        })
    end = datetime.fromtimestamp(int(ms[-1].get("ts") or 0), CST)
    w0, w1 = preview_window(now, es.get("preview_run_time"))
    return {
        "generatedAt": now.strftime("%H:%M"),
        "dateLabel": start.strftime("%b %d").upper(),
        "endDateLabel": end.strftime("%b %d").upper(),
        "windowLabel": "%s → %s" % (w0.strftime("%H:%M"), w1.strftime("%H:%M")),
        "matches": out,
    }


def render_result_card_html(row, es):
    """单场战报 HTML 大图（1920×1080）。出不了返回 None（上层退回 880px 旧卡）。"""
    if not row:
        return None
    try:
        if not os.path.isfile(RESULT_TEMPLATE_FILE):
            raise HtmlCardError("缺模板 %s" % RESULT_TEMPLATE_FILE)
        with open(RESULT_TEMPLATE_FILE, encoding="utf-8") as f:
            tmpl = f.read()
        png = render_html_png(tmpl, build_result_match(row, es), es)
        log("[info] 单场战报大图（HTML 1920×1080）：PNG %.1f KB"
            % (len(png) / 1024))
        return png
    except HtmlCardError as exc:
        log("[info] HTML 战报大图出不了（%s）→ 退回 880px 旧卡" % exc)
    except Exception as exc:  # noqa: BLE001
        log("[warn] HTML 战报大图构建出错（%s: %s）→ 退回 880px 旧卡"
            % (type(exc).__name__, exc))
    return None


def render_card_html(picked, now, es):
    """总预告 HTML 大图（1920×1080）。出不了返回 None（上层退回 880px 旧卡）。"""
    try:
        if not os.path.isfile(DAILY_TEMPLATE_FILE):
            raise HtmlCardError("缺模板 %s" % DAILY_TEMPLATE_FILE)
        data = build_daily_data(picked, now, es)
        if data is None:
            raise HtmlCardError("空场次或超过 30 场，版式兜不住")
        with open(DAILY_TEMPLATE_FILE, encoding="utf-8") as f:
            tmpl = f.read()
        png = render_html_png(tmpl, data, es)
        log("[info] 总预告大图（HTML 1920×1080）：PNG %.1f KB" % (len(png) / 1024))
        return png
    except HtmlCardError as exc:
        log("[info] HTML 总预告出不了（%s）→ 退回 880px 旧卡" % exc)
    except Exception as exc:  # noqa: BLE001
        log("[warn] HTML 总预告构建出错（%s: %s）→ 退回 880px 旧卡"
            % (type(exc).__name__, exc))
    return None


def build_daily_results_data(rows, now, es, gen_at=None):
    """全天整合版 V2 的数据对象（喂 daily_results_template.html）。

    和总预告 build_daily_data 是同一套版式语言，但每行画的是**已定的比分**：
    `[时间] 队A 2:0 队B`，胜方绿底 chip、负方灰。

    场次太多（> `HTML_IMG_MAX_ROWS`）或空场次返回 None —— 退回 880px Pillow 旧卡，
    绝不硬画一张被 `overflow:hidden` 切掉的图。

    `now` 是**赛程日**（窗口起点），用来出 `dayLabel`、`dateLabel` 这一组
    「这是哪天的战果」标签；`gen_at` 是**真正生成这张卡的时刻**，只用来出页脚的
    `generatedAt`。两者刻意分开 —— 合一的话页脚会写「LAST UPDATED 10-05 09:30」，
    而那个时刻是窗口的**起点**（数据其实一直收到次日 09:30），读起来会像是
    「数据截至昨天早上」。不传 `gen_at` 时退回 `now`。
    """
    es = es or {}
    rs = sorted(rows or [], key=lambda r: int(r.get("ts") or 0))
    # ⚠️ `HTML_IMG_MAX_ROWS`（21）是**量出来的**模板容量，见常量定义处的实测表。
    #    超过就返回 None 让上层退 880px 旧卡 —— 旧卡页脚会明说「图里只列前 N 场」，
    #    比硬画一张被 overflow:hidden 切掉最后一行的图诚实。
    #    (`run_daily` 已经按同一个常量截过一刀，这里是给直接调用方的兜底。)
    if not rs or len(rs) > HTML_IMG_MAX_ROWS:
        return None
    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    start = now.date()
    out, events = [], []
    for r in rs:
        ts = int(r.get("ts") or 0)
        dt = datetime.fromtimestamp(ts, CST)
        teams = list(r.get("teams") or []) + ["", ""]
        shorts = list(r.get("shorts") or []) + ["", ""]
        logos = list(r.get("logos") or []) + ["", ""]
        winner = (r.get("winner") or "").strip()
        nums = str(r.get("score") or "").split(":")
        sa, sb = (nums[0], nums[1]) if len(nums) == 2 else ("?", "?")
        ev = (r.get("tour") or "").strip()
        if ev and ev not in events:
            events.append(ev)
        out.append({
            "ts": ts, "time": dt.strftime("%H:%M"),
            "day": (dt.date() - start).days,
            "dateLabel": dt.strftime("%b %d").upper(),
            "teamA": {"name": teams[0], "short": shorts[0] or teams[0],
                      "logo": _html_logo(logos[0], es, budget),
                      "score": sa, "win": bool(winner) and winner == teams[0]},
            "teamB": {"name": teams[1], "short": shorts[1] or teams[1],
                      "logo": _html_logo(logos[1], es, budget),
                      "score": sb, "win": bool(winner) and winner == teams[1]},
            "event": ev, "bo": (r.get("bo") or "").strip().upper(),
            "status": "FINAL",
        })
    end = datetime.fromtimestamp(int(rs[-1].get("ts") or 0), CST)
    return {
        "generatedAt": (gen_at or now).strftime("%H:%M"),
        "dateLabel": start.strftime("%b %d").upper(),
        "endDateLabel": end.strftime("%b %d").upper(),
        "dayLabel": _day_label(now),
        "total": len(out),
        "events": events,
        "matches": out,
    }


def render_daily_results_card_html(rows, now, es, gen_at=None):
    """全天整合版 HTML 大图（1920×1080）。出不了返回 None（上层退 880px 旧卡）。"""
    try:
        if not os.path.isfile(DAILY_RESULTS_TEMPLATE_FILE):
            raise HtmlCardError("缺模板 %s" % DAILY_RESULTS_TEMPLATE_FILE)
        data = build_daily_results_data(rows, now, es, gen_at)
        if data is None:
            raise HtmlCardError("空场次或超过 %d 场，版式兜不住" % HTML_IMG_MAX_ROWS)
        with open(DAILY_RESULTS_TEMPLATE_FILE, encoding="utf-8") as f:
            tmpl = f.read()
        png = render_html_png(tmpl, data, es)
        log("[info] 整合版战果大图（HTML 1920×1080）：PNG %.1f KB"
            % (len(png) / 1024))
        return png
    except HtmlCardError as exc:
        log("[info] HTML 整合版战果出不了（%s）→ 退回 880px 旧卡" % exc)
    except Exception as exc:  # noqa: BLE001
        log("[warn] HTML 整合版战果构建出错（%s: %s）→ 退回 880px 旧卡"
            % (type(exc).__name__, exc))
    return None


def render_daily_results_card(rows, now, es, gen_at=None):
    """**全天整合版**战果卡片。优先 HTML 大图（1920×1080），出不了退 880px
    Pillow 旧卡，再不行返回 None，由上层退回纯文本。

    这条是三条流水线里**最后**补上 HTML 层的一条：在它之前，整合版只有
    Pillow 一档，所以用户看到的「昨天的总战果」一直是 880px 老卡。
    `now` / `gen_at` 的分工见 build_daily_results_data 的说明。

    ⚠️ Pillow 那档**没有**这个区分（它只按 `now` 写「10-05 周一」），所以退回旧卡时
    页脚不会出现「LAST UPDATED」这一栏 —— 两档的页脚本来就不一样，不是 bug。
    """
    if rows and (es or {}).get("card_html_enabled", True):
        png = render_daily_results_card_html(rows, now, es, gen_at)
        if png:
            return png
    return render_results_card(rows, now, es)


def build_preview_data(item, est_ts, now, es):
    """单场开赛提醒卡（Match Preview）的数据对象，喂 preview_template.html。

    字段与模板 JS 逐一对应（teamA/teamB 用 {name, short, logo}）。
    **不伪造数据**：赛前地图池未定 → mapPool 空（模板显示 MAPS TBA）；
    没有赛前阵容数据源 → lineupA/B 空（模板整组隐藏）；notes 同理。
    startsIn 按**渲染时刻**真实计算 —— 每分钟 timer 拉起时正好是 est 前 LEAD 分钟。
    """
    es = es or {}
    teams = list(item.get("teams") or [])
    while len(teams) < 2:
        teams.append("?")
    shorts = list(item.get("shorts") or [])
    while len(shorts) < 2:
        shorts.append("")
    logos = list(item.get("logos") or [])
    while len(logos) < 2:
        logos.append("")

    def side(i):
        name = teams[i] or "?"
        return {"name": name,
                "short": (shorts[i] or name)[:12],
                "logo": logos[i] or ""}

    tour = (item.get("tour") or "").strip()
    ev, _, stage = tour.partition(" - ")
    dt = datetime.fromtimestamp(est_ts, CST)
    lead = max(0, int(est_ts - now.timestamp()))
    h, rem = divmod(lead, 3600)
    mnt, sec = divmod(rem, 60)
    return {
        "event": ev.strip() or "CS2 MATCH",
        "stage": stage.strip().upper(),
        "status": "UPCOMING",
        "dateFull": dt.strftime("%b %d · %Y").upper(),
        "time": dt.strftime("%H:%M"),
        "tz": "UTC+8",
        "startsIn": "%02d:%02d:%02d" % (h, mnt, sec) if lead > 0 else "",
        "bo": (item.get("bo") or "").strip().upper(),
        "teamA": side(0),
        "teamB": side(1),
        "mapPool": [],
        "lineupA": [],
        "lineupB": [],
        "notes": [],
        "dataSource": "LIQUIPEDIA",
        "generatedAt": now.strftime("%H:%M") + " UTC+8",
    }


def render_preview_card_html(item, est_ts, now, es):
    """开赛提醒 HTML 大图（1920×1080）。出不了返回 None（上层退纯文本）。"""
    try:
        if not os.path.isfile(PREVIEW_TEMPLATE_FILE):
            raise HtmlCardError("缺模板 %s" % PREVIEW_TEMPLATE_FILE)
        with open(PREVIEW_TEMPLATE_FILE, encoding="utf-8") as f:
            tmpl = f.read()
        data = build_preview_data(item, est_ts, now, es)
        png = render_html_png(tmpl, data, es)
        log("[info] 开赛提醒大图（HTML 1920×1080）：PNG %.1f KB"
            % (len(png) / 1024))
        return png
    except HtmlCardError as exc:
        log("[info] HTML 开赛提醒出不了（%s）→ 退回纯文本" % exc)
    except Exception as exc:  # noqa: BLE001
        log("[warn] HTML 开赛提醒构建出错（%s: %s）→ 退回纯文本"
            % (type(exc).__name__, exc))
    return None


def render_card(picked, now, es, rank_names=None):
    """把赛程画成一张 PNG 返回 bytes。**任何一项前置条件不满足就返回 None。**

    优先出 2026-10-05 用户拍板的 HTML 大图（1920×1080）；出不了退回 880px
    Pillow 旧卡。为什么是「返回 None」而不是抛异常：图片只是锦上添花，
    没图也必须把赛程发出去。所以每条 return None 都对应一种**安静降级**：
      没装 Chromium&Pillow / 没字体文件 / 有画不出来的字 / 画的途中出任何错
    → 上层拿不到 bytes 就改发纯文本（format_daily），功能不会因此消失。
    """
    if picked and (es or {}).get("card_html_enabled", True):
        png = render_card_html(picked, now, es)
        if png:
            return png
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
    texts = ["CS2 赛程", sub, tour, "数据来源：Liquipedia", "次日", "…", "?"]
    for m in rows:
        texts += list(m.get("teams") or [])
        # ⚠️ 短名也要一起核 —— 队标拿不到时它就是画在占位块里的那行字。
        #    漏核的后果是「有字不在子集里 → 画出豆腐块」，正是这个检查要防的事。
        texts += list(m.get("shorts") or [])
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
        shorts = list(m.get("shorts") or [])
        for side, name in enumerate((m.get("teams") or [])[:2]):
            room = side_w - CARD_LOGO - 14
            nm, f = _card_fit(d, name, (27, 25, 23, 21, 20), max(60, room))
            # 占位块里写的短名：没有就退回长名（_card_placeholder 会自己缩字号）
            code = shorts[side] if side < len(shorts) and shorts[side] else name
            if side == 0:
                _card_logo(img, d, logos[i][0], left_x1 - CARD_LOGO, cy, code)
                x = left_x1 - CARD_LOGO - 14
                d.text((int(x - d.textlength(nm, font=f)), base), nm, font=f,
                       fill=C_INK, anchor="ls")
            else:
                _card_logo(img, d, logos[i][1], right_x0, cy, code)
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


# ==========================================================================
# 战果公布
#
# 数据来自**同一个页面**（Liquipedia:Matches 的 type=recent 区块），所以
# 除了「什么时候去抓」之外，没有任何新增的抓取复杂度。
# 这里只回答两个问题：① 现在该不该去抓 ② 抓到之后发什么。
# ==========================================================================

def _teams_key(teams):
    """两支队名的规范键。**排序**后拼接 —— 页面里左右顺序可能和预告不一致。"""
    return "|".join(sorted((t or "").strip().lower() for t in (teams or [])))


def _pairs_label(items, limit=4):
    """日志里把待结算的几个对阵列出来，够看就行。"""
    out = []
    for it in (items or [])[:limit]:
        t = list(it.get("teams") or [])
        while len(t) < 2:
            t.append("?")
        out.append("%s vs %s" % (t[0], t[1]))
    if len(items or []) > limit:
        out.append("…")
    return "、".join(out)


def finished_index(matches):
    """把已结束的场次做成 {队名键: [比赛, …]}。

    ⚠️ 值是**列表**不是单场：同一对队伍在几天内可能打两遍
    （实测 `FlyQuest vs Ground Zero Gaming` 在 10-03 打了两次）。
    只留一场的话，后一场会被前一场顶掉，配对就错了。
    真正取哪一场由 result_rows 按「时间最近」决定。
    """
    out = {}
    for m in matches or []:
        if not m.get("finished"):
            continue
        key = _teams_key(m.get("teams"))
        if key:
            out.setdefault(key, []).append(m)
    for v in out.values():
        v.sort(key=lambda x: x["ts"])
    return out


# 预告登记的开赛时间与页面上那场的时间差超过这个值，就不认它是同一场。
# 取 6 小时：够容忍小幅改期，又不会把「几天前打过的同一对队伍」错认成今天这场。
RESULTS_TS_TOLERANCE = 6 * 3600

# `--check-results` 演练模式下演示几场。取 8：和「一期预告通常 8 场」一样长，
# 卡片高度也就和预告卡片一样（880×806），版式好不好看最容易判断。
DRILL_ROWS = 8


def drill_items(matches, limit=DRILL_ROWS):
    """把页面上「最近打完的 limit 场」做成假的待结算条目，给 `--check-results` 用。

    **只读、不落盘**：返回的是临时对象，既不来自也不写回 state_results_pending.json。
    存在的理由：刚部署的服务器上清单是空的，而演练命令正是部署验收要用的东西 ——
    不这么做，`--check-results` 永远只会回一句「清单里没有到结算时刻的场次」。
    """
    got = [m for m in (matches or []) if m.get("finished")]
    got.sort(key=lambda x: int(x.get("ts") or 0))
    if limit and limit > 0:
        got = got[-limit:]
    return [{
        "teams": list(m.get("teams") or []),
        "ts": int(m.get("ts") or 0),
        "bo": m.get("bo") or "",
        "status": "pending",
        "noted_at": "-",
        "reported_at": None,
    } for m in got]


def _settle_seconds(bo, es):
    """返回 (最短等待, 放弃等待) 秒数。按赛制取，取不到就退回 Bo3。"""
    es = es or {}
    bo = (bo or "").strip()
    for table, fallback in (("results_grace_minutes", 100),
                            ("results_timeout_minutes", 180)):
        vals = es.get(table) or {}
        if not isinstance(vals, dict):
            vals = {}
        try:
            n = int(vals.get(bo) or vals.get("Bo3") or fallback)
        except (TypeError, ValueError):
            n = fallback
        if table == "results_grace_minutes":
            grace = max(0, n)
        else:
            timeout = max(0, n)
    return grace * 60, timeout * 60


def load_results_pending(path=RESULTS_STATE_FILE):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"items": []}


def save_results_pending(data, path=RESULTS_STATE_FILE):
    """原子写（tmp + rename）—— 预告任务和结算任务是两个 timer，可能同时跑。

    ⚠️ **原子写 ≠ 并发安全**：它只保证文件不会被写坏，不保证不丢更新。
    单条「读—改—写」要整体放进 `update_pending`（= 一把跨进程锁里）。
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def update_pending(apply_fn, path=RESULTS_STATE_FILE):
    """在**排他锁**里做一次「读 → `apply_fn(data)` → 写」。返回写回的数据。

    `apply_fn` 的约定：
      · **只做内存操作** —— 绝不能联网/发消息，否则锁会被持有多久就堵别人多久；
      · 返回 dict → 写回它；返回 `None`/`False` → **无变化、不写盘**（少一次无谓 IO）。

    为什么必须「锁内重读」：调用方手上的 data 可能是几分钟前读的（结算一轮要发消息、
    抓逐图、抓选手数据）。这中间 `douyu-esports` 可能刚登记了新场次；拿旧快照整体写回
    会把那些新场次**抹掉**。锁内重读 + 调用方按身份键合并（见 `_absorb_rows`）才是对的。
    """
    with file_lock(path):
        data = load_results_pending(path)
        out = apply_fn(data)
        if out is None or out is False:
            return data
        if out is True:
            out = data
        save_results_pending(out, path)
        return out


def _item_ident(it):
    """清单条目的身份键：队名规范键 + 登记 ts。

    和 `note_pending` 的第一条去重判据**同源** —— 读日志/查清单时它们是同一个「这一场」。
    """
    return (_teams_key(it.get("teams")), int(it.get("ts") or 0))


def _absorb_rows(data, rows, stamp):
    """把已经**送达**的 rows 按身份键合并进 data 并记账。就地改 data。"""
    idx = {_item_ident(it): it for it in data["items"]}
    for row in rows:
        src = row.get("item") or {}
        k = _item_ident(src)
        it = idx.get(k)
        if it is None:
            # 磁盘上没这条（理论上不该发生：它就是我们登记的那条）。
            # 不丢战果 —— 补一条进去。
            it = dict(src)
            data["items"].append(it)
            idx[k] = it
        absorb_result(it, row, stamp)
    return data


def note_pending(picked, now, es, path=RESULTS_STATE_FILE):
    """预告发出后登记「这些场次要结算」。**只增不改**。

    为什么只增不改：
      · 已经 reported 的场次不能被重新打开，否则改期/二次预告会让战果重发一遍；
      · 已经登记过的场次不刷新 ts —— 结算窗口锚定在**第一次预告**报出的开赛时间上，
        这样「打了多久还没结果」是可解释的。
    拿不到的场次（延期/取消）由结算任务的 timeout 兜住，不会被永远挂着。

    ---- 去重判据（2026-10-06 修正）----
    ⚠️ **不能只用队名当键**。原来的键是「排序后的队名」，而 `_prune_pending` 会把
       `reported`/`abandoned` 的条目**留 36 小时**才清 —— 于是同一对队伍在这个窗口内
       再打一场（EPL 这种瑞士轮 + 淘汰赛连着打的赛制很常见），第二场会被 `key in have`
       直接吞掉：不进待结算、拿不到单场战报、也进不了整合版。

    现在用两条判据，一起满足才算「同一场」：
      1. `(队名键, 赛事, 登记 ts)` 完全一致 → 同一场被重复预告，跳过；
      2. 已存在**同队名的 pending 条目** → 视为同一场（能兜住赛事名写法变化、
         ts 小幅漂移这类情况），跳过并打 `[warn]` 留痕。
    前一场**已经结算**（reported/abandoned）之后，同一对队伍再出现就当作**新的一场**，
    正常登记 —— 这一半正是原来缺的。

    ⚠️ 整个「读 → 判断 → 追加」都在一把**跨进程锁**里（`update_pending`）。
       这条路径和结算进程/补漏进程会同时改同一个文件，锁外做的话，
       一方刚登记的场次会被另一方的旧快照整体写回时抹掉。
    """
    added = {"n": 0}

    def _apply(data):
        have_key = {(_teams_key(it.get("teams")),
                     (it.get("tour") or "").strip(),
                     int(it.get("ts") or 0)) for it in data["items"]}
        open_pairs = {_teams_key(it.get("teams")) for it in data["items"]
                      if it.get("status") == "pending"}
        for m in picked or []:
            tk = _teams_key(m.get("teams"))
            if not tk:
                continue
            k3 = (tk, (m.get("tour") or "").strip(), int(m.get("ts") or 0))
            if k3 in have_key:
                continue
            if tk in open_pairs:
                log("[warn] %s 已有未结算的条目（同一场重复预告，或赛事名/时间有漂移），"
                    "本轮不重复登记" % _pairs_label([m]))
                continue
            data["items"].append({
                "teams": list(m.get("teams") or []),
                "ts": int(m.get("ts") or 0),
                "bo": m.get("bo") or "",
                "status": "pending",
                "noted_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "reported_at": None,
                # 开赛提醒（--announce）渲染 Match Preview 卡要用的快照：
                # 登记时页面手上就有，不存的话提醒卡只能画灰底占位块。
                "shorts": list(m.get("shorts") or []),
                "logos": list(m.get("logos") or []),
                "tour": m.get("tour") or "",
            })
            have_key.add(k3)
            open_pairs.add(tk)
            added["n"] += 1
        # 没加就不写盘 —— 保持「只增不改」的原语义（不刷新已有条目的 ts）。
        return _prune_pending(data, now, es) if added["n"] else None

    update_pending(_apply, path)
    return added["n"]


def _prune_pending(data, now, es):
    """丢掉「已经过了太久」的条目，免得文件无限长大。"""
    try:
        max_age = max(1, int((es or {}).get("results_max_age_hours") or 24)) * 3600
    except (TypeError, ValueError):
        max_age = 24 * 3600
    cutoff = now.timestamp() - max_age
    data["items"] = [it for it in data["items"]
                     if it.get("status") == "pending" or int(it.get("ts") or 0) >= cutoff]
    return data


def split_due(items, now, es):
    """把待结算条目分成 (到点了可以查, 超过上限该放弃)。

    到点的定义：`ts + grace <= now <= ts + timeout`。
    **grace 是最关键的那个安全阀** —— 开赛 100 分钟（Bo3）之内，就算页面
    已经把某队标成 winner，我们也不会发，因为那时比赛多半还在打。
    """
    due, expired = [], []
    t = now.timestamp()
    for it in items or []:
        if it.get("status") != "pending":
            continue
        grace, timeout = _settle_seconds(it.get("bo"), es)
        ts = int(it.get("ts") or 0)
        if t > ts + timeout:
            expired.append(it)
        elif t >= ts + grace:
            due.append(it)
    return due, expired


def _bo_clinch(bo):
    """「赢下这个系列赛要拿几图」。认不出的赛制返回 None（= 不做任何胜负条件校验）。"""
    b = (bo or "").strip().upper()
    if b == "BO1":
        return 1
    if b == "BO3":
        return 2
    if b == "BO5":
        return 3
    return None


def series_partial(bo, left_score, right_score):
    """比分是否**不可能**是这个赛制的最终比分（= 系列赛还没打完）。

    只有一种情况返回 True：赛制认得出、且**双方都没拿到赢下系列赛所需的图数**。
    Bo3 的 `1:0`/`0:1`、Bo5 的 `1:0`/`2:1`/`2:0` 这些，数学上都不可能是终局。

    Bo1 永远返回 False —— `1:0` 本来就是一个完整的 Bo1 结果，
    而且 Bo1 的比分栏给的是**地图比分**（13:4），一律不该拦。
    赛制认不出（`bo` 空串/写错）也返回 False：**宁可发，也别拿一个不确定的判据
    把合法战果永远挡住**（2026-10-05 那两场就是被门闸挡丢的，教训在文件顶上）。
    """
    need = _bo_clinch(bo)
    if need is None or need <= 1:
        return False
    try:
        hi = max(int(left_score), int(right_score))
    except (TypeError, ValueError):
        return False
    return hi < need


def series_overshoot(bo, left_score, right_score):
    """比分是否**超过**了这个赛制的上限（Bo3 出现 `3:0` 之类）。**只留痕、不拦。**

    为什么不拦：数字超过上限，更可能是**页面把赛制标错了**（其实是 Bo5），
    而不是比分错了 —— 拿它当闸门会把真战果挡掉。Bo1 跳过：它的比分栏是地图比分。
    """
    need = _bo_clinch(bo)
    if need is None or need <= 1:
        return False
    try:
        hi = max(int(left_score), int(right_score))
    except (TypeError, ValueError):
        return False
    return hi > need


def result_rows(due, fin, es, strict=True):
    """把「待结算条目」和「页面上的已结束场次」对上，返回可发送的行 + 还没出结果的条目。

    `strict=True`（结算流水线）：比分配不上赛制时**拦下来**留在待结算。
    `strict=False`（整合版发之前的补漏）：**不拦** —— 那是最后一道兜底，
    宁可把可疑的终局带上，也不能让一整场从汇总里消失。
    """
    rows, waiting = [], []
    for it in due:
        ts0 = int(it.get("ts") or 0)
        cands = fin.get(_teams_key(it.get("teams"))) or []
        m = None
        if cands:
            best = min(cands, key=lambda x: abs(int(x["ts"]) - ts0))
            if abs(int(best["ts"]) - ts0) <= RESULTS_TS_TOLERANCE:
                m = best
            else:
                log("[warn] %s 找到同名对阵但时间差 %.1f 小时，不认（多半还没打完）"
                    % (_pairs_label([it]), abs(int(best["ts"]) - ts0) / 3600.0))
        if m is None:
            waiting.append(it)
            continue
        nums = (m.get("score") or "").split(":")
        if len(nums) != 2 or not all(x.isdigit() for x in nums):
            waiting.append(it)
            continue
        left, right = m["teams"][0], m["teams"][1]
        sides = list(m.get("sides") or [])
        # 胜方以**比分**为准（两个信号实测一致，万一不一致以数值为准并留痕）
        winner = left if int(nums[0]) > int(nums[1]) else right
        if sorted(sides) == ["L", "W"]:
            by_flag = left if sides[0] == "W" else right
            if by_flag != winner:
                log("[warn] %s vs %s 的胜负标记与比分不一致（标记=%s 比分=%s），以比分栏为准"
                    % (left, right, by_flag, m.get("score")))
        if int(nums[0]) == int(nums[1]):
            log("[warn] %s vs %s 比分是平的（%s），跳过" % (left, right, m.get("score")))
            waiting.append(it)
            continue

        # ---- 校验「这个比分配得上这个赛制吗」（2026-10-06 加）----
        # 「已结束」的三个信号（data-finished / 胜负标记 / 两个数字）只说明**页面认为打完了**，
        # 没有一个在管「赢下系列赛了吗」。Bo3 打到 1:0、Bo5 打到 2:1 的时候页面一旦抢跑，
        # 就会把**进行中的比分**当战果发出去 —— 消息发出去就收不回来了，比漏发严重得多。
        bo_txt = m.get("bo") or it.get("bo") or ""
        if strict and series_partial(bo_txt, nums[0], nums[1]):
            log("[warn] %s vs %s 页面标了「已结束」，但 %s 的比分是 %s —— "
                "谁都还没赢下系列赛，判定为**还没打完**，留在待结算（下一轮再看）"
                % (left, right, bo_txt or "未知赛制", m.get("score")))
            waiting.append(it)
            continue
        if series_overshoot(bo_txt, nums[0], nums[1]):
            log("[warn] %s vs %s 的比分 %s 超过了 %s 的上限"
                "（多半是页面把赛制标错了，不是比分错），仍按页面发，记一笔"
                % (left, right, m.get("score"), bo_txt))

        if abs(int(m["ts"]) - int(it.get("ts") or 0)) > 3 * 3600:
            log("[warn] %s vs %s 的时间对不上（登记 %s / 页面 %s），按队名照发"
                % (left, right,
                   datetime.fromtimestamp(int(it.get("ts") or 0), CST).strftime("%m-%d %H:%M"),
                   datetime.fromtimestamp(int(m["ts"]), CST).strftime("%m-%d %H:%M")))
        rows.append({
            "item": it, "ts": int(m["ts"]),
            "teams": [left, right],
            "shorts": list(m.get("shorts") or []),
            "logos": list(m.get("logos") or []),
            "score": m.get("score") or "",
            "score_left": nums[0], "score_right": nums[1],
            "score_source": ("map" if (m.get("bo") or "") == "Bo1" else "series"),
            "winner": winner, "bo": m.get("bo") or it.get("bo") or "",
            "tour": m.get("tour") or "",
            # 赛事页路径：拿逐图比分要用（见 attach_maps）。空串 = 拿不到。
            "tour_page": m.get("tour_page") or "",
            # 逐图比分，由 attach_maps 填。默认空 = **只用系列比分出图**，
            # 这是「抓不到逐图」时的正常状态，不是异常。
            "maps": [],
        })
    rows.sort(key=lambda x: x["ts"])
    return rows, waiting


def absorb_result(it, row, stamp):
    """把一条结算结果**写进清单条目**，返回该条目。

    ⚠️ 下面这几个快照字段不是冗余：整合版要到**次日早上**才发，那时赛程页早翻篇了
    （recent 只有约 2 天窗口），队名/短名/队标/赛事名全都不在手上 —— 只能结算时存下来。

    抽成独立函数是因为**两条路径**都要写同样的字段：
      · `run_results` 的正常结算；
      · `backfill_abandoned` 的补漏（次日早上把被判延期/取消的场次捞回来）。
    """
    it["status"] = "reported"
    it["reported_at"] = stamp
    it["score"] = row["score"]
    it["winner"] = row["winner"]
    it["teams"] = list(row["teams"])
    it["shorts"] = list(row["shorts"])
    it["logos"] = list(row["logos"])
    it["tour"] = row["tour"]
    it["bo"] = row["bo"]
    return it


def attach_maps(rows, es):
    """给战果行补上逐图比分。返回 (抓了几个赛事页, 补上了几行)。

    设计要点：
      · **按赛事页去重**：同一轮里同赛事的多场只抓一次（cache 字典传进 fetch_event_page）。
      · **只给「这一轮真的要发」的行去抓** —— 不是给清单里所有场次抓。
      · 任何一个环节失败都只是少几行地图，**绝不**影响发送（所有异常都在
        fetch_event_page 里消化掉了，这里不做任何判断）。
      · 需要额外请求，所以由 `card_results_maps_enabled` 控制开关。
    """
    es = es or {}
    if not rows or not es.get("card_results_maps_enabled", True):
        return 0, 0

    pages = []
    for r in rows:
        p = (r.get("tour_page") or "").strip()
        if p and p not in pages:
            pages.append(p)
    if not pages:
        return 0, 0

    cache, idx = {}, {}
    fetched = 0
    for p in pages:
        html_text = fetch_event_page(es, p, cache)
        if html_text is None:
            continue
        fetched += 1
        idx.update(event_maps_index(parse_event_maps(html_text)))

    hit = 0
    for r in rows:
        maps = idx.get((int(r["ts"]), _teams_key(r.get("teams")))) or []
        if maps:
            r["maps"] = maps
            hit += 1
    return fetched, hit


# ==========================================================================
# 选手数据（csdb.gg）
# ==========================================================================
# ⚠️ 2026-10-09：**本段整体删除**。原实现是 fetch_csdb / parse_csdb_matches /
#    locate_csdb_match / parse_csdb_players / attach_players 五个函数，负责从
#    csdb.gg 抓逐图选手数据（K/D/ADR/KAST/Rating）。csdb.gg 改成 Next.js 客户端
#    渲染后这条链路已失效（详见文件顶部「选手数据」注释块的实测记录），抓取层
#    整体拆掉、不再有任何网络请求。
#
# 保留的是**展示层**（与抓取无关，纯离线）：
#   · `_aggregate_players()` —— 把「逐图选手原始结构」聚成整场行（模板要的形状）
#   · `build_result_match()` 里的 `players` / `mvp` / `mvp_basis` 字段
#   · `result_template.html` 的选段渲染 + `_draw_player_block()` 的 Pillow 版式
# 将来接上新数据源时，只要按 `_aggregate_players()` docstring 里的结构把数据填进
# `row["players"]`，展示层无需改动。


def format_result_caption(row, now):
    """单场战报上面那一行（有图也要有字，通知栏才不是「[图片]」）。

    刻意用**比赛时间**而不是 `now`：跨午夜结算时「现在」已经是次日凌晨，
    用 now 会和战报上的日期对不上（战果按时段批量发时就是这么写的，
    现在一场一条了，就该以这一场为准）。
    """
    d = datetime.fromtimestamp(int(row.get("ts") or 0), CST)
    t = list(row.get("teams") or [])
    while len(t) < 2:
        t.append("")
    return "【CS2 战报】%s %s %s  %s:%s  %s" % (
        _day_label(d), d.strftime("%H:%M"), t[0],
        row.get("score_left") or "?", row.get("score_right") or "?", t[1])


def format_result(row, now):
    """单场战报的**纯文本兜底**（出不了图时发这个，信息不比图少）。

    逐图那几行带上，是因为这正是「单场」相对「全天整合版」的价值所在：
    整合版只给系列比分，单场给到每张图。
    """
    d = datetime.fromtimestamp(int(row.get("ts") or 0), CST)
    t = list(row.get("teams") or [])
    while len(t) < 2:
        t.append("")
    lines = ["【CS2 战报】%s %s" % (_day_label(d), d.strftime("%H:%M")),
             "%s  %s:%s  %s" % (t[0], row.get("score_left") or "?",
                                row.get("score_right") or "?", t[1])]
    for i, m in enumerate(list(row.get("maps") or [])[:5]):
        rounds = list(m.get("rounds") or [])
        while len(rounds) < 2:
            rounds.append("?")
        lines.append("· 地图 %d  %s  %s:%s" % (i + 1, m.get("map") or "?",
                                              rounds[0], rounds[1]))
    bo = (row.get("bo") or "").strip()
    tour = (row.get("tour") or "").strip()
    if tour or bo:
        lines.append(("%s · %s" % (tour, bo)) if (tour and bo) else (tour or bo))
    lines.append("数据来源：Liquipedia")
    return "\n".join(lines)


def format_results_caption(rows, now):
    """全天整合版上面那一行（和预告一样：有图也要有字）。"""
    return "【CS2 战果】%s · 共 %d 场" % (_day_label(now), len(rows))


def format_results(rows, now):
    """纯文本版战果（**图片发不出来时的兜底**）。末尾必须署名 Liquipedia。

    这版刻意不分组、每行都写全 `MM-DD HH:MM`：战果通常横跨午夜
    （22:00 那批和次日 00:30 那批常常一起结算），只写 `HH:MM` 会分不清是哪天。
    """
    if not rows:
        return ""
    lines = ["【CS2 战果】%s · 共 %d 场" % (_day_label(now), len(rows)), ""]
    for r in rows:
        d = datetime.fromtimestamp(r["ts"], CST)
        lines.append("%s  %s %s:%s %s" % (
            d.strftime("%m-%d %H:%M"), r["teams"][0],
            r["score_left"], r["score_right"], r["teams"][1]))
    lines.append("")
    lines.append("共 %d 场 · 数据来源：Liquipedia" % len(rows))
    return "\n".join(lines)


def render_results_card(rows, now, es):
    """战果卡片。前置条件不满足一律返回 None，由上层退回纯文本。"""
    if Image is None:
        log("[info] 没装 Pillow，跳过战果卡片（本次发纯文本）")
        return None
    if not os.path.isfile(CARD_FONT_FILE):
        log("[warn] 找不到字体 %s，跳过战果卡片（本次发纯文本）" % CARD_FONT_FILE)
        return None
    if not rows:
        return None
    try:
        return _render_results_card_inner(rows, now, es)
    except Exception as exc:  # noqa: BLE001
        log("[warn] 画战果卡片出错（%s: %s），本次改发纯文本" % (type(exc).__name__, exc))
        return None


def _render_results_card_inner(rows, now, es):
    es = es or {}
    # ⚠️ 这里是 **880px Pillow 旧卡**（`render_results_card`），不是那张 1920×1080 的
    #    HTML 大图 —— 画布尺寸不同，容量自然不同：这张画得下 12 行，
    #    大图画得下 `HTML_IMG_MAX_ROWS`（21）行。所以**不是**同一个数，
    #    别为了「看起来一致」把两个常量硬合成一个。
    #    这条路径只在「HTML 出不了图」时走，超过 12 行会在页脚明说「图里只列前 N 场」。
    max_rows = max(1, int(es.get("card_max_rows") or 12))
    shown = rows[:max_rows]
    count = "共 %d 场" % len(rows)
    if len(shown) < len(rows):
        count += "（图里只列前 %d 场）" % len(shown)
    sub = "%s　%s" % (_day_label(now), count)

    texts = ["CS2 战果", sub, "数据来源：Liquipedia", "…", "?"]
    for r in shown:
        texts += list(r.get("teams") or [])
        texts += list(r.get("shorts") or [])
        texts.append(r.get("score") or "")
        texts.append(r.get("tour") or "")
    miss = card_missing_chars(texts)
    if miss:
        log("[warn] 字体子集里没有这些字：%s，本次改发纯文本" % "".join(sorted(miss))[:60])
        log("       要出图就往 deploy/make_card_font.py 的 UI_CHARS 补字并重跑它。")
        return None

    n = len(shown)
    height = CARD_HDR_H + CARD_ROW_H * n + CARD_FTR_H
    if height > 2400:
        log("[warn] 战果卡片会高达 %d px（%d 场），改用纯文本" % (height, n))
        return None

    img = Image.new("RGB", (CARD_W, height), C_BG)
    d = ImageDraw.Draw(img)
    f_title = load_card_font(34)
    f_sub = load_card_font(22)
    f_date = load_card_font(19)
    f_time = load_card_font(24)
    f_name = load_card_font(27)
    f_ft = load_card_font(19)

    # ---- 页头 ----
    d.text((CARD_PAD, 24), "CS2 战果", font=f_title, fill=C_INK,
           stroke_width=1, stroke_fill=C_INK)
    d.text((CARD_PAD, 70), sub, font=f_sub, fill=C_MUTED)
    d.line([(CARD_PAD, CARD_HDR_H - 1), (CARD_W - CARD_PAD, CARD_HDR_H - 1)],
           fill=C_LINE, width=1)

    # ---- 列位置（和预告卡片同一套推导，只把中间那列让给比分）----
    side_w = (CARD_W - 2 * CARD_PAD - CARD_TIME_W - CARD_VS_W) // 2
    left_x1 = CARD_PAD + CARD_TIME_W + side_w
    right_x0 = left_x1 + CARD_VS_W
    score_cx = left_x1 + CARD_VS_W // 2
    time_right = CARD_PAD + CARD_TIME_W - 14

    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    logos = []
    for r in shown:
        urls = list(r.get("logos") or [])
        while len(urls) < 2:
            urls.append("")
        logos.append([ensure_logo(u, es, budget) for u in urls[:2]])

    for i, r in enumerate(shown):
        top = CARD_HDR_H + CARD_ROW_H * i
        cy = top + CARD_ROW_H // 2
        base = cy + 9
        if i:
            d.line([(CARD_PAD, top), (CARD_W - CARD_PAD, top)], fill=C_ROWLINE, width=1)

        # 时间列：`10-05 22:00` —— 战果横跨午夜，必须带上日期
        dt = datetime.fromtimestamp(r["ts"], CST)
        segs = [(dt.strftime("%m-%d"), f_date, C_FAINT),
                (dt.strftime("%H:%M"), f_time, C_MUTED)]
        x = time_right - sum(d.textlength(s, font=f) for s, f, _ in segs) - 6
        for s, f, col in segs:
            d.text((int(x), base), s, font=f, fill=col, anchor="ls")
            x += d.textlength(s, font=f) + 6

        # 比分居中。13:4 / 16:12 比 2:0 宽，所以也走一次字号降级
        st, sf = _card_fit(d, r["score"], (26, 24, 22, 20), CARD_VS_W - 6)
        d.text((score_cx, base), st, font=sf, fill=C_INK, anchor="ms")

        # 两侧队伍：**胜方绿、负方红**（用户 2026-10-05 明确要求）。
        # 胜方额外加 stroke_width=1 —— 红绿对色盲不友好，留一个不依赖颜色的信号。
        shorts = list(r.get("shorts") or [])
        for side, name in enumerate((r.get("teams") or [])[:2]):
            won = (name == r.get("winner"))
            room = side_w - CARD_LOGO - 14
            nm, f = _card_fit(d, name, (27, 25, 23, 21, 20), max(60, room))
            code = shorts[side] if side < len(shorts) and shorts[side] else name
            col = C_WIN if won else C_LOSE
            kw = {"stroke_width": 1, "stroke_fill": col} if won else {}
            if side == 0:
                _card_logo(img, d, logos[i][0], left_x1 - CARD_LOGO, cy, code)
                x = left_x1 - CARD_LOGO - 14
                d.text((int(x - d.textlength(nm, font=f)), base), nm, font=f,
                       fill=col, anchor="ls", **kw)
            else:
                _card_logo(img, d, logos[i][1], right_x0, cy, code)
                x = right_x0 + CARD_LOGO + 14
                d.text((int(x), base), nm, font=f, fill=col, anchor="ls", **kw)

    # ---- 页脚 ----
    fy = CARD_HDR_H + CARD_ROW_H * n
    d.line([(CARD_PAD, fy), (CARD_W - CARD_PAD, fy)], fill=C_LINE, width=1)
    fbase = fy + 46
    credit = "数据来源：Liquipedia"
    cw = d.textlength(credit, font=f_ft)
    d.text((CARD_W - CARD_PAD, fbase), credit, font=f_ft, fill=C_MUTED, anchor="rs")
    tours = []
    for r in shown:
        t = (r.get("tour") or "").strip()
        if t and t not in tours:
            tours.append(t)
    left_txt = tours[0] if len(tours) == 1 else ("%d 个赛事" % len(tours) if tours else "")
    if left_txt:
        lt, lf = _card_fit(d, left_txt, (19, 18, 17, 16),
                           max(40, (CARD_W - 2 * CARD_PAD) - cw - 24))
        if lt:
            d.text((CARD_PAD, fbase), lt, font=lf, fill=C_MUTED, anchor="ls")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log("[info] 战果卡片：%d×%d，PNG %.1f KB，%d 场"
        % (CARD_W, height, len(data) / 1024.0, n))
    return data


# --------------------------------------------------------------------------
# 单场战报（版式 A）
# --------------------------------------------------------------------------
# 用户 2026-10-05 晚选的版式：**每两队打完就发这一场**，一条消息配这一张图。
# 和「全天整合版」（上面那个，每场一行）是两个不同的东西：
#   · 单场战报：地图逐行列出（左比分 / 地图名 / 右比分），信息足；
#   · 全天整合版：只列系列比分，一行一场，一眼扫完全天。
#
#   ┌────────────────────────────────────────┐
#   │ CS2 战报                                │
#   │ ESL Pro League Season 24 - Round 1 · Bo3│
#   ├────────────────────────────────────────┤
#   │ [标] Legacy           1:2      PARIVISION [标] │
#   │ 地图 1          13   Dust II    5        │
#   │ 地图 2           2   Inferno   13        │
#   │ 地图 3          12   Ancient   16        │
#   ├────────────────────────────────────────┤
#   │ ESL Pro League Season 24 · 数据来源：Liquipedia│
#   └────────────────────────────────────────┘
#
# 逐图那几行是**可选**的：抓不到赛事页（或那个赛事页里没有这一场）就整段不画，
# 卡片变矮但照样发 —— 这是设计好的降级路径，不是异常。


def _round_win(rounds, side):
    """这一张地图是不是 `side`（0=左，1=右）那边赢的。

    拿不到数字一律返回 False：宁可两边都画成红，也不要在画图路径上抛异常
    （那会让整张卡片退化成纯文本，比颜色不对严重得多）。
    """
    try:
        a, b = int(rounds[0]), int(rounds[1])
    except (TypeError, ValueError, IndexError):
        return False
    return (a > b) if side == 0 else (b > a)


def render_result_card(row, now, es):
    """**单场战报**卡片。优先 HTML 大图（1920×1080），出不了退 880px 旧卡，
    再不行返回 None，由上层退回纯文本。"""
    if row and (es or {}).get("card_html_enabled", True):
        png = render_result_card_html(row, es)
        if png:
            return png
    if Image is None:
        log("[info] 没装 Pillow，跳过单场战报卡片（本次发纯文本）")
        return None
    if not os.path.isfile(CARD_FONT_FILE):
        log("[warn] 找不到字体 %s，跳过单场战报卡片（本次发纯文本）" % CARD_FONT_FILE)
        return None
    if not row:
        return None
    try:
        return _render_result_card_inner(row, now, es)
    except Exception as exc:  # noqa: BLE001
        log("[warn] 画单场战报卡片出错（%s: %s），本次改发纯文本"
            % (type(exc).__name__, exc))
        return None


def _render_result_card_inner(row, now, es):
    es = es or {}
    maps = list(row.get("maps") or [])[:5]
    players = list(row.get("players") or [])      # 与 maps 一一对应（长度可不同）
    tour = (row.get("tour") or "").strip()
    bo = (row.get("bo") or "").strip()
    sub = ("%s · %s" % (tour, bo)) if (tour and bo) else (tour or bo)
    foot = ("%s · 数据来源：Liquipedia" % tour) if tour else "数据来源：Liquipedia"
    winner = (row.get("winner") or "").strip()

    # 每队、每图列几名选手（按 rating 降序）。0/抓不到 = 不画选手段。
    per_team = max(0, int(es.get("card_players_per_team") or 3))

    texts = ["CS2 战报", sub, foot, row.get("score") or "", "…", "?", "选手", "评分"]
    texts += list(row.get("teams") or [])
    texts += list(row.get("shorts") or [])
    for i, m in enumerate(maps):
        texts.append("地图 %d" % (i + 1))
        texts.append(m.get("map") or "")
        texts += list(m.get("rounds") or [])
    for pm in players:
        for p in pm.get("players") or []:
            texts.append(p.get("name") or "")
    miss = card_missing_chars(texts)
    if miss:
        log("[warn] 字体子集里没有这些字：%s，本次改发纯文本" % "".join(sorted(miss))[:60])
        log("       要出图就往 deploy/make_card_font.py 的 UI_CHARS 补字并重跑它。")
        return None

    # ---- 动态高度：适配 BO1 / BO3 / BO5 任意图数 ----
    n = len(maps)
    has_players = bool(players) and per_team > 0
    n_play = min(len(players), n) if has_players else 0
    per_map_h = CARD_MAP_H
    if has_players:
        per_map_h += CARD_PLAYER_HEAD_H + CARD_PLAYER_ROW_H * per_team
    height = CARD_HDR_H + CARD_FIX_H + per_map_h * n + CARD_FTR_H
    if height > 2400:
        log("[warn] 单场战报卡片高达 %d px，改用纯文本" % height)
        return None

    img = Image.new("RGB", (CARD_W, height), C_BG)
    d = ImageDraw.Draw(img)
    f_title = load_card_font(34)

    # ---- 页头 ----
    d.text((CARD_PAD, 24), "CS2 战报", font=f_title, fill=C_INK,
           stroke_width=1, stroke_fill=C_INK)
    st, sf = _card_fit(d, sub, (22, 21, 20, 19, 18), CARD_W - 2 * CARD_PAD)
    if st:
        d.text((CARD_PAD, 70), st, font=sf, fill=C_MUTED)
    d.line([(CARD_PAD, CARD_HDR_H - 1), (CARD_W - CARD_PAD, CARD_HDR_H - 1)],
           fill=C_LINE, width=1)

    # ---- 对阵行：两个队标贴最外侧、队名朝内、大比分居中 ----
    cy = CARD_HDR_H + CARD_FIX_H // 2
    base = cy + 11
    half_w = CARD_W // 2 - 110
    teams = list(row.get("teams") or [])[:2]
    while len(teams) < 2:
        teams.append("")
    shorts = list(row.get("shorts") or [])
    urls = list(row.get("logos") or [])
    while len(urls) < 2:
        urls.append("")
    budget = [max(0, int(es.get("logo_max_new_per_run") or 0))]
    logos = [ensure_logo(u, es, budget) for u in urls[:2]]

    sc, scf = _card_fit(d, row.get("score") or "", (46, 40, 34, 28), 190)
    d.text((CARD_W // 2, base), sc, font=scf, fill=C_INK, anchor="ms")
    for side, nm in enumerate(teams):
        won = (nm == row.get("winner") and nm != "")
        t, f = _card_fit(d, nm, (30, 27, 24, 22, 20),
                         max(60, half_w - CARD_LOGO - 20))
        code = shorts[side] if side < len(shorts) and shorts[side] else nm
        col = C_WIN if won else C_LOSE
        # 胜者不再用「描边假粗体」（叠在 30px 上会显肥），改用**正常字重 + 队名下方
        # 一小段胜利色短线**来标识 —— 颜色之外还有位置/形状线索，色盲与灰度都认得出。
        if side == 0:
            _card_logo(img, d, logos[0], CARD_PAD, cy, code)
            d.text((CARD_PAD + CARD_LOGO + 18, base), t, font=f, fill=col, anchor="ls")
            if won:
                d.rounded_rectangle(
                    [CARD_PAD + CARD_LOGO + 18, base + 24,
                     CARD_PAD + CARD_LOGO + 18 + min(46, d.textlength(t, font=f)), base + 28],
                    radius=2, fill=col)
        else:
            _card_logo(img, d, logos[1], CARD_W - CARD_PAD - CARD_LOGO, cy, code)
            d.text((CARD_W - CARD_PAD - CARD_LOGO - 18, base), t, font=f, fill=col,
                   anchor="rs")
            if won:
                tw = d.textlength(t, font=f)
                d.rounded_rectangle(
                    [CARD_W - CARD_PAD - CARD_LOGO - 18 - min(46, tw), base + 24,
                     CARD_W - CARD_PAD - CARD_LOGO - 18, base + 28],
                    radius=2, fill=col)
    d.line([(CARD_PAD, CARD_HDR_H + CARD_FIX_H),
            (CARD_W - CARD_PAD, CARD_HDR_H + CARD_FIX_H)], fill=C_ROWLINE, width=1)

    # ---- 逐图行 + 选手段 ----
    y = CARD_HDR_H + CARD_FIX_H
    f_map = load_card_font(26)
    f_no = load_card_font(20)
    for i, m in enumerate(maps):
        block_top = y + per_map_h * i
        if i:
            d.line([(CARD_PAD + 40, block_top), (CARD_W - CARD_PAD - 40, block_top)],
                   fill=C_ROWLINE, width=1)
        # 比分行（在 block 顶部）
        bry = block_top + CARD_MAP_H // 2
        rounds = list(m.get("rounds") or [])
        while len(rounds) < 2:
            rounds.append("")
        d.text((CARD_W // 2 - 92, bry + 9), rounds[0], font=f_map,
               fill=C_WIN if _round_win(rounds, 0) else C_LOSE, anchor="rs")
        mt, mf = _card_fit(d, m.get("map") or "", (24, 22, 20), 300)
        d.text((CARD_W // 2, bry + 8), mt, font=mf, fill=C_MUTED, anchor="ms")
        d.text((CARD_W // 2 + 92, bry + 9), rounds[1], font=f_map,
               fill=C_WIN if _round_win(rounds, 1) else C_LOSE, anchor="ls")
        d.text((CARD_PAD, bry + 8), "地图 %d" % (i + 1), font=f_no,
               fill=C_FAINT, anchor="ls")

        # 选手段
        if has_players and i < len(players):
            _draw_player_block(d, players[i], teams, winner, per_team,
                               block_top + CARD_MAP_H, CARD_PAD, CARD_W)

    # ---- 页脚 ----
    fy = y + per_map_h * n
    d.line([(CARD_PAD, fy), (CARD_W - CARD_PAD, fy)], fill=C_LINE, width=1)
    ft, ff = _card_fit(d, foot, (19, 18, 17, 16), CARD_W - 2 * CARD_PAD)
    if ft:
        d.text((CARD_PAD, fy + 46), ft, font=ff, fill=C_FAINT, anchor="ls")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log("[info] 单场战报：%d×%d，PNG %.1f KB，%s %s:%s %s，%d 张地图%s"
        % (CARD_W, height, len(data) / 1024.0, teams[0],
           row.get("score_left") or "", row.get("score_right") or "",
           teams[1], n, "，含选手数据" if has_players else ""))
    return data


def _team_same(a, b):
    """两队名是否指同一队（双向包含、大小写不敏感）。

    选手数据的队名常以缩写出现（NAVI），而赛程页给的是全名（Natus Vincere）——
    精确等号会让选手段整列分不到人、卡片缺半边，所以用包含匹配兜住。
    这个函数是**展示层**的（给选手分列用），与数据源无关、无需改。
    """
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _draw_player_block(d, pmap, teams, winner, per_team, top, pad, w):
    """画一张图的选手段：左队 / 右队各一列，每列 rating 前 per_team 名。

    布局（每列从左到右）：选手名（左对齐）→ K-D → ADR → 评分色块（右端贴齐）。
    列位置用**正向固定偏移**而不是从右往左挤 —— 后者会让评分色块压住 K-D。
    胜负色跟队名一致（胜绿负红）；评分用同色圆角色块 + 白字，视觉权重最高。
    队名分组用 _team_same（双向包含），容忍全名 / 缩写混写。
    """
    players = list(pmap.get("players") or [])
    t0 = (teams[0] or "").strip()
    t1 = (teams[1] or "").strip()

    # 按队分组、按 rating 降序、各取前 per_team
    left = sorted([p for p in players if _team_same(p["team"], t0)],
                  key=lambda x: -x["rating"])[:per_team]
    right = sorted([p for p in players if _team_same(p["team"], t1)],
                   key=lambda x: -x["rating"])[:per_team]

    col_w = (w - 2 * pad - 40) // 2
    f_hd = load_card_font(16)
    f_nm = load_card_font(19)
    f_v = load_card_font(18)

    # 每列内部的相对偏移：名字 0；K-D 190；ADR 268；评分色块右端贴 col_w
    off_name, off_kd, off_adr = 0, 190, 268
    pill_right = col_w - 4          # 色块右端离列右缘留 4px

    def one_col(rows, col_x, team_name):
        col = C_WIN if team_name == winner else C_LOSE
        yy = top + 30          # 表头底 ~top+17，留出间隙，pill 顶 = top+21 不压表头
        for p in rows:
            d.text((col_x + off_name, yy), p["name"], font=f_nm, fill=col)
            d.text((col_x + off_kd, yy), "%d-%d" % (p["k"], p["d"]),
                   font=f_v, fill=C_INK)
            d.text((col_x + off_adr, yy), "%.1f" % p["adr"], font=f_v,
                   fill=C_MUTED)
            rt = "%.2f" % p["rating"]
            rw = d.textlength(rt, font=f_v) + 14
            px = col_x + pill_right - rw
            d.rounded_rectangle([px, yy - 9, px + rw, yy + 9],
                                radius=8, fill=col)
            d.text((px + rw / 2, yy), rt, font=f_v,
                   fill=(255, 255, 255), anchor="mm")
            yy += CARD_PLAYER_ROW_H

    # 表头（两列各一份，与数据列严格对齐）
    for cx in (pad, pad + col_w + 40):
        d.text((cx + off_name, top + 1), "选手", font=f_hd, fill=C_FAINT)
        d.text((cx + off_kd, top + 1), "K-D", font=f_hd, fill=C_FAINT)
        d.text((cx + off_adr, top + 1), "ADR", font=f_hd, fill=C_FAINT)
        ht = "评分"
        hw = d.textlength(ht, font=f_hd)
        d.text((cx + pill_right - hw, top + 1), ht, font=f_hd, fill=C_FAINT)

    one_col(left, pad, t0)
    one_col(right, pad + col_w + 40, t1)


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
        # 预告真的发出去了，才登记「这些场次要结算战果」。
        # 登记失败不该让整轮算失败 —— 顶多是这轮之后没有战果，预告本身已经送达。
        try:
            added = note_pending(picked, now, es)
            if added:
                log("[info] 已登记 %d 场待结算（打完之后由 douyu-esports-results.timer 发战果）"
                    % added)
        except OSError as exc:
            log("[warn] 待结算清单写不进去（%s），这轮之后不会发战果" % exc)
        return 0

    log("[error] 发送失败：%s" % ("、".join(failed) or "未知"))
    # 故意**不**写状态：写进去就等于「今天已经交代过了」，
    # 而消息其实没到任何人手上。
    return 1


def run_results(cfg, args):
    """结算一轮战果。

    **这个函数的设计目标只有一个：绝大多数被唤起的时候一次网络请求都不发。**
    timer 每 10 分钟叫一次，但只有「有场次刚进入结算窗口」时才真的去抓 ——
    条款允许 1 次 / 30 秒，我们要做的是不需要时就别发请求。

    顺序很重要：**先看清单，再决定要不要抓**，绝不能先抓再判断。
    """
    es = resolve_config(cfg)
    now = datetime.now(CST)
    check = bool(getattr(args, "check_results", False))

    if not es.get("results_enabled", True):
        log("[silent] 战果公布已关闭（results_enabled=false）")
        return 0

    data = load_results_pending()
    due, expired = split_due(data["items"], now, es)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    abandoned_ids = {_item_ident(it) for it in expired}

    def _persist(rows_to_absorb):
        """把这一轮的变化**在锁内**合并进清单（重读 + 按身份键合并）。

        为什么不直接把手上这份 data 整体写回：`data` 是几分钟前读的，
        这中间 `douyu-esports`（登记）或 `-daily`（补漏）可能刚改了同一个文件，
        整体写回会把它们的变化**抹掉**。锁内重读、只合并我们改过的那几条，才对。
        """
        def _apply(fresh):
            _absorb_rows(fresh, rows_to_absorb, stamp)
            for it in fresh["items"]:
                if _item_ident(it) in abandoned_ids:
                    it["status"] = "abandoned"
                    it["reported_at"] = stamp
            return _prune_pending(fresh, now, es)
        return update_pending(_apply)

    if expired:
        log("[info] %d 场超过结算上限仍没出结果，判定为延期/取消，不再等：%s"
            % (len(expired), _pairs_label(expired)))

    if not due:
        if not check:
            # ⚠️ 这一行就是「省请求」的地方：直接 return，连 fetch 都不调
            log("[silent] 清单里没有到结算时刻的场次，本轮不发任何请求（待结算 %d 场）"
                % len([it for it in data["items"] if it.get("status") == "pending"]))
            if expired:
                _persist([])
            return 0
        # --check-results 是给人看的演练命令，**清单空着也得给点东西看** ——
        # 否则刚部署的服务器上跑它永远只会得到「清单里没有…」，等于没法验收。
        log("[check] 清单里没有到结算时刻的场次（待结算 %d 场），改走演练模式："
            "结算页面上当前所有已结束的比赛（不发消息、不写状态）"
            % len([it for it in data["items"] if it.get("status") == "pending"]))
    else:
        log("[info] %d 场进入结算窗口：%s" % (len(due), _pairs_label(due)))

    r = fetch_matches(es)
    if r is None or not r.get("ok"):
        log("[warn] 结算抓取失败（%s），本轮不发消息，留到下一轮再试"
            % ((r or {}).get("error") or "未知"))
        return 1

    html_text, err = extract_html(r["text"])
    if html_text is None:
        log("[error] %s" % err)
        return 1

    allm = parse_matches(html_text)
    fin = finished_index(allm)
    if not due:
        # 演练模式：拿页面上「最近打完的一批」当待结算条目，走一遍完整配对 + 出图。
        due = drill_items(allm, DRILL_ROWS)
        log("[check] 演练：页面上共 %d 场已结束，取最近 %d 场演示结算后的样子"
            % (sum(len(v) for v in fin.values()), len(due)))
    rows, waiting = result_rows(due, fin, es)

    if not rows:
        log("[info] 到点的 %d 场都还没打完，留到下一轮（不写状态、不重复查已完成的）"
            % len(due))
        return 0

    # ---- 逐图比分：可选步骤，抓不到只是卡片少几行，绝不影响发送 ----
    if es.get("card_results_maps_enabled", True):
        got_pages, got_rows = attach_maps(rows, es)
        log("[info] 逐图比分：抓了 %d 个赛事页，%d/%d 场补上了逐图"
            % (got_pages, got_rows, len(rows)))
    else:
        log("[info] 逐图比分已关闭（card_results_maps_enabled=false），只发系列比分")

    # ---- 选手数据：**当前无可用数据源，抓取层已拆出（2026-10-09）** ----
    # 曾用 csdb.gg（已改成客户端渲染而失效）与 Liquipedia（不存选手数据），
    # 详见文件顶部「选手数据」注释块。展示层原地保留，等接上新源即可生效。
    log("[info] 选手数据：当前无可用数据源，战报不含选手段（展示层已保留）")

    log("[info] 本轮结算 %d 场（还有 %d 场在打），**一场一条消息**"
        % (len(rows), len(waiting)))
    for row in rows:
        log("        %s  %s  %s:%s  %s  %s"
            % (datetime.fromtimestamp(row["ts"], CST).strftime("%m-%d %H:%M"),
               row["teams"][0], row["score_left"], row["score_right"],
               row["teams"][1],
               ("%d 张地图" % len(row["maps"])) if row.get("maps") else "无逐图"))

    # ---- 出图：**每场一张**。某一场出不了图只让那一场退回纯文本 ----
    want_card = bool(es.get("card_enabled", True)
                     and es.get("card_results_enabled", True))
    cards = [render_result_card(row, now, es) if want_card else None for row in rows]

    if check:
        for i, row in enumerate(rows):
            log("")
            log(format_result_caption(row, now) if cards[i] else format_result(row, now))
            if cards[i]:
                path = os.path.join(tempfile.gettempdir(),
                                    "esports_result_check_%02d.png" % (i + 1))
                try:
                    with open(path, "wb") as fp:
                        fp.write(cards[i])
                    log("[check] 这一场的图已存到：%s" % path)
                except OSError as exc:
                    log("[warn] 战报预览图写不出来：%s" % exc)
        log("\n[check] 以上是将会发送的内容（未发送、未写状态）")
        return 0

    # ---- 发送：每场各发一条。**一场失败不影响别场** ----
    notifiers = watch.build_notifiers(cfg)
    sent, not_sent = [], []
    for i, row in enumerate(rows):
        pair = " vs ".join(list(row.get("teams") or [])[:2])
        card = cards[i]
        body = format_result_caption(row, now) if card else format_result(row, now)
        delivered, failed = send_with_retry(with_card_image(notifiers, card), body, es)
        if not delivered or failed:
            log("[error] %s 的战报发送失败：%s（这一场不写状态，下一轮重发）"
                % (pair, "、".join(failed) or "未知"))
            not_sent.append(row)
            continue
        log("[sent] %s 的战报已送达（%d 个通道）" % (pair, len(notifiers)))
        sent.append(row)
        # ⚑ 送达后**立刻**落盘，不等整轮跑完。
        #    为什么必须立刻：万一进程在「消息已发出」和「状态已保存」之间被杀
        #    （OOM、部署重启、systemd 收工、磁盘满），这一场在清单里还是 pending，
        #    下一轮 10 分钟后会**原样再发一遍**。攒到整轮末尾才保存的话，本轮已发出的
        #    每一条都会变成重复战报；这样最多只丢「最后一条」的登记。
        #    落盘走 `_persist`：锁内重读 + 按身份键合并，不是把旧快照整体写回。
        _persist([row])

    # expired 的 status 由 `_persist` 在锁内重读后一并写（靠 abandoned_ids 认条目），
    # 正常情况下跟着上面每次落盘一起生效；但「一场都没送达」时上面一次都没落盘，
    # 末尾必须补一次，否则下一轮又拿同一批过期条目问一遍。
    if not sent:
        _persist([])

    if not_sent:
        log("[error] 本轮 %d 场：发出 %d 场、%d 场没发出去（没发的下一轮重试）"
            % (len(rows), len(sent), len(not_sent)))
        return 1
    return 0


# ==========================================================================
# 开赛提醒（STARTING SOON，2026-10-06 用户拍板实现）
# ==========================================================================
#
# 每分钟 timer（douyu-esports-announce.timer）拉起 `--announce`：
#   读 pending 清单 → estimate_starts 算 estimatedStart（**纯本地、0 网络请求**）
#   → est 落进「未来 LEAD 分钟」的场次发一条 Match Preview 卡。
# 为什么不做真实赛况轮询：Liquipedia 计时器到点才变 LIVE，盯它就得高频抓页，
# 和条款网关（≤1 次/30 秒）直接冲突 —— 所以按 47 节规范的务实映射，
# estimatedStart 只在**串场对**（同赛事且共用同一支队伍）上做级联修正，
# 其余场次信登记的 ts —— 同一赛事内的并行流互不干扰。
# 提醒锁在 state_esports_announce.json：每场（默认）只提醒一次；
# estimated 漂移 ≥10 分钟且没重提醒过 → 再提醒一次；之后绝不再打扰。


def _match_duration_sec(item):
    """fallback 时长模型：Bo1 80 / Bo3 140 / Bo5 240 分钟，认不出按 Bo3。"""
    bo = (item.get("bo") or "").strip()
    return ANNOUNCE_DURATE_MIN.get(bo, ANNOUNCE_DURATE_MIN["Bo3"]) * 60


def _share_team(a, b):
    """两场是否共用至少一支队伍（大小写不敏感）。"""
    ta = {(t or "").strip().lower() for t in (a.get("teams") or []) if (t or "").strip()}
    tb = {(t or "").strip().lower() for t in (b.get("teams") or []) if (t or "").strip()}
    return bool(ta & tb)


def estimate_starts(items):
    """给清单条目算 estimatedStart（秒级 epoch），返回 {下标: est_ts}。

    · 基准 = 自己登记的 ts（预告时页面报出的开赛时间）；
    · **串场对**才级联 —— 判据是「同一 tour **且共用至少一支队伍**」：
      那支队不可能同时打两场，所以本场最早只能在前一场推算结束时间
      （它的 est + fallback 时长）+ turnaround 之后开打；
      级联用 est 而不是 ts —— A/B → A/C → A/D 这种连环串场能一路推下去；
    · **同一赛事内的并行流不传染**：一个 Round 常见「3 个时段 × 2 条并行流」，
      两条流是不同的 12 支队，登记的那个时刻就是真的。按「同赛事」级联会把
      它们串成一条链、越推越晚（2026-10-06 线上故障见文件顶部注释）；
    · 不同赛事当然也不传染。
    """
    order = sorted(range(len(items or [])),
                   key=lambda i: int((items or [])[i].get("ts") or 0))
    est = {}
    for pos, i in enumerate(order):
        it = items[i]
        floor_ts = int(it.get("ts") or 0)
        tour = (it.get("tour") or "").strip()
        if tour:
            for j in order[:pos]:
                prev = items[j]
                if (prev.get("tour") or "").strip() != tour:
                    continue
                if not _share_team(prev, it):
                    continue                      # 不共用队伍 = 可以并行，不级联
                cand = est[j] + _match_duration_sec(prev) \
                    + ANNOUNCE_TURNAROUND_MIN * 60
                if cand > floor_ts:
                    floor_ts = cand
        est[i] = floor_ts
    return est


def announce_key(item):
    """提醒锁的键：队名规范键 + 登记的开赛时间。est 会漂，ts 不会。"""
    return "%s|%d" % (_teams_key(item.get("teams")), int(item.get("ts") or 0))


def load_announce_state(path=ANNOUNCE_STATE_FILE):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict) and isinstance(data.get("announced"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"announced": {}}


def save_announce_state(state, path=ANNOUNCE_STATE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _announce_mark(state, key, rec):
    """把「这一场提醒过了」记进状态（就地改并返回 state）。"""
    state.setdefault("announced", {})[key] = rec
    return state


def update_announce(apply_fn, path=ANNOUNCE_STATE_FILE):
    """在**排他锁**里做一次「读 → `apply_fn(state)` → 写」，语义同 `update_pending`。

    这里的锁比清单文件那把更要紧：提醒锁丢了 = **同一场提醒会再发一遍**。
    每发一条就在锁内读-改-写一次（而不是攒到整轮末尾），
    这样「已发出但锁没保存」的窗口只有一条消息那么长。
    """
    with file_lock(path):
        state = load_announce_state(path)
        out = apply_fn(state)
        if out is None or out is False:
            return state
        if out is True:
            out = state
        save_announce_state(out, path)
        return out


def announce_due(items, est_map, now_ts, lead_minutes, announced):
    """挑出「现在该提醒」的条目，返回 [(下标, est_ts, key, 第几次提醒)]。

    窗口：now <= est <= now + LEAD（已开打的不提醒 —— 战报马上就来了）。
    锁：没提醒过 → 提醒；提醒过但 est 漂移 ≥ ANNOUNCE_REMIND_DRIFT_SEC
    且重提醒次数 < ANNOUNCE_REMIND_MAX → 再提醒一次；其余跳过。
    """
    due = []
    window = max(1, lead_minutes) * 60
    for i, it in enumerate(items or []):
        if it.get("status") != "pending":
            continue
        est_ts = int(est_map.get(i) or it.get("ts") or 0)
        if est_ts < now_ts or est_ts > now_ts + window:
            continue
        key = announce_key(it)
        rec = announced.get(key)
        if not rec:
            due.append((i, est_ts, key, 0))
            continue
        reminders = int(rec.get("reminders") or 0)
        if reminders >= ANNOUNCE_REMIND_MAX:
            continue
        if abs(est_ts - int(rec.get("est_ts") or 0)) < ANNOUNCE_REMIND_DRIFT_SEC:
            continue
        due.append((i, est_ts, key, reminders + 1))
    return due


def format_announce_caption(item, est_ts, now, reminder):
    """开赛提醒的一行文字。第二次提醒会注明「时间有调整」。"""
    teams = list(item.get("teams") or [])
    while len(teams) < 2:
        teams.append("?")
    dt = datetime.fromtimestamp(est_ts, CST)
    mins = max(0, int(round((est_ts - now.timestamp()) / 60)))
    head = "【CS2 开赛提醒】"
    if reminder:
        head = "【CS2 开赛提醒 · 时间有调整】"
    return "%s %s %s vs %s（%s）· %s 开打 · 约 %d 分钟后" % (
        head, dt.strftime("%H:%M"), teams[0], teams[1],
        (item.get("bo") or "Bo3").strip().upper() or "BO?",
        dt.strftime("%H:%M"), mins)


def run_announce(cfg, args):
    check = bool(getattr(args, "check_announce", False))
    es = dict(cfg.get("esports") or {})
    if not es.get("announce_enabled", True) and not check:
        log("[info] 开赛提醒已关闭（announce_enabled=false），本轮不做事")
        return 0
    try:
        lead = max(1, int(es.get("announce_lead_minutes") or 5))
    except (TypeError, ValueError):
        lead = 5
    now = datetime.now(CST)
    data = load_results_pending()
    items = data.get("items") or []
    if not items and not check:
        log("[info] 清单为空，没有可提醒的场次（0 网络请求）")
        return 0
    est_map = estimate_starts(items)

    if check:
        # 演练：取清单里 est 最近的一场（不管窗口），渲染出图存 $TEMP 看效果。
        pending = [(i, it) for i, it in enumerate(items)
                   if it.get("status") == "pending"]
        if pending:
            i, it = max(pending, key=lambda p: int(est_map.get(p[0]) or 0))
            est_ts = int(est_map.get(i) or it.get("ts") or 0)
        else:
            it = {"teams": ["Team Spirit", "Team Falcons"],
                  "shorts": ["Spirit", "Falcons"], "logos": ["", ""],
                  "ts": int(now.timestamp()) + 4 * 60, "bo": "Bo3",
                  "tour": "ESL Pro League Season 24 - Round 3",
                  "status": "pending"}
            est_ts = int(now.timestamp()) + 4 * 60
        log("[check] 演练：%s vs %s（est %s）"
            % ((it.get("teams") or ["?"])[0], (it.get("teams") or ["?", "?"])[1],
               datetime.fromtimestamp(est_ts, CST).strftime("%H:%M")))
        png = render_preview_card_html(it, est_ts, now, es)
        log("[check] 文字版：%s" % format_announce_caption(it, est_ts, now, 0))
        if png:
            path = os.path.join(tempfile.gettempdir(), "esports_announce_check.png")
            try:
                with open(path, "wb") as fp:
                    fp.write(png)
                log("[check] 预览图已存到：%s" % path)
            except OSError as exc:
                log("[warn] 预览图写不出来：%s" % exc)
        else:
            log("[check] 卡片没出（退回纯文本的样子，见上面文字版）")
        log("\n[check] 以上是将会发送的内容（未发送、未写状态）")
        return 0

    due = announce_due(items, est_map, now.timestamp(), lead,
                       load_announce_state().get("announced") or {})
    if not due:
        log("[info] 没有进入提醒窗口的场次（清单 %d 条，0 网络请求）" % len(items))
        return 0
    log("[info] %d 场进入提醒窗口（LEAD=%d 分钟）" % (len(due), lead))

    state = load_announce_state()
    notifiers = watch.build_notifiers(cfg)
    want_card = bool(es.get("card_enabled", True))
    sent_cnt = 0
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for i, est_ts, key, reminder in due:
        it = items[i]
        pair = " vs ".join(list(it.get("teams") or [])[:2])
        png = render_preview_card_html(it, est_ts, now, es) if want_card else None
        body = format_announce_caption(it, est_ts, now, reminder)
        delivered, failed = send_with_retry(with_card_image(notifiers, png),
                                            body, es)
        if not delivered or failed:
            log("[error] %s 的开赛提醒发送失败：%s（不写锁，下一分钟重试）"
                % (pair, "、".join(failed) or "未知"))
            continue
        log("[sent] %s 的开赛提醒已送达（%s）"
            % (pair, "第 %d 次" % (reminder + 1) if reminder else "首次"))
        # ⚑ 送达后**立刻**写锁（锁内重读-改-写）。攒到整轮末尾才写的话，
        #    进程在「已发出」和「写锁」之间挂掉，下一分钟会把同一条提醒**再发一遍**。
        _rec = {"announced_at": stamp, "est_ts": est_ts, "reminders": reminder + 1}
        update_announce(lambda st, _k=key, _r=_rec: _announce_mark(st, _k, _r))
        sent_cnt += 1
    return 0 if sent_cnt == len(due) else (1 if sent_cnt == 0 else 0)


# ==========================================================================
# 全天整合版（版式 C）
# ==========================================================================
#
# 用户 2026-10-05 晚的要求：「全天的最后一场打完再发一遍合起来的战果」，
# 实现方案选了「**次日早上固定时刻**发前一个赛程日」——
# 比「等当天清单清空再发」稳：遇到延期/取消的场次不会把汇总永远卡住。
#
# 这一版**不发任何网络请求**，纯粹读 state_results_pending.json 里的快照
# （单场战报发出去的时候顺手存下来的队名/短名/队标/赛事名/比分）。

def load_daily_state(path=DAILY_STATE_FILE):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_daily_state(st, path=DAILY_STATE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(st, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def daily_rows(items, start, end, es):
    """挑出「上一个赛程日」里**已经发过单场战报**的场次，做成整合版要用的行。

    只认 `status == "reported"`，这是刻意的：
      · `pending`（还没出结果）和 `abandoned`（延期/取消）放进汇总，
        会让人以为「怎么少了一场」或者「这场怎么没比分」；
      · 比分不是「两个数字」的也丢掉 —— 宁可少一行，也不要画一行假的。
    """
    lo, hi = start.timestamp(), end.timestamp()
    rows = []
    for it in items or []:
        if it.get("status") != "reported":
            continue
        ts = int(it.get("ts") or 0)
        if not (lo <= ts < hi):
            continue
        teams = [t for t in (it.get("teams") or [])[:2]]
        if len(teams) < 2 or not all(teams):
            continue
        score = (it.get("score") or "").strip()
        nums = score.split(":")
        if len(nums) != 2 or not all(x.isdigit() for x in nums):
            continue
        winner = it.get("winner") or ""
        if winner not in teams:
            continue
        rows.append({
            "ts": ts, "teams": teams,
            "shorts": list(it.get("shorts") or []),
            "logos": list(it.get("logos") or []),
            "score": score,
            "score_left": nums[0], "score_right": nums[1],
            "winner": winner,
            "bo": it.get("bo") or "",
            "tour": it.get("tour") or "",
            # 用户选的版式 C **不画逐图**，所以这里永远空着。
            # 留着这个键是为了让 render_results_card 的入参形状和单场一致。
            "maps": [],
        })
    rows.sort(key=lambda x: x["ts"])
    return rows


def lost_items(items, start, end):
    """挑出这个赛程日里**被判成延期/取消**（`abandoned`）的场次。**纯函数、不联网。**

    这些就是「本该出现在整合版里、却因为结算放弃时刻太早而漏掉」的候选。
    """
    lo, hi = start.timestamp(), end.timestamp()
    return [it for it in (items or [])
            if it.get("status") == "abandoned" and lo <= int(it.get("ts") or 0) < hi]


def backfill_abandoned(items, start, end, es, dry=False):
    """把上一个赛程日里被判「延期/取消」的场次**再捞一次**。返回补回来的场次数。

    为什么需要它 —— 结算流水线的放弃时刻是「登记 ts + timeout」，而**登记 ts 是
    *计划*开赛时刻，不是实际开赛时刻**：同一个场地前一档打满三图会把下一档拖后
    30~60 分钟，所以「打满三图的 Bo3」经常在放弃时刻前后才出结果。
    2026-10-05 的 M80 vs TYLOO(2:1) 与 Aurora Gaming vs BetBoom(2:1) 就这么丢了，
    次日整合版只剩 6 场（用户报「应该是八场」）。

    把 timeout 调大只是减少发生的概率；**这里才是兜底** —— 整合版是次日早上才发的，
    那时所有场次早已尘埃落定，页面上一定查得到。

    成本控制：**只在确实有 abandoned 条目时才抓页面**。正常日 0 网络请求，
    性质上和原来「0 请求」的口径不冲突（有场次丢了才付这 1 次请求）。

    补回来的场次只进整合版，**不会补发单场战报** —— 隔了十几个小时再推一条
    「XX 2:1 YY」是纯噪音，汇总里带上才是它该在的地方。

    `dry=True`（`--check-daily` 演练）：照常抓、照常配对，**只改内存、不落盘**，
    这样预览图是对的（会显示补回来的场次），而清单文件一个字节都不动。
    """
    lost = lost_items(items, start, end)
    if not lost:
        return 0
    log("[info] 补漏：上一个赛程日有 %d 场被判延期/取消，再捞一次：%s"
        % (len(lost), _pairs_label(lost)))
    r = fetch_matches(es)
    if r is None or not r.get("ok"):
        log("[warn] 补漏：抓取失败（%s），这几场补不回来，整合版会少列"
            % ((r or {}).get("error") or "未知"))
        return 0
    html_text, err = extract_html(r["text"])
    if html_text is None:
        log("[warn] 补漏：%s" % err)
        return 0
    fin = finished_index(parse_matches(html_text))
    # ⚠️ `strict=False`：补漏是**最后一道兜底**。这里再拦「比分配不上赛制」的话，
    #    万一真是页面把赛制标错/弃权判负这类边角情况，这一场就从汇总里彻底消失了 ——
    #    而汇总「少一场」正是 2026-10-05 用户报的那个故障。宁可带上可疑的终局，也不丢场次。
    rows, still = result_rows(lost, fin, es, strict=False)
    if not rows:
        log("[info] 补漏：页面上这 %d 场仍没有结果，确认是延期/取消，整合版不列它们"
            % len(lost))
        return 0
    now2 = datetime.now(CST)
    stamp = now2.strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        absorb_result(row["item"], row, stamp)
        row["item"]["backfilled"] = True     # 留痕：这条不是实时战报报过的
    if dry:
        # 演练：图上是补齐后的（便于验收），但清单文件一个字节都不动。
        log("[check] 补漏（演练）：%d 场会改判为已结算并补进整合版，**只改内存未落盘**"
            % len(rows))
    else:
        # ⚠️ 原来这里是 `save_results_pending({"items": items})` —— 把调用方手上那份
        #    快照**整体覆盖**上去。`items` 是 run_daily 开始时就读的，这中间
        #    `douyu-esports`(登记) / `-results`(结算) 都可能改过同一个文件，
        #    整体覆盖会把它们的改动**静默抹掉**。现在改成锁内重读 + 只改我们认得的条目。
        def _apply(fresh):
            idx = {_item_ident(it): it for it in fresh["items"]}
            for row in rows:
                it = idx.get(_item_ident(row.get("item") or {}))
                if it is None:
                    # 磁盘上已经没这条了（被 prune 清掉），不凭空造一条回来。
                    continue
                absorb_result(it, row, stamp)
                it["backfilled"] = True      # 留痕：这条不是实时战报报过的
            return _prune_pending(fresh, now2, es)
        update_pending(_apply)
        # 注意用 [info] 而不是 [sent] —— 补漏**不往外发消息**，
        # 它只是把场次补进下面那条整合版汇总里。
        log("[info] 补漏：%d 场从「延期/取消」改判为已结算，将补进这次的整合版：%s"
            % (len(rows), "、".join("%s %s" % (" vs ".join(row["teams"][:2]), row["score"])
                                   for row in rows)))
    if still:
        log("[info] 补漏：另外 %d 场页面上确实没有结果：%s"
            % (len(still), _pairs_label(still)))
    return len(rows)


def run_daily(cfg, args):
    """发「上一个赛程日」的整合版战果。

    **正常日 0 网络请求**；只有「这个赛程日有场次被判成延期/取消」时，才多发 1 次
    请求把它们补回来（见 `backfill_abandoned`）—— 不补的话汇总会**静默地**少列几场，
    2026-10-05 就因此只发了 6 场（实际 8 场）。

    幂等是第一要求：这个 job 每天由 timer 触发，但完全可能被手动再跑一次，
    所以发成功之后在 state_esports_daily.json 里记下「哪个赛程日已经发过了」。
    """
    es = resolve_config(cfg)
    now = datetime.now(CST)
    check = bool(getattr(args, "check_daily", False))

    if not es.get("daily_enabled", True):
        log("[silent] 全天整合版已关闭（daily_enabled=false）")
        return 0

    # 显式挡住「daily_run_time 早于 preview_run_time」这个配置错误。
    # 不挡的话拿到的是前天那一场，汇总会**静默地**少一天，很难查。
    ph, pm = parse_run_time(es.get("preview_run_time"))
    dh, dm = parse_run_time(es.get("daily_run_time"))
    if (dh, dm) <= (ph, pm):
        log("[warn] daily_run_time(%02d:%02d) 不晚于 preview_run_time(%02d:%02d)，"
            "整合版会汇总到错误的赛程日，本轮不发" % (dh, dm, ph, pm))
        return 0

    start, end = last_schedule_day(now, es.get("preview_run_time"))
    day_key = start.strftime("%Y-%m-%d")
    span = "%s → %s" % (start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"))

    if not check:
        st = load_daily_state()
        if st.get("last_day") == day_key:
            log("[silent] 赛程日 %s 的整合版已经发过了（%s），本轮不发"
                % (day_key, st.get("last_at") or "?"))
            return 0

    data = load_results_pending()
    # 补漏：结算的放弃时刻锚在**登记（计划）开赛时刻**上，而前一档打满三图会把
    # 下一档拖后 30~60 分钟 → 打满三图的 Bo3 很容易被误判成「延期/取消」。
    # 出汇总之前再捞一次；没有 abandoned 条目时 0 网络请求。
    backfill_abandoned(data["items"], start, end, es, dry=check)
    rows = daily_rows(data["items"], start, end, es)
    n_all = len([it for it in data["items"]
                 if start.timestamp() <= int(it.get("ts") or 0) < end.timestamp()])
    log("[info] 上一个赛程日 %s：清单里 %d 场，其中 %d 场已结算入册"
        % (span, n_all, len(rows)))

    if not rows:
        log("[silent] 这个赛程日没有已结算的比赛，不发整合版")
        return 0

    max_rows = max(1, int(es.get("daily_max_rows") or HTML_IMG_MAX_ROWS))
    shown = rows[:max_rows]
    if len(shown) < len(rows):
        log("[info] 整合版只列前 %d 场（共 %d 场）" % (max_rows, len(rows)))

    # 卡片和标题都用**赛程日的开始时刻**当标签 —— 这样写出来就是
    # 「10-05 周一」，和当天早上那条预告的命名完全一致（预告也叫它「10-05」）。
    label_dt = start
    card = None
    if es.get("card_enabled", True) and es.get("card_results_enabled", True):
        # 三级降级：HTML 1920×1080 → 880px Pillow 旧卡 → 纯文本。
        # label_dt 当「赛程日标签」用，真正的生成时刻另传 now —— 否则页脚会把窗口
        # **起点**（10-05 09:30）当成「最后更新时刻」写出去。
        card = render_daily_results_card(shown, label_dt, es, gen_at=now)
    body = format_results_caption(rows, label_dt) if card else format_results(rows, label_dt)
    log("[info] 整合版：%d 场，%s"
        % (len(rows), "一行文字 + 一张整合图" if card else "纯文本"))

    if check:
        log("")
        log(body)
        if card:
            path = os.path.join(tempfile.gettempdir(), "esports_daily_check.png")
            try:
                with open(path, "wb") as fp:
                    fp.write(card)
                log("[check] 整合版预览图已存到：%s" % path)
            except OSError as exc:
                log("[warn] 整合版预览图写不出来：%s" % exc)
        log("\n[check] 以上是将会发送的内容（未发送、未写状态）")
        return 0

    notifiers = watch.build_notifiers(cfg)
    delivered, failed = send_with_retry(with_card_image(notifiers, card), body, es)
    if not delivered or failed:
        log("[error] 整合版发送失败：%s（不写状态，下一轮会重发）"
            % ("、".join(failed) or "未知"))
        return 1

    log("[sent] 整合版已送达（%d 个通道，%d 场）" % (len(notifiers), len(rows)))
    try:
        save_daily_state({"last_day": day_key,
                          "last_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                          "rows": len(rows)})
    except OSError as exc:
        # 标记写不进去 = 明天这一刻还有个机会重发今天这条。要显式说出来，
        # 否则「同一条整合版发了两遍」会变成一桩无头案。
        log("[warn] 整合版幂等标记写不进去（%s）：同一天再跑一次会重发" % exc)
    return 0


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

    def note(self, msg):
        """不计入项数的说明行：本机**没有可查的对象**时用它，别硬写一条永真断言。

        典型场景：某条断言要读 `config.example.json` / `*.service`，但那些文件
        只在仓库和部署包里，`install-watch.sh` 装到服务器上之后就没有了。
        那种情况下打一行 [skip] 说明为什么跳过，而不是让整个自检抛异常崩掉。
        """
        print("  [skip] %s" % msg, flush=True)

    def done(self, title):
        print("\n结果：%d 项通过，%d 项失败（共 %d 项）"
              % (self.n - self.fail, self.fail, self.n), flush=True)
        return 1 if self.fail else 0


# 战果解析用的 fixture：三块 —— ① 已结束 Bo3（胜方在左）② 已结束 Bo1（地图比分，胜方在右）
# ③ **有比分但没有 data-finished**（= 正在进行中）。第 ③ 块是这套断言里最重要的一个：
#    它钉住「绝不能把进行中的比分当战果发」。
RESULT_FIXTURE_HTML = """
<div class="match-info">
  <span class="match-info-countdown">
    <span class="timer-object" data-timestamp="1790000300" data-finished="finished">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left match-info-header-winner">
      <div class="block-team flipped">
        <span class="team-template-image-icon">
          <a href="/counterstrike/Team_Spirit" title="Team Spirit"><img alt="" src="/commons/images/thumb/1/1a/Spirit_allmode.png/50px-Spirit_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/Team_Spirit" title="Team Spirit">Spirit</a></span>
      </div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-icon"></span>
      <span class="match-info-header-scoreholder-scorewrapper">
        <span class="match-info-header-scoreholder-upper">
          <span class="match-info-header-scoreholder-score match-info-header-winner">2</span> : <span class="match-info-header-scoreholder-score">1</span></span>
        <span class="match-info-header-scoreholder-lower">(Bo3)</span></span>
      <span class="match-info-header-scoreholder-icon"></span></div>
    <div class="match-info-header-opponent match-info-header-loser">
      <div class="block-team">
        <span class="team-template-image-icon">
          <a href="/counterstrike/MOUZ" title="MOUZ"><img alt="" src="/commons/images/thumb/2/2b/MOUZ_allmode.png/50px-MOUZ_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/MOUZ" title="MOUZ">MOUZ</a></span>
      </div></div>
  </div>
  <div class="match-info-tournament">
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/ESL/Pro_League/Season_24#Round_3" title="ESL/Pro League/Season 24#Round 3"><span>ESL Pro League Season 24 - Round 3</span></a></span></span>
  </div>
</div>
<div class="match-info">
  <span class="match-info-countdown">
    <span class="timer-object" data-timestamp="1790000400" data-finished="finished">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left match-info-header-loser">
      <div class="block-team flipped">
        <span class="team-template-image-icon">
          <a href="/counterstrike/TYLOO" title="TYLOO"><img alt="" src="/commons/images/thumb/3/3c/TyLoo_allmode.png/50px-TyLoo_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/TYLOO" title="TYLOO">TYL</a></span>
      </div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-icon"></span>
      <span class="match-info-header-scoreholder-scorewrapper">
        <span class="match-info-header-scoreholder-upper">
          <span class="match-info-header-scoreholder-score">4</span> : <span class="match-info-header-scoreholder-score match-info-header-winner">13</span></span>
        <span class="match-info-header-scoreholder-lower">(Bo1)</span></span>
      <span class="match-info-header-scoreholder-icon"></span></div>
    <div class="match-info-header-opponent match-info-header-winner">
      <div class="block-team">
        <span class="team-template-image-icon">
          <a href="/counterstrike/9z_Team" title="9z Team"><img alt="" src="/commons/images/thumb/4/4d/9z_allmode.png/50px-9z_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/9z_Team" title="9z Team">9z</a></span>
      </div></div>
  </div>
  <div class="match-info-tournament">
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/Journey" title="Journey"><span>Journey Autumn 2026 - Group A</span></a></span></span>
  </div>
</div>
<div class="match-info">
  <span class="match-info-countdown">
    <span class="timer-object" data-timestamp="1790000500">x</span></span>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left">
      <div class="block-team flipped">
        <span class="team-template-image-icon">
          <a href="/counterstrike/Natus_Vincere" title="Natus Vincere"><img alt="" src="/commons/images/thumb/5/5e/NAVI_allmode.png/50px-NAVI_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/Natus_Vincere" title="Natus Vincere">NAVI</a></span>
      </div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-icon"></span>
      <span class="match-info-header-scoreholder-scorewrapper">
        <span class="match-info-header-scoreholder-upper">
          <span class="match-info-header-scoreholder-score">1</span> : <span class="match-info-header-scoreholder-score">0</span></span>
        <span class="match-info-header-scoreholder-lower">(Bo3)</span></span>
      <span class="match-info-header-scoreholder-icon"></span></div>
    <div class="match-info-header-opponent">
      <div class="block-team">
        <span class="team-template-image-icon">
          <a href="/counterstrike/Aurora_Gaming" title="Aurora Gaming"><img alt="" src="/commons/images/thumb/6/6f/Aurora_allmode.png/50px-Aurora_allmode.png" /></a></span>
        <span class="name"><a href="/counterstrike/Aurora_Gaming" title="Aurora Gaming">Aurora</a></span>
      </div></div>
  </div>
  <div class="match-info-tournament">
    <span class="match-info-tournament-wrapper">
      <span class="match-info-tournament-name">
        <a href="/counterstrike/ESL/Pro_League/Season_24#Round_3" title="ESL/Pro League/Season 24#Round 3"><span>ESL Pro League Season 24 - Round 3</span></a></span></span>
  </div>
</div>
"""


# 赛事页的 bracket 弹窗 fixture（拿逐图比分用）。
# 结构照 2026-10-05 实测的 ESL Pro League Season 24 赛事页裁：
#   · 第 1 个弹窗 = 正常一场打完的 Bo3，4 条 grid-row（**最后一条没打**，必须被丢掉）
#   · 第 2 个弹窗 = 只有一边队名（页面上的 TBD/未定对阵），必须被跳过
EVENT_FIXTURE_HTML = """
<div class="brkts-popup brkts-popup-container brkts-match-info-popup">
  <div class="brkts-popup-header">
    <span class="timer-object" data-timestamp="1791018000" data-finished="finished">x</span>
  </div>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left match-info-header-loser">
      <div class="block-team"><div class="team-name">Legacy</div></div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-scorewrapper">
        <span class="match-info-header-scoreholder-upper">
          <span class="match-info-header-scoreholder-score">1</span> : <span class="match-info-header-scoreholder-score match-info-header-winner">2</span></span>
        <span class="match-info-header-scoreholder-lower">(Bo3)</span></span></div>
    <div class="match-info-header-opponent match-info-header-winner">
      <div class="block-team"><div class="team-name">PARIVISION</div></div></div>
  </div>
  <div class="brkts-popup-body-grid">
    <div class="brkts-popup-body-grid-row">
      <div class="brkts-popup-body-grid-cell"><a href="/counterstrike/Dust_II">Dust II</a></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">13</div></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">5</div></div>
    </div>
    <div class="brkts-popup-body-grid-row">
      <div class="brkts-popup-body-grid-cell"><a href="/counterstrike/Inferno">Inferno</a></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">2</div></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">13</div></div>
    </div>
    <div class="brkts-popup-body-grid-row">
      <div class="brkts-popup-body-grid-cell"><a href="/counterstrike/Ancient">Ancient</a></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">12</div></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score">16</div></div>
    </div>
    <div class="brkts-popup-body-grid-row">
      <div class="brkts-popup-body-grid-cell"><a href="/counterstrike/Nuke">Nuke</a></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score"></div></div>
      <div class="brkts-popup-body-grid-cell"><div class="detailed-scores-main-score"></div></div>
    </div>
  </div>
  <div class="brkts-popup-footer">
    <a href="https://www.hltv.org/matches/2398718/match">Match page</a></div>
</div>
<div class="brkts-popup brkts-popup-container brkts-match-info-popup">
  <div class="brkts-popup-header">
    <span class="timer-object" data-timestamp="1791099000">x</span>
  </div>
  <div class="match-info-header">
    <div class="match-info-header-opponent match-info-header-opponent-left">
      <div class="block-team"><div class="team-name">TBD</div></div></div>
    <div class="match-info-header-scoreholder">
      <span class="match-info-header-scoreholder-scorewrapper">
        <span class="match-info-header-scoreholder-upper">vs</span></span></div>
  </div>
</div>
"""


def _mk(ts, teams, tour, bo="Bo3", tbd=False, logos=None, shorts=None,
        finished=False, sides=None, score="", tour_page=""):
    n = len(list(teams))
    return {"ts": ts, "teams": list(teams), "bo": bo, "tour": tour,
            # 赛事页路径：拿逐图比分用的。不传就空着 —— 空着只是「没有逐图」。
            "tour_page": tour_page, "tbd": tbd,
            # 页面自带的短名，只给「队标拿不到时的灰色占位块」用。
            # 不传就空着 —— 占位块会退回长名（自动缩字号）。
            "shorts": (list(shorts) if shorts else [""] * n)[:n],
            # 默认给空 URL：自检**绝不能联网**去下队标，
            # ensure_logo("") 在上面就返回 None，卡片会画占位块。
            "logos": list(logos) if logos else ["", ""],
            # 战果用的三件套；构造「已结束」的用例时要一起给，
            # 否则 finished_index / result_rows 会按「没打完」处理。
            "finished": bool(finished),
            "sides": (list(sides) if sides else [""] * n)[:n],
            "score": score}


def selftest():
    t = _T()
    es = dict(ESPORT_DEFAULTS)

    # ---- 本文件自己的源码 / 语法树：下面几条「结构断言」共用 ----
    # 为什么要 AST 而不是字符串 needle：自检就写在本文件里，字符串 needle 会**把自己
    # 匹配到**（正向 `in` 恒真、反向 `not in` 恒假，2026-10-06 已踩三次，见 _bad_call
    # 那几处的注解）。AST 问的是「谁调了谁、谁在谁后面」，天然没有自指问题。
    _src_txt = open(os.path.join(HERE, "esports.py"), encoding="utf-8").read()
    _ast_tree = ast.parse(_src_txt)

    def _call_names(node):
        """{被调用的名字: [行号, ...]} —— 只认 Name / Attribute 两种调用形式。"""
        out = {}
        for _c in ast.walk(node) if node is not None else []:
            if not isinstance(_c, ast.Call):
                continue
            _f = _c.func
            _nm = _f.id if isinstance(_f, ast.Name) else (
                _f.attr if isinstance(_f, ast.Attribute) else None)
            if _nm:
                out.setdefault(_nm, []).append(_c.lineno)
        return out

    def _fn_def(name):
        return next((n for n in ast.walk(_ast_tree)
                     if isinstance(n, ast.FunctionDef) and n.name == name), None)

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
        # 逐图比分要用「赛事页面路径」，它是 Liquipedia 链接里给的，不用维护别名表。
        t.check("tour_page 取链接 title（不是可见文案）",
                ms[0]["tour_page"] == "BLAST/Premier", ms[0]["tour_page"])
        t.check("每场都带 tour_page 字段（拿不到就是空串）",
                all(isinstance(m.get("tour_page"), str) for m in ms))
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
        t.check("⚑ 亮/暗两套图的队伍取「暗色版」（卡片全是深色底，黑蜜蜂之鉴）",
                ms[1]["logos"][1].endswith("50px-Beta_darkmode.png"),
                ms[1]["logos"][1])
        t.check("⚑ 队标 class 用前缀匹配，同时带 mode 后缀也认得（否则名队会变占位块）",
                ms[1]["logos"][1] != ""
                and "Beta_" in ms[1]["logos"][1],
                ms[1]["logos"][1])
        t.check("左/右队标各就各位，不串位",
                ms[1]["logos"] == [
                    "https://liquipedia.net/commons/images/thumb/a/aa/"
                    "Alpha_allmode.png/50px-Alpha_allmode.png",
                    "https://liquipedia.net/commons/images/thumb/d/dd/"
                    "Beta_darkmode.png/50px-Beta_darkmode.png"],
                ms[1]["logos"])
        t.check("TBD 场次没有队标", ms[2]["logos"] == ["", ""], ms[2]["logos"])
        t.check("两个队标始终是 2 个（对齐 teams，缺也给空串）",
                all(len(m["logos"]) == 2 for m in ms))

        # 短名：页面上 <span class="name"> 里那个缩写，只给「队标拿不到时的占位块」用
        t.check("短名取的是 <a> 内层文本，不是长名 title",
                ms[0]["shorts"] == ["TYL", "LVG"], ms[0]["shorts"])
        t.check("短名与长名一一对齐（数量一致，不串位）",
                all(len(m["shorts"]) == len(m["teams"]) for m in ms))
        t.check("TBD 那场没有 <a>，短名给空串而不是乱塞",
                ms[2]["shorts"] == ["", ""], ms[2]["shorts"])
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

    # 这一块全部在测 **880px Pillow 旧卡**（card_html_enabled=False 钉死，
    # 否则装了 Chromium 的机器会拿到 1920×1080 的 HTML 大图，高度断言全崩）。
    # HTML 大图的断言在下面 3f-2。
    pill = dict(es, card_html_enabled=False)
    if Image is None:
        t.check("本机没有 Pillow → 不出图，返回 None（上层据此退回纯文本）",
                render_card(many[:2], base, pill) is None)
        print("   （本机没装 Pillow，跳过渲染断言）")
    else:
        png = render_card(many[:2], base, pill)
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

        png3 = render_card(many, base, dict(pill, card_max_rows=3))
        if png3:
            t.check("超过 card_max_rows 时按上限截断",
                    Image.open(io.BytesIO(png3)).height
                    == CARD_HDR_H + CARD_ROW_H * 3 + CARD_FTR_H)

        # 字体文件缺失 → 安静降级（把模块级常量临时指到一个不存在的路径）
        _keep = globals()["CARD_FONT_FILE"]
        try:
            globals()["CARD_FONT_FILE"] = os.path.join(HERE, "__no-such-font__.otf")
            t.check("找不到字体文件时返回 None（不抛异常）",
                    render_card(many[:2], base, pill) is None)
        finally:
            globals()["CARD_FONT_FILE"] = _keep
        t.check("恢复字体路径后又能出图",
                isinstance(render_card(many[:2], base, pill), (bytes, bytearray)))

        # 队标预算为 0 → 不去联网，画占位块照旧出图
        png_b = render_card(
            [mk(ts(14), ["A", "B"], "T",
                logos=["https://example.invalid/never.png", ""])],
            base, dict(pill, logo_max_new_per_run=0))
        t.check("队标下载预算为 0 时不出网、照样出图",
                isinstance(png_b, (bytes, bytearray)), type(png_b))

        # 占位块：写页面短名，并随长度缩字号（曾经的「前两个字母」会撞车）
        _d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        t.check("占位块写页面短名，不是「队名前两个字母」",
                _placeholder_label(_d, "Spirit")[0] == "Spirit",
                _placeholder_label(_d, "Spirit"))
        t.check("短名短的用大字号、长的自动缩小（都不溢出方块）",
                _placeholder_label(_d, "PV")[1].size
                > _placeholder_label(_d, "Vitality")[1].size)
        t.check("缩到最小还放不下才截断，且带省略号",
                _placeholder_label(_d, "A" * 60)[0].endswith("…"),
                _placeholder_label(_d, "A" * 60))
        t.check("没有短名时写「?」，不画空白",
                _placeholder_label(_d, "")[0] == "?"
                and _placeholder_label(_d, "   ")[0] == "?")
        t.check("⚑ 曾经的 bug 不会复现：Spirit / Vitality / Falcons 三个块不再都是 TE",
                len({_placeholder_label(_d, x)[0]
                     for x in ("Spirit", "Vitality", "Falcons")}) == 3)
        t.check("⚑ 整张卡一个队标都没有时，仍然出图（不退回纯文本）",
                render_card(
                    [mk(ts(14), ["Team Spirit", "Team Falcons"], "T",
                        shorts=["Spirit", "Falcons"])],
                    base, dict(es, logo_max_new_per_run=0)) is not None)
        t.check("短名缺失时不炸（退回长名占位）",
                render_card([mk(ts(14), ["Team Spirit", "Team Falcons"], "T")],
                            base, dict(es, logo_max_new_per_run=0)) is not None)

        # 有画不出来的字：880px Pillow 层整张不出，退回纯文本（不能画豆腐块）。
        # V2 HTML 层没有「子集」概念 —— 生僻字由系统字体（fonts-noto-cjk 等）兜底，
        # 卡照常出、不拒画；所以这条防御只对关掉 HTML 一级的 pill 生效。
        if find_chrome(es):
            t.check("V2 开着：生僻字场次 HTML 卡照常出（系统字体兜底，不拒画）",
                    render_card([mk(ts(14), ["測試隊", "B"], "T")], base, es)
                    is not None)
        t.check("有子集外的字 → 880px 旧卡层整张不出（关掉 HTML 一级验证）",
                render_card([mk(ts(14), ["測試隊", "B"], "T")], base, pill)
                is None)
        t.check("同一个有生僻字的场次，纯文本兜底照样能出",
                "測試隊 vs B" in format_daily(
                    [mk(ts(14), ["測試隊", "B"], "T")], base, es))

    # ---- 3c. 战果公布 ----
    print("\n-- 3c. 战果公布（解析 / 配对 / 时间闸门 / 去重）--")
    rms = parse_matches(RESULT_FIXTURE_HTML)
    t.check("战果 fixture 切出 3 场", len(rms) == 3, "实际 %d" % len(rms))
    if len(rms) == 3:
        t.check("①已结束 Bo3 认得走 data-finished", rms[0]["finished"] is True)
        t.check("②已结束 Bo1 认得走 data-finished", rms[1]["finished"] is True)
        t.check("⚑ ③有比分但缺 data-finished → **不算已结束**（进行中的比赛）",
                rms[2]["finished"] is False,
                "score=%s sides=%s" % (rms[2]["score"], rms[2]["sides"]))
        t.check("比分从两个 scoreholder-score 里取（不是连 (Bo3) 一起吞进来）",
                rms[0]["score"] == "2:1" and rms[2]["score"] == "1:0",
                [m["score"] for m in rms])
        t.check("Bo1 保留的是**地图比分**（13:4）而不是 1:0",
                rms[1]["score"] == "4:13", rms[1]["score"])
        t.check("sides 与 teams 同序：左胜 = ['W','L']", rms[0]["sides"] == ["W", "L"],
                rms[0]["sides"])
        t.check("sides 与 teams 同序：右胜 = ['L','W']", rms[1]["sides"] == ["L", "W"],
                rms[1]["sides"])
        t.check("未结束场次的 score 是空串、sides 不成对",
                rms[2]["score"] == "1:0" and sorted(rms[2]["sides"]) != ["L", "W"])
        t.check("teams / shorts 依旧对齐（战果卡片也要画队标）",
                [m["teams"] for m in rms][0] == ["Team Spirit", "MOUZ"]
                and [m["shorts"] for m in rms][0] == ["Spirit", "MOUZ"])

    # finished_index：同一对队伍打过两遍时必须都留着（靠时间区分），不能互相顶掉
    same_pair = [
        _mk(1000, ["FlyQuest", "Ground Zero Gaming"], "T", "Bo3",
            finished=True, sides=["W", "L"], score="2:0"),
        _mk(9000, ["FlyQuest", "Ground Zero Gaming"], "T", "Bo3",
            finished=True, sides=["L", "W"], score="0:2"),
    ]
    fidx = finished_index(same_pair)
    t.check("同一对队伍的两场都留在索引里（不是只留一场）",
            len(fidx.get(_teams_key(["Ground Zero Gaming", "FlyQuest"])) or []) == 2)
    t.check("队名键与左右顺序无关",
            _teams_key(["A", "B"]) == _teams_key(["B", "A"]))
    t.check("未结束的场次不进索引",
            finished_index([_mk(1, ["A", "B"], "T")]) == {})

    # 按「队名 + 时间最近」配对：不能把几天前同一对队伍的结果当成今天这场
    item_old = {"teams": ["FlyQuest", "Ground Zero Gaming"], "ts": 1000,
                "bo": "Bo3", "status": "pending"}
    item_new = {"teams": ["FlyQuest", "Ground Zero Gaming"], "ts": 9000,
                "bo": "Bo3", "status": "pending"}
    rows_x, wait_x = result_rows([item_old], fidx, es)
    t.check("配对取「时间最近」的那一场（第 1 场）",
            len(rows_x) == 1 and rows_x[0]["ts"] == 1000, rows_x)
    rows_y, _w = result_rows([item_new], fidx, es)
    t.check("配对取「时间最近」的那一场（第 2 场）",
            len(rows_y) == 1 and rows_y[0]["ts"] == 9000, rows_y)
    # 要比**最近的那个候选**还远出容忍度，才算「这几场都不是它」
    far = dict(item_old, ts=9000 + RESULTS_TS_TOLERANCE + 600)
    rows_z, wait_z = result_rows([far], fidx, es)
    t.check("⚑ 时间差超出容忍度就不认（免得拿几天前的结果冒充今天）",
            not rows_z and len(wait_z) == 1)

    t.check("胜方判据：比分大的一方", rows_x[0]["score_left"] == "2"
            and rows_x[0]["winner"] == "FlyQuest", rows_x[0]["winner"])

    # ---- 「这个比分，配得上这个赛制吗」（2026-10-06 加）----
    #   三个「已结束」信号只说明页面认为打完了，没一个在管「赢下系列赛了吗」。
    t.check("系列赛判据：Bo3 的 1:0 / 0:1 都 = 还没打完",
            series_partial("Bo3", "1", "0") and series_partial("Bo3", "0", "1"))
    t.check("系列赛判据：Bo3 的 2:0 / 2:1 = 打完了",
            not series_partial("Bo3", "2", "0") and not series_partial("Bo3", "2", "1"))
    t.check("系列赛判据：Bo5 的 2:1 / 2:0 = 还没打完，3:0 / 3:2 = 打完了",
            series_partial("Bo5", "2", "1") and series_partial("Bo5", "2", "0")
            and not series_partial("Bo5", "3", "0") and not series_partial("Bo5", "3", "2"))
    t.check("⚑ 系列赛判据：Bo1 的 1:0 是**完整结果**，绝不能拦",
            not series_partial("Bo1", "1", "0"))
    t.check("⚑ 系列赛判据：赛制认不出就一律不拦（判据不确定时宁可发）",
            not series_partial("", "1", "0") and not series_partial("BO7", "1", "0"))
    t.check("系列赛判据：比分不是数字时不炸也不拦",
            not series_partial("Bo3", "?", "") and not series_partial("Bo3", None, None))
    t.check("超上限只留痕不拦：Bo3 的 3:0 算超上限；Bo1 永远不算（它是地图比分）",
            series_overshoot("Bo3", "3", "0")
            and not series_overshoot("Bo1", "13", "4")
            and not series_overshoot("Bo3", "2", "1"))

    # 页面抢跑：标了已结束，但比分只到 Bo3 的 1:0 → strict 下留在待结算，不发
    it_rush = {"teams": ["Alpha", "Bravo"], "ts": 1000, "bo": "Bo3",
               "status": "pending"}
    rush = _mk(1000, ["Alpha", "Bravo"], "T", "Bo3",
               finished=True, sides=["W", "L"], score="1:0")
    rows_r, wait_r = result_rows([it_rush], finished_index([rush]), es)
    t.check("⚑ 页面抢跑标 finished、比分只到 Bo3 的 1:0 → 不当战果发（strict）",
            not rows_r and len(wait_r) == 1,
            [r["score"] for r in rows_r])
    rows_r2, _w2 = result_rows([it_rush], finished_index([rush]), es, strict=False)
    t.check("⚑ 补漏那一遍（strict=False）不拦 —— 兜底宁可带上可疑终局，也不丢场次",
            len(rows_r2) == 1 and rows_r2[0]["score"] == "1:0", rows_r2)
    # 页面没给赛制时退回登记时的 bo，判据一样生效
    rush_nobo = _mk(1000, ["Alpha", "Bravo"], "T", "",
                    finished=True, sides=["W", "L"], score="1:0")
    rows_nb, wait_nb = result_rows([it_rush], finished_index([rush_nobo]), es)
    t.check("⚑ 页面没给赛制时退回登记时的 bo，同样拦得住 Bo3 的 1:0",
            not rows_nb and len(wait_nb) == 1, rows_nb)
    good_r = _mk(1000, ["Alpha", "Bravo"], "T", "Bo3",
                 finished=True, sides=["W", "L"], score="2:1")
    rows_g, _wg = result_rows([it_rush], finished_index([good_r]), es)
    t.check("闪电战不能误伤：Bo3 的 2:1 照常发",
            len(rows_g) == 1 and rows_g[0]["score_left"] == "2",
            [r["score"] for r in rows_g])

    # 时间闸门 + 每赛制的 grace/timeout
    t.check("Bo1 的 grace/timeout = 50/150 分钟",
            _settle_seconds("Bo1", es) == (3000, 9000), _settle_seconds("Bo1", es))
    t.check("Bo3 的 grace/timeout = 100/270 分钟",
            _settle_seconds("Bo3", es) == (6000, 16200), _settle_seconds("Bo3", es))
    t.check("Bo5 的 grace/timeout = 170/360 分钟",
            _settle_seconds("Bo5", es) == (10200, 21600), _settle_seconds("Bo5", es))
    t.check("赛制认不出来时退回 Bo3 的窗口",
            _settle_seconds("", es) == _settle_seconds("Bo3", es)
            and _settle_seconds("Bo7", es) == _settle_seconds("Bo3", es))

    gate = {"teams": ["A", "B"], "ts": 100000, "bo": "Bo3", "status": "pending"}
    def _gate(offset):
        return split_due([dict(gate)], datetime.fromtimestamp(100000 + offset, CST), es)
    t.check("开赛 30 分钟内：既不抓也不算超时（0 请求）", _gate(1800) == ([], []))
    t.check("刚过 grace（100 分钟）：进入待结算", len(_gate(6000)[0]) == 1)
    t.check("⚑ 开赛 110 分钟仍在 grace 与 timeout 之间 → 只是待结算，绝不判超时",
            len(_gate(6600)[0]) == 1 and _gate(6600)[1] == [])
    t.check("⚑ 开赛 183 分钟（**旧的**放弃点）现在仍处于待结算 —— "
            "前三小时正是「打满三图的 Bo3」的尾声，旧值在这里容易误判成延期",
            len(_gate(11000)[0]) == 1 and _gate(11000)[1] == [], _gate(11000))
    t.check("超过 timeout（270 分钟）：判超时放弃", len(_gate(17000)[1]) == 1)
    t.check("已经 reported 的条目不会再被结算",
            split_due([dict(gate, status="reported")], datetime.fromtimestamp(200000, CST), es)
            == ([], []))
    t.check("abandoned 的条目也不会被重复结算",
            split_due([dict(gate, status="abandoned")], datetime.fromtimestamp(200000, CST), es)
            == ([], []))

    # 待结算清单：只增不改、原子写
    st_path = os.path.join(tempfile.gettempdir(), "__es_results_selftest.json")
    if os.path.exists(st_path):
        os.remove(st_path)
    base_now = datetime.fromtimestamp(200000, CST)
    picked_a = [_mk(100000, ["A", "B"], "T", "Bo3"), _mk(100100, ["C", "D"], "T", "Bo3")]
    t.check("首次登记写入 2 场", note_pending(picked_a, base_now, es, st_path) == 2)
    t.check("重复登记同一批不重复写", note_pending(picked_a, base_now, es, st_path) == 0)
    t.check("清单条数正确", len(load_results_pending(st_path)["items"]) == 2)
    t.check("登记的是 pending 状态",
            all(it["status"] == "pending" for it in load_results_pending(st_path)["items"]))
    # 已经报过的场次不能被重新打开（否则二次预告会让战果重发）
    st = load_results_pending(st_path)
    st["items"][0]["status"] = "reported"
    st["items"][0]["reported_at"] = "2026-10-05 20:00:00"
    save_results_pending(st, st_path)
    note_pending(picked_a, base_now, es, st_path)
    st2 = load_results_pending(st_path)
    t.check("⚑ 已 reported 的场次不会被重新打开（战果不会重发）",
            st2["items"][0]["status"] == "reported", st2["items"][0]["status"])
    t.check("清单文件是原子写（不留 .tmp 半成品）", not os.path.exists(st_path + ".tmp"))
    t.check("清单读不出来时给空清单（不炸）",
            load_results_pending(st_path + ".nope") == {"items": []})
    # 过期条目会被清掉
    st3 = {"items": [{"teams": ["X", "Y"], "ts": 100, "bo": "Bo3", "status": "reported"},
                     {"teams": ["P", "Q"], "ts": 100000, "bo": "Bo3", "status": "pending"}]}
    pruned = _prune_pending(st3, datetime.fromtimestamp(100000 + 25 * 3600, CST), es)
    t.check("超过 results_max_age_hours 的旧条目被清掉、pending 的留着",
            len(pruned["items"]) == 1 and pruned["items"][0]["teams"] == ["P", "Q"],
            pruned["items"])

    # ---- ⚑ 同一对队伍「再交手」（2026-10-06 修的 bug）----
    #    旧键只有「排序后的队名」，而 _prune_pending 会把 reported/abandoned 的条目留满
    #    results_max_age_hours（36h）才清 —— 于是同一对队伍在这个窗口内打第二场
    #    （EPL 瑞士轮 + 淘汰赛连着打，很常见）时，第二场被 `key in have` 直接吞掉：
    #    不进待结算、拿不到单场战报、也进不了整合版。
    rr_path = os.path.join(tempfile.gettempdir(), "__es_results_selftest_rr.json")
    if os.path.exists(rr_path):
        os.remove(rr_path)
    t.check("再交手：首场（A vs B）登记成功",
            note_pending([_mk(100000, ["A", "B"], "T", "Bo3")], base_now, es, rr_path) == 1)
    _rr = load_results_pending(rr_path)
    _rr["items"][0]["status"] = "reported"          # 首场已结算并留在窗口内
    _rr["items"][0]["reported_at"] = "2026-10-05 20:00:00"
    save_results_pending(_rr, rr_path)
    t.check("⚑ 同一对队伍结算后再交手（不同 ts）能登记第二场 —— 旧键会把这场吞掉",
            note_pending([_mk(110000, ["A", "B"], "T", "Bo3")], base_now, es, rr_path) == 1)
    _rr2 = load_results_pending(rr_path)
    t.check("⚑ 第二场确实进了清单、且是 pending（reported 的那场也没被抹掉）",
            len(_rr2["items"]) == 2
            and sum(1 for it in _rr2["items"] if it["status"] == "pending") == 1
            and sum(1 for it in _rr2["items"] if it["status"] == "reported") == 1,
            [(it["teams"], it["ts"], it["status"]) for it in _rr2["items"]])
    t.check("同一场（同队 + 同赛事 + 同 ts）重复预告仍被跳过",
            note_pending([_mk(110000, ["A", "B"], "T", "Bo3")], base_now, es, rr_path) == 0)
    t.check("⚑ 同队同赛事仍有 pending 时不重复登记（兜赛事名写法/ts 小幅漂移）",
            note_pending([_mk(110900, ["A", "B"], "T", "Bo3")], base_now, es, rr_path) == 0)
    # 同队、但**赛事名不同**、且前一场还挂着 pending —— 仍然被挡。这是刻意选的保守口径：
    # open_pairs 只按队名，用来兜「同一场被赛事名写法漂移骗过去、重复登记成两条 pending」。
    # 代价是「同队同日两场不同赛事」也会被挡 —— 但那种组合几乎不可能发生，且一旦前一场
    # 结算（reported/abandoned）就立刻解禁，不像旧键要等满 36 小时。而且它会打 [warn]，
    # 不再像旧代码那样**静默**吞掉。
    t.check("⚑ 同队仍有 pending 时，即使赛事名不同也保守跳过（防赛事名漂移重复登记）",
            note_pending([_mk(111000, ["A", "B"], "T2", "Bo3")], base_now, es, rr_path) == 0)
    os.path.exists(rr_path) and os.remove(rr_path)

    # ---- 状态文件的并发安全（2026-10-06 加）----
    #   原子写（tmp + rename）只保证**文件不会写坏**，不保证**不丢更新**：
    #   A 读到 {1}、B 也读到 {1}；A 写 {1,2}、B 写 {1,3} → A 加的那条没了。
    #   线上 `douyu-esports`(09:30 登记) / `-results`(每 10 分钟结算) / `-daily`(09:40 补漏)
    #   是三个独立进程，这条路径真实存在。
    t.check("锁的可用性：本机有 fcntl 就真锁，Windows 上退化成无锁放行（都不该炸）",
            print_lock_note() in ("flock 生效", "无 fcntl，退化成无锁"),
            print_lock_note())
    lk_path = os.path.join(tempfile.gettempdir(), "__es_lock_selftest.json")
    try:
        with file_lock(lk_path):
            _lock_ok = True
    except Exception as exc:  # noqa: BLE001
        _lock_ok = "%s: %s" % (type(exc).__name__, exc)
    t.check("file_lock 当上下文管理器用不会炸（拿不到锁只打 warn、不抛给调用方）",
            _lock_ok is True, _lock_ok)
    t.check("⚑ file_lock 锁的是独立的 .lock 文件，不是状态文件本身"
            "（状态文件靠 rename 替换，锁在被换掉的 inode 上等于没锁）",
            os.path.exists(lk_path + ".lock"))

    up_path = os.path.join(tempfile.gettempdir(), "__es_update_selftest.json")
    if os.path.exists(up_path):
        os.remove(up_path)
    save_results_pending({"items": [
        {"teams": ["A", "B"], "ts": 1000, "bo": "Bo3", "status": "pending"}]}, up_path)
    # 模拟「我们读完之后，另一个进程又登记了一条新场次」
    _conc = load_results_pending(up_path)
    _conc["items"].append({"teams": ["C", "D"], "ts": 2000, "bo": "Bo3",
                           "status": "pending"})
    save_results_pending(_conc, up_path)

    def _mark_ab(d):
        for it in d["items"]:
            if _item_ident(it) == (_teams_key(["A", "B"]), 1000):
                it["status"] = "reported"
        return d

    update_pending(_mark_ab, up_path)
    _after = load_results_pending(up_path)
    t.check("⚑ 锁内读改写不会抹掉「别的进程刚登记的那条」（整体覆盖的旧写法会丢它）",
            len(_after["items"]) == 2, _after["items"])
    t.check("锁内读改写：自己改的那条生效了",
            any(it["status"] == "reported" for it in _after["items"]), _after["items"])
    t.check("⚑ 身份键与 note_pending 的去重判据同源（队名键 + 登记 ts）",
            _item_ident({"teams": ["B", "A"], "ts": 1000})
            == (_teams_key(["A", "B"]), 1000))

    def _noop(d):
        d["sentinel_should_not_be_written"] = 1
        return None                      # 明确表示「无变化、别写盘」

    update_pending(_noop, up_path)
    _raw = json.load(open(up_path, encoding="utf-8"))
    t.check("⚑ apply_fn 返回 None 就不写盘（免得把「只增不改」变成「每轮都重写一遍」）",
            "sentinel_should_not_be_written" not in _raw, list(_raw))

    # 补漏的落盘也必须走锁内合并，不能把调用方手上的旧快照整体覆盖回去
    t.check("⚑ 补漏调 update_pending（锁内合并），不再整体覆盖清单",
            "update_pending" in _call_names(_fn_def("backfill_abandoned")))
    # ⚠️ needle 必须**拼**出来：本自检就写在 esports.py 里，整串写出来的话
    #    它会数到自己（正向断言因此恒假 —— 2026-10-06 第 N 次踩这个坑）。
    _overwrite = "save_results_pending(" + "_prune_pending("
    t.check("⚑ 全文件已无「把旧快照整体覆盖回清单」的裸调用",
            _src_txt.count(_overwrite) == 0, _src_txt.count(_overwrite))

    # ---- 补漏：把被判「延期/取消」的场次捞回来（2026-10-05 因此少发 2 场的修复）----
    day_lo = datetime.fromtimestamp(100000, CST)
    day_hi = datetime.fromtimestamp(200000, CST)
    mixed_st = [dict(gate, status="abandoned"),                 # 窗口内 + abandoned → 该补
                dict(gate, ts=300, status="abandoned"),         # 窗口外 → 不该补
                dict(gate, ts=150000, status="reported")]       # 已报 → 不该补
    t.check("补漏候选：只挑「窗口内 + abandoned」的（纯函数、不联网）",
            [it["ts"] for it in lost_items(mixed_st, day_lo, day_hi)] == [100000],
            [it["ts"] for it in lost_items(mixed_st, day_lo, day_hi)])
    t.check("⚑ 补漏：没有 abandoned 条目时候选为空 —— 正常日「0 网络请求」就是靠这条",
            lost_items([dict(gate, status="reported"),
                        dict(gate, status="pending")], day_lo, day_hi) == [])

    brief = {"teams": ["M80", "TYLOO"], "ts": 100000, "bo": "BO3", "status": "abandoned",
             "reported_at": None, "score": None, "winner": None,
             "shorts": None, "logos": None, "tour": None}
    brow = {"teams": ["M80", "TYLOO"], "shorts": ["M80", "TYLOO"],
            "logos": ["u-m80", "u-tyloo"], "score": "2:1", "winner": "M80",
            "tour": "ESL Pro League Season 24 - Round 3", "bo": "Bo3", "ts": 100030}
    absorb_result(brief, brow, "2026-10-06 09:40:00")
    t.check("absorb_result：状态转 reported，比分/胜方/队标/赛事快照全部落袋"
            "（整合版次日才发，这些快照只能现在存）",
            brief["status"] == "reported" and brief["reported_at"] == "2026-10-06 09:40:00"
            and brief["score"] == "2:1" and brief["winner"] == "M80"
            and brief["logos"] == ["u-m80", "u-tyloo"]
            and brief["tour"] == "ESL Pro League Season 24 - Round 3"
            and brief["bo"] == "Bo3", brief)

    # --check-results 的演练模式：清单空着也能演示（刚部署的服务器就靠它验收）
    t.check("演练条目只取「已结束」的场次（进行中的不能混进来）",
            len(drill_items(rms, 8)) == 2,
            [it["teams"] for it in drill_items(rms, 8)])
    mixed = [_mk(5000, ["E", "F"], "T", "Bo3"),          # 未结束
             _mk(6000, ["G", "H"], "T", "Bo3", finished=True, sides=["W", "L"], score="2:0")]
    t.check("演练条目按时间升序、且只留最近 limit 场",
            [it["ts"] for it in drill_items(mixed, 8)] == [6000])
    many = [_mk(t, [chr(65 + i % 2) + str(i), "Z"], "T", "Bo3",
                finished=True, sides=["W", "L"], score="2:0")
            for i, t in enumerate(range(100, 100 + 20 * 60, 60))]
    t.check("演练条目最多取 DRILL_ROWS 场，并且取的是**最近的**那批",
            [it["ts"] for it in drill_items(many, DRILL_ROWS)]
            == [it["ts"] for it in many[-DRILL_ROWS:]],
            [it["ts"] for it in drill_items(many, DRILL_ROWS)])
    t.check("演练条目全是 pending，且不写成 reported 的样子",
            all(it["status"] == "pending" and it["reported_at"] is None
                for it in drill_items(rms, 8)))
    # ⚑ 演练必须只读：跑完不能凭空造出/改掉清单文件
    _st_before = os.path.exists(RESULTS_STATE_FILE)
    drill_items(rms, 8)
    t.check("⚑ 演练不写 state_results_pending.json（只读，不是真的登记）",
            os.path.exists(RESULTS_STATE_FILE) == _st_before)
    t.check("演练条目能直接喂给 result_rows 出正常行",
            len(result_rows(drill_items(rms, 8), finished_index(rms), es)[0]) == 2)

    # 正文文案
    sample_rows = [{
        "item": item_old, "ts": 1791104400, "teams": ["Team Spirit", "MOUZ"],
        "shorts": ["Spirit", "MOUZ"], "logos": ["", ""], "score": "2:1",
        "score_left": "2", "score_right": "1", "winner": "Team Spirit",
        "bo": "Bo3", "tour": "ESL Pro League Season 24 - Round 3",
    }]
    txt = format_results(sample_rows, datetime.fromtimestamp(1791104400, CST))
    t.check("战果正文带日期（横跨午夜时只写 HH:MM 会分不清哪天）",
            "10-04" in txt and "2:1" in txt, txt)
    t.check("战果正文署名字典里也是 Liquipedia",
            txt.rstrip().endswith("数据来源：Liquipedia"))
    t.check("战果 caption 是一行、带场次数",
            format_results_caption(sample_rows, datetime.fromtimestamp(1791104400, CST))
            .startswith("【CS2 战果】") and "共 1 场" in
            format_results_caption(sample_rows, datetime.fromtimestamp(1791104400, CST)))
    t.check("空列表不发空消息",
            format_results([], base_now) == "" and render_results_card([], base_now, es) is None)

    if Image is not None:
        rpng = render_results_card(sample_rows, base_now, es)
        t.check("战果卡片能出 PNG",
                isinstance(rpng, (bytes, bytearray))
                and bytes(rpng[:8]) == b"\x89PNG\r\n\x1a\n", type(rpng))
        if rpng:
            im = Image.open(io.BytesIO(rpng))
            t.check("战果卡片高度 = 页头 + 行高 × 场次 + 页脚",
                    im.size == (CARD_W, CARD_HDR_H + CARD_ROW_H * 1 + CARD_FTR_H),
                    im.size)
        t.check("⚑ 战果卡片缺字时整张不出（退回纯文本，不画豆腐块）",
                render_results_card(
                    [dict(sample_rows[0], teams=["測試隊", "MOUZ"],
                          shorts=["測試隊", "MOUZ"])], base_now, es) is None)
        t.check("战果卡片在「两边队标都没有」时照样出图（画占位块）",
                render_results_card(sample_rows, base_now,
                                    dict(es, logo_max_new_per_run=0)) is not None)
        t.check("「战果」两个字在固定字符表里",
                "战" in CARD_UI_CHARS and "果" in CARD_UI_CHARS)
        t.check("周一到周日在固定字符表里（战果卡片页头要写 `10-05 周一`）",
                all(c in CARD_UI_CHARS for c in "周一二三四五六日"))
        t.check("战果相关的字都在字体子集里（缺字会静默退回纯文本）",
                card_missing_chars(["CS2 战果", "10-05 周一", "共 2 场", "MOUZ 2:1 9z"]) == set())

    # ---- 3d. 条款节流闸门（跨进程的「1 次 / 30 秒」）----
    print("\n-- 3d. 条款节流（action=parse ≤ 1 次 / 30 秒）--")
    t.check("刚抓过 → 要等满一个间隔", parse_gate_delay(1000.0, 1000.0, 30) == 30.0)
    t.check("过了 12 秒 → 再等 18 秒", parse_gate_delay(1000.0, 1012.0, 30) == 18.0)
    t.check("早就超过间隔 → 不等", parse_gate_delay(1000.0, 1999.0, 30) == 0.0)
    t.check("从没抓过（时间戳为 0）→ 不等", parse_gate_delay(0, 12345.0, 30) == 0.0)
    t.check("间隔配成 0 → 等于关掉节流", parse_gate_delay(1000.0, 1000.0, 0) == 0.0)
    # ⚑ 时钟回拨 / 别的机器写了未来时间：只等一个完整间隔，
    #   否则会算出一个天文数字的 sleep，把这一轮卡到 systemd 的 TimeoutStartSec。
    t.check("⚑ 时间戳在未来（时钟回拨）也只等一个间隔",
            parse_gate_delay(2000.0, 1000.0, 30) == 30.0)
    t.check("节流时间戳文件不存在时不炸",
            _load_parse_stamp(os.path.join(HERE, "__no-such-stamp__.json")) == 0.0)

    # ---- 3e. 全天整合版的赛程日窗口 ----
    print("\n-- 3e. 全天整合版（窗口 / 取行）--")
    at_0940 = datetime(2026, 10, 6, 9, 40, tzinfo=CST)
    ds, de = last_schedule_day(at_0940, "09:30")
    t.check("⚑ 09:40 跑 → 汇总「昨天 09:30 → 今天 09:30」",
            (ds.strftime("%m-%d %H:%M"), de.strftime("%m-%d %H:%M"))
            == ("10-05 09:30", "10-06 09:30"), (ds, de))
    # 两者务必不能混：preview_window 给的是「即将开始的 24 小时」，正好跳过要汇总的那天。
    t.check("⚑ 和预告窗口是两回事（preview_window 给的是 10-07 09:30）",
            preview_window(at_0940, "09:30")[1].strftime("%m-%d %H:%M") == "10-07 09:30")
    # 记下这个坑：整合版若在预告时刻**之前**跑，右端点会退到昨天，汇总的就不是「刚过完」那天。
    t.check("⚑ 若在 09:30 之前跑，窗口会退成「前天 → 昨天」（所以要显式挡）",
            last_schedule_day(datetime(2026, 10, 6, 9, 20, tzinfo=CST),
                              "09:30")[1].strftime("%m-%d %H:%M") == "10-05 09:30")

    lo, hi = int(ds.timestamp()), int(de.timestamp())
    _dit = [
        {"teams": ["Legacy", "PARIVISION"], "ts": lo + 3600, "status": "reported",
         "score": "1:2", "winner": "PARIVISION", "shorts": ["Legacy", "PARIVISION"],
         "logos": ["", ""], "tour": "ESL Pro League Season 24 - Round 1", "bo": "Bo3"},
        {"teams": ["FURIA", "MOUZ"], "ts": hi - 3600, "status": "reported",
         "score": "2:0", "winner": "FURIA", "shorts": ["FURIA", "MOUZ"],
         "logos": ["", ""], "tour": "T", "bo": "Bo3"},
        {"teams": ["A", "B"], "ts": lo - 3600, "status": "reported",
         "score": "2:0", "winner": "A"},                       # 窗口之前 → 不算
        {"teams": ["C", "D"], "ts": lo + 7200, "status": "pending",
         "score": "", "winner": ""},                           # 还没出结果 → 不算
        {"teams": ["E", "F"], "ts": lo + 10800, "status": "abandoned",
         "score": "", "winner": ""},                           # 延期/取消 → 不算
        {"teams": ["G", "H"], "ts": lo + 14400, "status": "reported",
         "score": "2:?", "winner": "G"},                       # 比分不是数字 → 不算
        {"teams": ["I", "J"], "ts": lo + 18000, "status": "reported",
         "score": "2:0", "winner": "ZZZ"},                     # 胜方不在两队里 → 不算
    ]
    drows = daily_rows(_dit, ds, de, es)
    t.check("⚑ 整合版只收「窗口内 + 已 reported + 比分成形」的场次",
            len(drows) == 2 and [r["teams"][0] for r in drows] == ["Legacy", "FURIA"],
            [r["teams"] for r in drows])
    t.check("整合版按时间升序", [r["ts"] for r in drows] == sorted(r["ts"] for r in drows))
    t.check("整合版不带逐图（用户选的版式 C 就是不带）",
            all(r["maps"] == [] for r in drows))
    t.check("整合版在没有可入册场次时返回空（→ 不发空消息）",
            daily_rows([], ds, de, es) == [])
    t.check("空清单也能算窗口（不炸）",
            last_schedule_day(at_0940, None)[1].strftime("%H:%M") == "09:30")

    # ---- 3f. 单场战报（逐图比分 + 版式 A）----
    print("\n-- 3f. 单场战报（逐图比分解析 / 版式 A）--")
    evs = parse_event_maps(EVENT_FIXTURE_HTML)
    t.check("赛事页切出 1 个弹窗（只有一边队名的那个被跳过）", len(evs) == 1, len(evs))
    if len(evs) == 1:
        e0 = evs[0]
        t.check("弹窗队名取 team-name（不是 title，否则会拼三遍）",
                e0["teams"] == ["Legacy", "PARIVISION"], e0["teams"])
        t.check("弹窗系列比分", e0["series"] == ["1", "2"], e0["series"])
        t.check("弹窗时间戳", e0["ts"] == 1791018000, e0["ts"])
        t.check("弹窗 finished 标记", e0["finished"] is True)
        t.check("⚑ 没打的地图（比分栏是空的）被丢掉，只留 3 张",
                [m["map"] for m in e0["maps"]] == ["Dust II", "Inferno", "Ancient"],
                [m["map"] for m in e0["maps"]])
        t.check("逐图回合比分", [m["rounds"] for m in e0["maps"]]
                == [["13", "5"], ["2", "13"], ["12", "16"]],
                [m["rounds"] for m in e0["maps"]])
        t.check("弹窗里的 HLTV 比赛页 id",
                e0["hltv_id"] == "2398718", e0["hltv_id"])
        idx = event_maps_index(evs)
        t.check("逐图索引按 (时间戳, 队名键) 配对",
                (1791018000, _teams_key(["PARIVISION", "Legacy"])) in idx)
        t.check("⚑ 索引必须带时间戳（同两队可能打好几轮，只按队名配会张冠李戴）",
                (1791018001, _teams_key(["Legacy", "PARIVISION"])) not in idx)
        t.check("没有逐图的场次不进索引",
                event_maps_index([{"ts": 1, "teams": ["A", "B"], "maps": []}]) == {})

    t.check("逐图按每图胜负上色：左队赢的那图 → 左侧是胜色",
            _round_win(["13", "5"], 0) is True and _round_win(["13", "5"], 1) is False)
    t.check("逐图按每图胜负上色：右队赢的那图 → 右侧是胜色",
            _round_win(["2", "13"], 0) is False and _round_win(["2", "13"], 1) is True)
    t.check("⚑ 比分拿不到时两边都不算赢（不抛异常、不把卡片整张搞没）",
            _round_win(["", ""], 0) is False and _round_win([], 1) is False)

    srow = {
        "item": item_old, "ts": 1791104400, "teams": ["Legacy", "PARIVISION"],
        "shorts": ["Legacy", "PARIVISION"], "logos": ["", ""],
        "score": "1:2", "score_left": "1", "score_right": "2",
        "score_source": "series", "winner": "PARIVISION", "bo": "Bo3",
        "tour": "ESL Pro League Season 24 - Round 1",
        "tour_page": "ESL/Pro League/Season 24",
        "maps": [{"map": "Dust II", "rounds": ["13", "5"]},
                 {"map": "Inferno", "rounds": ["2", "13"]},
                 {"map": "Ancient", "rounds": ["12", "16"]}],
    }
    scap = format_result_caption(srow, base_now)
    t.check("单场战报 caption 是一行、带对阵与比分",
            scap.startswith("【CS2 战报】") and "Legacy" in scap and "1:2" in scap
            and "PARIVISION" in scap, scap)
    stxt = format_result(srow, base_now)
    t.check("纯文本兜底里带逐图（这正是单场相对整合版的价值）",
            "Dust II" in stxt and "地图 1" in stxt and "13:5" in stxt, stxt)
    t.check("单场正文末行署名 Liquipedia",
            stxt.rstrip().splitlines()[-1] == "数据来源：Liquipedia",
            stxt.rstrip().splitlines()[-1])
    t.check("单场 caption 用**比赛时间**而不是当前时间（跨午夜结算才不会串日期）",
            datetime.fromtimestamp(1791104400, CST).strftime("%m-%d") in scap, scap)

    if Image is not None:
        spng = render_result_card(srow, base_now, pill)
        t.check("单场战报卡片能出 PNG",
                isinstance(spng, (bytes, bytearray))
                and bytes(spng[:8]) == b"\x89PNG\r\n\x1a\n", type(spng))
        if spng:
            im = Image.open(io.BytesIO(spng))
            t.check("单场战报高度 = 页头 + 对阵行 + 逐图 × 3 + 页脚",
                    im.size == (CARD_W, CARD_HDR_H + CARD_FIX_H + CARD_MAP_H * 3
                                + CARD_FTR_H), im.size)
        # ⚑ 抓不到逐图时的降级路径：卡片照样出，只是矮了 3 行 —— 绝不能因此不发。
        nomap = render_result_card(dict(srow, maps=[]), base_now, pill)
        t.check("⚑ 没有逐图数据照样出图（只是矮一截，不是失败）",
                isinstance(nomap, (bytes, bytearray)))
        if nomap:
            im2 = Image.open(io.BytesIO(nomap))
            t.check("没有逐图时高度 = 页头 + 对阵行 + 页脚",
                    im2.size == (CARD_W, CARD_HDR_H + CARD_FIX_H + CARD_FTR_H), im2.size)
        t.check("单场战报缺字时整张不出（退回纯文本，不画豆腐块）",
                render_result_card(dict(srow, teams=["測試隊", "PARIVISION"],
                                        shorts=["測試隊", "PARIVISION"]),
                                   base_now, pill) is None)
        t.check("单场战报在「两边队标都没有」时照样出图（画占位块）",
                render_result_card(srow, base_now,
                                   dict(pill, logo_max_new_per_run=0)) is not None)
        # 这一条是 2026-10-05 预览时真踩到的：忘了补「报」「地」，整张卡片静默消失。
        t.check("⚑ 单场战报要画的中文全在字体子集里（报 / 地 最容易漏）",
                card_missing_chars(["CS2 战报", "地图 1", "地图 3", "Dust II",
                                    "PARIVISION"]) == set())
        t.check("「报」「地」在固定字符表里",
                "报" in CARD_UI_CHARS and "地" in CARD_UI_CHARS)

    # ---- 3f-2. HTML 大图（1920×1080）：V2 版式，三级降级的头一级 ----
    print("\n-- 3f-2. HTML 大图（V2 版式 / Chromium 截图 / 三级降级头一级）--")

    def _png_wh(b):
        # PNG IHDR：宽高在字节 16~24，大端两个 uint32。
        # 定义在 3f-2 开头 —— 3f-3 也要用它；嵌套 def 在同函数内后置会
        # 让前面的引用吃 UnboundLocalError（局部名遮蔽），所以必须前置。
        import struct as _struct
        return (_struct.unpack(">II", bytes(b[16:24]))
                if b and len(b) > 24 else (0, 0))

    t.check("chrome_bin 钉死到不存在的路径 → find_chrome 只认它、返回 None",
            find_chrome(dict(es, chrome_bin="/nonexistent/chrome")) is None)
    t.check("chrome_bin 没配时探测不炸（返回路径或 None 都是合法结果）",
            find_chrome(es) is None or isinstance(find_chrome(es), str))
    t.check("snap 装的 /snap/bin/chromium 在探测候选里（systemd PATH 无 /snap/bin 也能找到）",
            "/snap/bin/chromium" in _CHROME_CANDIDATES)
    _probe = os.path.join(tempfile.gettempdir(), "es_chrome_probe.bin")
    try:
        open(_probe, "w").close()
        t.check("chrome_bin 钉死到存在的文件 → 原样返回（isfile 分支）",
                find_chrome(dict(es, chrome_bin=_probe)) == _probe)
    finally:
        try:
            os.remove(_probe)
        except OSError:
            pass
    # 每份模板：字体占位符必须在（少了 → @font-face 404 → 静默回退系统字体），
    # 自己的数据占位符也必须在（少了 → 注入不进去 → 卡片空白），且不能残留
    # **别的**模板的占位符（那是复制粘贴改漏的信号）。
    _TPL_SPECS = (
        ("result_template.html", "__MATCH__"),
        ("daily_template.html", "__DAILY__"),
        ("daily_results_template.html", "__RESULTS__"),
        ("preview_template.html", "__MATCH__"),
    )
    for _tpl_name, _ph in _TPL_SPECS:
        with open(os.path.join(HERE, _tpl_name), encoding="utf-8") as _tf:
            _tpl_txt = _tf.read()
        _others = [p for _n, p in _TPL_SPECS if p != _ph]
        t.check("模板 %s：__FONTDIR__ 占位符在、且没有 file:/// 双前缀（会静默回退系统字体）"
                % _tpl_name,
                "__FONTDIR__" in _tpl_txt
                and "file:///__FONTDIR__" not in _tpl_txt)
        t.check("模板 %s：%s 占位符在、且没有别的模板的占位符残留"
                % (_tpl_name, _ph),
                _ph in _tpl_txt and not any(o in _tpl_txt for o in _others))
        t.check("模板 %s：署名已随仓库改名更新（大小写都不留 douyu）" % _tpl_name,
                "douyu" not in _tpl_txt.lower())
    t.check("随包字体三件套都在 HTML_FONT_DIR（缺了 @font-face 404 → 系统字体）",
            all(os.path.isfile(os.path.join(HTML_FONT_DIR, _fn)) for _fn in
                ("BebasNeue-Regular.ttf", "IBMPlexMono-Regular.ttf",
                 "IBMPlexMono-SemiBold.ttf")),
            HTML_FONT_DIR)
    hrow = dict(srow, players=[
        {"map": "Dust II", "players": [
            {"name": "pA", "team": "Legacy", "k": 20, "d": 18, "a": 5, "pm": 2,
             "adr": 88.4, "kast": 75, "rating": 1.21},
            {"name": "pB", "team": "PARIVISION", "k": 25, "d": 16, "a": 3, "pm": 9,
             "adr": 96.2, "kast": 81, "rating": 1.45}]},
        {"map": "Inferno", "players": [
            {"name": "pA", "team": "Legacy", "k": 10, "d": 22, "a": 2, "pm": -12,
             "adr": 61.0, "kast": 55, "rating": 0.72},
            {"name": "pC", "team": "PARIVISION", "k": 22, "d": 12, "a": 6, "pm": 10,
             "adr": 92.8, "kast": 79, "rating": 1.38}]}])
    hm = build_result_match(hrow, es)
    t.check("V2 数据：比分拆列、赛事按「 - 」拆名与赛段",
            hm["teamA"]["score"] == 1 and hm["teamB"]["score"] == 2
            and hm["event"] == "ESL Pro League Season 24"
            and hm["stage"] == "ROUND 1",
            (hm["teamA"]["score"], hm["event"], hm["stage"]))
    t.check("V2 数据：逐图回合数转数字、编号从 1 起",
            [mp["teamA"] for mp in hm["maps"]] == [13, 2, 12]
            and hm["maps"][0]["number"] == 1, hm["maps"])
    t.check("V2 数据：MVP 取跨图聚合后 rating 最高的（pB 1.45）",
            hm["mvp"]["name"] == "pB" and abs(hm["mvp"]["rating"] - 1.45) < 1e-9,
            hm["mvp"])
    t.check("V2 数据：跨图聚合 K 累加（20+10）、ADR 取平均（(88.4+61.0)/2）",
            hm["players"]["teamA"][0]["k"] == 30
            and abs(hm["players"]["teamA"][0]["adr"] - 74.7) < 1e-9,
            hm["players"]["teamA"])
    t.check("V2 数据：mvp_basis = 真有选手数据的图数（不是 maps 长度）",
            hm["mvp_basis"] == 2, hm["mvp_basis"])
    t.check("V2 数据：日期取比赛时间不是当前时间",
            hm["date"] == datetime.fromtimestamp(1791104400, CST).strftime("%Y-%m-%d"),
            hm["date"])
    t.check("V2 数据：赛制转大写（Bo3 → BO3）", hm["format"] == "BO3", hm["format"])
    hm0 = build_result_match(dict(srow, maps=[], players=[]), es)
    t.check("⚑ V2 数据：没逐图没选手也不炸（空列表 + 空 MVP，模板走居中形态）",
            hm0["maps"] == [] and hm0["mvp"] is None
            and hm0["players"] == {"teamA": [], "teamB": []}, (hm0["maps"], hm0["mvp"]))

    if find_chrome(es):
        hpng = render_result_card_html(hrow, es)
        t.check("有 Chromium 时单场战报出 1920×1080 HTML 大图（PNG 魔数 + 实际尺寸）",
                isinstance(hpng, (bytes, bytearray))
                and bytes(hpng[:8]) == b"\x89PNG\r\n\x1a\n"
                and _png_wh(hpng) == (1920, 1080),
                _png_wh(hpng) if hpng else type(hpng))
        dpng = render_card_html(many[:2], base, es)
        t.check("有 Chromium 时总预告出 1920×1080 HTML 大图（PNG 魔数 + 实际尺寸）",
                isinstance(dpng, (bytes, bytearray))
                and bytes(dpng[:8]) == b"\x89PNG\r\n\x1a\n"
                and _png_wh(dpng) == (1920, 1080),
                _png_wh(dpng) if dpng else type(dpng))
        hpng0 = render_result_card_html(dict(srow, maps=[], players=[]), es)
        t.check("⚑ 没逐图没选手的 HTML 大图照样出（居中形态，不是失败）",
                isinstance(hpng0, (bytes, bytearray))
                and _png_wh(hpng0) == (1920, 1080),
                _png_wh(hpng0) if hpng0 else type(hpng0))
    else:
        t.check("没 Chromium 时 HTML 卡安静退回 None（再退 Pillow/纯文本）",
                render_result_card_html(srow, es) is None)

    # ---- 3f-2b. 全天整合版 V2（本来就是唯一只有 Pillow 的那条，补 HTML 层）----
    print("\n-- 3f-2b. 全天整合版 V2（HTML 1920×1080 / 三级降级补齐）--")

    def _caprow(i):
        """整合版/预告通用的一行。**logos 必须是空串** —— 自检绝不能联网下队标。"""
        return {"ts": 100000 + i * 3600,
                "teams": ["T%02dA" % i, "T%02dB" % i],
                "shorts": ["T%02dA" % i, "T%02dB" % i], "logos": ["", ""],
                "winner": "T%02dA" % i, "score": "2:1", "bo": "Bo3",
                "tour": "T", "maps": []}

    # _dit / ds / de 来自 3e：两场已结算（1:2 右胜 + 2:0 左胜）
    rdrows = daily_rows(_dit, ds, de, es)
    rdat = build_daily_results_data(rdrows, ds, es)

    # ---- 整合版行数上限：**只准有一个数**（2026-10-06 对齐）----
    #   原来三处各说各话：`daily_max_rows`=24、模板 22 行起切版面（23 行起直接拒绝）、
    #   880px 旧卡 12 行。于是 22~24 场的日子会「HTML 拒绝 → 退旧卡只剩 12 行」，
    #   而 22 场时会硬画一张被 overflow:hidden 切掉最后一列的图。
    #   那个 21 是**量出来的**（deploy/measure_img_capacity.py，改动模板后重跑一遍）。
    t.check("⚑ daily_max_rows 的默认值 == HTML_IMG_MAX_ROWS（量出来的模板容量，"
            "两处写不一样的数就是这次要修的 bug）",
            ESPORT_DEFAULTS["daily_max_rows"] == HTML_IMG_MAX_ROWS,
            (ESPORT_DEFAULTS["daily_max_rows"], HTML_IMG_MAX_ROWS))
    # ⚠️ 下面两条要读 `config.example.json` 和 `douyu-esports-daily.service`，
    #    而这两个文件**只在仓库 / 部署包里**：`install-watch.sh` 装到服务器上之后
    #    只有 *.py + 模板 + 字体（unit 进 /etc/systemd/system，示例配置不装）。
    #    所以先看文件在不在，不在就 [skip] 说明，**不能直接 open()** ——
    #    2026-10-06 首次部署这一版时就是这么崩的：包内 407 项全绿，
    #    装好的目录里一条路径就 FileNotFoundError，整个自检中断。
    #    自检必须在两种布局下都能跑完，这是硬要求。
    _ex_path = os.path.join(HERE, "config.example.json")
    _svc_path = os.path.join(HERE, "douyu-esports-daily.service")
    if not (os.path.isfile(_ex_path) and os.path.isfile(_svc_path)):
        t.note("示例配置 / daily.service 不在本目录（装好的机器就是这样）—— "
               "「示例配置的 daily_max_rows」「示例配置的 card_max_rows」"
               "「service 注释写的是 %d 行」这 3 条跳过。"
               "想跑满就在仓库或解开 deploy.zip 的目录里跑。" % HTML_IMG_MAX_ROWS)
    else:
        _cfg_ex = json.load(open(_ex_path, encoding="utf-8")).get("esports") or {}
        t.check("⚑ config.example.json 里的 daily_max_rows 也是这个数"
                "（示例配置是用户复制出去改的那份，它写 24 就等于没修）",
                _cfg_ex.get("daily_max_rows") == HTML_IMG_MAX_ROWS,
                _cfg_ex.get("daily_max_rows"))
        t.check("config.example.json 里的 card_max_rows 与代码默认值一致"
                "（示例写 12、代码写 12，别只改一处）",
                _cfg_ex.get("card_max_rows") == ESPORT_DEFAULTS["card_max_rows"],
                (_cfg_ex.get("card_max_rows"), ESPORT_DEFAULTS["card_max_rows"]))
        _svc = open(_svc_path, encoding="utf-8").read()
        t.check("⚑ daily.service 的注释也说的是这个数，且不再说「24 行」"
                "（注释里的数是排查时唯一能看到的线索）",
                ("%d 行" % HTML_IMG_MAX_ROWS) in _svc and "24 行" not in _svc)

    # ---- 把上面那层守卫本身也钉住 ----
    # 规则：凡是「读只在仓库/包里才有的文件」的 open()，都必须落在带 `isfile` 判定的分支里。
    # 分两步做，**不能只认字面量** —— 代码里路径都存进了变量（`_ex_path`），
    # 所以 `open(_ex_path)` 的源码片段里根本没有文件名：
    #   ① 先找出「哪些变量被赋成了 os.path.join(HERE, "<只在仓库里才有的文件名>")」；
    #   ② 再从 selftest 根节点往下走，一路记住「当前是否处在带 isfile 的分支中」，
    #      凡是 open() 的实参用到①那些变量、却不在这种分支里的，就记一笔。
    # 第①步特意**不看 isfile** —— 否则守卫一改名（isfile→exists）两条会一起消失，
    # 断言又变成恒真。第一版写成「在 open() 片段里搜文件名」就是这么废掉的（变异测试抓到）。
    _repo_only_files = ("config.example.json", "douyu-esports-daily.service")
    _st_def = _fn_def("selftest")

    _repo_path_vars = set()
    for _n in (ast.walk(_st_def) if _st_def is not None else []):
        if not (isinstance(_n, ast.Assign) and len(_n.targets) == 1
                and isinstance(_n.targets[0], ast.Name)):
            continue
        _v = _n.value
        if (isinstance(_v, ast.Call) and isinstance(_v.func, ast.Attribute)
                and _v.func.attr == "join"
                and any(isinstance(_a, ast.Constant) and _a.value in _repo_only_files
                        for _a in _v.args)):
            _repo_path_vars.add(_n.targets[0].id)

    def _walk_opens(node, guarded, bad):
        if isinstance(node, ast.If) and "isfile" in _call_names(node.test):
            guarded = True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            _used = {_a.id for _a in node.args if isinstance(_a, ast.Name)}
            if _used & _repo_path_vars and not guarded:
                bad.append(node.lineno)
        for _ch in ast.iter_child_nodes(node):
            _walk_opens(_ch, guarded, bad)

    _bad_open = []
    if _st_def is not None:
        _walk_opens(_st_def, False, _bad_open)
    t.check("⚑ 「只在仓库/包里才有」的那几个文件，自检读它们之前必须先 isfile 挡一道"
            "（否则装好的机器上 FileNotFoundError 会让整个自检在**中途**断掉"
            " —— 2026-10-06 首次部署这一版就是这么崩的：包内全绿、服务器上一条路径就炸）",
            bool(_repo_path_vars) and not _bad_open,
            "受管变量=%s 未加守卫的 open 行号=%s"
            % (sorted(_repo_path_vars), _bad_open))

    _cap_ok = build_daily_results_data(
        [_caprow(i) for i in range(HTML_IMG_MAX_ROWS)], ds, es)
    _cap_no = build_daily_results_data(
        [_caprow(i) for i in range(HTML_IMG_MAX_ROWS + 1)], ds, es)
    t.check("⚑ 整合版模板数据：正好 %d 场要能出图、%d 场要**拒绝**"
            "（22 行实测溢出 8px —— 硬画就是切版面，拒绝才是对的）"
            % (HTML_IMG_MAX_ROWS, HTML_IMG_MAX_ROWS + 1),
            _cap_ok is not None and len(_cap_ok["matches"]) == HTML_IMG_MAX_ROWS
            and _cap_no is None,
            (None if _cap_ok is None else len(_cap_ok["matches"]), _cap_no))
    t.check("⚑ 总预告模板数据：同一个上限（两张模板是同一套几何，见常量注释）",
            build_daily_data([_caprow(i) for i in range(HTML_IMG_MAX_ROWS)],
                             ds, es) is not None
            and build_daily_data(
                [_caprow(i) for i in range(HTML_IMG_MAX_ROWS + 1)],
                ds, es) is None)
    # 880px 旧卡是**另一个画布**，容量天然不同 —— 别为了「看起来一致」把两个数合并。
    t.check("⚑ card_max_rows(880px 旧卡) 与 HTML_IMG_MAX_ROWS(1920×1080 模板)"
            "是两个独立的数，都 > 0 且旧卡更小（合并它们 = 要么切版面、要么白浪费版面）",
            ESPORT_DEFAULTS["card_max_rows"] > 0
            and ESPORT_DEFAULTS["card_max_rows"] < HTML_IMG_MAX_ROWS,
            (ESPORT_DEFAULTS["card_max_rows"], HTML_IMG_MAX_ROWS))

    t.check("整合版 V2 数据：比分拆成两侧、win 只落在赢的那一边",
            rdat is not None and len(rdat["matches"]) == 2
            and (rdat["matches"][0]["teamA"]["score"],
                 rdat["matches"][0]["teamB"]["score"]) == ("1", "2")
            and rdat["matches"][0]["teamA"]["win"] is False
            and rdat["matches"][0]["teamB"]["win"] is True
            and rdat["matches"][1]["teamA"]["win"] is True
            and rdat["matches"][1]["teamB"]["win"] is False,
            [(m["teamA"]["win"], m["teamB"]["win"])
             for m in (rdat or {}).get("matches", [])])
    t.check("整合版 V2 数据：dayLabel 用赛程日（和早上那条预告同名，便于对照）",
            rdat["dayLabel"] == _day_label(ds), rdat["dayLabel"])
    t.check("整合版 V2 数据：赛事名去重进 events、bo 转大写",
            rdat["events"] and len(rdat["events"]) == len(set(rdat["events"]))
            and rdat["matches"][0]["bo"] == "BO3", rdat["events"])
    t.check("整合版 V2 数据：空场次返回 None（→ 上层退 Pillow/纯文本，绝不硬画）",
            build_daily_results_data([], ds, es) is None)
    t.check("整合版 V2 数据：超过 HTML_IMG_MAX_ROWS 场返回 None（版式兜不住就退旧卡）"
            "—— 上限用常量而不是字面量，免得常量改了这条还在测 23",
            build_daily_results_data(
                [_caprow(i) for i in range(HTML_IMG_MAX_ROWS + 1)], ds, es) is None)
    t.check("整合版 V2 数据：比分不是「两个数字」→ 用 ? 占位（不画假比分）",
            build_daily_results_data([dict(rdrows[0], score="2:?")], ds, es)
            ["matches"][0]["teamB"]["score"] == "?")
    t.check("⚑ 整合版 V2 数据：generatedAt 用「实际生成时刻」、dayLabel 用赛程日"
            "（两者不是一个时间，合一会把窗口起点写成「最后更新」）",
            build_daily_results_data(
                rdrows, ds, es,
                datetime(2026, 10, 6, 9, 40, tzinfo=CST))["generatedAt"] == "09:40"
            and rdat["generatedAt"] == ds.strftime("%H:%M")
            and rdat["dayLabel"] == _day_label(ds),
            rdat["generatedAt"])
    t.check("⚑ run_daily 的卡片入口是 render_daily_results_card（三级降级的头一级）",
            "render_daily_results_card(shown, label_dt, es, gen_at=now)"
            in _src_txt)
    # ⚠️ 禁用串必须**拼**出来：本自检就写在 esports.py 里，直接写成字面量的话
    #    它会把自己匹配到（2026-10-06 真踩了 —— 断言因此恒假）。
    _bad_call = ("render_daily_results_card(shown, label_dt, es, gen_at="
                 + "label_dt)")
    t.check("⚑ run_daily 把**真实时刻**（now）传给了 gen_at，不是 label_dt",
            "gen_at=now)" in _src_txt and _bad_call not in _src_txt)
    # ⚠️ 下面这几个 needle 都必须**拼**出来：本自检就写在 esports.py 里，
    #    整串写出来的话 needle 会把自己匹配到 —— 正向 `in` 变成恒真、
    #    反向 `not in` 变成恒假（2026-10-06 已踩三次，见文件里另外两处注解）。
    _bf_call = ("backfill_abandoned(data[\"items\"], start, end, es, "
                "dry=check)")
    t.check("⚑ run_daily 出汇总前先跑一次补漏（否则被判延期的场次永远补不回来，"
            "2026-10-05 就是这么少了 2 场）", _bf_call in _src_txt)
    _bf_def = ("def backfill_abandoned(items, start, end, es, "
               "dry=False)")
    t.check("⚑ --check-daily 的补漏只改内存：dry=check → 演练时不落盘",
            _bf_def in _src_txt and _bf_call in _src_txt)
    # 补漏不往外发消息，日志里就不能出现 [sent] —— 否则排查时会被当成「已经发出去了」。
    _bad_sent = "[sent]" + " 补漏"
    t.check("⚑ 补漏的日志不许标 [sent]（它只改清单，真正发出去的是那条整合版汇总）",
            _bad_sent not in _src_txt)

    # ---- 源码结构（AST）：战报「送达后立刻落盘」 ----
    # 这里**故意用 ast 而不是字符串 needle**：needle 写在本文件里会把自己匹配到
    # （见上面 _bad_call / _bf_call 的注解，2026-10-06 已踩三次）。AST 按语法树问
    # 「谁在谁后面」，天然没有自指问题。
    # ⚠️ `_src_txt` / `_ast_tree` / `_call_names` / `_fn_def` 都在 selftest 开头定义好，
    #    这里直接复用 —— 别在本块里再 `_ast_tree = ast.parse(...)` 一次。
    _rr = _fn_def("run_results")
    t.check("⚑ 找得到 run_results（找不到说明函数被改名，下面两条会失去意义）",
            _rr is not None)

    # ⚠️ 不能用「第一个 `for … in rows`」来认这个循环：run_results 里前面还有一个
    #    **只打印清单**的 `for row in rows`（连它一起数的话，下面两条会认错循环、
    #    变成恒假 —— 2026-10-06 就是这么先失败了一次）。按内容认：含 send_with_retry 的那个。
    _send_loops = [n for n in (ast.walk(_rr) if _rr is not None else [])
                   if isinstance(n, ast.For) and "send_with_retry" in _call_names(n)]
    t.check("⚑ run_results 里找得到「逐场发送」的 for 循环，且只有一个"
            "（剥掉它下面两条就变成空断言）",
            len(_send_loops) == 1, [n.lineno for n in _send_loops])
    _calls = _call_names(_send_loops[0]) if _send_loops else {}
    _need = ("send_with_retry", "_persist")
    t.check("⚑ 逐场发送循环里同时有「发送」和「落盘」两个动作",
            all(k in _calls for k in _need),
            {k: _calls.get(k) for k in _need})
    # 顺序就是修复本身：消息先出去 → 立刻落盘。
    # 攒到整轮末尾才落盘的话，「已发出但状态没保存」会让整轮战报下一轮重发一遍。
    t.check("⚑ 落盘在**送达之后**：_persist 的行号大于 send_with_retry",
            all(k in _calls for k in _need)
            and min(_calls["_persist"]) > min(_calls["send_with_retry"]),
            {k: _calls.get(k) for k in _need})
    # 落盘必须走「锁内重读 + 按身份键合并」，不能把旧快照整体写回。
    _persist_fn = next((n for n in ast.walk(_rr) if isinstance(n, ast.FunctionDef)
                        and n.name == "_persist"), None) if _rr is not None else None
    _pc = _call_names(_persist_fn)
    t.check("⚑ run_results 的 _persist 是「锁内读改写」：调 update_pending"
            "（不是直接 save_results_pending 覆盖整份）",
            "update_pending" in _pc and "save_results_pending" not in _pc, sorted(_pc))
    t.check("⚑ _persist 里按身份键合并（_absorb_rows），不是整体覆盖",
            "_absorb_rows" in _pc, sorted(_pc))
    _absorb_fn = _fn_def("_absorb_rows")
    t.check("⚑ _absorb_rows 里真的调了 absorb_result（否则合并等于什么都没记）",
            _absorb_fn is not None
            and "absorb_result" in _call_names(_absorb_fn))
    # 提醒锁同理：送达一条写一条，而不是攒到末尾
    _ann_fn = _fn_def("run_announce")
    _ann_loops = [n for n in (ast.walk(_ann_fn) if _ann_fn is not None else [])
                  if isinstance(n, ast.For) and "send_with_retry" in _call_names(n)]
    t.check("⚑ 开赛提醒的发送循环也「送达后立刻写锁」（update_announce）",
            len(_ann_loops) == 1
            and "update_announce" in _call_names(_ann_loops[0]),
            [n.lineno for n in _ann_loops])

    # 补漏必须显式关掉「比分配不上赛制」的闸门：那是最后一道兜底，
    # 在那里再拦一次的话，边角情况（页面标错赛制/弃权判负）会让一整场从汇总里消失，
    # 而汇总「少一场」正是 2026-10-05 用户报的那个故障。
    _bf_fn = _fn_def("backfill_abandoned")
    t.check("⚑ 找得到 backfill_abandoned（找不到说明改名了，下面一条会失去意义）",
            _bf_fn is not None)
    _bf_strict = []
    for _n in (ast.walk(_bf_fn) if _bf_fn is not None else []):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                and _n.func.id == "result_rows"):
            for _kw in _n.keywords:
                _bf_strict.append(ast.literal_eval(_kw.value)
                                  if (isinstance(_kw.value, ast.Constant)) else None)
    t.check("⚑ 补漏调用 result_rows 时显式传 strict=False"
            "（否则边角情况会把整场从汇总里丢掉）",
            _bf_strict == [False], _bf_strict)
    t.check("⚑ 补漏落盘走 update_pending（锁内重读 + 按身份键合并），"
            "不再把调用方手上的旧快照整体覆盖回清单",
            "update_pending" in _call_names(_bf_fn), sorted(_call_names(_bf_fn)))

    if find_chrome(es):
        rdpng = render_daily_results_card_html(rdrows, ds, es)
        t.check("有 Chromium 时整合版出 1920×1080 HTML 大图（PNG 魔数 + 实际尺寸）",
                isinstance(rdpng, (bytes, bytearray))
                and bytes(rdpng[:8]) == b"\x89PNG\r\n\x1a\n"
                and _png_wh(rdpng) == (1920, 1080),
                _png_wh(rdpng) if rdpng else type(rdpng))
    _keep_rdt = globals()["DAILY_RESULTS_TEMPLATE_FILE"]
    try:
        globals()["DAILY_RESULTS_TEMPLATE_FILE"] = os.path.join(HERE, "__no-tpl__.html")
        _rdfb = render_daily_results_card(rdrows, ds, pill)
        t.check("⚑ 整合版缺模板时安静降级（缺 HTML 模板不该让整合版发不出去）",
                _rdfb is None or (isinstance(_rdfb, (bytes, bytearray))
                                  and bytes(_rdfb[:8]) == b"\x89PNG\r\n\x1a\n"),
                type(_rdfb))
    finally:
        globals()["DAILY_RESULTS_TEMPLATE_FILE"] = _keep_rdt

    _keep_tpl = globals()["RESULT_TEMPLATE_FILE"]
    try:
        globals()["RESULT_TEMPLATE_FILE"] = os.path.join(HERE, "__no-tpl__.html")
        t.check("缺模板时 HTML 卡安静退回 None（不外抛异常）",
                render_result_card_html(srow, es) is None)
    finally:
        globals()["RESULT_TEMPLATE_FILE"] = _keep_tpl

    # ---- 3f-3. 开赛提醒（STARTING SOON）：估算 / 提醒锁 / Match Preview 卡 ----
    print("\n-- 3f-3. 开赛提醒（estimatedStart 级联 / 提醒锁 / Match Preview）--")
    # _png_wh 定义在 3f-2 开头（那里第一次用），这里直接复用。

    def mk_it(ts, a, b, bo="Bo3", tour=""):
        return {"teams": [a, b], "shorts": [a[:6], b[:6]], "logos": ["", ""],
                "ts": ts, "bo": bo, "tour": tour, "status": "pending"}

    items = [mk_it(1000, "A", "B", "Bo3", "X League - Day 1"),
             mk_it(2000, "A", "C", "Bo3", "X League - Day 1"),
             mk_it(3000, "D", "E", "Bo3", "Y Cup")]
    est = estimate_starts(items)
    dur = _match_duration_sec(items[0])
    t.check("估算：自己 ts 可满足时不级联（A/B 照常开打）", est[0] == 1000, est)
    t.check("估算：同赛事且共用队伍 → 级联（A/C = A/B est + Bo3 时长 + turnaround）",
            est[1] == 1000 + dur + ANNOUNCE_TURNAROUND_MIN * 60, est)
    t.check("估算：不同赛事并行不传染（D/E 保留自己的 ts）", est[2] == 3000, est)

    # 连环串场：A/B → A/C → A/D 一路共用 A，必须逐级用 est 往下推（不是用 ts）
    chain = [mk_it(1000, "A", "B", "Bo3", "L - R1"),
             mk_it(1200, "A", "C", "Bo3", "L - R1"),
             mk_it(1400, "A", "D", "Bo3", "L - R1")]
    estc = estimate_starts(chain)
    d3 = _match_duration_sec(chain[0])
    t.check("估算：连环串场基于前一场的 est（A/C 基于 A/B，A/D 基于 A/C）",
            estc[1] == estc[0] + d3 + ANNOUNCE_TURNAROUND_MIN * 60
            and estc[2] == estc[1] + d3 + ANNOUNCE_TURNAROUND_MIN * 60, estc)

    # 2026-10-06 线上故障真身：同一 Round 两条并行流（12 支互不相同的队伍），
    # 登记时刻就是真的，绝不能被串成一条越来越晚的链。
    par = [mk_it(1759752000, "G2 Esports", "PARIVISION", "Bo3", "EPL - R4"),
           mk_it(1759752000, "Team Spirit", "1w Team", "Bo3", "EPL - R4"),
           mk_it(1759761000, "9z Team", "BetBoom", "Bo3", "EPL - R4"),
           mk_it(1759761000, "FURIA", "Aurora", "Bo3", "EPL - R4")]
    estp = estimate_starts(par)
    t.check("估算：同赛事并行流不传染（互不共用队伍 → 全部保留登记 ts）",
            estp == {0: 1759752000, 1: 1759752000, 2: 1759761000, 3: 1759761000}, estp)
    same_team = [mk_it(1759752000, "Team Spirit", "1w Team", "Bo3", "EPL - R4"),
                 mk_it(1759752000, "Team Spirit", "9z Team", "Bo3", "EPL - R4")]
    t.check("估算：对照组 —— 同赛事且共用队伍仍级联（证明上一行不是「全都不级联」）",
            estimate_starts(same_team)[1]
            == 1759752000 + _match_duration_sec(par[1]) + ANNOUNCE_TURNAROUND_MIN * 60,
            estimate_starts(same_team))

    ann_one = {announce_key(items[0]): {"est_ts": 1000, "reminders": 1}}
    t.check("提醒窗口：只有 est 落进 [now, now+LEAD] 的场次被提醒",
            [i for i, _e, _k, _r in announce_due(items, est, 900, 5, {})] == [0],
            est)
    t.check("提醒锁：已提醒过一次 → 不再打扰",
            announce_due(items, est, 1000, 5, ann_one) == [], ann_one)
    ann_drift = {announce_key(items[0]): {"est_ts": 100, "reminders": 0}}
    due3 = announce_due(items, est, 1000, 5, ann_drift)
    t.check("提醒锁：est 漂移 ≥10 分钟且没重提醒过 → 重提醒一次（第 2 次）",
            [(i, r) for i, _e, _k, r in due3] == [(0, 1)], due3)
    ann_same = {announce_key(items[0]): {"est_ts": 1000, "reminders": 0}}
    t.check("提醒锁：est 漂移 <10 分钟 → 不重提醒",
            announce_due(items, est, 1050, 5, ann_same) == [], ann_same)
    done = dict(items[0])
    done["status"] = "reported"
    est_d = estimate_starts([done, items[1], items[2]])
    t.check("提醒跳过：非 pending（已战报）不提醒",
            announce_due([done, items[1], items[2]], est_d, 900, 5, {}) == [],
            "reported")
    t.check("提醒跳过：已开打（est < now）不提醒 —— 战报马上就来",
            announce_due(items, est, 2000, 5, {}) == [], "late")

    pv_now = datetime.fromtimestamp(760, CST)
    pvd = build_preview_data(
        dict(items[0], tour="ESL Pro League Season 24 - Round 3",
             shorts=["AAA", "BBB"], logos=["u1", "u2"]), 1000, pv_now, es)
    t.check("预览数据：tour 按「 - 」拆、bo 大写、startsIn 按渲染时刻真实计算",
            pvd["event"] == "ESL Pro League Season 24"
            and pvd["stage"] == "ROUND 3" and pvd["bo"] == "BO3"
            and pvd["startsIn"] == "00:04:00", pvd)
    t.check("预览数据：队名/短名/队标快照进 teamA；不伪造数据（地图池/阵容/notes 全空）",
            pvd["teamA"] == {"name": "A", "short": "AAA", "logo": "u1"}
            and pvd["teamB"]["logo"] == "u2"
            and pvd["mapPool"] == [] and pvd["lineupA"] == []
            and pvd["lineupB"] == [] and pvd["notes"] == [], pvd)
    t.check("预览文案：含【CS2 开赛提醒】、剩余分钟；第二次注明时间有调整",
            "【CS2 开赛提醒】" in format_announce_caption(items[0], 1000, pv_now, 0)
            and "约 4 分钟后" in format_announce_caption(items[0], 1000, pv_now, 0)
            and "时间有调整" in format_announce_caption(items[0], 1000, pv_now, 1))
    with open(os.path.join(HERE, "preview_template.html"), encoding="utf-8") as _pf:
        _pv_txt = _pf.read()
    t.check("预览模板：__MATCH__ 在、__FONTDIR__ 在、无 file:/// 双前缀",
            "__MATCH__" in _pv_txt and "__FONTDIR__" in _pv_txt
            and "file:///__FONTDIR__" not in _pv_txt)
    if find_chrome(es):
        ppng = render_preview_card_html(
            dict(items[0], tour="ESL Pro League Season 24 - Round 3",
                 shorts=["AAA", "BBB"], logos=["", ""]),
            1000, pv_now, es)
        t.check("有 Chromium 时开赛提醒出 1920×1080 Match Preview（PNG 魔数 + 实际尺寸）",
                isinstance(ppng, (bytes, bytearray))
                and bytes(ppng[:8]) == b"\x89PNG\r\n\x1a\n"
                and _png_wh(ppng) == (1920, 1080),
                _png_wh(ppng) if ppng else type(ppng))
    _keep_pvp = globals()["PREVIEW_TEMPLATE_FILE"]
    try:
        globals()["PREVIEW_TEMPLATE_FILE"] = os.path.join(HERE, "__no-tpl__.html")
        t.check("预览卡缺模板安静退回 None（退纯文本，不外抛）",
                render_preview_card_html(items[0], 1000, pv_now, es) is None)
    finally:
        globals()["PREVIEW_TEMPLATE_FILE"] = _keep_pvp

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
    t.check("UA 含项目名与联系方式", "qq-esports-notify" in ua and "me@example.com" in ua)
    t.check("UA 缺失联系方式时有兜底", "contact-not-set" in build_ua({}))
    t.check("请求地址只用 api.php，不抓渲染页面",
            LIQUIPEDIA_API.endswith("/api.php") and "action=parse" in build_url())

    # ---- 选手数据：**抓取层已拆出（2026-10-09），这里只测保留的展示层** ----
    # 原来的 csdb 断言（parse_csdb_matches / locate_csdb_match / parse_csdb_players
    # 共约 20 条）随抓取层一起删除。展示层的自检在下面「V2 数据」一节 —— 用一段
    # 内嵌的逐图选手原始结构喂 build_result_match()，验证聚合 / MVP / 模板渲染，
    # 那段**不依赖任何抓取函数**，所以原样保留。
    print("\n-- 选手数据（展示层；抓取层已拆出，无可用数据源）--")
    t.check("选手数据：抓取层函数已全部移除（防止残留半截链路）",
            not any(hasattr(sys.modules[__name__], n) for n in
                    ("fetch_csdb", "parse_csdb_matches", "locate_csdb_match",
                     "parse_csdb_players", "attach_players")))
    t.check("选手数据：无 CSDB_* 常量残留（不再有任何 csdb 抓取配置）",
            not any(n.startswith("CSDB_") for n in dir(sys.modules[__name__])))

    t.check("队名匹配：子串/青训队名命中，首字母缩写不硬凑，空串不命中",
            _team_same("Natus Vincere", "NAVI") is False   # 缩写救不了：宁可空列不错分
            and _team_same("natus vincere", "Natus Vincere") is True
            and _team_same("NAVI Junior", "NAVI") is True
            and _team_same("", "NAVI") is False)

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
    parser.add_argument("--results", action="store_true",
                        help="结算一轮战果：只抓「已到结算窗口」的场次，没到点不发请求")
    parser.add_argument("--check-results", action="store_true",
                        help="同上但只打印/出图，不发消息、不写状态")
    parser.add_argument("--daily", action="store_true",
                        help="发上一个赛程日的全天整合版战果（不联网，只读清单快照）")
    parser.add_argument("--check-daily", action="store_true",
                        help="同上但只打印/出图，不发消息、不写状态")
    parser.add_argument("--announce", action="store_true",
                        help="开赛提醒一轮：est 落进提醒窗的场次发 Match Preview 卡（0 网络请求）")
    parser.add_argument("--check-announce", action="store_true",
                        help="同上但只渲染出图到 $TEMP，不发消息、不写状态")
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

    if args.daily or args.check_daily:
        # 和 --check-results 一样：只读演练连通道都不建，所以不校验推送配置
        if not args.check_daily and not watch.validate_cfg(cfg, args.config):
            log("[error] config.json 没配好，整合版会发不出去（见上面的报错）")
            return 2
        return run_daily(cfg, args)

    if args.results or args.check_results:
        # --check-results 是纯只读演练，连通道都不建，所以不校验推送配置
        if not args.check_results and not watch.validate_cfg(cfg, args.config):
            log("[error] config.json 没配好，战果会发不出去（见上面的报错）")
            return 2
        return run_results(cfg, args)

    if args.announce or args.check_announce:
        # --check-announce 只渲染不发，通道都不建；--announce 需要发送通道
        if not args.check_announce and not watch.validate_cfg(cfg, args.config):
            log("[error] config.json 没配好，开赛提醒会发不出去（见上面的报错）")
            return 2
        return run_announce(cfg, args)

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
