# K1 毫米波人体检测 + 网页大屏 + MCP 控制

一套跑在进迭时空 **K1（RISC-V, Bianbu Linux）** 板子上的工业物联网 Demo：
毫米波雷达点云检测人体，检测结果推到 Docker 里的网页大屏，同时把 Modbus 设备通过 MCP 暴露给大模型调用。

## 整体数据流

```
                     ┌──────────────────────────────────────────┐
  毫米波雷达          │  01 毫米波点云程序（原生运行，非 Docker）  │
  /dev/ttyACM0  ───▶ │  video_real_k1_lite.py                   │
  2000000 baud        │    ├─ PyQt5 点云界面（3 视图 + 摄像头）    │
                     │    └─ 只读状态接口 :8091/status            │
  USB 摄像头          │                                          │
  /dev/videoN   ───▶ └───────────────┬──────────────────────────┘
                                     │ HTTP/JSON（宿主机 → 容器，host 网络）
                     ┌───────────────▼──────────────────────────┐
                     │  02 网页大屏 web_real_server.py  :8090    │
                     │    DataV 风格大屏 + radar.js 三块状态      │
                     │    程序运行中 / 有人无人 / 正常跌倒        │
                     └───────────────┬──────────────────────────┘
                                     │ 共享状态文件
                     ┌───────────────▼──────────────────────────┐
                     │  04 MCP 管道 mcp_pipe.py                  │
                     │    ├─ modbus_real_mcp（5 个工具）          │
                     │    └─ 连接 :8004 注册中心                  │
                     └───────────────┬──────────────────────────┘
                     ┌───────────────▼──────────────────────────┐
                     │  03 MCP 接入点 main.py  :8004             │
                     │    给小智语音端提供 websocket 注册中心      │
                     └──────────────────────────────────────────┘
```

语音链路：麦克风 → SileroVAD（本地）→ FunASR / SenseVoiceSmall（本地）
          → Qwen3-0.6B（本地 Ollama）→ TTS → 喇叭

## 六个部分一览

| # | 目录 | 程序 | 端口 | 运行位置 |
|---|---|---|---|---|
| 0 | `06-本地AI/` | Ollama + Qwen3-0.6B / FunASR | 11434 | 板子宿主机（原生） |
| 1 | `01-毫米波点云程序/` | `video_real_k1_lite.py` + `launcher.py` | 8091 | 板子宿主机（原生） |
| 2 | `02-网页大屏/` | `web_real_server.py` | 8090 | Docker 容器 `xiaozhi` |
| 3 | `03-MCP接入点/` | `main.py`（mcp-endpoint-server） | 8004 | Docker 容器 `xiaozhi` |
| 4 | `04-MCP管道与Modbus工具/` | `mcp_pipe.py` + `modbus_real_mcp` | — | Docker 容器 `xiaozhi` |
| 5 | `05-标定与调试脚本/` | 各种探针/标定脚本 | — | 板子或 PC |

## 快速启动

板子上执行（详细命令见各子目录 README 和根目录 `start_all.sh`）：

```bash
# 0. 本地 AI（Ollama + Qwen3-0.6B + FunASR），首次部署先跑一次
cd <代码包根目录>
bash 06-本地AI/scripts/start_local_ai.sh
bash 06-本地AI/scripts/apply_xiaozhi_local.sh xiaozhi

# 1. 毫米波程序（界面 + :8091），启动约需 50 秒
export LC_ALL=C DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000
cd /home/testk1/mmwave
nohup env MMWAVE_PROFILE=1 bash run_native.sh lite > logs/run.log 2>&1 &

# 2. 网页大屏 :8090 —— 必须用容器里的 venv，否则报 No module named 'pymodbus'
docker exec -d xiaozhi bash -lc 'cd /opt/demo-real && nohup /opt/venv-mcp-official/bin/python web_real_server.py > /tmp/web.log 2>&1'

# 3. MCP 接入点 :8004
docker exec -d xiaozhi bash -lc 'cd /opt/mcp-endpoint-server-main && nohup /opt/venv-mcp-endpoint/bin/python main.py > /tmp/mcp_ep.log 2>&1'

# 4. MCP 管道（modbus 工具注册）
docker exec -d xiaozhi bash -lc 'cd /opt/demo-real && set -a && . ./.env && set +a && nohup /opt/venv-mcp-official/bin/python mcp_pipe.py > /tmp/mcp_pipe.log 2>&1'
```

打开 `http://<板子IP>:8090` 看网页大屏。

## 本地 AI 模型

语音链路不再依赖外部 API：

- **LLM**：`qwen3:0.6b`（Qwen3-0.6B Q4_K_M，约 378 MB），由板端 Ollama 在 `127.0.0.1:11434` 提供。
- **ASR**：`FunASR` + `SenseVoiceSmall`，在 `xiaozhi` 容器内本地推理；VAD 为 `SileroVAD`。
- **配置**：`06-本地AI/config/xiaozhi.local.config.yaml` 覆盖到容器的 `data/.config.yaml`，
  `selected_module.LLM` 指向 `OllamaLLM`，`ASR` 指向 `FunASR`。

```bash
# 一键准备（模型已随包时可跳过 fetch）
bash 06-本地AI/scripts/fetch_models.sh
bash 06-本地AI/scripts/start_local_ai.sh
bash 06-本地AI/scripts/apply_xiaozhi_local.sh xiaozhi
bash 06-本地AI/scripts/verify_local_ai.sh xiaozhi
```

模型文件 SHA256：`AC2D97712095A558E31573F62F466A3F9D93990898B0EC79D7C974C1780D524A`。
板端实测短问答 **1.4–2.0 s**、生成约 **7–9 tok/s**（详见 `06-本地AI/README.md`）。

## 关键实测数据

| 项目 | 数值 |
|---|---|
| 点云帧率 | **10.0 fps**（原程序 0.5 fps，快 20 倍） |
| 摄像头显示 / 源头 | 21~25 fps / 29.5 fps |
| 数据延迟（帧到屏幕） | **28~90 ms** |
| 单帧合成耗时 | 15~25 ms |
| 雷达包率 | 10 包/秒（有点 10 包/秒） |
| 板子温度 | 38~62 °C（空载~满载），无降频 |

原程序 `video_real12.py` **一个字节都没改**（MD5 `710842FB001D4B9A80B4564A6CC7BAE0`），
所有加速都在 `video_real_k1_lite.py` 里重写图形层实现，雷达协议解析、聚类、判定逻辑原样保留。

## 人体判定：为什么要调阈值

原程序的判据（`video_real12.py:1238-1239`）非常松：

```python
target_detected = len(X) > 10                                    # 超过 10 个点就算有人
fall_detected   = np.max(Z) < 0.6 and (np.max(Z)-np.min(Z)) < 0.4  # 最高点<0.6m 且高差<0.4
```

实测**空场**（雷达前无人）时，雷达正前方 0.17~0.50 m 处恒定有 15~29 个近场杂波点
（桌面/支架/旁边的摄像头盒），于是原程序恒定误报"有人 + 跌倒"。

极速版加了三个可调参数，`run_native.sh` 里已经按实测值设好：

| 环境变量 | 默认（本仓库） | 作用 |
|---|---|---|
| `MMWAVE_MIN_RANGE` | 0.6 | 扔掉 0.6 m 以内的近场杂波 |
| `MMWAVE_PERSON_MIN` | 30 | 0.6 m 以外还要有 30 个点才算有人 |
| `MMWAVE_PERSON_HOLD` | 5 | 连续 5 帧（约 0.5 s）满足才翻转状态 |

调完后空场复测：点数 0 / 判有人 0 次 / 判跌倒 0 次 / 状态恒为 `none`。

`MMWAVE_PERSON_MIN` / `MMWAVE_HOLD` 想按现场改，直接覆盖环境变量即可；
`MMWAVE_MIN_RANGE=0` 就退回原程序行为（全部点都算）。

## 状态接口

毫米波程序在 `:8091` 暴露只读 JSON（跨域已开）：

```bash
curl http://127.0.0.1:8091/status
```

```json
{
  "online": true, "person_detected": false, "fall_detected": false,
  "state": "none", "state_text": "无目标",
  "person_count": 0, "fall_count": 0,
  "points": 0, "clusters": 0,
  "bbox": [0,0,0,0,0,0],
  "raw_points": 26, "raw_bbox": [-0.06,0.23,0.26,0.50,-0.61,0.37],
  "fps": 10.0, "delay_ms": 43.0,
  "radar_pkt_rate": 10.0, "radar_hit_rate": 10.0,
  "ts": 1793848742173, "stale": false, "ok": true
}
```

- `points` / `bbox`：**过滤后**（过了 `MMWAVE_MIN_RANGE` 门限）的点数和包围盒
- `raw_points` / `raw_bbox`：**过滤前**的原始点数和包围盒 —— 现场调阈值全靠这两个字段
- `stale`：超过 3 秒没更新就为 `true`，网页据此显示"无响应"

## 网页大屏的三块状态

| 状态块 | 取值 | 判据 |
|---|---|---|
| **程序** | 运行中 / 无响应 / 不在线 | 接口能取到且 3 秒内有更新 = 运行中 |
| **人员** | 有人 / 无人 / 无数据 | 只看 `person_detected`，**不显示人数** |
| **状态** | 正常 / 跌倒 / -- | `fall_detected`；没人时显示 `--` |

## 踩过的坑（都已在代码里处理）

1. **摄像头设备名会变**：USB 重插后 `/dev/video20` 会变成 `/dev/video50` 之类。
   `launcher.py` 只接受 `/dev/` 开头的真实节点，探测不到就按 `/dev/video0,1,2…` 挨个试读一帧。
   绝不能兜底成 `0` —— K1 上 `VideoCapture(0)` 会报 GStreamer `no element 0`，把整条采集链路搞死。
2. **摄像头挂了不能连累点云**：采集主循环原本要同时拿到"摄像头同步帧 + 雷达同步帧"才往界面送数据，
   摄像头不出帧就永远 `continue`，雷达数据全闷在队列里（界面 0 fps）。
   现在 3 秒没摄像头帧就自动切"只送雷达"模式。
3. **网页容器里要用对 venv**：`web_real_server.py` 依赖 pymodbus，只有
   `/opt/venv-mcp-official/bin/python` 装了；用容器默认 `python` 会 `ModuleNotFoundError`。
4. **零点点帧会被整帧丢弃**：雷达一直 10 帧/秒，但没人时帧里 0 个点，头体变成 32 个 hex
   不满 40 个，解析函数直接返回 None —— 所以"没有点云"有两种完全不同的原因，
   接口里的 `radar_pkt_rate` 和 `radar_hit_rate` 就是用来区分的。
5. **原程序有两套跌倒判据**：状态行用"全局最高点 < 0.6"，而"实时跌倒人数"用"簇内最高点 < -0.4"。
   目标高度落在两者之间时会出现"人数 1、跌倒人数 0、状态却疑似跌倒"。
   `MMWAVE_FALL_BY_CLUSTER=1` 可以让状态行也按簇判断。

## 雷达串口协议要点

- 波特率 **2000000**，设备 `/dev/ttyACM0`
- 帧头 `DCFFEEFF`，帧头编号 `D3FFEEFF`
- 点云块 `CBFEDDFF`（旧）/ `C4FEDDFF`（新）
- 每个点 4 字节：x/y/z 各 1 字节（有符号，单位 0.25 m），加 1 字节速度/SNR
- 帧头主体必须正好 **40 个 hex 字符**（20 字节），否则整帧丢弃

## 硬件清单

- 进迭时空 K1（Bianbu Linux, RISC-V, 8 核）
- 毫米波雷达（2 Mbaud 串口输出点云）
- USB 摄像头（UVC，640×480）
- Modbus 十合一传感器 + 执行器（`10.60.0.222` / `10.60.0.223`）

## 目录说明

```
01-毫米波点云程序/    毫米波点云程序（极速版 + 原版对照 + 启动器）
02-网页大屏/          web_real_server.py + webui（DataV 大屏）
03-MCP接入点/         MCP websocket 注册中心
04-MCP管道与Modbus工具/ mcp_pipe + modbus 工具封装
05-标定与调试脚本/    串口探针、点云监视、帧率测试、现场标定记录器
06-本地AI/            Ollama + Qwen3-0.6B + FunASR 本地语音链路
```

## 许可

内部工程代码，仅用于项目交付与现场调试。
---

## 关于"本地模型"的现状（重要）

- **LLM 已在本地跑通**：Qwen3-0.6B Q4_K_M 由板端 Ollama 提供（`127.0.0.1:11434`），不再依赖外部 API。
- **ASR 目前仍走云端**：镜像里没有装了 `funasr`/`torch`，而 `core/providers/asr/fun_local.py` 第一行就是
  `from funasr import AutoModel`，直接切 FunASR 会让 ASR 启动崩溃。`apply_xiaozhi_local.sh` 会现场检测，
  缺依赖时**自动只切 LLM、保留原有 ASR**，不会把语音链路搞挂。要在板端跑 ASR，先装 funasr/torch 并下载
  SenseVoiceSmall（见 `06-本地AI/scripts/fetch_asr_model.sh`）。
- **模型权重不进 Git**：`qwen3-0.6b-q4_k_m.gguf`（378 MB）和 `spacemit-ollama-toolkit_0.0.8_riscv64.deb`（25 MB）
  已被 `.gitignore` 排除（GitHub 单文件上限 100 MB），随交付压缩包分发。
- **配置是深合并，不是覆盖**：`data/.config.yaml` 是覆盖层，里面还有 `mcp_endpoint`（MCP 工具地址）。
  脚本用 `merge_xiaozhi_config.py` 深合并，保住 `mcp_endpoint` 和 `server.websocket`；配置没变化时不重启容器。