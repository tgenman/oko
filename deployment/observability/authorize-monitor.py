#!/usr/bin/env python3
"""Authorize one dedicated public key for an immutable read-only command."""
import importlib.util
import grp
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oko_operator',SOURCE/'okoctl.py')
operator=importlib.util.module_from_spec(spec);spec.loader.exec_module(operator)


def authorize(key):
    if os.geteuid()!=0:raise RuntimeError('Run as root')
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]{40,100}={0,2}(?: [A-Za-z0-9_.@-]+)?',key):
        raise ValueError('Expected a dedicated Ed25519 public key')
    with operator.maintenance():
        try:
            if pwd.getpwuid(20003).pw_name!='oko-monitor':raise RuntimeError('Monitor UID is occupied')
        except KeyError:pass
        try:
            group=grp.getgrnam('oko-monitor')
            if group.gr_gid!=20003:raise RuntimeError('Unexpected monitor group')
        except KeyError:
            try:grp.getgrgid(20003)
            except KeyError:
                subprocess.run(['groupadd','--system','--gid','20003','oko-monitor'],check=True)
            else:raise RuntimeError('Monitor GID is occupied')
        try:account=pwd.getpwnam('oko-monitor')
        except KeyError:
            subprocess.run(['useradd','--system','--uid','20003','--gid','oko-monitor','--home-dir','/var/lib/oko-monitor','--shell','/bin/sh','oko-monitor'],check=True)
            account=pwd.getpwnam('oko-monitor')
        if account.pw_uid!=20003 or account.pw_gid!=20003 or account.pw_shell!='/bin/sh' or account.pw_dir!='/var/lib/oko-monitor':
            raise RuntimeError('Unexpected monitor account')
        home=Path(account.pw_dir);home.mkdir(exist_ok=True,mode=0o755);os.chown(home,0,0);home.chmod(0o755)
        ssh=home/'.ssh';ssh.mkdir(exist_ok=True,mode=0o755);os.chown(ssh,0,0);ssh.chmod(0o755)
        operator.atomic(ssh/'authorized_keys','restrict,command="sudo -n /usr/local/sbin/oko-monitor-snapshot" '+key+'\n',mode=0o644)
        operator.atomic(Path('/usr/local/sbin/oko-monitor-snapshot'),
            '#!/bin/sh\nexec /usr/bin/python3 /srv/oko/config/observability/snapshot.py\n',mode=0o755)
        sudo=Path('/etc/sudoers.d/oko-monitor')
        operator.atomic(sudo,'oko-monitor ALL=(root) NOPASSWD: /usr/local/sbin/oko-monitor-snapshot ""\n',mode=0o440)
        subprocess.run(['visudo','-cf',sudo],check=True)
    print('Dedicated read-only monitor key authorized')


if __name__=='__main__':authorize(sys.stdin.read(1024).strip())
