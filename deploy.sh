#!/bin/bash

# PythonAnywhere deployment script
# This script pulls latest changes, installs dependencies, and restarts the web app

echo "Starting deployment..."

# Change to the application directory
cd /home/yourusername/CRM

# Pull latest changes from GitHub
echo "Pulling latest changes from GitHub..."
git pull origin main

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Backup database before migrations
echo "Creating database backup..."
export FLASK_ENV=production
python manage_db.py backup

# Run database migrations if needed
echo "Checking for database migrations..."
python manage_db.py upgrade

# Restart the web app by touching the WSGI file
echo "Restarting web application..."
touch /var/www/yourusername_pythonanywhere_com_wsgi.py

echo "Deployment completed successfully!"
