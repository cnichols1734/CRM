from datetime import timedelta

class Config:
    SECRET_KEY = 'your-secret-key-here'  # Change this to a secure secret key
    SQLALCHEMY_DATABASE_URI = 'sqlite:///crm.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    
    # Mail settings
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = 'ogtechnolog@gmail.com'
    MAIL_PASSWORD = 'flblrqlmyvfxfqkx'
    MAIL_DEFAULT_SENDER = ('TechnolOG', 'ogtechnolog@gmail.com')
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False
    OPENAI_API_KEY = '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'