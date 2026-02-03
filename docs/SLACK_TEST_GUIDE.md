# Slack 실제 테스트 가이드

MoEngage Q&A Agent를 Slack에서 실제로 테스트하기 위한 설정 가이드입니다.

---

## 사전 준비물

| 항목 | 환경변수 | 형식 | 필수 |
|------|----------|------|------|
| Slack Bot Token | `SLACK_BOT_TOKEN` | `xoxb-...` | ✅ |
| Slack App Token | `SLACK_APP_TOKEN` | `xapp-...` | ✅ |
| Slack Signing Secret | `SLACK_SIGNING_SECRET` | 32자 hex | ✅ |
| Anthropic API Key | `ANTHROPIC_API_KEY` | `sk-ant-...` | ✅ |

---

## Step 0: API 키 확인

### Anthropic API 키 확인
1. https://console.anthropic.com/ 접속
2. 로그인 (계정 없으면 생성)
3. **API Keys** 메뉴 → 기존 키 확인 또는 새로 생성
4. `sk-ant-...` 형식의 키 복사

### Slack 앱 확인
1. https://api.slack.com/apps 접속
2. 기존 앱이 있는지 확인
3. 없으면 Step 1에서 새로 생성

---

## Step 1: Slack App 생성

### 1.1 Slack API 접속
1. https://api.slack.com/apps 접속
2. **Create New App** 클릭
3. **From scratch** 선택
4. App Name: `MoEngage Q&A Agent` (또는 원하는 이름)
5. Workspace: 테스트할 Slack 워크스페이스 선택

### 1.2 Socket Mode 활성화
1. 좌측 메뉴 → **Socket Mode** 클릭
2. **Enable Socket Mode** 토글 ON
3. App-Level Token 생성:
   - Token Name: `socket-token`
   - Scope: `connections:write` 추가
   - **Generate** 클릭
   - ⚠️ `xapp-...` 토큰 복사 → `.env`의 `SLACK_APP_TOKEN`

### 1.3 Bot Token Scopes 설정
1. 좌측 메뉴 → **OAuth & Permissions**
2. **Bot Token Scopes** 섹션에서 아래 권한 추가:

| Scope | 용도 |
|-------|------|
| `channels:history` | 채널 메시지 읽기 |
| `channels:read` | 채널 정보 읽기 |
| `chat:write` | 메시지 전송 |
| `reactions:read` | 이모지 반응 읽기 |
| `users:read` | 사용자 정보 읽기 |

### 1.4 Event Subscriptions 설정
1. 좌측 메뉴 → **Event Subscriptions**
2. **Enable Events** 토글 ON
3. **Subscribe to bot events** 섹션에서 추가:

| Event | 용도 |
|-------|------|
| `message.channels` | 채널 메시지 이벤트 |
| `reaction_added` | 이모지 추가 이벤트 |
| `reaction_removed` | 이모지 제거 이벤트 |

### 1.5 앱 설치
1. 좌측 메뉴 → **Install App**
2. **Install to Workspace** 클릭
3. 권한 승인
4. ⚠️ `xoxb-...` 토큰 복사 → `.env`의 `SLACK_BOT_TOKEN`

### 1.6 Signing Secret 복사
1. 좌측 메뉴 → **Basic Information**
2. **App Credentials** 섹션
3. **Signing Secret** → Show → 복사 → `.env`의 `SLACK_SIGNING_SECRET`

---

## Step 2: 환경 변수 설정

### 2.1 .env 파일 생성
```bash
cd /Users/joseph/Documents/GitHub/moengage-qna-agent
cp .env.example .env
```

### 2.2 .env 파일 수정
```bash
# Slack Configuration (Step 1에서 복사한 값들)
SLACK_BOT_TOKEN=xoxb-your-actual-bot-token
SLACK_APP_TOKEN=xapp-your-actual-app-token
SLACK_SIGNING_SECRET=your-actual-signing-secret

# Anthropic Claude API
ANTHROPIC_API_KEY=sk-ant-your-anthropic-api-key

# 나머지는 기본값 유지
```

---

## Step 3: 의존성 설치 및 Bot 실행

### 3.1 의존성 설치
```bash
cd /Users/joseph/Documents/GitHub/moengage-qna-agent
pip install -r requirements.txt
```

### 3.2 Bot 실행
```bash
python main.py
```

### 정상 실행 시 출력:
```
INFO - Starting MoEngage Q&A Agent...
INFO - Slack app created and handlers registered
INFO - Starting Slack app in Socket Mode...
```

---

## Step 4: 테스트

### 4.1 Bot 초대
1. Slack에서 테스트할 채널 열기
2. 채널에서 `/invite @MoEngage Q&A Agent` 입력
   - 또는 채널 설정 → 멤버 추가 → Bot 검색하여 추가

### 4.2 티켓 생성 테스트 (🎫)
1. 채널에 테스트 메시지 작성:
   ```
   푸시 알림이 지연되는 현상이 발생합니다. 어떻게 해결하나요?
   ```
2. 해당 메시지에 🎫 (`:ticket:`) 이모지 추가
3. Bot이 스레드에 답변 작성 확인

### 4.3 완료 테스트 (✅)
1. 티켓 처리 후 ✅ (`:white_check_mark:`) 이모지 추가
2. History에 저장되었다는 확인 메시지 확인

---

## 트러블슈팅

### Bot이 응답하지 않는 경우

1. **터미널 로그 확인**: 에러 메시지 확인
2. **Socket Mode 확인**: Slack API → Socket Mode → Connected 상태 확인
3. **채널 초대 확인**: Bot이 채널에 초대되어 있는지 확인
4. **이벤트 구독 확인**: `reaction_added` 이벤트가 구독되어 있는지 확인

### API 키 오류

**Slack 인증 오류:**
```
Error: invalid_auth
```
→ `SLACK_BOT_TOKEN`이 올바른지 확인

**Anthropic API 오류:**
```
Error: authentication_error
```
→ `ANTHROPIC_API_KEY`가 올바른지 확인

### Socket Mode 연결 실패
```
Error: cannot_connect_to_socket_mode_server
```
→ `SLACK_APP_TOKEN` (xapp-...) 확인

### 권한 오류
```
Error: missing_scope
```
→ OAuth & Permissions에서 필요한 scope 추가 후 앱 재설치

---

## 검증 체크리스트

- [ ] Slack App 생성 완료
- [ ] Socket Mode 활성화 및 토큰 발급
- [ ] Bot Token Scopes 설정
- [ ] Event Subscriptions 설정
- [ ] 앱 워크스페이스 설치
- [ ] .env 파일 설정
- [ ] Bot 실행 (`python main.py`)
- [ ] 채널에 Bot 초대
- [ ] 🎫 이모지 테스트
- [ ] ✅ 이모지 테스트

---

## 아키텍처 참고

```
User Query → Ticket Emoji 🎫 → Hybrid Search (MoEngage API + History RAG)
                                       ↓
                              Claude Response Generation
                                       ↓
                              Slack Thread Reply
                                       ↓
                        Complete Emoji ✅ → History Update → Better Future Answers
```
