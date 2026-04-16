# =============================================================================
# crs-claude-code Docker Bake Configuration
# =============================================================================
#
# Builds the CRS base image with Claude Code CLI and Python dependencies.
#
# Usage:
#   docker buildx bake prepare
#   docker buildx bake --push prepare   # Push to registry
# =============================================================================

variable "REGISTRY" {
  default = "ghcr.io/team-atlanta"
}

variable "VERSION" {
  default = "cli-2.0.17"
}

variable "CLAUDE_CODE_CLI_VERSION" {
  default = "2.0.17"
}

function "tags" {
  params = [name]
  result = [
    "${REGISTRY}/${name}:${VERSION}",
    "${name}:${VERSION}"
  ]
}

# -----------------------------------------------------------------------------
# Groups
# -----------------------------------------------------------------------------

group "default" {
  targets = ["prepare"]
}

group "prepare" {
  targets = ["claude-code-base"]
}

# -----------------------------------------------------------------------------
# Base Image
# -----------------------------------------------------------------------------

target "claude-code-base" {
  context    = "."
  dockerfile = "oss-crs/base.Dockerfile"
  tags       = tags("claude-code-base")
  args = {
    CLAUDE_CODE_CLI_VERSION = CLAUDE_CODE_CLI_VERSION
  }
}
