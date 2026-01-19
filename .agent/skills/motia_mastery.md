# 🎓 Motia Framework Mastery Guide

연구팀(Researcher Agent)이 공식 문서를 정밀 분석하여 작성한 **오류 없는 Motia 개발**을 위한 최종 마스터 지침서입니다.

---

## 1. Step 구성 (Configuration)

### 🚨 필수 준수 사항
- **`emits` 필드**: 해당 Step에서 `emit()`을 호출할 모든 토픽을 배열에 명시해야 합니다. 명시되지 않은 토픽은 런타임 에러를 발생시킵니다.
- **`flows` 필드**: Workbench에서 시각화하기 위해 필수입니다.
- **`name` 필드**: API Step의 경우 고유해야 합니다.

### API Step (TypeScript)
```typescript
export const config = {
  type: "api",
  name: "forecast-request",
  method: "POST",
  path: "/v1/forecast",
  emits: ["fetch.data"],
  flows: ["bitcoin-flow"]
};
```

---

## 2. 핸들러 구현 (Implementation)

### TypeScript 규칙
- **매개변수 구조분해**: `(req, { emit, state, logger })` 또는 `(data, { emit, state, logger })`.
- **JSON 처리**: `req.json()`을 호출하지 마세요. 이미 파싱된 `req.body`를 사용하세요.
- **응답**: API 결과는 `return { body: { ... }, status: 200 }` 형식을 권장하지만, 일반 객체 `{ ... }` 반환 시 프레임워크가 200 OK로 래핑합니다.

### Python 규칙
- **파일 명명**: `src/*_step.py` 형식을 사용해야 Motia가 에이전트 Step으로 인식합니다.
- **핸들러 정의**: `async def handler(input_data, ctx):`.
- **이벤트 발행**: `await ctx.emit({"topic": "...", "data": { ... }})`.
- **상태 관리**: `await ctx.state.set("group", "key", "value")`, `await ctx.state.get("group", "key")`.

---

## 3. 이벤트 주도 아키텍처 (EDA)

- **순차 실행**: `Step A (emit: Topic 1) -> Step B (subscribes: Topic 1)`.
- **병렬 실행**: 동일한 토픽을 여러 Step이 구독하면 병렬로 실행됩니다.
- **순환 금지**: Step A -> Step B -> Step A 와 같은 순환 구조는 무한 루프를 유발하므로 주의하세요.

---

## 4. 인프라 및 배포

- **의존성**: 
  - TS: 프로젝트 루트 `package.json`
  - Python: 프로젝트 루트 `requirements.txt`
- **배포**: Hugging Face Spaces Docker SDK 기반.
- **핫 리로딩**: `docker-compose up` 사용 시 소스 코드 변경이 즉시 반영됩니다.

---

## 5. 자주 발생하는 오류 및 해결 (FAQ)

- **Q: Flow 그래프가 안 보여요.**
  - A: `config`에 `flows: ["플로우명"]`이 있는지 확인하세요.
- **Q: Python Step이 작동하지 않아요.**
  - A: 파일명이 `_step.py`로 끝나는지 확인하세요.
- **Q: "Topic not allowed" 에러가 나요.**
  - A: 발신하는 Step의 `config.emits`에 해당 토픽이 정의되어 있는지 확인하세요.

---

**지휘 지침**: 위 가이드는 모든 서브에이전트에게 적용되는 절대적인 규칙입니다. 개발팀은 코드 작성 전 이 가이드를 반드시 복기하세요.
