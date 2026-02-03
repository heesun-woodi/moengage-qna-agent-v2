"""Test script for search functionality."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge.moengage_api import search_moengage
from src.knowledge.history_rag import search_history, get_history_rag
from src.knowledge.hybrid_searcher import hybrid_search, format_context_for_llm
from src.utils.logger import logger


async def test_moengage_search():
    """Test MoEngage API search."""
    print("\n" + "="*60)
    print("Testing MoEngage API Search")
    print("="*60)

    test_queries = [
        "push notification",
        "segment creation",
        "캠페인 설정",
        "SDK integration"
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        print("-" * 40)

        try:
            results = await search_moengage(query, top_k=3)

            if results:
                for i, r in enumerate(results, 1):
                    print(f"  {i}. {r.title}")
                    print(f"     URL: {r.url}")
                    print(f"     Snippet: {r.snippet[:100]}...")
            else:
                print("  No results found.")

        except Exception as e:
            print(f"  Error: {e}")


async def test_history_search():
    """Test History RAG search."""
    print("\n" + "="*60)
    print("Testing History RAG Search")
    print("="*60)

    rag = get_history_rag()
    count = rag.count()
    print(f"History entries: {count}")

    if count == 0:
        print("No history entries. Run scripts/init_history.py first.")
        return

    test_queries = [
        "푸시 발송 실패",
        "세그먼트 카운트",
        "카카오 연동"
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        print("-" * 40)

        try:
            results = await search_history(query, top_k=3)

            if results:
                for i, r in enumerate(results, 1):
                    print(f"  {i}. {r.entry.title} (score: {r.score:.2f})")
                    print(f"     Category: {r.entry.category}")
            else:
                print("  No results found.")

        except Exception as e:
            print(f"  Error: {e}")


async def test_hybrid_search():
    """Test hybrid search."""
    print("\n" + "="*60)
    print("Testing Hybrid Search")
    print("="*60)

    query = "푸시 알림 발송이 안됩니다"
    print(f"\nQuery: '{query}'")
    print("-" * 40)

    try:
        results = await hybrid_search(query)

        print(f"Total results: {len(results)}")
        for i, r in enumerate(results, 1):
            source_icon = "📚" if r.source == "moengage_docs" else "📝"
            print(f"  {i}. {source_icon} {r.title}")
            print(f"     Source: {r.source}, Score: {r.score:.2f}")
            print(f"     URL: {r.url}")

        # Format as context
        print("\n" + "-" * 40)
        print("Formatted Context for LLM:")
        print("-" * 40)
        context = format_context_for_llm(results[:3])
        print(context[:1000] + "..." if len(context) > 1000 else context)

    except Exception as e:
        print(f"Error: {e}")


async def main():
    """Run all tests."""
    print("MoEngage Q&A Agent - Search Test")
    print("=" * 60)

    await test_moengage_search()
    await test_history_search()
    await test_hybrid_search()

    print("\n" + "="*60)
    print("Tests completed!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
