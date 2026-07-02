import os
import uuid
import json
import redis
import psycopg2
import psycopg2.sql as sql

def as_uuid(value) -> uuid.UUID:
    """
    Convert a string or UUID to a UUID object.

    Args:
        - value (str | uuid.UUID) : The value to convert.
    Returns:
        uuid.UUID: The converted UUID object.
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))

def db_connect() -> psycopg2.extensions.connection:
    db = {
        'dbname': env('POSTGRESQL_DBNAME')[0],
        'user': env('POSTGRESQL_USER')[0],
        'password': env('POSTGRESQL_PASSWD')[0],
        'host': env('POSTGRESQL_HOST', 'localhost')[0],
        'port': int(env('POSTGRESQL_PORT', '5432')[0]),
    }
    return psycopg2.connect(**db)

def redis_connect(decode: bool = True) -> redis.Redis:
    """
    Normalize the Redis URL for Flask-Session configuration.
    
    Returns:
        redis.Redis: A Redis client instance.
    """
    host = env('REDIS_HOST', 'localhost:6379')[0]
    user, passwd = env('REDIS_USER,REDIS_PASSWD')
    return redis.from_url(f"redis://{user}:{passwd}@{host}/0", decode_responses=decode)

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

def refresh_series_and_challenges(redis_client: redis.Redis) -> None:
    """
    Retrieve the series and challenges from the database.

    """
    SIDS_AND_CIDS: dict = {"sids": {}}
    series_table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
    challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
    series_query = sql.SQL("SELECT sid FROM {table}").format(table=series_table)
    challenges_query = sql.SQL("SELECT sid, cid FROM {table}").format(table=challenges_table)
    
    with db_connect() as conn:
        with conn.cursor() as cursor:
                cursor.execute(series_query)
                rows = cursor.fetchall()
                for row in rows: SIDS_AND_CIDS["sids"][row[0]] = {"cids": {}}
                cursor.execute(challenges_query)
                rows = cursor.fetchall()
                for row in rows:
                    sid, cid = row
                    if sid in SIDS_AND_CIDS["sids"]:
                        SIDS_AND_CIDS["sids"][sid]["cids"][cid] = {}
                    else:
                        SIDS_AND_CIDS["sids"][sid] = {"cids": {cid: {}}}
    key_prefix = env('REDIS_KEY_PREFIX')[0]
    redis_client.set(f"{key_prefix}:series_and_challenges", json.dumps(SIDS_AND_CIDS))

def raise_on_missing_series_and_challenges(redis_client: redis.Redis, sid_str: int,
                                           cid_str: int | None = None) -> None:
    """
    Raise an exception if the series and challenges data is missing in Redis.
    """
    key_prefix = env('REDIS_KEY_PREFIX')[0]
    # NOTE: Redis stores keys as strings, so for comparison, convert sid and cid to strings
    sid = str(sid_str)
    cid = str(cid_str) if cid_str is not None else None
    
    raw = redis_client.get(f"{key_prefix}:series_and_challenges")
    if not raw: raise ValueError("Series and challenges data is missing. Please refresh the data.")
    
    data = json.loads(raw)
    sids = data['sids']
    if sid not in sids: raise ValueError(f"Series {sid} is missing. Please refresh the data. {data}")
    
    if cid is None: return  # No challenge ID to check, return early
    cids = sids[sid]["cids"]
    if cid not in cids: raise ValueError(f"Challenge {cid} is missing for Series {sid}. Please refresh the data. {data}")