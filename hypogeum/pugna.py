from . import REDIS_CLIENT, DIFFICULTY_LEVELS, CATEGORIES
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from psycopg2.errors import UniqueViolation
from hypogeum.armamentarium import (
    as_uuid, env, db_connect, raise_on_missing_series_and_challenges, refresh_series_and_challenges
)
from hypogeum.vomitoria import (
    flag_hash, series_signup_required, admin_required, cooldown_check, locked_challenge_check
)

import uuid
import logging
import psycopg2.sql as sql

pugna_bp = Blueprint('pugna', __name__, url_prefix='/series/<int:sid>/challenges/')

def _create_challenge(sid: int, **challenge) -> tuple[str, bool, str, int]:
    """
    Create a new challenge for a specific series.

    Args:
        - sid (int) : The ID of the series.
        - challenge (dict) : The challenge data to be created.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        required = {"title", "description", "author", "points", "category", "difficulty", "flag"}
        optional = {"prerequisite", "requires_instance", "file_url"}
        allowed = required | optional

        unknown = set(challenge.keys()) - allowed
        if unknown:
            return "", False, f"Unsupported fields: {', '.join(sorted(unknown))}", 400

        missing = [field for field in required if not challenge.get(field)]
        if missing:
            return "", False, f"Missing required fields: {', '.join(missing)}", 400

        normalized_challenge = {k: v for k, v in challenge.items() if k in allowed and v is not None}
        normalized_challenge['flag'] = flag_hash(normalized_challenge['flag'])
        normalized_challenge['points'] = int(normalized_challenge['points'])
        if normalized_challenge['points'] < 0:
            return "", False, "Points must be a non-negative integer.", 400
        normalized_challenge['sid'] = sid

        prerequisite = normalized_challenge.get("prerequisite")

        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        if prerequisite is not None:
            prerequisite = int(prerequisite)

            prereq_query = sql.SQL("SELECT 1 FROM {table} WHERE sid = %s AND cid = %s LIMIT 1"
                                   ).format(table=challenges_table)

            with db_connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(prereq_query, (sid, prerequisite))
                    if cursor.fetchone() is None:
                        return "", False, "Prerequisite challenge must exist in the same series.", 400
            normalized_challenge["prerequisite"] = prerequisite
        require_instance_raw = normalized_challenge.get("requires_instance")
        if require_instance_raw is None:
            require_instance = False
        elif isinstance(require_instance_raw, str):
            require_instance = require_instance_raw.lower() in ("true", "1", "yes")
        elif isinstance(require_instance_raw, bool):
            require_instance = require_instance_raw
        else:
            return "", False, "Invalid value for requires_instance. Must be a boolean.", 400
        normalized_challenge["requires_instance"] = require_instance

        file_url = normalized_challenge.get("file_url")
        if file_url is not None:
            if not isinstance(file_url, str):
                return "", False, "Invalid file_url.", 400
            file_url = file_url.strip()
            if len(file_url ) == 0 or len(file_url) > 2048:
                return "", False, "Invalid file_url length.", 400
            normalized_challenge["file_url"] = file_url

        if normalized_challenge["difficulty"] not in DIFFICULTY_LEVELS:
            return "", False, "Invalid difficulty.", 400

        if normalized_challenge["category"] not in CATEGORIES:
            return "", False, "Invalid category.", 400
        
        columns = sql.SQL(', ').join(
            sql.Identifier(col) for col in normalized_challenge.keys()
        )
        values = sql.SQL(', ').join(
            sql.Placeholder() for _ in normalized_challenge.values()
        )
        query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values}) RETURNING cid").format(
            table=challenges_table,
            columns=columns,
            values=values
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, tuple(normalized_challenge.values()))
                res = cursor.fetchone()
                if not res: raise Exception("Failed to create challenge.")
                cid = str(res[0])
            conn.commit()
        refresh_series_and_challenges(REDIS_CLIENT)
        return cid, True, f"Challenge created successfully", 201
    except ValueError as ve:
        logger.debug(f"Validation error in creating challenge: {ve}")
        return "", False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error creating challenge for Series ID {sid}: {e}")
        return "", False, "Internal server error", 500

@pugna_bp.put('/')
@login_required
@admin_required
def create_challenge(sid: int):
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    
    cid, success, message, status_code = _create_challenge(sid, **data)
    return jsonify({"success": success, "message": message, "cid": cid}), status_code

def _delete_challenge(sid: int, cid: int) -> tuple[bool, str, int]:
    """
    Delete a specific challenge from a series.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge to be deleted.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)

        table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        query = sql.SQL("DELETE FROM {table} WHERE sid = %s AND cid = %s").format(table=table)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid))
                if cursor.rowcount == 0:
                    return False, f"Challenge {cid} not found in Series {sid}.", 404
            conn.commit()
        refresh_series_and_challenges(REDIS_CLIENT)
        return True, f"Challenge {cid} deleted successfully from Series {sid}.", 200
    except ValueError as ve:
        logger.debug(f"Validation error in deleting challenge: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error deleting challenge for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@pugna_bp.delete('/<int:cid>')
@login_required
@admin_required
def delete_challenge(sid: int, cid: int):
    """
    Delete a specific challenge from a series.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge to be deleted.
    Returns:
        JSON response indicating the success or failure of the deletion.
    """

    success, message, status_code = _delete_challenge(sid, cid)
    return jsonify({"success": success, "message": message}), status_code

def _control_instance(sid: int, cid: int, pid: uuid.UUID, action: str) -> tuple[bool, str, int]:
    """
    Control the state of a challenge instance (start, stop, restart).
    Note: There are two kinds of instances: the shared instance and the spawned instance.
    This function controls spawned instances for individual users by calling the instance manager


    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - action (str) : The action to perform ('start', 'stop', 'restart').
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """

    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)

        action = action.lower()
        if action == "start":
            logger.info(f"Starting instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance started.", 200
        elif action == "stop":
            logger.info(f"Stopping instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance stopped.", 200
        elif action == "restart":
            logger.info(f"Restarting instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance restarted.", 200
        else:
            logger.warning(f"Invalid action '{action}' for Series {sid}, Challenge {cid}.")
            return False, "Invalid action. Use 'start', 'stop', or 'restart'.", 400
    except ValueError as ve:
        logger.debug(f"Validation error in controlling challenge instance: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error controlling challenge instance for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@pugna_bp.patch('/<int:cid>')
@login_required
@locked_challenge_check
def control_challenge_instance(sid: int, cid: int):  
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    action = str(data.get("action")).lower()
    pid = as_uuid(current_user.id)
    
    success, message, status_code = _control_instance(sid, cid, pid, action)
    return jsonify({"success": success, "message": message}), status_code

def _submit_flag(sid: int, cid: int, pid: uuid.UUID, flag: str) -> tuple[bool, str, int]:
    """
    Submit a flag for a specific challenge in a series.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - submitted_flag (str) : The flag submitted by the user.
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """

    logger = logging.getLogger(__name__)
    try:
        hashed_flag = flag_hash(flag)

        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)

        solve_insert_table = sql.Identifier(env('POSTGRESQL_SOLVES_TABLE')[0])
        solve_select_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        solve_columns = sql.SQL(', ').join(
            sql.Identifier(col) for col in ['sid', 'cid', 'points']
        )
        solve_query = sql.SQL("INSERT INTO {insert_table} ({columns}, subid, pid) " \
                        "SELECT {columns}, %s AS subid, %s AS pid FROM {select_table} " \
                        "WHERE sid = %s AND cid = %s AND flag = %s " \
                        "RETURNING solved_at").format(
                            insert_table=solve_insert_table,
                            select_table=solve_select_table,
                            columns=solve_columns
                        )
        submit_table = sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])

        insert_submission_query = sql.SQL("""INSERT INTO {submissions} (sid, cid, pid)
                                          VALUES (%s, %s, %s)
                                          RETURNING subid
                                        """).format(submissions=submit_table)
        
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(insert_submission_query, (sid, cid, pid))
                res = cursor.fetchone()
                if res is None: raise Exception("Failed to record submission.")
                subid = int(res[0])
                cursor.execute(solve_query, (subid, pid, sid, cid, hashed_flag))
                res = cursor.fetchall()
                if not res: return False, "Wrong Flag", 404
                _control_instance(sid, cid, pid, "stop")
                return True, "Correct Flag", 200
    except ValueError as ve:
        logger.debug(f"Validation error in submitting flag: {ve}")
        return False, str(ve), 404
    # NOTE: If the challenge has been solved by the same user, catch it
    except UniqueViolation:
        logger.warning(f"Duplicate submission for Series: {sid}, Challenge: {cid}, Player: {pid}.")
        return False, "Flag already submitted", 409
    except Exception as e:
        logger.exception(f"Error submitting flag for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@pugna_bp.post('/<int:cid>')
@login_required
@locked_challenge_check
@cooldown_check(
    lambda: f"{current_user.id}:{(request.view_args or {}).get('sid')}:{(request.view_args or {}).get('cid')}",
    seconds=10,
)
def submit_flag(sid: int, cid: int):
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    submitted_flag = data.get("flag")
    if not submitted_flag:
        return jsonify({"success": False, "message": "Flag is required."}), 400
    pid = as_uuid(current_user.id)
    success, message, status_code = _submit_flag(sid, cid, pid, submitted_flag)
    return jsonify({"success": success, "message": message}), status_code

def _get_solves(sid: int, cid: int) -> tuple[list, bool, str, int]:
    """
    Retrieve the solves for a specific series by its ID.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        list: A list of dictionaries, each representing a submission.
    """

    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)
        solves_table = sql.Identifier(env('POSTGRESQL_SOLVES_TABLE')[0])
        players_table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        columns = sql.SQL(', ').join([
            sql.SQL(', ').join(
            sql.Identifier(solves_table.string, col)
            for col in ['points', 'solved_at'] # from solves table
        ),
            sql.SQL(', ').join(
            sql.Identifier(players_table.string, col)
            for col in ['display_name', 'avatar'] # from players table
        )
        ])
        query = sql.SQL("SELECT {columns} FROM {s_table} " \
                                "LEFT JOIN {c_table} ON {s_table}.pid = {c_table}.pid " \
                                "WHERE {s_table}.sid = %s AND {s_table}.cid = %s"
                                ).format(
                                    s_table=solves_table,
                                    c_table=players_table,
                                    columns=columns,
                                )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,cid))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    # NOTE: No check for similar field names between the two tables
                    if cursor.description is None:
                        returned_columns = []
                    else:
                        returned_columns = [desc[0] for desc in cursor.description]
                    res.append(dict(zip(returned_columns, row)))
        # Send it raw. Client will handle sorting and filtering
        return res, True, "Solves retrieved successfully.", 200
    except ValueError as ve:
        logger.debug(f"Validation error while retrieving solves for Series ID {sid}: {ve}")
        return [], False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving solves for Series ID {sid}: {e}")
        return [], False, "Internal server error", 500

@pugna_bp.get('/<int:cid>/solves/')
@login_required
@series_signup_required
def get_solves(sid: int, cid: int):
    entries, success, message, status_code = _get_solves(sid, cid)
    return jsonify({"success": success, "message": message, "solves": entries}), status_code

def _get_submissions(sid: int, cid: int, pid: uuid.UUID) -> tuple[list, bool, str, int]:
    """
    Retrieve the submissions for a specific series and player by their IDs.

    Args:
        - sid (int) : The ID of the series.
        - pid (uuid.UUID) : The ID of the player.
    Returns:
        list: A list of dictionaries, each representing a submission.
    """

    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)
        submissions_table = sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        columns = sql.SQL(', ').join([
            sql.SQL(', ').join(
            sql.Identifier(submissions_table.string, col)
            for col in ['submitted_at'] # from submissions table
        ),
            sql.SQL(', ').join(
            sql.Identifier(challenges_table.string, col)
            for col in ['cid', 'title', 'description', 'category', 'difficulty'] # from challenges table
        )
        ])
        query = sql.SQL("SELECT {columns} FROM {s_table} " \
                                "LEFT JOIN {c_table} ON {s_table}.cid = {c_table}.cid " \
                                "WHERE {s_table}.sid = %s AND {s_table}.cid = %s AND {s_table}.pid = %s"
                                ).format(
                                    s_table=submissions_table,
                                    c_table=challenges_table,
                                    columns=columns,
                                )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    # NOTE: No check for similar field names between the two tables
                    if cursor.description is None:
                        returned_columns = []
                    else:
                        returned_columns = [desc[0] for desc in cursor.description]
                    res.append(dict(zip(returned_columns, row)))
        return res, True, "Submissions retrieved successfully.", 200
    except ValueError as ve:
        logger.debug(f"Validation error while retrieving submissions for Series {sid} and Player {pid}: {ve}")
        return [], False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving submissions for Series ID {sid} and Player ID {pid}: {e}")
        return [], False, "Internal server error", 500

@pugna_bp.get('/<int:cid>/submissions/')
@login_required
@series_signup_required
def get_submissions(sid: int, cid: int):
    pid = as_uuid(current_user.id)
    entries, success, message, status_code = _get_submissions(sid, cid, pid)
    return jsonify({"success": success, "message": message, "submissions": entries}), status_code

def integration_test(checklist: list[str], checks: list[bool], sid: int, pid: uuid.UUID) -> str:
    """
    Perform an integration test for a specific series and challenge.

    Args:
        - checklist (list[str]) : A list to append the results of each check.
        - checks (list[bool]) : A list to append the boolean results of each check
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - pid (uuid.UUID) : The ID of the player.
    """
    logger = logging.getLogger(__name__)

    challenge_data = {
        "title": "Test",
        "description": "This is a test challenge for integration testing.",
        "author": "Integration Test",
        "points": "0",
        "difficulty": "Sanity Check",
        "category": "Warmup",
        "flag": env('COLOSSEUM_TEST_FLAG', "CTF{f4k3_fl4g_f0r_t3st1ng}")[0]
    }

    cid: int| None = None

    checklist.append("Challenge Creation was successful.")
    try:
        cid_str, success, message, _ = _create_challenge(sid, **challenge_data)
        if success:
            cid = int(cid_str)
            checks.append(True)
        else:
            logger.warning(f"Challenge creation check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Challenge creation check failed: {e}")
        checks.append(False)

    checklist.append("Challenge Control was successful.")
    try:
        if cid is None: raise ValueError("Challenge ID is None, cannot control instance.")
        success, message, _ = _control_instance(sid, cid, pid, "start")
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
        if cid is None: raise ValueError("Challenge ID is None, cannot submit flag.")
        flag = str(challenge_data["flag"])
        success, message, _ = _submit_flag(sid, cid, pid, flag)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Flag submission check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Flag submission check failed: {e}")
        checks.append(False)
    
    checklist.append("Submissions Retrieval was successful.")
    try:
        if cid is None: raise ValueError("Challenge ID is None, cannot retrieve submissions.")
        submissions, success, message, _ = _get_submissions(sid, cid, pid)
        if success and isinstance(submissions, list):
            checks.append(True)
        else:
            logger.warning(f"Submissions retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Submissions retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Solves Retrieval was successful.")
    try:
        if cid is None: raise ValueError("Challenge ID is None, cannot retrieve solves.")
        solves, success, message, _ = _get_solves(sid, cid)
        if success and isinstance(solves, list):
            checks.append(True)
        else:
            logger.warning(f"Solves retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Solves retrieval check failed: {e}")
        checks.append(False)
    
    return str(cid) if cid is not None else ""

def integration_test_cleanup(checklist: list[str], checks: list[bool], sid: int, cid: int) -> None:
    """
    Perform cleanup after the integration test to remove any test data created.

    Args:
        checklist (list): A list to append the results of each check.
        checks (list): A list to append boolean values indicating the success of each check.
        sid (int): The series ID to clean up.
        cid (int): The challenge ID to clean up.
    """
    logger = logging.getLogger(__name__)
    checklist.append("Cleanup of Test Challenge was successful.")
    try:
        success, message, _ = _delete_challenge(sid, cid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Cleanup check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Cleanup check failed: {e}")
        checks.append(False)