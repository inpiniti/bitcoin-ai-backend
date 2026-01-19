# 🛠️ Bitcoin AI Backend 로컬 환경 구축 스크립트
# 이 스크립트는 로컬 OS(Windows)에 직접 개발 환경을 구축합니다. Docker 없이 실행할 때 사용하세요.

Write-Host "🚀 로컬 개발 환경 구축을 시작합니다..." -ForegroundColor Cyan

# 1. Node.js 의존성 설치
Write-Host "`n📦 Node.js 패키지 설치 중..." -ForegroundColor Yellow
npm install

# 2. Python 가상 환경 생성 및 의존성 설치
Write-Host "`n🐍 Python 가상환경 생성 및 패키지 설치 중..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

# 가상환경 활성화 및 설치
& .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r python-deps.txt
pip install -r requirements.txt

# 3. 모델 폴더 생성
Write-Host "`n📁 모델 저장 폴더 생성..." -ForegroundColor Yellow
if (-not (Test-Path "models")) { New-Item -ItemType Directory -Path "models/market_cap" -Force }

Write-Host "`n✅ 로컬 환경 구축 완료!" -ForegroundColor Green
Write-Host "이제 아래 명령어로 서버를 띄우고 테스트하세요:" -ForegroundColor Cyan
Write-Host "----------------------------------------"
Write-Host "npm run dev"
Write-Host "----------------------------------------"
