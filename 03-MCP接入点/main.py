#!/usr/bin/env python3
"""
MCP Endpoint Server 主入口文件
"""
import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.server import main

if __name__ == "__main__":
    main()
