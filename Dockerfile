FROM python:3.14-slim
WORKDIR /app
COPY requirements-image.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements-image.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps . \
 && useradd --create-home --uid 10001 agent \
 && mkdir -p /tmp/agentserver && chown 10001:10001 /tmp/agentserver
COPY --chown=10001:10001 .agent_configs ./.agent_configs
USER agent
# OPTIMIZATION_LOCAL_DIR: the team's optimizer-ready configuration; the solo agent's deployment sets .agent_configs/solo.
# AGENTSERVER_STATE_ROOT: a folder the agent user can write (resilient stream replay).
ENV PYTHONUNBUFFERED=1 ENABLE_SENSITIVE_DATA=false OPTIMIZATION_LOCAL_DIR=/app/.agent_configs/team \
    AGENTSERVER_STATE_ROOT=/tmp/agentserver
EXPOSE 8088
CMD ["advisor-team"]
