#!/usr/bin/env python3
"""Migrate data from Railway to GCS.

Usage:
    python scripts/migrate_railway_to_gcs.py

Required environment variables:
    HISTORY_API_URL: Railway API URL (e.g., https://moengage-qna-agent.up.railway.app)
    HISTORY_API_KEY: Railway API key
    GCP_STORAGE_BUCKET: GCS bucket name
"""

import asyncio
import json
import os
import sys
import tempfile

import aiohttp

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import storage


async def fetch_railway_data(base_url: str, api_key: str) -> dict:
    """Fetch all data from Railway API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        # Fetch history entries
        print("Fetching history entries from Railway...")
        async with session.get(
            f"{base_url}/api/history/export",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                error = await response.text()
                raise Exception(f"Failed to fetch history: {response.status} - {error}")
            history_data = await response.json()
            print(f"  Found {history_data.get('count', 0)} history entries")

        # Fetch learning entries
        print("Fetching learning entries from Railway...")
        async with session.get(
            f"{base_url}/api/learning/export",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                error = await response.text()
                raise Exception(f"Failed to fetch learning: {response.status} - {error}")
            learning_data = await response.json()
            print(f"  Found {learning_data.get('count', 0)} learning entries")

    return {
        "history": history_data,
        "learning": learning_data
    }


def upload_to_gcs(bucket_name: str, data: dict):
    """Upload data to GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # Download existing data from GCS first
    print("\nChecking existing GCS data...")

    # History metadata
    history_blob = bucket.blob("vectordb/metadata.json")
    existing_history = {"entries": {}, "id_to_idx": {}}
    if history_blob.exists():
        existing_data = json.loads(history_blob.download_as_string())
        existing_history = existing_data
        print(f"  Existing history entries in GCS: {len(existing_history.get('entries', {}))}")

    # Learning metadata
    learning_blob = bucket.blob("learning/learning_metadata.json")
    existing_learning = {"entries": {}, "id_to_idx": {}}
    if learning_blob.exists():
        existing_data = json.loads(learning_blob.download_as_string())
        existing_learning = existing_data
        print(f"  Existing learning entries in GCS: {len(existing_learning.get('entries', {}))}")

    # Merge history entries
    print("\nMerging history entries...")
    railway_history = data["history"].get("entries", [])
    new_count = 0
    for entry in railway_history:
        entry_id = entry.get("id")
        if entry_id and entry_id not in existing_history["entries"]:
            # Add new entry
            idx = len(existing_history["id_to_idx"])
            existing_history["entries"][entry_id] = entry
            existing_history["id_to_idx"][entry_id] = idx
            new_count += 1
    print(f"  Added {new_count} new history entries")

    # Merge learning entries
    print("Merging learning entries...")
    railway_learning = data["learning"].get("entries", [])
    new_learning_count = 0
    for entry in railway_learning:
        entry_id = entry.get("id")
        if entry_id and entry_id not in existing_learning["entries"]:
            idx = len(existing_learning["id_to_idx"])
            existing_learning["entries"][entry_id] = entry
            existing_learning["id_to_idx"][entry_id] = idx
            new_learning_count += 1
    print(f"  Added {new_learning_count} new learning entries")

    # Upload merged data
    print("\nUploading to GCS...")

    # Upload history metadata
    history_json = json.dumps(existing_history, ensure_ascii=False, indent=2)
    history_blob.upload_from_string(history_json, content_type="application/json")
    print(f"  Uploaded vectordb/metadata.json ({len(existing_history['entries'])} entries)")

    # Upload learning metadata
    learning_json = json.dumps(existing_learning, ensure_ascii=False, indent=2)
    learning_blob.upload_from_string(learning_json, content_type="application/json")
    print(f"  Uploaded learning/learning_metadata.json ({len(existing_learning['entries'])} entries)")

    print("\nNote: FAISS indices need to be regenerated on next startup.")
    print("The Cloud Run service will rebuild indices when it restarts.")


async def main():
    # Get configuration
    railway_url = os.environ.get("HISTORY_API_URL")
    railway_key = os.environ.get("HISTORY_API_KEY")
    gcs_bucket = os.environ.get("GCP_STORAGE_BUCKET")

    if not railway_url:
        print("Error: HISTORY_API_URL not set")
        sys.exit(1)
    if not railway_key:
        print("Error: HISTORY_API_KEY not set")
        sys.exit(1)
    if not gcs_bucket:
        print("Error: GCP_STORAGE_BUCKET not set")
        sys.exit(1)

    print("=" * 60)
    print("Railway → GCS Migration")
    print("=" * 60)
    print(f"Source: {railway_url}")
    print(f"Target: gs://{gcs_bucket}/")
    print("=" * 60)

    # Fetch data from Railway
    data = await fetch_railway_data(railway_url, railway_key)

    # Upload to GCS
    upload_to_gcs(gcs_bucket, data)

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
