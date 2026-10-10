FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPPLYSYNC_TEMP_DIR=/tmp/supplysync

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app

EXPOSE 8501
CMD ["sh", "-c", "exec python bootstrap.py streamlit --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
