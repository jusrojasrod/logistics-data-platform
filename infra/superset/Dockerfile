ARG SUPERSET_VERSION=6.0.0
FROM apache/superset:${SUPERSET_VERSION}

USER root
RUN uv pip install --no-cache --python /app/.venv/bin/python "psycopg2-binary==2.9.9"
USER superset

COPY superset_config.py /app/pythonpath/superset_config.py
