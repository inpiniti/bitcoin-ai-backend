---
name: Mission Control
description: Bitcoin AI Backend 프로젝트의 서브에이전트 지휘소. 복잡한 작업을 분할하여 전문 에이전트에게 할당합니다.
---

# 🛸 Mission Control - 서브에이전트 지휘소

이 스킬은 **Antigravity IDE**의 핵심 기능인 **다중 에이전트 워크플로우**를 정의합니다.
사용자가 복잡한 요청을 하면, 메인 에이전트가 이 문서를 참조하여 적절한 서브에이전트를 소환합니다.

## 🏗 에이전트 조직 구조

```
┌─────────────────────────────────────────────────────────────┐
│                    🧠 MAIN AGENT (지휘관)                    │
│         사용자 요청 분석 → 작업 분할 → 에이전트 할당          │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  🔬 연구팀    │ │  💻 개발팀    │ │  🧪 품질팀    │
│  (Research)   │ │  (Development)│ │  (Quality)    │
└───────────────┘ └───────────────┘ └───────────────┘
```

---

## 🔬 연구팀 (Research Team)

### 역할
- 최신 API 문서 조사 (Yahoo Finance, TradingView, Motia)
- 라이브러리 업데이트 확인 (TimesFM, TensorFlow)
- 기술 트렌드 분석 및 요약

### 소환 조건
- "~에 대해 조사해줘"
- "최신 문서 확인해줘"
- "어떤 API를 쓰면 좋을까?"

### 작업 방식
```
1. browser_subagent로 공식 문서 접근
2. 핵심 정보 추출 및 요약
3. README.md 또는 별도 문서에 정리
```

---

## 💻 개발팀 (Development Team)

### 역할 분류

#### 1. TypeScript Step 담당
- `src/*.step.ts` 파일 개발
- Motia 이벤트 기반 아키텍처 구현
- API 엔드포인트 생성

#### 2. Python AI 담당
- `src/*.py` AI 모델 개발
- `scripts/run_market_cap.py` 시총 유추 모델
- TimesFM, TensorFlow 활용

#### 3. 인프라 담당
- `Dockerfile` 최적화
- `docker-compose.yml` 설정
- Hugging Face Spaces 배포

### 소환 조건
- "새로운 API 만들어줘"
- "Step 추가해줘"
- "Python 모델 수정해줘"
- "배포 설정 변경해줘"

### 개발 규칙 (README.md 참조)
```typescript
// ✅ 올바른 패턴
export const config = {
  type: "event",
  subscribes: ["my-event"],
  emits: [],  // 반드시 명시
  flows: ["main-flow"]  // Flow UI 표시용
};

// ❌ 금지 패턴
- any 타입 남발
- req.json() 사용 (req.body 사용할 것)
- emits 필드 누락
```

---

## 🧪 품질팀 (Quality Team)

### 역할

#### 1. 테스트 담당
- API 엔드포인트 동작 확인
- curl 명령으로 수동 테스트
- 응답 포맷 검증

#### 2. 코드 리뷰 담당
- README.md 코딩 가이드라인 준수 확인
- 함수형 프로그래밍 원칙 검토
- 불변성 유지 여부 점검

#### 3. 보안 담당
- 환경 변수 노출 여부 점검
- API Rate Limit 확인
- 에러 메시지 정보 누출 점검

### 소환 조건
- "테스트해줘"
- "코드 리뷰해줘"
- "보안 점검해줘"
- "품질 확인해줘"

---

## 📋 작업 할당 프로토콜

### 1. 단일 작업 (Simple Task)
```
사용자: "forecast API에 파라미터 추가해줘"
      ↓
메인 에이전트: 개발팀(TypeScript) 직접 처리
```

### 2. 복합 작업 (Complex Task)
```
사용자: "새로운 AI 예측 기능 기획부터 배포까지"
      ↓
메인 에이전트:
  1. 연구팀 → API 조사	
  2. 개발팀(TS) → Step 작성
  3. 개발팀(Python) → AI 모델 개발
  4. 품질팀 → 테스트
  5. 개발팀(인프라) → 배포
```

### 3. 병렬 작업 (Parallel Task)
```
사용자: "whale API 버그 수정하면서 동시에 문서 업데이트"
      ↓
메인 에이전트:
  ├─ 개발팀 → 버그 수정 (병렬)
  └─ 연구팀 → 문서 업데이트 (병렬)
```

---

## 🔧 프로젝트 특화 정보

### 핵심 파일 구조
```
src/
├── api.step.ts           # POST /v1/forecast
├── fetch-stock.step.ts   # Yahoo Finance 데이터 수집
├── forecast_step.py      # TimesFM AI 예측
├── whale-api.step.ts     # POST /v1/whale
├── market-cap-api.step.ts # POST /v1/market-cap
└── ...

scripts/
└── run_market_cap.py     # TensorFlow 시총 유추 모델
```

### 배포 환경
- **Target**: Hugging Face Spaces (Docker SDK)
- **Local Dev**: docker-compose up

### 코딩 규칙 요약
1. 함수형 프로그래밍 (순수 함수)
2. 불변성 유지 (Spread syntax 활용)
3. 명시적 타입 (any 지양)
4. 이벤트 기반 통신 (emit 사용)
