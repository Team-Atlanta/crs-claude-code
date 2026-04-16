# =============================================================================
# crs-claude-code Patcher Module
# =============================================================================
# RUN phase: Receives POVs, generates patches using Claude Code,
# tests them using the snapshot image for incremental rebuilds.
#
# Uses host Docker socket (mounted by framework) to access snapshot images.
# =============================================================================

# These ARGs are required by the oss-crs framework template
ARG target_base_image
ARG crs_version

FROM claude-code-base:cli-2.0.17

# Install libCRS (CLI + Python package)
COPY --from=libcrs . /libCRS
RUN pip3 install /libCRS \
    && python3 -c "from libCRS.base import DataType; print('libCRS OK')"

# Install crs-claude-code package (patcher + agents)
COPY pyproject.toml /opt/crs-claude-code/pyproject.toml
COPY patcher.py /opt/crs-claude-code/patcher.py
COPY agents/ /opt/crs-claude-code/agents/
RUN pip3 install /opt/crs-claude-code

CMD ["run_patcher"]
