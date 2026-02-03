# MoEngage Q&A Slack Bot

MoEngage 기술 지원을 위한 Slack 기반 Q&A Agent입니다. 이모지 기반 트리거와 자동 학습 시스템을 통해 지속적으로 개선되는 지원 봇입니다.

## 주요 기능

- 🎫 **티켓 이모지 트리거**: Slack 메시지에 티켓 이모지를 달면 자동으로 답변 생성
- 🔍 **하이브리드 검색**: MoEngage 공식 문서 + 내부 지원 이력 동시 검색
- 🤖 **Claude AI 답변**: 검색 결과를 바탕으로 한국어 답변 생성
- 📚 **자동 학습**: 완료된 문의는 자동으로 History에 저장되어 향후 답변에 활용

## 워크플로우

```
1. 고객 문의 → 2. 티켓 이모지 🎫 → 3. Agent 답변 생성
                                          ↓
4. 완료 이모지 ✅ ← 5. 문제 해결 ←──────┘
       ↓
6. History 자동 업데이트 → 향후 답변 품질 향상
```

## 설치

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일 편집하여 API 키 입력
```

## 환경 변수

```bash
# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# 선택사항
REDIS_URL=redis://localhost:6379
```

## 실행

```bash
# Bot 실행
python main.py

# 검색 테스트
python scripts/test_search.py

# History 초기화 (샘플 데이터)
python scripts/init_history.py
```

## Slack 앱 설정

1. [api.slack.com](https://api.slack.com/apps)에서 앱 생성
2. **OAuth Scopes** 추가:
   - `app_mentions:read`
   - `chat:write`
   - `reactions:read`
   - `channels:history`
3. **Event Subscriptions** 활성화:
   - `reaction_added`
   - `reaction_removed`
   - `message.channels`
4. **Socket Mode** 활성화

## 이모지 설정

| 이모지 | 용도 |
|--------|------|
| 🎫 (`:ticket:`) | 티켓 생성 - Agent 호출 |
| ✅ (`:white_check_mark:`) | 티켓 완료 - History 저장 |

## 프로젝트 구조

```
moengage-qna-agent/
├── main.py                 # 메인 엔트리포인트
├── config/settings.py      # 설정 관리
├── src/
│   ├── bot/               # Slack Bot
│   │   ├── app.py         # Bolt 앱
│   │   ├── handlers.py    # 이벤트 핸들러
│   │   └── state_machine.py
│   ├── knowledge/         # 지식 검색
│   │   ├── moengage_api.py    # Zendesk API
│   │   ├── history_rag.py     # History RAG
│   │   └── hybrid_searcher.py
│   └── llm/               # Claude 연동
│       ├── claude_client.py
│       └── prompts.py
└── scripts/               # 유틸리티 스크립트
```

## 기술 스택

- **Slack**: slack-bolt
- **LLM**: Anthropic Claude
- **검색**: Zendesk API (MoEngage), ChromaDB (History)
- **상태 관리**: In-memory (Redis 확장 가능)

## 라이선스

Private - MarketFit Lab
