"""A thread mutex is safe for asyncio projections only while they never suspend."""
import ast
from pathlib import Path


def test_protected_sql_scopes_do_not_suspend():
    violations = []
    protected = 0

    def name(node):
        if isinstance(node, ast.Call):
            return name(node.func)
        return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''

    for path in (Path(__file__).resolve().parents[2] / 'oko').rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            bodies = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    name(decorator) in ('read_locked', 'repository_access') for decorator in node.decorator_list):
                bodies = node.body
            elif isinstance(node, ast.With) and any(name(item.context_expr) == 'transaction' for item in node.items):
                bodies = node.body
            if bodies:
                protected += 1
                if any(isinstance(child, (ast.Await, ast.AsyncWith, ast.AsyncFor)) for body in bodies for child in ast.walk(body)):
                    violations.append(f'{path.name}:{node.lineno}')
    assert protected > 0
    assert not violations, 'Protected SQL scopes must not suspend: ' + ', '.join(violations)
