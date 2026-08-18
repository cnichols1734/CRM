"""Lock the RQ worker listen list to the queues producers actually use."""


def test_worker_listens_to_inbox_bootstrap_queue():
    from worker import QUEUE_NAMES

    assert QUEUE_NAMES == (
        "doc_extraction",
        "bob_telegram",
        "contract_bootstrap",
    )
