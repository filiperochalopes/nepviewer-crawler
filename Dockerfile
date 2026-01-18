FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies defined in requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only) and system dependencies
RUN playwright install --with-deps chromium

# Copy application code
COPY nepviewer_daemon.py ./
COPY web_app.py ./

# Copy templates and static files
COPY templates ./templates
COPY static ./static

# Default command
CMD ["python", "nepviewer_daemon.py"]