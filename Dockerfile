# Motia 공식 이미지 사용 (Python 3.13 사전 설치됨)
FROM motiadev/motia:latest

# 작업 디렉토리 설정
WORKDIR /app

# 루트 권한으로 빌드 도구 설치 (TimesFM 등 무거운 라이브러리용)
USER root
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 패키지 파일 복사 및 설치
COPY package.json ./
RUN npm install

# 소스 코드 복사
COPY . .

# Motia 전용 설치 명령 수행 (requirements.txt 기반 가상환경 구축)
RUN npx motia@latest install

# Hugging Face Spaces 포트 설정
ENV PORT=7860
EXPOSE 7860

# 앱 실행
CMD ["npm", "start"]
