"""Message state machine for tracking ticket processing status."""

from enum import Enum
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
import asyncio

from src.utils.logger import logger


class MessageState(Enum):
    """States for a message in the ticket workflow."""
    IDLE = "idle"               # No emoji, not processing
    PROCESSING = "processing"   # Ticket emoji detected, generating response
    ANSWERED = "answered"       # Bot has replied, waiting for resolution
    DELIVERED = "delivered"     # Response delivered to customer thread
    ARCHIVING = "archiving"     # Complete emoji detected, archiving in progress
    COMPLETED = "completed"     # Archived to history


class InMemoryStateStore:
    """Simple in-memory state store for message states.

    For production, replace with Redis implementation.
    """

    def __init__(self, ttl_hours: int = 24):
        """Initialize the state store.

        Args:
            ttl_hours: Hours to keep state before expiring
        """
        self._states: Dict[str, Tuple[MessageState, datetime]] = {}
        self._ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    def _make_key(self, channel: str, message_ts: str) -> str:
        """Create a unique key for a message."""
        return f"{channel}:{message_ts}"

    async def get(self, channel: str, message_ts: str) -> MessageState:
        """Get the state of a message.

        Args:
            channel: Channel ID
            message_ts: Message timestamp

        Returns:
            Current state (IDLE if not found)
        """
        key = self._make_key(channel, message_ts)

        async with self._lock:
            if key in self._states:
                state, created_at = self._states[key]

                # Check TTL
                if datetime.now() - created_at > self._ttl:
                    del self._states[key]
                    return MessageState.IDLE

                return state

        return MessageState.IDLE

    async def set(self, channel: str, message_ts: str, state: MessageState):
        """Set the state of a message.

        Args:
            channel: Channel ID
            message_ts: Message timestamp
            state: New state
        """
        key = self._make_key(channel, message_ts)

        async with self._lock:
            self._states[key] = (state, datetime.now())
            logger.debug(f"State set: {key} -> {state.value}")

    async def delete(self, channel: str, message_ts: str):
        """Delete a message's state.

        Args:
            channel: Channel ID
            message_ts: Message timestamp
        """
        key = self._make_key(channel, message_ts)

        async with self._lock:
            if key in self._states:
                del self._states[key]

    async def cleanup_expired(self):
        """Remove expired entries."""
        now = datetime.now()

        async with self._lock:
            expired_keys = [
                key for key, (_, created_at) in self._states.items()
                if now - created_at > self._ttl
            ]

            for key in expired_keys:
                del self._states[key]

            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired state entries")


# Global state store instance
_state_store: Optional[InMemoryStateStore] = None


def get_state_store() -> InMemoryStateStore:
    """Get or create the global state store."""
    global _state_store
    if _state_store is None:
        _state_store = InMemoryStateStore()
    return _state_store


async def get_message_state(channel: str, message_ts: str) -> MessageState:
    """Get the current state of a message.

    Args:
        channel: Channel ID
        message_ts: Message timestamp

    Returns:
        Current MessageState
    """
    store = get_state_store()
    return await store.get(channel, message_ts)


async def set_message_state(channel: str, message_ts: str, state: MessageState):
    """Set the state of a message.

    Args:
        channel: Channel ID
        message_ts: Message timestamp
        state: New state to set
    """
    store = get_state_store()
    await store.set(channel, message_ts, state)


async def can_process_ticket(channel: str, message_ts: str) -> bool:
    """Check if a ticket can be processed (idempotency check).

    Args:
        channel: Channel ID
        message_ts: Message timestamp

    Returns:
        True if ticket can be processed
    """
    current_state = await get_message_state(channel, message_ts)

    # Can only process if in IDLE state
    if current_state == MessageState.IDLE:
        return True

    logger.info(
        f"Cannot process ticket {channel}/{message_ts}: "
        f"current state is {current_state.value}"
    )
    return False


async def can_process_complete(channel: str, message_ts: str) -> bool:
    """Check if completion can be processed.

    Args:
        channel: Channel ID
        message_ts: Message timestamp

    Returns:
        True if completion can be processed
    """
    current_state = await get_message_state(channel, message_ts)

    # Can process complete if ANSWERED (normal flow)
    # or IDLE (if ticket was processed before state tracking started)
    if current_state in [MessageState.ANSWERED, MessageState.DELIVERED, MessageState.IDLE]:
        return True

    logger.info(
        f"Cannot process complete {channel}/{message_ts}: "
        f"current state is {current_state.value}"
    )
    return False


# State transition diagram for reference:
#
#  ┌──────────┐  ticket emoji   ┌────────────┐  response posted  ┌──────────┐
#  │   IDLE   │ ───────────────>│ PROCESSING │ ─────────────────>│ ANSWERED │
#  └──────────┘                 └────────────┘                   └──────────┘
#       ▲                             │                               │
#       │                             │ error                   deliver button
#       │                             ▼                               │
#       │                        ┌──────────┐                   ┌───────────┐
#       └────────────────────────│   IDLE   │                   │ DELIVERED │
#         emoji removed          └──────────┘                   └───────────┘
#                                                                     │
#                                                               complete emoji
#                                                                     │
#                                                               ┌───────────┐
#                                                               │ ARCHIVING │
#                                                               └───────────┘
#                                                                     │
#                                                                  archived
#                                                                     │
#                                                               ┌───────────┐
#                                                               │ COMPLETED │
#                                                               └───────────┘
#
# Note: complete emoji can also be applied directly from ANSWERED state
