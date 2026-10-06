#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 deploy/card_font.otf —— 赛程预告图片卡片用的中文字体。

为什么不用系统字体、也不带全量 CJK 字体
----------------------------------------
1. **不能依赖系统字体**：服务器（Ubuntu）和开发机（Windows）字体不同，
   同一份代码画出来的卡片会不一样，出了问题无法在本地复现。
   所以字体必须随包发布，两边用同一个文件。
2. **不能带全量 CJK 字体**：Noto Sans SC 完整版 8 MB，全汉字子集也要 5.1 MB，
   而 deploy.zip 现在总共才 200 KB。卡片上要画的**固定中文只有十来个字**
   （见 UI_CHARS），加上拉丁字母和标点，子集化之后只有 64 KB。
3. **覆盖不到怎么办**：render_card() 在画之前会逐个字符核对，只要有一个字
   不在子集里就**放弃出图、退回纯文本**（见 esports.py）。所以漏字不会画出豆腐块，
   只是少一次图片而已 —— 日志里会打 `[warn]`。

所以：**往卡片上加新的中文文案时，记得把新字补进 UI_CHARS 并重跑本脚本**，
否则那行会触发退回。

用法（需要 fonttools + 网络，只用一次，产物已入库）
----------------------------------------------------
    pip install fonttools
    python3 deploy/make_card_font.py

许可
----
字体是 Noto Sans SC（Google / Adobe），SIL Open Font License 1.1，
许可证全文随字体一起放在 deploy/CARD_FONT_LICENSE.txt。
OFL 允许子集化与再分发（本文件即子集产物）。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "card_font.otf")

# 卡片上会画的**全部中文/特殊字符**。加文案就加到这里，然后重跑。
#   CS2 赛程 / 今天 / 明天 / 共 N 场 / 次日 / 数据来源：
#   N 个赛事 / （图里只列前 N 场） / ·（分隔点） →（箭头） 　（全角空格）
#   CS2 战果 / 周一~周日（战果卡片页头的 `10-05 周一`）
#   CS2 战报 / 地图 N（单场战报卡片：标题的「报」+ 逐图行前缀的「地」）
# ⚠️ 这份必须和 esports.py 的 CARD_UI_CHARS 一模一样（自检里有一条断言钉着）。
#    忘了加字的后果：不是画成豆腐块，而是**静默退回纯文本** —— 照样能发，
#    但那条卡片就没了，而且只有翻日志才发现。
UI_CHARS = "赛程今天明共场次日数据来源：个事图里只列前·→　战果周一二三四五六日地报选手评分"

# ⚠️ fontTools 的 --unicodes 语法是 `U+起始-结束`：**范围尾部不能再写 U+**。
#    写成 "U+0020-U+007E" 会解析成 "0020-" + "007E"，报 int('', 16)。
UNICODE_RANGES = [
    "U+0020-007E",   # ASCII
    "U+00A0-00FF",   # Latin-1（队名里的重音字母）
    "U+0100-024F",   # Latin Extended-A/B
    "U+0370-03FF",   # 希腊字母
    "U+0400-04FF",   # 西里尔字母
    "U+2000-206F",   # 通用标点（破折号、弯引号）
    "U+2190-21FF",   # 箭头
    "U+2500-257F",   # 制表符
    "U+25A0-25FF",   # 几何图形
    "U+3000-303F",   # CJK 标点
    "U+FF00-FFEF",   # 全角
]

FONT_URL = ("https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
            "Sans/SubsetOTF/SC/NotoSansSC-Regular.otf")
UA = "qq-esports-notify/make_card_font (+https://github.com/YUAN-27/qq-esports-notify)"


def main():
    src = os.path.join(HERE, "NotoSansSC-Regular.otf")
    if not os.path.isfile(src):
        import urllib.request
        print("下载字体源（约 8 MB）：%s" % FONT_URL)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(urllib.request.Request(src and FONT_URL,
                                           headers={"User-Agent": UA}),
                     timeout=180) as r, open(src, "wb") as f:
            f.write(r.read())
        print("已存到 %s（%.1f MB）" % (src, os.path.getsize(src) / 1048576.0))

    spec = ",".join(UNICODE_RANGES + ["U+%04X" % ord(c) for c in UI_CHARS])
    cmd = [sys.executable, "-m", "fontTools.subset", src,
           "--output-file=" + OUT,
           "--unicodes=" + spec,
           "--layout-features=",
           "--no-hinting",
           "--desubroutinize",
           "--name-IDs=*",
           "--drop-tables+=DSIG"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("子集化失败：\n%s" % (r.stderr or r.stdout)[-2000:], file=sys.stderr)
        return 1

    # 回读核对：UI_CHARS 里每个字都必须在产物里，否则等于埋了个「悄悄退回文本」
    from fontTools.ttLib import TTFont
    cmap = set()
    for t in TTFont(OUT)["cmap"].tables:
        cmap |= set(t.cmap.keys())
    miss = [c for c in UI_CHARS if ord(c) not in cmap]
    if miss:
        print("!! 产物里缺这些字：%s" % "".join(miss), file=sys.stderr)
        return 1

    print("已生成 %s（%d 字节，%.1f KB）" % (OUT, os.path.getsize(OUT),
                                            os.path.getsize(OUT) / 1024.0))
    print("覆盖 %d 个固定字符 + %d 个 Unicode 区间" % (len(UI_CHARS), len(UNICODE_RANGES)))
    print("别忘了把 %s 也留在包里（OFL 要求随字体分发许可证）。" % "CARD_FONT_LICENSE.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
