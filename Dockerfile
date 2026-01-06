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
    && ln -s /usr/local/bin/python /usr/local/bin/python3.13 \
    && rm -rf /var/lib/apt/lists/*

# Motia Python 환경 경로 명시 (빌드 및 런타임 공유)
ENV PYTHON_MODULES_PATH=/app/python_modules

# 패키지 파일 복사 및 설치 (Node.js)
COPY package.json ./
RUN npm install

# 패키지 파일 복사 및 설치 (Python) - 캐싱 효과 극대화
# Motia가 나중에 인식할 수 있도록 미리 venv를 만들고 패키지를 설치해 둡니다.
COPY requirements.txt ./
RUN python -m venv $PYTHON_MODULES_PATH \
    && . $PYTHON_MODULES_PATH/bin/activate \
    && pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사 (이 단계 이후에는 캐싱이 깨질 수 있음)
COPY . .

# Motia 설치 (소스 코드 인식 후 마무리 설정)
# 이미 설치된 패키지는 건너뛰고 필요한 설정만 수행하므로 빠릅니다.
RUN npx motia@latest install

# Hugging Face Spaces 포트 설정
ENV PORT=7860
EXPOSE 7860

# 앱 실행
CMD ["npm", "start"]
