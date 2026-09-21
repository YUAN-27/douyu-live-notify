#!/usr/bin/env bash
# =============================================================================
# add-swap.sh —— 给 Linux 服务器加交换文件（swap）
#
# 为什么需要它
# ------------
# NapCat 跑在 QQ NT（Electron）之上，常驻内存约 300~700MB。如果宿主机可用内存
# 只剩几百 MB 且没有 swap，内存一紧张就会触发 OOM killer 直接杀进程 ——
# 表现是「QQ 机器人莫名掉线，重启又好了」，日志里看不到任何报错，极难排查。
#
# 加 swap 不会让它变快，但能让它「活着」。这是小内存机器跑 NapCat 的标准解法。
#
# 这个脚本做什么
# --------------
#   1. 检查前置条件（root / 磁盘空间 / 是否已有 swap）
#   2. 创建 /swapfile（默认 2G，可按需指定）
#   3. swapon 启用，并**验证真的生效**（没生效就不写 fstab）
#   4. 写入 /etc/fstab（重启后仍生效；写前备份原文件）
#   5. 显式写入 vm.swappiness（第 5 步有说明 —— 刻意**不**用网上常见的 10）
#
# 幂等：已经配好 swap 会直接退出，不会重复追加 fstab 条目。重复跑是安全的。
#
# 用法
# ----
#   sudo bash add-swap.sh            # 加 2G（推荐）
#   sudo bash add-swap.sh 4          # 加 4G
#   sudo bash add-swap.sh --remove   # 撤销：停用并删除 fstab 条目
# =============================================================================
set -euo pipefail

SWAPFILE="/swapfile"
SYSCTL_CONF="/etc/sysctl.d/99-napcat-swap.conf"
SIZE_GB_DEFAULT=2

die() { echo "[错误] $*" >&2; exit 1; }
info() { echo "  $*"; }

[[ $EUID -eq 0 ]] || die "需要 root：sudo bash $0"

# ---------------------------------------------------------------------------
# 撤销分支
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--remove" ]]; then
  echo "=== 撤销 swap ==="
  if swapon --show=NAME --noheadings 2>/dev/null | tr -d ' ' | grep -qx "$SWAPFILE"; then
    swapoff "$SWAPFILE" && info "已停用 $SWAPFILE"
  else
    info "$SWAPFILE 当前未启用"
  fi
  if [[ -e $SWAPFILE ]]; then
    rm -f "$SWAPFILE"
    info "已删除 $SWAPFILE"
  fi

  if grep -qE "^[^#]*[[:space:]]${SWAPFILE}[[:space:]]" /etc/fstab 2>/dev/null; then
    cp -a /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
    sed -i "\|^[^#]*[[:space:]]${SWAPFILE}[[:space:]]|d" /etc/fstab
    info "已从 /etc/fstab 移除条目（原文件已备份）"
  else
    info "/etc/fstab 里没有该条目"
  fi

  rm -f "$SYSCTL_CONF"
  info "已移除 $SYSCTL_CONF"
  echo
  free -h
  echo "撤销完成。"
  exit 0
fi

SIZE_GB="${1:-$SIZE_GB_DEFAULT}"
[[ $SIZE_GB =~ ^[0-9]+$ ]] || die "大小要写整数（GB），例如：sudo bash $0 2"
(( SIZE_GB >= 1 )) || die "至少要 1G"

echo "=============================================================="
echo " 加 swap（目标 ${SIZE_GB}G）"
echo " $(date '+%F %T %Z')"
echo "=============================================================="
echo

# ---------------------------------------------------------------------------
echo "【1/5】现状"
echo "--------------------------------------------------------------"
free -h || true
echo
if swapon --show 2>/dev/null | grep -q .; then
  echo "  当前已启用的 swap："
  swapon --show | sed 's/^/    /'
  echo
  echo "  [跳过] 已经有 swap 在用了，本脚本不重复添加。"
  echo "         想调整大小：先 sudo bash $0 --remove，再重新跑。"
  echo "         想只看现状：bash mem-report.sh"
  exit 0
fi
info "当前没有启用中的 swap —— 继续。"

# ---------------------------------------------------------------------------
echo
echo "【2/5】磁盘空间"
echo "--------------------------------------------------------------"
avail_kb=$(df -Pk / | awk 'NR==2 {print $4}')
need_kb=$(( SIZE_GB * 1024 * 1024 ))
avail_gb=$(( avail_kb / 1024 / 1024 ))
info "/ 可用 ${avail_gb}GB，需要 ${SIZE_GB}GB"
# 留 1GB 余量，不要把磁盘填满
if (( avail_kb < need_kb + 1048576 )); then
  die "磁盘空间不足（要 ${SIZE_GB}GB + 1GB 余量，实有 ${avail_gb}GB）"
fi
info "空间足够。"

# ---------------------------------------------------------------------------
echo
echo "【3/5】创建 ${SWAPFILE}"
echo "--------------------------------------------------------------"
fstype=$(stat -f -c %T / 2>/dev/null || echo unknown)
info "根文件系统类型：$fstype"

case "$fstype" in
  zfs)
    die "ZFS 上不能用交换文件（会导致系统不稳定）。请改用 zvol，或直接加内存。"
    ;;
  btrfs)
    info "btrfs 需要先关掉 COW，否则交换文件会出问题 —— 先 chattr +C"
    ;;
esac

if [[ -e $SWAPFILE ]]; then
  info "$SWAPFILE 已存在（多半是上次没做完的残留），先清掉重来"
  swapoff "$SWAPFILE" 2>/dev/null || true
  rm -f "$SWAPFILE"
fi

if [[ $fstype == btrfs ]]; then
  touch "$SWAPFILE"
  chattr +C "$SWAPFILE" 2>/dev/null || info "[warn] chattr +C 失败，继续，但可能有问题"
  info "用 dd 写入（btrfs 不能用 fallocate）"
  dd if=/dev/zero of="$SWAPFILE" bs=1M count=$(( SIZE_GB * 1024 )) status=progress
else
  # fallocate 是秒级的；个别文件系统会生成带洞的文件导致 swapon 失败，所以失败就退回 dd
  if fallocate -l "${SIZE_GB}G" "$SWAPFILE" 2>/dev/null; then
    info "fallocate 完成（快）"
  else
    info "fallocate 不可用，改用 dd（慢，请等）"
    dd if=/dev/zero of="$SWAPFILE" bs=1M count=$(( SIZE_GB * 1024 )) status=progress
  fi
fi

chmod 600 "$SWAPFILE"
chown root:root "$SWAPFILE"
mkswap "$SWAPFILE"
info "mkswap 完成"

# ---------------------------------------------------------------------------
echo
echo "【4/5】启用并验证"
echo "--------------------------------------------------------------"
swapon "$SWAPFILE" || die "swapon 失败 —— 可能是文件系统不支持交换文件（$fstype）"

# 关键：确认真的生效了才继续。没生效就写 fstab 会导致下次开机启动失败。
if ! swapon --show=NAME --noheadings 2>/dev/null | tr -d ' ' | grep -qx "$SWAPFILE"; then
  die "swapon 命令没报错，但 swap 并未真正启用。已停在写入 fstab 之前，请把上面的输出发回。"
fi
info "已验证：$SWAPFILE 处于启用状态"
echo
swapon --show | sed 's/^/  /'
echo
free -h | sed 's/^/  /'

# ---------------------------------------------------------------------------
echo
echo "【5/5】持久化 + 参数"
echo "--------------------------------------------------------------"
if grep -qE "^[^#]*[[:space:]]${SWAPFILE}[[:space:]]" /etc/fstab 2>/dev/null; then
  info "/etc/fstab 已有条目，跳过"
else
  cp -a /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
  printf '%s none swap sw 0 0\n' "$SWAPFILE" >> /etc/fstab
  info "已写入 /etc/fstab（原文件已备份，重启后 swap 自动启用）"
fi

# vm.swappiness 为什么写 60 而不是网上到处传的 10
# ------------------------------------------------
# swappiness 越低，内核越**不愿意**把「匿名页」（= 进程真正占着的内存）换出去，
# 转而去丢文件缓存。很多调优教程建议设成 10，那是针对「追求极致延迟的数据库」——
# 宁可丢缓存也不让数据页进出磁盘。
#
# 我们这台机器的场景正好相反：
#   * 最大的一块匿名内存是**长期空闲的 NapCat**（几百 MB，只在有开播事件时才醒）
#   * 真正需要低延迟的是**同一台机器上的个人网页**
# 所以我们要的恰恰是「把空闲的 NapCat 换出去，把 RAM 让给网页」。
# swappiness=60（内核默认值）在这个场景是对的方向。这里显式写下来，
# 免得日后被别的教程改成 10 反而搞坏 —— 那是本末倒置。
#
# 如果网页仍然被挤：可以往上调到 80~100（更积极地换出匿名页）。
cat > "$SYSCTL_CONF" <<'EOF'
# 由 add-swap.sh 写入。配合 douyu-live-notify 的小内存部署场景。
# 说明见脚本内注释：这里刻意不用常见的 10，因为我们要主动换出空闲的 NapCat，
# 把物理内存留给同机的个人网页。
vm.swappiness = 60
EOF
sysctl -p "$SYSCTL_CONF" >/dev/null 2>&1 || sysctl -w vm.swappiness=60 >/dev/null
info "已写入 $SYSCTL_CONF（vm.swappiness = $(cat /proc/sys/vm/swappiness)）"

echo
echo "=============================================================="
echo " 完成。现在的内存状态："
echo "=============================================================="
free -h
echo
echo "后续建议："
echo "  1. 跑一次 bash mem-report.sh，看清是谁在占内存、以及历史上有没有被 OOM 杀过"
echo "  2. NapCat 起来之后，用 docker stats 观察它的实际占用："
echo "       docker stats napcat --no-stream"
echo "     如果它稳定在 700MB 以内，说明这套配置扛得住"
echo "  3. swap 被大量使用（used 长期接近 total）说明物理内存真的不够，"
echo "     那时该考虑加内存或换更轻的推送方案"
echo
echo "撤销：sudo bash $0 --remove"
