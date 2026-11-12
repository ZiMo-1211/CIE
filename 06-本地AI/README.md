# 06 本地 AI（Qwen3-0.6B + FunASR）

给 K1 板上的语音链路补上**完全本地**的 LLM 与 ASR：

```
麦克风 → VAD(Silero，本地) → ASR(FunASR SenseVoiceSmall，本地)
      → LLM(Qwen3-0.6B，Ollama，本地) → TTS → 喇叭
                              ↑
                    MCP 工具（Modbus / 大屏状态）
```

- LLM：`qwen3:0.6b`，Q4_K_M GGUF，约 378 MB，跑在板端 Ollama `127.0.0.1:11434`。
- ASR：`FunASR` + `SenseVoiceSmall`，跑在 `xiaozhi` 容器内，模型目录 `models/SenseVoiceSmall`。
- VAD：`SileroVAD`，本地。
- 配置：`config/xiaozhi.local.config.yaml` 覆盖 `xiaozhi-esp32-server` 的 `data/.config.yaml`，把 `selected_module.LLM` 切到 `OllamaLLM`、`ASR` 切到 `FunASR`。

## 目录

```
06-本地AI/
├─ models/
│  ├─ qwen3-0.6b-q4_k_m.gguf      # Qwen3-0.6B Q4_K_M（SHA256 见下）
│  └─ Modelfile.qwen3-0.6b        # ChatML 模板 + 工业助手 system prompt
├─ config/
│  └─ xiaozhi.local.config.yaml   # xiaozhi 覆盖配置
├─ vendor/
│  └─ spacemit-ollama-toolkit_0.0.8_riscv64.deb
└─ scripts/
   ├─ install_ollama.sh           # 装 Ollama（SpacemiT 版）
   ├─ fetch_models.sh             # 下载 Qwen3-0.6B
   ├─ start_local_ai.sh           # 起 Ollama + 导入/预热模型
   ├─ apply_xiaozhi_local.sh      # 覆盖 xiaozhi 配置并重启容器
   ├─ fetch_asr_model.sh          # 准备 FunASR SenseVoiceSmall
   └─ verify_local_ai.sh          # 全链路自检
```

## 首次部署

```bash
cd 06-本地AI

# 1) 模型文件（随包已带可跳过；换机器时执行）
bash scripts/fetch_models.sh

# 2) 安装并启动 Ollama，导入 qwen3:0.6b
bash scripts/start_local_ai.sh

# 3) ASR 模型（镜像里已有可跳过）
bash scripts/fetch_asr_model.sh

# 4) 把本地配置写进 xiaozhi 容器并重启
bash scripts/apply_xiaozhi_local.sh xiaozhi

# 5) 自检
bash scripts/verify_local_ai.sh xiaozhi
```

在仓库根目录执行 `bash start_all.sh` 时，会先跑第 2、4 步，再拉毫米波 / 网页 / MCP。

## 模型文件

| 文件 | 大小 | SHA256 |
|---|---:|---|
| `qwen3-0.6b-q4_k_m.gguf` | 396,705,472 B（378 MiB） | `AC2D97712095A558E31573F62F466A3F9D93990898B0EC79D7C974C1780D524A` |

模型来源：Qwen3-0.6B 官方权重的 Q4_K_M GGUF 量化；用 Ollama Modelfile 导入为 `qwen3:0.6b`。

## 为什么要自定义 ChatML 模板

该 GGUF 没有携带 `tokenizer.chat_template`，Ollama 默认会退化成 `{{ .Prompt }}`（纯文本续写），中文对话会跑偏。`Modelfile.qwen3-0.6b` 里补了 Qwen3 的 ChatML 模板，并在 assistant 起始处预置空 `<think></think>` 块，让 0.6B 直接出正文：

```
<|im_start|>system
...<|im_end|>
<|im_start|>user
...<|im_end|>
<|im_start|>assistant
<think>

</think>

```

实测回复不再带 `<think>` 标签，直接是可播报的中文短句。

## 板端实测（K1，8 核，Qwen3-0.6B Q4_K_M）

| 输入 | 回复 | 端到端 | 生成 |
|---|---|---|---:|
| 现场温度 62 度，正常吗？只答一句。 | 现场温度62度，正常。 | 1.85 s | 9 tok / 1.14 s |
| 用一句话说明毫米波雷达检测到有人。 | 毫米波雷达检测到有人。 | 2.02 s | 8 tok / 0.99 s |
| 风扇开到 60%，只回复确认。 | 确认。 | 1.37 s | 3 tok / 0.29 s |

短问答 1.4–2.0 s，生成约 7–9 tok/s；适合“是/否、状态、控制确认”这类现场短交互。

## xiaozhi 配置覆盖

`apply_xiaozhi_local.sh` 会自动找容器里的服务目录（常见路径：
`/opt/xiaozhi-esp32-server/main/xiaozhi-server`、`/opt/xiaozhi-server`、`/app`），
把 `config/xiaozhi.local.config.yaml` 写到 `<server>/data/.config.yaml`，原文件备份为 `.bak`，然后重启容器。

覆盖的关键项：

```yaml
selected_module:
  VAD: SileroVAD
  ASR: FunASR
  LLM: OllamaLLM

ASR:
  FunASR:
    type: fun_local
    model_dir: models/SenseVoiceSmall

LLM:
  OllamaLLM:
    type: ollama
    model_name: qwen3:0.6b
    base_url: http://127.0.0.1:11434
```

容器使用 host 网络，所以 `127.0.0.1:11434` 就是板子宿主机的 Ollama。

## 自检输出

```text
== Ollama ==
  ✓ 服务在线：{"version":"0.6.8+spacemit"}
  ✓ 模型已导入：qwen3:0.6b
== 推理 ==
  ✓ 回复：现场温度 62 度，正常。
== xiaozhi 配置 ==
  ✓ 本地配置已写入：.../data/.config.yaml
  ✓ ASR 模型目录存在：.../models/SenseVoiceSmall
```

## 排障

| 现象 | 处理 |
|---|---|
| `ollama: command not found` | 跑 `scripts/install_ollama.sh`，或手动 `sudo dpkg -i vendor/*.deb` |
| `model qwen3:0.6b not found` | 跑 `scripts/start_local_ai.sh`，确认 `ollama list` 里有该模型 |
| 回复带 `<think>` | 确认用的是本目录的 `Modelfile.qwen3-0.6b`（含预置空 think 块），重新 `ollama create` |
| xiaozhi 仍走原 LLM | 看容器里 `data/.config.yaml` 是否写入，重启容器；若用智控台，需在智控台里同步选择 Ollama |
| 找不到 xiaozhi 服务目录 | 用 `XIAOZHI_SERVER_DIR=/实际路径 bash scripts/apply_xiaozhi_local.sh xiaozhi` 指定 |
| ASR 报模型不存在 | 跑 `scripts/fetch_asr_model.sh`，或把 SenseVoiceSmall 目录 `docker cp` 到 `<server>/models/` |
