# Modbus Real Demo (Web + MCP + Real Device)

`demo-real` 是基于 `modbus-demo2.py` 协议构建的真实设备联调方案，目标是实现三端统一：

- Web 大屏可视化（实时状态 + 控制）
- MCP 工具交互（查询 + 控制）
- 真实 Modbus 设备读写（十合一 + 执行器）

三端通过同一份状态文件进行同步，保证“谁控制、谁可见、谁可追踪”。

## 目录结构

```text
demo-real/
  .env
  web_real_server.py
  modbus_real_shared.py
  modbus_real_runtime.py
  modbus_real_mcp.py
  mcp_pipe.py
  mcp_config.json
  requirements.txt
  README.md
  TECHNICAL_ROUTE.md
  webui/
    index.html
    styles.css
    app.js
    vendor/
      echarts.min.js
```

## 设备协议（按 modbus-demo2.py）

- 十合一传感器：`holding registers 0~9`
- 线圈：`coil 0~3` => `buzzer / green / yellow / red`
- 小灯亮度：`holding register 7`（默认）
- 风扇转速：`holding register 6`（默认）
- 模拟量原始范围：`0~4095`

说明：亮度/风扇寄存器支持环境变量覆盖，适配不同现场接线。

## 安装依赖

在 `demo-real` 目录执行：

```bash
pip install -r requirements.txt
```

## 启动 Web 大屏

```bash
python web_real_server.py
```

打开：

```text
http://127.0.0.1:8090
```

## 启动 MCP（stdio）

```bash
python -m modbus_real_mcp
```

可用工具：

- `modbus_real_get_status`
- `modbus_real_set_coil`
- `modbus_real_set_brightness`
- `modbus_real_set_fan`
- `modbus_real_control`

## 通过 mcp_pipe 接入网关（可选）

1. 设置 endpoint（PowerShell）：

```powershell
$env:MCP_ENDPOINT = 'ws://127.0.0.1:3001/mcp'
```

2. 可选指定配置文件：

```powershell
$env:MCP_CONFIG = 'E:\BaiduSyncdisk\project\python\mcp-dev\mcp-calculator-ai\demo-real\mcp_config.json'
```

3. 在 `demo-real` 目录运行：

```bash
python mcp_pipe.py
```

默认启用：`local-stdio-modbus-real`。

## 环境变量

### 设备连接

- `MODBUS_10IN1_HOST`（默认 `10.60.0.222`）
- `MODBUS_10IN1_PORT`（默认 `502`）
- `MODBUS_AIMO_HOST`（默认 `10.60.0.223`）
- `MODBUS_AIMO_PORT`（默认 `502`）

### 执行器映射

- `MODBUS_AIMO_BRIGHTNESS_REG`（默认 `7`）
- `MODBUS_AIMO_FAN_REG`（默认 `6`）
- `MODBUS_AIMO_ANALOG_RAW_MAX`（默认 `4095`）

### 运行策略

- `MODBUS_REAL_STATE_FILE`（共享状态文件路径）
- `MODBUS_REAL_POLL_INTERVAL_SEC`（默认 `0.8`）
- `MODBUS_REAL_MCP_ENABLE_POLLER`（默认 `0`，建议关闭，避免与 Web 轮询冲突）

## 常见问题

1. 页面显示离线
- 检查设备 IP/端口可达性
- 检查防火墙和网段

2. MCP 控制后页面未更新
- `Ctrl+F5` 强刷页面
- 确认 Web 和 MCP 使用同一个 `MODBUS_REAL_STATE_FILE`

3. mcp_pipe 配置报错
- 确认 `mcp_config.json` 为 UTF-8
- 先直接运行 `python -m modbus_real_mcp` 排除 pipe 问题