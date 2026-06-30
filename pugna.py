from __init__ import NAME, REDIS_CLIENT
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from psycopg2.errors import UniqueViolation
from armamentarium import env, db_connect, raise_on_missing_series_and_challenges, refresh_series_and_challenges
from vomitoria import flag_hash, series_signup_required, admin_required, flag_hash, cooldown_check

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
    logger = logging.getLogger(NAME)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        columns = [
            'title', 'description', 'author', 'points',
            'category', 'difficulty', 'prerequisite', 'flag'
        ]
        normalized_challenge = {k:v for k,v in challenge.items() if k in columns and v is not None}
        normalized_challenge['flag'] = flag_hash(normalized_challenge["flag"])
        normalized_challenge['sid'] = sid
        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
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
        return "", False, str(ve), 404
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
    logger = logging.getLogger(NAME)
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

    logger = logging.getLogger(NAME)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)

        action = action.lower()
        if action == "start":
            logger.info(f"Starting instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance started.", 200
        elif action == "stop":
            logger.info(f"Stopping instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance started.", 200
        elif action == "restart":
            logger.info(f"Restarting instance for Series {sid}, Challenge {cid} by Player {pid}.")
            return True, "Instance started.", 200
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
@series_signup_required
def control_challenge_instance(sid: int, cid: int):  
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    action = str(data.get("action")).lower()
    pid = uuid.UUID(current_user.id)
    
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

    logger = logging.getLogger(NAME)
    try:
        hashed_flag = flag_hash(flag)

        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid, cid)

        solve_insert_table = sql.Identifier(env('POSTGRESQL_SOLVES_TABLE')[0])
        solve_select_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        solve_columns = sql.SQL(', ').join(
            sql.Identifier(col) for col in ['sid', 'cid', 'points']
        )
        solve_query = sql.SQL("INSERT INTO {insert_table} ({columns}, pid) " \
                        "SELECT {columns}, %s AS pid FROM {select_table} " \
                        "WHERE sid = %s AND cid = %s AND flag = %s " \
                        "RETURNING solved_at").format(
                            insert_table=solve_insert_table,
                            select_table=solve_select_table,
                            columns=solve_columns
                        )
        submit_table = sql.Identifier(env('POSTGRESQL_SUBMISSIONS_TABLE')[0])
        submit_query = sql.SQL("WITH entered AS (" \
                               "INSERT INTO {table} (sid, cid, pid) " \
                               "VALUES (%s, %s, %s)" \
                               "RETURNING sid, cid, pid" \
                               ") " \
                               "SELECT COUNT(*) FROM {table} s " \
                               "JOIN entered i ON s.sid = i.sid AND s.cid = i.cid AND s.pid = i.pid " \
                               "WHERE s.submitted_at > NOW() - INTERVAL '1 minute'"
                               ).format(table=submit_table)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(submit_query, (sid, cid, pid))
                res = cursor.fetchone()
                if res is None: raise Exception("Failed to record submission.")
                submission_count_per_minute = int(res[0])
                standard_limit_per_minute = int(env('COLOSSEUM_SUBMISSION_LIMIT_PER_MIN', '10')[0])
                if submission_count_per_minute > standard_limit_per_minute:
                    return False, f"Submission limit exceeded. Please wait before submitting again.", 429
                cursor.execute(solve_query, (pid, sid, cid, hashed_flag))
                res = cursor.fetchall()
                if not res: return False, "Wrong Flag", 404
                _control_instance(sid, cid, pid, "stop")
                return True, "Correct Flag", 200
    except ValueError as ve:
        logger.debug(f"Validation error in submitting flag: {ve}")
        return False, str(ve), 404
    except UniqueViolation:
        logger.warning(f"Duplicate submission for Series: {sid}, Challenge: {cid}, Player: {pid}.")
        return False, "Flag already submitted", 409
    except Exception as e:
        logger.exception(f"Error submitting flag for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@pugna_bp.post('/<int:cid>')
@login_required
@cooldown_check(lambda: current_user.id, seconds=10)
@series_signup_required
def submit_flag(sid: int, cid: int):
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    submitted_flag = str(data.get("flag"))
    success, message, status_code = _submit_flag(sid, cid, current_user.id, submitted_flag)
    return jsonify({"success": success, "message": message}), status_code

def _get_solves(sid: int, cid: int) -> tuple[list, bool, str, int]:
    """
    Retrieve the solvess for a specific series by its ID.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        list: A list of dictionaries, each representing a submission.
    """

    logger = logging.getLogger(NAME)
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
                    res.append(dict(zip([desc[0] for desc in returned_columns], row)))
        # Send it raw. Client will handle sorting and filtering
        return res, True, "Solves retrieved successfully.", 200
    except ValueError as ve:
        logger.debug(f"Validation error while retrieving solvess for Series ID {sid}: {ve}")
        return [], False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving solvess for Series ID {sid}: {e}")
        return [], False, "Internal server error", 500

@pugna_bp.get('/<int:cid>/solves/')
@login_required
@series_signup_required
def get_solves(sid: int, cid: int):
    entries, success, message, status_code = _get_solves(sid, cid)
    return jsonify({"success": success, "message": message, "submissions": entries}), status_code

def _get_submissions(sid: int, cid: int, pid: uuid.UUID) -> tuple[list, bool, str, int]:
    """
    Retrieve the submissions for a specific series and player by their IDs.

    Args:
        - sid (int) : The ID of the series.
        - pid (uuid.UUID) : The ID of the player.
    Returns:
        list: A list of dictionaries, each representing a submission.
    """

    logger = logging.getLogger(NAME)
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
                    res.append(dict(zip([desc[0] for desc in returned_columns], row)))
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
    pid = uuid.UUID(current_user.id)
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
    logger = logging.getLogger(NAME)

    challenge_data = {
        "title": "Test",
        "description": "This is a test challenge for integration testing.",
        "author": "Integration Test",
        "points": 0,
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
    logger = logging.getLogger(NAME)
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