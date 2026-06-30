from . import login_manager
from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user, UserMixin
from datetime import timedelta
from functools import wraps
from time import time
from hypogeum.armamentarium import env, db_connect, redis_connect, as_uuid

import re
import hmac
import uuid
import bcrypt
import hashlib
import logging
import psycopg2.sql as sql

vomitoria_bp = Blueprint('vomitoria', __name__, url_prefix='/auth')

class User(UserMixin):
    def __init__(self, pid: uuid.UUID, sids: list | None = None, is_admin: bool = False):
        self.id = pid
        self.sids = sids or []
        self.is_admin = is_admin

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({
        "success": False,
        "message": "Authentication required",
        "redirect": "/login"
    }), 401

@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    logger = logging.getLogger(__name__)

    try:
        pid = uuid.UUID(str(user_id))
    except ValueError:
        return None

    try:
        users_table = env("POSTGRESQL_USER_TABLE")[0]
        memberships_table = env("POSTGRESQL_MEMBERSHIPS_TABLE")[0]

        query = sql.SQL("""
            SELECT u.pid, u.is_admin, COALESCE(array_agg(m.sid) FILTER (WHERE m.sid IS NOT NULL), ARRAY[]::INTEGER[]) AS sids
            FROM {users} u
            LEFT JOIN {memberships} m ON u.pid = m.pid
            WHERE u.pid = %s
            GROUP BY u.pid, u.is_admin
        """).format(
            users=sql.Identifier(users_table),
            memberships=sql.Identifier(memberships_table),
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid,))
                row = cursor.fetchone()

        if row is None:
            return None

        return User(pid=row[0], is_admin=bool(row[1]), sids=list(row[2] or []))

    except Exception as e:
        logger.exception(f"Error loading user {user_id}: {e}")
        return None

def series_signup_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        sid = kwargs.get('sid')
        if sid is None:
            return jsonify({"message": "Series ID not provided"}), 400
        if sid not in current_user.sids:
            return jsonify({"message": "User not signed up for this series"}), 403
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"message": "Authentication required"}), 401
        if not current_user.is_admin: 
            return jsonify({"message": "Admin privileges required"}), 403
        return f(*args, **kwargs)
    return decorated_function

def cooldown_check(key_func, seconds=5):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            r = redis_connect()
            
            key = f"colosseum:cooldown:{key_func()}"
            last_submission = r.get(key)
            current_time = time()
            
            if last_submission:
                elapsed = current_time - float(last_submission)
                if elapsed < seconds:
                    remaining = round(seconds - elapsed, 1)
                    return jsonify({
                        "error": f"Please wait {remaining}s before submitting again."
                    }), 429
            
            r.setex(key, seconds, current_time)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def locked_challenge_check(f):
    """
    Require that:
    1. the request has sid and cid route arguments,
    2. the current user is a member of the series,
    3. the challenge exists in that series,
    4. if the challenge has a prerequisite, the user has solved it.

    This should protect active challenge interactions only:
    - instance control
    - flag submission

    It should not be used for public/visible challenge metadata.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        logger = logging.getLogger(__name__)

        sid = kwargs.get("sid")
        cid = kwargs.get("cid")

        if sid is None:
            return jsonify({"success": False, "message": "Series ID not provided."}), 400

        if cid is None:
            return jsonify({"success": False, "message": "Challenge ID not provided."}), 400

        if not current_user.is_authenticated:
            return jsonify({"success": False, "message": "Authentication required."}), 401

        try:
            pid = as_uuid(current_user.id)

            memberships_table = env("POSTGRESQL_MEMBERSHIPS_TABLE")[0]
            challenges_table = env("POSTGRESQL_CHALLENGES_TABLE")[0]
            solves_table = env("POSTGRESQL_SOLVES_TABLE")[0]

            membership_query = sql.SQL("""
                SELECT 1
                FROM {memberships}
                WHERE sid = %s AND pid = %s
                LIMIT 1
            """).format(
                memberships=sql.Identifier(memberships_table),
            )

            challenge_query = sql.SQL("""
                SELECT prerequisite
                FROM {challenges}
                WHERE sid = %s AND cid = %s
                LIMIT 1
            """).format(
                challenges=sql.Identifier(challenges_table),
            )

            prerequisite_solve_query = sql.SQL("""
                SELECT 1
                FROM {solves}
                WHERE sid = %s AND cid = %s AND pid = %s
                LIMIT 1
            """).format(
                solves=sql.Identifier(solves_table),
            )

            with db_connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(membership_query, (sid, pid))
                    if cursor.fetchone() is None:
                        return jsonify({
                            "success": False,
                            "message": "User not signed up for this series."
                        }), 403

                    cursor.execute(challenge_query, (sid, cid))
                    challenge_row = cursor.fetchone()

                    if challenge_row is None:
                        return jsonify({
                            "success": False,
                            "message": "Challenge not found in this series."
                        }), 404

                    prerequisite = challenge_row[0]

                    if prerequisite is not None:
                        cursor.execute(prerequisite_solve_query, (sid, prerequisite, pid))
                        if cursor.fetchone() is None:
                            return jsonify({
                                "success": False,
                                "message": f"This challenge is locked. Solve challenge {prerequisite} first."
                            }), 403

            return f(*args, **kwargs)

        except ValueError:
            return jsonify({"success": False, "message": "Invalid user ID."}), 400
        except Exception as e:
            logger.exception(
                f"Challenge interaction authorization failed for sid={sid}, cid={cid}, user={current_user.get_id()}: {e}"
            )
            return jsonify({"success": False, "message": "Internal server error"}), 500

    return decorated_function

def raise_on_invalid_creds(email: str, password: str):
    """
    Check if the provided email and password are valid credentials in the database.

    Args:
        - email (str) : The email to check.
        - password (str) : The password to check.
    Returns:
        bool: True if the credentials are valid, False otherwise.
    """
    email = email.strip().lower()
    if password != password.strip(): raise ValueError("Password must not contain leading or trailing whitespace.")
    email_pattern = re.compile(
        r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
        r"(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
        r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
    )
    if not email or not password: raise ValueError("Email and password must not be empty.")
    if len(password) < 8: raise ValueError("Password must be at least 8 characters long.")
    # NOTE: Enforce the check below. bcrypt has a maximum password length of 72 bytes (idk why).
    if len(password.encode('utf-8')) > 72: raise ValueError ("Password must be 72 bytes of fewer.")
    if re.fullmatch(email_pattern, email) is None: raise ValueError("Invalid email format.")

def flag_hash(flag: str) -> str:
    """
    Hash the provided flag using HMAC with a pepper from the environment.

    Args:
        - flag (str) : The flag to hash.
    Returns:
        str: The resulting hash of the flag.
    """
    flag = flag.strip()
    if not flag: raise ValueError("Flag must not be empty.")
    pepper = env('COLOSSEUM_FLAG_PEPPER')[0].encode('utf-8')
    return hmac.new(pepper, flag.encode('utf-8'), hashlib.sha256).hexdigest()

# -- Authentication & Profile --

def _login(email: str, password: str) -> tuple[dict, bool, str, int]:
    logger = logging.getLogger(__name__)
    email = email.strip().lower()

    try:
        raise_on_invalid_creds(email, password)

        users_table = env("POSTGRESQL_USER_TABLE")[0]
        memberships_table = env("POSTGRESQL_MEMBERSHIPS_TABLE")[0]

        query = sql.SQL("""
            SELECT u.pid, u.password, u.is_admin, u.status,
            COALESCE(array_agg(m.sid) FILTER (WHERE m.sid IS NOT NULL), ARRAY[]::INTEGER[]) AS sids
            FROM {users} u
            LEFT JOIN {memberships} m ON u.pid = m.pid
            WHERE u.email = %s
            GROUP BY u.pid, u.password, u.is_admin, u.status
        """).format(
            users=sql.Identifier(users_table),
            memberships=sql.Identifier(memberships_table),
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (email,))
                row = cursor.fetchone()

        if row is None:
            return {}, False, "Invalid credentials", 401

        pid, password_hash, is_admin, status, sids = row

        if status not in ["active", "verified"]:
            return {}, False, f"User status is '{status}', cannot log in.", 403

        if password_hash is None:
            return {}, False, "Invalid credentials", 401

        if not bcrypt.checkpw(password.encode("utf-8"), str(password_hash).encode("utf-8")):
            return {}, False, "Invalid credentials", 401

        return {
            "pid": str(pid),
            "sids": list(sids or []),
            "is_admin": bool(is_admin),
        }, True, "", 200

    except ValueError as ve:
        return {}, False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error during login for user {email}: {e}")
        return {}, False, "Internal server error", 500

@vomitoria_bp.post('/')
@cooldown_check(lambda: request.remote_addr, seconds=5)
def login():
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()

    email = str(data.get("email"))
    password = str(data.get("password"))
    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required."}), 400
    
    details, success, message, status_code = _login(email, password)

    if not success:
        return jsonify({"success": False, "message": message}), status_code

    pid = uuid.UUID(str(details["pid"]))
    user = User(
        pid=pid,
        sids=list(details.get("sids", [])),
        is_admin=bool(details.get("is_admin", False)),
    )
    login_user(user, remember=True, duration=timedelta(days=1))

    return jsonify({"success": True, "message": message}), status_code

@vomitoria_bp.post('/logout')
@login_required
def logout():
    logger = logging.getLogger(__name__)
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
    logger = logging.getLogger(__name__)
    email = email.strip().lower()
    try:
        raise_on_invalid_creds(email, password)
        salt = bcrypt.gensalt()
        pid = str(uuid.uuid4().hex)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        cols = ['pid', 'email', 'password']
        columns = sql.SQL(', ').join(sql.Identifier(col) for col in cols)
        values_clause = sql.SQL(', ').join(sql.Placeholder() for _ in cols)
        query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
            table=table,
            columns=columns,
            values=values_clause
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid, email, hashed_password))
        return True, "User registered successfully.", 201
    except ValueError as ve:
        logger.debug(f"Validation error during registration for user {email}: {ve}")
        return False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error during registration for user {email}: {e}")
        return False, "Internal server error", 500

@vomitoria_bp.put('/')
def register():
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    email = str(data.get("email"))
    password = str(data.get("password"))
    success, message, status_code = _register(email, password)
    return jsonify({"success": success, "message": message}), status_code

def integration_test(checklist: list[str], checks: list[bool], email: str, password: str
                    ) -> uuid.UUID | None:
    """
    Perform an integration test to check the health of the Vomitoria service.

    Returns:
        tuple: A tuple containing a checklist of tests, their results, and a unique test identifier.
    """
    pid: uuid.UUID | None = None
    checklist.append("User Registration was successful.")

    logger = logging.getLogger(__name__)
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
        pid = details.get("pid")
        if success:
            checks.append(True)
        else:
            logger.warning(f"User login check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Integration test failed: {e}")
    return pid