#!/bin/bash
# 把本地模型配置深合并进 xiaozhi 容器，并按需重启
#
# 相比"直接覆盖 data/.config.yaml"的三个改进：
#   1. 路径探测补上了本镜像的真实布局 /opt/xiaozhi-esp32-server（原来 5 个候选全都对不上）
#   2. 深合并而不是覆盖，保住 server.websocket / mcp_endpoint（否则 MCP 工具全断）
#   3. 配置没变化就不重启容器（否则会把容器里的 8090/8004/mcp_pipe 一起干掉）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AI_DIR="$(cd "$HERE/.." && pwd)"
CONTAINER="${1:-xiaozhi}"
PATCH="$AI_DIR/config/xiaozhi.local.config.yaml"
MERGER="$HERE/merge_xiaozhi_config.py"

say()  { printf '\033[36m[local-ai]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[local-ai]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[local-ai]\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || { warn "未安装 docker，跳过 xiaozhi 配置合并"; exit 0; }
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" = "true" ] \
    || { warn "容器 $CONTAINER 未运行，跳过配置合并"; exit 0; }

# ---------- 1. 定位 xiaozhi 服务目录 ----------
SERVER_DIR="${XIAOZHI_SERVER_DIR:-}"
if [ -z "$SERVER_DIR" ]; then
    SERVER_DIR="$(docker exec "$CONTAINER" sh -lc '
for d in \
  /opt/xiaozhi-esp32-server \
  /opt/xiaozhi-esp32-server/main/xiaozhi-server \
  /opt/xiaozhi-esp32-server/xiaozhi-server \
  /opt/xiaozhi-server \
  /app; do
  if [ -f "$d/config.yaml" ]; then echo "$d"; exit 0; fi
done
find /opt /app -maxdepth 4 -name config.yaml -not -path "*/data/*" 2>/dev/null | head -1
' 2>/dev/null | tr -d '\r')"
fi

if [ -z "${SERVER_DIR:-}" ]; then
    die "没找到 xiaozhi 的 config.yaml；可指定目录重跑：XIAOZHI_SERVER_DIR=/实际路径 bash $0 $CONTAINER"
fi
say "服务目录：$SERVER_DIR"

CFG="$SERVER_DIR/data/.config.yaml"
docker exec "$CONTAINER" test -f "$CFG" \
    || die "$CFG 不存在，镜像可能没初始化过配置"

# ---------- 2. 判断本地 ASR 是否可用 ----------
DROP_ASR=""
if docker exec "$CONTAINER" sh -lc "cd '$SERVER_DIR' && python3 -c 'import funasr'" >/dev/null 2>&1 \
   && docker exec "$CONTAINER" test -f "$SERVER_DIR/models/SenseVoiceSmall/model.pt" 2>/dev/null; then
    say "本地 ASR 可用（funasr + SenseVoiceSmall）→ 一并切到 FunASR"
else
    DROP_ASR="--drop-asr"
    warn "本地 ASR 不可用（容器缺 funasr/torch 或没下 SenseVoiceSmall）"
    warn "→ 自动跳过 ASR，只把 LLM 切到本地 Ollama，原有云端 ASR 保持不动"
fi

# ---------- 3. 深合并配置 ----------
WORK=/tmp/mmwave-local-ai
docker exec "$CONTAINER" mkdir -p "$WORK"
docker cp "$PATCH"  "$CONTAINER:$WORK/patch.yaml"
docker cp "$MERGER" "$CONTAINER:$WORK/merge.py"

RESULT="$(docker exec "$CONTAINER" sh -lc \
    "python3 $WORK/merge.py '$CFG' $WORK/patch.yaml $DROP_ASR" 2>&1 | tail -1)"

case "$RESULT" in
    CHANGED)
        say "配置已更新（原文件备份为 $(basename "$CFG").bak）"
        ;;
    UNCHANGED)
        say "配置无变化，跳过重启（网页 8090 / MCP 8004 不受影响）"
        exit 0
        ;;
    *)
        printf '%s\n' "$RESULT" >&2
        die "配置合并失败，上面是容器内的报错"
        ;;
esac

# ---------- 4. 重启容器（仅配置真的变了才走到这里）----------
say "重启容器使新配置生效"
docker restart "$CONTAINER" >/dev/null
for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" = "true" ] && break
    sleep 1
done

warn "容器已重启 —— 里面的 web_real_server(8090)、MCP 接入点(8004)、mcp_pipe 已被清掉"
warn "跑一次  bash start_all.sh web mcp  就能把它们拉回来（或直接 bash start_all.sh）"
say "完成：LLM=OllamaLLM(local)  $( [ -n "$DROP_ASR" ] && echo 'ASR=保持原样' || echo 'ASR=FunASR(local)' )"