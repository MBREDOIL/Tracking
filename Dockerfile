# Dockerfile
FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    API_ID="" \
    API_HASH="" \
    BOT_TOKEN="" \
    OWNER_ID="" \
    DB_NAME="tracker.db" \
    CHECK_INTERVAL=300

RUN apt-get update && apt-get install -y \
    wget unzip curl chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

RUN CHROME_VERSION=$(chromium --version | grep -oP '\d+\.\d+\.\d+') \
    && CHROMEDRIVER_VERSION=$(curl -s https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION%.*}) \
    && wget -q -O /tmp/chromedriver.zip https://chromedriver.storage.googleapis.com/$CHROMEDRIVER_VERSION/chromedriver_linux64.zip \
    && unzip /tmp/chromedriver.zip -d /usr/bin/ \
    && rm /tmp/chromedriver.zip \
    && chmod +x /usr/bin/chromedriver

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
