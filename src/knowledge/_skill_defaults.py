"""Embedded default skill prompts.

These are used as fallback when skill MD files are not yet in storage.
On first run, they are saved to GCS/local storage and subsequently loaded from there.
"""

# Import current hardcoded prompts as defaults
# These will be replaced by storage-loaded versions once initialized
from src.llm.prompts import (
    SUPPORT_BOT_SYSTEM_PROMPT as _SUPPORT_BOT,
    CSM_CONVERSATIONAL_PROMPT as _CSM_CONV,
    THREAD_ANALYZER_SYSTEM_PROMPT as _THREAD_ANALYZER,
    GROUNDING_VALIDATION_PROMPT as _GROUNDING,
    PDF_PARSER_SYSTEM_PROMPT as _PDF_PARSER,
    LEARNING_EXTRACTION_SYSTEM_PROMPT as _LEARNING_EXTRACTION,
    CSM_AGENT_SYSTEM_PROMPT as _CSM_AGENT,
    WRITE_RESPONSE_SYSTEM_PROMPT as _WRITE_RESPONSE,
    RERANK_SYSTEM_PROMPT as _RERANK,
)
from src.llm.query_optimizer import QUERY_OPTIMIZER_PROMPT as _QUERY_OPTIMIZER


RETROSPECTIVE_DEFAULT = """당신은 기술 지원 봇의 자기 개선 전문가입니다.

## 역할
최근 학습 데이터를 분석하여 에이전트의 스킬을 업그레이드할 개선사항을 도출합니다.

## 분석 관점
1. **패턴 식별**: 반복되는 실수나 개선 포인트
2. **검색 전략**: 더 나은 검색 키워드/문서 조합 패턴
3. **답변 품질**: 자주 누락되는 정보, 구조 개선점
4. **문의 해석**: 특정 주제에서 반복되는 해석 오류

## 입력
- 최근 학습 엔트리 목록 (query_lesson, search_lesson, response_lesson)
- 각 대상 스킬 파일의 현재 내용

## 출력 형식 (JSON만 출력)
```json
{
  "upgrades": {
    "write_response": "추가할 지침 (없으면 빈 문자열)",
    "query_optimizer": "추가할 지침 (없으면 빈 문자열)",
    "rerank": "추가할 지침 (없으면 빈 문자열)"
  },
  "summary": "전체 회고 요약 1-2줄"
}
```

## 규칙
- 기존 스킬에 이미 있는 내용은 중복 생성 금지
- 일반화 가능한 교훈만 추출 (특정 케이스에만 해당되는 것 제외)
- 각 개선사항은 프롬프트에 직접 반영 가능한 구체적 지침 형태
- 개선사항이 없는 스킬은 빈 문자열
- 반드시 유효한 JSON만 출력"""


EMBEDDED_DEFAULTS = {
    "support_bot": _SUPPORT_BOT,
    "csm_conversational": _CSM_CONV,
    "thread_analyzer": _THREAD_ANALYZER,
    "grounding_validation": _GROUNDING,
    "pdf_parser": _PDF_PARSER,
    "learning_extraction": _LEARNING_EXTRACTION,
    "csm_agent": _CSM_AGENT,
    "write_response": _WRITE_RESPONSE,
    "rerank": _RERANK,
    "query_optimizer": _QUERY_OPTIMIZER,
    "retrospective": RETROSPECTIVE_DEFAULT,
}
