"""
连接管理器
负责管理WebSocket连接和消息转发
"""

import asyncio
import json
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed
from ..utils.logger import get_logger

logger = get_logger()


class RobotConnection:
    """小智端连接信息"""

    def __init__(self, websocket: WebSocketServerProtocol, agent_id: str):
        self.websocket = websocket
        self.agent_id = agent_id
        self.connection_uuid = str(uuid.uuid4())
        self.timestamp = time.time()


class ConnectionManager:
    """连接管理器"""

    def __init__(self):
        # 工具端连接: {agentId: websocket}
        self.tool_connections: Dict[str, WebSocketServerProtocol] = {}
        # 小智端连接: {connection_uuid: RobotConnection}
        self.robot_connections: Dict[str, RobotConnection] = {}
        # 连接时间戳: {agentId: timestamp}
        self.connection_timestamps: Dict[str, float] = {}
        # 连接锁
        self._lock = asyncio.Lock()

    async def register_tool_connection(
        self, agent_id: str, websocket: WebSocketServerProtocol
    ):
        """注册工具端连接"""
        async with self._lock:
            # 如果已存在连接，先关闭旧连接
            if agent_id in self.tool_connections:
                old_websocket = self.tool_connections[agent_id]
                try:
                    await old_websocket.close(1000, "新连接替换")
                except Exception as e:
                    logger.warning(f"关闭旧工具端连接失败: {e}")

            self.tool_connections[agent_id] = websocket
            self.connection_timestamps[agent_id] = time.time()
            logger.info(f"工具端连接已注册: {agent_id}")

    async def register_robot_connection(
        self, agent_id: str, websocket: WebSocketServerProtocol
    ) -> str:
        """注册小智端连接，返回分配的UUID"""
        async with self._lock:
            robot_conn = RobotConnection(websocket, agent_id)
            self.robot_connections[robot_conn.connection_uuid] = robot_conn
            self.connection_timestamps[agent_id] = time.time()
            logger.info(
                f"小智端连接已注册: {agent_id}, UUID: {robot_conn.connection_uuid}"
            )
            return robot_conn.connection_uuid

    async def unregister_tool_connection(self, agent_id: str):
        """注销工具端连接"""
        async with self._lock:
            if agent_id in self.tool_connections:
                del self.tool_connections[agent_id]
                if agent_id in self.connection_timestamps:
                    del self.connection_timestamps[agent_id]
                logger.info(f"工具端连接已注销: {agent_id}")

    async def unregister_robot_connection(self, connection_uuid: str):
        """注销小智端连接"""
        async with self._lock:
            if connection_uuid in self.robot_connections:
                robot_conn = self.robot_connections[connection_uuid]
                del self.robot_connections[connection_uuid]
                logger.info(
                    f"小智端连接已注销: {robot_conn.agent_id}, UUID: {connection_uuid}"
                )

    def _transform_jsonrpc_id(self, original_id: Any, connection_uuid: str) -> str:
        """转换JSON-RPC ID"""
        if isinstance(original_id, int):
            return f"{connection_uuid}_n_{original_id}"
        elif isinstance(original_id, str):
            return f"{connection_uuid}_s_{original_id}"
        else:
            # 其他类型转换为字符串
            return f"{connection_uuid}_s_{str(original_id)}"

    def _restore_jsonrpc_id(self, transformed_id: str) -> Tuple[Optional[str], Any]:
        """还原JSON-RPC ID，返回(connection_uuid, original_id)"""
        if not transformed_id or "_" not in transformed_id:
            return None, None

        # 解析格式: uuid_type_original_id
        parts = transformed_id.split("_", 2)
        if len(parts) < 3:
            return None, None

        connection_uuid = parts[0]
        id_type = parts[1]
        original_id_part = parts[2]

        if id_type == "n":
            # 数字类型
            try:
                original_id = int(original_id_part)
            except ValueError:
                original_id = original_id_part
        elif id_type == "s":
            # 字符串类型
            if original_id_part == "null":
                original_id = None
            else:
                original_id = original_id_part
        else:
            # 未知类型，保持原样
            original_id = original_id_part

        return connection_uuid, original_id

    def transform_jsonrpc_message(
        self, message: Dict[str, Any], connection_uuid: str
    ) -> Dict[str, Any]:
        """转换JSON-RPC消息的ID"""
        if not isinstance(message, dict):
            return message

        # 创建消息副本
        transformed_message = message.copy()

        # 转换ID
        if "id" in transformed_message:
            original_id = transformed_message["id"]
            if original_id:
                transformed_message["id"] = self._transform_jsonrpc_id(
                    original_id, connection_uuid
                )

        return transformed_message

    def restore_jsonrpc_message(
        self, message: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """还原JSON-RPC消息的ID，返回(connection_uuid, restored_message)"""
        if not isinstance(message, dict):
            return None, message

        # 创建消息副本
        restored_message = message.copy()

        # 还原ID
        if "id" in restored_message:
            transformed_id = restored_message["id"]
            connection_uuid, original_id = self._restore_jsonrpc_id(transformed_id)
            if connection_uuid:
                restored_message["id"] = original_id
                return connection_uuid, restored_message

        return None, restored_message

    async def forward_to_tool(self, agent_id: str, message: Any) -> bool:
        """转发消息给工具端"""
        async with self._lock:
            if agent_id not in self.tool_connections:
                logger.warning(f"工具端连接不存在: {agent_id}")
                return False

            websocket = self.tool_connections[agent_id]
            try:
                # 确保消息是字符串格式
                if isinstance(message, dict):
                    message_str = json.dumps(message, ensure_ascii=False)
                elif isinstance(message, str):
                    message_str = message
                else:
                    message_str = str(message)

                await websocket.send_text(message_str)
                logger.debug(f"消息已转发给工具端 {agent_id}: {message_str[:100]}...")
                return True
            except ConnectionClosed:
                logger.warning(f"工具端连接已关闭: {agent_id}")
                await self.unregister_tool_connection(agent_id)
                return False
            except Exception as e:
                logger.error(f"转发消息给工具端失败: {e}")
                return False

    async def forward_to_robot_by_uuid(
        self, connection_uuid: str, message: Any
    ) -> bool:
        """根据UUID转发消息给特定的小智端连接"""
        async with self._lock:
            if connection_uuid not in self.robot_connections:
                logger.warning(f"小智端连接不存在: {connection_uuid}")
                return False

            robot_conn = self.robot_connections[connection_uuid]
            try:
                # 确保消息是字符串格式
                if isinstance(message, dict):
                    message_str = json.dumps(message, ensure_ascii=False)
                elif isinstance(message, str):
                    message_str = message
                else:
                    message_str = str(message)

                await robot_conn.websocket.send_text(message_str)
                logger.debug(
                    f"消息已转发给小智端 {robot_conn.agent_id} (UUID: {connection_uuid}): {message_str[:100]}..."
                )
                return True
            except ConnectionClosed:
                logger.warning(f"小智端连接已关闭: {connection_uuid}")
                await self.unregister_robot_connection(connection_uuid)
                return False
            except Exception as e:
                logger.error(f"转发消息给小智端失败: {e}")
                return False

    def get_connection_stats(self) -> Dict[str, Any]:
        """获取连接统计信息"""
        # 统计每个agent_id的连接数
        agent_connection_counts = {}
        for robot_conn in self.robot_connections.values():
            agent_id = robot_conn.agent_id
            agent_connection_counts[agent_id] = (
                agent_connection_counts.get(agent_id, 0) + 1
            )

        return {
            "tool_connections": len(self.tool_connections),
            "robot_connections": len(self.robot_connections),
            "total_connections": len(self.tool_connections)
            + len(self.robot_connections),
            "robot_connections_by_agent": agent_connection_counts,
        }

    def is_tool_connected(self, agent_id: str) -> bool:
        """检查工具端是否已连接"""
        return agent_id in self.tool_connections

    def is_robot_connected(self, agent_id: str) -> bool:
        """检查小智端是否已连接"""
        return any(
            conn.agent_id == agent_id for conn in self.robot_connections.values()
        )

    def get_robot_connections_by_agent(self, agent_id: str) -> List[RobotConnection]:
        """获取指定agent_id的所有小智端连接"""
        return [
            conn
            for conn in self.robot_connections.values()
            if conn.agent_id == agent_id
        ]


# 全局连接管理器实例
connection_manager = ConnectionManager()
