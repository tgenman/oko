"""Durable, bounded collection attempts; independent of processing and read sync."""
from oko.storage.sqlite_schema import is_sqlite
from oko.storage.database import Database, Row
from collections.abc import Callable
from datetime import datetime, timezone
import json
from typing import Any
from oko.diagnostics import redact, exception_details


def timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None


def clipped(value: str, limit: int) -> tuple[str, bool]:
    raw = redact(value).encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw) > limit


class CollectionStatus:
    def __init__(self, db: Database, clock: Callable[[], float], wall: Callable[[], float]) -> None:
        self.db, self.clock, self.wall = db, clock, wall
        if is_sqlite(db):
            if 'queued_wall' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN queued_wall REAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS collection_attempts (job INTEGER PRIMARY KEY, feed TEXT, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS collection_results (feed TEXT PRIMARY KEY, data TEXT NOT NULL);
              DROP TRIGGER IF EXISTS collection_queued_insert;
              DROP TRIGGER IF EXISTS collection_queued_update;
              CREATE TRIGGER collection_queued_insert AFTER INSERT ON jobs
              WHEN NEW.kind IN ('discover','fetch','download_media') BEGIN
                UPDATE jobs SET queued_wall=(julianday('now')-2440587.5)*86400.0 WHERE id=NEW.id;
              END;
              CREATE TRIGGER collection_queued_update AFTER UPDATE OF status ON jobs
              WHEN NEW.kind IN ('discover','fetch','download_media') AND NEW.status='pending' AND OLD.status!='pending' BEGIN
                UPDATE jobs SET queued_wall=(julianday('now')-2440587.5)*86400.0 WHERE id=NEW.id;
              END;
              CREATE TRIGGER IF NOT EXISTS collection_job_deleted AFTER DELETE ON jobs BEGIN
                DELETE FROM collection_attempts WHERE job=OLD.id;
              END;
              CREATE TRIGGER IF NOT EXISTS collection_feed_deleted AFTER DELETE ON feeds BEGIN
                DELETE FROM collection_results WHERE feed=OLD.id;
                DELETE FROM collection_attempts WHERE feed=OLD.id;
              END;
            ''')

    def read(self, job: int) -> dict[str, Any]:
        row = self.db.execute('SELECT data FROM collection_attempts WHERE job=?', (job,)).fetchone()
        return json.loads(row[0]) if row else {}

    def start(self, job: Any, feed: str) -> dict[str, Any]:
        previous = self.read(job.id)
        data = dict(operation_id=f'{job.kind}:{job.item}', attempt_id=f'{job.id}:{job.generation}', kind=job.kind,
                    started_at=self.wall(), finished_at=None, elapsed_seconds=0.,
                    last_error=previous.get('last_error'))
        with self.db:
            self._write(job.id, feed, data)
        return data

    def _write(self, job: int, feed: str, data: dict[str, Any]) -> None:
        self.db.execute('INSERT INTO collection_attempts VALUES (?,?,?) ON CONFLICT(job) DO UPDATE SET feed=excluded.feed,data=excluded.data', (job, feed, json.dumps(data)))

    def finish(self, job: Any, feed: str, data: dict[str, Any], elapsed: float, observation: dict[str, Any]) -> None:
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job.id,)).fetchone()
        if row is None or row['generation'] != job.generation:
            return
        data.update(finished_at=self.wall(), elapsed_seconds=elapsed)
        completed = row['status'] == 'done' and not row['error']
        failure = bool(observation.get('error')) or bool(observation['requests'])
        if failure:
            details = observation.get('error') or {}
            message = details.get('message') or row['error'] or ''
            trace = details.get('traceback', '')
            kind = details.get('error_type', '')
            message, message_cut = clipped(message, 16384)
            trace, trace_cut = clipped(trace, 65536)
            requests = observation['requests'][:100]
            data['last_error'] = dict(attempt_id=data['attempt_id'], operation_id=data['operation_id'], occurred_at=timestamp(self.wall()),
                error_type=redact(kind), message=message, traceback=trace, source=details.get('origin', 'server'),
                message_truncated=message_cut, traceback_truncated=trace_cut, resolved=completed,
                requests=requests, request_count=observation['request_count'])
        elif row['status'] == 'done':
            if data.get('last_error'):
                data['last_error']['resolved'] = True
        with self.db:
            self._write(job.id, feed, data)
            result_row = self.db.execute('SELECT data FROM collection_results WHERE feed=?', (feed,)).fetchone()
            result = json.loads(result_row[0]) if result_row else {}
            if completed:
                result.update(success_at=self.wall(), duration=data['elapsed_seconds'])
            if data.get('last_error') and (not result.get('last_error') or data['last_error']['occurred_at'] >= result['last_error']['occurred_at']):
                result['last_error'] = data['last_error']
            if completed and result.get('last_error'):
                # Only a retry of the same operation resolves its error.
                if result['last_error']['operation_id'] == data['operation_id']:
                    result['last_error']['resolved'] = True
            self.db.execute('INSERT INTO collection_results VALUES (?,?) ON CONFLICT(feed) DO UPDATE SET data=excluded.data', (feed, json.dumps(result)))
            # Completed operations need only the single latest per-feed result.
            self.db.execute("DELETE FROM collection_attempts WHERE job IN (SELECT id FROM jobs WHERE status='done' AND error IS NULL) AND feed=?", (feed,))
            self._bound(feed)

    def _bound(self, feed: str) -> None:
        rows = self.db.execute('SELECT job,data FROM collection_attempts WHERE feed=?', (feed,)).fetchall()
        attempts = [(row['job'], json.loads(row['data'])) for row in rows]
        result_row = self.db.execute('SELECT data FROM collection_results WHERE feed=?', (feed,)).fetchone()
        result = json.loads(result_row[0]) if result_row else {}
        errors = [data['last_error'] for _, data in attempts if data.get('last_error')]
        if result.get('last_error'):
            errors.append(result['last_error'])
        unique = {error['attempt_id']: error for error in errors}
        requests = sorted(((error['attempt_id'], request) for error in unique.values() for request in error['requests']),
                          key=lambda value: value[1]['last_at'], reverse=True)[:100]
        retained: dict[str, list[dict[str, Any]]] = {}
        for attempt, request in requests:
            retained.setdefault(attempt, []).append(request)
        for error in errors:
            error['requests'] = retained.get(error['attempt_id'], [])
        for job, data in attempts:
            self._write(job, feed, data)
        if result:
            self.db.execute('UPDATE collection_results SET data=? WHERE feed=?', (json.dumps(result), feed))

    def interrupt(self, job: Any, feed: str, data: dict[str, Any], elapsed: float) -> None:
        data.update(finished_at=self.wall(), elapsed_seconds=elapsed)
        self._write(job.id, feed, data)

    def _snapshot_inputs(self, feeds: list[str], details: bool) -> tuple[list[Row], dict[str, dict[str, Any]]]:
        """Select only the requested feeds; summary never transfers diagnostic bodies."""
        jobs: list[Row] = []
        results: dict[str, dict[str, Any]] = {}
        attempt_fields = "a.data AS attempt_data" if details else """
            json_extract(a.data,'$.attempt_id') AS saved_attempt,
            json_extract(a.data,'$.started_at') AS started_at,
            json_extract(a.data,'$.finished_at') AS finished_at,
            json_extract(a.data,'$.elapsed_seconds') AS elapsed_seconds,
            json_extract(a.data,'$.last_error.resolved') AS error_resolved"""
        result_fields = "r.data AS result_data" if details else """
            json_extract(r.data,'$.success_at') AS success_at,
            json_extract(r.data,'$.duration') AS duration,
            json_extract(r.data,'$.last_error.resolved') AS error_resolved"""
        fields = 'j.id,j.kind,j.item,j.status,j.generation,j.next,j.error,j.queued_wall,j.problem_code'
        for start in range(0, len(feeds), 400):
            batch = feeds[start:start + 400]
            marks = ','.join('?' for _ in batch)
            discoveries = batch + ([''] if 'demo' in batch else [])
            discovery_marks = ','.join('?' for _ in discoveries)
            # Job kinds are disjoint, so these branches cannot duplicate an operation.
            selected = f"""SELECT {fields},CASE WHEN j.item='' THEN 'demo' ELSE j.item END AS feed
                FROM jobs j WHERE j.kind='discover' AND j.item IN ({discovery_marks})
                UNION ALL
                SELECT {fields},i.feed_id AS feed FROM jobs j JOIN items i ON i.id=j.item
                    WHERE j.kind='fetch' AND i.viewed=0 AND i.feed_id IN ({marks})
                UNION ALL
                SELECT {fields},i.feed_id AS feed FROM jobs j JOIN media_files m ON m.id=j.item
                    JOIN items i ON i.id=m.publication_id
                    WHERE j.kind='download_media' AND i.viewed=0 AND i.feed_id IN ({marks})"""
            jobs.extend(self.db.execute(f"""SELECT j.*,{attempt_fields} FROM ({selected}) j
                LEFT JOIN collection_attempts a ON a.job=j.id
                WHERE (j.status IN ('pending','running','failed','unavailable') OR j.status='done' AND j.error IS NOT NULL)
                    AND coalesce(j.problem_code,'')!='source-deleted'""", (discoveries + batch + batch)).fetchall())
            for row in self.db.execute(f"""SELECT f.id,d.updated,{result_fields} FROM feeds f
                LEFT JOIN collection_results r ON r.feed=f.id LEFT JOIN feed_details d ON d.feed=f.id
                WHERE f.id IN ({marks})""", batch):
                if details:
                    result = json.loads(row['result_data']) if row['result_data'] else {}
                else:
                    result = dict(
                        success_at=float(row['success_at']) if row['success_at'] is not None else None,
                        duration=float(row['duration']) if row['duration'] is not None else None,
                        last_error=dict(resolved=bool(int(row['error_resolved']))) if row['error_resolved'] is not None else None)
                if not result.get('success_at') and row['updated'] is not None:
                    result['success_at'] = row['updated']
                results[row['id']] = result
        return jobs, results

    def snapshot(self, feeds: list[dict[str, Any]], allowed: bool, reason: str,
                 active: dict[int, dict[str, Any]], *, details: bool = False) -> list[dict[str, Any]]:
        now, wall = self.clock(), self.wall()
        jobs, results = self._snapshot_inputs([feed['id'] for feed in feeds], details)
        groups: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            if details:
                data = json.loads(job['attempt_data']) if job['attempt_data'] else {}
            else:
                data = dict(
                    attempt_id=job['saved_attempt'],
                    started_at=float(job['started_at']) if job['started_at'] is not None else None,
                    finished_at=float(job['finished_at']) if job['finished_at'] is not None else None,
                    elapsed_seconds=float(job['elapsed_seconds']) if job['elapsed_seconds'] is not None else None,
                    last_error=dict(resolved=bool(int(job['error_resolved']))) if job['error_resolved'] is not None else None)
            running = active.get(job['id'])
            if job['status'] == 'done' and not job['error']:
                continue
            collecting = bool(running and running['generation'] == job['generation'] and allowed)
            wait = job['status'] == 'pending' and (job['next'] > now or bool(job['error']))
            state = 'collecting' if collecting else 'waiting' if wait else 'error' if job['status'] in ('done','failed','unavailable') and job['error'] else 'queued'
            if not allowed and state != 'error':
                state = 'paused'
            stage = (running['stage'] if running else '') or ('Поиск публикаций' if job['kind']=='discover' else 'Загрузка вложения' if job['kind']=='download_media' else 'Загрузка содержимого')
            elapsed = running['elapsed_seconds'] if collecting and running else data.get('elapsed_seconds') if state in ('paused','error') else max(0.,wall-data['finished_at']) if state=='waiting' and data.get('finished_at') else max(0.,wall-job['queued_wall']) if state=='queued' and job['queued_wall'] is not None else None
            groups.setdefault(job['feed'], []).append(dict(operation_id=f"{job['kind']}:{job['item']}", attempt_id=data.get('attempt_id'), kind='fetch' if job['kind']=='download_media' else job['kind'],
                state=state, stage=stage, reason=redact(reason if state=='paused' else job['error'] or ''),
                elapsed_seconds=elapsed, time_kind='attempt' if state in ('collecting','paused','error') else 'waiting' if state=='waiting' else 'queue',
                queued_at=timestamp(job['queued_wall']), started_at=timestamp(data.get('started_at')), finished_at=timestamp(data.get('finished_at')),
                retry_at=timestamp(wall+max(0.,job['next']-now)) if wait else None,
                last_error=data.get('last_error')))
        sources = []
        ranks = {'collecting':0,'error':1,'waiting':2,'queued':3,'paused':4,'deleting':5,'unavailable':6,'complete':7,'never':8}
        for feed in feeds:
            operations = groups.get(feed['id'], [])
            operations.sort(key=lambda op:(ranks[op['state']], -(op['elapsed_seconds'] or 0) if op['state']=='collecting' else op['retry_at'] or '', op['operation_id']))
            result = results.get(feed['id'], {})
            primary = operations[0] if operations else None
            state = primary['state'] if primary else 'complete' if result.get('success_at') else 'never'
            if feed['connection'] == 'deleting':
                state = 'deleting'
            elif feed['connection'] != 'connected':
                state = 'unavailable'
            sources.append(dict(feed_id=feed['id'], title=feed['title'], source_url=redact(next(iter(feed.get('original_urls') or []), '') or feed.get('url') or ''),
                source_kind=feed['kind'], access=feed.get('collection_access', feed.get('transport', feed['kind'])), state=state,
                operation=primary, operation_count=len(operations), error_count=sum(op['state']=='error' or bool(op['last_error'] and not op['last_error']['resolved']) for op in operations),
                waiting_count=sum(op['state']=='waiting' for op in operations), last_success_at=timestamp(result.get('success_at')),
                last_success_seconds=result.get('duration'), has_details=bool(operations or result.get('last_error')),
                history=feed.get('initial_history'), operations=operations, last_error=result.get('last_error')))
        sources.sort(key=lambda s:(ranks[s['state']], -(s['operation']['elapsed_seconds'] or 0) if s['state']=='collecting' else 0,
                                  s['operation']['retry_at'] or '9999' if s['state']=='waiting' and s['operation'] else '', s['title'].casefold()))
        return sources
