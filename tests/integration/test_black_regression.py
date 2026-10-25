import os

import pytest

from benchmarks.black_regression.adapter import verify
from benchmarks.black_regression.reproduce import reproduce

pytestmark=pytest.mark.integration


@pytest.fixture(scope='module')
def historical(tmp_path_factory):
    if os.environ.get('CAUSESCOPE_RUN_BLACK_INTEGRATION')!='1':
        pytest.skip('Set CAUSESCOPE_RUN_BLACK_INTEGRATION=1 after pinned Black setup')
    verify('26.5.1'); verify('26.10.0')
    return reproduce(tmp_path_factory.mktemp('black-evidence')/'verified',allow_local_execution=True)


def test_historical_defect_verified_independently(historical):
    assert historical['verified']
    assert all(historical['independent_checks'].values())
    assert len(historical['independent_runs'])==10


def test_cause_scope_isolates_original_bug(historical):
    result=historical['causescope_bad']
    assert result['status']=='failure_found'
    assert result['diagnosis']['one_minimal'] and result['diagnosis']['confirmed']
    assert result['diagnosis']['changes']=={'BLACK_LINE_LENGTH':'40','BLACK_SKIP_COMMA':'true'}
    assert result['search']['subprocess_runs']==18


def test_fixed_release_is_an_exhaustive_healthy_control(historical):
    assert historical['causescope_fixed']['status']=='no_failure_found'
    assert historical['causescope_fixed']['search']['search_complete']
    assert historical['causescope_fixed']['search']['subprocess_runs']==12


def test_fast_mode_demonstrates_actual_ast_corruption_not_intentional_rejection(historical):
    bad=[r for r in historical['independent_runs'] if r['release']=='26.5.1' and r['fast']]
    fixed=[r for r in historical['independent_runs'] if r['release']=='26.10.0' and r['fast']]
    assert bad[0]['exit_code']==0 and not bad[0]['ast_equivalent']
    assert fixed[0]['exit_code']==0 and fixed[0]['ast_equivalent']
