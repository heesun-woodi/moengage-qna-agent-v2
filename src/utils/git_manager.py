"""Git operations for agent self-improvement."""

import subprocess
import difflib
from typing import Tuple, Optional

from src.utils.logger import logger


async def commit_and_push(file_path: str, commit_message: str) -> bool:
    """Commit and push changes to git repository.

    Args:
        file_path: Path to the file that was changed
        commit_message: Commit message

    Returns:
        True if successful, False otherwise
    """
    try:
        # Git add
        result = subprocess.run(
            ['git', 'add', file_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            logger.error(f"Git add failed: {result.stderr}")
            return False

        # Git commit
        full_message = f"{commit_message}\n\nCo-Authored-By: MoEngage Bot <bot@moengage.com>"
        result = subprocess.run(
            ['git', 'commit', '-m', full_message],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            logger.error(f"Git commit failed: {result.stderr}")
            return False

        # Git push
        result = subprocess.run(
            ['git', 'push'],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            logger.error(f"Git push failed: {result.stderr}")
            return False

        logger.info(f"Successfully committed and pushed: {file_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out")
        return False
    except Exception as e:
        logger.error(f"Git operation failed: {e}")
        return False


def get_diff_text(old_code: str, new_code: str, file_path: str) -> str:
    """Generate unified diff between old and new code.

    Args:
        old_code: Original code content
        new_code: New code content
        file_path: Path to the file (for diff header)

    Returns:
        Unified diff string
    """
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm=''
    )

    return ''.join(diff)


def validate_python_syntax(code: str) -> Tuple[bool, Optional[str]]:
    """Validate Python syntax.

    Args:
        code: Python code to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        compile(code, '<string>', 'exec')
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"
