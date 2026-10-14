FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user (uid 1000 is also what Hugging Face Spaces expects).
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app app ./app
USER app

# TRUSTED_PROXY_HOPS is detected at runtime (1 on Hugging Face Spaces and Render, else 0).
ENV PORT=8000 DATABASE_PATH=/tmp/tutor.db
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
