FROM python:3.10

ARG PYTHON_ENV

ENV PYTHON_ENV=${PYTHON_ENV} \
  PYTHONFAULTHANDLER=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONHASHSEED=random \
  PIP_NO_CACHE_DIR=off \
  PIP_DISABLE_PIP_VERSION_CHECK=on \
  PIP_DEFAULT_TIMEOUT=100 \
  POETRY_VERSION=1.2.2 \
  TINI_VERSION=v0.19.0

RUN pip install "poetry==$POETRY_VERSION"

WORKDIR /app
COPY poetry.lock pyproject.toml /app/

RUN dpkgArch="$(dpkg --print-architecture | awk -F- '{ print $NF }')" \
    && curl -o /usr/local/bin/tini -sSLO "https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-${dpkgArch}" \
    && chmod +x /usr/local/bin/tini && tini --version \
    && python -m venv /venv \
    && pip install "poetry==$POETRY_VERSION" \
    && poetry --version \
    && poetry export -f requirements.txt | /venv/bin/pip install -r /dev/stdin

COPY . /app
RUN poetry build --no-ansi --no-interaction && /venv/bin/pip install dist/*.whl

ENTRYPOINT ["/usr/local/bin/tini", "--"]
CMD ["./docker-entrypoint.sh"]
