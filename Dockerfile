# 1. Base Image
FROM python:3.11-slim

# 2. Workspace setup
WORKDIR /app

# 3. System dependencies & Node.js installation
# Motia가 내부적으로 python3.13 경로를 찾을 수 있으므로 심볼릭 링크 유지
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && ln -s /usr/local/bin/python /usr/local/bin/python3.13 \
    && rm -rf /var/lib/apt/lists/*

# 4. Environment Variables
ENV PORT=7860
ENV PYTHON_MODULES_PATH=/app/python_modules

# 5. Dependency files copy (Caching Layer)
COPY package.json requirements.txt ./

# 6. Install Node.js dependencies
RUN npm install

# 7. Install Python dependencies via Motia (Heavy Layer)
# 소스 코드가 복사되기 전에 실행하여, 소스 변경 시에도 이 레이어는 캐시를 활용함
# npx motia@latest 대신 설치된 motia를 사용하기 위해 npx motia 사용
RUN npx motia install

# 8. Copy Source Code
# 이제 .ts나 .py 파일만 수정하는 경우, 위 단계들은 모두 캐시되어 건너뜁니다.
COPY . .

# 9. Port & Execution
EXPOSE 7860
CMD ["npm", "start"]

