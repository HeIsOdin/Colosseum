from flask_login import LoginManager
from dotenv import load_dotenv
from hypogeum.armamentarium import redis_connect

load_dotenv() # remove for prod

NAME = "Hypogeum"
login_manager = LoginManager()
REDIS_CLIENT = redis_connect()

USER_STATUS = ['active', 'verified', 'suspended', 'banned']
DIFFICULTY_LEVELS = ['Sanity Check', 'Easy', 'Medium', 'Hard']
CATEGORIES = ['Warmup', 'Web', 'Crypto', 'Forensics', 'Pwn', 'Misc']