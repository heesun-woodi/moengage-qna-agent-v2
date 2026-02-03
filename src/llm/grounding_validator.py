"""Grounding validator to prevent hallucinations."""

from typing import Dict, Any, List, Tuple
import json

from src.llm.claude_client import get_claude_client
from src.llm.prompts import GROUNDING_VALIDATION_PROMPT, get_grounding_validation_prompt
from src.utils.logger import logger


async def validate_grounding(
    context: str,
    answer: str,
    threshold: float = 0.7
) -> Dict[str, Any]:
    """Validate that an answer is grounded in the provided context.

    Args:
        context: The source documents/context
        answer: The generated answer to validate
        threshold: Confidence threshold for validation

    Returns:
        Validation result with is_grounded, confidence, issues, suggestions
    """
    client = get_claude_client()
    prompt = get_grounding_validation_prompt(context, answer)

    try:
        response = await client.async_client.messages.create(
            model=client.DEFAULT_MODEL,
            max_tokens=1024,
            system=GROUNDING_VALIDATION_PROMPT,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        result_text = response.content[0].text

        # Parse JSON
        if "```json" in result_text:
            json_str = result_text.split("```json")[1].split("```")[0]
        elif "```" in result_text:
            json_str = result_text.split("```")[1].split("```")[0]
        else:
            json_str = result_text

        result = json.loads(json_str.strip())

        # Apply threshold
        result["passes_threshold"] = result.get("confidence", 0) >= threshold

        logger.info(
            f"Grounding validation: grounded={result.get('is_grounded')}, "
            f"confidence={result.get('confidence')}"
        )

        return result

    except Exception as e:
        logger.error(f"Grounding validation failed: {e}")
        return {
            "is_grounded": True,  # Fail open
            "confidence": 0.5,
            "issues": [f"Validation error: {str(e)}"],
            "suggestions": [],
            "passes_threshold": False
        }


def quick_grounding_check(context: str, answer: str) -> List[str]:
    """Quick heuristic check for obvious hallucinations.

    Args:
        context: Source context
        answer: Generated answer

    Returns:
        List of potential issues found
    """
    issues = []
    context_lower = context.lower()
    answer_lower = answer.lower()

    # Check for menu paths that aren't in context
    menu_indicators = ["설정 >", "메뉴 >", "settings >", "→", ">>"]
    for indicator in menu_indicators:
        if indicator in answer_lower:
            # Extract the menu path
            start = answer_lower.find(indicator)
            end = min(start + 100, len(answer_lower))
            menu_path = answer[start:end].split("\n")[0]

            # Check if it exists in context
            if menu_path.lower() not in context_lower:
                issues.append(f"Menu path not found in context: {menu_path}")

    # Check for specific version numbers not in context
    import re
    version_pattern = r'\d+\.\d+\.\d+'
    answer_versions = set(re.findall(version_pattern, answer))
    context_versions = set(re.findall(version_pattern, context))

    for version in answer_versions:
        if version not in context_versions:
            issues.append(f"Version number not found in context: {version}")

    # Check for URLs not in context
    url_pattern = r'https?://[^\s\)\]>]+'
    answer_urls = set(re.findall(url_pattern, answer))
    context_urls = set(re.findall(url_pattern, context))

    for url in answer_urls:
        # Allow moengage.com URLs as they're expected
        if "moengage.com" not in url and url not in context_urls:
            issues.append(f"URL not found in context: {url}")

    return issues


async def validate_and_filter_response(
    context: str,
    answer: str,
    strict: bool = False
) -> Tuple[str, bool]:
    """Validate response and potentially filter/modify it.

    Args:
        context: Source context
        answer: Generated answer
        strict: If True, return fallback message on any grounding issue

    Returns:
        Tuple of (final_answer, was_modified)
    """
    # Quick heuristic check first
    quick_issues = quick_grounding_check(context, answer)

    if quick_issues:
        logger.warning(f"Quick grounding check found issues: {quick_issues}")

        if strict:
            return (
                "문서에서 관련 내용을 찾을 수 없어 정확한 답변을 드리기 어렵습니다. "
                "MoEngage 대시보드를 통해 서포트 티켓을 생성해 주세요.",
                True
            )

    # Full validation for non-strict mode or if quick check passed
    if not strict and not quick_issues:
        validation = await validate_grounding(context, answer)

        if not validation.get("is_grounded", True):
            logger.warning(
                f"Full grounding validation failed: {validation.get('issues')}"
            )

            # Add disclaimer to answer
            disclaimer = (
                "\n\n⚠️ *참고: 일부 내용은 추가 확인이 필요할 수 있습니다.*"
            )
            return answer + disclaimer, True

    return answer, False
