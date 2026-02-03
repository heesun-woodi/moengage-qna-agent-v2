#!/usr/bin/env python3
"""Migrate history entries from v1 Railway to v2 Railway.

Usage:
    python scripts/migrate_from_v1.py \
        --v1-url https://moengage-qna-agent-production.up.railway.app \
        --v1-key moengage-history-2024 \
        --v2-url https://moengage-qna-agent-v2-production.up.railway.app \
        --v2-key moengage-v2-api-2024

Or use defaults from environment:
    python scripts/migrate_from_v1.py
"""

import argparse
import asyncio
import sys
from typing import Dict, Any, List, Optional

import aiohttp


async def fetch_v1_entries(
    base_url: str,
    api_key: str
) -> Optional[List[Dict[str, Any]]]:
    """Fetch all history entries from v1 API.

    Args:
        base_url: v1 API base URL
        api_key: v1 API key

    Returns:
        List of entry dicts or None if failed
    """
    url = f"{base_url.rstrip('/')}/api/history/export"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"Fetching entries from v1: {url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    entries = data.get("entries", [])
                    print(f"Found {len(entries)} entries in v1")
                    return entries
                elif response.status == 404:
                    print("Error: /api/history/export endpoint not found.")
                    print("Please deploy v1 with the export endpoint first.")
                    return None
                else:
                    error = await response.text()
                    print(f"Error fetching v1 entries: {response.status} - {error}")
                    return None
    except aiohttp.ClientError as e:
        print(f"Connection error to v1: {e}")
        return None


async def fetch_v2_entry_ids(
    base_url: str,
    api_key: str
) -> set:
    """Fetch existing entry IDs from v2 API.

    Args:
        base_url: v2 API base URL
        api_key: v2 API key

    Returns:
        Set of existing entry IDs
    """
    url = f"{base_url.rstrip('/')}/api/history"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    entries = data.get("entries", [])
                    return {e["id"] for e in entries}
                else:
                    print(f"Warning: Could not fetch v2 entries, proceeding anyway")
                    return set()
    except Exception as e:
        print(f"Warning: Could not connect to v2 for dedup check: {e}")
        return set()


async def post_entry_to_v2(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    entry: Dict[str, Any]
) -> bool:
    """Post a single entry to v2 API.

    Args:
        session: aiohttp session
        base_url: v2 API base URL
        api_key: v2 API key
        entry: Entry data to post

    Returns:
        True if successful, False otherwise
    """
    url = f"{base_url.rstrip('/')}/api/history"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Prepare entry data (remove id, will be auto-generated or use existing)
    data = {
        "title": entry.get("title", ""),
        "customer": entry.get("customer", ""),
        "category": entry.get("category", ""),
        "query_summary": entry.get("query_summary", ""),
        "solution": entry.get("solution", ""),
        "created_at": entry.get("created_at", ""),
        "url": entry.get("url", ""),
        "channel_id": entry.get("channel_id", ""),
        "channel_name": entry.get("channel_name", ""),
        "referenced_docs": entry.get("referenced_docs", []),
        "referenced_history": entry.get("referenced_history", []),
        "metadata": entry.get("metadata", {}),
        "source": entry.get("source", "migration_v1")
    }

    try:
        async with session.post(
            url,
            json=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status == 200:
                return True
            else:
                error = await response.text()
                print(f"  Error: {response.status} - {error[:100]}")
                return False
    except Exception as e:
        print(f"  Error posting: {e}")
        return False


async def migrate(
    v1_url: str,
    v1_key: str,
    v2_url: str,
    v2_key: str,
    dry_run: bool = False
) -> None:
    """Run the migration from v1 to v2.

    Args:
        v1_url: v1 API base URL
        v1_key: v1 API key
        v2_url: v2 API base URL
        v2_key: v2 API key
        dry_run: If True, don't actually post to v2
    """
    print("=" * 60)
    print("v1 -> v2 History Migration")
    print("=" * 60)
    print(f"v1: {v1_url}")
    print(f"v2: {v2_url}")
    print(f"Dry run: {dry_run}")
    print()

    # Fetch v1 entries
    v1_entries = await fetch_v1_entries(v1_url, v1_key)
    if v1_entries is None:
        print("Migration aborted: Could not fetch v1 entries")
        return

    if not v1_entries:
        print("No entries to migrate")
        return

    # Fetch existing v2 entry IDs for dedup
    print("\nChecking existing v2 entries for deduplication...")
    v2_ids = await fetch_v2_entry_ids(v2_url, v2_key)
    print(f"Found {len(v2_ids)} existing entries in v2")

    # Filter out duplicates
    entries_to_migrate = []
    skipped = 0
    for entry in v1_entries:
        if entry.get("id") in v2_ids:
            skipped += 1
        else:
            entries_to_migrate.append(entry)

    print(f"\nEntries to migrate: {len(entries_to_migrate)}")
    print(f"Skipped (already exists): {skipped}")

    if not entries_to_migrate:
        print("\nNo new entries to migrate")
        return

    if dry_run:
        print("\n[DRY RUN] Would migrate the following entries:")
        for i, entry in enumerate(entries_to_migrate[:10]):
            print(f"  {i+1}. {entry.get('title', 'No title')[:50]}")
        if len(entries_to_migrate) > 10:
            print(f"  ... and {len(entries_to_migrate) - 10} more")
        return

    # Migrate entries
    print("\nMigrating entries...")
    success = 0
    failed = 0

    async with aiohttp.ClientSession() as session:
        for i, entry in enumerate(entries_to_migrate):
            title = entry.get("title", "No title")[:40]
            print(f"  [{i+1}/{len(entries_to_migrate)}] {title}...", end=" ")

            if await post_entry_to_v2(session, v2_url, v2_key, entry):
                print("OK")
                success += 1
            else:
                print("FAILED")
                failed += 1

    print()
    print("=" * 60)
    print("Migration Complete")
    print("=" * 60)
    print(f"Successfully migrated: {success}")
    print(f"Failed: {failed}")
    print(f"Skipped (duplicates): {skipped}")

    # Verify final count
    print("\nVerifying v2 stats...")
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{v2_url.rstrip('/')}/api/history/stats"
            headers = {"Authorization": f"Bearer {v2_key}"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    stats = await response.json()
                    print(f"v2 total entries: {stats.get('total_entries', 'N/A')}")
    except Exception as e:
        print(f"Could not verify stats: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate history entries from v1 to v2 Railway"
    )
    parser.add_argument(
        "--v1-url",
        default="https://moengage-qna-agent-production.up.railway.app",
        help="v1 API base URL"
    )
    parser.add_argument(
        "--v1-key",
        default="moengage-history-2024",
        help="v1 API key"
    )
    parser.add_argument(
        "--v2-url",
        default="https://moengage-qna-agent-v2-production.up.railway.app",
        help="v2 API base URL"
    )
    parser.add_argument(
        "--v2-key",
        default="moengage-v2-api-2024",
        help="v2 API key"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually post to v2, just show what would be migrated"
    )

    args = parser.parse_args()

    asyncio.run(migrate(
        v1_url=args.v1_url,
        v1_key=args.v1_key,
        v2_url=args.v2_url,
        v2_key=args.v2_key,
        dry_run=args.dry_run
    ))


if __name__ == "__main__":
    main()
