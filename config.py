from datetime import timedelta

class Config:
    SECRET_KEY = 'your-secret-key-here'  # Change this to a secure secret key
    SQLALCHEMY_DATABASE_URI = 'sqlite:///crm.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)