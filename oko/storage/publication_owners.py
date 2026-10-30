"""SQL for selecting primary publication owners, including pending cleanup."""


def owned_ids(feed_condition: str) -> str:
    """Select owner IDs; feed_condition is an internal SQL comparison suffix.

    Each branch uses the caller's feed expression independently, so placeholders
    in it must be supplied twice. A surviving primary always wins over cleanup.
    """
    return f"""SELECT m.publication_id AS id FROM feed_publications m
        WHERE m.is_primary AND m.feed_id {feed_condition}
        UNION ALL
        SELECT p.id FROM jobs j JOIN publications p ON p.id=j.item AND p.viewed=1
        WHERE j.kind='delete' AND (j.payload::jsonb->>'cleanup_feed') {feed_condition}
          AND NOT EXISTS (SELECT 1 FROM feed_publications primary_owner
                          WHERE primary_owner.publication_id=p.id AND primary_owner.is_primary)"""
