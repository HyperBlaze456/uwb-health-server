# syntax=docker/dockerfile:1
# uwb-server 이미지 (SPEC 3.1, 4.1). migrate·intake·core·api가 이 이미지 하나를 명령만 바꿔 씁니다.
# compose.dev는 같은 이미지로 tools/fake_gaitfusion.py도 띄웁니다.
#
#   docker build --build-arg CORE_VERSION=$(git rev-parse --short HEAD) -t uwb-server .
#
# 의존성은 uv.lock 그대로 설치합니다(uv sync --frozen). 코드(src, tools)는 /app 아래에 두고 PYTHONPATH로 찾으므로
# 프로젝트 자체는 설치하지 않습니다(--no-install-project). 그래서 잠금 파일 밖의 빌드 도구(hatchling)가 필요 없습니다.

FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM python:3.14.10-slim-trixie

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 1. 의존성 층: 코드가 바뀌어도 다시 쓰입니다.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2. 코드 층. migrate는 /app/migrations를, 지표 목록은 /app/contracts를 찾습니다(pipeline/migrate.py, pipeline/metrics.py).
COPY migrations ./migrations
COPY contracts ./contracts
COPY src ./src
COPY tools ./tools
RUN python -m compileall -q src tools

# 3. root가 아닌 사용자. 호스트의 DATA_DIR(raw, rejected, archive, result, trajectory)은 이 UID가 쓸 수 있어야 합니다:
#    sudo mkdir -p /srv/uwb/{raw,rejected,archive,result,trajectory} && sudo chown -R 10001:10001 /srv/uwb
ARG APP_UID=10001
RUN groupadd --gid ${APP_UID} uwb \
 && useradd --uid ${APP_UID} --gid uwb --no-create-home --home-dir /app --shell /usr/sbin/nologin uwb \
 && mkdir -p /data \
 && chown uwb:uwb /data

# CORE_VERSION은 analysis.run.core_version에 들어갑니다(SPEC 4.3). 빌드마다 바뀌므로 맨 뒤에 둡니다.
ARG CORE_VERSION=dev
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src:/app" \
    DATA_ROOT=/data \
    CORE_VERSION=${CORE_VERSION}

USER uwb

# 명령은 compose.yaml이 서비스마다 줍니다.
CMD ["python", "-c", "import sys; sys.exit('명령이 없습니다. compose.yaml의 command를 보세요.')"]
