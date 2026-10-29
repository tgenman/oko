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


def cleanup(db: Database, cutoff: float, cursor: tuple[float, int] | None = None, limit: int = 200) -> tuple[float, int] | None:
    """Visit at most one indexed page; never delete an identity's latest receipt."""
    if cursor is None:
        rows = db.execute("SELECT sequence,completed FROM reader_action_results WHERE completed<=%s ORDER BY completed,sequence LIMIT %s", (cutoff, limit)).fetchall()
    else:
        rows = db.execute("SELECT sequence,completed FROM reader_action_results WHERE completed<=%s AND (completed,sequence)>(%s,%s) ORDER BY completed,sequence LIMIT %s", (cutoff, *cursor, limit)).fetchall()
    if rows:
        db.execute("""DELETE FROM reader_action_results r WHERE r.sequence=ANY(%s) AND r.completed<=%s
            AND EXISTS (SELECT 1 FROM reader_action_results newer WHERE newer.identity=r.identity AND newer.sequence>r.sequence)""",
            ([row['sequence'] for row in rows], cutoff))
    return (float(rows[-1]['completed']), int(rows[-1]['sequence'])) if len(rows) == limit else None
