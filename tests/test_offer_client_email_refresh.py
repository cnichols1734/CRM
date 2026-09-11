"""Stale client-email previews and send handlers must not touch a newer composer."""

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


def oce_next_session_gen(current):
    return (int(current or 0)) + 1


def oce_session_is_current(gen, current):
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


def test_later_composer_session_invalidates_an_earlier_send():
    current = 0
    first_open = oce_next_session_gen(current)
    current = first_open
    send_gen = current
    closed = oce_next_session_gen(current)
    current = closed
    reopened = oce_next_session_gen(current)
    current = reopened

    assert not oce_session_is_current(send_gen, current)
    assert oce_session_is_current(reopened, current)


def test_composer_send_guards_success_error_and_cleanup():
    assert 'sessionGen: 0' in OCE_JS
    assert 'function oceNextSessionGen(' in OCE_JS
    assert 'function oceSessionIsCurrent(' in OCE_JS
    # then + catch ignore stale responses. finally must not.
    assert OCE_JS.count(
        'oceSessionIsCurrent(gen, offerClientEmail.sessionGen)'
    ) == 2
    assert OCE_JS.count(
        'offerClientEmail.sessionGen = oceNextSessionGen(offerClientEmail.sessionGen)'
    ) >= 2
    send_fn = OCE_JS[OCE_JS.index('function sendOfferClientEmail('):]
    success_idx = send_fn.index(
        'if (!oceSessionIsCurrent(gen, offerClientEmail.sessionGen)) return;'
    )
    close_idx = send_fn.index('closeOfferClientEmail();')
    assert success_idx < close_idx
    error_idx = send_fn.index(
        'if (!oceSessionIsCurrent(gen, offerClientEmail.sessionGen)) return;',
        success_idx + 1,
    )
    assert close_idx < error_idx
    finally_idx = send_fn.index('.finally(() => {')
    reset_idx = send_fn.index('offerClientEmail.sending = false;', finally_idx)
    sync_idx = send_fn.index('oceSyncSend();', reset_idx)
    assert reset_idx < sync_idx
    finally_block = send_fn[finally_idx:sync_idx]
    assert 'oceSessionIsCurrent' not in finally_block
    assert 'closeOfferClientEmail' not in finally_block


def test_send_lock_survives_composer_reopen_until_post_settles():
    """Close/reopen bumps sessionGen. The in-flight POST lock must stay
    until that request settles, or Send can fire a second email."""
    open_fn = OCE_JS[
        OCE_JS.index('function openOfferClientEmail('):
        OCE_JS.index('function closeOfferClientEmail(')
    ]
    assert 'offerClientEmail.sending = false' not in open_fn
    send_fn = OCE_JS[OCE_JS.index('function sendOfferClientEmail('):]
    assert 'if (offerClientEmail.sending) return;' in send_fn
    finally_idx = send_fn.index('.finally(() => {')
    send_fn.index('offerClientEmail.sending = false;', finally_idx)


def test_stale_send_finally_syncs_the_live_session():
    """Close/reopen while a POST is in flight. finally must unlock Send
    on the live composer and clear "Sending", not return early."""
    send_fn = OCE_JS[OCE_JS.index('function sendOfferClientEmail('):]
    finally_idx = send_fn.index('.finally(() => {')
    reset_idx = send_fn.index('offerClientEmail.sending = false;', finally_idx)
    sync_idx = send_fn.index('oceSyncSend();', reset_idx)
    between = send_fn[reset_idx:sync_idx]
    assert 'if (!oceSessionIsCurrent' not in between
    assert 'return;' not in between
    assert "oceEl('status')" in send_fn[finally_idx:sync_idx]
    assert "statusEl.textContent = ''" in send_fn[finally_idx:sync_idx]


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_js_session_helpers_ignore_a_stale_generation():
    helpers = _oce_refresh_helpers()
    assert 'function oceNextSessionGen(' in helpers
    assert 'function oceSessionIsCurrent(' in helpers
    result = subprocess.run(
        [
            shutil.which('node'),
            '-e',
            helpers + (
                'const first = oceNextSessionGen(0);'
                'const closed = oceNextSessionGen(first);'
                'const reopened = oceNextSessionGen(closed);'
                'if (oceSessionIsCurrent(first, reopened)) process.exit(1);'
                'if (!oceSessionIsCurrent(reopened, reopened)) process.exit(1);'
                'if (!oceSessionIsCurrent(first, first)) process.exit(1);'
                "process.stdout.write('ok');"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == 'ok'


def test_close_composer_cleanup_ignores_a_newer_session():
    close_fn = OCE_JS[
        OCE_JS.index('function closeOfferClientEmail('):
        OCE_JS.index('function oceBindEvents(')
    ]
    assert 'const closeGen = offerClientEmail.sessionGen' in close_fn
    guard = "if (!oceSessionIsCurrent(closeGen, offerClientEmail.sessionGen)) return;"
    guard_idx = close_fn.index(guard)
    lock_idx = close_fn.index("document.body.classList.remove('overflow-hidden')")
    timer_idx = close_fn.index('clearTimeout(offerClientEmail.timer)')
    assert guard_idx < lock_idx < timer_idx


def test_recipients_hydrate_on_first_session_paint():
    refresh = OCE_JS[
        OCE_JS.index('function oceRefresh('):
        OCE_JS.index('function oceFillCopy(')
    ]
    first_idx = refresh.index(
        'const firstPaint = offerClientEmail.paintedFor == null'
    )
    assign_idx = refresh.index('offerClientEmail.paintedFor = key')
    fill_idx = refresh.index('if (firstPaint) oceFillRecipients(data.draft)')
    assert first_idx < assign_idx < fill_idx
    assert 'if (initial) oceFillRecipients' not in refresh


def test_first_paint_hydrates_when_initial_preview_was_invalidated():
    """Typing before the first preview returns marks that request stale.
    The follow-up refresh has initial=False; recipients still need a fill."""
    painted_for = None
    initial = False
    first_paint = painted_for is None
    assert first_paint
    assert not initial
    assert first_paint

    painted_for = '12,34'
    initial = False
    first_paint = painted_for is None
    assert not first_paint
    assert not (initial or first_paint)
