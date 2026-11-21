"""A new client's first requests often arrive together (a hook's session start and prompt)."""

import asyncio

from ontyra.app import Application


async def test_concurrent_first_requests_of_one_client_all_succeed(make_app) -> None:
    app: Application = await make_app()
    callers = await asyncio.gather(
        *[app.actors.caller(kind="agent", name="newcomer", harness="h") for _ in range(8)]
    )
    assert len({c.actor.id for c in callers}) == 1
    owners = await asyncio.gather(*[app.actors.ensure_owner("user", "dana") for _ in range(8)])
    assert len(set(owners)) == 1
