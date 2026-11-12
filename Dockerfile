FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[postgres]" && useradd -m wt && mkdir /data && chown wt /data
USER wt
# Запускаемся не из /srv, чтобы импортировался установленный пакет, а не исходники
WORKDIR /data
ENV WT_DATABASE_URL=sqlite+aiosqlite:////data/watchtower.db
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
# Один процесс: планировщик живёт внутри приложения. Масштабирование — репликами контейнера.
# PORT задают хостинги (Render, Heroku-подобные); exec нужен, чтобы SIGTERM дошёл до uvicorn.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
