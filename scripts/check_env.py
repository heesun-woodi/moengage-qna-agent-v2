#!/usr/bin/env python3
"""환경 설정 검증 스크립트.

.env 파일의 필수 환경 변수를 검증합니다.
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load .env file
load_dotenv(project_root / ".env")


def check_env():
    """환경 변수 검증."""
    print("=" * 60)
    print("MoEngage Q&A Agent - 환경 설정 검증")
    print("=" * 60)
    print()

    errors = []
    warnings = []

    # Required variables
    required = {
        "SLACK_BOT_TOKEN": {
            "prefix": "xoxb-",
            "description": "Slack Bot Token",
            "hint": "Slack API → OAuth & Permissions → Bot User OAuth Token",
        },
        "SLACK_APP_TOKEN": {
            "prefix": "xapp-",
            "description": "Slack App Token (Socket Mode)",
            "hint": "Slack API → Basic Information → App-Level Tokens",
        },
        "SLACK_SIGNING_SECRET": {
            "prefix": None,
            "description": "Slack Signing Secret",
            "hint": "Slack API → Basic Information → App Credentials",
        },
        "ANTHROPIC_API_KEY": {
            "prefix": "sk-ant-",
            "description": "Anthropic API Key",
            "hint": "https://console.anthropic.com/ → API Keys",
        },
    }

    for var_name, config in required.items():
        value = os.getenv(var_name, "")
        prefix = config["prefix"]
        description = config["description"]
        hint = config["hint"]

        print(f"[{description}]")
        print(f"  변수: {var_name}")

        if not value:
            print(f"  상태: ❌ 설정되지 않음")
            print(f"  힌트: {hint}")
            errors.append(f"{var_name} is not set")
        elif prefix and not value.startswith(prefix):
            print(f"  상태: ⚠️  형식 오류 ('{prefix}...'로 시작해야 함)")
            print(f"  힌트: {hint}")
            errors.append(f"{var_name} has invalid format")
        elif value in [f"{prefix}your-bot-token", f"{prefix}your-app-token",
                       f"{prefix}your-api-key", "your-signing-secret"]:
            print(f"  상태: ⚠️  예시 값 사용 중 (실제 값으로 변경 필요)")
            print(f"  힌트: {hint}")
            warnings.append(f"{var_name} is using example value")
        else:
            masked = value[:8] + "..." + value[-4:] if len(value) > 16 else value[:4] + "..."
            print(f"  상태: ✅ 설정됨 ({masked})")

        print()

    # Optional variables
    print("-" * 60)
    print("선택적 설정:")
    print("-" * 60)
    print()

    optional = {
        "REDIS_URL": os.getenv("REDIS_URL", "redis://localhost:6379"),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        "TICKET_EMOJI": os.getenv("TICKET_EMOJI", "ticket"),
        "COMPLETE_EMOJI": os.getenv("COMPLETE_EMOJI", "white_check_mark"),
    }

    for var_name, value in optional.items():
        print(f"  {var_name}: {value}")

    print()
    print("=" * 60)

    # Summary
    if errors:
        print(f"❌ 오류 {len(errors)}개 발견:")
        for err in errors:
            print(f"   - {err}")
        print()
        print("위 오류를 해결한 후 다시 실행하세요.")
        print("설정 가이드: docs/SLACK_TEST_GUIDE.md")
        return False
    elif warnings:
        print(f"⚠️  경고 {len(warnings)}개:")
        for warn in warnings:
            print(f"   - {warn}")
        print()
        print("예시 값을 실제 값으로 변경하세요.")
        return False
    else:
        print("✅ 모든 필수 환경 변수가 올바르게 설정되었습니다!")
        print()
        print("다음 명령으로 봇을 실행하세요:")
        print("  python main.py")
        return True


if __name__ == "__main__":
    success = check_env()
    sys.exit(0 if success else 1)
