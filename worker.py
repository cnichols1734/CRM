"""
RQ worker entry point for background document extraction.

Boots the Flask app (reuses the module-level instance from app.py) so that
SQLAlchemy, config, and all services are available inside jobs.

Usage (local):
    python worker.py

Railway: set as the custom start command for the worker service.
"""
import logging
import socket
import time

from dotenv import load_dotenv
load_dotenv()

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Worker, Queue
from app import app
from config import Config

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)


def _connect_redis(url: str, attempts: int = 12, delay: float = 2.5) -> Redis:
    """
    Connect to Redis with retries.

    Railway's private DNS (*.railway.internal) is sometimes not resolvable for
    the first few seconds after the container starts. Without this, the worker
    crashes inside Worker.__init__ (which calls CLIENT SETNAME) and Railway
    just restarts it in a loop. Retrying here turns a cold-start race into a
    short delay instead of a manual-intervention outage.
    """
    last_err: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            conn = Redis.from_url(
                url,
                socket_connect_timeout=10,
                socket_timeout=30,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            conn.ping()
            log.info("connected to redis on attempt %d", i)
            return conn
        except (RedisConnectionError, socket.gaierror, OSError) as e:
            last_err = e
            log.warning("redis connect attempt %d/%d failed: %s", i, attempts, e)
            time.sleep(delay)
    raise RuntimeError(f"could not connect to redis after {attempts} attempts") from last_err


def main():
    with app.app_context():
        conn = _connect_redis(Config.REDIS_URL)
        queues = [Queue("doc_extraction", connection=conn)]
        worker = Worker(queues, connection=conn)
        worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
