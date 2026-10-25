import gzip
import hashlib
import json
import importlib

import pytest
import yaml

from benchmarks.discovery.audit import audit
from benchmarks.discovery.check_build import check
from benchmarks.discovery.generate import fixture
from benchmarks.discovery.pack import pack
from benchmarks.discovery.study import run_study
from benchmarks.black_regression.reproduce import reproduce


def test_all_benchmark_execution_paths_require_opt_in(tmp_path):
    with pytest.raises(ValueError,match='allow-local-execution'):
        run_study(tmp_path,tmp_path/'never-created','simulation')
    with pytest.raises(ValueError,match='allow-local-execution'):
        reproduce(tmp_path/'never-created')
    with pytest.raises(ValueError,match='allow-local-execution'):
        check(tmp_path,tmp_path/'never-created')
    assert not (tmp_path/'never-created').exists()


def test_packing_is_lossless_and_retains_original_hashes(tmp_path):
    path=tmp_path/'trial.json'; raw=b'{"observations": [1,2,3]}\r\n'; path.write_bytes(raw)
    summary={'records':[{'evidence':'trial.json','evidence_sha256':hashlib.sha256(raw).hexdigest(),'observations':3}]}
    (tmp_path/'summary.json').write_text(json.dumps(summary),encoding='utf-8')
    packed=pack(tmp_path)
    record=packed['records'][0]
    assert not path.exists()
    assert gzip.decompress((tmp_path/record['evidence']).read_bytes())==raw
    assert record['uncompressed_sha256']==hashlib.sha256(raw).hexdigest()
    assert record['observations']==3
    assert pack(tmp_path)['records']==packed['records']


def test_packing_refuses_tampered_evidence(tmp_path):
    (tmp_path/'trial.json').write_text('{}',encoding='utf-8')
    (tmp_path/'summary.json').write_text(json.dumps({'records':[{'evidence':'trial.json','evidence_sha256':'bad'}]}))
    with pytest.raises(ValueError,match='hash mismatch'):
        pack(tmp_path)


def test_packing_refuses_path_traversal(tmp_path):
    (tmp_path/'summary.json').write_text(json.dumps({'records':[{'evidence':'../outside.json','evidence_sha256':'bad'}]}))
    with pytest.raises(ValueError,match='Unsafe'):
        pack(tmp_path)


def test_auditor_checks_measured_accounting_and_packed_observations(tmp_path):
    fixtures=tmp_path/'fixtures'; directory=fixtures/'exhaustive_clean-10-104729'
    directory.mkdir(parents=True)
    public,oracle=fixture(104729,10,'exhaustive_clean')
    files={}
    for name,data in [('experiment.yaml',yaml.safe_dump(public)),('oracle.json',json.dumps(oracle))]:
        path=directory/name
        path.write_text(data,encoding='utf-8')
        files[path.relative_to(fixtures).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    (fixtures/'manifest.json').write_text(json.dumps({'cases':[directory.name],'files_sha256':files}),encoding='utf-8')
    results=tmp_path/'results'
    run_study(fixtures,results,'simulation',allow_local_execution=True)
    pack(results)
    checked=audit(fixtures,results)
    assert checked['passed'],checked['errors']
    assert checked['observations']==72
    assert checked['physical_temporary_directories']==0
    summary=json.loads((results/'summary.json').read_text(encoding='utf-8'))
    summary['records'][0]['observations']+=1
    (results/'summary.json').write_text(json.dumps(summary),encoding='utf-8')
    checked=audit(fixtures,results)
    assert not checked['passed']
    assert any('observation accounting' in error for error in checked['errors'])


def test_failed_independent_reproduction_exports_failure_without_success_claim(tmp_path,monkeypatch):
    module=importlib.import_module('benchmarks.black_regression.reproduce')
    monkeypatch.setattr(module,'verify',lambda release:None)
    def not_reproduced(release,work,length,skip,fast):
        return {'release':release,'line_length':length,'skip_magic_trailing_comma':skip,
                'fast':fast,'exit_code':0,'stderr':'','ast_equivalent':True}
    monkeypatch.setattr(module,'execute',not_reproduced)
    monkeypatch.setattr(module,'ExperimentEngine',lambda *args:pytest.fail('Independent verification must precede discovery'))
    output=tmp_path/'failed'
    result=module.reproduce(output,allow_local_execution=True)
    assert not result['verified']
    text=(output/'REPORT.md').read_text(encoding='utf-8')
    assert 'No successful external validation is claimed' in text
    assert 'supports a genuine historical defect reproduction' not in text
    assert not (output/'causescope-bad').exists()
