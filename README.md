---
title: Bitcoin Ai Backend
emoji: 🏃
colorFrom: red
colorTo: gray
sdk: docker
pinned: false
---

# Bitcoin AI Backend (Motia Framework)

이 프로젝트는 [Motia](https://motia.dev) 프레임워크를 사용하여 구축된 Bitcoin 분석 및 예측 백엔드 서버입니다. Hugging Face Spaces의 Docker SDK를 통해 배포됩니다.

## 🏗 아키텍처 요약

Motia 프레임워크를 기반으로 다중 언어(Polyglot) 환경을 구축하여 AI 모델 추론과 API 서빙을 효율적으로 통합합니다.

### 1. AI Step (Python)
- **목적**: TimesFM (Time-series Foundation Model)을 이용한 비트코인 시계열 예측.
- **환경**: Python 3.11+ 환경에서 실행되며, Motia의 `Python Step`을 통해 정의됩니다.
- **역할**: 대규모 시계열 데이터를 입력받아 향후 가격 변동성을 예측하고 이벤트를 방출합니다.

### 2. API Step (Node.js/TypeScript)
- **목적**: React 앱 및 외부 클라이언트와의 통신을 위한 엔드포인트 제공.
- **환경**: Node.js/TypeScript 환경에서 실행되며, Motia의 `API Step`을 통해 정의됩니다.
- **역할**: RESTful API를 통해 클라이언트 요청을 처리하고, AI Step에서 생성된 데이터를 반환하거나 워크플로우를 트리거합니다.

## 🚀 시작하기

### 도커이미지 활용
본 프로젝트는 `motiadev/motia:latest` 기반의 커스텀 Dockerfile을 사용하여 구축됩니다.

### 개발 로드맵 및 진행 상태
- [x] **1. Motia 서버 초기 설정**: 기본 프로젝트 구조 (`package.json`, `Dockerfile`) 및 의존성 설정 완료.
- [x] **2. API Step 기초 구현**: React 앱과 통신할 `src/api.step.ts` 완성.
- [x] **3. AI Step (TimesFM) 구현**: `src/forecast_step.py`에서 Google TimesFM 모델 추론 로직 구현 완료.
    - [x] Google TimesFM 체크포인트 로드 (Hugging Face Hub 연동)
    - [x] 시계열 데이터 전처리 및 입력 파이프라인 구축
    - [x] 추론 결과 후처리 및 JSON 응답 반환
- [ ] **4. Docker 및 배포 최적화**: Hugging Face Spaces 환경에 최적화된 빌드 및 런타임 검증.

## 🧠 TimesFM 구현 상세

TimesFM (Time-series Foundation Model)은 Google Research에서 개발한 시계열 예측 파운데이션 모델입니다. 이 프로젝트에서는 다음과 같이 활용합니다:

- **Model**: `google/timesfm-1.0-200m`
- **Input**: 비트코인 과거 OHLCV 데이터
- **Output**: 향후 N개 시점에 대한 가격 예측 값 및 신뢰 구간
- **Flow**:
    1. 클라이언트가 API Step(`POST /v1/forecast`)으로 과거 데이터 전송
    2. API Step이 `step.call("bitcoin-forecast", ...)`를 통해 Python Step 호출
    3. Python Step이 `TimesFM` 모델로 추론 수행 (JAX/CPU 활용)
    4. 예측 결과를 JSON 객체로 즉시 반환하여 API 응답으로 전달

---
Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference
