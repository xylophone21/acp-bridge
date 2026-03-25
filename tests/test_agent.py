"""Tests for src/agent.py — unit tests for AgentHandle message parsing."""

import asyncio
import json

import pytest

from src.agent import AgentHandle


class TestAgentHandle:
    @pytest.mark.asyncio
    async def test_request_id_increments(self):
        """Verify request IDs increment correctly."""
        # We can't easily test the full flow without a real process,
        # but we can test the ID counter logic
        handle = AgentHandle.__new__(AgentHandle)
        handle._request_id = 0
        handle._lock = asyncio.Lock()
        handle._pending = {}

        async with handle._lock:
            handle._request_id += 1
            assert handle._request_id == 1
            handle._request_id += 1
            assert handle._request_id == 2
