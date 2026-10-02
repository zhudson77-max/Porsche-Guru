FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY porsche_guru ./porsche_guru
COPY data ./data

EXPOSE 8080
CMD ["python", "-m", "porsche_guru.web"]
