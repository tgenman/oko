"""Indexed material references and superseded receipt cleanup."""
import json
from oko.storage.database import Database, Row




def material_records(db: Database, kind: str, members: set[str]) -> list[Row]:
    table = {'problem': 'reader_problems', 'action': 'reader_action_results'}[kind]
    rows = {}
    ids = sorted(members)
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        query = f"SELECT * FROM {table} WHERE (body::jsonb->'objects') @> ANY(%s::jsonb[])"
        needles = [json.dumps([dict(kind='material', id=identity)]) for identity in batch]
        rows.update({r['id']: r for r in db.execute(query, (needles,))})
    return list(rows.values())


def cleanup(db: Database, cutoff: float, limit: int = 200) -> None:
    db.execute('DELETE FROM reader_action_results WHERE sequence IN ( SELECT sequence FROM reader_action_superseded WHERE completed <= %s ORDER BY completed LIMIT %s )', (cutoff, limit))
