#!/usr/bin/env bash
# 斗鱼开播提醒 · 「开播了但群里没收到」——一条命令定位
#
# 只读：不改配置、不动状态文件、不发消息。
# 用法：  sudo bash why-no-notify.sh
#
# 排查顺序是刻意的 —— 从「有没有在跑」到「跑了但为什么被拦下」，
# 每一步的结论都对应一个确定的修法，见最后的【结论速查】。

set -uo pipefail

APP="${APP_DIR:-/opt/douyu-live-notify}"
LOG="${TICK_LOG:-/var/log/douyu-watch/tick.log}"
hr() { printf '\n===== %s =====\n' "$1"; }

hr "0. 当前配置（token 已脱敏）"
python3 - "$APP/config.json" <<'PY'
import json, sys
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print("读不了 %s：%s" % (sys.argv[1], exc))
    raise SystemExit(0)
ob = dict(cfg.get("onebot") or {})
if ob.get("token"):
    ob["token"] = "<已设置，已隐藏>"
print(json.dumps({
    "room_id": cfg.get("room_id"),
    "channels": cfg.get("channels"),
    "confirm_rounds": cfg.get("confirm_rounds"),
    "log_heartbeat": cfg.get("log_heartbeat"),
    "treat_loop_as_live": cfg.get("treat_loop_as_live"),
    "notify_on_end": cfg.get("notify_on_end"),
    "onebot": ob,
}, ensure_ascii=False, indent=2, sort_keys=True))
PY

hr "1. 主定时器（谁负责每分钟跑一轮）"
echo "enabled : $(systemctl is-enabled douyu-watch.timer 2>&1)"
echo "active  : $(systemctl is-active  douyu-watch.timer 2>&1)"
systemctl list-timers douyu-watch.timer --no-pager 2>&1 | head -3
echo "--- 最近一次 douyu-watch.service 结果 ---"
systemctl show douyu-watch.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp 2>&1

hr "2. 心跳日志（最近 8 轮）"
if [[ -f "$LOG" ]]; then
  echo "文件：$LOG（最后写入 $(date -r "$LOG" '+%Y-%m-%d %H:%M:%S')，距今 $(( $(date +%s) - $(date -r "$LOG" +%s) )) 秒）"
  tail -n 8 "$LOG"
else
  echo "❌ 没有 $LOG —— 说明这个服务一次都没成功跑起来过"
fi

hr "3. 日志里的状态切换与发送失败"
if [[ -f "$LOG" ]]; then
  echo "--- 最近的 [change]（状态切换，有它才会推）---"
  grep -n '\[change\]' "$LOG" | tail -n 5 || echo "（一条都没有：从来没判定过状态切换）"
  echo "--- 最近的 [error]（有它说明推了但没送出去）---"
  grep -n '\[error\]' "$LOG" | tail -n 5 || echo "（没有发送失败记录）"
  echo "--- 最近的 loop= 取值分布（判断是不是被当成轮播拦下了）---"
  grep -o 'loop=[^ ]*' "$LOG" | sort | uniq -c | tail -n 5 || true
fi

hr "4. 状态文件（机器认为主播现在在不在播）"
ROOM_ID="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8')).get('room_id',''))" "$APP/config.json" 2>/dev/null)"
STATE="$APP/state_${ROOM_ID}.json"
echo "文件：$STATE"
if [[ -f "$STATE" ]]; then
  python3 - "$STATE" <<'PY'
import json, sys
st = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ("is_live", "pending", "pending_n", "rounds", "last_change_at")
print(json.dumps({k: st.get(k) for k in keys}, ensure_ascii=False, indent=2))
PY
else
  echo "❌ 状态文件不存在"
fi

hr "5. 斗鱼侧此刻的真实状态（不入库、不发通知）"
( cd "$APP" && timeout 60 python3 watch.py --once 2>&1 | tail -n 20 )

hr "6. NapCat 与 OneBot 通道"
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1 | grep -i napcat || echo "❌ 没看到 napcat 容器在跑"
else
  echo "（没装 docker 命令）"
fi
python3 - "$APP/config.json" "$LOG" "$APP" <<'PY'
import json, sys, urllib.request

cfg_path, log_path, APP_DIR_HINT = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    ob = (json.load(open(cfg_path, encoding="utf-8")).get("onebot") or {})
except Exception as exc:
    print("读配置失败：%s" % exc); raise SystemExit(0)
base = (ob.get("base") or "").rstrip("/")
if not base:
    print("❌ onebot.base 没配"); raise SystemExit(0)


def call(path):
    req = urllib.request.Request(base + path)
    if ob.get("token"):
        req.add_header("Authorization", "Bearer " + ob["token"])
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


login_ok = False
try:
    d = (call("/get_login_info").get("data") or {})
    login_ok = True
    print("✅ /get_login_info：QQ %s（%s）" % (d.get("user_id"), d.get("nickname")))
except Exception as exc:
    print("❌ /get_login_info 不通（%s）：%s" % (base, exc))

if login_ok:
    try:
        st = (call("/get_status").get("data") or {})
        print("    /get_status   ：online=%s good=%s" % (st.get("online"), st.get("good")))
    except Exception as exc:
        print("    /get_status 查询失败：%s" % exc)

# 「半死」签名：接口说在线，消息却发不出去。这是在线探测抓不到的一种状态。
# 关键：必须区分「现在正坏着」和「几小时前坏过、已经好了」——
# 判断依据是最后一条失败离日志末尾有多远（一轮约 1 行，150 行 ≈ 2.5 小时）。
NEAR = 150
try:
    lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
except Exception:
    lines = []

KEYS = ("sendMsg", "1006514", "网络连接异常", "NodeIKernelMsgService")
sig_idx = [i for i, ln in enumerate(lines)
           if "[error]" in ln and any(k in ln for k in KEYS)]

if sig_idx:
    last = sig_idx[-1]
    tail_gap = len(lines) - 1 - last
    print("")
    print("⚠️ 日志里有「登录态半死」的签名：共 %d 条，最后一条在第 %d 行"
          "（日志共 %d 行，其后还有 %d 行）。" % (len(sig_idx), last + 1, len(lines), tail_gap))
    print("   " + lines[last].strip()[:160])
    if tail_gap <= NEAR:
        print("   → **现在多半还坏着**。接口还在应答，但消息通道已断。")
        print("     `docker restart` 修不好这个：重启只会让缓存 token 被拒、退回报码。")
        print("     要重新扫码登录：")
        print("       docker restart napcat && sleep 25 && \\")
        print("         docker logs -n 200 napcat | grep txz.qq.com")
        print("     拿到链接后在本机跑 qr_make.py 出图（别贴到在线二维码网站，那是登录凭据）。")
    else:
        print("   → 这是**历史记录**（之后已连续跑了 %d 轮没有新的失败），"
              "不一定是当前故障。" % tail_gap)
        print("     如果开播提醒这次确实没收到，按第 3、4 节继续查；")
        print("     想确认发送通道此刻是否正常，发一条测试消息验证（会真的进群）：")
        print("       cd %s && python3 watch.py --test-notify" % APP_DIR_HINT)
PY

hr "结论速查"
printf '%s\n' \
"① 第 1 节 active 不是 active          → 定时器没跑，直接原因。修：systemctl enable --now douyu-watch.timer" \
"② 第 2 节没有 LIVE 行、只有 LOOP(轮播) → 被 treat_loop_as_live=false 主动拦下，不是故障。斗鱼侧 loop 标志还没回 0（刚开播/首页仍显示轮播时常见）" \
"③ 第 2 节是 LIVE 但没有 [change]      → 状态卡在 is_live=true（上次开播没正常收尾）。修：python3 watchdog.py --recover-notify" \
"④ 有 [change] 也有 [error]            → 判定对了，但 OneBot 发送失败。先看第 6 节是哪种：
                                         · 接口不通/超时 → 容器假死，docker restart napcat 可修
                                         · 接口通、但日志有 sendMsg / 1006514 / 网络连接异常
                                           → 登录态半死，**restart 修不好**，必须重新扫码登录" \
"⑤ 第 5 节说未开播、但直播页确实在播   → 接口给的数据本身就判成轮播，同 ②" \
"⑥ 第 1 节 Result 不是 success         → 服务执行失败。看 journalctl -u douyu-watch -n 50（209/STDOUT 是 /var/log/douyu-watch 丢了）"
