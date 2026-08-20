import os
from datetime import date, timedelta

DEFAULT_APP_BASE_URL = 'https://agentflow.origentechnolog.com'
LEGACY_PUBLIC_HOSTS = frozenset({
    'www.origentechnolog.com',
    'origentechnolog.com',
})


class Config:
    # Environment
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')

    # Global NEW badge cutoff for Customize Groups (launch date + 28 days).
    # Update CUSTOMIZE_GROUPS_LAUNCH_DATE when shipping to production.
    CUSTOMIZE_GROUPS_LAUNCH_DATE = date(2026, 7, 21)
    CUSTOMIZE_GROUPS_NEW_UNTIL = date(2026, 8, 18)

    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Database configuration - supports multiple environments
    if FLASK_ENV == 'production':
        # Production: Railway provides DATABASE_URL pointing to Supabase PostgreSQL
        SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///instance/crm_prod.db')
    else:
        # Development: Use local SQLite database
        SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///instance/crm_dev.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)  # 24-hour sliding expiration

    # Connection pool settings for cloud PostgreSQL (Supabase Pro)
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 1800,
        'pool_size': 5,
        'max_overflow': 3,
    }

    # Mail settings
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = (os.getenv('MAIL_SENDER_NAME', 'AgentFlow'), os.getenv('MAIL_SENDER_EMAIL', 'noreply@example.com'))
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False

    # OpenAI configuration
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

    # Document extraction must not auto-apply extracted fields to canonical
    # seller contracts / milestones. Human-approved proposals apply later.
    EXTRACTION_AUTO_APPLY = (
        os.getenv('EXTRACTION_AUTO_APPLY', 'false').lower() == 'true'
    )

    # Phase 3 narrow autonomy thresholds (only with org flags on).
    BOB_VTC_AUTONOMY_CONFIDENCE_MAX = float(
        os.getenv('BOB_VTC_AUTONOMY_CONFIDENCE_MAX', '0.85')
    )
    BOB_VTC_AUTONOMY_RISK_MAX = os.getenv(
        'BOB_VTC_AUTONOMY_RISK_MAX', 'low'
    ).lower()

    # SendGrid configuration
    SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')

    # Marketing campaigns send from their own authenticated subdomain so a
    # campaign that draws complaints cannot take password resets and org
    # invites down with it. Same brand, separate DKIM signing domain.
    MARKETING_FROM_EMAIL = os.getenv(
        'MARKETING_FROM_EMAIL', 'agents@mail.origentechnolog.com'
    )
    MARKETING_FROM_NAME = os.getenv('MARKETING_FROM_NAME', 'AgentFlow')
    # Address in the List-Unsubscribe header for clients that prefer mailto.
    MARKETING_UNSUBSCRIBE_MAILTO = os.getenv('MARKETING_UNSUBSCRIBE_MAILTO')
    # Bounce rate that auto-pauses a running campaign, as a fraction of
    # attempted sends. Above roughly 5% mailbox providers start filtering.
    MARKETING_BOUNCE_PAUSE_RATE = float(
        os.getenv('MARKETING_BOUNCE_PAUSE_RATE', '0.05')
    )
    # Attempts below which the bounce rate is too noisy to act on.
    MARKETING_BOUNCE_PAUSE_MIN = int(
        os.getenv('MARKETING_BOUNCE_PAUSE_MIN', '50')
    )

    # Product analytics. The project token is intentionally public-safe; never
    # expose a PostHog personal API key to the application or browser.
    POSTHOG_PROJECT_TOKEN = os.getenv('POSTHOG_PROJECT_TOKEN')
    POSTHOG_HOST = os.getenv('POSTHOG_HOST', 'https://us.i.posthog.com')
    POSTHOG_ENABLED = bool(POSTHOG_PROJECT_TOKEN)
    POSTHOG_SESSION_REPLAY = (
        os.getenv('POSTHOG_SESSION_REPLAY', 'False').lower() == 'true'
    )
    ACTIVATION_EXPERIENCE_VERSION = os.getenv(
        'ACTIVATION_EXPERIENCE_VERSION', 'retention_v2'
    )
    ACTIVATION_EVENT_SCHEMA_VERSION = int(
        os.getenv('ACTIVATION_EVENT_SCHEMA_VERSION', '2')
    )
    SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY = os.getenv(
        'SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY'
    )
    APP_BASE_URL = os.getenv(
        'APP_BASE_URL', DEFAULT_APP_BASE_URL
    ).rstrip('/')

    # Redis / RQ task queue
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # RentCast API configuration
    RENTCAST_API_KEY = os.getenv('RENTCAST_API_KEY')
    RENTCAST_REFRESH_HOURS = int(os.getenv('RENTCAST_REFRESH_HOURS', 48))  # Hours before allowing re-fetch
    # Market Insights cache TTL. RentCast /markets data updates monthly upstream
    # and the free tier is 50 calls/month, so we default to 7 days. With ~5 ZIPs
    # seeded that works out to roughly 22 calls per month.
    MARKET_DATA_REFRESH_HOURS = int(os.getenv('MARKET_DATA_REFRESH_HOURS', 168))

    # DocuSeal configuration
    DOCUSEAL_WEBHOOK_SECRET = os.getenv('DOCUSEAL_WEBHOOK_SECRET')

    # Google Gmail Integration (OAuth)
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GMAIL_TOKEN_ENCRYPTION_KEY = os.getenv('GMAIL_TOKEN_ENCRYPTION_KEY')
    GMAIL_SYNC_DAYS = int(os.getenv('GMAIL_SYNC_DAYS', 30))  # Initial sync window

    # B.O.B. over Telegram. Off by default; enable per-org via feature_flags.
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME')
    TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET')
    # Opaque path segment stacked on top of the secret header. Generate once
    # with secrets.token_urlsafe(24) and keep it stable across deploys.
    TELEGRAM_WEBHOOK_PATH = os.getenv('TELEGRAM_WEBHOOK_PATH')