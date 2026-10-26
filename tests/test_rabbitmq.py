"""Run against the pinned RabbitMQ container, never substitute an in-memory queue."""

import os

import pytest
from sqlalchemy import select

from quality import models as m
from quality.db import Database
from quality.worker import publish_once


@pytest.mark.skipif(
    not os.environ.get("QUALITY_TEST_RABBITMQ_URL"),
    reason="需要真实RabbitMQ；当前主机没有Docker/Erlang，未执行",
)
def test_rabbit_publisher_confirms_inbox_and_replay(core):
    settings, _, clients, lines = core
    from .test_workflow import case

    case(clients["engineer"], lines[0])
    configured = settings.model_copy(update={"rabbitmq_url": os.environ["QUALITY_TEST_RABBITMQ_URL"]})
    db = Database(configured)
    first = publish_once(configured, db)
    assert first["sent"] > 0
    assert publish_once(configured, db)["sent"] == 0
    with db.session() as session:
        messages = session.scalars(select(m.Inbox)).all()
        assert messages and len({r.event_id for r in messages}) == len(messages)
