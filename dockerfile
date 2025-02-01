# Use official Python image with Playwright dependencies
FROM python:3.11-slim-bullseye

# Install system dependencies for Playwright and monitoring tools
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
    fonts-noto \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip

# Install Playwright browsers
RUN playwright install chromium && \
    playwright install-deps chromium

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PATH="/venv/bin:$PATH"

# Set up application directory
WORKDIR /app
COPY . .

# Set up entrypoint
CMD ["python", "bot.py"]
