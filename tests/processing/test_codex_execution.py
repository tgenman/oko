import json
from pathlib import Path
import pytest
from oko.processing.codex import LocalCodex, FEATURES
from oko.processing.execution import CodexChoice


@pytest.mark.parametrize('fast', [False, True])
def test_isolated_exec_passes_explicit_model_effort_and_speed(tmp_path,monkeypatch,fast):
    import asyncio
    from oko.processing import codex
    seen=[]
    monkeypatch.setattr(codex,'supported_binary',lambda: Path('/synthetic/codex'))
    async def run(args,prompt,directory):
        seen.append((args,prompt,directory))
        return 0, (json.dumps(dict(type='item.completed',item=dict(type='agent_message',text='{}')))+'\n'+json.dumps(dict(type='turn.completed'))).encode()
    monkeypatch.setattr(codex,'run_isolated',run)
    choice=CodexChoice(model='test-model',reasoning_effort='high',fast=fast)
    assert asyncio.run(LocalCodex().complete('input',{},tmp_path/'scratch',choice))=='{}'
    args,prompt,directory=seen[0]
    assert args[:2]==['/usr/bin/sandbox-exec','-p']
    assert args[args.index('--model')+1]=='test-model'
    assert 'model_reasoning_effort="high"' in args
    assert ('service_tier="fast"' if fast else 'service_tier="default"') in args
    assert any(args[i]=='fast_mode' and args[i-1]==('--enable' if fast else '--disable') for i in range(1,len(args)))
    for flag in ['--ephemeral','--ignore-user-config','--ignore-rules','--output-schema']:
        assert flag in args
    assert 'mcp_servers={}' in args
    for feature in FEATURES:
        assert any(args[i]==feature and args[i-1]=='--disable' for i in range(1,len(args)))
    assert prompt==b'input' and not directory.exists()
