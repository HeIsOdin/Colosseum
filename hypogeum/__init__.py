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
    'starting', 'started', 'pausing', 'paused', 'stopping', 'stopped',
    'resuming', 'restarting', 'resetting', 'failed'
]

INSTANCE_TRANSITIONS = {
    None: {
        'start': {'intermediate': 'starting', 'final': 'started'},
    },
    'stopped': {
        'start': {'intermediate': 'starting', 'final': 'started'},
    },
    'started': {
        'pause': {'intermediate': 'pausing', 'final': 'paused'},
        'stop': {'intermediate': 'stopping', 'final': 'stopped'},
        'restart': {'intermediate': 'restarting', 'final': 'started'},
        'reset': {'intermediate': 'resetting', 'final': 'started'},
    },
    'paused': {
        'resume': {'intermediate': 'resuming', 'final': 'started'},
        'stop': {'intermediate': 'stopping', 'final': 'stopped'},
        'restart': {'intermediate': 'restarting', 'final': 'started'},
    },
    'failed': {},
}

# Compatibility projection used by the existing control function.
ALLOWED_TRANSITIONS = {
    previous_status: {
        action: transition['intermediate']
        for action, transition in actions.items()
    }
    for previous_status, actions in INSTANCE_TRANSITIONS.items()
}

# Worker projection derived from the same source of truth.
WORKER_TRANSITIONS = {
    transition['intermediate']: transition['final']
    for actions in INSTANCE_TRANSITIONS.values()
    for transition in actions.values()
}

INSTANCE_COLUMNS = (
    'sid',
    'cid',
    'host',
    'port',
    'type',
    'status',
    'created_at',
    'started_at',
    'paused_at',
    'expires_at',
    'updated_at',
)

INSTANCES_TYPES = ['private', 'shared']
