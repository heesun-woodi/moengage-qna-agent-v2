# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoEngage Q&A Slack Bot v2 - CSM 타겟 MVP 버전입니다. CSM이 봇의 답변을 먼저 검토하고 개선한 후 고객에게 전달하는 플로우를 지원합니다. 답변 개선 과정에서 학습 데이터를 수집하여 향후 답변 품질을 고도화합니다.

## v1과의 차이점

| 항목 | v1 | v2 |
|------|----|----|
| 타겟 | 고객 채널 직접 답변 | CSM 채널에 답변 게시 |
| 답변 방식 | 원본 스레드에 직접 | CSM 채널에서 검토 후 전달 |
| 학습 | 단순 히스토리 저장 | 3가지 역량 학습 (문의해석/검색/답변작성) |
| DB | Railway FAISS | v1과 동일한 DB 공유 |

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Check environment configuration
python scripts/check_env.py

# Run the bot
python main.py

# Test search functionality
python scripts/test_search.py

# Initialize history with sample data
python scripts/init_history.py
```

## Architecture (v2)

```
1. 고객 문의 발생
2. CSM이 문의에 🎫 이모지 추가
3. 봇이 CSM 채널에 답변 게시 (원본 문의 링크 포함)
        ↓
[답변 개선 루프]
4. CSM이 봇 답변 스레드에서 피드백
   - 추가 검색 요청: "SDK 버전 관련 문서도 찾아줘"
   - 맥락 정보 제공: "이 고객사는 A 상황이야"
   - 오류 지적: "이 부분이 틀렸어"
5. 봇이 개선된 답변 생성
6. 만족할 때까지 반복
        ↓
7. CSM이 최종 답변을 고객에게 전달 (수동)
8. 원본 메시지에 ✅ 이모지 추가
9. 히스토리 DB + 학습 DB에 저장
```

### Key Components

1. **Knowledge Search** (`src/knowledge/`)
   - `moengage_api.py`: MoEngage Help Center API 클라이언트
   - `history_rag.py`: FAISS 기반 내부 지원 이력 RAG
   - `hybrid_searcher.py`: 두 소스 병렬 검색 및 결과 병합
   - `learning_store.py`: (v2 신규) 학습 데이터 저장/조회

2. **LLM** (`src/llm/`)
   - `claude_client.py`: Anthropic Claude API (학습 추출, CSM 피드백 분석 기능 추가)
   - `prompts.py`: 시스템 프롬프트 (학습 추출 프롬프트 포함)
   - `grounding_validator.py`: 할루시네이션 검증

3. **Slack Bot** (`src/bot/`)
   - `app.py`: Slack Bolt 앱 (Socket Mode)
   - `handlers.py`: CSM 채널 답변 게시, 대화형 개선, 학습 추출
   - `formatters.py`: CSM용 응답 포맷, 개선된 답변 포맷
   - `state_machine.py`: 메시지 상태 관리

## Data Flow (v2)

1. **티켓 생성** (🎫 이모지)
   - 원본 메시지 텍스트 추출
   - Hybrid Search 실행
   - Claude로 답변 생성
   - **CSM 응답 채널에 새 메시지로 게시** (원본 링크 포함)
   - 세션 데이터 저장 (개선 루프용)

2. **답변 개선** (CSM 스레드 답글)
   - CSM 피드백 의도 분석
   - 추가 검색 실행 (필요시)
   - 개선된 답변 생성
   - 세션 데이터 업데이트

3. **티켓 완료** (✅ 이모지)
   - 전체 대화 수집
   - 학습 포인트 추출 (Claude)
   - History RAG + Learning Store에 저장

## 학습 구조 (v2 신규)

### LearningEntry 데이터 모델

```python
LearningEntry:
  - original_query: str          # 원본 문의
  - query_interpretation:        # 문의 해석 학습
      - initial: str
      - corrections: List[str]
      - final: str
  - search_history:              # 검색 학습
      - initial_queries: List[str]
      - initial_results: List[str]
      - additional_searches: List[SearchIteration]
      - used_documents: List[str]
  - response_evolution:          # 답변 작성 학습
      - initial_response: str
      - feedback: List[str]
      - iterations: List[str]
      - final_response: str
  - learning_points:             # 추출된 학습 포인트
      - query_lesson: str
      - search_lesson: str
      - response_lesson: str
```

## Configuration

환경 변수 (`.env`):
```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...

# v2 필수 설정
CSM_RESPONSE_CHANNEL_ID=C...   # 봇이 답변을 게시할 CSM 채널

# v1과 공유 (선택)
HISTORY_API_URL=https://...    # Railway FAISS API URL
```

설정 파일: `config/settings.py` (pydantic-settings 기반)

## MoEngage Terminology

한영 용어 매핑은 `src/utils/term_mapper.py` 참조:
- 세그먼트 → Segment, Segmentation
- 캠페인 → Campaign
- 푸시 → Push Notification
- 속성 → Attribute, User Attribute
- 플로우 → Flow, Flows

## Support Categories

1. 채널 세팅 및 연동 (카카오/SMS/이메일)
2. 써드파티 연동
3. 데이터 모델
4. SDK 설치
5. Analyze 분석 기능
6. 유저 세그먼테이션
7. 캠페인 세팅
8. 기본 UI 가이드

## Response Format

### CSM 채널 초기 답변
```
📋 **새로운 문의**

**원본 메시지**: [슬랙 링크]

**고객 문의 내용**:
>문의 내용

---

**🔍 문제 파악**
(이슈 요약)

**✅ 해결 가이드**
- 단계 1: ...

**🔗 참고 자료**
[이전 Q&A]
[MoEngage HelpCenter]

---
_💡 답변이 불충분하면 이 스레드에서 추가 질문을 해주세요._
_✅ 최종 답변이 완성되면 원본 메시지에 :white_check_mark: 이모지를 추가해주세요._
```

### 개선된 답변
```
📝 **개선된 답변 (#1)**

(개선된 답변 내용)
```

## Key Files

- `main.py`: 엔트리포인트
- `config/settings.py`: 설정 관리 (CSM_RESPONSE_CHANNEL_ID 추가)
- `src/knowledge/hybrid_searcher.py`: 하이브리드 검색 핵심 로직
- `src/knowledge/learning_store.py`: (v2) 학습 데이터 저장/조회
- `src/knowledge/history_updater.py`: (v2) LearningEntry 모델 포함
- `src/llm/prompts.py`: Claude 시스템 프롬프트 (학습 추출 프롬프트 포함)
- `src/llm/claude_client.py`: (v2) 학습 추출, CSM 피드백 분석 메서드 추가
- `src/bot/handlers.py`: (v2) CSM 채널 게시, 대화형 개선 핸들러
- `src/bot/formatters.py`: (v2) CSM용 응답 포맷

## Slack 테스트 가이드

1. `.env`에 `CSM_RESPONSE_CHANNEL_ID` 설정
2. `python main.py` 실행
3. 테스트 채널에서 문의 메시지에 🎫 이모지 추가
4. CSM 채널에 답변이 게시되는지 확인
5. CSM 채널 답변 스레드에서 피드백 작성
6. 개선된 답변이 생성되는지 확인
7. 원본 메시지에 ✅ 이모지 추가
8. 학습 포인트가 추출되어 저장되는지 확인
