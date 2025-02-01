FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    API_ID="" \
    API_HASH="" \
    BOT_TOKEN="" \
    OWNER_ID="" \
    DB_NAME="tracker.db" \
    CHECK_INTERVAL=300

# Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    unzip \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update -y \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install correct ChromeDriver using official method
RUN CHROME_MAJOR_VERSION=$(google-chrome --version | awk -F '[ .]' '{print $3}') \
    && CHROMEDRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$CHROME_MAJOR_VERSION") \
    && wget -q -O /tmp/chromedriver.zip "https://chromedriver.storage.googleapis.com/$CHROMEDRIVER_VERSION/chromedriver_linux64.zip" \
    && unzip /tmp/chromedriver.zip -d /usr/bin/ \
    && chmod +x /usr/bin/chromedriver \
    && rm /tmp/chromedriver.zip

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
