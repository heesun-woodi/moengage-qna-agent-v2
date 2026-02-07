"""Claude API client for MoEngage Q&A Agent."""

from typing import Optional, AsyncGenerator, List, Dict
import json
import re

import anthropic


def extract_json_safely(text: str) -> dict:
    """Robust JSON extraction from LLM response.

    Tries multiple patterns to extract JSON from text that may include
    markdown code blocks or other formatting.

    Args:
        text: Raw text containing JSON

    Returns:
        Parsed dictionary or empty dict on failure
    """
    patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # ```json ... ```
        r'```\s*([\s\S]*?)\s*```',       # ``` ... ```
        r'\{[\s\S]*\}',                   # Raw { ... }
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                json_str = match.group(1) if '```' in pattern else match.group(0)
                return json.loads(json_str.strip())
            except (json.JSONDecodeError, IndexError):
                continue

    # Last resort: try parsing the entire text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return {}

from config.settings import settings
from src.utils.logger import logger
from src.utils.retry import retry_claude_api, claude_circuit
from src.llm.prompts import (
    SUPPORT_BOT_SYSTEM_PROMPT,
    THREAD_ANALYZER_SYSTEM_PROMPT,
    CSM_CONVERSATIONAL_PROMPT,
    LEARNING_EXTRACTION_SYSTEM_PROMPT,
    CSM_REPLY_ANALYSIS_SYSTEM_PROMPT,
    get_support_prompt,
    get_thread_analysis_prompt,
    get_learning_extraction_prompt,
    get_csm_reply_analysis_prompt,
)


class ClaudeClient:
    """Anthropic Claude API client."""

    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    MAX_TOKENS = 2048

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude client.

        Args:
            api_key: Anthropic API key (uses settings if not provided)
        """
        self.api_key = api_key or settings.anthropic_api_key
        # Add 60 second timeout to prevent hanging requests
        self.client = anthropic.Anthropic(api_key=self.api_key, timeout=60.0)
        self.async_client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=60.0)

    @retry_claude_api
    @claude_circuit
    async def generate_support_response(
        self,
        context: str,
        user_query: str,
        model: Optional[str] = None
    ) -> str:
        """Generate a support response based on context and query.

        Args:
            context: Retrieved documents as context
            user_query: User's question
            model: Model to use (default: claude-sonnet-4-20250514)

        Returns:
            Generated response
        """
        model = model or self.DEFAULT_MODEL
        prompt = get_support_prompt(context, user_query)

        logger.debug(f"Generating support response for: {user_query[:100]}...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=SUPPORT_BOT_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result = response.content[0].text
        logger.info(f"Generated response ({len(result)} chars)")

        return result

    @retry_claude_api
    @claude_circuit
    async def analyze_thread(
        self,
        messages: List[Dict],
        model: Optional[str] = None
    ) -> dict:
        """Analyze a support thread and extract structured information.

        Args:
            messages: List of thread messages with 'role' and 'text' keys
            model: Model to use

        Returns:
            Structured analysis as dictionary
        """
        model = model or self.DEFAULT_MODEL
        prompt = get_thread_analysis_prompt(messages)

        logger.debug(f"Analyzing thread with {len(messages)} messages...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=THREAD_ANALYZER_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result_text = response.content[0].text

        # Parse JSON from response using robust extraction
        result = extract_json_safely(result_text)
        if result:
            logger.info(f"Thread analysis complete: {result.get('title', 'N/A')}")
            return result
        else:
            logger.error("Failed to parse thread analysis JSON")
            return {
                "title": "분석 실패",
                "category": "기타",
                "query_summary": "JSON 파싱 오류",
                "cause": "",
                "solution": result_text,
                "is_resolved": False,
                "confidence": 0.0
            }

    async def generate_support_response_stream(
        self,
        context: str,
        user_query: str,
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Generate a support response with streaming.

        Args:
            context: Retrieved documents as context
            user_query: User's question
            model: Model to use

        Yields:
            Response text chunks
        """
        model = model or self.DEFAULT_MODEL
        prompt = get_support_prompt(context, user_query)

        async with self.async_client.messages.stream(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=SUPPORT_BOT_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        ) as stream:
            async for text in stream.text_stream:
                yield text

    @retry_claude_api
    @claude_circuit
    async def generate_csm_response(
        self,
        context: str,
        user_query: str,
        model: Optional[str] = None
    ) -> str:
        """Generate conversational response for CSM channel.

        Args:
            context: Retrieved documents as context
            user_query: User's question
            model: Model to use

        Returns:
            Generated response in conversational style
        """
        model = model or self.DEFAULT_MODEL

        prompt = f"""## 컨텍스트 (검색된 문서)
{context}

## 질문
{user_query}

위 컨텍스트를 기반으로 질문에 답변해주세요. 컨텍스트에 없는 내용은 만들어내지 마세요."""

        logger.debug(f"Generating CSM response for: {user_query[:100]}...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=CSM_CONVERSATIONAL_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result = response.content[0].text
        logger.info(f"Generated CSM response ({len(result)} chars)")

        return result

    @retry_claude_api
    @claude_circuit
    async def extract_learning_points(
        self,
        original_query: str,
        initial_response: str,
        csm_feedback: list,
        improved_responses: list,
        final_response: str,
        model: Optional[str] = None
    ) -> dict:
        """Extract learning points from a CSM-improved conversation.

        Args:
            original_query: Original customer query
            initial_response: Bot's initial response
            csm_feedback: List of CSM feedback messages
            improved_responses: List of improved responses
            final_response: Final approved response
            model: Model to use

        Returns:
            Dictionary with query_lesson, search_lesson, response_lesson, category
        """
        model = model or self.DEFAULT_MODEL
        prompt = get_learning_extraction_prompt(
            original_query,
            initial_response,
            csm_feedback,
            improved_responses,
            final_response
        )

        logger.debug("Extracting learning points...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=1024,
            system=LEARNING_EXTRACTION_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result_text = response.content[0].text

        # Parse JSON from response using robust extraction
        result = extract_json_safely(result_text)
        if result:
            logger.info(f"Extracted learning points: {list(result.keys())}")
            return result
        else:
            logger.error("Failed to parse learning points JSON")
            return {
                "query_lesson": "",
                "search_lesson": "",
                "response_lesson": "",
                "category": "기타"
            }

    @retry_claude_api
    @claude_circuit
    async def analyze_csm_reply(
        self,
        csm_message: str,
        conversation_context: str = "",
        model: Optional[str] = None
    ) -> dict:
        """Analyze CSM's reply to understand their intent.

        Args:
            csm_message: CSM's feedback message
            conversation_context: Previous conversation for context
            model: Model to use

        Returns:
            Dictionary with intent, keywords, context, instruction
        """
        model = model or self.DEFAULT_MODEL
        prompt = get_csm_reply_analysis_prompt(csm_message, conversation_context)

        logger.debug(f"Analyzing CSM reply: {csm_message[:100]}...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=512,
            system=CSM_REPLY_ANALYSIS_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result_text = response.content[0].text

        # Parse JSON from response using robust extraction
        result = extract_json_safely(result_text)
        if result:
            logger.info(f"CSM reply intent: {result.get('intent', 'unknown')}")
            return result
        else:
            logger.error("Failed to parse CSM reply analysis JSON")
            return {
                "intent": "other",
                "keywords": [],
                "context": "",
                "instruction": csm_message
            }

    @retry_claude_api
    @claude_circuit
    async def generate_improved_response(
        self,
        context: str,
        original_query: str,
        previous_response: str,
        csm_feedback: str,
        additional_context: str = "",
        model: Optional[str] = None
    ) -> str:
        """Generate an improved response based on CSM feedback.

        Args:
            context: Retrieved documents as context
            original_query: Original customer query
            previous_response: Previous bot response
            csm_feedback: CSM's feedback/instruction
            additional_context: Any additional context from CSM
            model: Model to use

        Returns:
            Improved response
        """
        model = model or self.DEFAULT_MODEL

        prompt = f"""## 컨텍스트 (검색된 문서)
{context}

## 원본 문의
{original_query}

## 이전 답변
{previous_response}

## CSM 피드백
{csm_feedback}

{f"## 추가 맥락{chr(10)}{additional_context}" if additional_context else ""}

## 지시사항
CSM의 피드백을 반영하여 개선된 답변을 작성해주세요.
- 피드백에서 지적한 부분을 수정하세요
- 추가 요청된 정보를 포함하세요
- 컨텍스트에 없는 내용은 만들어내지 마세요"""

        logger.debug(f"Generating improved response based on feedback: {csm_feedback[:100]}...")

        response = await self.async_client.messages.create(
            model=model,
            max_tokens=self.MAX_TOKENS,
            system=SUPPORT_BOT_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result = response.content[0].text
        logger.info(f"Generated improved response ({len(result)} chars)")

        return result


# Global client instance
_claude_client: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    """Get or create the global Claude client instance."""
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient()
    return _claude_client


async def generate_response(context: str, query: str) -> str:
    """Convenience function to generate a support response.

    Args:
        context: Retrieved documents
        query: User query

    Returns:
        Generated response
    """
    client = get_claude_client()
    return await client.generate_support_response(context, query)


async def analyze_thread(messages: List[Dict]) -> dict:
    """Convenience function to analyze a thread.

    Args:
        messages: Thread messages

    Returns:
        Analysis result
    """
    client = get_claude_client()
    return await client.analyze_thread(messages)


async def generate_csm_response(context: str, query: str) -> str:
    """Convenience function to generate a CSM conversational response.

    Args:
        context: Retrieved documents
        query: User query

    Returns:
        Generated conversational response
    """
    client = get_claude_client()
    return await client.generate_csm_response(context, query)


async def extract_learning_points(
    original_query: str,
    initial_response: str,
    csm_feedback: list,
    improved_responses: list,
    final_response: str
) -> dict:
    """Convenience function to extract learning points.

    Args:
        original_query: Original customer query
        initial_response: Bot's initial response
        csm_feedback: List of CSM feedback messages
        improved_responses: List of improved responses
        final_response: Final approved response

    Returns:
        Dictionary with learning points
    """
    client = get_claude_client()
    return await client.extract_learning_points(
        original_query,
        initial_response,
        csm_feedback,
        improved_responses,
        final_response
    )


async def analyze_csm_reply(csm_message: str, conversation_context: str = "") -> dict:
    """Convenience function to analyze CSM reply.

    Args:
        csm_message: CSM's feedback message
        conversation_context: Previous conversation context

    Returns:
        Analysis result
    """
    client = get_claude_client()
    return await client.analyze_csm_reply(csm_message, conversation_context)


async def extract_learning_from_thread(thread_messages: list) -> dict:
    """Extract learning points from a completed thread conversation.

    Analyzes the thread messages to identify:
    - Original query and how it was interpreted
    - What search/information gathering was done
    - How the response was formulated
    - Key takeaways for future similar queries

    Args:
        thread_messages: List of Slack message dicts from the thread

    Returns:
        Dictionary with query_lesson, search_lesson, response_lesson, category
    """
    # Parse thread messages to extract conversation flow
    original_query = ""
    bot_responses = []
    user_messages = []

    for msg in thread_messages:
        text = msg.get("text", "")
        if not text:
            continue

        if msg.get("bot_id"):
            bot_responses.append(text[:1000])  # Limit length
        else:
            if not original_query:
                original_query = text
            else:
                user_messages.append(text[:500])

    if not original_query or not bot_responses:
        logger.warning("Thread has insufficient content for learning extraction")
        return {}

    # Build conversation summary for learning extraction
    initial_response = bot_responses[0] if bot_responses else ""
    final_response = bot_responses[-1] if bot_responses else ""

    # Use the existing extract_learning_points function
    return await extract_learning_points(
        original_query=original_query,
        initial_response=initial_response,
        csm_feedback=user_messages,
        improved_responses=bot_responses[1:] if len(bot_responses) > 1 else [],
        final_response=final_response
    )


async def generate_improved_response(
    context: str,
    original_query: str,
    previous_response: str,
    csm_feedback: str,
    additional_context: str = ""
) -> str:
    """Convenience function to generate improved response.

    Args:
        context: Retrieved documents
        original_query: Original customer query
        previous_response: Previous bot response
        csm_feedback: CSM's feedback
        additional_context: Additional context from CSM

    Returns:
        Improved response
    """
    client = get_claude_client()
    return await client.generate_improved_response(
        context,
        original_query,
        previous_response,
        csm_feedback,
        additional_context
    )
