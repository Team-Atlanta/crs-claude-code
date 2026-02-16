# Claude Code CRS base image (prepare phase)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git \
    rsync \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# uv + Python (standalone, no system python3 needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN uv python install 3.12 \
    && UV_PYTHON=$(uv python find 3.12) \
    && ln -s "$UV_PYTHON" /usr/local/bin/python3.12 \
    && ln -s python3.12 /usr/local/bin/python3 \
    && ln -s python3 /usr/local/bin/python

# Docker CLI (not daemon — uses host socket)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" \
    > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Node.js (for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (pinned to avoid breaking .claude.json schema changes)
RUN npm install -g @anthropic-ai/claude-code@2.1.42

# Git config
RUN git config --global user.email "crs@oss-crs.dev" \
    && git config --global user.name "OSS-CRS Patcher" \
    && git config --global --add safe.directory '*'
