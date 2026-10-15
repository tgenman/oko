"""Remaining v2 bridge, excluding the deliberately retired OPML surface."""
import json
from pathlib import Path


def document(path: Path) -> dict:
    value = json.loads(path.read_text())
    for route in ('/api/import', '/api/import/preview', '/api/telegram/topics'):
        value['paths'].pop(route, None)
    schemas = value['components']['schemas']
    removed = {name for name in schemas if name.startswith('Import') or name == 'TelegramTopic'}
    for name in removed:
        del schemas[name]
    schemas['ConnectTelegram']['properties'].pop('topic_id', None)
    schemas['SubscriptionFeed']['properties'].pop('telegram_topic', None)
    def prune(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in ('anyOf', 'oneOf'):
                    node[key] = [part for part in child if part.get('$ref', '').split('/')[-1] not in removed]
                else:
                    prune(child)
        elif isinstance(node, list):
            for child in node:
                prune(child)
    prune(value)
    return value


if __name__ == '__main__':
    import sys
    print(json.dumps(document(Path(sys.argv[1])), ensure_ascii=False, sort_keys=True, separators=(',', ':')))
