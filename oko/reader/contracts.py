"""Reader wire contracts derived solely from the canonical OpenAPI library.

Export JSON Schema 2020-12 snapshots without starting Runtime or reading YAML.
"""
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from oko.reader.legacy_generated.models import (
    Card as Card, Material as Material, Navigation as Navigation,
    Status as Status, Version as Version,
)

CONTRACTS: dict[str, TypeAdapter[Any]] = {
    'navigation': TypeAdapter(Navigation), 'cards': TypeAdapter(list[Card]),
    'material': TypeAdapter(Material), 'version': TypeAdapter(Version),
    'status': TypeAdapter(Status),
}


def schemas() -> dict[str, Any]:
    document = json.loads((Path(__file__).parent / 'legacy_generated/openapi.json').read_text())
    routes = {'navigation': '/api/navigation', 'cards': '/api/cards',
              'material': '/api/materials/{item}',
              'version': '/api/materials/{item}/versions/{number}',
              'status': '/api/status'}
    return {name: dict(document['paths'][route]['get']['responses']['200']['content']['application/json']['schema'],
                       components=document['components'],
                       **{'$schema': 'https://json-schema.org/draft/2020-12/schema'})
            for name, route in routes.items()}


if __name__ == '__main__':
    print(json.dumps(schemas(), ensure_ascii=False))
