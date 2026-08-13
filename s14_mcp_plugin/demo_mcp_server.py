#!/usr/bin/env python3
"""
demo_mcp_server.py - 一个最小 MCP 工具服务器（演示用）

它实现了 JSON-RPC 2.0 over stdio 的最小子集：
    initialize      -> 返回服务器信息
    tools/list      -> 返回本服务器提供的工具清单
    tools/call      -> 执行某个工具

消息格式：每条消息一行 JSON。

这个服务器提供两个"天气"工具（数据是编造的，仅演示协议）。
harness 侧的代码（code.py）不知道也不关心工具是怎么实现的——
这就是 MCP 的价值：工具的实现方与使用方彻底解耦。
"""

import json
import sys

TOOLS = [
    {
        "name": "get_weather",
        "description": "查询指定城市的当前天气（演示数据）。",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "get_time",
        "description": "返回当前服务器时间。",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def handle(req: dict) -> dict:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"serverInfo": {"name": "demo-weather", "version": "1.0"}}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "get_weather":
            city = args.get("city", "未知")
            text = f"{city}: 晴，25°C，微风（演示数据）"
        elif name == "get_time":
            import datetime
            text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"未知工具 {name}"}}
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]}}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"未知方法 {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle(req)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "JSON 解析失败"}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
