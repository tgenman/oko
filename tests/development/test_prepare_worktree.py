"""Worktree preparation uses installed dependencies and stops at the failed step."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

CLI = Path(__file__).resolve().parents[2] / 'scripts/prepare-worktree'


def fixture(tmp_path):
    donor, root = tmp_path/'donor', tmp_path/'checkout'
    (donor/'node_modules').mkdir(parents=True)
    (donor/'.venv/bin').mkdir(parents=True)
    (donor/'data').mkdir()
    (donor/'data/private').write_text('keep private')
    (donor/'.env').write_text('not copied')
    (root/'scripts').mkdir(parents=True)
    if CLI.exists():
        shutil.copy2(CLI, root/'scripts/prepare-worktree')
    binaries = tmp_path/'bin'
    binaries.mkdir()
    tool = '''#!/usr/bin/env python3
import os,sys
with open(os.environ['RECORD'],'a') as f:f.write('STEP\\n')
sys.exit(int(os.environ.get('FAIL_STEP','0')))
'''
    npm=binaries/'npm'
    npm.write_text(tool.replace('STEP','BUILD'))
    npm.chmod(0o755)
    python=donor/'.venv/bin/python'
    python.write_text(tool.replace('STEP','VALIDATE'))
    python.chmod(0o755)
    record=tmp_path/'record'
    env=dict(os.environ, PATH=str(binaries)+os.pathsep+os.environ['PATH'], RECORD=str(record))
    env.pop('OKO_PYTHON',None)
    return donor,root,record,env


def run(root, donor, env):
    return subprocess.run([sys.executable,str(root/'scripts/prepare-worktree'),'--from',str(donor)],cwd=root,env=env,text=True,capture_output=True)


def test_prepare_reuses_dependencies_builds_validates_and_keeps_private_data_out(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    result=run(root,donor,env)
    assert result.returncode==0,result.stdout+result.stderr
    assert (root/'node_modules').resolve()==donor/'node_modules'
    assert (root/'.venv').resolve()==donor/'.venv'
    assert record.read_text().splitlines()==['BUILD','VALIDATE']
    assert not (root/'data').exists() and not (root/'.env').exists()
    assert 'Prepared and validated' in result.stdout


def test_prepare_stops_before_validation_after_build_failure(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    result=run(root,donor,dict(env,FAIL_BUILD='7'))
    assert result.returncode==7
    assert record.read_text().splitlines()==['BUILD']
    assert 'Prepared and validated' not in result.stdout


def test_prepare_validation_failure_is_not_success(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    result=run(root,donor,dict(env,FAIL_VALIDATE='9'))
    assert result.returncode==9
    assert record.read_text().splitlines()==['BUILD','VALIDATE']
    assert 'Prepared and validated' not in result.stdout


def test_prepare_missing_donor_keeps_existing_paths_and_creates_no_partial_links(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    shutil.rmtree(donor/'node_modules')
    result=run(root,donor,env)
    assert result.returncode!=0
    assert not (root/'.venv').exists()
    assert not record.exists()
    # A caller-owned dependency path must never be replaced, including a bad link.
    (root/'node_modules').symlink_to(root/'missing')
    result=run(root,donor,env)
    assert result.returncode!=0
    assert (root/'node_modules').is_symlink()
    assert os.readlink(root/'node_modules')==str(root/'missing')


def test_prepare_keeps_installed_local_dependencies(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    shutil.copytree(donor/'.venv',root/'.venv')
    (root/'node_modules').mkdir()
    (root/'node_modules/local').write_text('keep')
    result=run(root,donor,env)
    assert result.returncode==0,result.stderr
    assert not (root/'.venv').is_symlink()
    assert not (root/'node_modules').is_symlink()
    assert (root/'node_modules/local').read_text()=='keep'


def test_prepare_finds_dependencies_in_main_git_worktree(tmp_path):
    donor,root,record,env=fixture(tmp_path)
    # Use the real worktree metadata; the command does not fetch or change refs.
    subprocess.run(['git','init','-q',str(donor)],check=True)
    subprocess.run(['git','-C',str(donor),'config','user.name','Test'],check=True)
    subprocess.run(['git','-C',str(donor),'config','user.email','test@example.test'],check=True)
    (donor/'tracked').write_text('base')
    subprocess.run(['git','-C',str(donor),'add','tracked'],check=True)
    subprocess.run(['git','-C',str(donor),'commit','-qm','base'],check=True)
    target=tmp_path/'managed'
    subprocess.run(['git','-C',str(donor),'worktree','add','-q','--detach',str(target)],check=True)
    (target/'scripts').mkdir()
    shutil.copy2(CLI,target/'scripts/prepare-worktree')
    result=subprocess.run([sys.executable,str(target/'scripts/prepare-worktree')],cwd=target,env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert (target/'.venv').resolve()==donor/'.venv'
    assert record.read_text().splitlines()==['BUILD','VALIDATE']
    assert (target/'tracked').read_text()=='base'
