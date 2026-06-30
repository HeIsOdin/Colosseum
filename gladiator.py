from __init__ import NAME
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user, logout_user
from armamentarium import env, db_connect

import uuid
import logging
import psycopg2.sql as sql

gladiator_bp = Blueprint('gladiator', __name__, url_prefix='/players')

def _delete(pid: uuid.UUID):
    logger = logging.getLogger(NAME)
    try:
        table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        query = sql.SQL("DELETE FROM {table} WHERE pid = %s").format(table=table)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid,))
                if cursor.rowcount == 0:
                    return False, "User not found.", 404
        logout_user()
        logger.debug(f"User {pid} deleted successfully.")
        return True, "User deleted successfully.", 200
    except Exception as e:
        logger.exception(f"Error during user deletion for user {pid}: {e}")
        return False, "Internal server error", 500

@gladiator_bp.delete('/me')
@login_required
def delete():
    success, message, status_code = _delete(current_user.id)
    return jsonify({"success": success, "message": message}), status_code

def raise_on_invalid_profile(profile: dict[str, str|None]) -> None:
    """
    Validate the display name and avatar for a user profile.

    Args:
        - display_name (str) : The display name to validate.
        - avatar (str) : The avatar to validate.
    
    Raises:
        ValueError: If the display name or avatar is invalid.
    """
    display_name = profile.get('display_name')
    avatar = profile.get('avatar')
    if display_name is None:
        profile.pop('display_name', None)
    if avatar is None:
        profile.pop('avatar', None) 
    if isinstance(display_name, str) and not (1 <= len(display_name) <= 20):
        raise ValueError("Display name must be a string between 1 and 20 characters.")
    if isinstance(avatar, str) and not (1 <= len(avatar) <= 10):
        raise ValueError("Avatar must be a string between 1 and 10 characters.")
    if len(profile.keys()) > 2:
        raise ValueError("Only 'display_name' and 'avatar' are allowed as profile fields.")

def _update_profile(pid: uuid.UUID, **profile):
    logger = logging.getLogger(NAME)
    try:
        raise_on_invalid_profile(profile)
        # NOTE: raise_on_invalid_profile only allows display_name and avatar so unpacking is safe
        table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        set_clause = sql.SQL(', ').join(
            sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
            for k in profile.keys()
        )
        query = sql.SQL("UPDATE {table} SET {set_clause} WHERE pid = %s").format(
            table=table,
            set_clause=set_clause
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (*profile.values(), pid))
        logger.debug(f"User {pid} profile updated successfully.")
        return True, "Profile updated successfully.", 200
    except ValueError as ve:
        logger.debug(f"Validation error in profile update for user {pid}: {ve}")
        return False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error during profile update for user {pid}: {e}")
        return False, "Internal server error", 500

@gladiator_bp.patch('/me')
@login_required
def update_profile():
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    profile = {k: v for k, v in data.items() if k in ['display_name', 'avatar']}

    if not profile:
        return jsonify({"success": False, "message": "No valid profile fields provided."}), 400
    success, message, status_code = _update_profile(current_user.id, **profile)
    return jsonify({"success": success, "message": message}), status_code

def _get_player_data(pid: uuid.UUID) -> tuple[dict, bool, str, int]:
    """
    Retrieve the data for a specific player by their ID.

    Args:
        - pid (str) : The ID of the player.
    
    Returns:
        dict: A dictionary representing the player data
    """
    logger = logging.getLogger(NAME)
    try:
        players_table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])
        solves_table = sql.Identifier(env('POSTGRESQL_SOLVES_TABLE')[0])
        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        players_columns = sql.SQL(', ').join(
            sql.Identifier(col)
            for col in ['pid', 'display_name', 'avatar']
        )
        columns = sql.SQL(', ').join([
            sql.SQL(', ').join(
                sql.Identifier(solves_table.string, col)
                for col in ['sid', 'cid', 'points', 'solved_at']
            ),
            sql.SQL(', ').join(
                sql.Identifier(challenges_table.string, col)
                for col in ['category', 'difficulty']
            )
        ])
        p_query = sql.SQL("SELECT {players_columns} FROM {players_table} WHERE pid = %s").format(
            players_table=players_table,
            players_columns=players_columns
        )
        query = sql.SQL("SELECT {columns} FROM {s_table} " \
                        "LEFT JOIN {c_table} ON {s_table}.cid = {c_table}.cid " \
                        "WHERE {s_table}.pid = %s" \
                        "ORDER BY {s_table}.solved_at DESC").format(
                            s_table=solves_table,
                            c_table=challenges_table,
                            columns=columns
                        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(p_query, (pid,))
                player_row = cursor.fetchone()
                if not player_row: return {}, False, "Player ID not found.", 404
                if cursor.description is None:
                    player_returned_columns = []
                else:
                    player_returned_columns = [desc[0] for desc in cursor.description]
                player_data = dict(zip(player_returned_columns, player_row))
                cursor.execute(query, (pid,))
                solves_rows = cursor.fetchall()
                solves_data = []
                for solve_row in solves_rows:
                    if cursor.description is None:
                        solves_returned_columns = []
                    else:
                        solves_returned_columns = [desc[0] for desc in cursor.description]
                    solves_data.append(dict(zip(solves_returned_columns, solve_row)))
                player_data['solves'] = solves_data
        return player_data, True, "Player data retrieved successfully.", 200
    except Exception as e:
        logger.exception(f"Error retrieving player data for Player ID {pid}: {e}")
        return {}, False, "Internal server error", 500

@gladiator_bp.get('/<uuid:pid>/')
def get_player_data(pid: uuid.UUID):
    player_data, success, message, status_code = _get_player_data(pid)
    return jsonify({"success": success, "message": message, "player": player_data}), status_code

def integration_test(checklist: list[str], checks: list[bool], pid: uuid.UUID) -> None:
    """
    Perform an integration test to check the health of the Colosseum service.

    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(NAME)
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
    
    checklist.append("Player Data Retrieval was successful.")
    try:
        if pid is None: raise ValueError("User ID is None, cannot retrieve player data.")
        player_data, success, message, _ = _get_player_data(pid)
        if success and isinstance(player_data, dict):
            checks.append(True)
        else:
            logger.warning(f"Player data retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Player data retrieval check failed: {e}")
        checks.append(False)
    
def integration_test_cleanup(checklist: list[str], checks: list[bool], pid: uuid.UUID) -> None:
    """
    Perform cleanup after the integration test to remove any test data created.

    Args:
        checklist (list): A list to append the results of each check.
        checks (list): A list to append boolean values indicating the success of each check.
        pid (uuid.UUID): The player ID to clean up.
    """
    logger = logging.getLogger(NAME)
    checklist.append("Cleanup of Test User was successful.")
    try:
        success, message, _ = _delete(pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Cleanup check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Cleanup check failed: {e}")
        checks.append(False)