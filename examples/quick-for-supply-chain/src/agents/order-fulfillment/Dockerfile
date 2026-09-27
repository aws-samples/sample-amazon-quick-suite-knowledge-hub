# Order Fulfillment Agent — AgentCore Runtime container (arm64).
# Runs an MCP server (FastMCP, streamable-http) on port 8000 at /mcp via main.py,
# exposing the agent as a callable MCP tool (run_order_fulfillment).
# mcp is pinned <2.0.0: mcp 2.0.0 removed mcp.server.fastmcp which main.py imports.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir strands-agents strands-agents-tools "mcp>=1.10.0,<2.0.0" boto3
COPY main.py .
# AgentCore delivers requests from outside the container's loopback, so the
# server must listen on all interfaces. main.py defaults to 127.0.0.1 for local
# use; the container opts in to bind-all explicitly.
ENV BIND_HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "main.py"]
