from __future__ import annotations

import logging

import lark_oapi as lark  # type: ignore[import-untyped]

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.runtime import AgentRuntime
from visit_agent.api.feishu_events import FeishuAgentEventHandler, FeishuEventQueue
from visit_agent.config import settings
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

    repo = seed_demo(InMemoryRepository())
    agent = VisitCoordinatorAgent(repo)
    runtime = AgentRuntime(
        agent,
        feishu_app_id=settings.feishu_app_id,
        feishu_app_secret=settings.feishu_app_secret,
        feishu_base_url=settings.feishu_base_url,
        feishu_calendar_id=settings.feishu_calendar_id,
    )
    handler = FeishuAgentEventHandler(runtime)
    events = FeishuEventQueue(handler.handle)
    events.start()
    dispatcher = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(events.submit)
        .build()
    )
    ws_client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        log_level=lark.LogLevel.INFO,
        event_handler=dispatcher,
    )
    ws_client.on_reconnecting = lambda: logger.warning("飞书长连接已断开，正在重连")
    ws_client.on_reconnected = lambda: logger.info("飞书长连接重连成功")
    logger.info("正在建立飞书长连接")
    try:
        ws_client.start()
    finally:
        events.shutdown()


if __name__ == "__main__":
    main()
