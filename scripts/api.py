#!/usr/bin/env python3
"""Validate/bundle the canonical contract or serve a reader-free local viewer."""
import argparse
import json
from pathlib import Path
import sys

import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]


def validate_examples(document: dict) -> None:
    """Require runnable literal examples before bundling or generating libraries."""
    def check(schema: dict, examples: object, location: str) -> None:
        if not isinstance(examples, list) or not examples:
            raise ValueError(f'Missing examples: {location}')
        # Keep local references rooted in the complete contract, including for
        # inline response schemas. External references were rejected above.
        validator = Draft202012Validator(dict(document, **schema), format_checker=FormatChecker())
        for index, value in enumerate(examples):
            try:
                validator.validate(value)
            except ValidationError as error:
                path = '/'.join(str(segment) for segment in error.absolute_path) or '$'
                raise ValueError(f'Invalid example: {location}, example {index}, path {path} ({error.validator})') from None

    for name, schema in document.get('components', {}).get('schemas', {}).items():
        pointer = name.replace('~', '~0').replace('/', '~1')
        check({'$ref': '#/components/schemas/' + pointer}, schema.get('examples'), f'schema {name}')
    for route, methods in document['paths'].items():
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            for status, response in operation['responses'].items():
                for media, content in response.get('content', {}).items():
                    schema = content['schema']
                    if schema.get('format') == 'binary':
                        continue
                    location = f'{method.upper()} {route} response {status} {media}'
                    if 'example' in content:
                        examples = [content['example']]
                    else:
                        entries = content.get('examples', {})
                        if any('value' not in entry for entry in entries.values()):
                            raise ValueError(f'Literal examples required: {location}')
                        examples = [entry['value'] for entry in entries.values()]
                    check(schema, examples, location)


def contract(path: Path = ROOT / 'api/openapi.yaml') -> dict:
    document = yaml.load(path.read_text(), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
    def local_references(value):
        if isinstance(value, dict):
            for keyword in ('$ref', '$dynamicRef', '$recursiveRef'):
                if keyword not in value:
                    continue
                reference = value[keyword]
                if not isinstance(reference, str) or not reference.startswith('#/'):
                    raise ValueError('External contract references are not supported')
                target = document
                try:
                    for part in reference[2:].split('/'):
                        key = part.replace('~1', '/').replace('~0', '~')
                        target = target[int(key)] if isinstance(target, list) else target[key]
                except (KeyError, IndexError, TypeError, ValueError) as error:
                    raise ValueError(f'Unresolved local contract reference: {reference}') from error
            for child in value.values():
                local_references(child)
        elif isinstance(value, list):
            for child in value:
                local_references(child)
    local_references(document)
    validate(document)
    identities = []
    for route, methods in document['paths'].items():
        if not route.startswith('/api/'):
            raise ValueError(f'Not a reader API route: {route}')
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            identities.append(operation['operationId'])
            for status, response in operation['responses'].items():
                for media, content in response.get('content', {}).items():
                    schema = content['schema']
                    if schema.get('type') == 'object' and not schema.get('properties') and not schema.get('additionalProperties'):
                        raise ValueError(f'Unspecified response: {method} {route} {status} {media}')
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate operationId')
    validate_examples(document)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'bundle', 'docs'])
    parser.add_argument('--spec', type=Path, default=ROOT / 'api/openapi.yaml')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--assets', type=Path, default=ROOT / 'node_modules/swagger-ui-dist')
    args = parser.parse_args()
    document = contract(args.spec)
    if args.command == 'bundle':
        data = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(data)
        else:
            print(data, end='')
    elif args.command == 'validate':
        print(f"Valid OpenAPI {document['openapi']}: {len(document['paths'])} routes")
    else:
        sys.path.insert(0, str(ROOT))
        from api.viewer import serve
        serve(document, args.assets, args.port)


if __name__ == '__main__':
    main()
