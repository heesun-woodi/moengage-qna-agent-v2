"""LLM-based search query optimizer.

Analyzes user queries to generate optimal search keywords.
"""

import json
from typing import List, Optional

from anthropic import AsyncAnthropic

from config.settings import settings
from src.utils.logger import logger
from src.utils.retry import retry_claude_api


QUERY_OPTIMIZER_PROMPT = """당신은 MoEngage 고객 지원 검색 쿼리 최적화 전문가입니다.

사용자의 문의 내용을 분석하여 MoEngage Help Center에서 관련 문서를 찾기 위한 최적의 검색 쿼리를 생성합니다.

## MoEngage 주요 기능 및 용어
- User Attribute / User Property: 사용자 속성
- Event / Event Attribute: 사용자 행동 이벤트
- Segment / Segmentation: 사용자 그룹 분류
- Campaign: 마케팅 캠페인 (Push, Email, SMS, In-App)
- Flow / Journey: 자동화된 사용자 여정
- Analytics / Dashboard: 분석 및 대시보드
- SDK: 모바일/웹 SDK 연동
- API: Delete API, Data API, REST API, Push API, Get User API
- CSV Import: 데이터 일괄 업로드
- Push Notification: 푸시 알림
- In-App Message / On-Site Message (OSM): 인앱/온사이트 메시지
- Frequency Capping: 피로도 관리
- Delivery Controls: 발송 제어
- RFM Segmentation: RFM 세그먼테이션
- User Deletion / Delete User: 사용자 삭제
- Reachability: 사용자 도달 가능성

## 작업
1. 문의 핵심 파악
2. **질문 분해**: 문의에 여러 하위 질문이 있으면 분리 (최대 3개)
3. 각 하위 질문별로 **목적이 다른** 검색 쿼리 생성
4. 가장 관련 있는 MoEngage 기능 1-2개 식별

## 출력 형식 (JSON)
{
    "intent": "문의 의도 한 줄 요약 (한국어)",
    "sub_questions": [
        {"question": "하위 질문 1 (한국어)", "search_query": "짧은 영어 검색 쿼리 2-4 단어"},
        {"question": "하위 질문 2 (한국어)", "search_query": "다른 영어 검색 쿼리 2-4 단어"}
    ],
    "moengage_features": ["관련 MoEngage 기능 1-2개"],
    "search_keywords": ["핵심 한국어 키워드 2-3개"],
    "search_query": "대표 영어 검색 쿼리 (2-4 단어)"
}

## 질문 분해 규칙
- 단일 질문이면 sub_questions에 1개만 포함
- 복합 질문이면 최대 3개로 분해
- 각 하위 질문의 search_query는 **서로 다른 문서를 찾을 수 있도록** 다르게 생성
  - 예: "Push API response format" / "get user push permission" / "push reachability tracking"
- 하위 질문별 검색 목적을 고려:
  - 직접 답: 해당 기능/API의 공식 문서
  - 대체 경로: 다른 API나 기능으로 같은 목적 달성
  - 운영 가이드: 권장 방식, 아키텍처 가이드

## 중요
- search_query는 반드시 **2-4 단어**로 짧게!
- 예시: "user deletion", "push notification delay", "segment export"
- 긴 쿼리는 검색 정확도를 떨어뜨림
- 가장 핵심적인 MoEngage 용어만 사용"""


def get_query_optimizer_system_prompt() -> str:
    """Get query optimizer prompt from SkillStore (fallback to embedded default)."""
    try:
        from src.knowledge.skill_store import get_skill_store
        store = get_skill_store()
        skill = store.get_skill("query_optimizer")
        if skill:
            return skill
    except Exception:
        pass
    return QUERY_OPTIMIZER_PROMPT


class QueryOptimizer:
    """LLM-based query optimizer for search."""

    def __init__(self):
        # Add 30 second timeout for query optimization
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=30.0)

    @retry_claude_api
    async def optimize_query(self, user_query: str) -> dict:
        """Analyze user query and generate optimized search keywords.

        Args:
            user_query: Original user query in Korean

        Returns:
            Dictionary with intent, features, keywords, and search_query
        """
        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=get_query_optimizer_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": f"다음 문의를 분석하여 최적의 검색 키워드를 추출해주세요:\n\n{user_query}"
                }
            ]
        )

        content = response.content[0].text.strip()

        # Parse JSON response
        try:
            # Handle markdown code blocks
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            result = json.loads(content)
            logger.info(f"Query optimized: {result.get('search_query', '')}")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse query optimizer response: {e}")
            # Return fallback
            return {
                "intent": "파싱 실패",
                "moengage_features": [],
                "search_keywords": [],
                "search_query": user_query[:100]
            }

    async def get_search_query(self, user_query: str) -> str:
        """Get optimized search query string.

        Args:
            user_query: Original user query

        Returns:
            Optimized search query string
        """
        result = await self.optimize_query(user_query)
        return result.get("search_query", user_query[:100])

    async def get_search_keywords(self, user_query: str) -> List[str]:
        """Get list of search keywords.

        Args:
            user_query: Original user query

        Returns:
            List of search keywords
        """
        result = await self.optimize_query(user_query)
        return result.get("search_keywords", [])


# Global instance
_optimizer: Optional[QueryOptimizer] = None


def get_query_optimizer() -> QueryOptimizer:
    """Get or create the global query optimizer instance."""
    global _optimizer
    if _optimizer is None:
        _optimizer = QueryOptimizer()
    return _optimizer


async def optimize_search_query(user_query: str) -> str:
    """Convenience function to optimize a search query.

    Args:
        user_query: Original user query

    Returns:
        Optimized search query
    """
    optimizer = get_query_optimizer()
    return await optimizer.get_search_query(user_query)


async def analyze_query(user_query: str) -> dict:
    """Convenience function to analyze a query.

    Args:
        user_query: Original user query

    Returns:
        Analysis result dictionary
    """
    optimizer = get_query_optimizer()
    return await optimizer.optimize_query(user_query)
