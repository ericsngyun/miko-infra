FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 miko && chown -R miko:miko /app
USER miko

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8100"]
