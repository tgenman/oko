"""One public captions request, JSON pipe only, no cookies or content files."""
import json
import logging
import re
import sys
from oko.diagnostics import exception_details
from typing import Any


def main() -> None:
    logging.disable(logging.CRITICAL)
    result: dict[str, Any]
    try:
        from requests import Session
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnplayable
        with Session() as session:
            session.trust_env = False
            tracks = list(YouTubeTranscriptApi(http_client=session).list(sys.argv[1]))
            tracks.sort(key=lambda track: (0 if track.language_code == 'ru' else 1 if track.language_code == 'en' else 2, track.is_generated))
            text = '\n'.join(snippet.text for snippet in tracks[0].fetch()) if tracks else None
        result = dict(text=text)
    except (TranscriptsDisabled, NoTranscriptFound):
        result = dict(text=None)
    except VideoUnplayable as error:
        if re.match(r'^This live event will begin\b', error.reason or '', re.IGNORECASE):
            result = dict(skip=True)
        else:
            result = dict(error=True, **exception_details(error))
    except Exception as error:
        result = dict(error=True, **exception_details(error))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
