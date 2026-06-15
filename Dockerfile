FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces uses port 7860
EXPOSE 7860

# Start gunicorn
CMD gunicorn app:app --bind 0.0.0.0:7860 --timeout 120 --workers 2
