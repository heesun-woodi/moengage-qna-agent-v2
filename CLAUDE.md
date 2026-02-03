# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoEngage Q&A Slack Bot v2 - CSM 타겟 MVP 버전. CSM이 봇의 답변을 먼저 검토하고 개선한 후 고객에게 전달하는 플로우를 지원합니다. 답변 개선 과정에서 학습 데이터를 수집하여 향후 답변 품질을 고도화합니다.

### v1과의 차이점

| 항목 | v1 | v2 |
|------|----|----|
| 타겟 | 고객 채널 직접 답변 | CSM 채널에 답변 게시 |
| 답변 방식 | 원본 스레드에 직접 | CSM 채널에서 검토 후 전달 |
| 학습 | 단순 히스토리 저장 | 3가지 역량 학습 (문의해석/검색/답변작성) |

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

# Run tests
pytest
pytest -v tests/test_file.py::test_function  # Single test
```

## Architecture

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
   - `hybrid_searcher.py`: MoEngage API + History RAG 병렬 검색 및 결과 병합
   - `moengage_api.py`: MoEngage Help Center API 클라이언트
   - `history_rag.py`: FAISS 기반 내부 지원 이력 RAG
   - `history_api_client.py`: Railway History API 원격 클라이언트
   - `learning_store.py`: 학습 데이터 저장/조회 (로컬 + Railway)
   - `learning_api_client.py`: Railway Learning API 원격 클라이언트

2. **LLM** (`src/llm/`)
   - `claude_client.py`: Anthropic Claude API (답변 생성, 학습 추출, CSM 피드백 분석)
   - `prompts.py`: 시스템 프롬프트
   - `grounding_validator.py`: 할루시네이션 검증
   - `query_optimizer.py`: LLM 기반 검색 쿼리 최적화
   - `thread_analyzer.py`: 스레드 분석

3. **Slack Bot** (`src/bot/`)
   - `app.py`: Slack Bolt 앱 (Socket Mode)
   - `handlers.py`: 이모지 반응, CSM 스레드 답변, PDF 임포트 핸들러
   - `formatters.py`: CSM용 응답 포맷
   - `state_machine.py`: 메시지 상태 관리 (IDLE → PROCESSING → ANSWERED → COMPLETED)

4. **Utilities** (`src/utils/`)
   - `term_mapper.py`: 한영 MoEngage 용어 매핑
   - `content_analyzer.py`: URL/이미지 콘텐츠 분석
   - `retry.py`: Claude API 재시도 로직 (circuit breaker 포함)

## Data Flow

1. **티켓 생성** (🎫 이모지) → `handle_ticket_emoji()`
   - `hybrid_search()` 실행 (MoEngage API + History RAG 병렬)
   - `generate_response()` → Claude로 답변 생성
   - `validate_and_filter_response()` → 할루시네이션 검증
   - CSM 응답 채널에 새 메시지로 게시
   - 세션 데이터 `_csm_sessions`에 저장

2. **답변 개선** (CSM 스레드 답글) → `handle_csm_thread_reply()`
   - `analyze_csm_reply()` → CSM 피드백 의도 분석
   - 추가 검색 실행 (필요시)
   - `generate_improved_response()` → 개선된 답변 생성

3. **티켓 완료** (✅ 이모지) → `handle_complete_emoji()`
   - `extract_learning_points()` → Claude로 학습 포인트 추출
   - `add_from_slack_thread()` → History DB 저장 (로컬 + Railway)
   - `save_learning_entry()` → Learning Store 저장 (로컬 + Railway)

## Configuration

환경 변수 (`.env`):
```bash
# Slack (필수)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Claude (필수)
ANTHROPIC_API_KEY=sk-ant-...

# CSM 채널 설정 (v2 필수)
CSM_RESPONSE_CHANNEL_ID=C...   # 봇이 답변을 게시할 CSM 채널
CSM_CHANNEL_IDS=C...,C...      # CSM 내부 채널 목록 (쉼표 구분)

# History API (선택 - Railway 배포시)
HISTORY_API_URL=https://...
HISTORY_API_KEY=...
HISTORY_API_ENABLED=true

# 기타
LOG_LEVEL=INFO
```

설정 파일: `config/settings.py` (pydantic-settings 기반)

## MoEngage Terminology

한영 용어 매핑은 `src/utils/term_mapper.py` 참조:
- 세그먼트 → Segment, Segmentation
- 캠페인 → Campaign
- 푸시 → Push Notification
- 속성 → Attribute, User Attribute
- 플로우 → Flow, Flows

## 학습 구조 (LearningEntry)

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

## Slack 테스트 가이드

1. `.env`에 `CSM_RESPONSE_CHANNEL_ID` 설정
2. `python main.py` 실행
3. 테스트 채널에서 문의 메시지에 🎫 이모지 추가
4. CSM 채널에 답변이 게시되는지 확인
5. CSM 채널 답변 스레드에서 피드백 작성
6. 개선된 답변이 생성되는지 확인
7. 원본 메시지에 ✅ 이모지 추가
8. 학습 포인트가 추출되어 저장되는지 확인
