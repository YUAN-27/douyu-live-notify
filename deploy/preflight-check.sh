#!/usr/bin/env bash
# 只读预检：确认服务器上已有的服务（网站、其他容器等）不会被本次部署影响。
#
# 这个脚本只「看」，不改任何文件、不重启任何服务、不装任何东西。
# 随便跑，跑几次都行。
#
# 用法：bash preflight-check.sh
#
# 重点补两项容易被忽略的信息：内存（NapCat 常驻 400MB~1GB）、80/443 端口占用。

echo "=============================================================="
echo " 部署前共存预检（只读，不会改动任何东西）"
echo " $(date '+%F %T %Z')"
echo "=============================================================="

# ---------- 1. 内存 ----------
echo
echo "【1】内存 —— NapCat 是 Electron 应用，常驻约 400MB~1GB"
echo "--------------------------------------------------------------"
free -h 2>/dev/null || echo "  free 命令不可用"
echo
python3 - <<'PY' 2>/dev/null || true
import re
try:
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        mem[k.strip()] = int(v.split()[0])  # kB
    total = mem.get("MemTotal", 0) / 1048576
    avail = mem.get("MemAvailable", 0) / 1048576
    swap_total = mem.get("SwapTotal", 0) / 1048576
    swap_free = mem.get("SwapFree", 0) / 1048576
    print("  总内存   : %.2f GB" % total)
    print("  可用内存 : %.2f GB" % avail)
    print("  Swap     : %.2f GB 总量 / %.2f GB 可用" % (swap_total, swap_free))
    print()
    if avail >= 1.5:
        print("  判定：[OK] 可用内存充足，NapCat 跑得下")
    elif avail >= 0.8:
        print("  判定：[注意] 勉强够。NapCat 高峰可能触发 OOM，建议观察 free -h")
    else:
        print("  判定：[警告] 可用内存不足 800MB。先查清个人网页占了多少，")
        print("        再决定是加内存、加 swap，还是把 NapCat 换成更轻的方案")
    if swap_total == 0 and avail < 1.5:
        print("  建议：没配 swap。加 1~2GB swap 能显著降低被 OOM killer 打死的概率：")
        print("        fallocate -l 2G /swapfile && chmod 600 /swapfile")
        print("        mkswap /swapfile && swapon /swapfile")
        print("        echo '/swapfile none swap sw 0 0' >> /etc/fstab")
except Exception as e:
    print("  读取 /proc/meminfo 失败：%s" % e)
PY

# ---------- 2. Web 端口占用 ----------
echo
echo "【2】常见 Web 端口占用 —— 确认已有服务的端口，以及跟 NapCat 是否撞车"
echo "--------------------------------------------------------------"
if ss -lntp >/dev/null 2>&1; then
  hit=$(ss -lntp 2>/dev/null | grep -E ':(80|443|8080|8000|8443|8888|3000|3001|6099)\b' || true)
  if [[ -n $hit ]]; then
    echo "$hit" | sed 's/^/  /'
  else
    echo "  以上端口全部空闲"
  fi
else
  echo "  ss 不可用，改用 netstat："
  netstat -lntp 2>/dev/null | grep -E ':(80|443|8080|8000|8443|8888|3000|3001|6099)\b' | sed 's/^/  /' || echo "  查询失败"
fi
echo
echo "  说明：本次部署只用 3000（OneBot）和 6099（NapCat WebUI），且都只绑 127.0.0.1。"
echo "       已有服务若在 80/443，互不干扰。"

# ---------- 3. 宿主上的 Web 服务 ----------
echo
echo "【3】宿主机上正在运行的服务（非 Docker）"
echo "--------------------------------------------------------------"
found=$(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
        | awk '{print $1}' \
        | grep -Ei 'nginx|caddy|apache|httpd|node|npm|pm2|gunicorn|uwsgi|php|java|mysql|redis|postgres' || true)
if [[ -n $found ]]; then
  echo "$found" | sed 's/^/  /'
else
  echo "  未发现常见的 Web / 数据库类 systemd 服务"
fi
echo
echo "  systemd 单元总数（仅统计用）：$(systemctl list-units --type=service --no-legend --no-pager 2>/dev/null | wc -l)"
echo "  其中 douyu-* 相关：$(systemctl list-unit-files 'douyu*' --no-legend --no-pager 2>/dev/null | wc -l) 个（首次部署应为 0）"

# ---------- 4. Docker 容器与自启策略 ----------
echo
echo "【4】Docker 容器清单 —— 这份清单决定「重启 Docker」时谁会掉线"
echo "--------------------------------------------------------------"
if ! command -v docker >/dev/null 2>&1; then
  echo "  未安装 docker（如果打算用 Docker 跑 NapCat，需要先装）"
else
  echo "  -- 全部容器（含已停止）--"
  docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | sed 's/^/  /'
  echo
  echo "  -- 重启策略（关键：none 的容器重启 Docker 后不会自愈）--"
  ids=$(docker ps -aq 2>/dev/null || true)
  if [[ -n $ids ]]; then
    # shellcheck disable=SC2086
    docker inspect -f '  {{.Name}}  restart={{.HostConfig.RestartPolicy.Name}}  running={{.State.Running}}' $ids 2>/dev/null
    echo
    echo "  提醒：restart=none / 空 的容器，在 setup-docker-mirror.sh 重启 Docker 后"
    echo "        不会自己起来。该脚本已内置「记录清单 → 重启 → 核对 → 自动拉起」，"
    echo "        但动手前你应该知道哪些服务会闪断。"
  else
    echo "  没有任何容器（这台机器上目前不用 Docker 跑服务）"
  fi
fi

# ---------- 5. 防火墙 / iptables ----------
echo
echo "【5】防火墙状态 —— Docker 起容器会往 iptables 插链"
echo "--------------------------------------------------------------"
if command -v ufw >/dev/null 2>&1; then
  echo "  ufw: $(ufw status 2>/dev/null | head -1)"
else
  echo "  ufw: 未安装"
fi
echo "  iptables 规则条数: $(iptables -S 2>/dev/null | wc -l)（部署后会增加几条 Docker 链）"
echo "  DOCKER 链是否已存在: $(iptables -S 2>/dev/null | grep -c '^-N DOCKER' || echo 0)"
echo
echo "  说明：本次端口只发布到 127.0.0.1，不会新增任何对公网开放的规则。"

# ---------- 6. 目录占用 ----------
echo
echo "【6】目标目录是否已被占用"
echo "--------------------------------------------------------------"
for d in /opt/napcat /opt/douyu-live-notify; do
  if [[ -e $d ]]; then
    echo "  ⚠️  $d 已存在！内容："
    ls -la "$d" 2>/dev/null | head -10 | sed 's/^/      /'
    echo "      → 部署前先确认这不是别的项目占用的目录"
  else
    echo "  [OK] $d 不存在（可用）"
  fi
done

echo
echo "=============================================================="
echo " 预检结束。若内存判定为「不足」或目标目录被占用，先处理再继续部署。"
echo "=============================================================="
