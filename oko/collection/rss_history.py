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


def continuation_links(raw: bytes, final: str) -> dict[str, list[str]]:
    # entries() has already rejected unsafe/malformed XML. Resolve every level
    # inside the caller's terminal page-error boundary, including xml:base.
    root = ET.fromstring(raw)
    parent = root if root.tag == ATOM + 'feed' else next((c for c in root if c.tag.rsplit('}', 1)[-1] == 'channel'), root)
    base = urljoin(urljoin(final, root.get(BASE, '')), parent.get(BASE, '') if parent is not root else '')
    links: dict[str, list[str]] = {'next': [], 'prev-archive': []}
    for link in parent:
        if link.tag != ATOM + 'link' or link.get('rel') not in links or not link.get('href'):
            continue
        target = normalized(urljoin(urljoin(base, link.get(BASE, '')), link.attrib['href']))
        targets = links[link.attrib['rel']]
        if target not in targets:
            targets.append(target)
    return links
