#!/usr/bin/env python3
"""Prepare, reconcile and verify issue exports; GitHub MCP performs the API calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit


class ExportError(ValueError):
    pass


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path.is_symlink():
        raise ExportError('Output must not be a symlink')
    if path.exists():
        if path.read_text() == payload:
            return
        raise ExportError('Output already exists; choose a new result path: ' + str(path))
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(payload)
            file.close()
            # Publish a complete file without replacing another run's evidence.
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def metadata(body, name):
    found = re.findall(r'^' + re.escape(name) + r':\s*(.+)$', body, re.M)
    if len(found) > 1:
        raise ExportError('Duplicate metadata: ' + name)
    return found[0].strip() if found else None


def issue_number(url, repo):
    match = re.fullmatch(r'https://github\.com/' + re.escape(repo) + r'/issues/([1-9][0-9]*)', url)
    if not match:
        raise ExportError('Expected an issue URL in ' + repo)
    return int(match[1])


def rewrite_links(body, resolve):
    """Rewrite Markdown destinations while preserving code and list indentation."""
    def label(value):
        return ' '.join(value.split()).casefold()

    def lines():
        fence, fence_indent = None, 0
        list_indents, indented, previous_blank = [], False, True
        for line in body.splitlines(keepends=True):
            expanded = line.expandtabs(4)
            indent = len(expanded) - len(expanded.lstrip(' '))
            marker = re.match(r'^ *(`{3,}|~{3,})', expanded)
            if fence is not None:
                if marker and indent <= fence_indent + 3 and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not expanded[marker.end():].strip():
                    fence = None
                yield line, True
                previous_blank = True
                continue
            if not expanded.strip():
                yield line, indented
                previous_blank = True
                continue
            while list_indents and indent < list_indents[-1]:
                list_indents.pop()
            content_indent = list_indents[-1] if list_indents else 0
            if marker and indent - content_indent <= 3:
                fence, fence_indent = marker[1], content_indent
                yield line, True
                continue
            item = re.match(r'^ *(?:[-+*]|[0-9]+[.)]) +', expanded)
            if item and indent - content_indent < 4:
                list_indents.append(item.end())
                indented = False
                previous_blank = not expanded[item.end():].strip()
            else:
                indented = indent - content_indent >= 4 and (indented or previous_blank)
                previous_blank = False
            yield line, indented

    classified = list(lines())
    image_references = set()
    for line, code in classified:
        if code:
            continue
        text = re.sub(r'(`+)(.*?)\1(?!`)', '', line)
        for reference in re.finditer(r'!\[([^\]\n]*)\](?:\[([^\]\n]*)\])?', text):
            if text[reference.end():].startswith('('):
                continue
            image_references.add(label(reference[2] or reference[1]))

    def destination(value, image=False):
        return '<' + resolve(value[1:-1], image=image) + '>' if value.startswith('<') else resolve(value, image=image)

    def plain(segment):
        segment = re.sub(r'(!\[[^\]\n]*\]\()(<[^>\n]+>|[^\s)\n]+)([^)\n]*)(\))',
                         lambda match: match[1] + destination(match[2], image=True) + match[3] + match[4], segment)
        # Matching the closing label also handles a link wrapped around an image.
        segment = re.sub(r'(\]\()(<[^>\n]+>|[^\s)\n]+)([^)\n]*)(\))',
                         lambda match: match[1] + destination(match[2]) + match[3] + match[4], segment)
        return re.sub(r'^([ \t]*\[([^\]\n]+)\]:[ \t]*)(<[^>\n]+>|[^\s]+)',
                      lambda match: match[1] + destination(match[3], image=label(match[2]) in image_references), segment)

    result, pending_reference = [], None
    for line, code in classified:
        if code:
            if pending_reference:
                raise ExportError('Reference definition is missing its destination')
            result.append(line)
            continue
        if pending_reference:
            match = re.match(r'^([ \t]*)(<[^>\n]+>|[^\s]+)', line)
            if not match:
                raise ExportError('Reference definition is missing its destination')
            line = match[1] + destination(match[2], image=label(pending_reference[1]) in image_references) + line[match.end():]
        pending_reference = re.fullmatch(r'[ \t]*\[([^\]\n]+)\]:[ \t]*\n?', line)
        pieces, offset = [], 0
        for code_span in re.finditer(r'(`+)(.*?)\1(?!`)', line):
            pieces.extend((plain(line[offset:code_span.start()]), code_span[0]))
            offset = code_span.end()
        pieces.append(plain(line[offset:]))
        result.append(''.join(pieces))
    if pending_reference:
        raise ExportError('Reference definition is missing its destination')
    return ''.join(result)


def plan(root, feature, repo, links):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ExportError('Repository must be owner/name')
    feature = feature.resolve()
    if feature.parent != root / '.scratch' or not re.fullmatch(r'[A-Za-z0-9_-]+', feature.name):
        raise ExportError('Feature must be .scratch/<ASCII-slug> inside the checkout')
    if not isinstance(links, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in links.items()):
        raise ExportError('Links must map repository paths to GitHub URLs')
    for url in links.values():
        if not url.startswith('https://github.com/' + repo + '/'):
            raise ExportError('Context link must point into ' + repo)
    ref = git(root, 'rev-parse', 'HEAD').decode().strip()
    paths = sorted((feature / 'issues').glob('*.md'))
    if (feature / 'issues').is_symlink():
        raise ExportError('Issue directory must not be a symlink')
    if not paths:
        raise ExportError('No Markdown tickets in feature/issues')
    sources, ids = {}, set()
    for path in paths:
        match = re.fullmatch(r'([0-9]{2,})-.+\.md', path.name)
        if not match or path.is_symlink():
            raise ExportError('Expected a regular NN-slug.md ticket: ' + str(path))
        identity = match[1]
        if identity in ids:
            raise ExportError('Duplicate local ID: ' + identity)
        ids.add(identity)
        sources[path.relative_to(root).as_posix()] = identity
    committed = {}
    tickets = []
    for source, identity in sources.items():
        path = root / source
        text = path.read_text()
        heading = re.match(r'^# ([^\n]+)\n', text)
        if not heading:
            raise ExportError('Ticket needs a first-level title: ' + source)
        if 'oko-issue://' in text or '<!-- oko-issue:' in text:
            raise ExportError('Reserved export marker in source: ' + source)
        raw_dependencies = metadata(text, 'Blocked by') or 'Нет'
        if re.match(r'^(Нет|None)\b', raw_dependencies):
            dependencies = []
        else:
            dependencies = [part.strip() for part in raw_dependencies.split(',')]
            if any(value not in ids or value == identity for value in dependencies):
                raise ExportError('Unknown or self dependency in ' + source)

        def resolve(url, *, image=False):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or url.startswith('#'):
                return url
            if parsed.query:
                raise ExportError('Relative query link needs an absolute URL: ' + url)
            target = (path.parent / unquote(parsed.path)).resolve()
            if not target.is_relative_to(root):
                raise ExportError('Context link leaves checkout: ' + url)
            name = target.relative_to(root).as_posix()
            if name in sources:
                destination = 'oko-issue://' + sources[name]
            elif name in links:
                destination = links[name]
            else:
                if name not in committed:
                    try:
                        saved = git(root, 'show', ref + ':' + name)
                    except subprocess.CalledProcessError:
                        raise ExportError('Publish context or supply --links for ' + name) from None
                    if not target.is_file() or target.read_bytes() != saved:
                        raise ExportError('Context differs from published commit; supply --links for ' + name)
                    committed[name] = True
                prefix = f'https://raw.githubusercontent.com/{repo}/{ref}/' if image else f'https://github.com/{repo}/blob/{ref}/'
                destination = prefix + quote(name, safe='/')
            if parsed.fragment and '#' not in destination:
                destination += '#' + parsed.fragment
            return destination

        body = rewrite_links(text, resolve)
        binding = metadata(text, 'GitHub')
        number = issue_number(binding, repo) if binding else None
        marker = f'<!-- oko-issue:{feature.name}:{identity} -->'
        body = ('> Снимок описания задачи. Актуальное выполнение ведётся в GitHub; '
                'импорт не запускает исполнителя и не меняет метки или состояние.\n\n' + body.rstrip() + '\n\n' + marker + '\n')
        tickets.append(dict(id=identity, source=source, title=heading[1], body=body, marker=marker,
                            depends_on=dependencies, bound_number=number,
                            criteria=re.findall(r'\*\*(C[0-9]+\.[0-9]+)\*\*', text),
                            checklist_count=len(re.findall(r'^\s*[-*+] \[[ xX]\]', text, re.M))))
    graph = {ticket['id']: ticket['depends_on'] for ticket in tickets}

    def visit(identity, path):
        if identity in path:
            raise ExportError('Dependency cycle: ' + ' -> '.join([*path, identity]))
        for dependency in graph[identity]:
            visit(dependency, [*path, identity])

    for identity in graph:
        visit(identity, [])
    value = dict(version=1, repository=repo, feature=feature.name, ref=ref, tickets=tickets)
    value['digest'] = fingerprint(value)
    return value


def load_plan(path):
    value = json.loads(path.read_text())
    digest = value.pop('digest')
    if value.get('version') != 1 or digest != fingerprint(value):
        raise ExportError('Plan changed or has an unsupported version; regenerate it')
    value['digest'] = digest
    return value


def load_remote(path, repo):
    value = json.loads(path.read_text())
    if value.get('repository') != repo or value.get('complete') is not True:
        raise ExportError('A fresh complete issue list for ' + repo + ' is required')
    if not isinstance(value.get('issues'), list):
        raise ExportError('Snapshot needs an issues array')
    issues, seen = [], set()
    for raw in value['issues']:
        if 'pull_request' in raw:
            continue
        number = raw.get('issue_number', raw.get('number'))
        url = raw.get('html_url', raw.get('url'))
        body = raw.get('body') if raw.get('body') is not None else ''
        if (type(number) is not int or number <= 0 or number in seen
                or not isinstance(raw.get('title'), str) or not isinstance(body, str)
                or raw.get('state') not in ('open', 'closed')):
            raise ExportError('Malformed or duplicate issue in snapshot')
        if issue_number(url, repo) != number:
            raise ExportError('Issue URL/number mismatch in snapshot')
        seen.add(number)
        issues.append(dict(number=number, url=url, title=raw['title'],
                           body=body.replace('\r\n', '\n'), state=raw['state']))
    return issues


def bindings(plan, remote):
    result, used = {}, set()
    for ticket in plan['tickets']:
        marked = [issue for issue in remote if ticket['marker'] in issue['body'].splitlines()]
        if len(marked) > 1:
            raise ExportError('Duplicate export marker for ' + ticket['id'])
        bound = next((issue for issue in remote if issue['number'] == ticket['bound_number']), None)
        if ticket['bound_number'] is not None and bound is None:
            raise ExportError('Bound GitHub issue is missing for ' + ticket['id'])
        if marked and bound and marked[0]['number'] != bound['number']:
            raise ExportError('Marker and GitHub binding disagree for ' + ticket['id'])
        selected = bound or (marked[0] if marked else None)
        if selected:
            if selected['number'] in used:
                raise ExportError('Two tickets bind to the same GitHub issue')
            result[ticket['id']] = selected
            used.add(selected['number'])
        elif any(issue['title'] == ticket['title'] for issue in remote):
            raise ExportError('Unbound issue with this title exists; review before creating ' + ticket['id'])
    return result


def rendered_body(ticket, selected, repo):
    missing = False

    def reference(identity):
        nonlocal missing
        if identity not in selected:
            missing = True
            return 'https://github.com/' + repo + '/issues'
        return selected[identity]['url']

    body = re.sub(r'oko-issue://([0-9]+)', lambda match: reference(match[1]), ticket['body'])
    if ticket['depends_on']:
        body += '\nЗависит от: ' + ', '.join(f'[{identity}]({reference(identity)})' for identity in ticket['depends_on']) + '.\n'
    if missing:
        body = '> Импорт связей ещё не завершён; после создания всех задач ссылки будут заполнены.\n\n' + body
    if len(body.encode('utf-8')) > 65000:
        raise ExportError('Issue body exceeds the safe publication size: ' + ticket['id'])
    return body


def reconcile(plan, remote):
    selected = bindings(plan, remote)
    missing = [ticket for ticket in plan['tickets'] if ticket['id'] not in selected]
    # Check every existing body before proposing any write. Only the exact
    # provisional body produced by this same plan may be finalized automatically.
    for ticket in plan['tickets']:
        current = selected.get(ticket['id'])
        if current and (current['title'] != ticket['title'] or current['body'] not in
                        (rendered_body(ticket, {}, plan['repository']),
                         rendered_body(ticket, selected, plan['repository']))):
            raise ExportError('Existing issue differs; preserve edits and review ' + ticket['id'])
    if missing:
        actions = [dict(operation='create_issue', local_id=ticket['id'], arguments=dict(
            repository_full_name=plan['repository'], title=ticket['title'],
            body=rendered_body(ticket, {}, plan['repository']))) for ticket in missing]
        phase = 'create'
    else:
        actions = []
        for ticket in plan['tickets']:
            current = selected[ticket['id']]
            body = rendered_body(ticket, selected, plan['repository'])
            if current['body'] != body:
                actions.append(dict(operation='update_issue', local_id=ticket['id'], arguments=dict(
                    repository_full_name=plan['repository'], issue_number=current['number'],
                    title=ticket['title'], body=body)))
        phase = 'finalize' if actions else 'complete'
    return dict(version=1, repository=plan['repository'], plan_digest=plan['digest'],
                snapshot_digest=fingerprint(remote), phase=phase, actions=actions)


def verify(plan, remote):
    outcome = reconcile(plan, remote)
    if outcome['phase'] != 'complete':
        raise ExportError('Publication is incomplete: ' + outcome['phase'])
    selected = bindings(plan, remote)
    return dict(version=1, verified=True, repository=plan['repository'], plan_digest=plan['digest'],
                snapshot_digest=fingerprint(remote),
                criteria_count=sum(len(ticket['criteria']) for ticket in plan['tickets']),
                checklist_count=sum(ticket['checklist_count'] for ticket in plan['tickets']),
                issues={identity: dict(number=issue['number'], url=issue['url'], state=issue['state'],
                                       body_digest=fingerprint(issue['body']))
                        for identity, issue in selected.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('plan', help='Create an immutable publication plan without API calls')
    command.add_argument('feature', type=Path)
    command.add_argument('--repo', required=True)
    command.add_argument('--links', type=Path, help='Explicit repository path → published GitHub URL map')
    command.add_argument('--output', type=Path, required=True)
    for name in ('reconcile', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('plan', type=Path)
        command.add_argument('--remote', type=Path, required=True, help='Complete fresh GitHub issue snapshot')
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'plan':
            root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
            links = json.loads(args.links.read_text()) if args.links else {}
            value = plan(root, args.feature, args.repo, links)
        else:
            prepared = load_plan(args.plan)
            remote = load_remote(args.remote, prepared['repository'])
            value = reconcile(prepared, remote) if args.command == 'reconcile' else verify(prepared, remote)
        write_json(args.output, value)
        print(args.command + ': ' + str(value.get('phase', 'verified' if value.get('verified') else 'prepared')))
    except (ExportError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
