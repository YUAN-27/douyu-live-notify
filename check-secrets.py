#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开仓库前的凭据体检：扫描**已跟踪文件**，看有没有混进真实凭据。

为什么需要它
------------
这个仓库是 public。真实凭据（OneBot token、告警 webhook 密钥）只该待在被
.gitignore 挡住的 `config.json` / `.env` 里。但「手滑把真实值写进 example 或文档」
是极易犯、又极难自查的错 —— 一旦推上去就永久留在 git 历史里，删文件也删不掉。

2026-10-05 就为此白排查了一轮：`deploy/config.example.json` 里的
`"token": "YOUR_ONEBOT_TOKEN"` 被当成了真实 token。其实它只是模板的占位符
（真实值在 gitignore 掉的 `deploy/config.json` 里，32 位十六进制）。**人是会看错的，
所以让机器来对账。**

两层检查
--------
1. **对账（最有价值，也最准）**：本机存在真实配置时，把里面的凭据值逐个拿去和
   已跟踪文件比对。命中就一定是「你的真实凭据进了仓库」，零误报。
2. **形态**：没有真实配置也能跑（比如 CI 上的全新 clone）。按「值长得像真凭据 +
   出现在敏感字段名附近」判定，配白名单排除已知占位符。

误报怎么处理
------------
占位符写进 `PLACEHOLDER_ALLOW`。**不要为了让它闭嘴就放松规则** ——
放松之后下一次真泄就没人拦了。确实需要例外就往白名单里加，
并在注释里写清「为什么它是安全的」。

用法：
    python check-secrets.py          # 扫已跟踪文件（CI 用这个）
    python check-secrets.py --all    # 连未跟踪文件一起扫（本地全面自查）
    python check-secrets.py -v       # 同时打印通过的对账项

退出码：0 = 干净，1 = 有发现，2 = 环境不对（比如不在 git 仓库里）。
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 第 1 层：本机的真实凭据文件。存在才读，不存在就跳过（CI 上本来就没有）。
# ---------------------------------------------------------------------------
REAL_CONFIG_FILES = [
    "config.json", "deploy/config.json",
    ".env", "deploy/.env",
    "watchdog.env", "deploy/watchdog.env",
    "deploy/.env.local",
]

# 从真实配置里挑「哪些值是凭据」：键名命中这些词，或者值本身长得就像凭据。
SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|pwd|api[_-]?key|apikey|webhook|"
    r"app[_-]?key|access[_-]?key|auth|密钥|口令)", re.I)

# ---------------------------------------------------------------------------
# 第 2 层：形态判定。
# ---------------------------------------------------------------------------
# 「一眼就是凭据」的形状：整段的十六进制长串（openssl rand -hex N 的产物就是这个样）。
HEX_SECRET_RE = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{40}$|^[0-9a-f]{48}$|^[0-9a-f]{64}$",
                           re.I, )
# 宽泛的「token 形状」：只由字母数字和 _ - 组成，且够长。
TOKENISH_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")

# 敏感字段名附近才算 —— 否则一大把普通长标识符（URL、哈希文件名）会淹没结果。
CONTEXT_RE = re.compile(
    r'["\']?(token|secret|password|passwd|api[_-]?key|apikey|webhook|'
    r'app[_-]?key|access[_-]?key)["\']?\s*[:=]\s*["\']?([A-Za-z0-9_\-]{20,})',
    re.I)

# 已知占位符 / 已知安全的示例值。**只放确实不可能是真凭据的**。
PLACEHOLDER_ALLOW = {
    "YOUR_ONEBOT_TOKEN",
    "your_onebot_token",
    "YOUR_GROUP_ID",
    "YOUR_ROOM_ID",
    "CHANGE_ME",
    "REPLACE_ME",
    "TEST_TOKEN",
    "example_token",
    # 自检 / 单测里故意用的假值
    "token-not-set",
    "contact-not-set",
}

# 白名单里还要放「文档正文里出现的示例串」，它们不带引号也不在赋值位置，
# 但会被 TOKENISH 抓到。按整串精确匹配，别用前缀。
PLACEHOLDER_ALLOW |= {
    "填你在OneBot端设置的token",  # 中文占位符（去掉标点后）
    "httpsgithubcomYUAN27douyulivenotify",
    "githubYUAN27douyulivenotify",
    "1249850641qqcom",
}

# 这些文件里出现长串是正常的，不参与形态判定（但**仍然参与第 1 层对账**）。
# 加进来之前想清楚：它是「本来就不含凭据」，还是「我不想看到告警」。
SKIP_SHAPE_FILES = {
    ".gitignore",
    "# 自检结果里有各种示例输出",
    "selftest_result.txt",
}

# 形态判定容易误伤的文件类型：给人看的文档、以及法律文本。
TEXT_ONLY_SUFFIXES = {".md", ".txt", ".pdf"}


def tracked_files(include_untracked=False):
    """列出要扫的文件。默认只扫 git 已跟踪的（未跟踪的还没进仓库，不构成泄漏）。"""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=HERE,
                             capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    if include_untracked:
        for root, dirs, files in os.walk(HERE):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), HERE).replace(os.sep, "/")
                if rel not in names:
                    names.append(rel)
    return names


def read_text(path):
    try:
        with open(path, "rb") as fp:
            raw = fp.read()
    except OSError:
        return ""
    if b"\0" in raw[:4096]:          # 二进制
        return ""
    return raw.decode("utf-8", "replace")


def looks_like_secret(value):
    """值本身是不是「像个真凭据」。只用于第 2 层，且只喂给明确的占位白名单过滤后的值。"""
    if not value or len(value) < 20:
        return False
    if value in PLACEHOLDER_ALLOW:
        return False
    if HEX_SECRET_RE.match(value):
        return True
    # 混合字符类 + 没有分隔点（有点的基本是路径/域名/邮箱）
    classes = 0
    if re.search(r"[a-z]", value):
        classes += 1
    if re.search(r"[A-Z]", value):
        classes += 1
    if re.search(r"[0-9]", value):
        classes += 1
    if re.search(r"[_\-]", value):
        classes += 1
    return classes >= 3 and len(value) >= 24


def collect_real_secrets():
    """第 1 层的数据源：本机真实配置里的凭据值 → [(来源文件, 值), ...]。

    只取「键名像凭据」的值；另外把整份文件里所有 token 形状的串也收进来，
    因为 .env 那种 KEY=value 的写法解析起来容易漏。
    """
    found = []
    for rel in REAL_CONFIG_FILES:
        path = os.path.join(HERE, rel)
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        if not text:
            continue
        # ① JSON / .env 里键名像凭据的值
        for m in re.finditer(r'["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*[:=]\s*["\']?([^"\'\s,}]{8,})',
                             text):
            key, val = m.group(1), m.group(2)
            if SECRET_KEY_RE.search(key) and val not in PLACEHOLDER_ALLOW:
                found.append((rel, val))
        # ② 兜底：整份文件里所有 token 形状的长串（怕上面的正则漏掉嵌套结构）
        for m in TOKENISH_RE.finditer(text):
            val = m.group(0)
            if looks_like_secret(val):
                found.append((rel, val))
    # 去重、按值长度从长到短（长的更具体，先报更好定位）
    uniq = {}
    for src, val in found:
        uniq.setdefault(val, src)
    return sorted(uniq.items(), key=lambda kv: -len(kv[0]))


def scan_shape(text, rel):
    """第 2 层：按「敏感字段名 = 长串」找。返回 [(行号, 片段)]。"""
    hits = []
    base = os.path.basename(rel)
    if base in SKIP_SHAPE_FILES:
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        for m in CONTEXT_RE.finditer(line):
            val = m.group(2)
            if val in PLACEHOLDER_ALLOW:
                continue
            if not looks_like_secret(val):
                continue
            # 只是「值里有 24 位以上的长串」，可能出现在散文里 —— 那是第 1 层该管的，
            # 这里严格只在赋值形态上报。
            hits.append((i, "%s = %s" % (m.group(1), _mask(val))))
    return hits


def _mask(v):
    """报告里不回显完整值 —— 万一是真的，别把它又打到 CI 日志里。"""
    if len(v) <= 8:
        return "*" * len(v)
    return "%s…%s（%d 位）" % (v[:3], v[-3:], len(v))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    include_untracked = "--all" in argv
    verbose = "-v" in argv or "--verbose" in argv

    files = tracked_files(include_untracked)
    if files is None:
        print("[error] 这里不是 git 仓库（或 git 不可用），没法确定「哪些文件已跟踪」",
              file=sys.stderr)
        print("        这个脚本的意义就在于只扫已跟踪文件，所以必须在仓库里跑。",
              file=sys.stderr)
        return 2

    print("凭据体检：%s扫描 %d 个文件"
          % ("含未跟踪，" if include_untracked else "只扫已跟踪，", len(files)))

    findings = []

    # ---- 第 1 层：和真实配置对账 ----
    reals = collect_real_secrets()
    if reals:
        print("\n-- 第 1 层：和本机真实配置对账（%d 个凭据值参与比对）--" % len(reals))
        for val, src in reals:
            hit_files = []
            for rel in files:
                if rel == src:
                    continue
                if val and val in read_text(os.path.join(HERE, rel)):
                    hit_files.append(rel)
            if hit_files:
                findings.append("真实凭据泄漏：%s 里的值（%s）出现在已跟踪文件里 → %s"
                                % (src, _mask(val), "、".join(sorted(set(hit_files)))))
            elif verbose:
                print("  [ok]   %s 里的值（%s）没在已跟踪文件里出现"
                      % (src, _mask(val)))
        if not any(f.startswith("真实凭据泄漏") for f in findings) and not verbose:
            print("  [ok]   真实配置里的凭据都没有出现在已跟踪文件里")
    else:
        print("\n-- 第 1 层：跳过（本机没有真实配置文件，或里面没有凭据）--")
        print("        CI 上就是这样：全新 clone 只有 example，没有真实值。")

    # ---- 第 2 层：形态 ----
    print("\n-- 第 2 层：敏感字段名附近的长随机串（排除已知占位符）--")
    shape_hits = 0
    for rel in sorted(files):
        if os.path.basename(rel) in SKIP_SHAPE_FILES:
            continue
        text = read_text(os.path.join(HERE, rel))
        if not text:
            continue
        for lineno, snippet in scan_shape(text, rel):
            shape_hits += 1
            findings.append("疑似硬编码凭据：%s:%d  %s" % (rel, lineno, snippet))
    if not shape_hits:
        print("  [ok]   没有发现")
    else:
        for f in findings:
            if f.startswith("疑似硬编码凭据"):
                print("  [!!]   " + f)

    # ---- 结论 ----
    print("")
    if findings:
        print("结果：发现 %d 处需要人看的东西。" % len(findings))
        print("")
        for f in findings:
            print("  · " + f)
        print("")
        print("怎么办：")
        print("  · 如果是真凭据 —— 先**轮换它**（删文件没用，git 历史里还在），再清理文件。")
        print("  · 如果只是占位符 —— 加进本脚本的 PLACEHOLDER_ALLOW，并写清为什么安全。")
        print("  · 别为了让脚本闭嘴放松判定规则：那等于把护栏拆了。")
        return 1

    print("结果：干净。已跟踪文件里没有真实凭据，也没有疑似硬编码凭据。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[stop] 已手动停止")
        sys.exit(130)
