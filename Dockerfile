FROM python:3.13-slim

# Install ffmpeg + curl (untuk healthcheck)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies dulu (layer terpisah agar cache efisien)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy seluruh source
COPY . .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
