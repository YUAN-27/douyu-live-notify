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
echo "【1】内存 —— NapCat 跑在 QQ NT（Electron）之上，按 300~700MB 做容量规划"
echo "      （官网说的「50~100MB」只是它自身框架层，不含 QQ NT 进程，别按那个规划）"
echo "--------------------------------------------------------------"
free -h 2>/dev/null || echo "  free 命令不可用"
echo
python3 - <<'PY' 2>/dev/null || true
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
    # 预算 = 可用内存 + swap 可用量的一半。
    # 为什么 swap 只算半个：它慢一个数量级，能兜底但不等价于内存。
    budget = avail + swap_free * 0.5
    print("  NapCat 预算: 可用 %.2f GB + swap 折半 %.2f GB = %.2f GB（需要 0.3~0.7 GB）"
          % (avail, swap_free * 0.5, budget))
    print()
    # 「可用内存 × 0.8」= 给系统和其他服务留 20% 的那条经验线（不含 swap）。
    suggest = int(avail * 0.8 * 1024)
    if avail >= 1.5:
        print("  判定：[OK] 可用内存充足，NapCat（0.3~0.7 GB）跑得下")
    elif avail >= 0.8:
        print("  判定：[注意] 可用 %.2f GB，勉强够（NapCat 需要 0.3~0.7 GB）" % avail)
        if swap_free >= 1.0:
            print("          已有 %.2f GB swap 可用 → 可以部署。" % swap_free)
        else:
            print("          没有 swap 兜底 → 先加：sudo bash add-swap.sh")
        print("          必做：确认 .env 的 NAPCAT_MEM_LIMIT ≈ 可用内存 × 0.8（本例约 %dm）"
              % suggest)
    elif budget >= 1.2:
        print("  判定：[注意] 物理内存偏紧（可用 %.2f GB），但 swap 兜得住" % avail)
        print("          预算 %.2f GB ≥ 1.2 GB → 可以部署。代价是高峰时 NapCat 会变慢。" % budget)
        print("          必做：容器上限 NAPCAT_MEM_LIMIT 再收一档，给同机其他服务留余量")
    else:
        print("  判定：[不足] 可用 %.2f GB、swap 可用 %.2f GB，不足以安全承载 NapCat。"
              % (avail, swap_free))
    if avail < 1.5:
        print()
        print("  下一步（完整决策路径见 DEPLOY.md 的 0.3 节）：")
        # 第 1 条按 swap 现状给，避免「已判定可以部署、却又让你去加 swap」的自相矛盾
        if swap_free < 1.0:
            print("    1) 加 swap 兜底（幂等、可撤销）：sudo bash add-swap.sh")
        else:
            print("    1) swap 已够（%.2f GB 可用），无需再加" % swap_free)
        print("    2) 想查内存被谁占了、有没有 OOM 历史 → mem-report.sh（只读）")
        print("    3) 容器上限 NAPCAT_MEM_LIMIT（在 .env 里）取「可用内存 × 0.8」左右")
        print("       ⚠️ 明显高于可用内存就等于没设：容器一个人吃光，宿主机照样被拖进 OOM")
        print("    4) 若有 OOM 历史、或占用大头就是同机那个网页 → 改走零内存推送通道")
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
echo " 预检结束。"
echo "   内存判定为「不足」→ 按上面「下一步」的四条走，或看 DEPLOY.md 的 0.3 节"
echo "   目标目录被占用   → 先确认那是不是别的项目，别覆盖"
echo "=============================================================="
