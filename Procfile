# Railway does not support Heroku-style `release:` phases. Railpack reads this
# Procfile for the web start command, so migrations run as part of boot.
# The Vite frontend is built in railpack.json (Node is a build tool, not the runtime).
# Do not put a startCommand in railpack.json: document-worker and the crons share
# this repo and keep their own start commands in Railway.
web: python3 scripts/manage_db.py upgrade && gunicorn app:app --bind 0.0.0.0:5011 --workers 2 --timeout 120 --log-level warning --max-requests 10000 --max-requests-jitter 500
