FROM python:3.11-slim

WORKDIR /app

# FAISS 빌드를 위한 시스템 의존성
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p /app/data/vectordb /app/data/feedback

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "main.py"]
