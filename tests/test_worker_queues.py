"""Lock the RQ worker listen list to the queues producers actually use."""


def test_worker_listens_to_inbox_bootstrap_queue():
    from services.device_push import QUEUE_NAME as APNS_QUEUE
    from services.messaging.queue import QUEUE_NAME as TELEGRAM_QUEUE
    from worker import QUEUE_NAMES

    assert QUEUE_NAMES == (
        "doc_extraction",
        "bob_telegram",
        "contract_bootstrap",
        "apns",
    )
    assert TELEGRAM_QUEUE in QUEUE_NAMES
    assert APNS_QUEUE in QUEUE_NAMES
