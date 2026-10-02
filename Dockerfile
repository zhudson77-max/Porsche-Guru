FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY porsche_guru ./porsche_guru
COPY data ./data

# Strongly runs app containers as a non-root user.
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD ["python", "-m", "porsche_guru.web"]
