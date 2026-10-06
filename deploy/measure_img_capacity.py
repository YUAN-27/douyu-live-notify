#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量一量 1920×1080 的那两张模板到底画得下几行 —— `HTML_IMG_MAX_ROWS` 的出处。

两张都量：`daily_template.html`（总预告）和 `daily_results_template.html`（全天整合版）。
它们共用同一个常量，因为 `.hd` / `.ftr` / `.bd` / `.col` / `.sep` / `density()`
是同一套几何 —— 谁哪天把其中一个改瘦了，这个脚本会告诉你两个数分家了。

为什么要单独一个脚本：那个常量是**量出来的**，不是估的。写死一个数在代码里，
半年后没人知道它凭什么等于 21、也不知道改了模板之后还算不算数 ——
把尺子一起放进仓库，谁动模板谁就能重跑一遍。

量法：把模板灌上 N 场数据写成独立 HTML → Chrome 无头 `--dump-dom` →
把页面上量到的几何值（最后一行的下边缘 vs 主体区 `.bd` 的下边缘）打回来。
溢出就是被 `overflow:hidden` 切掉（截图里看不出来，只能这么量）。

用法：
    python3 deploy/measure_img_capacity.py             # 测 18..24
    python3 deploy/measure_img_capacity.py 21 22       # 只测这两档
    python3 deploy/measure_img_capacity.py --daily     # 只量整合版
    python3 deploy/measure_img_capacity.py --preview   # 只量总预告

⚠️ 需要本机有 Chromium/Chrome（和出卡片用的是同一个，`find_chrome` 自己会找）。
⚠️ 这只是个开发/复核工具，不参与线上任何流程，也不联网。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esports  # noqa: E402

# 探针脚本：插在模板自己的 render(data) 之后，把几何值塞进 #probe。
# `.bd` 是 flex:1 + min-height:0，所以它的 clientHeight 是固定的；
# 内容超出时 justify-content:center 会让它上下对称溢出，最后一行就会被 body 的
# overflow:hidden 切掉 —— 拿「最后一行下边缘」和「.bd 下边缘」比就知道超没超。
_PROBE_JS = """
<pre id="probe"></pre>
<script>
(function(){
  var bd   = document.querySelector('.bd');
  var cols = [].slice.call(document.querySelectorAll('.col'));
  var ftr  = document.querySelector('.ftr');
  var last = cols.map(function(c){
      var k = c.lastElementChild;
      return k ? k.getBoundingClientRect().bottom : 0;
  }).reduce(function(a,b){ return Math.max(a,b); }, 0);
  var r = {
    n: %d,
    cols: cols.map(function(c){ return c.children.length; }),
    seps: document.querySelectorAll('.sep').length,
    lastBottom: Math.round(last),
    bdBottom:   Math.round(bd.getBoundingClientRect().bottom),
    ftrTop:     Math.round(ftr.getBoundingClientRect().top)
  };
  r.over = Math.max(0, r.lastBottom - r.bdBottom);
  document.getElementById('probe').textContent = 'PROBE' + JSON.stringify(r) + 'END';
})();
</script>
</body>"""


def mkrows(n, single_event=True, midnight=False):
    """造 n 行**两张模板都能吃**的数据（两张要的字段是同一套）。

    `midnight=True`：把整段排到跨过 CST 午夜，逼模板插一条「NEXT DAY」分隔线 ——
    分隔线也吃高度，是容量最紧的那种情况。
    """
    base = 1759680000 - 10 * 3600 if midnight else 1759700000
    out = []
    for i in range(n):
        a, b = "TEAM%02dA" % i, "TEAM%02dB" % i
        out.append({
            "ts": base + i * 3600,
            "teams": [a, b], "shorts": [a, b], "logos": ["", ""],
            "winner": a, "score": "2:1" if i % 3 else "2:0",
            "bo": "Bo3", "maps": [],
            "tour": ("ESL Pro League Season 24 - Round 3" if single_event
                     else "Event %d" % i),
            # 预告模板会用到的两个字段（已结束的那张不看它们）
            "tour_page": "", "finished": False, "sides": ["", ""], "tbd": False,
        })
    return out


# 两张模板：名字 / 模板文件 / 占位符 / 用哪个 build_*_data / 它拒绝超限时返回什么
TEMPLATES = (
    ("整合版", "daily_results_template.html", "__RESULTS__",
     "build_daily_results_data"),
    ("总预告", "daily_template.html", "__DAILY__", "build_daily_data"),
)


def probe(n, tpl, single_event=True, midnight=False):
    """返回一条测量结果 dict；测不了就返回带 err 的 dict（**不抛异常**）。"""
    _label, tpl_name, ph, builder = tpl
    tpl_path = os.path.join(HERE, tpl_name)
    chrome = esports.find_chrome(dict(esports.ESPORT_DEFAULTS))
    if not chrome:
        return {"n": n, "err": "找不到 Chromium/Chrome"}
    rows = mkrows(n, single_event, midnight)
    now = esports.datetime.fromtimestamp(min(r["ts"] for r in rows), esports.CST)
    data = getattr(esports, builder)(rows, now, dict(esports.ESPORT_DEFAULTS))
    if data is None:
        return {"n": n, "data": None,
                "note": "%s 直接返回 None（> %d 场，轮不到模板出图）"
                        % (builder, esports.HTML_IMG_MAX_ROWS)}
    with open(tpl_path, encoding="utf-8") as f:
        tmpl = f.read()
    inject = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (tmpl.replace("__FONTDIR__", esports._file_uri(os.path.join(HERE, "fonts")))
            .replace(ph, inject))
    html = html.replace("</body>", _PROBE_JS % n, 1)

    work = tempfile.mkdtemp(prefix="cap_probe_")
    hp = os.path.join(work, "probe.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(html)
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--no-first-run", "--hide-scrollbars", "--allow-file-access-from-files",
           "--user-data-dir=" + os.path.join(work, "prof"),
           "--window-size=%d,%d" % (esports.HTML_CARD_W, esports.HTML_CARD_H),
           "--dump-dom", esports._file_uri(hp)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=90,
                             check=False).stdout.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"n": n, "err": "%s: %s" % (type(exc).__name__, exc)}
    i = out.find("PROBE")
    j = out.find("END", i)
    if i < 0 or j < 0:
        return {"n": n, "err": "拿不到探针（--dump-dom 没吐出来）"}
    try:
        return json.loads(out[i + len("PROBE"):j])
    except ValueError as exc:
        return {"n": n, "err": "探针不是合法 JSON：%s" % exc}


def main(argv):
    args = [a for a in argv[1:]]
    only = None
    if "--daily" in args:
        only = "整合版"
    elif "--preview" in args:
        only = "总预告"
    cap = esports.HTML_IMG_MAX_ROWS
    want = [int(a) for a in args if a.isdigit()] or [18, 20, 21, 22, 23]
    print("1920×1080 大图容量实测（HTML_IMG_MAX_ROWS 当前 = %d）" % cap)
    print("两张模板共用这一个常量（同一套几何）；列 = 两列各几行；")
    print("下边缘 > 主体下边缘 就是被 overflow:hidden 切了")
    print("判据：N <= %d 必须「模板出图且装得下」；N > %d 必须「直接拒绝、退旧卡」\n"
          % (cap, cap))
    bad = 0
    for tpl in TEMPLATES:
        if only and tpl[0] != only:
            continue
        print("---- %s（%s）----" % (tpl[0], tpl[1]))
        for single in (True, False):
            for mid in (False, True):
                for n in want:
                    r = probe(n, tpl, single, mid)
                    tag = ("单赛事" if single else "多赛事") + (
                        "·跨午夜" if mid else "·同日")
                    if "err" in r:
                        print("  %-12s N=%-3d 量不了：%s" % (tag, n, r["err"]))
                        bad += 1
                        continue
                    if n > cap:
                        if r.get("data", 1) is None:
                            print("  %-12s N=%-3d ✓ 直接拒绝（N > %d），上层退旧卡"
                                  % (tag, n, cap))
                        else:
                            print("  %-12s N=%-3d ✗ 居然还在渲染（应当拒绝）" % (tag, n))
                            bad += 1
                        continue
                    if r.get("data", 1) is None:
                        print("  %-12s N=%-3d ✗ 被拒绝了（N <= %d，本该出图）"
                              % (tag, n, cap))
                        bad += 1
                    elif r["over"] == 0:
                        print("  %-12s N=%-3d ✓ 装得下（列 %-9s 下边缘 %4d vs 主体 %4d）"
                              % (tag, n, r["cols"], r["lastBottom"], r["bdBottom"]))
                    else:
                        print("  %-12s N=%-3d ✗ 溢出 %dpx（列 %-9s 下边缘 %4d > %4d）"
                              % (tag, n, r["over"], r["cols"],
                                 r["lastBottom"], r["bdBottom"]))
                        bad += 1
    print("")
    if bad:
        print("⚠️ 有 %d 条不通过：模板容量和 HTML_IMG_MAX_ROWS 已经对不上了，"
              "去改 esports.py 里那个常量（和它上面的实测注释表）。" % bad)
        return 1
    print("✓ 全通过：HTML_IMG_MAX_ROWS = %d 仍然成立。" % cap)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
