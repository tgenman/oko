"""Discover direct Telegraph links for independent linked publications."""
import re
from urllib.parse import quote,unquote,urlsplit


def direct_articles(text: str) -> tuple[str, ...]:
    result = []
    for match in re.finditer(r'https?://(?:www\.)?telegra\.ph/[^\s<>\])]+',text,re.I):
        address = urlsplit(match[0].rstrip('.,;!'))
        if address.username or address.password or address.port or not address.path.strip('/') or address.path.startswith('/file/'):
            continue
        url = 'https://telegra.ph/'+quote(unquote(address.path.lstrip('/')),safe='/-_')
        if url not in result:
            result.append(url)
    return tuple(result)
