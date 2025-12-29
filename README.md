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
# Note: DATABASE_URL is automatically set based on FLASK_ENV
```

5. Initialize the database:
```bash
# For development
python manage_db.py init

# For production (on PythonAnywhere)
FLASK_ENV=production python manage_db.py init
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

## Database Management

The application supports separate databases for development and production environments.

### Database Configuration

- **Development:** Uses `instance/crm_dev.db` (local SQLite)
- **Production:** Uses `instance/crm_prod.db` on PythonAnywhere or MySQL

The database is automatically selected based on the `FLASK_ENV` environment variable.

### Database Operations

Use the `manage_db.py` script for all database operations:

```bash
# Initialize a new database
python manage_db.py init

# Set up migrations (first time only)
python manage_db.py setup

# Create a new migration
python manage_db.py migrate "add new feature"

# Upgrade database to latest migration
python manage_db.py upgrade

# Check migration status
python manage_db.py status

# Backup database (SQLite only)
python manage_db.py backup
```

### Environment-Specific Operations

```bash
# Development database operations
python manage_db.py <command>

# Production database operations (on PythonAnywhere)
FLASK_ENV=production python manage_db.py <command>
```

### Important Notes

- **Never commit database files** - they're automatically excluded by `.gitignore`
- **Backup before migrations** - use `python manage_db.py backup`
- **Test migrations locally first** - before applying to production
- **Use different databases** - development and production should be separate

## PythonAnywhere Deployment

### Initial Setup

1. **Clone your repository on PythonAnywhere:**
```bash
cd ~
git clone https://github.com/cnichols1734/CRM.git
cd CRM
```

2. **Set up virtual environment:**
```bash
python3.9 -m venv venv  # Use Python 3.9 or your preferred version
source venv/bin/activate
pip install -r requirements.txt
```

3. **Configure environment variables:**
```bash
cp .env.example .env
# Edit .env with your production API keys and settings
# Set FLASK_ENV=production for production database
```

4. **Initialize database:**
```bash
# Set production environment and initialize database
export FLASK_ENV=production
python manage_db.py init
```

### Web App Configuration

1. Go to the **Web** tab in PythonAnywhere
2. Create a new web app or modify existing one
3. Set the **Source code** path to: `/home/yourusername/CRM`
4. Set the **Working directory** to: `/home/yourusername/CRM`
5. Set the **WSGI configuration file** to: `/home/yourusername/CRM/pythonanywhere_wsgi.py`
6. Set **Virtualenv** path to: `/home/yourusername/CRM/venv`

### Automated Updates

#### Option 1: Manual Git Pull (Simplest)
```bash
cd ~/CRM
git pull origin main
# Restart web app from PythonAnywhere dashboard
```

#### Option 2: Scheduled Task (Recommended)
1. Go to **Tasks** tab in PythonAnywhere
2. Create a new scheduled task:
   - Command: `cd /home/yourusername/CRM && ./deploy.sh`
   - Schedule: Daily at your preferred time
3. Create `deploy.sh` in your CRM directory:
```bash
#!/bin/bash
cd /home/yourusername/CRM
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
# Touch WSGI file to restart app
touch /var/www/yourusername_pythonanywhere_com_wsgi.py
```

#### Option 3: GitHub Webhook (Advanced)
Set up a webhook that triggers on GitHub pushes to automatically deploy.

### Database Considerations

- Use PythonAnywhere's MySQL database for production
- Update `DATABASE_URL` in `.env` to point to your MySQL database
- Run migrations after updates: `flask db upgrade`
