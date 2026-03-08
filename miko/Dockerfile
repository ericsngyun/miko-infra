FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY miko_bot.py .
COPY SOUL.md .
COPY static/ ./static/

EXPOSE 8400

CMD ["python", "miko_bot.py"]
