#!/bin/bash
# 本地 AI 链路自检
#
# 检查的是"真正生效的配置"，不是"文件存在"：
#   通过 xiaozhi 自己的 load_config() 读合并后的结果，
#   避免"文件写进去了但内容没生效"这种假绿。
set -uo pipefail

CONTAINER="${1:-xiaozhi}"
FAIL=0
ok()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; }
bad()  { printf '  \033[31m✗ %s\033[0m\n' "$*"; FAIL=1; }
info() { printf '  \033[36m· %s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }

echo "== Ollama =="
if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    ok "服务在线：$(curl -s http://127.0.0.1:11434/api/version)"
else
    bad "127.0.0.1:11434 不通（跑 scripts/install_ollama.sh）"
fi

if ollama list 2>/dev/null | awk '{print $1}' | grep -qx 'qwen3:0.6b'; then
    ok "模型已导入：qwen3:0.6b"
else
    bad "模型 qwen3:0.6b 不存在（跑 scripts/start_local_ai.sh）"
fi

echo "== 推理 =="
RESP="$(curl -s --max-time 90 http://127.0.0.1:11434/api/chat -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:0.6b","messages":[{"role":"user","content":"现场温度 62 度，正常吗？只答一句。"}],"stream":false,"options":{"num_predict":48,"temperature":0.2}}')"
if echo "$RESP" | grep -q '"message"'; then
    echo "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  \033[32m✓ 回复：" + d["message"]["content"].strip() + "\033[0m")'
else
    bad "推理请求失败：$RESP"
fi

echo "== xiaozhi 生效配置 =="
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    warn "容器 $CONTAINER 不存在，跳过"
    echo; [ $FAIL -eq 0 ] && echo "（前面各项通过）"; exit $FAIL
fi

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

if [ -z "${SERVER_DIR:-}" ]; then
    bad "没找到 xiaozhi 服务目录"
else
    info "服务目录：$SERVER_DIR"

    # 用服务自己的加载器读"合并后真正生效"的配置
    EFFECTIVE="$(docker exec "$CONTAINER" sh -lc "cd '$SERVER_DIR' && python3 - <<'PY' 2>/dev/null
from config.config_loader import load_config
c = load_config()
sm = c.get('selected_module') or {}
print('LLM=' + str(sm.get('LLM')))
print('ASR=' + str(sm.get('ASR')))
print('URL=' + str(((c.get('LLM') or {}).get('OllamaLLM') or {}).get('base_url')))
print('MODEL=' + str(((c.get('LLM') or {}).get('OllamaLLM') or {}).get('model_name')))
print('MCP=' + ('set' if c.get('mcp_endpoint') else 'MISSING'))
PY" | tr -d '\r')"

    LLM_MOD="$(echo "$EFFECTIVE" | sed -n 's/^LLM=//p')"
    ASR_MOD="$(echo "$EFFECTIVE" | sed -n 's/^ASR=//p')"
    OLLAMA_URL="$(echo "$EFFECTIVE" | sed -n 's/^URL=//p')"
    OLLAMA_MODEL="$(echo "$EFFECTIVE" | sed -n 's/^MODEL=//p')"
    MCP_STATE="$(echo "$EFFECTIVE" | sed -n 's/^MCP=//p')"

    if [ "$LLM_MOD" = "OllamaLLM" ]; then
        ok "LLM = OllamaLLM（本地）→ $OLLAMA_URL  model=$OLLAMA_MODEL"
    else
        bad "LLM = ${LLM_MOD:-空}，不是 OllamaLLM —— 本地模型没接上"
    fi

    if [ "$ASR_MOD" = "FunASR" ]; then
        if docker exec "$CONTAINER" test -f "$SERVER_DIR/models/SenseVoiceSmall/model.pt" 2>/dev/null; then
            ok "ASR = FunASR（本地）"
        else
            bad "ASR = FunASR 但 models/SenseVoiceSmall/model.pt 不存在 —— 启动会崩"
        fi
    else
        info "ASR = ${ASR_MOD:-空}（非本地；要在本地跑请先装 funasr/torch 并下模型）"
    fi

    # 关键：覆盖配置不能把 MCP 工具地址冲掉
    if [ "$MCP_STATE" = "set" ]; then
        ok "mcp_endpoint 仍在（MCP 工具可用）"
    else
        bad "mcp_endpoint 丢失 —— MCP/Modbus 工具会全部失效"
    fi
fi

echo "== 语音服务 =="
if ss -lntp 2>/dev/null | grep -q ':8000 ' || docker exec "$CONTAINER" sh -c 'ss -lntp 2>/dev/null || netstat -lnt 2>/dev/null' | grep -q ':8000 '; then
    ok "语音服务在监听 :8000"
else
    bad "语音服务未运行（8000 没监听）→ 跑 scripts/start_voice.sh"
fi

echo
if [ $FAIL -eq 0 ]; then
    printf '\033[32m全部通过：本地 LLM 已接入，语音链路可跑\033[0m\n'
else
    printf '\033[31m有项目未通过，见上面 ✗\033[0m\n'
fi
exit $FAIL