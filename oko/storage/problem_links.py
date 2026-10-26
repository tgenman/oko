"""Indexed material references and superseded receipts, derived from journal rows.

Triggers keep these projections in the same transaction, including legacy writes.
Only initial migration scans the journal; its authoritative JSON stays unchanged.
"""
from oko.storage.database import Database, Row




def material_records(db: Database, kind: str, members: set[str]) -> list[Row]:
    table = {'problem': 'reader_problems', 'action': 'reader_action_results'}[kind]
    rows = {}
    ids = sorted(members)
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        query = f"SELECT * FROM {table} WHERE id IN ( SELECT record FROM reader_material_links WHERE kind = %s AND item IN ( {','.join(('%s' for _ in batch))} ) )"
        rows.update({r['id']: r for r in db.execute(query, [kind, *batch])})
    return list(rows.values())


def cleanup(db: Database, cutoff: float, limit: int = 200) -> None:
    db.execute('DELETE FROM reader_action_results WHERE sequence IN ( SELECT sequence FROM reader_action_superseded WHERE completed <= %s ORDER BY completed LIMIT %s )', (cutoff, limit))
