import sys
import os

# Add your project directory to the sys.path
project_home = '/home/yourusername/CRM'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set environment variable for Flask
os.environ['FLASK_ENV'] = 'production'

# Import your Flask app
from app import app as application

# Optional: Load environment variables from .env if it exists
try:
    from dotenv import load_dotenv
    dotenv_path = os.path.join(project_home, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
except ImportError:
    pass
