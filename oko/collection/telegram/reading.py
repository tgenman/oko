"""Project saved Telegraph articles without fetching or changing processing text."""
import json
import re
from typing import Any

from oko.collection.contracts import SourceManifest


def error_block(url: str, reason: str) -> str:
    url = url.replace('<', '%3C').replace('>', '%3E')
    return '> [!load-error]\n>\n> <' + url + '>\n>\n> ' + reason.replace('\n', '\n> ')


def preview_text(text: str) -> str:
    """Escape metadata formatting while keeping literal HTTP URLs intact."""
    def escape(value: str) -> str:
        return ''.join('\\' + char if char in '\\`*_{}[]()#+.!<>|~-' else char for char in value)
    parts = []
    end = 0
    for match in re.finditer(r'https?://[^\s<>\[\]]+', text):
        url = match[0]
        while url and (url[-1] in '.,;!…' or url[-1] == ')' and url.count(')') > url.count('(')):
            url = url[:-1]
        parts.append(escape(text[end:match.start()]))
        parts.append('<' + url + '>')
        end = match.start() + len(url)
    parts.append(escape(text[end:]))
    return ''.join(parts)


def saved_preview(header: dict[str, Any]) -> dict[str, Any] | None:
    if not header.get('source_manifest'):
        return None
    manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest']))
    if manifest.preview_fragment is None:
        return None
    post = str(manifest.post.get('text', ''))
    if manifest.preview_fragment is not None:
        note = 'Фрагмент предпросмотра Telegram — неполная копия. Полный текст, ссылки и вложения могут быть доступны в Telegram.'
        if manifest.preview_fragment.endswith('…'):
            note += ' Фрагмент обрезан.'
        post += '\n\n> ' + note
        if manifest.source_url:
            post += '\n>\n> [Открыть пост в Telegram](' + manifest.source_url + ')'
        if manifest.preview_fragment:
            # Metadata is plain text: do not interpret it as authored Markdown.
            post += '\n\n' + preview_text(manifest.preview_fragment)
        if manifest.preview_error and manifest.source_url:
            post += '\n\n' + error_block(manifest.source_url, manifest.preview_error)
    return dict(post_text=post,articles=[])
