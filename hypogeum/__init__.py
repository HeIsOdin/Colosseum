from flask_login import LoginManager
from dotenv import load_dotenv
from hypogeum.armamentarium import redis_connect

load_dotenv() # remove for prod

API_ROOT = '/api'

login_manager = LoginManager()
REDIS_CLIENT = redis_connect()

USER_STATUS = ['active', 'verified', 'suspended', 'banned']
DIFFICULTY_LEVELS = ['Sanity Check', 'Easy', 'Medium', 'Hard']
CATEGORIES = ['Warmup', 'Web', 'Crypto', 'Forensics', 'Pwn', 'Misc']
# NOTE: Know what you are doing. Intermediate states must always end with "ing"
INSTANCE_STATES = [
    'starting',  'started', 'pausing', 'paused',  'stopping', 'stopped',
    'resuming', 'restarting', 'resetting', 'failed'
]
INSTANCES_TYPES = ['private', 'shared']