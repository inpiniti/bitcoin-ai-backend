# 🏗 Project Structure & Flows

이 프로젝트는 기능별로 Flow가 분리되어 관리됩니다. 모든 Step은 해당 Flow 폴더 내에 위치해야 합니다.

## 📁 디렉토리 구조

```
src/
├── forecast/           # 📈 가격 예측 Flow (Bitcoin/Stock)
│   ├── api.step.ts
│   ├── fetch-stock.step.ts
│   ├── forecast_step.py
│   ├── format-result.step.ts
│   └── result.step.ts
│
├── whale/              # 🐋 고래 수급 추적 Flow
│   ├── whale-api.step.ts
│   ├── fetch-whale-data.step.ts
│   ├── analyze_whale_step.py
│   └── format-whale-result.step.ts
│
├── market-cap/         # 💰 AI 시총 유추 Flow
│   ├── market-cap-api.step.ts
│   ├── fetch-market-data.step.ts
│   └── format-market-cap.step.ts
│
└── common/             # 🛠 공통 유틸리티 및 Step
```

## 🌊 Defined Flows

Motia Workbench에서 다음 Flow 명칭을 사용합니다.

1.  **`bitcoin-forecast-flow`**: 가격 예측 전체 프로세스
2.  **`whale-tracking-flow`**: 고래 수급 분석 프로세스
3.  **`market-cap-inference-flow`**: 시총 유추 프로세스

## 🆕 신규 기능 추가 지침

새로운 기능을 추가할 때는 다음 절차를 따르세요.

1.  **폴더 생성**: `src/` 하위에 새로운 도메인 폴더 생성 (예: `src/portfolio/`)
2.  **Flow 명칭 정의**: `config.flows`에 사용할 고유한 Flow 이름 결정 (예: `portfolio-analysis-flow`)
3.  **Step 작성**: 해당 폴더 내에 Step 파일 생성
    - TS: `*.step.ts`
    - PY: `*_step.py`
4.  **연결**: `emits`와 `subscribes`를 사용하여 폴더 내 Step들을 연결
