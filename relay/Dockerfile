FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY __init__.py app.py store.py /app/relay/
RUN useradd --uid 10001 --create-home receiver
USER receiver
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "exec uvicorn relay.app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
