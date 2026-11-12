#!/usr/bin/env python3
"""把本地模型配置"深合并"进 xiaozhi 的 data/.config.yaml。

为什么不能直接覆盖：
    xiaozhi 启动时执行 merge_configs(config.yaml, data/.config.yaml)，
    data/.config.yaml 只是"覆盖层"。里面有 server.websocket、mcp_endpoint
    （MCP 工具接入地址，带 token）这些只在 .config.yaml 里设置过的键，
    直接整体覆盖会把这些键丢掉 —— 最直接的后果是 MCP 工具（Modbus 控制）失效。

用法（在容器内执行）：
    python3 merge_xiaozhi_config.py <目标 .config.yaml> <补丁 yaml> [--drop-asr]

输出：
    第一行 CHANGED / UNCHANGED，供 shell 决定要不要重启容器。
"""

import shutil
import sys

import yaml


def deep_merge(base: dict, patch: dict) -> dict:
    """把 patch 深合并到 base：只动 patch 里出现的键，其余原样保留。"""
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def dump(data: dict) -> str:
    return yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: merge_xiaozhi_config.py <config.yaml> <patch.yaml> [--drop-asr]",
              file=sys.stderr)
        return 2

    cfg_path, patch_path = sys.argv[1], sys.argv[2]
    drop_asr = "--drop-asr" in sys.argv[3:]

    with open(cfg_path, encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}
    with open(patch_path, encoding="utf-8") as f:
        patch = yaml.safe_load(f) or {}

    if drop_asr:
        # 容器里没有 funasr / 模型文件时，不要碰 ASR，
        # 让原来的云端 ASR 继续工作（否则 fun_local 会 import 失败直接崩）。
        patch.pop("ASR", None)
        selected = patch.get("selected_module")
        if isinstance(selected, dict):
            selected.pop("ASR", None)

    before = dump(current)
    merged = deep_merge(current, patch)
    after = dump(merged)

    if before == after:
        print("UNCHANGED")
        return 0

    # 保留一份备份，方便回滚
    shutil.copy2(cfg_path, cfg_path + ".bak")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(after)
    print("CHANGED")
    return 0


if __name__ == "__main__":
    sys.exit(main())