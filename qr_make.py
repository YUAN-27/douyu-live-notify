#!/usr/bin/env python3
"""把 NapCat 的登录二维码变成手机能扫的图片（全程本地，不经过第三方网站）。

为什么需要它
------------
服务器上的 NapCat 是 headless 的，二维码只能从容器日志里取出来。
日志里给的是一个「二维码解码URL」，本质是**一次性登录凭据**——
把它贴进在线二维码网站，等于把这个 token 交给对方。
这个脚本在你自己的机器上完成转换，不联网、不外传。

输入方式（三选一）
------------------
  # 1) 有日志里的「二维码解码URL」——最省事
  python qr_make.py --url "https://txz.qq.com/p?k=xxxx&f=xxxx"

  # 2) 拿到了 qrcode.png 的 base64 文本
  python qr_make.py --b64file qrcode.b64

  # 3) base64 直接贴命令行
  python qr_make.py --b64str "iVBORw0KGgo..."

输出
----
  qr.png（默认；用手机 QQ 扫它）
  没有 Pillow 时自动改输出 qr.svg —— 用浏览器打开，扫屏幕即可。

依赖
----
  「--url」方式需要 qrcode：
      python -m pip install qrcode
  想输出 PNG 还需要 Pillow（缺少时自动退回 SVG）：
      python -m pip install pillow
  「--b64str / --b64file」方式只用标准库。

不想装依赖也可以：把那个 URL 贴到任意在线二维码工具生成图片即可，
但要知道那等于把一次性登录凭据交给对方，用完即弃、别留在页面上。
"""
import argparse
import base64
import os
import sys

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def die(msg):
    print("[error] %s" % msg, file=sys.stderr)
    sys.exit(1)


def make_from_url(url, out):
    try:
        import qrcode
    except ImportError:
        print("[error] 缺少 qrcode 库。装它：", file=sys.stderr)
        print("        python -m pip install qrcode pillow", file=sys.stderr)
        print("        或者：把下面的 URL 贴进任意在线二维码工具生成图片", file=sys.stderr)
        print("        %s" % url, file=sys.stderr)
        sys.exit(1)

    # box_size 稍大一点，屏幕上拍照更好识别
    qr = qrcode.QRCode(border=2, box_size=10,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)

    try:
        qr.make_image().save(out)
        print("[ok] 已按 URL 生成二维码（PNG）")
    except Exception as e:
        # 多半是没装 Pillow。SVG 是纯 Python 生成的，不需要 Pillow。
        svg_out = os.path.splitext(out)[0] + ".svg"
        import qrcode.image.svg
        qr2 = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr2.add_data(url)
        qr2.make(fit=True)
        img = qr2.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        with open(svg_out, "wb") as fp:
            img.save(fp)
        print("[warn] 存 PNG 失败（%s），已改存 SVG" % type(e).__name__)
        print("       用浏览器打开它，然后扫屏幕 —— 记得放大到全屏")
        out = svg_out

    print("     URL 长度: %d 字符" % len(url))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="把 NapCat 的登录二维码变成可扫的图片（本地转换，不联网）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="日志里的「二维码解码URL」（txz.qq.com 那种）")
    g.add_argument("--b64file", help="存放 base64 文本的文件")
    g.add_argument("--b64str", help="base64 字符串本身")
    ap.add_argument("-o", "--out", default="qr.png", help="输出文件名，默认 qr.png")
    a = ap.parse_args()

    if a.url:
        url = a.url.strip().strip('"').strip("'")
        if not url.lower().startswith(("http://", "https://")):
            die("这不像一个 URL：%s" % url[:60])
        out = make_from_url(url, a.out)
    else:
        raw = a.b64str if a.b64str else open(a.b64file, "r", encoding="utf-8").read()
        raw = "".join(raw.split())          # 容忍换行和空格
        if raw.startswith("data:"):          # 容忍 data URL 前缀
            raw = raw.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as e:
            die("base64 解码失败：%s" % e)
        if data[:8] != PNG_MAGIC:
            die("解出来的不是 PNG（文件头 %r）。多半是复制时被截断了。" % data[:8])
        out = a.out
        with open(out, "wb") as fp:
            fp.write(data)
        print("[ok] 已从 base64 还原出图片")

    if not os.path.exists(out):
        die("没有生成输出文件")
    size = os.path.getsize(out)
    if size < 200:
        die("文件太小（%d 字节），多半不对" % size)
    print("     输出: %s  (%d bytes)" % (os.path.abspath(out), size))
    print()
    print("→ 手机 QQ 扫它。二维码有效期只有约 1~2 分钟，过期就让服务器重新打印一次。")


if __name__ == "__main__":
    main()
