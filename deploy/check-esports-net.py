#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 每日赛程 · 数据源连通性自检

只读：不发消息、不写文件、不改配置。跑完什么都不留下。
用法：  python3 check-esports-net.py
        （只想看某一节时，直接跑，全部都是只读的）

为什么需要这个脚本
------------------
「每天发赛事动态」这个功能的全部外部依赖，就是**服务器能不能拿到赛程数据**。
本机（开发机）实测 Liquipedia 可用，但服务器网络环境不同，可能：被墙、被劫持、
被限流、或 DNS 解析不出来。**在写主体代码之前先确认这一件事**，否则可能整套
方案都要换源。

排查顺序是刻意的：DNS → TLS 证书 → HTTP 请求 → 数据能不能解析出比赛，
从下往上定位，每一层的失败都对应一个确定的修法，见最后的【结论速查】。

关于 Liquipedia 的硬性要求（不满足会被 406/403 挡）
---------------------------------------------------
  1. User-Agent 必须写明项目名 + 联系方式；用 Python-urllib / node-fetch 这类
     通用 UA 会被直接拒绝（本机实测 406）。
  2. 必须支持 gzip（Accept-Encoding: gzip）。
  3. 条款限制 action=parse 类请求 <= 1 次 / 30 秒，且禁止抓渲染好的 HTML 页面，
     只能走 api.php。→ 所以这个功能**不能高频轮询**，每天请求 1 次即可。
  4. 内容 CC-BY-SA 3.0，正式推送时消息里必须署名「数据来源：Liquipedia」。
"""

import gzip
import html as html_mod
import json
import os
import platform
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# User-Agent 里必须带项目名 + 联系方式（Liquipedia 的条款要求）
UA = ("douyu-live-notify/1.0 "
      "(+https://github.com/YUAN-27/douyu-live-notify; 1249850641@qq.com)")

TIMEOUT = 25
MATCH_LIMIT_SHOWN = 6          # 样例里最多打印几场
FOLD_HINT = 15                 # 超过这个场次就要折叠低级别赛事（和既定方案一致）

LIQUIPEDIA_Q = urllib.parse.urlencode({
    "action": "parse",
    "page": "Liquipedia:Matches",
    "prop": "text",
    "format": "json",
})
LIQUIPEDIA_URL = "https://liquipedia.net/counterstrike/api.php?" + LIQUIPEDIA_Q
PANDASCORE_URL = "https://api.pandascore.co/"

HOSTS = ("liquipedia.net", "api.pandascore.co")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def hr(title):
    print("\n===== %s =====" % title, flush=True)


def ok(msg):
    print("  [OK]   %s" % msg, flush=True)


def bad(msg):
    print("  [FAIL] %s" % msg, flush=True)


def warn(msg):
    print("  [WARN] %s" % msg, flush=True)


def info(msg):
    print("         %s" % msg, flush=True)


def clean(s):
    return re.sub(r"\s+", " ", html_mod.unescape(s or "")).strip()


def bj(ts):
    return datetime.fromtimestamp(ts, CST)


# --------------------------------------------------------------------------
# 0. 运行环境
# --------------------------------------------------------------------------

def sec0_env():
    hr("0. 运行环境")
    info("python   : %s" % sys.version.split()[0])
    info("平台     : %s %s" % (sys.platform, platform.machine()))
    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy")
    found = {k: os.environ[k] for k in proxy_keys if os.environ.get(k)}
    if found:
        warn("环境里设了代理变量，外网请求会走代理：")
        for k, v in found.items():
            # 代理串里可能带账号密码，脱敏
            safe = re.sub(r"//[^@/]+@", "//<已隐藏>@", v)
            info("  %s=%s" % (k, safe))
        info("如果下面第 3 节失败，优先怀疑是这里；本脚本不改代理设置。")
    else:
        ok("没有设置代理环境变量（直连）")


# --------------------------------------------------------------------------
# 1. DNS
# --------------------------------------------------------------------------

def sec1_dns():
    hr("1. DNS 解析")
    results = {}
    for host in HOSTS:
        t0 = time.time()
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            ips = sorted({i[4][0] for i in infos})
            ms = int((time.time() - t0) * 1000)
            results[host] = ips
            ok("%-20s -> %s  (%d ms)" % (host, ", ".join(ips[:4]), ms))
        except Exception as exc:
            results[host] = []
            bad("%-20s 解析失败：%s: %s" % (host, type(exc).__name__, exc))
    return results


# --------------------------------------------------------------------------
# 2. TLS 证书（看有没有被中间人 / 劫持）
# --------------------------------------------------------------------------

def sec2_tls(host):
    hr("2. TLS 证书（%s）" % host)
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                info("TLS 版本 : %s / %s" % (ssock.version(), ssock.cipher()[0]))
    except ssl.SSLCertVerificationError as exc:
        bad("证书校验未通过：%s" % exc)
        warn("这通常意味着**中间人劫持**（被墙时常表现为 DNS 被污染 + 假证书），")
        info("不是 Liquipedia 自身的问题。需要换网络出口或走代理。")
        return False
    except Exception as exc:
        bad("建连失败：%s: %s" % (type(exc).__name__, exc))
        return False

    issuer = {k: v for tup in cert.get("issuer", ()) for k, v in tup}
    subject = {k: v for tup in cert.get("subject", ()) for k, v in tup}
    info("颁发者   : %s" % (issuer.get("organizationName") or issuer.get("commonName") or "?"))
    info("主体     : %s" % (subject.get("commonName") or "?"))
    info("有效期   : %s -> %s（UTC）" % (cert.get("notBefore"), cert.get("notAfter")))
    san = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    if san:
        info("域名覆盖 : %s" % ", ".join(san[:6]))

    # 过期检查：证书过期时会抛 SSLCertVerificationError，能走到这里基本没事，
    # 但还是显式提示一下剩余天数，方便预判。
    try:
        nb = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z")
        na = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        left = (na - datetime.now(timezone.utc).replace(tzinfo=None)).days
        info("剩余天数 : %d 天" % left)
    except Exception:
        pass
    ok("证书校验通过，没有被中间人替换的迹象")
    return True


# --------------------------------------------------------------------------
# 3. 真实请求
# --------------------------------------------------------------------------

def fetch(url, timeout=TIMEOUT):
    """按 Liquipedia 要求的姿势发请求：自定义 UA + 支持 gzip 解压。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = int((time.time() - t0) * 1000)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                raw = gzip.decompress(raw)
            return {
                "status": resp.status,
                "ms": ms,
                "encoding": enc,
                "bytes": len(raw),
                "headers": dict(resp.headers),
                "text": raw.decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        ms = int((time.time() - t0) * 1000)
        body = b""
        try:
            body = exc.read()
            if (exc.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
        except Exception:
            pass
        return {"status": exc.code, "ms": ms, "error": exc.reason,
                "bytes": len(body), "text": body.decode("utf-8", "replace"),
                "headers": dict(exc.headers or {})}
    except Exception as exc:
        return {"status": None, "ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(exc).__name__, exc), "text": "", "headers": {}}


def sec3_request():
    hr("3. Liquipedia 赛程接口（真实请求）")
    info("GET %s" % LIQUIPEDIA_URL)
    r = fetch(LIQUIPEDIA_URL)

    if r["status"] is None:
        bad("请求失败：%s" % r["error"])
        info("可能是网络出口不通、被墙、或第 1/2 节没通过。")
        return None

    info("HTTP %s  耗时 %d ms  解压后 %d 字节" % (r["status"], r["ms"], r["bytes"]))
    for k in ("X-Cache", "Age", "Cache-Control", "Content-Type"):
        if r["headers"].get(k):
            info("%s: %s" % (k, r["headers"][k]))

    if r["status"] == 200:
        ok("接口应答正常")
        return r["text"]

    prev = r["text"][:200].replace("\n", " ")
    bad("接口返回 %s：%s" % (r["status"], prev))
    if r["status"] == 406:
        info("→ 典型的「gzip 或 User-Agent 不合格」。本脚本已带 gzip + 自定义 UA，")
        info("  若仍 406，可能是中间设备改写了请求头。")
    elif r["status"] == 403:
        info("→ 被拒绝。很可能是 UA 被判为通用爬虫，或被临时封了 IP。")
    elif r["status"] == 429:
        info("→ 触发限流。条款是 action=parse <= 1 次 / 30 秒，等一会儿再试。")
    elif r["status"] >= 500:
        info("→ 对方服务端问题，不是我们这边的错，过阵子重试。")
    return None


# --------------------------------------------------------------------------
# 4. 数据可用性（能不能真的解析出比赛）
# --------------------------------------------------------------------------

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
      1. **不能按 data-timestamp 去重**。同时开打的两场比赛时间戳完全相同
         （实测 Team Liquid vs NRG 和 Chicken Coop vs Wildcard 同一秒开打），
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


def sec4_data(text):
    hr("4. 数据可用性（解析结果）")
    if not text:
        warn("跳过：第 3 节没拿到数据")
        return None
    try:
        payload = json.loads(text)
    except Exception as exc:
        bad("响应不是 JSON：%s" % exc)
        return None

    page = payload.get("parse")
    if not page:
        bad("JSON 里没有 parse 字段，响应结构变了")
        info("顶层字段：%s" % list(payload.keys())[:10])
        return None
    html_text = (page.get("text") or {}).get("*") or ""
    if not html_text:
        bad("parse.text 是空的")
        return None
    info("页面标题 : %s" % page.get("title"))
    info("HTML 长度: %d 字节" % len(html_text))

    anchors = len(re.findall(re.escape(MATCH_MARK), html_text))
    matches = parse_matches(html_text)
    if not matches:
        bad("一个比赛都没解析出来 —— 说明页面结构变了，选择器要重新对")
        info("请在浏览器打开 https://liquipedia.net/counterstrike/Liquipedia:Matches 核对")
        return None
    ok("解析出 %d 场比赛（锚点 %d 个）" % (len(matches), anchors))
    info("队名没凑齐 : %d 场" % len([m for m in matches if len(m["teams"]) < 2]))
    info("赛事名为空 : %d 场" % len([m for m in matches if not m["tour"]]))

    now = datetime.now(CST)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = today0.timestamp()
    past = [m for m in matches if m["ts"] < t0]
    ahead = [m for m in matches if m["ts"] >= t0]
    tbd = [m for m in ahead if m["tbd"]]
    live = [m for m in ahead if m["ts"] >= now.timestamp() and not m["tbd"]]

    print("")
    warn("这个页面混着**已结束**的比赛，必须按时间过滤，否则会刷一堆比分：")
    info("已过去（今天 0 点前）    : %d 场   <- 正式推送会丢掉" % len(past))
    info("今天 0 点后（含已打完的）: %d 场" % len(ahead))
    if tbd:
        info("其中 TBD vs TBD 占位     : %d 场   <- 建议也丢掉（对阵未定）" % len(tbd))
    info("此刻之后且对阵已定       : %d 场   <- 这才是真正会推的" % len(live))

    days = {}
    for m in ahead:
        days[bj(m["ts"]).date()] = days.get(bj(m["ts"]).date(), 0) + 1
    info("按北京日期分布（最多列 7 天）：")
    for d in sorted(days)[:7]:
        tag = "  <- 今天" if d == today0.date() else ""
        info("  %s  %d 场%s" % (d.isoformat(), days[d], tag))

    n_today = days.get(today0.date(), 0)
    print("")
    if n_today == 0:
        warn("今天（北京 %s）暂无可推送比赛 —— 正式推送会发「今日无赛事」" % today0.date())
    else:
        info("今天（北京 %s）共 %d 场" % (today0.date(), n_today))
        if n_today > FOLD_HINT:
            info("超过 %d 场 → 会触发折叠规则" % FOLD_HINT)

    warn("赛事分级（S/A/B 级）在这个页面上**拿不到**：")
    info("页面所有 data-* 属性已普查，没有 data-liquipediatier / data-tier 这类字段，")
    info("分级过滤按钮是客户端行为、没写进 HTML。分组只能改用「赛事名」。")

    info("样例（此刻之后、对阵已定，已换算北京时间）：")
    for m in live[:MATCH_LIMIT_SHOWN]:
        d = bj(m["ts"])
        teams = " vs ".join(m["teams"]) or "（队名解析失败）"
        info("  %s  %-34s %-5s %s" % (d.strftime("%m-%d %H:%M"), teams,
                                      m["bo"], m["tour"][:40]))
    return matches


# --------------------------------------------------------------------------
# 5. 备用源
# --------------------------------------------------------------------------

def sec5_backup():
    hr("5. 备用源 PandaScore（可选）")
    info("GET %s  —— 只探活，不需要 token" % PANDASCORE_URL)
    r = fetch(PANDASCORE_URL)
    if r["status"] is None:
        bad("不通：%s" % r["error"])
        info("不影响主方案（主源是 Liquipedia），只是少了个兜底。")
        return False
    info("HTTP %s  耗时 %d ms" % (r["status"], r["ms"]))
    if r["status"] == 200:
        try:
            v = json.loads(r["text"])
            ok("可达，接口版本 %s" % v.get("version"))
            info("若将来要用它：免费档 1000 次/小时，需注册一个免费 token。")
            return True
        except Exception:
            ok("可达")
            return True
    warn("返回 %s（不算故障，仅记录）" % r["status"])
    return False


# --------------------------------------------------------------------------
# 6. 结论
# --------------------------------------------------------------------------

def sec6_verdict(dns, tls_ok, matches):
    hr("6. 结论")
    lp_ips = dns.get("liquipedia.net") or []

    if not lp_ips:
        print("  [!] DNS 解析不出 liquipedia.net —— 先解决解析（换 DNS 或走代理）。")
        print("      方案本身没问题，是网络到不了。")
        return 1
    if not tls_ok:
        print("  [!] TLS 层出了问题（多半是被中间人拦下）—— 换网络出口或走代理后重跑。")
        return 1
    if matches:
        print("  [OK] 结论：**服务器可以拿到 CS2 赛程数据，方案可行**。")
        print("       下一步可以开始实现 esports.py + 每日定时器。")
        print("       正式推送时记得：消息末尾署名「数据来源：Liquipedia」，")
        print("       且每天只请求 1 次（条款限制 action=parse <= 1 次 / 30 秒）。")
        return 0

    print("  [!] 网络能通，但拿不到可用的赛程数据 —— 见第 3、4 节的具体报错。")
    print("      若第 4 节说「结构变了」，需要重新对选择器；")
    print("      若第 3 节是 403/406，多半是 UA 或 gzip 被中间设备改写了。")
    return 1


def main():
    print("=" * 68)
    print("CS2 每日赛程 · 数据源连通性自检（只读，不写任何文件）")
    print("时间: %s (北京时间)" % datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 68)

    sec0_env()
    dns = sec1_dns()
    tls_ok = sec2_tls("liquipedia.net") if dns.get("liquipedia.net") else False
    text = sec3_request()
    matches = sec4_data(text) if text else None
    sec5_backup()
    code = sec6_verdict(dns, tls_ok, matches)

    print("\n结论速查")
    print("  ① 第 1 节解析失败      → 换 DNS（如 223.5.5.5）或走代理")
    print("  ② 第 2 节证书不过      → 中间人劫持，换网络出口或走代理")
    print("  ③ 第 3 节 406          → User-Agent / gzip 不合格（本脚本已是合规写法）")
    print("  ④ 第 3 节 403 / 429    → 被封或限流，降频、等一会儿再试")
    print("  ⑤ 第 3 节 200 但第 4 节空 → 页面结构变了，需重新对选择器")
    print("  ⑥ 全部通过             → 可以进入实现阶段")
    print("\n（本脚本没有修改任何文件，也没有发送任何消息。）")
    return code


if __name__ == "__main__":
    sys.exit(main())
