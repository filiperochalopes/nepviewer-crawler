FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache tzdata
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY nepviewer_client.py web_app.py ./
COPY templates ./templates
COPY static ./static

USER 1000:1000

CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
