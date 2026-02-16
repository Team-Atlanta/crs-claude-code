# =============================================================================
# atlantis-claude-code Patcher Module
# =============================================================================
# RUN phase: Receives POVs, generates patches using Claude Code,
# tests them using the snapshot image for incremental rebuilds.
#
# Uses host Docker socket (mounted by framework) to access snapshot images.
# =============================================================================

# These ARGs are required by the oss-crs framework template
ARG target_base_image
ARG crs_version

FROM claude-code-base

# Install libCRS
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY bin/run_patcher /usr/local/bin/run_patcher

CMD ["run_patcher"]
