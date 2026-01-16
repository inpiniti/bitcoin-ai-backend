# 1. Base Image
FROM python:3.11-slim
WORKDIR /app

# 2. System Dependencies & Node.js
RUN apt-get update && apt-get install -y \
    build-essential curl git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && ln -s /usr/local/bin/python /usr/local/bin/python3.13 \
    && rm -rf /var/lib/apt/lists/*


# 3. Environments
ENV PORT=7860
ENV PYTHON_MODULES_PATH=/app/python_modules
ENV PATH="$PYTHON_MODULES_PATH/bin:$PATH"

# 4. Copy dependency files
COPY package.json python-deps.txt ./

# 5. Install Node.js dependencies
RUN npm install

# 6. MANUALLY create Python venv and install heavy packages (CACHED LAYER)
# This is the 30-minute step - cached unless python-deps.txt changes
RUN python -m venv $PYTHON_MODULES_PATH \
    && . $PYTHON_MODULES_PATH/bin/activate \
    && pip install --no-cache-dir -r python-deps.txt

# 7. Copy source code (Moved up so Motia can detect steps)
COPY . .

# 8. Run Motia install
RUN npx motia install

# 10. Execution
EXPOSE 7860
CMD ["npm", "start"]
