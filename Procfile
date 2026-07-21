# Railway does not support Heroku-style `release:` phases, and this Procfile
# overrides the start command in nixpacks.toml — so migrations must run as
# part of the web process command itself.
web: python3 scripts/manage_db.py upgrade && gunicorn app:app --bind 0.0.0.0:5011 --workers 2 --timeout 120 --log-level warning --max-requests 10000 --max-requests-jitter 500
