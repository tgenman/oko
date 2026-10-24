"""Compile the repository's small SQL dialect to native PostgreSQL statements.

Values stay bound parameters. Quoted literals, identifiers and comments are
lexed before rewriting; this is deliberately not a general SQL transpiler.
DDL and trigger installation are handled by immutable native migrations.
"""
from collections.abc import Mapping
import re
from functools import lru_cache

_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/|:[A-Za-z_]\w*|\?|[A-Za-z_]\w*|\d+(?:\.\d+)?|<=|>=|!=|<>|\|\||\S")
_NAME = re.compile(r'[A-Za-z_]\w*\Z')


class Queries:
    def __init__(self, columns: Mapping[str, frozenset[str]]) -> None:
        self.columns = columns
        self.compile = lru_cache(maxsize=2048)(self._compile)

    def _compile(self, query: str) -> str:
        tokens = [token for token in _TOKEN.findall(query) if not token.startswith(('--', '/*'))]
        if not tokens:
            raise ValueError('Empty storage query')
        if any(token == ';' for token in tokens[:-1]):
            raise ValueError('One repository statement is required')
        upper = [token.upper() for token in tokens]
        if upper[0] in ('BEGIN','COMMIT','ROLLBACK','SAVEPOINT','RELEASE','END'):
            raise ValueError('Use the owned transaction interface')
        if upper[0] not in ('SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE'):
            raise ValueError('Repository queries cannot install schema or control transactions')
        if upper[0] == 'WITH' and any(token in ('INSERT','UPDATE','DELETE') for token in upper[1:]):
            raise ValueError('Data-changing CTEs must use an explicit repository write')
        ignore = upper[:3] == ['INSERT', 'OR', 'IGNORE']
        replace = upper[:3] == ['INSERT', 'OR', 'REPLACE']
        if ignore or replace:
            del tokens[1:3]
        output = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            keyword = token.upper()
            if token == '?':
                output.append('%s')
            elif token.startswith(':'):
                output.append('%(' + token[1:] + ')s')
            elif keyword == 'IS':
                negated = index + 1 < len(tokens) and tokens[index + 1].upper() == 'NOT'
                next_index = index + 2 if negated else index + 1
                following = tokens[next_index].upper() if next_index < len(tokens) else ''
                if following in ('NULL', 'TRUE', 'FALSE', 'UNKNOWN'):
                    output.append(token)
                else:
                    output.extend(('IS', 'DISTINCT', 'FROM') if negated else ('IS', 'NOT', 'DISTINCT', 'FROM'))
                    index += int(negated)
            elif keyword == 'JSON_EXTRACT':
                output.append('oko_json_extract')
            elif keyword == 'JSON_EACH':
                output.append('oko_json_each')
            elif keyword == 'MAX' and index + 1 < len(tokens) and tokens[index + 1] == '(':
                depth, commas = 0, 0
                for part in tokens[index + 1:]:
                    depth += int(part == '(') - int(part == ')')
                    if depth == 0:
                        break
                    commas += int(part == ',' and depth == 1)
                output.append('oko_max' if commas else token)
            else:
                # Psycopg scans percent signs even inside bound-query literals.
                output.append(token.replace('%', '%%'))
            index += 1
        if output[-1] == ';':
            output.pop()
        if ignore:
            output.extend(('ON', 'CONFLICT', 'DO', 'NOTHING'))
        if replace:
            table = output[2]
            fields = {'state': ('key', 'value'), 'reader_action_superseded': ('sequence', 'completed')}.get(table)
            if fields is None:
                raise ValueError('Unsupported replacement table: ' + table)
            output.extend(('ON', 'CONFLICT', '(', fields[0], ')', 'DO', 'UPDATE', 'SET', fields[1], '=', 'excluded', '.', fields[1]))
        self._upsert_columns(output)
        self._cross_json_join(output)
        index = 0
        while index + 2 < len(output):
            if output[index].upper() == 'CROSS' and output[index + 1].upper() == 'JOIN':
                tail = [part.upper() for part in output[index + 2:]]
                boundary = next((offset for offset, part in enumerate(tail) if part in ('JOIN','WHERE','UNION')), len(tail))
                if 'ON' in tail[:boundary]:
                    output.pop(index)
            index += 1
        return ' '.join(output)

    def _upsert_columns(self, tokens: list[str]) -> None:
        upper = [token.upper() for token in tokens]
        if upper[0] != 'INSERT' or 'CONFLICT' not in upper or 'UPDATE' not in upper:
            return
        target = tokens[upper.index('INTO') + 1]
        columns = self.columns.get(target, frozenset())
        start = upper.index('UPDATE', upper.index('CONFLICT')) + 2
        # SET assignment targets remain unqualified. All bare RHS columns belong
        # to the INSERT target; source query scopes precede this conflict clause.
        assignment = True
        depth = 0
        index = start
        while index < len(tokens):
            token = tokens[index]
            if token == '(':
                depth += 1
            elif token == ')':
                depth -= 1
            elif depth == 0 and token == ',':
                assignment = True
            elif token == '=' and assignment:
                assignment = False
            elif depth == 0 and token.upper() == 'WHERE':
                assignment = False
            if not assignment and token in columns and _NAME.fullmatch(token):
                previous = tokens[index - 1] if index else ''
                following = tokens[index + 1] if index + 1 < len(tokens) else ''
                if previous != '.' and following not in ('.', '('):
                    tokens[index:index + 1] = [target, '.', token]
                    index += 2
            index += 1

    @staticmethod
    def _cross_json_join(tokens: list[str]) -> None:
        # SQLite accepts JOIN table_function without ON; PostgreSQL spells it CROSS JOIN.
        index = 0
        while index + 2 < len(tokens):
            if tokens[index].upper() == 'JOIN' and tokens[index + 1] == 'oko_json_each':
                end, depth = index + 2, 0
                while end < len(tokens):
                    depth += int(tokens[end] == '(') - int(tokens[end] == ')')
                    end += 1
                    if depth == 0:
                        break
                if end < len(tokens) and tokens[end].upper() == 'AS':
                    end += 1
                if end < len(tokens) and _NAME.fullmatch(tokens[end]) and tokens[end].upper() not in ('ON', 'WHERE', 'JOIN'):
                    end += 1
                if end == len(tokens) or tokens[end].upper() != 'ON':
                    tokens.insert(index, 'CROSS')
                    index += 1
            index += 1
