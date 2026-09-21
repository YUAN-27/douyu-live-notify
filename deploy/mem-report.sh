#!/usr/bin/env bash
# =============================================================================
# mem-report.sh —— 查清「内存到底被谁吃了」，以及这台机器有没有被 OOM 杀过
#
# 只读：不写任何文件、不重启任何服务、不装东西。跑几次都行。
#
# 为什么需要它
# ------------
# `free -h` 只告诉你「剩多少」，不告诉你「被谁占了」。而在一台既要跑个人网页、
# 又要跑 NapCat 的小内存机器上，这两件事同等重要：
#
#   * 如果占用大头是**可回收的缓存** → 其实没事，NapCat 能跑
#   * 如果占用大头是**别的进程的匿名内存** → 硬塞 NapCat 会触发内核 OOM killer，
#     而 OOM killer 是按内存占用大小挑牺牲品的，**你的网页很可能就是那个最大的**
#
# 用法：bash mem-report.sh
# =============================================================================

hr() { echo "--------------------------------------------------------------"; }

echo "=============================================================="
echo " 内存归因报告（只读）"
echo " $(date '+%F %T %Z')   $(uname -srm)"
echo "=============================================================="

# ---------------------------------------------------------------------------
echo
echo "【1】总览"
hr
free -h 2>/dev/null || echo "  free 不可用"
echo
if [[ -r /proc/meminfo ]]; then
  grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapCached|SwapTotal|SwapFree|Slab|SReclaimable|SUnreclaim|Shmem|Committed_AS)' /proc/meminfo \
    | sed 's/^/  /'
fi
echo
echo "  当前启用的 swap："
if swapon --show 2>/dev/null | grep -q .; then
  swapon --show | sed 's/^/    /'
else
  echo "    （没有 swap）"
fi
echo
echo "  vm.swappiness = $(cat /proc/sys/vm/swappiness 2>/dev/null || echo '?')"
echo "  vm.overcommit_memory = $(cat /proc/sys/vm/overcommit_memory 2>/dev/null || echo '?')"

# ---------------------------------------------------------------------------
echo
echo "【2】谁在占内存（按 RSS 排序，前 15）"
hr
echo "  注意：RSS 会把共享内存重复计算，所以合计值会**高估**真实占用，仅用于找大头。"
echo
printf "  %-8s %-10s %6s %11s  %s\n" PID USER %MEM RSS-KB COMMAND
ps -eo pid,user,pmem,rss,args --sort=-rss 2>/dev/null | tail -n +2 | head -n 15 | \
awk '{
  cmd=""
  for (i=5; i<=NF; i++) cmd = cmd " " $i
  printf "  %-8s %-10s %6s %11s  %.70s\n", $1, $2, $3, $4, cmd
}'
echo
rss_sum=$(ps -eo rss --no-headers 2>/dev/null | awk '{s+=$1} END {printf "%.2f", s/1048576}')
echo "  全部进程 RSS 合计（高估值）：${rss_sum:-?} GB"

# ---------------------------------------------------------------------------
echo
echo "【3】内存压力信号（PSI —— 比「剩多少」更能说明问题）"
hr
if [[ -r /proc/pressure/memory ]]; then
  cat /proc/pressure/memory | sed 's/^/  /'
  echo
  echo "  解读：avg10 是「最近 10 秒内因内存不足而卡住的进程占比（%）」。"
  echo "        长期 > 0 说明已经在承压；> 10 说明已经很吃力。"
else
  echo "  内核不支持 PSI（< 4.20），跳过"
fi
echo
echo "  -- 最近 3 秒的 swap 换入/换出（si/so，正常应接近 0）--"
if command -v vmstat >/dev/null 2>&1; then
  vmstat 1 3 2>/dev/null | sed 's/^/  /'
else
  echo "  vmstat 未安装"
fi

# ---------------------------------------------------------------------------
echo
echo "【4】Docker 容器内存"
hr
if ! command -v docker >/dev/null 2>&1; then
  echo "  未安装 docker"
else
  if docker ps -q 2>/dev/null | grep -q .; then
    docker stats --no-stream --format "  {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" 2>/dev/null
    echo
    echo "  -- 有没有设过内存上限（Memory=0 表示没限制）--"
    docker inspect -f '  {{.Name}}  Memory={{.HostConfig.Memory}}  MemorySwap={{.HostConfig.MemorySwap}}  OOMKilled={{.State.OOMKilled}}  Restart={{.HostConfig.RestartPolicy.Name}}' $(docker ps -aq 2>/dev/null) 2>/dev/null
  else
    echo "  没有运行中的容器"
  fi
fi

# ---------------------------------------------------------------------------
echo
echo "【5】systemd 按 cgroup 排序的内存占用"
hr
if command -v systemd-cgtop >/dev/null 2>&1; then
  systemd-cgtop -m --batch --iterations=1 --order=memory 2>/dev/null | head -n 12 | sed 's/^/  /'
else
  echo "  systemd-cgtop 不可用"
fi
echo
echo "  -- 有没有服务被设了内存上限（MemoryMax / MemoryHigh）--"
found=0
while read -r unit; do
  [[ -z $unit ]] && continue
  val=$(systemctl show "$unit" -p MemoryMax -p MemoryHigh --value 2>/dev/null | tr '\n' ' ')
  echo "    $unit  ->  $val"
  found=1
done < <(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1}')
[[ $found -eq 0 ]] && echo "    （没有运行中的服务）"

# ---------------------------------------------------------------------------
echo
echo "【6】历史上被 OOM killer 杀过吗"
hr
oom_lines=""
if command -v journalctl >/dev/null 2>&1; then
  oom_lines=$(journalctl -k --no-pager 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -n 15 || true)
fi
if [[ -z $oom_lines ]] && command -v dmesg >/dev/null 2>&1; then
  oom_lines=$(dmesg 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -n 15 || true)
fi
if [[ -n $oom_lines ]]; then
  echo "  ⚠️  发现 OOM 记录："
  echo "$oom_lines" | sed 's/^/    /'
else
  echo "  [OK] 没有发现 OOM 记录"
fi
echo
echo "  容器的 OOMKilled 标志："
if command -v docker >/dev/null 2>&1 && docker ps -aq 2>/dev/null | grep -q .; then
  docker inspect --format '    {{.Name}}  OOMKilled={{.State.OOMKilled}}  ExitCode={{.State.ExitCode}}' $(docker ps -aq 2>/dev/null) 2>/dev/null
else
  echo "    （无容器）"
fi

echo
echo "  -- systemd-oomd（Ubuntu 24.04 默认启用，会按 cgroup 主动杀进程）--"
if systemctl list-unit-files systemd-oomd.service >/dev/null 2>&1; then
  echo "    状态：$(systemctl is-active systemd-oomd 2>/dev/null || echo 'inactive')"
  if command -v journalctl >/dev/null 2>&1; then
    oomd=$(journalctl -u systemd-oomd --no-pager -n 8 2>/dev/null | grep -iE 'kill|oom' | tail -n 5 || true)
    [[ -n $oomd ]] && echo "$oomd" | sed 's/^/    /'
  fi
else
  echo "    未安装"
fi

# ---------------------------------------------------------------------------
echo
echo "【7】结论与建议"
hr
if [[ -r /proc/meminfo ]]; then
  avail_mb=$(awk '/^MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)
  swap_mb=$(awk '/^SwapTotal/{printf "%d", $2/1024}' /proc/meminfo)
  echo "  可用内存：${avail_mb} MB       swap：${swap_mb} MB"
  echo
  if (( swap_mb == 0 )); then
    echo "  ⚠️  没有 swap。一旦内存见顶，内核只能杀进程。"
    echo "      → 先跑：sudo bash add-swap.sh"
    echo
  fi
  # NapCat（Docker 版）实测常驻 300~700MB。官方首页声称 50~100MB，
  # 那指的是 NapCat 自身框架层，不含它承载的 QQ NT 进程 —— 别按 50MB 做容量规划。
  if (( avail_mb >= 900 )); then
    echo "  NapCat（Docker）需要约 300~700MB → 现在就有余量，可以直接部署。"
  elif (( avail_mb + swap_mb >= 900 )); then
    echo "  物理内存不够，但加上 swap 之后够跑 → 可以部署，但要留意 swap 用量。"
    echo "      → 部署后用 sysstat/vmstat 观察，如果 swap 长期占到 70% 以上，说明真的该加内存了。"
  else
    echo "  ❌ 即使算上 swap 也不宽裕。建议先做这两件事之一："
    echo "      a) 从第 2/5/6 节里找出占用大头，看能不能压下来"
    echo "      b) 换成零内存的推送通道（企业微信 / 钉钉 / 飞书 / Telegram / Bark 群机器人 webhook）"
  fi
fi

echo
echo "=============================================================="
echo " 报告结束。若【6】有 OOM 记录，或【7】判定不宽裕，"
echo " 把本报告全文发回来再决定下一步 —— 不要直接硬上。"
echo "=============================================================="
