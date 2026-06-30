from __init__ import REDIS_CLIENT
import os
import json
import redis
import psycopg2
import psycopg2.sql as sql

def db_connect() -> psycopg2.extensions.connection:
    db = {
        'dbname': env('POSTGRESQL_DBNAME')[0],
        'user': env('POSTGRESQL_USER')[0],
        'password': env('POSTGRESQL_PASSWD')[0],
        'host': env('POSTGRESQL_HOST', 'localhost')[0],
        'port': int(env('POSTGRESQL_PORT', '5432')[0]),
    }
    return psycopg2.connect(**db)

def redis_connect() -> redis.Redis:
    """
    Normalize the Redis URL for Flask-Session configuration.
    
    Returns:
        str: A normalized Redis URL in the format `redis://user:password@host:port/`
    """
    host = env('REDIS_HOST', 'localhost:6379')[0]
    user, passwd = env('REDIS_USER,REDIS_PASSWD')
    return redis.from_url(f"redis://{user}:{passwd}@{host}/0", decode_responses=True)

def env(keys: str, defaults: str = '', delimiter: str = ",") -> tuple[str, ...]:
    """
    Retrieve environment variables.

    Args:
        - vars      (str) : variables set in the environment
        - defaults  (str) : default values for the variables, separated by the specified delimiter
        - delimiter (str) : the character used to separate default values in the defaults string
    
    Returns:
        tuple (number of arguments passed): values of environmental variables
    """
    values: list[str] = []
    l_keys = keys.split(delimiter); l_defaults = defaults.split(delimiter)
    while len(l_defaults) < len(l_keys): l_defaults.append('') # Pad defaults with empty strings if not enough provided
    for key, default in zip(l_keys, l_defaults):
        key = key.strip()
        if not key: raise ValueError("Environment variable names must not be empty.")
        value = os.getenv(key)
        if value: values.append(value)
        elif default: values.append(default.strip())
    
    if len(values) != len(l_keys):
        raise Exception(f"Some keys in {l_keys} not set in environment without defaults.")

    return tuple(values)

def refresh_series_and_challenges():
    """
    Retrieve the series and challenges from the database.

    """
    table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
    query = sql.SQL("SELECT sid, cid FROM {table}").format(table=table)
    SIDS_AND_CIDS: dict = {"sids": {}}
    with db_connect() as conn:
        with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
                for row in rows:
                    sid, cid = row
                    if sid not in SIDS_AND_CIDS["sids"]:
                        SIDS_AND_CIDS["sids"][sid] = {"cids": {}}
                    if cid not in SIDS_AND_CIDS["sids"][sid]["cids"]:
                        SIDS_AND_CIDS["sids"][sid]["cids"][cid] = {}
    key_prefix = env('REDIS_KEY_PREFIX')[0]
    REDIS_CLIENT.set(f"{key_prefix}:series_and_challenges", json.dumps(SIDS_AND_CIDS))

def raise_on_missing_series_and_challenges(sid: int, cid: int | None = None) -> None:
    """
    Raise an exception if the series and challenges data is missing in Redis.
    """
    key_prefix = env('REDIS_KEY_PREFIX')[0]
    
    raw = REDIS_CLIENT.get(f"{key_prefix}:series_and_challenges")
    if not raw: raise Exception("Series and challenges data is missing. Please refresh the data.")
    
    data = json.loads(raw)
    sids = data['sids']
    if sid not in sids: raise Exception(f"Series {sid} is missing. Please refresh the data.")
    
    if cid is None: return  # No challenge ID to check, return early
    cids = sids[sid]["cids"]
    if cid not in cids: raise Exception(f"Challenge {cid} is missing for Series {sid}. Please refresh the data.")