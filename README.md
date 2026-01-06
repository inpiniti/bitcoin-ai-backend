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

## 🏗 아키텍처 요약 (Polyglot)

Motia 프레임워크를 기반으로 Node.js와 Python 환경을 통합하여 AI 모델 추론과 API 서빙을 효율적으로 처리합니다.

### 1. API Step (Node.js/TypeScript)
- **목적**: 클라이언트 통신 및 전체 워크플로우 제어
- **구성**:
  - `src/api.step.ts`: 엔트리포인트. 요청을 받아 하위 스텝들을 호출하고 응답을 반환.
  - `src/fetch-stock.step.ts`: Yahoo Finance API를 통해 실시간/과거 주가 데이터 수집.
  - `src/format-result.step.ts`: 예측 결과를 UI에 적합한 형태로 가공.

### 2. AI Step (Python 3.11+)
- **목적**: TimesFM (Time-series Foundation Model)을 이용한 고성능 시계열 예측
- **구성**:
  - `src/forecast_step.py`: Google TimesFM 모델 로드 및 추론 수행 (JAX/CPU 활용).

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
- **API Step 핸들러 시그니처**: API Step의 핸들러는 `(req, context)` 형태의 인자를 받습니다. `req` 객체에서 직접 `body` 등에 접근해야 합니다.
  ```typescript
  export const handler = async (req: any, { step }: any) => { ... }
  ```
- **Step 통신**: 현재 Motia 버전 호환성을 위해 `task` 타입 대신 **`event` 타입**을 사용해야 합니다.
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
