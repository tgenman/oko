"""Issue export through its CLI, with temporary Git repositories and API snapshots."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

CLI = Path(__file__).resolve().parents[2] / 'scripts/github_issues.py'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.test')
    feature = root / '.scratch/migrate'
    (feature / 'issues').mkdir(parents=True)
    (feature / 'spec.md').write_text('# Specification\n\nAccepted requirements.\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'specification')
    return root, feature


def run(root, *args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=root,
                          capture_output=True, text=True)


def ticket(feature, name='01-first.md', text=None):
    value = text or ('# First task\n\nStatus: ready-for-agent\nBlocked by: Нет\n\n'
                     '[Specification](../spec.md)\n\n'
                     '- [ ] **C01.01** Boolean remains strict.\n'
                     '  Multiline requirement stays here.\n'
                     '- [ ] Requirement without a numbered ID.\n')
    (feature / 'issues' / name).write_text(value)
    return value


def test_plan_preserves_entire_requirements_and_pins_context_to_commit(tmp_path):
    root, feature = repository(tmp_path)
    original = ticket(feature)
    output = tmp_path / 'plan.json'
    result = run(root, 'plan', '.scratch/migrate', '--repo', 'example/reader', '--output', output)
    assert result.returncode == 0, result.stderr
    plan = json.loads(output.read_text())
    issue = plan['tickets'][0]
    assert issue['title'] == 'First task'
    assert issue['marker'] == '<!-- oko-issue:migrate:01 -->'
    assert original.split('- [ ]', 1)[1] in issue['body']
    assert issue['criteria'] == ['C01.01']
    assert issue['checklist_count'] == 2
    assert f'https://github.com/example/reader/blob/{git(root, "rev-parse", "HEAD")}/.scratch/migrate/spec.md' in issue['body']
    assert (feature / 'issues/01-first.md').read_text() == original


def snapshot(tmp_path, issues, **changes):
    path = tmp_path / 'remote.json'
    path.write_text(json.dumps({'repository': 'example/reader', 'complete': True,
                                'issues': issues, **changes}))
    return path


def remote_issue(number, arguments):
    return {'number': number, 'html_url': f'https://github.com/example/reader/issues/{number}',
            'title': arguments['title'], 'body': arguments['body'], 'state': 'open'}


def actions(root, plan, remote, tmp_path):
    path = tmp_path / f'actions-{len(list(tmp_path.glob("actions-*.json")))}.json'
    result = run(root, 'reconcile', plan, '--remote', remote, '--output', path)
    assert result.returncode == 0, result.stderr
    return json.loads(path.read_text())


def test_lost_creation_reply_is_recovered_without_duplicate_and_every_body_is_read_back(tmp_path):
    root, feature = repository(tmp_path)
    ticket(feature, text='# First task\n\nBlocked by: Нет\n\n- [ ] First requirement.\n\n[Next](02-second.md)\n')
    ticket(feature, '02-second.md', '# Second task\n\nBlocked by: 01\n\n- [ ] Second requirement.\n')
    plan = tmp_path / 'plan.json'
    assert run(root, 'plan', feature, '--repo', 'example/reader', '--output', plan).returncode == 0
    first = actions(root, plan, snapshot(tmp_path, []), tmp_path)
    assert [action['operation'] for action in first['actions']] == ['create_issue', 'create_issue']
    # GitHub committed the first create, but its reply was lost. A new complete
    # list is the authority; neither a local receipt nor a blind retry is needed.
    remote = [remote_issue(41, first['actions'][0]['arguments'])]
    resumed = actions(root, plan, snapshot(tmp_path, remote), tmp_path)
    assert len(resumed['actions']) == 1
    assert resumed['actions'][0]['local_id'] == '02'
    assert resumed['actions'][0]['operation'] == 'create_issue'
    remote.append(remote_issue(43, resumed['actions'][0]['arguments']))
    finalizing = actions(root, plan, snapshot(tmp_path, remote), tmp_path)
    for action in finalizing['actions']:
        assert action['operation'] == 'update_issue'
        item = next(item for item in remote if item['number'] == action['arguments']['issue_number'])
        item.update(title=action['arguments']['title'], body=action['arguments']['body'])
    assert 'https://github.com/example/reader/issues/43' in remote[0]['body']
    assert 'https://github.com/example/reader/issues/41' in remote[1]['body']
    remote[1]['state'] = 'closed'  # Import does not reopen a human-closed issue.
    remote_path = snapshot(tmp_path, remote)
    assert actions(root, plan, remote_path, tmp_path)['actions'] == []
    receipt = tmp_path / 'receipt.json'
    result = run(root, 'verify', plan, '--remote', remote_path, '--output', receipt)
    assert result.returncode == 0, result.stderr
    saved = json.loads(receipt.read_text())
    assert saved['verified'] is True
    assert saved['checklist_count'] == 2
    assert saved['issues']['01']['number'] == 41
    assert saved['issues']['02']['state'] == 'closed'


def test_markdown_examples_stay_verbatim_while_reference_links_are_published(tmp_path):
    root, feature = repository(tmp_path)
    original = ('# Markdown task\n\n[Spec](../spec.md "Approved spec")\n\n'
                '`[example](../not-a-real-file.md)`\n\n'
                '```md\n[example](../also-not-a-file.md)\n```\n\n'
                '[ref]: ../spec.md\n\n- [ ] Preserve every example.\n')
    ticket(feature, text=original)
    output = tmp_path / 'plan.json'
    result = run(root, 'plan', feature, '--repo', 'example/reader', '--output', output)
    assert result.returncode == 0, result.stderr
    body = json.loads(output.read_text())['tickets'][0]['body']
    assert '`[example](../not-a-real-file.md)`' in body
    assert '```md\n[example](../also-not-a-file.md)\n```' in body
    assert '[ref]: https://github.com/example/reader/blob/' in body
    assert '[Spec](https://github.com/example/reader/blob/' in body
    assert ' "Approved spec")' in body


@pytest.mark.parametrize('problem', ['duplicate_marker', 'human_checked', 'lost_requirement',
                                    'title_collision', 'incomplete', 'other_repo', 'malformed_body'])
def test_conflicts_stop_before_writes_and_cannot_produce_a_verification_receipt(tmp_path, problem):
    root, feature = repository(tmp_path)
    ticket(feature)
    prepared = tmp_path / 'plan.json'
    assert run(root, 'plan', feature, '--repo', 'example/reader', '--output', prepared).returncode == 0
    initial = actions(root, prepared, snapshot(tmp_path, []), tmp_path)
    remote = [remote_issue(9, initial['actions'][0]['arguments'])]
    changes = {}
    if problem == 'duplicate_marker': remote.append(remote_issue(10, initial['actions'][0]['arguments']))
    if problem == 'human_checked': remote[0]['body'] = remote[0]['body'].replace('- [ ]', '- [x]', 1)
    if problem == 'lost_requirement': remote[0]['body'] = remote[0]['body'].replace('  Multiline requirement stays here.\n', '')
    if problem == 'title_collision': remote[0]['body'] = 'An unrelated issue with the same title.'
    if problem == 'incomplete': changes['complete'] = False
    if problem == 'other_repo': changes['repository'] = 'someone/else'
    if problem == 'malformed_body': remote[0]['body'] = False
    remote_path = snapshot(tmp_path, remote, **changes)
    for command in ('reconcile', 'verify'):
        output = tmp_path / (command + '-blocked.json')
        result = run(root, command, prepared, '--remote', remote_path, '--output', output)
        assert result.returncode == 1, result.stdout
        assert not output.exists()


@pytest.mark.parametrize('problem', ['unpublished_context', 'dirty_context', 'dependency_cycle', 'duplicate_id'])
def test_invalid_local_input_cannot_be_published(tmp_path, problem):
    root, feature = repository(tmp_path)
    ticket(feature)
    if problem == 'unpublished_context': (feature / 'spec.md').unlink(); git(root, 'rm', '.scratch/migrate/spec.md'); git(root, 'commit', '-qm', 'remove context')
    if problem == 'dirty_context': (feature / 'spec.md').write_text('Unpublished new requirements.\n')
    if problem == 'dependency_cycle':
        ticket(feature, text='# First\n\nBlocked by: 02\n')
        ticket(feature, '02-second.md', '# Second\n\nBlocked by: 01\n')
    if problem == 'duplicate_id': ticket(feature, '01-also.md', '# Another task\n')
    output = tmp_path / 'invalid-plan.json'
    result = run(root, 'plan', feature, '--repo', 'example/reader', '--output', output)
    assert result.returncode == 1, result.stdout
    assert not output.exists()


def test_explicit_published_context_mapping_allows_uncommitted_specification(tmp_path):
    root, feature = repository(tmp_path)
    ticket(feature)
    (feature / 'spec.md').write_text('The approved requirements were published in an issue comment.\n')
    links = tmp_path / 'links.json'
    links.write_text(json.dumps({'.scratch/migrate/spec.md': 'https://github.com/example/reader/issues/8#issuecomment-42'}))
    output = tmp_path / 'plan.json'
    result = run(root, 'plan', feature, '--repo', 'example/reader', '--links', links, '--output', output)
    assert result.returncode == 0, result.stderr
    assert '[Specification](https://github.com/example/reader/issues/8#issuecomment-42)' in json.loads(output.read_text())['tickets'][0]['body']


def test_existing_local_github_binding_is_never_replaced_by_a_new_issue(tmp_path):
    root, feature = repository(tmp_path)
    ticket(feature, text='# Bound task\n\nGitHub: https://github.com/example/reader/issues/7\n\n- [ ] Keep this requirement.\n')
    output = tmp_path / 'plan.json'
    assert run(root, 'plan', feature, '--repo', 'example/reader', '--output', output).returncode == 0
    result = run(root, 'reconcile', output, '--remote', snapshot(tmp_path, []), '--output', tmp_path / 'actions.json')
    assert result.returncode == 1
    assert 'Bound GitHub issue is missing' in result.stderr


def test_modified_plan_is_rejected_instead_of_dropping_criteria(tmp_path):
    root, feature = repository(tmp_path)
    ticket(feature)
    output = tmp_path / 'plan.json'
    assert run(root, 'plan', feature, '--repo', 'example/reader', '--output', output).returncode == 0
    value = json.loads(output.read_text())
    value['tickets'][0]['body'] = 'Shortened by mistake.'
    output.write_text(json.dumps(value))
    result = run(root, 'reconcile', output, '--remote', snapshot(tmp_path, []), '--output', tmp_path / 'actions.json')
    assert result.returncode == 1
    assert 'Plan changed' in result.stderr


def test_previous_plan_is_preserved_when_a_new_export_uses_the_same_output_path(tmp_path):
    root, feature = repository(tmp_path)
    ticket(feature)
    output = tmp_path / 'plan.json'
    args = ('plan', feature, '--repo', 'example/reader', '--output', output)
    assert run(root, *args).returncode == 0
    previous = output.read_bytes()
    assert run(root, *args).returncode == 0  # Exact retries are harmless.
    ticket(feature, text='# Changed task\n\n- [ ] New requirement.\n')
    result = run(root, *args)
    assert result.returncode == 1
    assert output.read_bytes() == previous


def test_multiline_reference_and_linked_image_do_not_leave_local_context_links(tmp_path):
    root, feature = repository(tmp_path)
    (feature / 'issues/icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    git(root, 'add', '.scratch/migrate/issues/icon.svg')
    git(root, 'commit', '-qm', 'public image')
    ticket(feature, text='# Linked task\n\n[spec]:\n  ../spec.md\n\n[![spec](icon.svg)](../spec.md)\n\n- [ ] Context must be accessible.\n')
    output = tmp_path / 'plan.json'
    result = run(root, 'plan', feature, '--repo', 'example/reader', '--output', output)
    assert result.returncode == 0, result.stderr
    body = json.loads(output.read_text())['tickets'][0]['body']
    assert '[spec]:\n  https://github.com/example/reader/blob/' in body
    assert '[![spec](https://raw.githubusercontent.com/example/reader/' in body
    assert '.svg)](https://github.com/example/reader/blob/' in body
    assert '](../spec.md)' not in body


def test_reference_images_use_raw_context_and_indented_code_stays_literal(tmp_path):
    root, feature = repository(tmp_path)
    diagram = feature / 'diagram.svg'
    diagram.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    git(root, 'add', str(diagram))
    git(root, 'commit', '-qm', 'diagram')
    original = ('# Diagram task\n\nBlocked by: Нет\n\n'
                '[Figure]: ../diagram.svg\n\n![Diagram][figure]\n\n'
                '- [ ] Requirement\n    [Specification](../spec.md)\n    ![Nested][figure]\n\n'
                '      [code in list](../also-missing.md)\n\n'
                '    [nested-figure]: ../diagram.svg\n    ![Nested definition][nested-figure]\n\nEnd list.\n\n'
                '    [example](../missing.md)\n\n'
                '```md\n![literal][other]\n```\n\n[other]: ../spec.md\n')
    ticket(feature, text=original)
    output = tmp_path / 'plan.json'
    result = run(root, 'plan', feature, '--repo', 'example/reader', '--output', output)
    assert result.returncode == 0, result.stderr
    body = json.loads(output.read_text())['tickets'][0]['body']
    ref = git(root, 'rev-parse', 'HEAD')
    assert f'[Figure]: https://raw.githubusercontent.com/example/reader/{ref}/.scratch/migrate/diagram.svg' in body
    assert f'    [nested-figure]: https://raw.githubusercontent.com/example/reader/{ref}/.scratch/migrate/diagram.svg' in body
    assert '    [example](../missing.md)' in body
    assert '      [code in list](../also-missing.md)' in body
    assert f'    [Specification](https://github.com/example/reader/blob/{ref}/.scratch/migrate/spec.md)' in body
    assert '```md\n![literal][other]\n```' in body
    assert f'[other]: https://github.com/example/reader/blob/{ref}/.scratch/migrate/spec.md' in body
    assert (feature / 'issues/01-first.md').read_text() == original
