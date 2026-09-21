#!/usr/bin/env bash
# 把 watch.py 装到 /opt/douyu-live-notify，并装好 systemd 单元。
#
# 设计原则：
#   - 幂等，可以重复执行
#   - 绝不覆盖已有的 config.json（那里面有你填的群号和 token）
#   - 不自动 enable 定时器 —— 等 --test-notify 验证通过再开，免得带着错配置空跑
#   - 装完打印文件指纹，方便确认服务器上跑的是不是最新版
#
# 用法：sudo bash install-watch.sh

set -euo pipefail

APP_DIR=/opt/douyu-live-notify
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash install-watch.sh" >&2
  exit 1
fi

# ---- 找 watch.py / selftest.py ----
# 两种摆法都要支持：
#   a) 源文件和本脚本同目录 —— 用部署包 deploy.zip 解开就是这样
#   b) 本脚本在 deploy/ 子目录、源文件在仓库根 —— 从 GitHub 直接 clone 就是这样
PY_DIR=""
for cand in "$HERE" "$HERE/.." "$HERE/../.."; do
  if [[ -f "$cand/watch.py" && -f "$cand/selftest.py" ]]; then
    PY_DIR="$(cd "$cand" && pwd)"
    break
  fi
done

if [[ -z "$PY_DIR" ]]; then
  cat >&2 <<EOF
找不到 watch.py / selftest.py，装不了。

  这两个文件必须和 install-watch.sh 放在同一目录（或它的上一级）。
  用部署包的话，解开 deploy.zip 后它们本来就在同一层，直接
      cd deploy && sudo bash install-watch.sh
  从 GitHub 直接 clone 的仓库，请进 deploy/ 再跑：
      cd douyu-live-notify/deploy && sudo bash install-watch.sh

已经找过这些目录，都没有：
      $HERE
      $HERE/..
      $HERE/../..
EOF
  exit 1
fi
echo "源文件目录：$PY_DIR"

# systemd 单元和模板跟着脚本走；万一不在再退回源文件目录
UNIT_DIR="$HERE"
if [[ ! -f "$UNIT_DIR/douyu-watch.service" && -f "$PY_DIR/douyu-watch.service" ]]; then
  UNIT_DIR="$PY_DIR"
fi

# ---- 依赖检查 ----
if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3。先执行：apt update && apt install -y python3" >&2
  exit 1
fi
echo "python3：$(python3 -V 2>&1)"

# 建议但非必需：Docker（NapCat 用）。这里只提示，不擅自安装。
if ! command -v docker >/dev/null 2>&1; then
  echo "提示：未检测到 docker —— 如果准备用 Docker 跑 NapCat，需要先装 Docker"
fi

# ---- 放文件 ----
mkdir -p "$APP_DIR"

for f in watch.py selftest.py; do
  # 已有旧版先备份，别把服务器上改坏的版本无声冲掉
  if [[ -f "$APP_DIR/$f" ]]; then
    cp -a "$APP_DIR/$f" "$APP_DIR/$f.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cp -a "$PY_DIR/$f" "$APP_DIR/$f"
  echo "已放入 $APP_DIR/$f"
done

# 打印指纹：以后怀疑「服务器上是不是旧版」，和仓库里的对一下 sha256 前 16 位即可
if command -v sha256sum >/dev/null 2>&1; then
  echo "指纹（sha256 前 16 位）："
  ( cd "$APP_DIR" && sha256sum watch.py selftest.py ) \
    | awk '{ f = $2; sub(/^\*/, "", f); printf "  %s  %s\n", substr($1, 1, 16), f }'
fi

if [[ -f "$APP_DIR/config.json" ]]; then
  echo "config.json 已存在，保持不动（不会覆盖你的 room_id 和 token）"
else
  if [[ -f "$PY_DIR/config.json" ]]; then
    cp -a "$PY_DIR/config.json" "$APP_DIR/config.json"
    echo "已放入 $APP_DIR/config.json"
  else
    tpl="$PY_DIR/config.example.json"
    [[ -f "$tpl" ]] || tpl="$UNIT_DIR/config.example.json"
    cp -a "$tpl" "$APP_DIR/config.json"
    echo "已放入 $APP_DIR/config.json（从模板复制的，里面还是占位符，需要你填）"
  fi
  chmod 600 "$APP_DIR/config.json"
fi

# ---- 装 systemd 单元 ----
install -m 644 "$UNIT_DIR/douyu-watch.service" /etc/systemd/system/douyu-watch.service
install -m 644 "$UNIT_DIR/douyu-watch.timer"   /etc/systemd/system/douyu-watch.timer
systemctl daemon-reload
echo "已安装 douyu-watch.service / douyu-watch.timer"

cat <<'EOF'

==================== 装完了，接下来按顺序做 ====================

【1】填配置（必做）
    nano /opt/douyu-live-notify/config.json
      - room_id           ← 斗鱼房间的真实 room_id（不是靓号）
      - onebot.token      ← NapCat 里配的 OneBot token
      - onebot.target_id  ← 目标 QQ 群号

【2】逻辑自检（不联网、不发消息）
    cd /opt/douyu-live-notify && python3 selftest.py && tail -3 selftest_result.txt
    期望最后一行：结果：24 项通过，0 项失败（共 24 项）
    若显示 18 项 → 这份 watch.py / selftest.py 是旧版，重新解压最新的 deploy.zip

【3】斗鱼侧体检（联网，验证接口还通）
    cd /opt/douyu-live-notify && python3 watch.py --probe YOUR_ROOM_ID
    能打印出主播名、房间标题就说明 room_id 填对了

【4】看当前判定（不入库、不发通知）
    cd /opt/douyu-live-notify && python3 watch.py --once
    如果房间正在放轮播，这里应该是「未开播」—— 这是对的，不是故障

【5】链路验证（会真的往群里发一条测试消息）
    cd /opt/douyu-live-notify && python3 watch.py --test-notify

【6】上面全部通过后，才开定时器
    systemctl enable --now douyu-watch.timer
    systemctl list-timers douyu-watch.timer
    journalctl -u douyu-watch -f          # 看执行日志
    tail -f /var/log/douyu-watch/tick.log # 看心跳（含 loop= 字段）

【回滚】
    systemctl disable --now douyu-watch.timer
    rm -f /etc/systemd/system/douyu-watch.{service,timer}
    systemctl daemon-reload

===============================================================
EOF
