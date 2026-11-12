#!/bin/bash
# 准备本地 ASR 模型：FunASR SenseVoiceSmall
#
# 现实情况（已在板子的 xiaozhi 镜像里核实）：
#   fun_local.py 依赖 `from funasr import AutoModel`，而镜像里
#   funasr / torch / modelscope 全都没装，models/ 下也只有 silero-vad。
#   所以"本地 ASR"不是拷个模型文件就行，得先装 funasr + torch（riscv64 上体积大、耗时长）。
#
# 本脚本做三件事：
#   1. 明确报告缺什么
#   2. 装了依赖之后，用 modelscope 把 SenseVoiceSmall 下到 models/SenseVoiceSmall
#   3. 下完打印校验信息
#
# 依赖不齐时，apply_xiaozhi_local.sh 会自动跳过 ASR、只切 LLM，不会把语音链路搞崩。
set -uo pipefail

CONTAINER="${1:-xiaozhi}"
SERVER_DIR="${2:-}"

warn() { printf '\033[33m[local-ai]\033[0m %s\n' "$*"; }
say()  { printf '\033[36m[local-ai]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[local-ai]\033[0m %s\n' "$*" >&2; exit 1; }

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

[ -n "${SERVER_DIR:-}" ] || die "没找到 xiaozhi 服务目录（可用第 2 个参数指定）"
say "服务目录：$SERVER_DIR"

# ---------- 1. 依赖检查 ----------
say "检查 ASR 依赖（funasr / modelscope）"
MISSING=""
for m in funasr modelscope; do
    if docker exec "$CONTAINER" python3 -c "import $m" >/dev/null 2>&1; then
        say "  $m ✓"
    else
        say "  $m ✗"
        MISSING="$MISSING $m"
    fi
done

if [ -n "$MISSING" ]; then
    warn "缺少依赖：$MISSING"
    warn "在 riscv64 上装 funasr 会拖 torch（约 1.5~2 GB），耗时长且不一定有可用轮子，"
    warn "所以本脚本不会自动安装。确认要装时在容器里执行："
    echo
    echo "  docker exec -it $CONTAINER bash -lc 'pip3 install -U modelscope funasr'"
    echo
    warn "装好后再跑一次本脚本；在那之前 apply_xiaozhi_local.sh 会保持云端 ASR 不变。"
    exit 1
fi

# ---------- 2. 下载模型 ----------
if docker exec "$CONTAINER" test -f "$SERVER_DIR/models/SenseVoiceSmall/model.pt" 2>/dev/null; then
    say "SenseVoiceSmall 已存在，跳过下载"
else
    say "下载 SenseVoiceSmall 到 $SERVER_DIR/models/SenseVoiceSmall"
    docker exec "$CONTAINER" bash -lc "
set -e
cd '$SERVER_DIR'
mkdir -p models
python3 - <<'PY'
from modelscope import snapshot_download
p = snapshot_download('iic/SenseVoiceSmall', local_dir='models/SenseVoiceSmall')
print('[local-ai] 下载完成:', p)
PY
" || die "下载失败（网络？modelscope 版本？）"
fi

# ---------- 3. 校验 ----------
say "模型目录内容"
docker exec "$CONTAINER" ls -lh "$SERVER_DIR/models/SenseVoiceSmall" 2>/dev/null | head -12

if docker exec "$CONTAINER" test -f "$SERVER_DIR/models/SenseVoiceSmall/model.pt" 2>/dev/null; then
    say "完成。现在可以跑：bash scripts/apply_xiaozhi_local.sh $CONTAINER"
else
    die "没找到 model.pt，下载可能不完整"
fi