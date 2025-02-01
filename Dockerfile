FROM python:3.11-slim-bullseye

# 1. Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    git \
    gcc \
    g++ \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libgtk-3-0 \
    libasound2 \
    libharfbuzz-icu0 \
    libgles2 \
    libegl1 \
    fonts-noto \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Install Playwright with proper chromium installation
RUN playwright install --with-deps chromium

# 4. Set environment variables
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 5. Configure working directory
WORKDIR /app
COPY . .

CMD ["python", "bot.py"]
