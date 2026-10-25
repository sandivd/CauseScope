"""Real SDK protocol tests; optional dependency stays outside the core install."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip('mcp')
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from causescope.mcp_server import create_server
from causescope.service import InvestigationService


def decoded(result):
    assert not result.is_error, result
    return result.structured_content


def test_sdk_tools_have_correct_effect_annotations_and_no_approval_tool(tmp_path):
    async def check():
        async with Client(create_server(InvestigationService(tmp_path))) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {'capabilities','validate_specification','prepare_investigation',
                'start_investigation','inspect_investigation','get_evidence','get_findings'}
            for name in ['capabilities','validate_specification','inspect_investigation','get_evidence','get_findings']:
                assert tools[name].annotations.read_only_hint is True
            assert tools['prepare_investigation'].annotations.read_only_hint is False
            execute = tools['start_investigation'].annotations
            assert execute.destructive_hint and execute.open_world_hint and not execute.idempotent_hint
            caps = decoded(await client.call_tool('capabilities', {}))
            assert not caps['execution_enabled'] and 'root cause' in caps['interpretation']
    asyncio.run(check())


def test_real_stdio_client_plans_approves_runs_polls_and_reads_evidence(tmp_path, raw_spec):
    raw_spec['settings'].update(max_runs=100, max_seconds=30)
    path = tmp_path/'spec with spaces.yaml'
    path.write_text(yaml.safe_dump(raw_spec), encoding='utf-8')
    async def check():
        transport = StdioServerParameters(command=sys.executable,
            args=['-m','causescope','mcp','--workspace',str(tmp_path),'--allow-local-execution'])
        async with Client(transport) as client:
            assert decoded(await client.call_tool('validate_specification', {'specification':path.name}))['valid']
            prepared = decoded(await client.call_tool('prepare_investigation', {
                'specification':path.name, 'output':'artifacts with spaces'}))
            assert not (tmp_path/'artifacts with spaces').exists()
            invalid = await client.call_tool('start_investigation', {'plan_id':prepared['id'],'approval_id':'0'*32})
            assert invalid.is_error
            # Out-of-band operator approval, never a server tool.
            grant = InvestigationService(tmp_path).approve(prepared['plan_path'], allow_local_execution=True)
            job = decoded(await client.call_tool('start_investigation',
                {'plan_id':prepared['id'], 'approval_id':grant['approval_id']}))
            for _ in range(300):
                progress = decoded(await client.call_tool('inspect_investigation', {'job_id':job['job_id']}))
                if progress['state'] != 'running':
                    break
                await asyncio.sleep(.02)
            assert progress['state'] == 'completed', progress
            view = decoded(await client.call_tool('get_findings', {'job_id':job['job_id']}))
            assert view['status'] == 'failure_found' and view['counts']['subprocess_runs'] == 18
            assert view['evidence']['status'] == 'repeated_failure'
            evidence = decoded(await client.call_tool('get_evidence', {'job_id':job['job_id'],'limit':50}))
            assert evidence['total_observations'] == len(evidence['observations']) == 18
            assert len({r['working_directory'] for r in evidence['observations']}) == 18
            assert all(not Path(r['working_directory']).exists() for r in evidence['observations'])
            assert 'reproduce' in view['reproduction']['command_powershell']
    asyncio.run(check())


def test_readonly_stdio_server_refuses_execution_even_with_a_valid_approval(tmp_path, raw_spec):
    raw_spec['settings'].update(max_runs=10, max_seconds=10)
    (tmp_path/'spec.yaml').write_text(yaml.safe_dump(raw_spec), encoding='utf-8')
    service = InvestigationService(tmp_path)
    plan = service.prepare('spec.yaml','output')
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    async def check():
        async with Client(StdioServerParameters(command=sys.executable,
                args=['-m','causescope','mcp','--workspace',str(tmp_path)])) as client:
            response = await client.call_tool('start_investigation', {'plan_id':plan['id'], 'approval_id':grant['approval_id']})
            assert response.is_error and 'disabled' in response.content[0].text
            invalid = decoded(await client.call_tool('validate_specification', {'specification':'../escape.yaml'}))
            assert not invalid['valid'] and not invalid['executed']
    asyncio.run(check())
    assert not (tmp_path/'output').exists()
