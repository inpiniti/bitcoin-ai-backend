---
description: Git Worktree를 활용한 병렬 개발 환경 설정 가이드
---

# 🔀 Parallel Development Workflow

여러 에이전트가 **동시에** 다른 기능을 개발할 때 충돌 없이 작업하는 방법입니다.

## 핵심 개념: Git Worktree

하나의 저장소를 **여러 폴더로 체크아웃**하여 각 에이전트가 독립적으로 작업합니다.

```
c:\Users\USER\git\
├── bitcoin-ai-backend/        # 메인 (main 브랜치)
├── bitcoin-ai-feature-api/    # 에이전트1 (feature/new-api)
├── bitcoin-ai-fix-whale/      # 에이전트2 (fix/whale-bug)
└── bitcoin-ai-perf-opt/       # 에이전트3 (perf/optimization)
```

---

## Step 1: 병렬 작업 환경 생성

```powershell
# 메인 저장소로 이동
cd c:\Users\USER\git\bitcoin-ai-backend

# 새 기능용 worktree 생성
// turbo
git worktree add ../bitcoin-ai-feature-api -b feature/new-api

# 버그 수정용 worktree 생성
// turbo
git worktree add ../bitcoin-ai-fix-whale -b fix/whale-bug

# 성능 최적화용 worktree 생성
// turbo
git worktree add ../bitcoin-ai-perf-opt -b perf/optimization
```

---

## Step 2: 각 에이전트에 작업 할당

### 에이전트 1: 신규 API 개발
```powershell
cd c:\Users\USER\git\bitcoin-ai-feature-api
# 여기서 새 API 관련 코드 작성
```

### 에이전트 2: 버그 수정
```powershell
cd c:\Users\USER\git\bitcoin-ai-fix-whale
# 여기서 whale API 버그 수정
```

### 에이전트 3: 성능 최적화
```powershell
cd c:\Users\USER\git\bitcoin-ai-perf-opt
# 여기서 시총 유추 API 최적화
```

---

## Step 3: 작업 완료 후 병합

```powershell
# 메인으로 돌아가기
cd c:\Users\USER\git\bitcoin-ai-backend

# 기능 브랜치 병합
git merge feature/new-api
git merge fix/whale-bug
git merge perf/optimization

# worktree 정리
// turbo
git worktree remove ../bitcoin-ai-feature-api
// turbo
git worktree remove ../bitcoin-ai-fix-whale
// turbo
git worktree remove ../bitcoin-ai-perf-opt
```

---

## 충돌 방지 규칙

### 파일 담당 분리
| 에이전트 | 담당 파일 | 금지 파일 |
|---------|----------|----------|
| API 개발 | `src/new-*.step.ts` | 기존 파일 수정 금지 |
| 버그 수정 | `src/whale-*.ts` | 다른 API 수정 금지 |
| 최적화 | `scripts/*.py` | Step 파일 수정 금지 |

### 공통 파일 수정 시
```
README.md, package.json, requirements.txt 같은 공통 파일은
반드시 순차적으로 수정 (병렬 수정 금지)
```

---

## 작업 상태 확인

```powershell
# 현재 활성 worktree 목록
// turbo
git worktree list

# 출력 예시:
# c:/Users/USER/git/bitcoin-ai-backend        abc1234 [main]
# c:/Users/USER/git/bitcoin-ai-feature-api    def5678 [feature/new-api]
# c:/Users/USER/git/bitcoin-ai-fix-whale      ghi9012 [fix/whale-bug]
```

---

## 알림 시스템 연동

각 에이전트 작업 완료 시 OS 알림:
```powershell
# PowerShell 알림 스크립트
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "✅ feature/new-api 개발 완료!`n병합 준비 되었습니다.",
    "에이전트 알림",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
)
```

---

## 요약

```
1. git worktree로 병렬 환경 생성
2. 각 에이전트에 독립 폴더 할당
3. 담당 파일 분리로 충돌 방지
4. 작업 완료 후 순차 병합
5. worktree 정리
```
