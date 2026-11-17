# AWS Quick Admin Buddy MCP server container image for Amazon Bedrock AgentCore Runtime.
#
# AgentCore Runtime hosts a stateless streamable-HTTP MCP server. The server
# defaults to a loopback bind; this image sets MCP_BIND_HOST so it listens on
# all interfaces inside the Runtime-managed container, where the Runtime's
# proxy reaches it. No host port is published, and access is gated by the
# AgentCore Gateway, not by this container. If you run the image locally,
# publish to loopback only: docker run -p 127.0.0.1:8000:8000 <image>
#
# Base image: the official Docker `python` image, pulled from its Amazon ECR
# Public mirror (public.ecr.aws/docker/library). It is publicly pullable
# without credentials, so it works for any consumer of this sample, and it
# avoids Docker Hub's anonymous pull rate limits. For supply-chain hardening,
# pin the base image to a digest you have verified, e.g.:
#   FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:<digest>
# (obtain with
#  `docker buildx imagetools inspect public.ecr.aws/docker/library/python:3.12-slim`).
FROM public.ecr.aws/docker/library/python:3.12-slim

# Do not buffer stdout/stderr so logs surface promptly in the Runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_BIND_HOST=0.0.0.0

WORKDIR /app

# Install the project and its pinned runtime dependencies (see pyproject.toml).
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .

# Run as an unprivileged user. The server needs no write access to the image
# and binds to an unprivileged port, so it does not need root.
RUN useradd --create-home --uid 10001 app
USER app

# AgentCore Runtime expects the MCP server to listen on port 8000.
EXPOSE 8000

# Run the MCP server entry point (streamable-HTTP, stateless).
CMD ["quick-admin-buddy-mcp"]
