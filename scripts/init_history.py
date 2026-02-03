"""Initialize the History RAG database with sample entries."""

import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.utils.logger import logger


def add_sample_entries():
    """Add sample entries to the history database."""
    rag = get_history_rag()

    sample_entries = [
        HistoryEntry(
            id="",
            title="푸시 알림 발송 실패 - FCM 토큰 만료",
            customer="진에어",
            category="캠페인 세팅",
            query_summary="""
문의: 푸시 캠페인 발송 시 일부 사용자에게 전달되지 않는 현상 발생.
증상: 발송 완료로 표시되나 실제 수신율이 70% 미만.
            """.strip(),
            solution="""
원인: FCM 토큰 만료로 인한 발송 실패.
해결:
1. SDK 버전 업데이트 (최신 버전으로)
2. 토큰 갱신 로직 추가
3. MoEngage 대시보드에서 토큰 상태 확인

참고: Settings > Push Configuration > Token Status에서 확인 가능
            """.strip(),
            created_at=datetime.now().isoformat(),
            url=""
        ),
        HistoryEntry(
            id="",
            title="세그먼트 카운트 불일치 문제",
            customer="진에어",
            category="유저 세그먼테이션",
            query_summary="""
문의: 세그먼트 생성 후 예상 사용자 수와 실제 타겟 수가 다름.
증상: 필터 조건에 맞는 사용자가 100명인데 세그먼트에는 80명만 표시.
            """.strip(),
            solution="""
원인: 세그먼트 계산 시점과 실제 캠페인 발송 시점의 차이.
해결:
1. 세그먼트 새로고침 (Refresh) 실행
2. 실시간 세그먼트(Live Segment) 사용 권장
3. 캠페인 발송 직전 세그먼트 재계산 옵션 활성화

참고: Segment > Settings > Auto-refresh 옵션 확인
            """.strip(),
            created_at=datetime.now().isoformat(),
            url=""
        ),
        HistoryEntry(
            id="",
            title="카카오 알림톡 연동 오류",
            customer="진에어",
            category="채널 세팅 및 연동",
            query_summary="""
문의: 카카오 알림톡 발송 시 '템플릿 승인 오류' 메시지 발생.
증상: 템플릿이 카카오 비즈니스에서 승인 완료 상태인데 MoEngage에서 오류.
            """.strip(),
            solution="""
원인: MoEngage와 카카오 비즈니스 간 템플릿 동기화 지연.
해결:
1. MoEngage 대시보드에서 템플릿 다시 불러오기
2. 템플릿 코드 정확히 일치하는지 확인
3. 24시간 후 재시도 (동기화 주기)

참고: Channel > KakaoTalk > Template Sync 버튼 사용
            """.strip(),
            created_at=datetime.now().isoformat(),
            url=""
        )
    ]

    for entry in sample_entries:
        entry_id = rag.add_entry(entry)
        logger.info(f"Added entry: {entry.title} (ID: {entry_id})")

    logger.info(f"Total entries in history: {rag.count()}")


def main():
    """Main function."""
    logger.info("Initializing History RAG database...")

    rag = get_history_rag()
    current_count = rag.count()

    if current_count > 0:
        logger.info(f"History already has {current_count} entries.")
        response = input("Add sample entries anyway? (y/N): ")
        if response.lower() != 'y':
            logger.info("Skipping sample entries.")
            return

    add_sample_entries()
    logger.info("History initialization complete.")


if __name__ == "__main__":
    main()
