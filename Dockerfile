# 1. Base Image
FROM python:3.11-slim
WORKDIR /app

# 2. System Dependencies & Node.js
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && ln -s /usr/local/bin/python /usr/local/bin/python3.13 \
    && rm -rf /var/lib/apt/lists/*

# 3. Environments
ENV PORT=7860
ENV PYTHON_MODULES_PATH=/app/python_modules
ENV PATH="$PYTHON_MODULES_PATH/bin:$PATH"

# 4. Dependency Manifests Copy
COPY package.json requirements.txt ./

# 5. Install Node.js dependencies
RUN npm install

# 6. [CACHING MAGIC] Create a dummy Python step to "bait" Motia installer
# Motia installer needs to see at least one .py file to build the Python env.
# Doing this before COPYing the full source allows us to cache the 30-min install.
RUN mkdir -p src && echo "def handler(event, context): pass" > src/placeholder.py

# 7. Heavy Installation (This layer will be CACHED unless requirements.txt changes)
# Torch, TimesFM, JAX 등 30분 걸리는 설치가 여기서 진행되고 캐싱됩니다.
RUN npx motia install

# 8. Copy Real Source Code
# 이제 .ts나 .py 소스코드만 수정하는 경우, 여기서부터 빌드가 시작되어 매우 빠릅니다.
COPY . .

# 9. Fast Finalize
# 이미 라이브러리가 캐시된 레이어에 있으므로, 이 단계는 수 초 내에 설정만 완료하고 끝납니다.
RUN npx motia install

# 10. Start
EXPOSE 7860
CMD ["npm", "start"]



