#!/bin/bash
# 安装并启动 Ollama（SpacemiT K1 版）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AI_DIR="$(cd "$HERE/.." && pwd)"
DEB="$AI_DIR/vendor/spacemit-ollama-toolkit_0.0.8_riscv64.deb"

if command -v ollama >/dev/null 2>&1; then
    echo "[local-ai] ollama 已安装：$(ollama --version 2>&1 | head -1)"
else
    echo "[local-ai] 安装 spacemit-ollama-toolkit"
    if [ -f "$DEB" ]; then
        sudo dpkg -i "$DEB"
    else
        sudo apt-get update -y
        sudo apt-get install -y spacemit-ollama-toolkit
    fi
fi

# 让 ollama 用户能读到本目录里的 GGUF
chmod -R a+rX "$AI_DIR/models" 2>/dev/null || true

if systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
    sudo systemctl enable ollama >/dev/null 2>&1 || true
    sudo systemctl start ollama
else
    # 没有 systemd 单元时，用当前用户起一个前台服务
    if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        mkdir -p "$HOME/.ollama"
        nohup ollama serve >"$AI_DIR/ollama.log" 2>&1 &
    fi
fi

for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        echo "[local-ai] ollama 就绪：$(curl -s http://127.0.0.1:11434/api/version)"
        exit 0
    fi
    sleep 1
done
echo "[local-ai] ollama 启动超时，检查 systemctl status ollama" >&2
exit 1
