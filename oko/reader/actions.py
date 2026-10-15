"""Public reader action context without submitted values, prompts or credentials."""
import re
from typing import Any


def action_context(method: str, path: str, fields: list[str]) -> dict[str, Any] | None:
    if method not in ('POST', 'PUT', 'PATCH', 'DELETE') or not path.startswith('/api/'):
        return None
    if path.startswith(('/api/tabs/', '/api/debug', '/api/problems/')):
        return None
    field = fields[0] if len(fields) == 1 and re.fullmatch(r'[a-z_]{1,50}', fields[0]) else ''
    objects: list[dict[str, str]] = []
    action: dict[str, str] | None = None
    title = 'Не удалось выполнить действие'
    setting = re.fullmatch(r'/api/settings/(oko|folder|feed)/([\w-]+)', path)
    material = re.fullmatch(r'/api/materials/([\w-]+)/[\w/-]+', path)
    feed = re.fullmatch(r'/api/feeds/([\w-]+)(?:/[\w-]+)?', path)
    if setting:
        scope, owner = setting.groups()
        objects = [dict(kind='feed' if scope == 'feed' else 'settings', id=owner)]
        action = dict(kind='settings', page='field/' + field if field else 'home')
        if scope != 'oko':
            action.update(scope=scope, id=owner)
        title = 'Не удалось сохранить настройку'
    elif path.startswith('/api/processors/'):
        page = 'codex' if '/codex' in path else 'llamacpp'
        objects = [dict(kind='settings', id='global')]
        action = dict(kind='settings', page=page)
        title = 'Не удалось сохранить настройки обработчика' if method == 'PUT' else 'Не удалось проверить обработчик'
    elif path == '/api/youtube':
        objects = [dict(kind='settings', id='global')]
        action = dict(kind='settings', page='youtube')
        title = 'Не удалось сохранить настройки YouTube'
    elif material:
        objects = [dict(kind='material', id=material[1])]
        action = dict(kind='material', id=material[1])
        title = 'Не удалось выполнить действие с материалом'
    elif feed:
        objects = [dict(kind='feed', id=feed[1])]
        action = dict(kind='feed-settings', id=feed[1])
        title = 'Не удалось изменить подключение фида'
    elif path == '/api/collect':
        action = dict(kind='collect')
        title = 'Не удалось обновить источники'
    elif path.startswith(('/api/feeds', '/api/telegram', '/api/import')):
        action = dict(kind='add', page='telegram' if '/telegram' in path else 'rss')
        title = 'Не удалось подключить источник'
    elif path.startswith('/api/folders/'):
        title = 'Не удалось выполнить действие с папкой'
    elif path == '/api/stop':
        title = 'Не удалось подтвердить остановку Око'
    else:
        return None
    return dict(identity=method + ':' + path + ':' + field, title=title, objects=objects,
                action=action, discardable=bool(setting or method == 'PUT' and path.startswith(('/api/processors/', '/api/youtube'))))
