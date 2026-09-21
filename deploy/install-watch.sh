#!/usr/bin/env bash
# 把 watch.py 装到 /opt/douyu-live-notify，并装好 systemd 单元。
#
# 设计原则：
#   - 幂等，可以重复执行
#   - 绝不覆盖已有的 config.json（那里面有你填的群号和 token）
#   - 不自动 enable 定时器 —— 等 --test-notify 验证通过再开，免得带着错配置空跑
#
# 用法：sudo bash install-watch.sh

set -euo pipefail

APP_DIR=/opt/douyu-live-notify
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash install-watch.sh" >&2
  exit 1
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
  if [[ ! -f "$SRC_DIR/$f" ]]; then
    echo "缺少源文件 $SRC_DIR/$f" >&2
    exit 1
  fi
  # 已有旧版先备份，别把服务器上改坏的版本无声冲掉
  if [[ -f "$APP_DIR/$f" ]]; then
    cp -a "$APP_DIR/$f" "$APP_DIR/$f.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cp -a "$SRC_DIR/$f" "$APP_DIR/$f"
  echo "已放入 $APP_DIR/$f"
done

if [[ -f "$APP_DIR/config.json" ]]; then
  echo "config.json 已存在，保持不动（不会覆盖你的 room_id 和 token）"
elif [[ -f "$SRC_DIR/config.json" ]]; then
  cp -a "$SRC_DIR/config.json" "$APP_DIR/config.json"
  chmod 600 "$APP_DIR/config.json"
  echo "已放入 $APP_DIR/config.json"
else
  cp -a "$SRC_DIR/config.example.json" "$APP_DIR/config.json"
  chmod 600 "$APP_DIR/config.json"
  echo "已放入 $APP_DIR/config.json（从模板复制的，里面还是占位符，需要你填）"
fi

# ---- 装 systemd 单元 ----
install -m 644 "$SRC_DIR/douyu-watch.service" /etc/systemd/system/douyu-watch.service
install -m 644 "$SRC_DIR/douyu-watch.timer"   /etc/systemd/system/douyu-watch.timer
systemctl daemon-reload
echo "已安装 douyu-watch.service / douyu-watch.timer"

cat <<'EOF'

==================== 装完了，接下来按顺序做 ====================

【1】填配置（必做）
    nano /opt/douyu-live-notify/config.json
      - room_id           ← 斗鱼房间的真实 room_id（不是靓号）
      - onebot.token      ← NapCat 里配的 OneBot token
      - onebot.target_id  ← 目标 QQ 群号

【2】斗鱼侧体检（联网，验证接口还通）
    cd /opt/douyu-live-notify && python3 watch.py --probe YOUR_ROOM_ID
    能打印出主播名、房间标题就说明 room_id 填对了

【3】看当前判定（不入库、不发通知）
    cd /opt/douyu-live-notify && python3 watch.py --once
    如果房间正在放轮播，这里应该是「未开播」—— 这是对的，不是故障

【4】链路验证（会真的往群里发一条测试消息）
    cd /opt/douyu-live-notify && python3 watch.py --test-notify

【5】上面全部通过后，才开定时器
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
