FROM docker.io/library/python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
# Bot images install their own deps with `-c /opt/venv/constraints.txt`, so they never upgrade the base's packages.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /opt/venv/constraints.txt

FROM docker.io/library/python:3.14-slim
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin sprite \
 && mkdir /data /config \
 && chown sprite:sprite /data /config
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SPRITE_DATA_DIR=/data \
    SPRITE_CONFIG=/config/config.toml \
    SPRITE_SECRETS_DIR=/run/secrets
USER sprite
WORKDIR /data
