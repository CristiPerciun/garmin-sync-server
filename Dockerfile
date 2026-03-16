FROM python:3.12-slim

WORKDIR /app

# Installa dipendenze di sistema
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cartella token persistente
RUN mkdir -p /app/garth_tokens && chmod 700 /app/garth_tokens

CMD ["python", "main.py"]
