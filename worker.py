"""
RQ worker entry point for background document extraction.

Boots the Flask app (reuses the module-level instance from app.py) so that
SQLAlchemy, config, and all services are available inside jobs.

Usage (local):
    python worker.py

Railway: set as the custom start command for the worker service.
"""
from dotenv import load_dotenv
load_dotenv()

from redis import Redis
from rq import Worker, Queue
from app import app
from config import Config

def main():
    with app.app_context():
        conn = Redis.from_url(Config.REDIS_URL)
        queues = [Queue('doc_extraction', connection=conn)]
        worker = Worker(queues, connection=conn)
        worker.work()


if __name__ == '__main__':
    main()
