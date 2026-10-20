"""One page of a bounded RSS history walk; the caller owns I/O and commits."""
import time
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from oko.collection.rss import Entry, normalized
from oko.collection.contracts import SourceError

ATOM = '{http://www.w3.org/2005/Atom}'
BASE = '{http://www.w3.org/XML/1998/namespace}base'
ORDER_WARNING = 'Порядок RSS неоднозначен: точность выбора последних N не подтверждена'
DATE_WARNING = 'RSS: полнота истории за период не подтверждена'


class RSSHistory:
    def __init__(self, url: str, selection: dict[str, Any], previous: dict[str, Any] | None) -> None:
        self.selection = selection
        run = selection.get('run', 'initial')
        saved = previous.get('rss_history') if previous else None
        self.state: dict[str, Any] = saved if saved and saved['run'] == run else dict(
            run=run, boundary=time.time(), pending=[dict(url=url, path=[])], visited=[], selected=[],
            seen=[], seen_urls=[], identities={}, requests=0, stop_reason=None, archive=False, last_date=None, warnings=[], error=None)
        if selection.get('cancelled'):
            self.finish('cancelled')

    @property
    def complete(self) -> bool:
        return self.state['stop_reason'] is not None or bool(self.selection.get('cancelled'))

    @property
    def requests(self) -> int:
        return int(self.state['requests'])

    @property
    def url(self) -> str:
        return str(self.state['pending'][0]['url'])

    @property
    def warnings(self) -> list[str]:
        return list(self.state['warnings'])

    def warn(self, message: str) -> None:
        if message not in self.state['warnings']:
            self.state['warnings'].append(message)

    def finish(self, reason: str) -> None:
        self.state['stop_reason'] = reason
        self.state['pending'] = []

    def fail(self, message: str) -> None:
        self.state['requests'] += 1
        self.state['error'] = message
        self.finish('request_failed')

    def updates(self, batch: list[Entry]) -> list[Entry]:
        selected, seen = set(self.state['selected']), set(self.state['seen'])
        urls = set(self.state['seen_urls'])
        current = [self.identity(e) for e in batch]
        return [e for e in current if e.publication.id in selected or (e.publication.id not in seen and e.publication.url not in urls)]

    def identity(self, entry: Entry) -> Entry:
        p = entry.publication
        previous_url = self.state['identities'].get(p.id)
        return replace(entry, publication=replace(p, id=p.url)) if previous_url and previous_url != p.url else entry

    def accept(self, batch: list[Entry], raw: bytes, final: str) -> list[Entry]:
        frame = self.state['pending'].pop(0)
        path = frame['path']
        self.state['requests'] += 1
        final = normalized(final)
        if final in path or frame['url'] in path:
            self.finish('cycle')
            return []
        if final in self.state['visited']:
            if not self.state['pending']:
                self.finish('archive_end' if self.state['archive'] else 'no_continuation')
            return []
        self.state['visited'].extend(dict.fromkeys([frame['url'], final]))
        path = list(dict.fromkeys([*path, frame['url'], final]))
        chosen = []
        seen, selected = set(self.state['seen']), set(self.state['selected'])
        seen_urls = set(self.state['seen_urls'])
        if self.selection['mode'] == 'date':
            self.warn(DATE_WARNING)
        for supplied in batch:
            entry = self.identity(supplied)
            p = entry.publication
            if p.id in seen or p.url in seen_urls:
                continue
            seen.add(p.id)
            seen_urls.add(p.url)
            self.state['identities'][p.id] = p.url
            if p.published_known and p.published > self.state['boundary']:
                chosen.append(entry)  # New arrivals do not consume history N.
                continue
            if not p.published_known:
                self.warn(ORDER_WARNING)
            else:
                last = self.state['last_date']
                if last is not None and p.published > last:
                    self.warn(ORDER_WARNING)
                self.state['last_date'] = p.published
                if self.selection['mode'] == 'date' and p.published < self.selection['since']:
                    self.finish('date_boundary')
                    break
            selected.add(p.id)
            self.state['selected'].append(p.id)
            chosen.append(entry)
            if self.selection['mode'] == 'count' and len(selected) >= self.selection['count']:
                self.finish('count_reached')
                break
        chosen_ids = {e.publication.id for e in chosen}
        for entry in batch:
            p = entry.publication
            if p.published_known and p.published > self.state['boundary'] and p.id not in chosen_ids:
                chosen.append(entry)
                chosen_ids.add(p.id)
        self.state['seen'] = list(seen | {e.publication.id for e in batch})
        self.state['seen_urls'] = list(seen_urls | {e.publication.url for e in batch})
        if not self.complete:
            root = ET.fromstring(raw)  # entries() already rejected unsafe/malformed XML.
            parent = root if root.tag == ATOM + 'feed' else next((c for c in root if c.tag.rsplit('}', 1)[-1] == 'channel'), root)
            base = urljoin(urljoin(final, root.get(BASE, '')), parent.get(BASE, '') if parent is not root else '')
            links: dict[str, list[str]] = {'next': [], 'prev-archive': []}
            for link in parent:
                if link.tag != ATOM + 'link' or link.get('rel') not in links or not link.get('href'):
                    continue
                try:
                    target = normalized(urljoin(urljoin(base, link.get(BASE, '')), link.attrib['href']))
                except SourceError as error:
                    self.state['error'] = error.label
                    self.finish('request_failed')
                    return chosen
                targets = links[link.attrib['rel']]
                if target not in targets:
                    targets.append(target)
            if links['next']:
                self.warn('Страницы RSS могут меняться во время обхода: точность выбора последних N не подтверждена')
            if any(len(targets) > 1 for targets in links.values()):
                self.finish('ambiguous_continuation')
            else:
                self.state['archive'] |= bool(links['prev-archive'])
                targets = list(dict.fromkeys(links['next'] + links['prev-archive']))
                if any(target in path for target in targets):
                    self.finish('cycle')
                else:
                    pending = self.state['pending']
                    # A direct continuation takes precedence over a deferred archive.
                    new = [dict(url=target, path=path) for target in targets if target not in self.state['visited']]
                    pending[:] = new + [f for f in pending if f['url'] not in targets and f['url'] not in self.state['visited']]
                    if not pending:
                        self.finish('archive_end' if self.state['archive'] else 'no_continuation')
                    elif self.requests >= 1000:
                        self.finish('page_limit')
        return chosen

    def cursor(self) -> dict[str, Any]:
        return dict(rss_history=self.state, history_enroll=self.state['selected'], scan_complete=self.complete,
                    stop_reason=self.state['stop_reason'], warnings=self.warnings,
                    history_error=self.state['error'], history_requests=self.requests, since=self.state['boundary'])
