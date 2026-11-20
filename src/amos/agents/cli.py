"""Agent evaluation entry point.

python -m amos.agents.cli routing
"""

from __future__ import annotations

import asyncio

from amos.agents.registry import AgentRegistry
from amos.agents.router import ROUTING_CASES, Router, evaluate_routing
from amos.api.dependencies import build_provider
from amos.config import get_settings
from amos.observability import configure_logging


async def routing() -> None:
    settings = get_settings()
    router = Router(build_provider(settings), AgentRegistry())
    result = await evaluate_routing(router, ROUTING_CASES)

    print(result.summary())
    for instruction, expected, actual in result.mistakes:
        print(f"  {instruction[:64]}")
        print(f"    expected {expected}, routed to {actual}")


def main() -> None:
    configure_logging("WARNING")
    asyncio.run(routing())


if __name__ == "__main__":
    main()
