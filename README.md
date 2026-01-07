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

## 🏗 아키텍처 요약 (Event-Driven Flow)

Motia 프레임워크를 기반으로 **비동기 이벤트 기반 아키텍처**를 사용합니다.

### 프로젝트 구조
```
src/
├── api.step.ts           # Step 1: API 엔드포인트 (POST /v1/forecast)
├── fetch-stock.step.ts   # Step 2: Yahoo Finance 데이터 수집
├── forecast_step.py      # Step 3: TimesFM AI 예측 (Python)
├── format-result.step.ts # Step 4: 결과 포맷팅
└── result.step.ts        # 결과 조회 API (GET /v1/result/:jobId)
```

### Flow 다이어그램
```
POST /v1/forecast
       ↓
┌──────────────────┐
│ Step1: API       │ → jobId 생성, emit("fetch-stock")
└────────┬─────────┘
         ↓ (비동기)
┌──────────────────┐
│ Step2: Fetch     │ → Yahoo Finance 데이터 수집
└────────┬─────────┘
         ↓ emit("run-forecast")
┌──────────────────┐
│ Step3: Forecast  │ → TimesFM 2.5 AI 예측 (Python)
└────────┬─────────┘
         ↓ emit("format-result")  
┌──────────────────┐
│ Step4: Format    │ → 결과 포맷팅, State 저장
└──────────────────┘

GET /v1/result/:jobId → State에서 결과 조회
```

### API 사용법

#### 요청 파라미터
| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `symbol` | string | "BTC-USD" | 예측할 종목 심볼 (Yahoo Finance 형식) |
| `interval` | string | "hour" | 데이터 주기: `"day"` (일봉) 또는 `"hour"` (시봉) |

#### 시봉 예측 (24시간)
```bash
curl -X POST https://your-space.hf.space/v1/forecast \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USD", "interval": "hour"}'
```

#### 일봉 예측 (30일)
```bash
curl -X POST https://your-space.hf.space/v1/forecast \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USD", "interval": "day"}'
```

**응답 (최대 60초 대기 후 결과 반환):**
```json
{
  "title": "BTC-USD 시봉 가격 예측 보고서",
  "symbol": "BTC-USD",
  "interval": "hour",
  "model": "TimesFM-2.5-200m",
  "predictionCount": 24,
  "predictions": [
    { "step": 1, "date": "2026-01-07T06:00:00Z", "price": 92500, "priceFormatted": "$92,500.00" }
  ]
}

```

#### 타임아웃 시 (60초 초과)
```json
{
  "jobId": "abc123-...",
  "status": "processing",
  "message": "작업이 아직 진행 중입니다.",
  "resultUrl": "/v1/result/abc123-..."
}
```
이 경우 `/v1/result/{jobId}`로 별도 조회가 필요합니다.

}
```

### 데이터 정리 정책
- **TTL**: 결과는 10분 후 자동 만료
- **조회 후 삭제**: 완료된 결과는 조회 시 자동 삭제



---

## 👩‍💻 개발 가이드라인 (Coding Guidelines)

이 프로젝트는 안정성과 유지보수성을 위해 엄격한 코딩 규칙을 따릅니다.

### 공통 원칙
1. **함수형 프로그래밍 지향 (FP)**:
   - 상태 변경(Mutation)을 최소화합니다.
   - 모든 함수는 가능한 한 순수 함수(Pure Function)로 작성하여 부작용(Side Effect)을 제거합니다.
2. **불변성 (Immutability)**:
   - 데이터 객체는 직접 수정하지 않고, 새로운 객체를 반환하는 방식을 사용합니다 (`Spread syntax`, `map`, `filter` 등 활용).
3. **명시적 타입 사용**:
   - `any` 사용을 지양하고 구체적인 인터페이스나 타입을 정의합니다.

### TypeScript (Steps) 구현 규칙
- **API Step 핸들러 시그니처**: API Step의 핸들러는 `(req, context)` 형태의 인자를 받습니다. `context`에는 `{ emit, logger }` 등이 포함됩니다.
  ```typescript
  export const handler = async (req: any, { emit }: any) => { ... }
  ```
- **Flow 시각화**: 모든 Step의 `config`에 `flows: ['flow-name']`을 명시해야 Workbench 다이어그램에 표시됩니다.
- **Event 기반 통신**: `step.call` 대신 `emit`을 사용하여 이벤트를 발행합니다.
  ```typescript
  await emit({ topic: 'event-name', data: { ... } });
  ```
  ```typescript
  // 권장사항
  export const config = {
      type: "event",
      subscribes: ["my-event-name"]
  };
  // 호출 시: step.call("my-event-name", { ... })
  ```
- **에러 처리**: `try-catch` 내부에서 에러를 묵살하지 않고, `throw error`를 통해 프레임워크 수준으로 전파하여 중앙에서 처리되도록 합니다.
- **요청 처리**: `req.json()` 대신 이미 파싱된 `req.body`를 사용합니다.

### Python (AI Models) 구현 규칙
- **모델 캐싱**: 무거운 AI 모델은 전역 변수나 싱글톤 패턴을 사용하여 **콜드 스타트(Cold Start)** 비용을 줄입니다.
  ```python
  # 초기화 예시
  tfm_model = None
  def get_model():
      global tfm_model
      if tfm_model is None:
         # Load Model...
      return tfm_model
  ```
- **타입 명시**: 함수의 입력과 반환 값에 Type Hint를 적극 활용합니다.

---

## 모티아 공식문서

아래 공식문서를 숙지한뒤 개발하시기 바랍니다.
에러가 너무 많이 발생하기 때문에 공식문서를 숙지한뒤 개발하시기 바랍니다.

https://www.motia.dev/docs

## 시작하기
https://www.motia.dev/docs/getting-started/quick-start
https://www.motia.dev/docs/getting-started/build-your-first-motia-app
https://www.motia.dev/docs/getting-started/build-your-first-motia-app/api-endpoints
https://www.motia.dev/docs/getting-started/build-your-first-motia-app/background-jobs
https://www.motia.dev/docs/getting-started/build-your-first-motia-app/workflows
https://www.motia.dev/docs/getting-started/build-your-first-motia-app/ai-agents
https://www.motia.dev/docs/getting-started/build-your-first-motia-app/streaming-agents

## 핵심개념
https://www.motia.dev/docs/concepts/overview
https://www.motia.dev/docs/concepts/steps
https://www.motia.dev/docs/concepts/workbench

## 제품소개
https://www.motia.dev/docs/product-showcase
https://www.motia.dev/docs/product-showcase/chessarena-ai

## 예제
https://www.motia.dev/docs/examples
https://www.motia.dev/docs/examples/sentiment-analysis
https://www.motia.dev/docs/examples/multi-language-data-processing
https://www.motia.dev/docs/examples/ai-content-moderation
https://www.motia.dev/docs/examples/rag-docling-weaviate
https://www.motia.dev/docs/examples/trello-automation
https://www.motia.dev/docs/examples/uptime-discord-monitor
https://www.motia.dev/docs/examples/github-stars-counter
https://www.motia.dev/docs/examples/github-integration-workflow
https://www.motia.dev/docs/examples/gmail-automation
https://www.motia.dev/docs/examples/finance-agent
https://www.motia.dev/docs/examples/ai-deep-research-agent
https://www.motia.dev/docs/examples/adapter-configuration
https://www.motia.dev/docs/examples/human-in-the-loop-workflows

## 개발 가이드
https://www.motia.dev/docs/development-guide/project-structure
https://www.motia.dev/docs/development-guide/state-management
https://www.motia.dev/docs/development-guide/streams
https://www.motia.dev/docs/development-guide/flows
https://www.motia.dev/docs/development-guide/infrastructure
https://www.motia.dev/docs/development-guide/adapters
https://www.motia.dev/docs/development-guide/observability
https://www.motia.dev/docs/development-guide/customizing-flows
https://www.motia.dev/docs/development-guide/plugins
https://www.motia.dev/docs/development-guide/middleware
https://www.motia.dev/docs/development-guide/testing
https://www.motia.dev/docs/development-guide/environment-variables
https://www.motia.dev/docs/development-guide/cli
https://www.motia.dev/docs/development-guide/motia-config

## 그외
https://www.motia.dev/docs/ai-development-guide
https://www.motia.dev/docs/api-reference

---

## ⚡ 최적화 및 배포 전략

### Docker 빌드 최적화 (Layer Caching)
허깅페이스 배포 속도 향상을 위해 `Dockerfile`의 레이어 순서를 최적화했습니다.

1. **의존성 우선 설치**: `package.json`과 `requirements.txt`를 소스 코드보다 먼저 복사(`COPY`)합니다.
2. **캐시 활용**: 소스 코드가 변경되더라도 의존성 파일이 변경되지 않았다면, 무거운 `pip install` (Torch, TimesFM 등) 과정을 캐시에서 불러와 즉시 건너뜁니다.
3. **결과**: 라이브러리 변경이 없는 배포는 **수 초 내에 완료**됩니다.

### 개발 환경 (Docker Compose)
로컬 Python 버전 이슈를 해결하기 위해 `docker-compose`를 사용합니다.
- **Hot Reloading**: 소스 코드를 수정하면 컨테이너 재시작 없이 즉시 반영됩니다.
- **Volume Mount**: 로컬 소스와 컨테이너 내부를 동기화하되, `node_modules`와 `python_modules`는 컨테이너 내부 버전을 유지하여 충돌을 방지합니다.

---

## 🚀 시작하기

**참고**: 이 프로젝트는 로컬에서 직접 빌드/실행할 필요 없이, **Hugging Face Spaces에 배포**하여 운용하는 것을 주 목적으로 합니다.
로컬 환경(`docker-compose`)은 개발 및 테스트 용도로 선택적으로 사용하실 수 있습니다.

### 배포 방법 (Hugging Face)
소스 코드를 Github 등 연동된 저장소에 Push하면, Hugging Face Spaces가 자동으로 Docker 이미지를 빌드하고 실행합니다.
- 빌드 최적화가 적용되어 있어, 초기 배포 이후 라이브러리 변경이 없다면 빠르게 재배포됩니다.

### 로컬 실행 (선택 사항)
개발 목적으로 로컬에서 실행하려면:
```powershell
docker-compose up
```

---

## 🛠 문제 해결 (Troubleshooting)

- **Flow 그래프 누락**: Step 타입을 `event`로 설정하고 명시적으로 `subscribes`를 지정해야 Flow UI에 정상적으로 표시됩니다.
- **500 Internal Server Error**: API 반환 시 `Response.json()` 대신 일반 객체를 반환해야 프레임워크가 정상 처리합니다. JSON 파싱은 `req.body`를 사용하세요.
- **Invalid input: expected array (emits)**: `config` 객체에 `emits: []` 필드가 누락되면 에러가 발생합니다. 이벤트를 방출하지 않더라도 빈 배열을 명시해야 합니다.

---
Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference