FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo HOST=0.0.0.0
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /app/data && chown app:app /app/data
COPY --chown=app:app app ./app
COPY --chown=app:app config ./config
USER app
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
CMD ["python", "-m", "app.cli", "serve"]
