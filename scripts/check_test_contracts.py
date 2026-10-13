#!/usr/bin/env python3
"""Check test Codex overrides against the production protocol without importing tests."""
import argparse
import ast
import inspect
import os
from pathlib import Path
import subprocess
import sys
from task_changes import read_file


def signature(node):
    parameters = []
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    required = len(positional) - len(args.defaults)
    for i, arg in enumerate(positional):
        kind = inspect.Parameter.POSITIONAL_ONLY if i < len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
        parameters.append(inspect.Parameter(arg.arg, kind, default=inspect.Parameter.empty if i < required else None))
    if args.vararg:
        parameters.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parameters.append(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if default is None else None))
    if args.kwarg:
        parameters.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(parameters)


def check(sources):
    trees = {name: ast.parse(content, filename=name) for name, content in sources.items()}
    protocol = trees.get('oko/processing/codex.py')
    if protocol is None:
        return []  # Small standalone hook-test repos do not contain this product.
    node = next(n for n in ast.walk(protocol) if isinstance(n, ast.ClassDef) and n.name == 'CodexClient')
    contracts = {n.name: signature(n) for n in node.body if isinstance(n, ast.AsyncFunctionDef)}
    failures = []
    modules = {name: name[:-3].replace('/', '.').removesuffix('.__init__') for name in trees}
    aliases = {}
    declarations = []
    for name, tree in trees.items():
        module = modules[name]
        symbols = {}
        for imported in ast.walk(tree):
            if isinstance(imported, ast.ImportFrom):
                target = imported.module or ''
                if imported.level:
                    package = module if name.endswith('/__init__.py') else module.rsplit('.', 1)[0]
                    prefix = package.split('.')[:len(package.split('.')) - imported.level + 1]
                    target = '.'.join([*prefix, target]).rstrip('.')
                for alias in imported.names:
                    symbols[alias.asname or alias.name] = target + '.' + alias.name
                if 'tests/support/app.py' in trees and name.startswith('tests/') and name != 'tests/support/app.py' and imported.module == 'oko.reader.app' and any(n.name == 'create_app' for n in imported.names):
                    failures.append(f'{name}:{imported.lineno}: use tests.support.app.create_app for synthetic defaults')
            elif isinstance(imported, ast.Import):
                for alias in imported.names:
                    symbols[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
        aliases[module] = symbols
        declarations.extend((name, module, n) for n in ast.walk(tree) if isinstance(n, ast.ClassDef))

    def reference(module, base):
        if isinstance(base, ast.Name):
            return aliases[module].get(base.id, module + '.' + base.id)
        if isinstance(base, ast.Attribute):
            return reference(module, base.value) + '.' + base.attr
        if isinstance(base, ast.Subscript):
            return reference(module, base.value)
        return ''

    derived = {'oko.processing.codex.CodexClient', 'tests.support.codex.CodexService'}
    while True:
        discovered = {module + '.' + cls.name for _, module, cls in declarations if any(reference(module, base) in derived for base in cls.bases)}
        if discovered <= derived:
            break
        derived.update(discovered)
    for name, module, cls in declarations:
        if not name.startswith('tests/') or module + '.' + cls.name not in derived:
            continue
        for method in cls.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.name not in contracts:
                continue
            expected = contracts[method.name]
            actual = signature(method)
            names = list(expected.parameters)
            try:
                actual.bind(*[None for _ in names])
                actual.bind(None, **{key: None for key in names[1:]})
                if not isinstance(method, ast.AsyncFunctionDef):
                    raise TypeError('method must be async')
            except TypeError as error:
                failures.append(f'{name}:{method.lineno}: {cls.name}.{method.name}{actual} incompatible with CodexClient{expected}: {error}')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    try:
        root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
        command = ['git', 'ls-files', '-z'] if args.staged else ['git', 'ls-files', '-co', '--exclude-standard', '-z']
        files = subprocess.check_output(command, cwd=root).split(b'\0')
        sources = {}
        for raw in set(files) - {b''}:
            name = os.fsdecode(raw)
            if name == 'oko/processing/codex.py' or name.startswith('tests/') and name.endswith('.py'):
                if args.staged:
                    sources[name] = subprocess.check_output(['git', 'show', ':' + name], cwd=root).decode()
                else:
                    value = read_file(root, name)
                    if value is not None:
                        if value[0] != 'file':
                            raise OSError('Contract source must be a regular Git file: ' + name)
                        sources[name] = value[2].decode()
        failures = check(sources)
        for failure in failures:
            print(failure, file=sys.stderr)
        return bool(failures)
    except (OSError, subprocess.CalledProcessError, SyntaxError, StopIteration) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
