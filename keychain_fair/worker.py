from __future__ import annotations

import asyncio
import logging

from .services import OrderService


logger = logging.getLogger(__name__)


async def worker_loop(service: OrderService, poll_seconds: int) -> None:
    while True:
        try:
            await asyncio.to_thread(service.run_queue)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queue worker failed")
        await asyncio.sleep(poll_seconds)

