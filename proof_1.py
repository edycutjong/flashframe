import os
import sys
import asyncio
from dotenv import load_dotenv
from mcp.client.stdio import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

async def proof_1():
    load_dotenv(os.path.expanduser('~/.config/flashframe/clickhouse.env'))
    
    env = os.environ.copy()
    env.update({
        "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
        "CLICKHOUSE_USER": os.environ["CLICKHOUSE_USER"],
        "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
        "CLICKHOUSE_DATABASE": os.environ.get("CLICKHOUSE_DATABASE", "flashframe"),
        "CLICKHOUSE_ALLOW_WRITE_ACCESS": os.environ.get("CLICKHOUSE_ALLOW_WRITE_ACCESS", "true"),
        "CHDB_ENABLED": "true"
    })

    clickhouse = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_clickhouse.main"],
                env=env,
            )
        )
    )
    
    print("Proof 1: MCP Server Tools")
    tools = await clickhouse.get_tools()
    print(f"Loaded {len(tools)} tools:")
    for tool in tools:
        print(f"- {tool.name}")

if __name__ == "__main__":
    asyncio.run(proof_1())
