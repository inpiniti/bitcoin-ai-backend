# Base image with Motia framework
FROM motiadev/motia:latest

# 작업 디렉토리 설정
WORKDIR /app

# 루트 권한으로 필요한 패키지 설치 (Python 및 빌드 도구)
USER root
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 패키지 파일 복사 및 설치
COPY package.json ./
RUN npm install

# Python 의존성 설치
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# Hugging Face Spaces는 기본적으로 7860 포트를 사용합니다.
ENV PORT=7860
EXPOSE 7860

# 앱 실행
CMD ["npm", "start"]
