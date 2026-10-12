"""Forum selection and message scope at the external Telethon boundary."""
from typing import Any


def message_topic(message: Any) -> int:
    if type(getattr(message, 'action', None)).__name__ == 'MessageActionTopicCreate':
        return int(message.id)
    reply = getattr(message, 'reply_to', None)
    return int((getattr(reply, 'reply_to_top_id', None) or
                (getattr(reply, 'reply_to_msg_id', None) if getattr(reply, 'forum_topic', False) else None)) or 1)


async def topic_info(client: Any, entity: Any, topic_id: int) -> Any:
    from telethon import functions  # type: ignore[import-untyped]
    if not getattr(entity, 'forum', False) or not isinstance(topic_id, int) or topic_id <= 0:
        raise ValueError('topic')
    result = await client(functions.messages.GetForumTopicsByIDRequest(peer=entity, topics=[topic_id]))
    topic = next((t for t in result.topics if t.id == topic_id and hasattr(t, 'title')), None)
    if topic is None:
        raise ValueError('topic unavailable')
    return topic


async def list_topics(client: Any, entity: Any) -> list[dict[str, Any]]:
    from telethon import functions
    if not getattr(entity, 'forum', False):
        return []
    offset_date, offset_id, offset_topic = None, 0, 0
    topics: dict[int, dict[str, Any]] = {}
    while True:
        result = await client(functions.messages.GetForumTopicsRequest(peer=entity,
            offset_date=offset_date, offset_id=offset_id, offset_topic=offset_topic, limit=100))
        if not result.topics:
            break
        for topic in result.topics:
            if hasattr(topic, 'title'):
                topics[topic.id] = dict(id=topic.id, title=topic.title)
        last = result.topics[-1]
        if last.id == offset_topic:
            raise ValueError('topic pagination stalled')
        offset_topic, offset_id = last.id, last.top_message
        offset_date = last.date if result.order_by_create_date else next(
            (m.date for m in result.messages if m.id == last.top_message), None)
        if offset_date is None:
            raise ValueError('topic pagination unavailable')
        if len(topics) >= result.count:
            break
    return sorted(topics.values(), key=lambda t: (t['id'] != 1, t['title'].casefold(), t['id']))


async def history(client: Any, entity: Any, topic_id: int | None, **params: Any) -> tuple[list[Any], list[Any]]:
    if topic_id is not None and topic_id != 1:
        params['reply_to'] = topic_id
    batch = list(await client.get_messages(entity, **params))
    return batch, [m for m in batch if m and (topic_id is None or message_topic(m) == topic_id)]


async def read_state(client: Any, entity: Any, topic_id: int, since: Any) -> dict[str, int]:
    topic = await topic_info(client, entity, topic_id)
    offset = 0
    while True:
        batch, selected = await history(client, entity, topic_id, limit=100, offset_date=since, offset_id=offset)
        if selected or len(batch) < 100:
            return dict(read_max=topic.read_inbox_max_id, initial=selected[0].id if selected else 0)
        next_offset = min(m.id for m in batch)
        if offset and next_offset >= offset:
            raise ValueError('topic history stalled')
        offset = next_offset


async def read_window(client: Any, entity: Any, topic_id: int, after: int, until: int) -> dict[str, Any]:
    while True:
        batch, selected = await history(client, entity, topic_id, limit=100, reverse=True,
                                        offset_id=after, max_id=until + 1)
        if selected or len(batch) < 100:
            return dict(ids=sorted(m.id for m in selected), more=len(batch) == 100)
        next_after = max(m.id for m in batch)
        if next_after <= after:
            raise ValueError('topic history stalled')
        after = next_after
