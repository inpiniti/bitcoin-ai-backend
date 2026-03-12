# 1. Base Image
FROM python:3.11-slim
WORKDIR /app

# 2. System Dependencies (Node.js 불필요 - Motia 제거)
RUN apt-get update && apt-get install -y \
    build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

# 3. Environments
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 4. Copy dependency files (캐시 레이어 분리)
COPY requirements.txt ./

# 5. Python 의존성 설치 (무거운 패키지 포함 - 캐시됨)
# torch + timesfm 설치 시간이 오래 걸릴 수 있음
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy source code
COPY . .

# 7. Execution
EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
