"""Project saved Telegraph articles without fetching or changing processing text."""
import json
import re
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


def saved_telegraph(header: dict[str, Any], base: str) -> dict[str, Any] | None:
    if not header.get('source_manifest'):
        return None
    manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest']))
    if not manifest.articles:
        return None
    post = str(manifest.post.get('text', ''))
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
