FROM python:3.12-slim

WORKDIR /opt/nexora

COPY src ./src

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "uvicorn>=0.30,<1" \
    "psycopg[binary]>=3.2,<4" \
    "pydantic>=2.7,<3" \
    "pydantic-settings>=2.3,<3" \
    "python-dotenv>=1.0,<2"

ENV PYTHONPATH=/opt/nexora/src

EXPOSE 8000

CMD ["python", "-m", "nexora.generators.api.app"]
