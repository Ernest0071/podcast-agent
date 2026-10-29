FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COLUMNS=160

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[dev]"

COPY config.yaml ./
COPY data ./data
COPY tests ./tests

RUN useradd --create-home --uid 1000 agent && mkdir -p /app/outputs && chown -R agent /app
USER agent

ENTRYPOINT ["podcast-agent"]
CMD ["run", "--input", "data/transcripts", "--output", "outputs"]
