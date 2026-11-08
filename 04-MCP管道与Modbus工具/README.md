# 04 MCP 管道与 Modbus 工具

把 Modbus 设备封装成 MCP 工具，通过 `mcp_pipe.py` 接到 `03-MCP接入点` 的
WebSocket 注册中心，让大模型可以直接查询和控制现场设备。
跑在 Docker 容器 `xiaozhi` 里。

## 文件

| 文件 | 作用 |
|---|---|
| `mcp_pipe.py` | 主循环：拉起 stdio 子进程（MCP 服务），双向转发到 :8004 |
| `modbus_real_mcp.py` | MCP 工具定义（5 个工具） |
| `modbus_real_runtime.py` | 真实设备读写（pymodbus） |
| `modbus_real_shared.py` | 共享状态文件读写（Web / MCP / 设备三端同步） |
| `mcp_config.json` | 要拉起的 MCP 服务列表 |
| `requirements.txt` | 依赖 |
| `README-demo-real.md` / `TECHNICAL_ROUTE.md` | 原版说明与设计文档 |

## 5 个 MCP 工具

```
modbus_real_get_status       读十合一传感器全部通道
modbus_real_set_coil         控制线圈（蜂鸣器 / 三色灯）
modbus_real_set_brightness   调灯亮度
modbus_real_set_fan          调风扇转速
modbus_real_control          组合控制
```

## 启动

依赖 `.env` 里的 `MCP_ENDPOINT` 和 `MCP_CONFIG`，启动前要先 source 进来：

```bash
docker exec -d xiaozhi bash -lc 'cd /opt/demo-real && set -a && . ./.env && set +a && nohup /opt/venv-mcp-official/bin/python mcp_pipe.py > /tmp/mcp_pipe.log 2>&1'
```

同样要用 `/opt/venv-mcp-official/bin/python`（pymodbus + fastmcp + dotenv 都在这）。

成功日志：

```
MCP_PIPE - INFO - Starting servers: local-stdio-modbus-real
MCP_PIPE - INFO - [local-stdio-modbus-real] Successfully connected to WebSocket server
MCP_PIPE - INFO - [local-stdio-modbus-real] Started server process: ... -m modbus_real_mcp
```

## 配置文件

`.env`（**不提交到仓库**，因为含 MCP token）：

```ini
MCP_ENDPOINT=ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=<token>
MCP_CONFIG=./mcp_config.json

MODBUS_10IN1_HOST=10.60.0.222
MODBUS_10IN1_PORT=502
MODBUS_AIMO_HOST=10.60.0.223
MODBUS_AIMO_PORT=502
```

`mcp_config.json`：

```json
{
  "mcpServers": {
    "local-stdio-modbus-real": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "modbus_real_mcp"]
    }
  }
}
```

## 设备协议

- 十合一传感器：holding registers `0~9`
- 线圈：coil `0~3` → 蜂鸣器 / 绿灯 / 黄灯 / 红灯
- 小灯亮度：holding register `7`（可用 `MODBUS_AIMO_BRIGHTNESS_REG` 覆盖）
- 风扇转速：holding register `6`（可用 `MODBUS_AIMO_FAN_REG` 覆盖）
- 模拟量原始范围 `0~4095`

## 注意

- `MODBUS_REAL_MCP_ENABLE_POLLER` 建议保持默认 `0`，避免和 Web 大屏的轮询抢设备。
- 三端通过共享状态文件同步，所以"谁在控制"在网页上能看到。