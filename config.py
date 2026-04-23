import os
from datetime import timedelta

class Config:
    # Environment
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')

    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Database configuration - supports multiple environments
    if FLASK_ENV == 'production':
        # Production: Use PythonAnywhere MySQL or specified database
        SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:////home/yourusername/CRM/instance/crm_prod.db')
    else:
        # Development: Use local SQLite database
        SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///instance/crm_dev.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)  # 24-hour sliding expiration

    # Connection pool health settings for cloud PostgreSQL (Supabase)
    # - pool_pre_ping: Test connection before use (prevents stale connection errors)
    # - pool_recycle: Recycle connections every 5 min (before Supabase timeout)
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Mail settings
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = (os.getenv('MAIL_SENDER_NAME', 'Origen TechnolOG'), os.getenv('MAIL_SENDER_EMAIL', 'noreply@example.com'))
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False

    # OpenAI configuration
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

    # SendGrid configuration
    SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')

    # Redis / RQ task queue
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # RentCast API configuration
    RENTCAST_API_KEY = os.getenv('RENTCAST_API_KEY')
    RENTCAST_REFRESH_HOURS = int(os.getenv('RENTCAST_REFRESH_HOURS', 48))  # Hours before allowing re-fetch
    # Market Insights cache TTL. RentCast /markets data updates monthly upstream
    # and the free tier is 50 calls/month, so we default to 7 days. With ~5 ZIPs
    # seeded that works out to roughly 22 calls per month.
    MARKET_DATA_REFRESH_HOURS = int(os.getenv('MARKET_DATA_REFRESH_HOURS', 168))

    # Google Gmail Integration (OAuth)
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GMAIL_TOKEN_ENCRYPTION_KEY = os.getenv('GMAIL_TOKEN_ENCRYPTION_KEY')
    GMAIL_SYNC_DAYS = int(os.getenv('GMAIL_SYNC_DAYS', 30))  # Initial sync window