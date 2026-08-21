"""Transporte stdio/JSON-RPC do MCPServer modular."""

from .application import LONG_RUNNING_TOOLS, TOOLS, MCPApplication
from .contracts import MCPApplicationContract, PreparedToolCall, StdioTransport
from .server import PROTOCOL_VERSION, JsonRpcStdioServer

__all__ = [
    "LONG_RUNNING_TOOLS",
    "PROTOCOL_VERSION",
    "TOOLS",
    "JsonRpcStdioServer",
    "MCPApplication",
    "MCPApplicationContract",
    "PreparedToolCall",
    "StdioTransport",
]
