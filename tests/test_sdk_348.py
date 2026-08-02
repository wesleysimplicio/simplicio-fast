import asyncio

from simplicio_fast.projection import ProjectionEnvelope
from simplicio_fast.sdk import ProjectionSDK


def envelope(handle: str) -> ProjectionEnvelope:
    return ProjectionEnvelope.create("code", producer="mapper", producer_schema="mapper/v1", generation="g1", stable_handle=handle, payload={"repository": "repo", "name": handle})


def test_sdk_exposes_scoped_in_process_operations_and_context(tmp_path) -> None:
    sdk = ProjectionSDK("repo")
    sdk.publish(envelope("symbol:a"))
    assert sdk.query("symbol:a")["stable_handle"] == "symbol:a"
    assert sdk.capabilities()["authority"] == "derived_read_only"
    assert sdk.context()["projection_count"] == 1
    path = tmp_path / "projection.json"
    sdk.save(path)
    reopened = ProjectionSDK.open(path, "repo")
    assert reopened.snapshot() == sdk.snapshot()


def test_sdk_async_surface_is_read_only_and_matches_sync() -> None:
    sdk = ProjectionSDK("repo")
    sdk.publish(envelope("symbol:a"))

    async def run() -> None:
        assert await sdk.query_async("symbol:a") == sdk.query("symbol:a")
        assert await sdk.context_async() == sdk.context()

    asyncio.run(run())


def test_sdk_supports_twenty_concurrent_async_queries(tmp_path) -> None:
    sdk = ProjectionSDK("repo")
    sdk.publish(envelope("symbol:a"))

    async def run() -> None:
        results = await asyncio.gather(*(sdk.query_async("symbol:a") for _ in range(20)))
        assert results == [sdk.query("symbol:a")] * 20
        path = tmp_path / "projection.json"
        receipt = await asyncio.to_thread(sdk.save, path)
        assert receipt["records"] == 1

    asyncio.run(run())
