"""What a recall engine is: the three operations transports call.

The pipeline's retrieval and ranking are replaceable: `RecallService` (the first engine,
`legacy`) and `FusionRecallService` (`fusion`) both satisfy this protocol, and
`ONTYRA_RECALL__ENGINE` chooses between them in the composition root. Supporting services
(exploration) use the shared stages both engines inherit.
"""

from __future__ import annotations

from typing import Protocol

from ontyra.models.recall import CheckResult, RecallContext, RecallPack, RecallRequest
from ontyra.services.context import Caller


class RecallEngine(Protocol):
    async def recall(self, caller: Caller, request: RecallRequest) -> RecallPack: ...

    async def briefing(self, caller: Caller, request: RecallRequest) -> RecallPack: ...

    async def check(self, caller: Caller, intent: str, context: RecallContext) -> CheckResult: ...
