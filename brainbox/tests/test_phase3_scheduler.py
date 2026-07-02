"""Tests for Phase 3: resource-aware runner dispatch.

Covers:
- RunnerInfo load fields (queue_depth, in_flight, max_concurrent)
- update_load() method
- queue_depth tracking on enqueue/dequeue
- register() accepts load parameters
"""

from __future__ import annotations

import asyncio


from brainbox.runners import RunnerRegistry


class TestRunnerLoadFields:
    async def test_default_load_fields(self):
        reg = RunnerRegistry()
        info = await reg.register(name="r1", capabilities={"docker": True})
        assert info.queue_depth == 0
        assert info.in_flight == 0
        assert info.max_concurrent == 4

    async def test_register_with_load_params(self):
        reg = RunnerRegistry()
        info = await reg.register(
            name="r1", capabilities={"docker": True},
            in_flight=2, max_concurrent=8,
        )
        assert info.in_flight == 2
        assert info.max_concurrent == 8

    async def test_negative_in_flight_clamped_to_zero(self):
        reg = RunnerRegistry()
        info = await reg.register(name="r1", capabilities={"docker": True}, in_flight=-5)
        assert info.in_flight == 0

    async def test_max_concurrent_minimum_one(self):
        reg = RunnerRegistry()
        info = await reg.register(name="r1", capabilities={"docker": True}, max_concurrent=0)
        assert info.max_concurrent >= 1


class TestQueueDepthTracking:
    async def test_queue_depth_increments_on_enqueue(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        await reg.enqueue(runner="r1", kind="session.create", payload={})
        info = await reg.get("r1")
        assert info.queue_depth == 1

    async def test_queue_depth_increments_per_item(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        await reg.enqueue(runner="r1", kind="session.create", payload={"a": 1})
        await reg.enqueue(runner="r1", kind="session.create", payload={"b": 2})
        info = await reg.get("r1")
        assert info.queue_depth == 2

    async def test_queue_depth_decrements_on_dequeue(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        await reg.enqueue(runner="r1", kind="session.create", payload={})
        await reg.enqueue(runner="r1", kind="session.create", payload={})

        await reg.next_pending("r1", timeout=0.1)
        info = await reg.get("r1")
        assert info.queue_depth == 1

        await reg.next_pending("r1", timeout=0.1)
        info = await reg.get("r1")
        assert info.queue_depth == 0

    async def test_queue_depth_zero_when_no_work(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        info = await reg.get("r1")
        assert info.queue_depth == 0


class TestUpdateLoad:
    async def test_update_load_sets_in_flight(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        result = await reg.update_load("r1", in_flight=3)
        assert result is True
        info = await reg.get("r1")
        assert info.in_flight == 3

    async def test_update_load_sets_max_concurrent(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        await reg.update_load("r1", max_concurrent=16)
        info = await reg.get("r1")
        assert info.max_concurrent == 16

    async def test_update_load_partial_update(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True}, max_concurrent=8)
        await reg.update_load("r1", in_flight=2)  # only update in_flight
        info = await reg.get("r1")
        assert info.in_flight == 2
        assert info.max_concurrent == 8  # unchanged

    async def test_update_load_returns_false_for_unknown_runner(self):
        reg = RunnerRegistry()
        result = await reg.update_load("ghost", in_flight=1)
        assert result is False

    async def test_update_load_clamps_negative_in_flight(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        await reg.update_load("r1", in_flight=-1)
        info = await reg.get("r1")
        assert info.in_flight == 0

    async def test_update_load_ignores_zero_max_concurrent(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True}, max_concurrent=4)
        await reg.update_load("r1", max_concurrent=0)
        info = await reg.get("r1")
        assert info.max_concurrent == 4  # unchanged (0 rejected)

    async def test_update_load_bumps_last_seen(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        before = (await reg.get("r1")).last_seen
        await asyncio.sleep(0.01)
        await reg.update_load("r1", in_flight=1)
        after = (await reg.get("r1")).last_seen
        assert after >= before


class TestSelectRunner:
    async def test_no_runners_returns_none(self):
        reg = RunnerRegistry()
        result = await reg.select_runner(backend="docker")
        assert result is None

    async def test_wrong_backend_returns_none(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"utm": True})
        result = await reg.select_runner(backend="docker")
        assert result is None

    async def test_saturated_runner_returns_none(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True}, in_flight=4, max_concurrent=4)
        result = await reg.select_runner(backend="docker")
        assert result is None

    async def test_stale_runner_returns_none(self):
        import time
        from unittest.mock import patch
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        # Advance clock past the 90s online window
        with patch("brainbox.runners.time.time", return_value=time.time() + 91):
            result = await reg.select_runner(backend="docker")
        assert result is None

    async def test_single_eligible_runner_returned(self):
        reg = RunnerRegistry()
        await reg.register(name="r1", capabilities={"docker": True})
        result = await reg.select_runner(backend="docker")
        assert result == "r1"

    async def test_local_process_runner_not_auto_selected(self):
        # Interactive local-process runners (the app's Mac runner) are opt-in:
        # never auto-dispatched — task falls back to headless box execution.
        reg = RunnerRegistry()
        await reg.register(name="Local", capabilities={"docker": True}, host="local-process")
        result = await reg.select_runner(backend="docker")
        assert result is None

    async def test_box_runner_preferred_over_local_process(self):
        reg = RunnerRegistry()
        await reg.register(name="Local", capabilities={"docker": True}, host="local-process")
        await reg.register(name="box", capabilities={"docker": True}, host="10.0.0.5")
        result = await reg.select_runner(backend="docker")
        assert result == "box"  # the real remote runner wins; Local is excluded

    async def test_prefers_more_headroom(self):
        reg = RunnerRegistry()
        await reg.register(name="busy", capabilities={"docker": True}, in_flight=3, max_concurrent=4)
        await reg.register(name="free", capabilities={"docker": True}, in_flight=0, max_concurrent=4)
        result = await reg.select_runner(backend="docker")
        assert result == "free"

    async def test_tag_overlap_breaks_headroom_tie(self):
        reg = RunnerRegistry()
        await reg.register(name="r-notag", capabilities={"docker": True})
        await reg.register(name="r-tag", capabilities={"docker": True}, tags=["gpu"])
        result = await reg.select_runner(backend="docker", preferred_tags=["gpu"])
        assert result == "r-tag"

    async def test_alphabetical_tiebreak(self):
        reg = RunnerRegistry()
        await reg.register(name="beta", capabilities={"docker": True})
        await reg.register(name="alpha", capabilities={"docker": True})
        result = await reg.select_runner(backend="docker")
        assert result == "alpha"
