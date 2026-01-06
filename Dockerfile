# Motia 공식 이미지 사용 (Python 3.13 사전 설치됨)
# Python 3.11 사용 (TimesFM 호환성 위함)
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 및 Node.js 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 패키지 파일 복사 및 설치
COPY package.json ./
RUN npm install

# 소스 코드 복사
COPY . .

# Motia 전용 설치 명령 수행
RUN npx motia@latest install

# Hugging Face Spaces 포트 설정
ENV PORT=7860
EXPOSE 7860

# 앱 실행
CMD ["npm", "start"]
