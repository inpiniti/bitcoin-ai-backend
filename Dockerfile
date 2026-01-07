# 1. Base Image (Python 3.11 사용)
FROM python:3.11-slim

# 2. Workspace setup
WORKDIR /app

# 3. System dependencies & Node.js installation
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
# 가상환경의 실행 경로를 PATH에 미리 추가하여 Motia가 바로 찾을 수 있게 함
ENV PATH="$PYTHON_MODULES_PATH/bin:$PATH"

# 5. Dependency manifests copy (Caching Layer)
COPY package.json requirements.txt ./

# 6. Install Node.js dependencies
RUN npm install

# 7. Pre-install Python dependencies (Heavy Layer)
# 중요: 소스 복사 전에 가상환경을 직접 만들고 라이브러리를 설치합니다.
# requirements.txt가 바뀌지 않는 한 이 무거운 단계는 무조건 캐시(CACHED)됩니다.
RUN python -m venv $PYTHON_MODULES_PATH \
    && . $PYTHON_MODULES_PATH/bin/activate \
    && pip install --no-cache-dir -r requirements.txt

# 8. Copy Source Code
# 이제 .ts나 .py 파일만 수정하는 경우, 여기서부터 빌드가 시작되므로 매우 빠릅니다.
COPY . .

# 9. Motia Finalize (Fast)
# 이미 $PYTHON_MODULES_PATH에 라이브러리가 다 있으므로, 
# Motia는 설정만 확인하고 수 초 내에 끝납니다.
RUN npx motia install

# 10. Execution
EXPOSE 7860
CMD ["npm", "start"]


