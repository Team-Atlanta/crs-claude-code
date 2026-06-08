# Claude Code CRS base image (prepare phase)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git \
    rsync \
    curl \
    ca-certificates \
    gnupg \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# Python 3.12 (deadsnakes PPA)
RUN add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.12 python3.12-venv python3.12-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12
RUN ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf python3 /usr/bin/python

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

ARG CLAUDE_CODE_CLI_VERSION=2.1.168

# Claude Code CLI (pinned to avoid breaking .claude.json schema changes)
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_CLI_VERSION}

# Git config
RUN git config --global user.email "crs@oss-crs.dev" \
    && git config --global user.name "OSS-CRS Patcher" \
    && git config --global --add safe.directory '*'
