from . import NAME, REDIS_CLIENT
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from hypogeum.vomitoria import admin_required
from hypogeum.armamentarium import (
    as_uuid, env, db_connect, raise_on_missing_series_and_challenges, refresh_series_and_challenges
)

import uuid
import logging
import psycopg2.sql as sql

auctoramentum_bp = Blueprint('auctoramentum', __name__, url_prefix='/series')

def _get_series_list(offset: int = 0, limit: int = 10) -> tuple[list, bool, str, int]:
    """
    Retrieve the list of series from the database.

    Returns:
        list: A list of dictionaries, each representing a series.
    """
    logger = logging.getLogger(__name__)
    try:
        table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        columns = sql.SQL(', ').join(
            sql.Identifier(col)
            for col in ['sid', 'title', 'description', 'starts_at', 'ends_at', 'image']
        )
        query = sql.SQL("SELECT {columns} FROM {table} " \
                        "ORDER BY starts_at DESC, sid DESC OFFSET %s LIMIT %s"
                        ).format(table=table, columns=columns)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (offset, limit))
                rows = cursor.fetchall()
                res = []
                for row in rows:
                    returned_columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    entry = dict(zip([desc for desc in returned_columns], row))
                    res.append(entry)
        return res, True, "Series list retrieved successfully.", 200
    except Exception as e:
        logger.exception(f"Error retrieving series list: {e}")
        return [], False, "Internal server error", 500

@auctoramentum_bp.get('/')
def get_series_list():
    offset = request.args.get('offset', default=0, type=int)
    limit = request.args.get('limit', default=10, type=int)
    limit = min(max(limit, 1), 20)
    series_list, success, message, status_code = _get_series_list(offset, limit)
    return jsonify({"success": success, "message": message, "series": series_list}), status_code

def _get_series_data(sid: int) -> tuple[dict, bool, str, int]:
    """
    Retrieve the data for a specific series by its ID, including all challenges
    and their solvers.

    Args:
        - sid (int): The ID of the series.
    Returns:
        tuple[dict, bool, str, int]: series data, success flag, message, status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)

        series_table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        solves_table = sql.Identifier(env('POSTGRESQL_SOLVES_TABLE')[0])
        user_table = sql.Identifier(env('POSTGRESQL_USER_TABLE')[0])

        series_query = sql.SQL(
            "SELECT sid, title, description, starts_at, ends_at, image "
            "FROM {series_table} WHERE sid = %s"
        ).format(series_table=series_table)

        challenges_query = sql.SQL("""
            SELECT
                c.cid, c.title, c.description, c.points, c.category,
                c.difficulty, c.prerequisite,
                COALESCE(
                    json_agg(
                        json_build_object(
                            'display_name', u.display_name,
                            'avatar', u.avatar,
                            'solved_at', s.solved_at
                        )
                    ) FILTER (WHERE s.pid IS NOT NULL),
                    '[]'
                ) AS solvers
            FROM {c_table} c
            LEFT JOIN {s_table} s ON c.cid = s.cid AND s.sid = %s
            LEFT JOIN {u_table} u ON s.pid = u.pid
            WHERE c.sid = %s
            ORDER BY c.points DESC, c.cid ASC
            GROUP BY c.cid, c.title, c.description, c.points, c.category,
                     c.difficulty, c.prerequisite
        """).format(
            c_table=challenges_table,
            s_table=solves_table,
            u_table=user_table
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(series_query, (sid,))
                series_row = cursor.fetchone()
                if not series_row:
                    return {}, False, "Series ID not found.", 404

                series_columns = [desc[0] for desc in cursor.description] if cursor.description else []
                series_data = dict(zip(series_columns, series_row))

                cursor.execute(challenges_query, (sid, sid))
                challenges_columns = [desc[0] for desc in cursor.description] if cursor.description else []
                challenges_rows = cursor.fetchall()

                series_data['challenges'] = [
                    dict(zip(challenges_columns, row)) for row in challenges_rows
                ]

        return series_data, True, "Series data retrieved successfully.", 200

    except ValueError as ve:
        logger.debug(f"Validation error while retrieving series data for Series ID {sid}: {ve}")
        return {}, False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving series data for Series ID {sid}: {e}")
        return {}, False, "Internal server error", 500

@auctoramentum_bp.get('/<int:sid>')
@login_required
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
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        columns = sql.SQL(', ').join(
            sql.Identifier(col)
            for col in ['title', 'description', 'starts_at', 'ends_at', 'image']
        )
        query = sql.SQL("SELECT {columns} FROM {table} WHERE sid = %s").format(
            table=table,
            columns=columns,
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,))
                row = cursor.fetchone()
                if not row: return {}, False, "Series ID not found.", 404
                if cursor.description is None:
                    returned_columns = []
                else:
                    returned_columns = [desc[0] for desc in cursor.description]
                res = dict(zip(returned_columns, row))
        return res, True, "Series overview retrieved successfully.", 200
    except ValueError as ve:
        logger.debug(f"Validation error while retrieving overview for Series ID {sid}: {ve}")
        return {}, False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error retrieving series overview for Series ID {sid}: {e}")
        return {}, False, "Internal server error", 500

@auctoramentum_bp.get('/<int:sid>/overview/')
def get_series_overview(sid: int):
    overview, success, message, status_code = _get_series_overview(sid)
    return jsonify({"success": success, "message": message, "overview": overview}), status_code

def _create_series(title: str, description: str, starts_at_str: str,
                   ends_at_str: str, image: str) -> tuple[str, bool, str, int]:
    """
    Create a new series in the database.

    Args:
        - title (str) : The title of the series.
        - description (str) : The description of the series.
        - starts_at_str (str) : The start date of the series.
        - ends_at_str (str) : The end date of the series.
        - image (str) : The image URL for the series.
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        starts_at = datetime.fromisoformat(starts_at_str)
        ends_at = datetime.fromisoformat(ends_at_str) if ends_at_str else None
        table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        columns = sql.SQL(', ').join(
            sql.Identifier(col)
            for col in ['title', 'description', 'starts_at', 'ends_at', 'image']
        )
        values = sql.SQL("(%s, %s, %s, %s, %s)")
        query = sql.SQL("INSERT INTO {table} ({columns}) VALUES {values} RETURNING sid").format(
                            table=table,
                            columns=columns,
                            values=values
                        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (title, description, starts_at, ends_at, image))
                res = cursor.fetchone()
                if not res:
                    logger.error("Failed to retrieve the newly created series ID.")
                    return "", False, "Failed to create series", 500
                sid = str(res[0])
                logger.debug(f"Series '{title}' created with ID {sid}.")
            conn.commit()
        refresh_series_and_challenges(REDIS_CLIENT)
        return sid, True, f"Series {title} created successfully.", 201
    except Exception as e:
        logger.exception(f"Error creating series {title}: {e}")
        return "", False, "Internal server error", 500

@auctoramentum_bp.put('/')
@login_required
@admin_required
def create_series():
    data = request.get_json()
    if data is None:
        data = request.form.to_dict()
    data["starts_at_str"] = data.pop("starts_at", '')
    data["ends_at_str"] = data.pop("ends_at", '')
    sid, success, message, status_code = _create_series(**data)
    return jsonify({"sid": sid, "success": success, "message": message}), status_code

def _delete_series(sid: int) -> tuple[bool, str, int]:
    """
    Delete a series from the database.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        table = sql.Identifier(env('POSTGRESQL_SERIES_TABLE')[0])
        query = sql.SQL("DELETE FROM {table} WHERE sid = %s").format(
            table=table
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid,))
                if cursor.rowcount == 0:
                    logger.warning(f"Series ID {sid} not found for deletion.")
                    return False, "Series ID not found", 404
            conn.commit()
        refresh_series_and_challenges(REDIS_CLIENT)
        return True, f"Series ID {sid} deleted successfully.", 200
    except Exception as e:
        logger.exception(f"Error deleting series ID {sid}: {e}")
        return False, "Internal server error", 500

@auctoramentum_bp.delete('/<int:sid>')
@login_required
@admin_required
def delete_series(sid: int):
    success, message, status_code = _delete_series(sid)
    return jsonify({"success": success, "message": message}), status_code

def _join_series(sid: int, pid: uuid.UUID,) -> tuple[bool, str, int]:
    """
    Add a player to a specific series.

    Args:
        - sid (int) : The ID of the series.
        - pid (str) : The ID of the player.
    
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        
        table_name = sql.Identifier(env('POSTGRESQL_MEMBERSHIPS_TABLE')[0])
        query = sql.SQL("INSERT INTO {table} (sid, pid) VALUES (%s, %s) ON CONFLICT DO NOTHING"
                        ).format(
            table=table_name
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, pid))
        return True, f"Player {pid} added to series {sid}.", 200
    except ValueError as ve:
        logger.debug(f"Validation error in adding player to series: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error adding player to Series ID {sid}: {e}")
        return False, "Internal server error", 500

@auctoramentum_bp.put('/<int:sid>/join')
@login_required
def join_series(sid: int):
    pid = as_uuid(current_user.id)
    success, message, status_code = _join_series(sid, pid)
    if success and sid not in current_user.sids: current_user.sids.append(sid)
    return jsonify({"success": success, "message": message}), status_code

def _leave_series(sid: int, pid: uuid.UUID,) -> tuple[bool, str, int]:
    """
    Remove a player from a specific series.

    Args:
        - sid (int) : The ID of the series.
        - pid (str) : The ID of the player.
    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(__name__)
    try:
        raise_on_missing_series_and_challenges(REDIS_CLIENT, sid)
        
        user_table = sql.Identifier(env('POSTGRESQL_MEMBERSHIPS_TABLE')[0])
        query = sql.SQL("DELETE FROM {table} WHERE sid = %s AND pid = %s").format(
            table=user_table
        )
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, pid))
                if cursor.rowcount == 0:
                    logger.warning(f"Player {pid} not found in series {sid}.")
                    return False, "Player not found in series", 404
        return True, f"Player {pid} removed from series {sid}.", 200
    except ValueError as ve:
        logger.debug(f"Validation error in removing player from series: {ve}")
        return False, str(ve), 404
    except Exception as e:
        logger.exception(f"Error removing player from Series ID {sid}: {e}")
        return False, "Internal server error", 500

@auctoramentum_bp.delete('/<int:sid>/leave')
@login_required
def leave_series(sid: int):
    pid = as_uuid(current_user.id)
    success, message, status_code = _leave_series(sid, pid)
    if success and sid in current_user.sids: current_user.sids.remove(sid)
    return jsonify({"success": success, "message": message}), status_code

def integration_test(checklist: list[str], checks: list[bool], pid: uuid.UUID,) -> str:
    """
    Perform an integration test for a specific series and challenge.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - pid (str) : The ID of the player.
    """
    logger = logging.getLogger(__name__)

    sid: int | None = None
    series_data = {
        "title": "Diagnostics",
        "description": "This series is created for integration testing purposes.",
        "starts_at_str": datetime.now().isoformat(),
        "ends_at_str": (datetime.now() + timedelta(days=1)).isoformat(),
        "image": "https://example.com/test_image.png"
    }
        
    checklist.append("Series Creation was successful.")
    try:
        sid_str, success, message, _ = _create_series(**series_data)
        if success:
            sid = int(sid_str)
            checks.append(True)
        else:
            logger.warning(f"Series creation check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series creation check failed: {e}")
        checks.append(False)

    checklist.append("Series Retrieval was successful.")
    try:
        series_list, success, message, _ = _get_series_list()
        if success and isinstance(series_list, list):
            title = series_data["title"]
            if any(series['title'] == title for series in series_list):
                checks.append(True)
            else:
                logger.warning(f"Series with title '{title}' not found.")
                checks.append(False)
        else:
            logger.warning(f"Series retrieval check failed: {message} {series_list}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Overview Retrieval was successful.")
    try:
        if sid is None: raise ValueError("Series ID is None, cannot retrieve overview.")
        overview, success, message, _ = _get_series_overview(sid)
        if success and isinstance(overview, dict):
            checks.append(True)
        else:
            logger.warning(f"Series overview retrieval check failed: {message}")
            checks.append(False)
    except ValueError as ve:
        logger.error(f"Series overview retrieval check failed due to a value error: {ve}")
        checks.append(False)
    except Exception as e:
        logger.exception(f"Series overview retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Signup was successful.")
    try:
        if sid is None: raise ValueError("Series ID is None, cannot join series.")
        if pid is None: raise ValueError("User ID is None, cannot add to series.")
        success, message, _ = _join_series(sid, pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Series signup check failed: {message}")
            checks.append(False)
    except ValueError as ve:
        logger.error(f"Series signup check failed due to a value error: {ve}")
        checks.append(False)
    except Exception as e:
        logger.exception(f"Series signup check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Data Retrieval was successful.")
    try:
        if sid is None: raise ValueError("Series ID is None, cannot retrieve series data.")
        series_data, success, message, _ = _get_series_data(sid)
        if success and isinstance(series_data, dict):
            checks.append(True)
        else:
            logger.warning(f"Series data retrieval check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series data retrieval check failed: {e}")
        checks.append(False)
    
    checklist.append("Series Exit was successful.")
    try:
        if sid is None: raise ValueError("Series ID is None, cannot leave series.")
        if pid is None: raise ValueError("User ID is None, cannot remove from series.")
        success, message, _ = _leave_series(sid, pid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Series exit check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series exit check failed: {e}")
        checks.append(False)
    
    return str(sid) if sid is not None else ""

def integration_test_cleanup(checklist: list[str], checks: list[bool], sid: int,) -> None:
    """
    Clean up after the integration test by deleting the created series.

    Args:
        - sid (int) : The ID of the series to delete.
    """
    logger = logging.getLogger(__name__)
    checklist.append("Series Deletion was successful.")
    try:
        if sid is None: raise ValueError("Series ID is None, cannot delete series.")
        success, message, _ = _delete_series(sid)
        if success:
            checks.append(True)
        else:
            logger.warning(f"Series deletion check failed: {message}")
            checks.append(False)
    except Exception as e:
        logger.exception(f"Series deletion check failed: {e}")
        checks.append(False)