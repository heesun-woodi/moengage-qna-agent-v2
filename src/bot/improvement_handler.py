"""Agent self-improvement handler."""

from typing import Dict, Any

from slack_sdk.web.async_client import AsyncWebClient

from config.settings import settings
from src.llm.claude_client import get_claude_client
from src.utils.git_manager import commit_and_push, get_diff_text, validate_python_syntax
from src.utils.logger import logger


# Improvement system prompt
IMPROVEMENT_SYSTEM_PROMPT = """당신은 Python 코드 개선 전문가입니다.

## 작업
사용자의 요청에 따라 기존 Python 코드를 개선합니다.

## 규칙
1. 기존 코드의 구조와 스타일을 유지합니다
2. 변수명, 함수명은 그대로 유지합니다
3. import 문은 수정하지 않습니다
4. 요청된 부분만 정확히 수정합니다
5. 전체 파일 내용을 반환합니다

## 출력 형식
수정된 전체 Python 파일 내용만 반환합니다.
마크다운 코드 블록이나 설명 없이 순수 Python 코드만 반환합니다.
"""

# File keyword mapping
ALLOWED_FILES = {
    "프롬프트": "src/llm/prompts.py",
    "prompt": "src/llm/prompts.py",
    "prompts": "src/llm/prompts.py",
    "포맷터": "src/bot/formatters.py",
    "formatter": "src/bot/formatters.py",
    "formatters": "src/bot/formatters.py",
    "쿼리최적화": "src/llm/query_optimizer.py",
    "query_optimizer": "src/llm/query_optimizer.py",
}

# Pending improvements storage (in production, use Redis)
pending_improvements: Dict[str, Dict[str, Any]] = {}


async def handle_improve_command(
    client: AsyncWebClient,
    event: Dict[str, Any],
    user_query: str
):
    """Handle /improve command for agent self-improvement.

    Args:
        client: Slack client
        event: The Slack event
        user_query: The user's query (with /improve prefix removed)
    """
    channel = event.get("channel", "")
    message_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts", message_ts)
    user = event.get("user", "")

    # Parse command: /improve [target] [request]
    query = user_query.replace('/improve', '').replace('/개선', '').strip()

    if not query:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                "**사용법**: `/improve [대상] [요청사항]`\n\n"
                "**대상**:\n"
                "- 프롬프트 (prompts.py)\n"
                "- 포맷터 (formatters.py)\n"
                "- 쿼리최적화 (query_optimizer.py)\n\n"
                "**예시**:\n"
                "- `/improve 프롬프트 응답 형식을 더 간결하게 바꿔줘`\n"
                "- `/improve 포맷터 에러 메시지를 더 친절하게 바꿔줘`"
            )
        )
        return

    # Identify target file
    target_file = None
    for keyword, filepath in ALLOWED_FILES.items():
        if keyword in query.lower():
            target_file = filepath
            break

    if not target_file:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                "수정 가능한 대상을 명시해주세요.\n\n"
                "**지원 대상**:\n"
                "- 프롬프트 (prompts.py)\n"
                "- 포맷터 (formatters.py)\n"
                "- 쿼리최적화 (query_optimizer.py)"
            )
        )
        return

    # Check if improvement feature is enabled
    if not settings.improvement_enabled:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="개선 기능이 비활성화되어 있습니다."
        )
        return

    # Send processing message
    processing_msg = await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"`{target_file}` 개선안을 생성 중입니다..."
    )

    try:
        # Read current file content
        with open(target_file, 'r', encoding='utf-8') as f:
            current_code = f.read()

        # Generate improvement using Claude
        claude_client = get_claude_client()
        improved_code = await claude_client.generate_improvement(
            current_code=current_code,
            improvement_request=query,
            file_path=target_file
        )

        if not improved_code or improved_code == current_code:
            await client.chat_update(
                channel=channel,
                ts=processing_msg["ts"],
                text="변경할 내용이 없거나 개선안 생성에 실패했습니다."
            )
            return

        # Validate Python syntax
        is_valid, error = validate_python_syntax(improved_code)
        if not is_valid:
            await client.chat_update(
                channel=channel,
                ts=processing_msg["ts"],
                text=(
                    f"생성된 코드에 문법 오류가 있습니다:\n"
                    f"```\n{error}\n```\n\n"
                    "다시 시도해주세요."
                )
            )
            return

        # Generate diff
        diff_text = get_diff_text(current_code, improved_code, target_file)

        # Truncate diff if too long
        diff_display = diff_text[:2000]
        if len(diff_text) > 2000:
            diff_display += "\n... (diff가 너무 깁니다)"

        # Show preview with diff
        preview_text = (
            f"**개선안 미리보기** (`{target_file}`)\n\n"
            f"```diff\n{diff_display}\n```\n\n"
            f":{settings.complete_emoji}: 승인하려면 이 메시지에 :{settings.complete_emoji}: 이모지를 추가하세요.\n"
            f":x: 취소하려면 무시하세요."
        )

        # Update message with preview
        preview_result = await client.chat_update(
            channel=channel,
            ts=processing_msg["ts"],
            text=preview_text
        )

        # Store pending improvement
        key = f"{channel}:{preview_result['ts']}"
        pending_improvements[key] = {
            "file_path": target_file,
            "improved_code": improved_code,
            "user": user,
            "request": query
        }

        logger.info(f"Improvement preview posted for {target_file}, key: {key}")

    except Exception as e:
        logger.error(f"Error in improve command: {e}", exc_info=True)
        await client.chat_update(
            channel=channel,
            ts=processing_msg["ts"],
            text=f"오류가 발생했습니다: {str(e)[:100]}"
        )


async def handle_improvement_approval(
    client: AsyncWebClient,
    channel: str,
    message_ts: str,
    user: str
) -> bool:
    """Handle approval of pending improvement.

    Args:
        client: Slack client
        channel: Channel ID
        message_ts: Message timestamp
        user: User who approved

    Returns:
        True if handled, False if not an improvement approval
    """
    key = f"{channel}:{message_ts}"

    if key not in pending_improvements:
        return False

    improvement = pending_improvements.pop(key)
    file_path = improvement["file_path"]
    improved_code = improvement["improved_code"]
    request = improvement["request"]

    try:
        # Write file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(improved_code)

        # Git commit & push
        commit_message = f"Improve {file_path}: {request[:50]}"
        success = await commit_and_push(file_path, commit_message)

        if success:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=message_ts,
                text=(
                    f"**개선 완료!**\n\n"
                    f"- 파일: `{file_path}`\n"
                    f"- 커밋 메시지: {commit_message}\n\n"
                    "Railway에서 자동 배포가 진행됩니다."
                )
            )
        else:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=message_ts,
                text=(
                    "파일은 수정되었으나 git push에 실패했습니다.\n"
                    "수동으로 확인해주세요."
                )
            )

        return True

    except Exception as e:
        logger.error(f"Error applying improvement: {e}", exc_info=True)
        await client.chat_postMessage(
            channel=channel,
            thread_ts=message_ts,
            text=f"개선 적용 중 오류 발생: {str(e)[:100]}"
        )
        return True
