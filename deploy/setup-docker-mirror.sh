#!/usr/bin/env bash
# 探测可用的 Docker 镜像加速源 → 写入 /etc/docker/daemon.json → 真的拉一次镜像验证。
#
# 为什么需要它：
#   国内不少服务器直连 Docker Hub 会超时或不通（curl 返回 000），
#   而 NapCat 的镜像正好只发布在 Docker Hub 上。
#   registry-mirrors 只对 docker.io 的镜像生效 —— 正好是我们要的。
#
# 设计取舍：
#   - 不硬编码某个镜像源。网上流传的地址过几个月就失效，所以先探测再写入，
#     只写「当前实测活着」的那几个；Docker 会按顺序尝试，第一个挂了自动换下一个。
#   - 用 curl -4 强制 IPv4 探测。有些镜像站只有 IPv6，而 Docker 默认不一定走 IPv6，
#     探到却拉不动会更难排查。
#   - 幂等，可重复执行；已有的 daemon.json 会先备份，其他配置项不会被覆盖。
#
# 用法：sudo bash setup-docker-mirror.sh

set -uo pipefail   # 故意不加 -e：单个源探测失败是正常现象

DAEMON=/etc/docker/daemon.json
TARGET_IMAGE=mlikiowa/napcat-docker:latest

# 候选源（按社区实测口碑排序，2026-09 整理）。不是官方白名单，只当候选。
CANDIDATES=(
  "https://docker.m.daocloud.io"
  "https://docker.1ms.run"
  "https://docker.1panel.live"
  "https://docker.xuanyuan.me"
  "https://hub.rat.dev"
  "https://dockerproxy.net"
  "https://docker.mirrors.ustc.edu.cn"
  "https://hub-mirror.c.163.com"
  "https://mirror.baidubce.com"
  "https://hub.crdz.gq"
  "https://hub.firefly.store"
)

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash setup-docker-mirror.sh" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "缺 curl。先执行：apt update && apt install -y curl" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "缺 python3。先执行：apt update && apt install -y python3" >&2
  exit 1
fi

echo "=============================================================="
echo "第 1 步：探测 ${#CANDIDATES[@]} 个镜像源（强制 IPv4，每个超时 6 秒）"
echo "=============================================================="
GOOD=()
for m in "${CANDIDATES[@]}"; do
  # /v2/ 是 Docker Registry API 的固定入口。
  # 200 = 匿名可读；401 = 需要鉴权但仍说明这是个活的 registry。两者都算可用。
  # curl 连不上时 -w 也会输出 000，所以不用额外兜底。
  code=$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 6 "$m/v2/")
  [[ -z "$code" ]] && code="000"
  if [[ "$code" == "200" || "$code" == "401" ]]; then
    echo "  [可用] $m   (HTTP $code)"
    GOOD+=("$m")
  else
    echo "  [跳过] $m   (HTTP $code)"
  fi
done

if [[ ${#GOOD[@]} -eq 0 ]]; then
  cat >&2 <<'EOF'

==============================================================
没有探测到任何可用镜像源。
==============================================================

Plan B —— 改用 NapCat 的 Shell 一键安装脚本。
它从国内 CDN 下载，不依赖 Docker Hub：

  cd /opt && curl -o napcat.sh \
    https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh \
    && bash napcat.sh --docker n --cli y

安装后用 `napcat` 进入 TUI CLI 配置（无桌面服务器也支持）。
注意：这条路要装 Xvfb 和 QQ 桌面版运行库，坑比 Docker 多。

另一个办法：在别的能上 Docker Hub 的机器上
  docker pull mlikiowa/napcat-docker:latest
  docker save mlikiowa/napcat-docker:latest | gzip > napcat.tar.gz
再把 tar.gz 传上服务器，`docker load -i napcat.tar.gz`。

--------------------------------------------------------------
EOF
  exit 1
fi

echo
echo "=============================================================="
echo "第 2 步：写入 $DAEMON（可用源 ${#GOOD[@]} 个）"
echo "=============================================================="
mkdir -p /etc/docker
if [[ -f $DAEMON ]]; then
  cp -a "$DAEMON" "$DAEMON.bak.$(date +%Y%m%d%H%M%S)"
  echo "已有配置，先备份到 $DAEMON.bak.*"
fi

# 用 python3 合并写 JSON：手写字符串容易把 daemon.json 里已有的其他配置冲掉
python3 - "$DAEMON" "${GOOD[@]}" <<'PY'
import json, os, sys

path, mirrors = sys.argv[1], sys.argv[2:]
cfg = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fp:
            cfg = json.load(fp)
    except Exception:
        cfg = {}
cfg["registry-mirrors"] = mirrors
with open(path, "w", encoding="utf-8") as fp:
    json.dump(cfg, fp, ensure_ascii=False, indent=2)
print(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

echo
echo "=============================================================="
echo "第 3 步：重启 Docker 让配置生效"
echo "=============================================================="

# registry-mirrors 不支持热重载（systemctl reload docker 改不了它），只能重启。
# 这台机器上可能还跑着别的服务的容器，所以要做三件事：
#   1. 重启前记下正在运行的容器清单
#   2. 重启后逐个核对谁没自己起来
#   3. 漏掉的自动拉起 —— 没配 restart 策略的容器不会自愈
RUNNING_BEFORE=/tmp/docker-running-before-$$.txt
docker ps --format '{{.Names}}' | sort > "$RUNNING_BEFORE"
if [[ -s $RUNNING_BEFORE ]]; then
  echo "重启前有 $(wc -l < "$RUNNING_BEFORE") 个容器在运行（它们会短暂中断）："
  sed 's/^/  - /' "$RUNNING_BEFORE"
else
  echo "重启前没有正在运行的容器（这台机器上没有其他 Docker 服务）。"
fi

systemctl restart docker
sleep 3
if ! systemctl is-active --quiet docker; then
  echo "Docker 没起来！多半是 daemon.json 格式有问题，看：systemctl status docker" >&2
  echo "回滚：cp -a /etc/docker/daemon.json.bak.* /etc/docker/daemon.json && systemctl restart docker" >&2
  exit 1
fi
echo "Docker 已重启：$(docker --version)"

# ---- 核对原有容器有没有掉队 ----
sleep 3
MISSED=()
while read -r name; do
  [[ -z $name ]] && continue
  running=$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo "missing")
  if [[ $running == "true" ]]; then
    echo "  [正常] $name 已自动恢复"
  else
    echo "  [掉队] $name 没有自己起来，尝试拉起 …"
    if docker start "$name" >/dev/null 2>&1 && \
       [[ $(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null) == "true" ]]; then
      MISSED+=("$name（已拉起）")
    else
      MISSED+=("$name（★拉起失败，需要手动查）")
    fi
  fi
done < "$RUNNING_BEFORE"
rm -f "$RUNNING_BEFORE"

if [[ ${#MISSED[@]} -eq 0 ]]; then
  echo "原有容器全部正常 —— 这台机器上的其他服务没受影响。"
else
  echo
  echo "⚠️  这些容器需要你留意："
  for m in "${MISSED[@]}"; do echo "    - $m"; done
fi

echo
echo "=============================================================="
echo "第 4 步：验证（拉一个几十 KB 的镜像试水）"
echo "=============================================================="
if docker pull hello-world >/dev/null 2>&1; then
  echo "  hello-world 拉取成功 —— 镜像源确实通了"
  docker rmi hello-world >/dev/null 2>&1 || true
else
  echo "  hello-world 拉取失败。先别放弃，继续试目标镜像（体积大，可能只是慢）"
fi

echo
echo "=============================================================="
echo "第 5 步：拉取目标镜像（约 1GB，看网速，耐心等）"
echo "=============================================================="
if docker pull "$TARGET_IMAGE"; then
  echo
  echo "镜像已就绪。下一步：cd /opt/napcat && docker compose up -d"
else
  echo
  echo "NapCat 镜像拉取失败。换一批镜像源重跑本脚本，或改用上面的 Plan B。" >&2
  exit 1
fi
