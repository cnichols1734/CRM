# CRM Application

A Flask-based Customer Relationship Management system with AI chat capabilities, task management, and marketing tools.

## Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd CRM
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your actual API keys and configuration
```

5. Initialize the database:
```bash
python init_db.py
```

6. Run the application:
```bash
python app.py
```

## Environment Variables

The application uses the following environment variables (configured in `.env`):

- `FLASK_ENV`: Environment (development/production)
- `SECRET_KEY`: Flask secret key
- `DATABASE_URL`: Database connection string
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`: Email server settings
- `MAIL_USERNAME`, `MAIL_PASSWORD`: Email credentials
- `MAIL_SENDER_NAME`, `MAIL_SENDER_EMAIL`: Email sender information
- `OPENAI_API_KEY`: OpenAI API key for AI chat features
- `SENDGRID_API_KEY`: SendGrid API key for email services

## Features

- User authentication and registration
- Contact management
- Task management
- Daily todo lists
- AI-powered chat assistant
- Marketing campaign management
- Email integration
- Admin panel for user management

## Database Migrations

The application uses Flask-Migrate for database migrations:

```bash
# Create a new migration
flask db migrate -m "Description of changes"

# Apply migrations
flask db upgrade

# Rollback
flask db downgrade
```
