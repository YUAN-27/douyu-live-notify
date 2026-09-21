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
    "preflight-check.sh",
    "setup-docker-mirror.sh",
    "add-swap.sh",
    "mem-report.sh",
    "DEPLOY.md",
    "AGENT_PROMPT.md",
    "WATCHDOG.md",
]

# 从仓库根塞进包的源文件（不在 deploy/ 里）
FROM_ROOT = ["watch.py", "selftest.py"]

# 这些文件在 deploy/ 里，装到服务器上时要打印指纹
FINGERPRINT = ["watch.py", "selftest.py", "deploy/watchdog.py"]

# 文本文件统一转 LF 的扩展名
TEXT_EXT = {".sh", ".py", ".md", ".json", ".yml", ".yaml", ".service", ".timer",
            ".example", ".txt", ""}

SKIP_NAMES = {"__pycache__", "pack_deploy.py", "deploy.zip", "selftest_result.txt"}
SKIP_SUFFIX = (".pyc", ".pyo", ".bak", ".orig", ".rej")


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

    # ---- 收集 deploy/ 里的文件 ----
    entries = {}
    for name in sorted(os.listdir(DEPLOY_DIR)):
        if name in SKIP_NAMES or name.endswith(SKIP_SUFFIX):
            continue
        full = os.path.join(DEPLOY_DIR, name)
        if os.path.isfile(full):
            entries[name] = read_normalized(full)
    for name in FROM_ROOT:
        entries[name] = read_normalized(os.path.join(HERE, name))

    # ---- 校验必备文件 ----
    lack = [f for f in REQUIRED if f not in entries]
    if lack:
        print("deploy/ 里缺这些必备文件：%s" % "、".join(lack), file=sys.stderr)
        print("补齐后再打包，免得服务器上装到一半才报错。", file=sys.stderr)
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
        crlf = [n for n in got if b"\r\n" in z.read(n)]
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
        key = os.path.basename(rel) if rel.startswith("deploy/") else rel
        if key in entries:
            print("    %-16s  %s" % (sha16(entries[key]), rel))
        else:
            print("    %-16s  %s（!! 包里没有，指纹打不出来）" % ("-", rel), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
