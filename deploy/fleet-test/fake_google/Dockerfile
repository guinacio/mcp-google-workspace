# syntax=docker/dockerfile:1
#
# TEST-ONLY derived image for the fleet qualification stack. Never published:
# release.yml builds only the repository-root Dockerfile. It adds a fake Google
# transport that activates only with MCP_FLEET_FAKE_GOOGLE set to its sentinel
# and a .test issuer (see fleet_fake_google/hook.py).
ARG BASE_IMAGE=mcp-google-workspace:fleet-test
FROM ${BASE_IMAGE}

ARG SITE_PACKAGES=/app/.venv/lib/python3.12/site-packages
COPY --chown=root:root fleet_fake_google/ ${SITE_PACKAGES}/fleet_fake_google/
COPY --chown=root:root fleet_fake_google.pth ${SITE_PACKAGES}/fleet_fake_google.pth

LABEL org.opencontainers.image.title="mcp-google-workspace fleet-test (fake Google, TEST ONLY)" \
      io.github.guinacio.mcp-google-workspace.test-only="true"
