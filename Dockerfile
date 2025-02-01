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
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update -y \
    && apt-get install -y google-chrome-stable fonts-ipafont-gothic fonts-wqy-zenhei fonts-thai-tlwg fonts-kacst fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Install matching ChromeDriver
RUN GOOGLE_CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d '.' -f 1) \
    && CHROMEDRIVER_DOWNLOAD_URL="https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/$GOOGLE_CHROME_VERSION.0.0.0/linux64/chromedriver-linux64.zip" \
    && wget --no-verbose -O /tmp/chromedriver.zip "$CHROMEDRIVER_DOWNLOAD_URL" \
    && unzip /tmp/chromedriver.zip -d /usr/bin/ \
    && mv /usr/bin/chromedriver-linux64/chromedriver /usr/bin/chromedriver \
    && chmod +x /usr/bin/chromedriver \
    && rm -rf /usr/bin/chromedriver-linux64 /tmp/chromedriver.zip

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
