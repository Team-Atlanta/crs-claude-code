# =============================================================================
# crs-claude-code Seed Generator Module (bug-finding)
# =============================================================================
# RUN phase: Uses Claude Code to analyze the target and generate intelligent
# seed inputs, then launches the fuzzer sidecar via libCRS to find crashes.
# =============================================================================

# These ARGs are required by the oss-crs framework template
ARG target_base_image
ARG crs_version

FROM claude-code-base

# Install libCRS (CLI + Python package)
COPY --from=libcrs . /libCRS
RUN pip3 install /libCRS \
    && python3 -c "from libCRS.base import DataType; print('libCRS OK')"

# Install crs-claude-code package (seed generator + agents)
COPY pyproject.toml /opt/crs-claude-code/pyproject.toml
COPY seed_generator.py /opt/crs-claude-code/seed_generator.py
COPY agents/ /opt/crs-claude-code/agents/
RUN pip3 install /opt/crs-claude-code

CMD ["run_seed_generator"]
