"""One public captions request, JSON pipe only, no cookies or content files."""
import json
import logging
import re
import sys
import time
from email.utils import parsedate_to_datetime
import traceback
from urllib.parse import unquote, urlsplit
from oko.collection.youtube.access import CaptionsBlocked
from oko.diagnostics import redact
from oko.collection import request_observation as observation
from typing import Any


def check_response(response: Any, **kwargs: Any) -> Any:
    if response.status_code == 429:
        value = response.headers.get('Retry-After', '')
        try:
            delay = float(value) if value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = 0
        error = CaptionsBlocked(delay)
        setattr(error, 'response', response)
        raise error
    return response


def safe_details(error: Exception, proxy: str | None) -> dict[str, str]:
    def clean(value: str) -> str:
        if proxy:
            url = urlsplit(proxy)
            for secret in (url.username, url.password):
                if secret:
                    value = value.replace(secret, '[hidden]').replace(unquote(secret), '[hidden]')
        return redact(re.sub(r'https?://[^\s\"\'<>]+', '[url]', value))
    frames = traceback.extract_tb(error.__traceback__)
    location = f'{frames[-1].filename}:{frames[-1].lineno}:{frames[-1].name}' if frames else type(error).__name__
    message = clean(str(error))
    # No source lines or exception chains: either can embed credentials/URLs.
    trace = '\n'.join(f'{frame.filename}:{frame.lineno}:{frame.name}' for frame in frames)
    return dict(error_type=type(error).__name__, message=message, location=location,
                traceback=trace + '\n' + type(error).__name__ + ': ' + message)


def main() -> None:
    logging.disable(logging.CRITICAL)
    result: dict[str, Any]
    proxy = None
    capture = observation.Capture()
    try:
        from requests import Session
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnplayable
        config = json.loads(sys.stdin.read() or '{}')
        proxy = config.get('proxy')
        class ObservedSession(Session):
            def send(self, request: Any, **kwargs: Any) -> Any:
                try:
                    response = super().send(request, **kwargs)
                except Exception as error:
                    # The SDK/requests owns retries and redirects. Observe only
                    # a request that was actually attempted.
                    failed_response = getattr(error, 'response', None)
                    failed_url = str(getattr(getattr(error, 'request', None), 'url', '') or getattr(failed_response, 'url', '') or request.url)
                    capture.failed(failed_url, error, purpose='publication', method=request.method,
                                   original_url=request.url,
                                   http_status=getattr(failed_response, 'status_code', None))
                    raise
                if response.status_code >= 400:
                    from requests import HTTPError
                    status_error = HTTPError(f'{response.status_code} {response.reason}', response=response)
                    capture.failed(response.url, status_error, purpose='publication', method=request.method,
                                   original_url=request.url, http_status=response.status_code)
                return response

        with ObservedSession() as session:
            session.trust_env = False
            session.hooks['response'].append(check_response)
            options = {}
            if proxy:
                from youtube_transcript_api.proxies import GenericProxyConfig
                options['proxy_config'] = GenericProxyConfig(http_url=proxy, https_url=proxy)
            tracks = list(YouTubeTranscriptApi(http_client=session, **options).list(sys.argv[1]))
            tracks.sort(key=lambda track: (0 if track.language_code == 'ru' else 1 if track.language_code == 'en' else 2, track.is_generated))
            text = '\n'.join(snippet.text for snippet in tracks[0].fetch()) if tracks else None
        result = dict(text=text)
    except (TranscriptsDisabled, NoTranscriptFound):
        result = dict(text=None)
    except CaptionsBlocked as error:
        result = dict(error=True, blocked=True, retry_after=error.retry_after, **safe_details(error, proxy))
    except VideoUnplayable as error:
        if re.match(r'^This live event will begin\b', error.reason or '', re.IGNORECASE):
            result = dict(skip=True)
        else:
            result = dict(error=True, **safe_details(error, proxy))
    except Exception as error:
        if type(error).__name__ in ('RequestBlocked', 'IpBlocked'):
            result = dict(error=True, blocked=True, retry_after=0, **safe_details(error, proxy))
        else:
            result = dict(error=True, **safe_details(error, proxy))
    result.update(observation.worker_details(capture))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
