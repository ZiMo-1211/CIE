#!/bin/bash
# 拉取本地模型文件（首次部署或换机器时执行）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AI_DIR="$(cd "$HERE/.." && pwd)"
MODEL_DIR="$AI_DIR/models"
mkdir -p "$MODEL_DIR"

QWEN_URL="https://hf-mirror.com/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_K_M.gguf"
QWEN_OUT="$MODEL_DIR/qwen3-0.6b-q4_k_m.gguf"

if [ -f "$QWEN_OUT" ] && [ "$(stat -c%s "$QWEN_OUT")" -gt 300000000 ]; then
    echo "[local-ai] Qwen3-0.6B 已存在：$QWEN_OUT"
else
    echo "[local-ai] 下载 Qwen3-0.6B Q4_K_M（约 378MB）"
    curl -L --retry 5 --retry-delay 2 -C - -o "$QWEN_OUT" "$QWEN_URL"
fi

echo "[local-ai] 模型文件："
ls -lh "$QWEN_OUT"
sha256sum "$QWEN_OUT" || true
