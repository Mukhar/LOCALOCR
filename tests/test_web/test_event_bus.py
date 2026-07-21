"""
test_event_bus.py
=================
Unit tests for the async in-memory event bus.

Uses pytest-asyncio. Each test spins up a fresh EventBus (not the
module-level singleton) for isolation.
"""

from __future__ import annotations

import asyncio

import pytest

from src.web.services.event_bus import EventBus, event_bus as module_bus


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    """A subscriber receives events published AFTER it subscribed."""
    bus = EventBus()
    received: list = []

    async def consumer():
        async for event in bus.subscribe(run_id=1):
            received.append(event)
            if event.get("type") == "done":
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let the consumer register its queue

    bus.publish(1, {"type": "progress", "step": "OCR"})
    bus.publish(1, {"type": "done"})
    await asyncio.wait_for(task, timeout=2.0)

    assert received == [{"type": "progress", "step": "OCR"}, {"type": "done"}]


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    """Each subscriber gets its own copy of every event -- fan-out."""
    bus = EventBus()
    r1: list = []
    r2: list = []

    async def consume(target: list):
        async for event in bus.subscribe(run_id=42):
            target.append(event)
            if event.get("type") == "done":
                break

    t1 = asyncio.create_task(consume(r1))
    t2 = asyncio.create_task(consume(r2))
    await asyncio.sleep(0.05)  # let both queues register

    bus.publish(42, {"type": "progress", "n": 1})
    bus.publish(42, {"type": "progress", "n": 2})
    bus.publish(42, {"type": "done"})
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)

    assert r1 == r2 == [
        {"type": "progress", "n": 1},
        {"type": "progress", "n": 2},
        {"type": "done"},
    ]


@pytest.mark.asyncio
async def test_publish_ignores_missing_subscribers():
    """Publishing to a run with zero subscribers is a no-op (not error)."""
    bus = EventBus()
    bus.publish(999, {"type": "progress"})  # should not raise
    # And the internal registry stays empty
    assert 999 not in bus._queues


@pytest.mark.asyncio
async def test_publish_scoped_to_run_id():
    """Publishing to run A doesn't reach subscribers of run B."""
    bus = EventBus()
    received_a: list = []
    received_b: list = []

    async def consume_a():
        async for event in bus.subscribe(run_id=1):
            received_a.append(event)
            if event.get("type") == "done":
                break

    async def consume_b():
        async for event in bus.subscribe(run_id=2):
            received_b.append(event)
            if event.get("type") == "done":
                break

    ta = asyncio.create_task(consume_a())
    tb = asyncio.create_task(consume_b())
    await asyncio.sleep(0.05)

    bus.publish(1, {"type": "progress", "run": "A"})
    bus.publish(2, {"type": "progress", "run": "B"})
    bus.publish(1, {"type": "done"})
    bus.publish(2, {"type": "done"})
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)

    assert received_a == [{"type": "progress", "run": "A"}, {"type": "done"}]
    assert received_b == [{"type": "progress", "run": "B"}, {"type": "done"}]


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_done():
    """After a subscriber stops iterating (via done event), the queue is
    removed from the internal registry."""
    bus = EventBus()

    async def consumer():
        async for event in bus.subscribe(run_id=7):
            if event.get("type") == "done":
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    assert 7 in bus._queues and len(bus._queues[7]) == 1

    bus.publish(7, {"type": "done"})
    await asyncio.wait_for(task, timeout=2.0)

    # Queue is gone from the registry; key is popped when list is empty
    assert 7 not in bus._queues


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_client_cancel():
    """If the subscriber task is cancelled mid-iteration, the finally
    block still cleans up the queue."""
    bus = EventBus()

    async def consumer():
        async for _ in bus.subscribe(run_id=8):
            pass  # never break; will be cancelled from outside

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    assert 8 in bus._queues

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Queue got cleaned up despite the cancel
    assert 8 not in bus._queues


@pytest.mark.asyncio
async def test_module_singleton_is_shared():
    """The module-level `event_bus` is the same instance on every import."""
    from src.web.services.event_bus import event_bus as bus_again
    assert module_bus is bus_again


@pytest.mark.asyncio
async def test_done_event_yielded_before_iterator_stops():
    """The 'done' event is delivered to the subscriber, THEN iteration
    ends -- so consumers can see the terminal state."""
    bus = EventBus()
    received: list = []

    async def consumer():
        async for event in bus.subscribe(run_id=10):
            received.append(event)
            if event.get("type") == "done":
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    bus.publish(10, {"type": "done", "status": "success"})
    await asyncio.wait_for(task, timeout=2.0)

    assert received == [{"type": "done", "status": "success"}]
