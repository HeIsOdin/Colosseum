from flask import Flask, jsonify, request
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask_session import Session
from datetime import timedelta
from flask_cors import CORS
import psycopg2.sql
from supabase import create_client, Client
from functools import wraps
from dotenv import load_dotenv
from armamentarium import env, raise_on_invalid_creds

import os
import uuid
import bcrypt
import hashlib
import logging
import psycopg2

app = Flask(__name__)
load_dotenv()  # Remove this for production; it's only for local development
supabase: Client = create_client(env('SUPABASE_URL')[0], env('SUPABASE_KEY')[0])
(app.secret_key,) = env('COLOSSEUM_SECRET_KEY')
app.config['SESSION_TYPE'] = 'filesystem'
SESSION_FILE_DIR = os.path.join(os.getcwd(), 'processes', 'flask_sessions')
os.makedirs(SESSION_FILE_DIR, exist_ok=True)
app.config['SESSION_FILE_DIR'] = SESSION_FILE_DIR
login_manager = LoginManager()
login_manager.init_app(app)
Session(app)
HOST, PORT = env('POSTGRESQL_HOST,POSTGRESQL_PORT', 'localhost,5432')
DATABASE, USER, PASSWORD = env('POSTGRESQL_DBNAME,POSTGRESQL_USER,POSTGRESQL_PASSWD')
app.config["COLOSSEUM_DB"] = {
    "host": HOST,
    "port": PORT,
    "dbname": DATABASE,
    "user": USER,
    "password": PASSWORD
}

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({
        'redirect':'login.html'
    })

class User(UserMixin):
    def __init__(self, pid: str, sids: list | None = None):
        self.id = pid
        self.sids = sids or [0]

@login_manager.user_loader
def load_user(user_id):
    db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
    with psycopg2.connect(db_params) as conn:
        with conn.cursor() as cursor:
            table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
            query = psycopg2.sql.SQL("SELECT sids FROM {table} WHERE pid = %s").format(
                table=table
            )
            cursor.execute(query, (user_id,))
            res = cursor.fetchone()
            if not res:
                return None
            sids = list(res[0]) if res[0] is not None else [0]
    return User(user_id, sids)

def series_signup_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        sid = kwargs.get('sid')
        if sid is None:
            return jsonify({"error": "Series ID not provided"}), 400
        if sid not in current_user.sids:
            return jsonify({"error": "User not signed up for this series"}), 403
        return f(*args, **kwargs)
    return decorated_function

# -- Authentication & Profile --

def _login(email: str, password: str) -> tuple[dict, bool, str, int]:
    """
    Attempt to log in a user with the provided email and password.

    Args:
        - email (str) : The email of the user.
        - password (str) : The password of the user.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        query = psycopg2.sql.SQL("SELECT pid, sids, password FROM {table} WHERE email = %s").format(
            table=table
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (email,))
                res = cursor.fetchall()
                if len(res) > 1: raise AssertionError(f"Multiple users found: {res}")
                if not res:
                    return {}, False, "Invalid credentials", 401
                actual_password = str(res[0][2])
                if actual_password is None: return {}, False, "Invalid credentials", 401
                if bcrypt.checkpw(password.encode('utf-8'), actual_password.encode('utf-8')):
                    return {'pid': str(res[0][0]), 'sids': list(res[0][1])}, True, "", 200
                else:
                    return {}, False, "Invalid email or password.", 401
    except AssertionError as ae:
        logger.error(f"Assertion error during login for user {email}: {ae}")
        return {}, False, "Internal Server Error", 500
    except Exception as e:
        logger.exception(f"Error during login for user {email}: {e}")
        return {}, False, "Internal server error", 500

@app.post('/me')
def login():
    data = request.form or request.get_json()
    email = str(data.get("email"))
    password = str(data.get("password"))
    details, success, message, status_code = _login(email, password)
    if success:
        user = User(**details)
        login_user(user, remember=True, duration=timedelta(days=1))
    return jsonify({"success": success, "message": message}), status_code

@app.post('/logout')
@login_required
def logout():
    logger = logging.getLogger('Hypogeum')
    try:
        logout_user()
        logger.info("User logged out successfully.")
        return jsonify({"result": "Logout successful"}), 200
    except Exception as e:
        logger.exception(f"Error during logout: {e}")
        return jsonify({"error": "Internal server error"}), 500


def _register(email: str, password: str) -> tuple[bool, str, int]:
    """
    Register a new user in the database.

    Args:
        - email (str) : The email of the new user.
        - password (str) : The password of the new user.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        raise_on_invalid_creds(email, password)
        salt = bcrypt.gensalt()
        pid = uuid.uuid4().hex
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        cols = ['pid', 'email', 'password']
        columns = psycopg2.sql.SQL(', ').join(psycopg2.sql.Identifier(col) for col in cols)
        values_clause = psycopg2.sql.SQL(', ').join(psycopg2.sql.Placeholder() for _ in cols)
        query = psycopg2.sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
            table=table,
            columns=columns,
            values=values_clause
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid, email, hashed_password))
        return True, "User registered successfully.", 201
    except ValueError as ve:
        logger.warning(f"Validation error during registration for user {email}: {ve}")
        return False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error during registration for user {email}: {e}")
        return False, "Internal server error", 500

@app.put('/me')
def register():
    data = request.form or request.get_json()
    email = str(data.get("email"))
    password = str(data.get("password"))
    success, message, status_code = _register(email, password)
    return jsonify({"success": success, "message": message}), status_code

def _delete(pid: str):
    logger = logging.getLogger('Hypogeum')
    try:
        table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        query = psycopg2.sql.SQL("DELETE FROM {table} WHERE pid = %s").format(table=table)
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid,))
        logout_user()
        logger.debug(f"User {pid} deleted successfully.")
        return True, "User deleted successfully.", 200
    except Exception as e:
        logger.exception(f"Error during user deletion for user {pid}: {e}")
        return False, "Internal server error", 500

@app.delete('/me')
@login_required
def delete():
    success, message, status_code = _delete(current_user.id)
    return jsonify({"success": success, "message": message}), status_code

def _update_profile(pid: str, **profile,):
    logger = logging.getLogger('Hypogeum')
    try:
        table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        set_clause = psycopg2.sql.SQL(', ').join(
            psycopg2.sql.SQL("{} = {}").format(psycopg2.sql.Identifier(k), psycopg2.sql.Placeholder())
            for k in profile.keys()
        )
        query = psycopg2.sql.SQL("UPDATE {table} SET {set_clause} WHERE pid = %s").format(
            table=table,
            set_clause=set_clause
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (*profile.values(), pid))
        logger.debug(f"User {pid} profile updated successfully.")
        return True, "Profile updated successfully.", 200
    except Exception as e:
        logger.exception(f"Error during profile update for user {pid}: {e}")
        return False, "Internal server error", 500

@app.patch('/me')
@login_required
def update_profile():
    data = request.form or request.get_json()
    profile = {k: v for k, v in data.items() if k in ['display_name', 'avatar', 'email']}
    success, message, status_code = _update_profile(current_user.id, **profile)
    return jsonify({"success": success, "message": message}), status_code

# -- Series --

def _get_series_list(offset: int = 0) -> tuple[list, bool, str, int]:
    """
    Retrieve the list of series from the database.

    Returns:
        list: A list of dictionaries, each representing a series.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        table = psycopg2.sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        columns = psycopg2.sql.SQL(', ').join(
            psycopg2.sql.Identifier(col)
            for col in ['title', 'description', 'start_date', 'end_date', 'image']
        )
        query = psycopg2.sql.SQL("SELECT {columns} FROM {table} OFFSET %s LIMIT 10").format(
            table=table,
            columns=columns,
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (offset,))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    returned_columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    res.append(dict(zip([desc[0] for desc in returned_columns], row)))
        return res, True, "Series list retrieved successfully.", 200
    except Exception as e:
        logger.exception(f"Error retrieving series list: {e}")
        return [], False, "Internal server error", 500

@app.get('/series/')
def get_series_list():
    offset = request.args.get('offset', default=0, type=int)
    series_list, success, message, status_code = _get_series_list(offset)
    return jsonify({"success": success, "message": message, "series": series_list}), status_code

def _get_series_data(sid: int) -> tuple[dict, bool, str, int]:
    """
    Retrieve the data for a specific series by its ID.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        dict | None: A dictionary representing the series data, or None if not found.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        SIDS = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in SIDS: raise ValueError("Series is unavailable or not found.")
        left_table = psycopg2.sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        right_table = psycopg2.sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        left_columns = psycopg2.sql.SQL(', ').join(
                psycopg2.sql.Identifier(left_table.string, col)
                for col in ['title', 'description', 'start_date', 'end_date', 'image']
            )
        right_columns = psycopg2.sql.SQL(', ').join(
                psycopg2.sql.Identifier(right_table.string, col)
                for col in ['title', 'description', 'points', 'category', 'difficulty']
            )
        query = psycopg2.sql.SQL("SELECT {left_columns}, {right_columns} FROM {left_table} " \
                                "LEFT JOIN {right_table} ON {left_table}.sid = {right_table}.sid " \
                                "WHERE {left_table}.sid = %s").format(
                                    left_table=left_table,
                                    right_table=right_table,
                                    left_columns=left_columns,
                                    right_columns=right_columns
                                )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    if cursor.description is None:
                        returned_columns = []
                    else:
                        returned_columns = [desc[0] for desc in cursor.description]
                    res.append(dict(zip([desc[0] for desc in returned_columns], row)))
        if not res: return {}, False, "Series ID not found.", 404
        return res[0], True, "Series data retrieved successfully.", 200
    except ValueError as ve:
        logger.warning(f"Validation error while retrieving series data for Series ID {sid}: {ve}")
        return {}, False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving series data for Series ID {sid}: {e}")
        return {}, False, "Internal server error", 500

@app.get('/series/<int:sid>')
def get_series_data(sid: int):
    series_data, success, message, status_code = _get_series_data(sid)
    return jsonify({"success": success, "message": message, "series": series_data}), status_code


def _get_series_overview(sid: int) -> tuple[dict, bool, str, int]:
    """
    Retrieve the overview for a specific series by its ID.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        dict: A dictionary representing the series overview
    """
    logger = logging.getLogger('Hypogeum')
    try:
        SIDS = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in SIDS: raise ValueError("Series is unavailable or not found.")
        table = psycopg2.sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        columns = psycopg2.sql.SQL(', ').join(
            psycopg2.sql.Identifier(col)
            for col in ['title', 'description', 'start_date', 'end_date', 'image']
        )
        query = psycopg2.sql.SQL("SELECT {columns} FROM {table} WHERE sid = %s").format(
            table=table,
            columns=columns,
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,))
                row = cursor.fetchone()
                if not row: return {}, False, "Series ID not found.", 404
                if cursor.description is None:
                    returned_columns = []
                else:
                    returned_columns = [desc[0] for desc in cursor.description]
                res = dict(zip([desc[0] for desc in returned_columns], row))
        return res, True, "Series overview retrieved successfully.", 200
    except ValueError as ve:
        logger.warning(f"Validation error while retrieving overview for Series ID {sid}: {ve}")
        return {}, False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving series overview for Series ID {sid}: {e}")
        return {}, False, "Internal server error", 500

@app.get('/series/<int:sid>/overview/')
def get_series_overview(sid: int):
    overview, success, message, status_code = _get_series_overview(sid)
    return jsonify({"success": success, "message": message, "overview": overview}), status_code

def _get_submissions(sid: int) -> tuple[list, bool, str, int]:
    """
    Retrieve the submissions for a specific series by its ID.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        list: A list of dictionaries, each representing a submission.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        SIDS = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in SIDS: raise ValueError("Series is unavailable or not found.")
        submissions_table = psycopg2.sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        players_table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        columns = psycopg2.sql.SQL(', ').join([
            psycopg2.sql.SQL(', ').join(
            psycopg2.sql.Identifier(submissions_table.string, col)
            for col in ['points'] # from submissions table
        ),
            psycopg2.sql.SQL(', ').join(
            psycopg2.sql.Identifier(players_table.string, col)
            for col in ['display_name', 'avatar'] # from players table
        )
        ])
        query = psycopg2.sql.SQL("SELECT {columns} FROM {s_table} " \
                                "LEFT JOIN {c_table} ON {s_table}.pid = {c_table}.pid " \
                                "WHERE {s_table}.sid = %s"
                                ).format(
                                    s_table=submissions_table,
                                    c_table=players_table,
                                    columns=columns,
                                )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    res.append({"sid": row[0],"cid": row[1],"pid": row[2],"points": row[3]})
        return res, True, "Submissions retrieved successfully.", 200
    except ValueError as ve:
        logger.warning(f"Validation error while retrieving submissions for Series ID {sid}: {ve}")
        return [], False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving submissions for Series ID {sid}: {e}")
        return [], False, "Internal server error", 500

@app.get('/series/<int:sid>/submissions/')
@login_required
@series_signup_required
def get_scoreboard(sid: int):
    entries, success, message, status_code = _get_submissions(sid)
    return jsonify({"success": success, "message": message, "submissions": entries}), status_code

# -- Challenges --
    
def _control_challenge_instance(sid: int, cid: int, pid: str, action: str) -> tuple[bool, str, int]:
    """
    Control the state of a challenge instance (start, stop, restart).

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - action (str) : The action to perform ('start', 'stop', 'restart').
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        SIDS = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in SIDS: raise ValueError(f"Series {sid} is unavailable or not found.")
        CIDS = dict(SIDS[sid]["cids"])
        if cid not in CIDS: raise ValueError(f"Challenge {cid} is unavailable or not found.")

        action = action.lower()
        if action == "start":
            logger.info(f"Starting challenge instance for Series ID {sid}, Challenge ID {cid}.")
            return True, "Challenge instance started.", 200
        elif action == "stop":
            logger.info(f"Stopping challenge instance for Series ID {sid}, Challenge ID {cid}.")
            return True, "Challenge instance stopped.", 200
        elif action == "restart":
            logger.info(f"Restarting challenge instance for Series ID {sid}, Challenge ID {cid}.")
            return True, "Challenge instance restarted.", 200
        else:
            logger.warning(f"Invalid action '{action}' for Series ID {sid}, Challenge ID {cid}.")
            return False, "Invalid action. Use 'start', 'stop', or 'restart'.", 400
    except ValueError as ve:
        logger.warning(f"Validation error in controlling challenge instance: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error controlling challenge instance for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@app.patch('/series/<int:sid>/challenges/<int:cid>/')
@login_required
@series_signup_required
def control_challenge_instance(sid: int, cid: int):  
    request_data = request.form or request.get_json()
    action = str(request_data.get("action")).lower()
    pid = current_user.id
    success, message, status_code = _control_challenge_instance(sid, cid, pid, action)
    return jsonify({"success": success, "message": message}), status_code

def _submit_flag(sid: int, cid: int, pid: str, submitted_flag: str) -> tuple[bool, str, int]:
    """
    Submit a flag for a specific challenge in a series.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - submitted_flag (str) : The flag submitted by the user.
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        submitted_flag_hash = hashlib.md5(submitted_flag.strip().encode('utf-8')).hexdigest()

        SIDS = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in SIDS: raise ValueError(f"Series {sid} is unavailable or not found.")
        CIDS = dict(SIDS[sid]["cids"])
        if cid not in CIDS: raise ValueError(f"Challenge {cid} is unavailable or not found.")

        insert_table = psycopg2.sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        select_table = psycopg2.sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        columns = psycopg2.sql.SQL(', ').join(
            psycopg2.sql.Identifier(col) for col in ['sid', 'cid', 'points']
        )
        query = psycopg2.sql.SQL("INSERT INTO {insert_table} ({columns}, pid) " \
                                "SELECT {columns}, %s AS pid FROM {select_table} " \
                                "WHERE sid = %s AND cid = %s AND flag = %s " \
                                "RETURNING fid").format(
                                    insert_table=insert_table,
                                    select_table=select_table,
                                    columns=columns
                                )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid, sid, cid, submitted_flag_hash))
                res = cursor.fetchall()
                if not res: return False, "Wrong Flag", 404
                _control_challenge_instance(sid, cid, pid, "stop")
                return True, "Correct Flag", 200
    except ValueError as ve:
        logger.warning(f"Validation error in submitting flag: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error submitting flag for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@app.post('/series/<int:sid>/challenges/<int:cid>/')
@login_required
@series_signup_required
def submit_flag(sid: int, cid: int):
    data = request.form or request.get_json()
    submitted_flag = str(data.get("flag"))
    success, message, status_code = _submit_flag(sid, cid, current_user.id, submitted_flag)
    return jsonify({"success": success, "message": message}), status_code

# -- Players --

def _get_player_data(pid: str) -> tuple[dict, bool, str, int]:
    """
    Retrieve the data for a specific player by their ID.

    Args:
        - pid (str) : The ID of the player.
    
    Returns:
        dict: A dictionary representing the player data
    """
    logger = logging.getLogger('Hypogeum')
    try:
        players_table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        submissions_table = psycopg2.sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        challenges_table = psycopg2.sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        columns = psycopg2.sql.SQL(', ').join([
            psycopg2.sql.SQL(', ').join(
                psycopg2.sql.Identifier(players_table.string, col)
                for col in ['display_name', 'avatar', 'email']
            ),
            psycopg2.sql.SQL(', ').join(
                psycopg2.sql.Identifier(submissions_table.string, col)
                for col in ['sid', 'cid', 'points', 'submission_time']
            ),
            psycopg2.sql.SQL(', ').join(
                psycopg2.sql.Identifier(challenges_table.string, col)
                for col in ['category', 'difficulty']
            )
        ])
        query = psycopg2.sql.SQL("SELECT {columns} FROM {p_table} " \
                                "LEFT JOIN {s_table} ON {p_table}.pid = {s_table}.pid " \
                                "LEFT JOIN {c_table} ON {s_table}.cid = {c_table}.cid " \
                                "WHERE {p_table}.pid = %s").format(
                                    p_table=players_table,
                                    s_table=submissions_table,
                                    c_table=challenges_table,
                                    columns=columns
                                )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid,))
                row = cursor.fetchone()
                if not row: return {}, False, "Player ID not found.", 404
                if cursor.description is None:
                    returned_columns = []
                else:
                    returned_columns = [desc[0] for desc in cursor.description]
                res = dict(zip([desc[0] for desc in returned_columns], row))
        return res, True, "Player data retrieved successfully.", 200
    except Exception as e:
        logger.exception(f"Error retrieving player data for Player ID {pid}: {e}")
        return {}, False, "Internal server error", 500

@app.get('/players/<uuid:pid>/')
def get_player_data(pid: str):
    player_data, success, message, status_code = _get_player_data(pid)
    return jsonify({"success": success, "message": message, "player": player_data}), status_code

def _add_player_to_series(sid: int, pid: str) -> tuple[bool, str, int]:
    """
    Add a player to a specific series.

    Args:
        - sid (int) : The ID of the series.
        - pid (str) : The ID of the player.
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        series_data = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in series_data:
            logger.warning(f"Series ID {sid} not found.")
            return False, "Series ID not found", 404
        
        table_name = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        query = psycopg2.sql.SQL("SELECT sids FROM {table} WHERE pid = %s").format(table=table_name)
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid,))
                res = cursor.fetchone()
                if not res:
                    logger.warning(f"Player ID {pid} not found.")
                    return False, "Player ID not found", 404
                player_sids = list(res[0]) if res[0] is not None else []
                if sid in player_sids:
                    logger.warning(f"Player ID {pid} already exists in series {sid}.")
                    return False, "Player ID already exists in series", 400
                player_sids.append(sid)
                update_query = psycopg2.sql.SQL("UPDATE {table} SET sids = %s WHERE pid = %s").format(table=table_name)
                cursor.execute(update_query, (player_sids, pid))
        return True, f"Player {pid} added to series {sid}.", 201
    except Exception as e:
        logger.exception(f"Error adding player to Series ID {sid}: {e}")
        return False, "Internal server error", 500

@app.put('/series/<int:sid>/players/<uuid:pid>/')
@login_required
def add_player_to_series(sid, pid):
    success, message, status_code = _add_player_to_series(sid, pid)
    if success: current_user.sids.append(sid)
    return jsonify({"success": success, "message": message}), status_code

def _remove_player_from_series(sid: int, pid: str) -> tuple[bool, str, int]:
    """
    Remove a player from a specific series.

    Args:
        - sid (int) : The ID of the series.
        - pid (str) : The ID of the player.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger('Hypogeum')
    try:
        series_data = dict(app.config['COLOSSEUM_DATA']["sids"])
        if sid not in series_data:
            logger.warning(f"Series ID {sid} not found.")
            return False, "Series ID not found", 404
        
        user_table = psycopg2.sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        submissions_table = psycopg2.sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        query_for_user = psycopg2.sql.SQL("SELECT sids FROM {table} WHERE pid = %s").format(
            table=user_table
        )
        query_for_subs = psycopg2.sql.SQL("DELETE FROM {table} WHERE sid = %s AND pid = %s").format(
            table=submissions_table
        )
        db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
        with psycopg2.connect(db_params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query_for_user, (pid,))
                res = cursor.fetchone()
                if not res:
                    logger.warning(f"Player ID {pid} not found.")
                    return False, "Player ID not found", 404
                player_sids = list(res[0]) if res[0] is not None else []
                if sid not in player_sids:
                    logger.warning(f"Player ID {pid} does not exist in series {sid}.")
                    return False, "Player ID does not exist in series", 400
                player_sids.remove(sid)
                update_q = psycopg2.sql.SQL("UPDATE {table} SET sids = %s WHERE pid = %s").format(
                    table=user_table
                )
                cursor.execute(update_q, (player_sids, pid))
                cursor.execute(query_for_subs, (sid, pid))
        return True, f"Player {pid} removed from series {sid}.", 200
    except Exception as e:
        logger.exception(f"Error removing player from Series ID {sid}: {e}")
        return False, "Internal server error", 500

@app.delete('/series/<int:sid>/players/<uuid:pid>/')
@login_required
def remove_player_from_series(sid, pid):
    success, message, status_code = _remove_player_from_series(sid, pid)
    if success and sid in current_user.sids: current_user.sids.remove(sid)
    return jsonify({"success": success, "message": message}), status_code

@app.get('/health')
def health_check():
    return jsonify(**health()), 200

def health() -> dict[str, list]:
    logger = logging.getLogger('Hypogeum')
    checklist: list[str] = []
    checks: list[bool] = []
    pid: str = ""
    sid: int = 0
    cid: int = 0
    email: str = uuid.uuid4().hex + "@oluwajuwon.dev"
    password: str = uuid.uuid4().hex
    test_flag: str = env('COLOSSEUM_TEST_FLAG', "CTF{f4k3_fl4g_f0r_t3st1ng}")[0]
    
    checklist.append("User Registration was successful.")
    try:
        raise_on_invalid_creds(email, password)
        success, message, _ = _register(email, password)
        if success:
            checks.append(True)
        else:
            logger.warning(f"User registration check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"User registration check failed: {e}")
        checks.append(False)
    
    checklist.append("User Login was successful.")
    try:
        details, success, message, _ = _login(email, password)
        pid = details.get("pid", "")
        if success:
            checks.append(True)
        else:
            logger.warning(f"User login check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"User login check failed: {e}")
        checks.append(False)

    checklist.append("User Profile Update was successful.")
    try:
        success, message, _ = _update_profile(pid, display_name="Health Bot")
        if success:
            checks.append(True)
        else:
            logger.warning(f"User profile update check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"User profile update check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Retrieval was successful.")
    try:
        series_list, success, message, _ = _get_series_list()
        if success and isinstance(series_list, list):
            checks.append(True)
        else:
            logger.warning(f"Series retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Exit was successful.")
    try:
        success, message, _ = _remove_player_from_series(sid, pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Series exit check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series exit check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Signup was successful.")
    try:
        success, message, _ = _add_player_to_series(sid, pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Series signup check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series signup check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Data Retrieval was successful.")
    try:
        series_data, success, message, _ = _get_series_data(sid)
        if success and isinstance(series_data, dict):
            checks.append(True)
        else:
            logger.warning(f"Series data retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series data retrieval check failed: {e}")
        checks.append(False)

    checklist.append("Series Overview Retrieval was successful.")
    try:
        overview, success, message, _ = _get_series_overview(sid)
        if success and isinstance(overview, dict):
            checks.append(True)
        else:
            logger.warning(f"Series overview retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series overview retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Submissions Retrieval was successful.")
    try:
        submissions, success, message, _ = _get_submissions(sid)
        if success and isinstance(submissions, list):
            checks.append(True)
        else:
            logger.warning(f"Submissions retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Submissions retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Challenge Control was successful.")
    try:
        success, message, _ = _control_challenge_instance(sid, cid, pid, "start")
        if success:
            checks.append(True)
        else:
            logger.warning(f"Challenge control check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Challenge control check failed: {e}")
        checks.append(False)
    
    checklist.append("Flag Submission was successful.")
    try:
        success, message, _ = _submit_flag(sid, cid, pid, test_flag)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Flag submission check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Flag submission check failed: {e}")
        checks.append(False)
    
    checklist.append("Player Data Retrieval was successful.")
    try:
        player_data, success, message, _ = _get_player_data(pid)
        if success and isinstance(player_data, dict):
            checks.append(True)
        else:
            logger.warning(f"Player data retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Player data retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("User Deletion was successful.")
    try:
        success, message, _ = _delete(pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"User deletion check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"User deletion check failed: {e}")
        checks.append(False)
    
    return { "checklist": checklist, "checks": checks }
        

def main():
    logger = logging.getLogger('Hypogeum')
    logger.setLevel(logging.DEBUG)

    table = psycopg2.sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
    query = psycopg2.sql.SQL("SELECT sid, cid FROM {table}").format(table=table)
    SIDS_AND_CIDS: dict = {}
    db_params = ' '.join([f"{k}={v}" for k, v in dict(app.config["COLOSSEUM_DB"]).items()])
    with psycopg2.connect(db_params) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            for row in rows:
                sid, cid = row
                if sid not in SIDS_AND_CIDS: SIDS_AND_CIDS[sid] = {"cids": {}}
                SIDS_AND_CIDS[sid]["cids"][cid] = {}
    app.config['COLOSSEUM_DATA'] = {"sids": SIDS_AND_CIDS}
    try:
        app.run(host='127.0.0.1', port=5000, debug=True)
    except Exception as e:
        logger.exception(f"Error starting the server: {e}")

if __name__ == '__main__':
    main()
    