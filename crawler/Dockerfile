FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the frontend directory so the script can write to it if volume mapped
RUN mkdir -p /frontend

CMD ["python", "crawler.py"]
