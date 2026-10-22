"""Project saved Telegraph articles without fetching or changing processing text."""
import json
import re
from urllib.parse import urljoin
from typing import Any

from oko.collection.contracts import SourceManifest
from oko.collection.telegram.telegraph import direct_articles


def error_block(url: str, reason: str) -> str:
    url = url.replace('<', '%3C').replace('>', '%3E')
    return '> [!load-error]\n>\n> <' + url + '>\n>\n> ' + reason.replace('\n', '\n> ')


def article_error(post: str, url: str, message: str) -> str:
    # Match complete original destinations, then compare their canonical identity.
    # Never insert into a URL's query/fragment or into a Markdown label.
    for match in re.finditer(r'https?://(?:www\.)?telegra\.ph/[^\s<>\])]+', post, re.I):
        if direct_articles(match[0]) != (url,):
            continue
        end = match.end()
        if post[end:end + 2] == '](':
            continue
        if post[end:end + 1] == '>':
            end += 1
        if post[end:end + 1] == ')':
            end += 1
        return post[:end] + '\n\n' + error_block(match[0], message) + '\n\n' + post[end:]
    return post + '\n\n' + error_block(url, message)


def preview_text(text: str) -> str:
    """Escape metadata formatting while keeping literal HTTP URLs intact."""
    def escape(value: str) -> str:
        return re.sub('([\\`*_{}\\[\\]()#+!<>|~])', lambda match: '\\' + match[0], value)
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


def saved_telegraph(header: dict[str, Any], base: str) -> dict[str, Any] | None:
    if not header.get('source_manifest'):
        return None
    manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest']))
    if not manifest.articles and not manifest.download_partial and manifest.preview_fragment is None:
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
    if manifest.download_partial:
        article_keys = {key for article in manifest.articles for key in article.resource_keys}
        for failed_resource in manifest.resources:
            if not failed_resource.reason or failed_resource.key in article_keys or failed_resource.status == 'external':
                continue
            member = failed_resource.key.split(':')[1] if failed_resource.key.startswith('telegram-web:') else ''
            url = urljoin(manifest.source_url or '', member) if member else manifest.source_url
            if url:
                reason = failed_resource.reason
                if failed_resource.status == 'loaded':
                    reason += '. Сохранённая копия вложения доступна.'
                post += '\n\n' + error_block(url, reason)
    resources = {r.key: r for r in manifest.resources}
    articles = []
    for article in manifest.articles:
        text = article.text or ''
        media_urls = []
        for key in article.resource_keys:
            resource = resources.get(key)
            if resource is None:
                continue
            marker = {'image': '[Изображение статьи]', 'video': '[Видео статьи]', 'audio': '[Аудио статьи]'}.get(resource.kind)
            replacement = ''
            if resource.status == 'loaded':
                media_urls.append(base + '/media/' + resource.name)
            elif resource.source_url:
                url = resource.source_url.replace('<', '%3C').replace('>', '%3E')
                replacement = '\n\n' + error_block(url, resource.reason or 'Не удалось загрузить вложение') + '\n\n'
            else:
                replacement = resource.reason or 'Не удалось загрузить вложение'
            if marker:
                text = text.replace(marker, replacement, 1)
        if article.text is None:
            message = 'Не удалось загрузить статью: ' + (article.reason or 'источник недоступен')
            post = article_error(post, article.url, message)
        articles.append(dict(source_url=article.url, title=article.title or 'Telegraph',
                             text=re.sub(r'\n{3,}', '\n\n', text).strip(),
                             error=article.reason or None, media_urls=media_urls))
    return dict(post_text=post, articles=articles)
