"""Small dependency guard; behavioral SQL/HTTP tests remain the main evidence."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]/'oko'


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node,ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom):
            yield node.module or ''


def test_services_do_not_own_jobs_adapters_or_commits():
    violations=[]
    for path in (ROOT/'services').glob('*.py'):
        modules=list(imports(path))
        for module in modules:
            if module.startswith(('oko.workers','oko.application','oko.storage.job_repository')):
                violations.append(f'{path.name}: import {module}')
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('commit','rollback','execute','executemany','executescript'):
                violations.append(f'{path.name}:{node.lineno}: {node.func.attr}')
        assert not any(isinstance(n,ast.Name) and n.id=='Job' for n in ast.walk(ast.parse(path.read_text())))
    assert not violations,violations


def test_workers_use_services_and_job_repository_without_entity_sql():
    violations=[]
    allowed={'oko.storage.store','oko.storage.job_repository','oko.storage.job_types','oko.storage.media'}
    for path in (ROOT/'workers').glob('*.py'):
        for module in imports(path):
            if module.startswith('oko.storage.') and module not in allowed:
                violations.append(f'{path.name}: {module}')
            if module.startswith(('oko.reader','oko.application')):
                violations.append(f'{path.name}: {module}')
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Attribute) and (node.attr in ('execute','executemany','executescript','commit','rollback','db') or node.attr in ('publications','subscriptions','read_sync','youtube') and ast.unparse(node.value) in ('store','current','self._store','self.execution._store')):
                violations.append(f'{path.name}:{node.lineno}: {node.attr}')
    assert not violations,violations


def test_storage_has_no_application_or_http_imports_and_no_runtime_facade():
    violations=[]
    for path in (ROOT/'storage').glob('*.py'):
        violations.extend(f'{path.name}: {module}' for module in imports(path) if module.startswith(('oko.application','oko.workers','oko.reader','fastapi')))
    assert not violations,violations
    assert not (ROOT/'reader'/'runtime.py').exists()
    assert not (ROOT/'application'/'jobs.py').exists()
    tree=ast.parse((ROOT/'host.py').read_text())
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('next_job','prepare','accept','finish','reconcile') for n in ast.walk(tree))
