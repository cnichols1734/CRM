"""Stale client-email previews must not overwrite the current picker set."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OCE_JS = (ROOT / 'static' / 'js' / 'offer_client_email.js').read_text()


def oce_next_refresh_gen(current):
    return (int(current or 0)) + 1


def oce_refresh_is_current(gen, current):
    return gen == current


def _oce_refresh_helpers():
    start = OCE_JS.index('function oceNextRefreshGen(')
    end = OCE_JS.index('function oceTransactionId(')
    return OCE_JS[start:end]


def test_later_refresh_invalidates_an_earlier_preview():
    current = 0
    all_offers = oce_next_refresh_gen(current)
    current = all_offers
    picked = oce_next_refresh_gen(current)
    current = picked

    assert not oce_refresh_is_current(all_offers, current)
    assert oce_refresh_is_current(picked, current)


def test_composer_refresh_guards_success_error_and_cleanup():
    assert 'refreshGen: 0' in OCE_JS
    assert 'function oceNextRefreshGen(' in OCE_JS
    assert 'function oceRefreshIsCurrent(' in OCE_JS
    assert OCE_JS.count('oceRefreshIsCurrent(gen, offerClientEmail.refreshGen)') >= 3
    success_idx = OCE_JS.index(
        'if (!oceRefreshIsCurrent(gen, offerClientEmail.refreshGen)) return;'
    )
    assign_idx = OCE_JS.index('offerClientEmail.offerIds = data.draft.offer_ids')
    assert success_idx < assign_idx


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_js_refresh_helpers_ignore_a_stale_generation():
    helpers = _oce_refresh_helpers()
    assert 'function oceNextRefreshGen(' in helpers
    assert 'function oceRefreshIsCurrent(' in helpers
    result = subprocess.run(
        [
            shutil.which('node'),
            '-e',
            helpers + (
                'const first = oceNextRefreshGen(0);'
                'const second = oceNextRefreshGen(first);'
                'if (oceRefreshIsCurrent(first, second)) process.exit(1);'
                'if (!oceRefreshIsCurrent(second, second)) process.exit(1);'
                'if (!oceRefreshIsCurrent(first, first)) process.exit(1);'
                "process.stdout.write('ok');"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == 'ok'
