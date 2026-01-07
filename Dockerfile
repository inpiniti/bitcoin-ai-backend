# 1. Base Image
FROM python:3.11-slim
WORKDIR /app

# 2. System Dependencies & Node.js
RUN apt-get update && apt-get install -y \
    build-essential curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && ln -s /usr/local/bin/python /usr/local/bin/python3.13 \
    && rm -rf /var/lib/apt/lists/*

# 3. Environments
ENV PORT=7860
ENV PYTHON_MODULES_PATH=/app/python_modules
ENV PATH="$PYTHON_MODULES_PATH/bin:$PATH"

# 4. Copy dependency files (requirements.txt is now empty/minimal)
COPY package.json python-deps.txt ./

# 5. Install Node.js dependencies
RUN npm install

# 6. Create placeholder for Motia to recognize Python environment
RUN mkdir -p src && echo "def handler(event, context): pass" > src/placeholder.py

# 7. Run Motia install to create venv structure
RUN npx motia install

# 8. HEAVY PACKAGES INSTALLATION (CACHED LAYER)
# This is the 30-minute step - cached unless python-deps.txt changes
RUN . $PYTHON_MODULES_PATH/bin/activate \
    && pip install --no-cache-dir -r python-deps.txt

# 9. Copy source code (only this layer changes on code edits)
COPY . .

# 10. Execution (NO second motia install - packages already installed)
EXPOSE 7860
CMD ["npm", "start"]
