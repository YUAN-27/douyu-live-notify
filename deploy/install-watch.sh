#!/usr/bin/env bash
# 把 watch.py 装到 /opt/douyu-live-notify，并装好 systemd 单元。
#
# 设计原则：
#   - 幂等，可以重复执行
#   - 绝不覆盖已有的 config.json（那里面有你填的群号和 token）
#   - 绝不覆盖 /etc/default/douyu-watchdog（告警密钥 + 报平安文案的定制都放那儿）
#   - 覆盖 .py 前先备份，并且**明确告诉你**它被换掉了（不搞静默冲掉）
#   - 不自动 enable 定时器 —— 等 --test-notify 验证通过再开，免得带着错配置空跑
#   - 装完打印文件指纹，方便确认服务器上跑的是不是最新版
#
# 用法：sudo bash install-watch.sh
#
# 如果你在服务器上直接改过 watch.py / selftest.py / watchdog.py，又不想被这次
# 更新覆盖（比如只想先看看 diff），加一个开关：
#     sudo WD_KEEP_LOCAL=1 bash install-watch.sh
# 它会跳过这三个 .py 的更新，其余照装。想改文案/颜文字这类东西，不用改代码，
# 写进 /etc/default/douyu-watchdog 更省事（本脚本从不碰它）。

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
  if [[ -f "$APP_DIR/$f" && "${WD_KEEP_LOCAL:-0}" == "1" ]]; then
    echo "WD_KEEP_LOCAL=1 → 保留已有的 $APP_DIR/$f，不更新"
    continue
  fi
  # 已有旧版先备份，别把服务器上改坏的版本无声冲掉
  if [[ -f "$APP_DIR/$f" ]]; then
    cp -a "$APP_DIR/$f" "$APP_DIR/$f.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cp -a "$PY_DIR/$f" "$APP_DIR/$f"
  echo "已放入 $APP_DIR/$f"
done

# 看门狗在 deploy/ 里，和本脚本同层（不在仓库根的源文件目录），所以从 $UNIT_DIR 取
if [[ -f "$UNIT_DIR/watchdog.py" ]]; then
  if [[ "${WD_KEEP_LOCAL:-0}" == "1" && -f "$APP_DIR/watchdog.py" ]]; then
    echo "WD_KEEP_LOCAL=1 → 保留已有的 $APP_DIR/watchdog.py，不更新"
  elif [[ -f "$APP_DIR/watchdog.py" ]]; then
    # 比指纹：新旧不一致，说明服务器上这份要么被本地改过、要么本来就不是这一版。
    # 升级照做，但绝不静默 —— 备份 + 明确告知 + 给出还原/合并命令。
    old_sum=$(sha256sum "$APP_DIR/watchdog.py" 2>/dev/null | cut -c1-16 || true)
    new_sum=$(sha256sum "$UNIT_DIR/watchdog.py" 2>/dev/null | cut -c1-16 || true)
    changed=0
    if [[ -n "$old_sum" && -n "$new_sum" && "$old_sum" != "$new_sum" ]]; then
      changed=1
    fi
    # 只有真的会换内容（或指纹没法算、不敢断言）才备份，免得重复安装攒一堆没用的 .bak
    if [[ "$changed" == "1" || -z "$old_sum" ]]; then
      bak="$APP_DIR/watchdog.py.bak.$(date +%Y%m%d%H%M%S)"
      cp -a "$APP_DIR/watchdog.py" "$bak"
    fi
    cp -a "$UNIT_DIR/watchdog.py" "$APP_DIR/watchdog.py"
    if [[ "$changed" == "1" ]]; then
      echo "已放入 $APP_DIR/watchdog.py（指纹 ${old_sum} → ${new_sum}）"
      echo "  ⚠️ 新旧指纹不同：如果你在服务器上改过 watchdog.py，这次更新把它整份换掉了，"
      echo "     改动不会自动合并。旧版已备份：$bak"
      echo "     · 想看改了什么：diff -u '$bak' '$APP_DIR/watchdog.py'"
      echo "     · 想改回去：    cp -a '$bak' '$APP_DIR/watchdog.py'"
      echo "     · 想跳过更新： 下次跑之前加 WD_KEEP_LOCAL=1"
      echo "     · 只是想改报平安文案/颜文字？不用改代码，写进"
      echo "       /etc/default/douyu-watchdog 的 DAILY_OK_TITLE / DAILY_OK_BODY /"
      echo "       DAILY_OK_KAOMOJI —— 本脚本从不覆盖那个文件，升级也不会丢。"
    else
      echo "已放入 $APP_DIR/watchdog.py（指纹 ${new_sum:-未知}，与服务器上原有版本一致）"
    fi
  else
    cp -a "$UNIT_DIR/watchdog.py" "$APP_DIR/watchdog.py"
    echo "已放入 $APP_DIR/watchdog.py"
  fi
else
  echo "⚠️ 没找到 watchdog.py（症状：日志里会少一路掉线告警）"
fi

# 打印指纹：以后怀疑「服务器上是不是旧版」，和仓库里的对一下 sha256 前 16 位即可
if command -v sha256sum >/dev/null 2>&1; then
  echo "指纹（sha256 前 16 位）："
  ( cd "$APP_DIR" && sha256sum watch.py selftest.py watchdog.py 2>/dev/null ) \
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
echo "已安装 douyu-watch.service / douyu-watch.timer"

# 看门狗单元。装上但**不 enable** —— 和主定时器一样，等你验证过再开。
if [[ -f "$UNIT_DIR/douyu-watchdog.service" && -f "$UNIT_DIR/douyu-watchdog.timer" ]]; then
  install -m 644 "$UNIT_DIR/douyu-watchdog.service" /etc/systemd/system/douyu-watchdog.service
  install -m 644 "$UNIT_DIR/douyu-watchdog.timer"   /etc/systemd/system/douyu-watchdog.timer
  echo "已安装 douyu-watchdog.service / douyu-watchdog.timer（尚未启用）"
fi

# 看门狗的告警通道配置。**已存在绝不覆盖** —— 里面是你要填的 webhook 密钥。
if [[ ! -f /etc/default/douyu-watchdog && -f "$UNIT_DIR/watchdog.env.example" ]]; then
  install -m 600 "$UNIT_DIR/watchdog.env.example" /etc/default/douyu-watchdog
  echo "已放入 /etc/default/douyu-watchdog（告警通道配置，现在还是空的，见下面【7】）"
elif [[ -f /etc/default/douyu-watchdog ]]; then
  # 里面是 webhook 密钥，权限必须是 600（显式再设一次，不依赖 install 的 -m）
  chmod 600 /etc/default/douyu-watchdog
  echo "/etc/default/douyu-watchdog 已存在，保持不动"
fi

# 把权限打出来核实 —— 这个文件里有 webhook 密钥，权限错了要当场看见，
# 而不是等到某天发现别的用户能读它
if command -v stat >/dev/null 2>&1 && [[ -f /etc/default/douyu-watchdog ]]; then
  echo "  /etc/default/douyu-watchdog 权限：$(stat -c '%a %U:%G' /etc/default/douyu-watchdog)（应为 600 root:root）"
fi

systemctl daemon-reload

# ---- 目录兜底 ----
# unit 里是 StandardOutput=append:/var/log/douyu-watch/tick.log。目录不存在时，
# 服务会在「打开输出文件」这一步就失败（209/STDOUT），而不是自动建目录。
# 这个失败早于 ExecStartPre，所以只能用 tmpfiles.d 在开机阶段建，不能靠 ExecStartPre。
# 规则里同时包含看门狗的 /var/lib/douyu-watchdog。
if [[ -f "$UNIT_DIR/douyu-watch.tmpfiles" ]]; then
  install -m 644 "$UNIT_DIR/douyu-watch.tmpfiles" /etc/tmpfiles.d/douyu-watch.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/douyu-watch.conf 2>/dev/null || true
  echo "已安装 /etc/tmpfiles.d/douyu-watch.conf（保证 /var/log/douyu-watch 与 /var/lib/douyu-watchdog 开机就存在）"
fi
mkdir -p /var/log/douyu-watch
mkdir -p /var/lib/douyu-watchdog
chmod 700 /var/lib/douyu-watchdog

cat <<'EOF'

==================== 装完了，接下来按顺序做 ====================

【1】填配置（必做）
    nano /opt/douyu-live-notify/config.json
      - room_id           ← 斗鱼房间的真实 room_id（不是靓号）
      - onebot.token      ← NapCat 里配的 OneBot token
      - onebot.target_id  ← 目标 QQ 群号

【2】逻辑自检（不联网、不发消息）
    cd /opt/douyu-live-notify && python3 selftest.py && tail -3 selftest_result.txt
    期望最后一行：0 项失败（项数随版本变，别拿它判断版本 —— 上面打印的指纹才准）

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

【7】看门狗（可选，但强烈建议：没有它，掉线和停摆都是静默的）
    a) 先离线自检，确认脚本本身没问题
       cd /opt/douyu-live-notify && python3 watchdog.py --selftest

    b) 配告警通道 —— 这是唯一需要你花几分钟决定的事。
       关键：NapCat 掉线时它自己发不出消息，所以必须有一条**不经过 NapCat** 的通道。
       nano /etc/default/douyu-watchdog
         至少填一项：ALERT_WEBHOOK（推荐）或 ALERT_ONEBOT_PRIVATE（你的主 QQ 号）
       然后验证通道真的通：
         python3 watchdog.py --test-alert

    c) 先只读看一眼当前是否健康（不告警、不重启任何东西）
       python3 watchdog.py --status

    d) 确认无误后启用
       systemctl enable --now douyu-watchdog.timer
       systemctl list-timers douyu-watchdog.timer
       tail -f /var/log/douyu-watch/watchdog.log

    ⚠️ AUTO_RESTART 默认是 1（掉线会自动 docker restart napcat）。
       敢开它的前提是「重启免扫码自动登录」已经验收通过（见 DEPLOY.md）。
       没验过就先在 /etc/default/douyu-watchdog 里设 AUTO_RESTART=0。

【回滚】
    # 只回滚看门狗：
    systemctl disable --now douyu-watchdog.timer
    rm -f /etc/systemd/system/douyu-watchdog.{service,timer}
    systemctl daemon-reload
    # 全部回滚：
    systemctl disable --now douyu-watch.timer douyu-watchdog.timer
    rm -f /etc/systemd/system/douyu-watch.{service,timer}
    rm -f /etc/systemd/system/douyu-watchdog.{service,timer}
    systemctl daemon-reload
    # 注意：不回滚 /var/log/douyu-watch（日志留着排错）和
    #       /var/lib/douyu-watchdog（告警历史留着）、/etc/default/douyu-watchdog（你的密钥）
    #       确认不再用的时候自己删。

===============================================================
EOF
