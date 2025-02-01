# Use the official Python image from the Docker Hub
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Copy the requirements file first
COPY requirements.txt /app/requirements.txt

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libaio-dev \
    libssl-dev \
    libffi-dev \
    wget \
    gnupg \
    unzip \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install Cython==0.29.24 multidict==5.1.0 yarl==1.6.3

# Install the remaining dependencies from requirements.txt
RUN pip install -r requirements.txt

# Install Chrome and WebDriver for Selenium
RUN apt-get update && apt-get install -y \
    google-chrome-stable \
    && wget -O /tmp/chromedriver.zip https://chromedriver.storage.googleapis.com/$(wget -q -O - https://chromedriver.storage.googleapis.com/LATEST_RELEASE)/chromedriver_linux64.zip \
    && unzip /tmp/chromedriver.zip chromedriver -d /usr/local/bin/ \
    && rm /tmp/chromedriver.zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the bot source code
COPY . /app

# Set the entrypoint command to run the bot
CMD ["python", "bot.py"]
