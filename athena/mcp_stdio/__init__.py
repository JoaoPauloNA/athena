"""Transporte stdio/JSON-RPC do MCPServer modular."""

from .application import LONG_RUNNING_TOOLS, TOOLS, MCPApplication
from .contracts import MCPApplicationContract, PreparedToolCall, StdioTransport
from .modern import (
    LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_MODERN_VERSIONS,
)
from .server import PROTOCOL_VERSION, JsonRpcStdioServer

__all__ = [
    "LEGACY_PROTOCOL_VERSION",
    "LONG_RUNNING_TOOLS",
    "MODERN_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_MODERN_VERSIONS",
    "TOOLS",
    "JsonRpcStdioServer",
    "MCPApplication",
    "MCPApplicationContract",
    "PreparedToolCall",
    "StdioTransport",
]
