#!/bin/bash
# 板子上一键拉起全部四个常驻程序（第 5 个"标定记录器"按需另开）
#
# 用法：
#   bash start_all.sh            # 全部拉起
#   bash start_all.sh mmwave     # 只拉毫米波
#   bash start_all.sh web mcp    # 只拉网页 + MCP
#
# 说明：docker 服务和 xiaozhi 容器是 unless-stopped，开机自动回来，
#       这里只负责容器里面那几个用 sleep infinity 常驻、需要手动起的程序。

set -u
CONTAINER=xiaozhi
MMWAVE_DIR=/home/testk1/mmwave
PY_MAIN=/opt/venv-mcp-official/bin/python      # 这个才有 pymodbus
PY_EP=/opt/venv-mcp-endpoint/bin/python        # 这个才有 fastapi

WHAT="${*:-mmwave web mcp}"
say() { printf '\033[36m>>> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[32m✓ %s\033[0m\n' "$*"; }
bad() { printf '    \033[31m✗ %s\033[0m\n' "$*"; }

port_up() { ss -lntp 2>/dev/null | grep -q ":$1 "; }

# ---------- docker / 容器 ----------
ensure_container() {
    if ! systemctl is-active --quiet docker; then
        say "启动 docker 服务"
        sudo systemctl start docker || { bad "docker 起不来"; return 1; }
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' $CONTAINER 2>/dev/null)" != "true" ]; then
        say "启动容器 $CONTAINER"
        docker start $CONTAINER >/dev/null || { bad "容器起不来"; return 1; }
    fi
    ok "docker + $CONTAINER 就绪"
}

# ---------- 1. 毫米波 ----------
start_mmwave() {
    say "1/4 毫米波点云程序（界面 + :8091）"
    pkill -9 -f launcher.py 2>/dev/null
    sleep 2
    cd "$MMWAVE_DIR" || { bad "找不到 $MMWAVE_DIR"; return 1; }
    export LC_ALL=C DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000
    nohup env MMWAVE_PROFILE=1 bash run_native.sh lite > logs/run.log 2>&1 &
    # import video_real12 本身要 13 秒，起来大约 50 秒
    for i in $(seq 1 60); do
        port_up 8091 && { ok "毫米波就绪（:8091），约 ${i} 秒"; return 0; }
        sleep 1
    done
    bad "60 秒还没监听 8091，看 $MMWAVE_DIR/logs/run.log"
    return 1
}

# ---------- 2. 网页大屏 ----------
start_web() {
    say "2/4 网页大屏（:8090）"
    port_up 8090 && { ok "已经在跑"; return 0; }
    docker exec -d $CONTAINER bash -lc \
        "cd /opt/demo-real && nohup $PY_MAIN web_real_server.py > /tmp/web.log 2>&1"
    for i in $(seq 1 20); do
        port_up 8090 && { ok "网页就绪 http://127.0.0.1:8090"; return 0; }
        sleep 1
    done
    bad "8090 没起来：docker exec $CONTAINER tail -20 /tmp/web.log"
    return 1
}

# ---------- 3. MCP 接入点 ----------
start_mcp_endpoint() {
    say "3/4 MCP 接入点（:8004）"
    port_up 8004 && { ok "已经在跑"; return 0; }
    docker exec -d $CONTAINER bash -lc \
        "cd /opt/mcp-endpoint-server-main && nohup $PY_EP main.py > /tmp/mcp_ep.log 2>&1"
    for i in $(seq 1 20); do
        port_up 8004 && { ok "接入点就绪 ws://127.0.0.1:8004"; return 0; }
        sleep 1
    done
    bad "8004 没起来：docker exec $CONTAINER tail -20 /tmp/mcp_ep.log"
    return 1
}

# ---------- 4. MCP 管道 ----------
start_mcp_pipe() {
    say "4/4 MCP 管道 + Modbus 工具"
    if docker exec $CONTAINER pgrep -f mcp_pipe.py >/dev/null 2>&1; then
        ok "已经在跑"; return 0
    fi
    docker exec -d $CONTAINER bash -lc \
        "cd /opt/demo-real && set -a && . ./.env && set +a && nohup $PY_MAIN mcp_pipe.py > /tmp/mcp_pipe.log 2>&1"
    sleep 8
    if docker exec $CONTAINER pgrep -f mcp_pipe.py >/dev/null 2>&1; then
        ok "管道已连接"; return 0
    fi
    bad "没连上：docker exec $CONTAINER tail -20 /tmp/mcp_pipe.log"
    return 1
}

# ---------- 跑 ----------
ensure_container || exit 1
FAIL=0
case "$WHAT" in *mmwave*) start_mmwave        || FAIL=1 ;; esac
case "$WHAT" in *web*)    start_web           || FAIL=1 ;; esac
case "$WHAT" in *mcp*)    start_mcp_endpoint  || FAIL=1
                          start_mcp_pipe      || FAIL=1 ;; esac

echo
say "状态汇总"
echo "    端口: $(ss -lntp 2>/dev/null | grep -oE ':(8090|8091|8004)' | sort -u | tr '\n' ' ')"
echo "    毫米波进程: $(pgrep -c -f launcher.py) 个"
echo "    容器进程:   $(docker exec $CONTAINER sh -c 'ps -eo args | grep -cE "web_real_server|main.py|mcp_pipe|modbus_real_mcp"' 2>/dev/null) 个"
[ $FAIL -eq 0 ] && ok "全部就绪" || bad "有程序没起来，看上面的 ✗"
exit $FAIL