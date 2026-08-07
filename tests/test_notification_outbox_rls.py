"""Outbox worker must restore org RLS context after each commit."""

from unittest.mock import MagicMock, patch

from jobs.notification_outbox_worker import run_notification_outbox_worker


def test_outbox_worker_resets_org_context_after_commit(app, seed):
    delivery = MagicMock()
    delivery.id = 1
    delivery.organization_id = seed['org_a']
    delivery.delivery_method = 'in_app'
    delivery.error = None
    delivery.status = 'queued'

    with app.app_context():
        with patch(
            'jobs.base.set_job_org_context',
        ) as mock_set_ctx, patch(
            'services.notification_outbox.NotificationOutboxService.list_pending_deliveries',
            return_value=[delivery],
        ), patch(
            'jobs.notification_outbox_worker._deliver_one',
            return_value=True,
        ), patch(
            'services.notification_outbox.NotificationOutboxService.mark_delivered',
        ), patch(
            'models.db.session.commit',
        ) as mock_commit, patch(
            'models.db.session.remove',
        ), patch(
            'models.db.session.rollback',
        ):
            totals = run_notification_outbox_worker(org_id=seed['org_a'], limit=10)

            assert totals['delivered'] == 1
            assert mock_commit.called
            # Initial set + post-commit restore (at minimum).
            assert mock_set_ctx.call_count >= 2
            assert all(
                call.args[0] == seed['org_a'] for call in mock_set_ctx.call_args_list
            )


def test_outbox_worker_resets_org_context_after_error_commit(app, seed):
    delivery = MagicMock()
    delivery.id = 42
    delivery.organization_id = seed['org_a']
    delivery.delivery_method = 'in_app'
    delivery.error = None
    delivery.status = 'queued'

    failed_row = MagicMock()
    failed_row.status = 'queued'
    failed_row.error = None

    with app.app_context():
        with patch(
            'jobs.base.set_job_org_context',
        ) as mock_set_ctx, patch(
            'services.notification_outbox.NotificationOutboxService.list_pending_deliveries',
            return_value=[delivery],
        ), patch(
            'jobs.notification_outbox_worker._deliver_one',
            side_effect=RuntimeError('boom'),
        ), patch(
            'models.NotificationDelivery.query',
        ) as mock_nd_query, patch(
            'models.db.session.commit',
        ) as mock_commit, patch(
            'models.db.session.remove',
        ), patch(
            'models.db.session.rollback',
        ):
            mock_nd_query.get.return_value = failed_row

            totals = run_notification_outbox_worker(org_id=seed['org_a'], limit=10)

            assert totals['errors'] >= 1
            assert mock_commit.called
            assert mock_set_ctx.call_count >= 2
            # Last successful restore after error-path commit
            assert mock_set_ctx.call_args_list[-1].args[0] == seed['org_a']
