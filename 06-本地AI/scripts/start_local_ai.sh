#!/bin/bash
# 启动本地 AI：Ollama + Qwen3-0.6B
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AI_DIR="$(cd "$HERE/.." && pwd)"
MODEL_DIR="$AI_DIR/models"
MODEL_FILE="$MODEL_DIR/qwen3-0.6b-q4_k_m.gguf"
MODEL_NAME="qwen3:0.6b"

bash "$HERE/install_ollama.sh"

if [ ! -f "$MODEL_FILE" ]; then
    echo "[local-ai] 缺少模型文件：$MODEL_FILE" >&2
    echo "[local-ai] 先运行：bash 06-本地AI/scripts/fetch_models.sh" >&2
    exit 1
fi

# 模型目录必须对 ollama 用户可读
chmod -R a+rX "$MODEL_DIR" 2>/dev/null || true
# 确保 ollama 用户能穿过模型文件的所有父目录
P="$MODEL_DIR"
while [ "$P" != "/" ]; do
    chmod a+rx "$P" 2>/dev/null || true
    P="$(dirname "$P")"
done

if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL_NAME"; then
    echo "[local-ai] 模型已存在：$MODEL_NAME"
else
    echo "[local-ai] 导入模型：$MODEL_NAME"
    cd "$MODEL_DIR"
    if ! ollama create "$MODEL_NAME" -f Modelfile.qwen3-0.6b; then
        # 系统服务以 ollama 用户运行，用该用户导入
        sudo -u ollama env HOME=/usr/share/ollama OLLAMA_HOST=127.0.0.1:11434 \
            ollama create "$MODEL_NAME" -f "$MODEL_DIR/Modelfile.qwen3-0.6b"
    fi
fi

# 预热一次，顺便验证 API
echo "[local-ai] 预热模型"
curl -sf http://127.0.0.1:11434/api/chat \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"用一句话说明你现在运行在哪里。\"}],\"stream\":false,\"options\":{\"num_predict\":32,\"temperature\":0.2}}" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("[local-ai] 模型回复：" + d.get("message",{}).get("content","").strip())'

echo "[local-ai] 本地 LLM 就绪：http://127.0.0.1:11434  model=$MODEL_NAME"
ollama ps 2>/dev/null || true
