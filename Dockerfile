FROM python:3.12-slim

# Playwright chromium + system deps (installed by playwright itself)
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Runtime directories (mounted from the host in compose)
RUN mkdir -p /app/data/screenshots /app/data/videos

EXPOSE 8000

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]