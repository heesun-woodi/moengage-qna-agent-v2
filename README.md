# MoEngage Q&A Slack Bot v2

MoEngage 기술 지원을 위한 Slack 기반 Q&A Agent입니다. CSM이 답변을 검토하고 개선한 후 고객에게 전달하는 워크플로우를 지원하며, 답변 개선 과정에서 학습 데이터를 수집하여 지속적으로 품질을 향상시킵니다.

## 주요 기능

- **CSM 중심 워크플로우**: 봇 답변을 CSM 채널에 먼저 게시하여 검토 후 전달
- **하이브리드 검색**: MoEngage 공식 문서 + 내부 지원 이력 동시 검색 (FAISS 기반)
- **Claude AI 답변**: 검색 결과 기반 한국어 답변 생성 (Grounding 검증 포함)
- **자동 학습 시스템**: CSM 피드백에서 3가지 역량 학습 (문의 해석/검색/답변 작성)
- **GCP 클라우드 배포**: Cloud Run + Cloud Storage 기반 운영

## 워크플로우

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
8. 원본 메시지에 :완료: 이모지 추가
9. History DB + Learning DB에 자동 저장
```

## 아키텍처

```
                    ┌─────────────────┐
                    │   Slack 채널    │
                    │  (고객 문의)    │
                    └────────┬────────┘
                             │ 🎫 이모지
                             ▼
┌─────────────────────────────────────────────────────┐
│                  Cloud Run (GCP)                    │
│  ┌───────────────────────────────────────────────┐  │
│  │              Slack Bot (Socket Mode)          │  │
│  │  ┌─────────┐  ┌──────────┐  ┌─────────────┐  │  │
│  │  │ Handler │──│ Searcher │──│ Claude API  │  │  │
│  │  └─────────┘  └──────────┘  └─────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
│                         │                           │
│  ┌───────────────────────────────────────────────┐  │
│  │              Storage Backend                  │  │
│  │         (FAISS + Metadata JSON)               │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                             │
                             ▼
              ┌─────────────────────────────┐
              │    Cloud Storage (GCS)      │
              │  ├── vectordb/              │
              │  │   ├── faiss.index        │
              │  │   └── metadata.json      │
              │  └── learning/              │
              │      ├── learning_faiss.index│
              │      └── learning_metadata.json│
              └─────────────────────────────┘
```

## 설치 및 실행

### 로컬 개발

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일 편집

# 실행
python main.py
```

### GCP 배포

```bash
# 1. GCP 프로젝트 설정
gcloud config set project YOUR_PROJECT_ID

# 2. Secret Manager에 시크릿 생성
echo -n "xoxb-..." | gcloud secrets create slack-bot-token --data-file=-
echo -n "xapp-..." | gcloud secrets create slack-app-token --data-file=-
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
echo -n "C..." | gcloud secrets create csm-response-channel-id --data-file=-

# 3. Cloud Storage 버킷 생성
gsutil mb -l asia-northeast3 gs://YOUR_BUCKET_NAME

# 4. Cloud Build로 배포
gcloud builds submit --config cloudbuild.yaml
```

## 환경 변수

```bash
# Slack (필수)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Claude (필수)
ANTHROPIC_API_KEY=sk-ant-...

# CSM 채널 설정 (필수)
CSM_RESPONSE_CHANNEL_ID=C...   # 봇이 답변을 게시할 CSM 채널

# 스토리지 설정 (GCP 배포시)
STORAGE_BACKEND=gcs            # "local" 또는 "gcs"
GCP_STORAGE_BUCKET=...         # GCS 버킷 이름

# 기타
COMPLETE_EMOJI=완료            # 완료 이모지 (커스텀 이모지명)
LOG_LEVEL=INFO
```

## 이모지 설정

| 이모지 | 용도 |
|--------|------|
| 🎫 (`:ticket:`) | 티켓 생성 - 봇이 CSM 채널에 답변 게시 |
| :완료: (커스텀) | 티켓 완료 - History + Learning 저장 |

## 프로젝트 구조

```
moengage-qna-agent-v2/
├── main.py                     # 메인 엔트리포인트
├── config/settings.py          # 설정 관리 (pydantic-settings)
├── cloudbuild.yaml             # GCP Cloud Build 설정
├── Dockerfile                  # 컨테이너 이미지
├── src/
│   ├── api/                    # REST API
│   │   └── history_api.py      # History/Learning API 엔드포인트
│   ├── bot/                    # Slack Bot
│   │   ├── app.py              # Bolt 앱
│   │   ├── handlers.py         # 이모지 반응, CSM 피드백 핸들러
│   │   └── formatters.py       # CSM용 응답 포맷
│   ├── knowledge/              # 지식 검색
│   │   ├── moengage_api.py     # MoEngage Help Center API
│   │   ├── history_rag.py      # FAISS 기반 History RAG
│   │   ├── learning_store.py   # 학습 데이터 저장소
│   │   └── hybrid_searcher.py  # 하이브리드 검색
│   ├── llm/                    # Claude 연동
│   │   ├── claude_client.py    # Anthropic API 클라이언트
│   │   ├── prompts.py          # 시스템 프롬프트
│   │   └── grounding_validator.py  # 할루시네이션 검증
│   ├── storage/                # 스토리지 추상화
│   │   ├── base.py             # 추상 클래스
│   │   ├── local.py            # 로컬 파일시스템
│   │   └── gcs.py              # Google Cloud Storage
│   └── utils/                  # 유틸리티
│       ├── term_mapper.py      # 한영 MoEngage 용어 매핑
│       └── content_analyzer.py # URL/이미지 분석
└── scripts/                    # 유틸리티 스크립트
    ├── test_search.py          # 검색 테스트
    ├── init_history.py         # History 초기화
    └── migrate_railway_to_gcs.py  # 데이터 마이그레이션
```

## 학습 시스템

CSM이 봇 답변을 개선하는 과정에서 3가지 역량을 학습합니다:

| 역량 | 설명 | 예시 |
|------|------|------|
| **문의 해석** | 고객 질문의 의도 파악 | "세그먼트 설정 문의는 타겟팅 조건 검토가 필요" |
| **검색 전략** | 효과적인 문서 검색 방법 | "Push + Analytics 조합 검색 필요" |
| **답변 작성** | 응답 품질 향상 방법 | "구체적인 설정 경로 포함 필요" |

## 기술 스택

- **런타임**: Python 3.11+
- **Slack**: slack-bolt (Socket Mode)
- **LLM**: Anthropic Claude (claude-sonnet-4-20250514)
- **벡터 검색**: FAISS + sentence-transformers
- **임베딩**: paraphrase-multilingual-MiniLM-L12-v2
- **클라우드**: GCP (Cloud Run, Cloud Storage, Secret Manager)

## 라이선스

Private - MarketFit Lab
