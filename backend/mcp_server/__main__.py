"""Run the Doosra MCP server over stdio: `python -m mcp_server` from backend/."""

from dotenv import load_dotenv

load_dotenv()

from mcp_server.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
