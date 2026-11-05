"""冻结的旧Web兼容包装，不再创建内部调度器。"""

import asyncio
import logging

from .collectors import Collectors
from .network import Network
from .pipeline import collect_legacy, execute_legacy


class Service:
    def __init__(self, store, network, settings):
        self.store, self.network, self.settings = store, network, settings
        self.collectors = Collectors(network, lambda: self.settings)
        self.active_task = None
        self.active_run = None
        self.last_failed_auto_date = None

    def start(self):
        logging.getLogger(__name__).info("旧Web模式：内置调度已停用，请由外部调用pulse run")

    def reschedule(self):
        logging.getLogger(__name__).info("不再启用进程内定时；schedule仅作为配置说明")

    def submit(self, kind, trigger="manual"):
        if self.active_task and not self.active_task.done():
            return self.active_run, False
        if self.network is None:
            self.network = Network()
            self.collectors.network = self.network
        run_id = self.store.start_run(kind, trigger)
        self.active_run = run_id
        self.active_task = asyncio.create_task(self.execute(run_id, kind, trigger))
        return run_id, True

    async def collect(self, run_id):
        return await collect_legacy(self, run_id)

    async def execute(self, run_id, kind, trigger):
        try:
            await execute_legacy(self, run_id, kind, trigger)
        finally:
            await self.network.close()
            self.network = None

    async def close(self):
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        if self.network is not None:
            await self.network.close()
            self.network = None
