"""Canonical v3 response schemas, without starting Runtime or loading YAML."""
import json
from pathlib import Path
from typing import Any
from pydantic import TypeAdapter
from oko.reader.generated.models import ReadingComposition, CardPage, PublicationResponse, RuntimeStatus

CONTRACTS: dict[str, TypeAdapter[Any]] = {
    'navigation': TypeAdapter(ReadingComposition), 'cards': TypeAdapter(CardPage),
    'publication': TypeAdapter(PublicationResponse),
    'status': TypeAdapter(RuntimeStatus),
}


def schemas() -> dict[str, Any]:
    document = json.loads((Path(__file__).parent / 'generated/openapi.json').read_text())
    routes = {'navigation':'/api/v3/reading-composition', 'cards':'/api/v3/cards',
              'publication':'/api/v3/publication/{item}',
              'status':'/api/v3/status'}
    return {name: dict(document['paths'][route]['get']['responses']['200']['content']['application/json']['schema'],
                       components=document['components'], **{'$schema':'https://json-schema.org/draft/2020-12/schema'})
            for name, route in routes.items()}


if __name__ == '__main__':
    print(json.dumps(schemas(),ensure_ascii=False))
