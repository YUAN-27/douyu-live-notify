#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打部署包：生成 deploy.zip。

解开后第一层是 deploy/，里面 watch.py、selftest.py 和 install-watch.sh
在**同一层** —— 传到服务器上 `cd deploy && sudo bash install-watch.sh`
就能直接装，不会报「缺少源文件」。

这个脚本存在的理由：手动 `zip -r deploy.zip deploy/` 很容易漏掉
仓库根目录的 watch.py / selftest.py，而 install-watch.sh 恰恰需要它们。
所以这里把「必备文件清单」写死，缺一个就直接报错退出，不生成包。

⚠️ 包里**故意包含** deploy/.env 和 deploy/config.json（里面有群号 / token），
   这份包只该送到你自己的服务器上。deploy.zip 已在 .gitignore 里，别提交。

另外：所有文本文件在打包时统一转成 LF。Windows 上工作区可能是 CRLF，
直接压进包里的话，到 Linux 上跑 shell 脚本会报 `$'\r': command not found`。

用法：
    python pack_deploy.py
"""
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY_DIR = os.path.join(HERE, "deploy")
OUT = os.path.join(HERE, "deploy.zip")

# 必须在包里出现的文件（相对 deploy/ 的路径），缺一个就失败
REQUIRED = [
    "watch.py",
    "selftest.py",
    "install-watch.sh",
    "docker-compose.yml",
    "config.example.json",
    "douyu-watch.service",
    "douyu-watch.timer",
    "douyu-watch.tmpfiles",
    "watchdog.py",
    "douyu-watchdog.service",
    "douyu-watchdog.timer",
    "watchdog.env.example",
    "esports.py",
    # 2026-10-05 用户拍板的 V2 大图卡片：两个 HTML 模板 + 三个字体（OFL 许可，
    # 可随包分发）。缺了不会报错，但卡片会**安静退回 880px Pillow 旧卡**，
    # 所以必须进 REQUIRED —— 免得打出一个「看起来正常、实际上全退旧版」的包。
    "result_template.html",
    "daily_template.html",
    # 全天整合版的 V2 赛果模板（--daily 渲染 1920x1080 用）。同样属于
    # 「缺了不报错、但整合版会安静退回 880px Pillow 旧卡」的那种，必须进
    # REQUIRED —— 2026-10-06 之前整合版压根没有 HTML 层，就是漏了这一件。
    "daily_results_template.html",
    # 开赛提醒的 Match Preview 模板（--announce 渲染 1920x1080 用）。
    # 缺了 render_preview_card_html 会 raise → log 后 return None → 纯文本发送，
    # 功能不炸但大图卡永远出不来，所以必须进 REQUIRED。
    "preview_template.html",
    "fonts/BebasNeue-Regular.ttf",
    "fonts/IBMPlexMono-Regular.ttf",
    "fonts/IBMPlexMono-SemiBold.ttf",
    "fonts/LICENSE-OFL.txt",
    # 图片卡片的三件套：字体（二进制）+ 它的许可证 + 生成字体的脚本。
    # 少了字体不会报错，但卡片会**静默降级成纯文本**；少了 make_card_font.py
    # 则 `esports.py --selftest` 里 2 条「两张字符表是否一致」的断言会被跳过
    # （项数从 278 掉到 276，容易被误判成「少了什么」）。所以缺了就不让打包。
    "card_font.otf",
    "CARD_FONT_LICENSE.txt",
    "make_card_font.py",
    "douyu-esports.service",
    "douyu-esports.timer",
    # 战果公布（比赛打完后推战果）的两个单元。同样属于「缺了不会当场报错，
    # 但装了也不会发战果」的那种 —— 必须进 REQUIRED，否则打出来的包看着正常、
    # install-watch.sh 里那段 install 分支却找不到源文件而静默跳过。
    "douyu-esports-results.service",
    "douyu-esports-results.timer",
    # 全天整合版（次日早上发上一个赛程日的汇总）的两个单元。同上，缺了不报错但发不出来。
    "douyu-esports-daily.service",
    "douyu-esports-daily.timer",
    # 开赛提醒（比赛快开打时推 Match Preview）的两个单元。同上。
    "douyu-esports-announce.service",
    "douyu-esports-announce.timer",
    "preflight-check.sh",
    "setup-docker-mirror.sh",
    "add-swap.sh",
    "mem-report.sh",
    "DEPLOY.md",
    "AGENT_PROMPT.md",
    "WATCHDOG.md",
    "ESPORTS.md",
    # 群内命令交互（@ 机器人 / `/赛事`）的常驻单元。
    # ⚠️ 它**没有 .timer**（这是个 Restart=always 的常驻服务，不是 oneshot 定时任务）——
    #    所以这里只列 .service。缺了不会报错，但 install-watch.sh 里那段 install
    #    分支找不到源文件会静默跳过，装了也没法启用。
    "douyu-cmd.service",
    # 群内命令交互的设计文档。它是「这条路为什么这么走、哪里会翻车」的唯一记录，
    # 也是维护正则 / 水位线 / 冷却时的手册。缺了不影响运行。
    "CMD_INTERACT.md",
]

# 从仓库根塞进包的源文件（不在 deploy/ 里）
FROM_ROOT = ["watch.py", "selftest.py"]

# 这些文件在 deploy/ 里，装到服务器上时要打印指纹
FINGERPRINT = ["watch.py", "selftest.py", "deploy/watchdog.py", "deploy/esports.py",
               "deploy/result_template.html", "deploy/daily_template.html",
               "deploy/daily_results_template.html",
               "deploy/preview_template.html",
               "deploy/fonts/BebasNeue-Regular.ttf",
               "deploy/fonts/IBMPlexMono-Regular.ttf",
               "deploy/fonts/IBMPlexMono-SemiBold.ttf"]

# 文本文件统一转 LF 的扩展名
TEXT_EXT = {".sh", ".py", ".md", ".json", ".yml", ".yaml", ".service", ".timer",
            ".example", ".txt", ".html", ""}

SKIP_NAMES = {"__pycache__", "pack_deploy.py", "deploy.zip", "selftest_result.txt",
              # 世界排名的抓取缓存：服务器上会自己生成，别把开发机上的那份打进包
              "rank_cache.json", "rank_cache.json.tmp",
              # 队标缓存目录（ensure_logo 运行期下载的 PNG）：同上，服务器自己会长出来。
              # 2026-10-05 打包脚本改递归后踩到 —— 47 张本地缓存差点混进部署包。
              "logo_cache",
              # 生成 card_font.otf 用的**源字体**（Noto Sans SC，8 MB）。
              # 产出的卡片字体是 card_font.otf（66 KB），这个源文件只在重跑
              # make_card_font.py 时才需要 —— 进了包 deploy.zip 会从 260 KB 涨到 8 MB。
              # （.gitignore 里也钉着，两条都要有：一个管仓库，一个管部署包。）
              "NotoSansSC-Regular.otf"}
SKIP_SUFFIX = (".pyc", ".pyo", ".bak", ".orig", ".rej")


def is_runtime_state(name):
    """运行期状态文件（`state_*.json`）——**绝不能进部署包**。

    服务器上那些文件是「当前进度」；开发机上如果也有，那是本地跑 `--check` /
    `--check-results` 留下的残渣（新加的 `state_esports_parse.json` 只要跑一次
    `--check` 就会生成）。把它打进包之后，`install-watch.sh` 会**覆盖服务器上的
    `state_results_pending.json`** —— 后果是「该发的战果不发」或者「已发过的重发」，
    属于最难查的一类故障（本地怎么试都正常）。
    """
    base = os.path.basename(name)
    if not base.startswith("state_"):
        return False
    return (base.endswith(".json")
            or base.endswith(".json.tmp")
            # 2026-10-06 起 esports.py 用 `state_*.json.lock` 做跨进程 flock。
            # 它不是 `.json` 结尾，上面两条规则**都挡不住它** —— 开发机跑一次
            # `--check` 就会留下这个空文件，然后被打进 deploy.zip。
            # 空文件本身无害，但它会在服务器上留一个来路不明的 `.lock`，
            # 而且一旦哪天锁文件里真写了东西（比如记 pid），就是白送一份状态出去。
            or base.endswith(".lock"))

# 单个文件超过这个大小就认为「打包名单出问题了」，直接失败。
# 现状：最大的文件是 card_font.otf（66 KB）。加这条是为了让「不小心把 8 MB 源字体
# 或数据集打进去」这种事**在打包时就报错**，而不是等传上去才发现包变大了。
MAX_FILE_BYTES = 1024 * 1024


def is_text(name):
    base = os.path.basename(name)
    if base in (".env", ".env.example", ".gitignore"):
        return True
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXT


def read_normalized(path):
    """读文件；文本类统一转 LF，避免 CRLF 带到 Linux 上。"""
    with open(path, "rb") as fp:
        data = fp.read()
    if is_text(path):
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def sha16(data):
    return hashlib.sha256(data).hexdigest()[:16]


def main():
    # ---- 先检查必备的源文件在不在，缺了就别生成半成品包 ----
    missing = [f for f in FROM_ROOT if not os.path.isfile(os.path.join(HERE, f))]
    if missing:
        print("缺少源文件（应当在仓库根目录）：%s" % "、".join(missing), file=sys.stderr)
        print("先确认 watch.py / selftest.py 存在再打包。", file=sys.stderr)
        return 1

    if not os.path.isdir(DEPLOY_DIR):
        print("找不到 deploy/ 目录", file=sys.stderr)
        return 1

    # ---- 收集 deploy/ 里的文件（**递归**：fonts/ 子目录里的字体也要进包）----
    entries = {}
    for root, dirs, files in os.walk(DEPLOY_DIR):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, DEPLOY_DIR).replace("\\", "/")
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIX) or is_runtime_state(rel):
                continue
            entries[rel] = read_normalized(full)
    for name in FROM_ROOT:
        entries[name] = read_normalized(os.path.join(HERE, name))

    # 兜一道：上面那条规则要是哪天被改坏了，这里会当场报错而不是悄悄把状态打进包。
    leaked = [n for n in entries if is_runtime_state(n)]
    if leaked:
        print("运行期状态文件不该进部署包：%s" % "、".join(leaked), file=sys.stderr)
        print("检查 pack_deploy.py 的 is_runtime_state()。", file=sys.stderr)
        return 1

    # ---- 校验必备文件 ----
    lack = [f for f in REQUIRED if f not in entries]
    if lack:
        print("deploy/ 里缺这些必备文件：%s" % "、".join(lack), file=sys.stderr)
        print("补齐后再打包，免得服务器上装到一半才报错。", file=sys.stderr)
        return 1

    # ---- 体积守卫 ----
    # deploy/ 是**全收**（listdir），所以少写一条 SKIP 就可能把大文件带进去。
    # 这里兜一道：超标就报错，而不是打出一个 8 MB 的包。
    fat = [(n, os.path.getsize(os.path.join(DEPLOY_DIR, n)))
           for n in entries if os.path.isfile(os.path.join(DEPLOY_DIR, n))]
    fat = [(n, s) for n, s in fat if s > MAX_FILE_BYTES]
    if fat:
        print("这些文件超过 %d KB，不该进部署包：" % (MAX_FILE_BYTES // 1024),
              file=sys.stderr)
        for n, s in fat:
            print("    %-40s %.1f MB" % (n, s / 1048576.0), file=sys.stderr)
        print("加进 SKIP_NAMES（本脚本）和 .gitignore（仓库）再打包。", file=sys.stderr)
        return 1

    # ---- 写包 ----
    # 第一层是 deploy/，对应文档里的 `unzip -o deploy.zip && cd deploy`
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            # 固定时间戳，内容不变时包的内容也不变，方便比对
            info = zipfile.ZipInfo("deploy/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, entries[name])

    # ---- 回读校验：确认写的包真能用 ----
    with zipfile.ZipFile(OUT) as z:
        got = set(z.namelist())
        # 只对文本文件查 CRLF。二进制（如字体 card_font.ttf）里出现 \r\n 字节
        # 纯属巧合，拿它当「换行符没归一」会误报，把好好的包拦下来。
        crlf = [n for n in got if is_text(n) and b"\r\n" in z.read(n)]
    still = [f for f in REQUIRED if ("deploy/" + f) not in got]
    if still or crlf:
        if still:
            print("回读发现缺失：%s" % "、".join(still), file=sys.stderr)
        if crlf:
            print("这些文件含 CRLF：%s" % "、".join(sorted(crlf)), file=sys.stderr)
        return 1

    # ---- 报告 ----
    size = os.path.getsize(OUT)
    print("已生成 %s（%d 个文件，%.1f KB）" % (OUT, len(entries), size / 1024.0))
    print("文本文件均已归一为 LF。解压后结构：deploy/ 下 watch.py 与 install-watch.sh 同层。")
    print("")
    print("传到服务器上后：")
    print("    unzip -o deploy.zip && cd deploy")
    print("    sudo bash install-watch.sh")
    print("")
    print("指纹（sha256 前 16 位）—— 服务器装完会打印同样的值，可用来确认不是旧版：")
    for rel in FINGERPRINT:
        # entries 的键是「相对 deploy/ 的路径」（含子目录，如 fonts/xxx.ttf）
        key = rel[len("deploy/"):] if rel.startswith("deploy/") else rel
        if key in entries:
            print("    %-16s  %s" % (sha16(entries[key]), rel))
        else:
            print("    %-16s  %s（!! 包里没有，指纹打不出来）" % ("-", rel), file=sys.stderr)
    # 卡片字体也报一下：它是二进制资产、不参与上面 4 个代码指纹的核对，
    # 但「卡片画不出来 / 字变方块」时第一个要确认的就是这个文件对不对得上。
    for extra in ("card_font.otf", "make_card_font.py"):
        if extra in entries:
            print("    %-16s  deploy/%s（卡片资产）" % (sha16(entries[extra]), extra))
    return 0


if __name__ == "__main__":
    sys.exit(main())
