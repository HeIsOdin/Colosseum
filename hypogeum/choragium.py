"""
## Implementation for 'Paused' and 'Pausing'
The valid actions for a challenge instance are more or less similar to docker actions
Things get weird with pause and that is because of the instances table schema.
The table has two fields for representing when the instance was created and last updated.
When you pause something, you usually need two timestamps so that resuming is easier.
These are when the instance was paused and the time of the action taken right before the pause
Therefore, pauses can only be done if the instance has not been acted upon since it was created.
This means that if you had stopped or restarted an instance, you will update changed_at
Hence, you cannot pause that instance anymore. You can only stop it or restart it.
"""

from . import INSTANCE_STATES
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from hypogeum.armamentarium import env, db_connect, as_uuid
from hypogeum.vomitoria import locked_challenge_check
from datetime import datetime, timedelta, timezone

import uuid
import asyncio
import logging
import psycopg2.sql as sql

choragium_bp = Blueprint('choragium', __name__, url_prefix='/series/<int:sid>')

# --- Routes and their corresponding private functions ---

def _get_all_private_instances(sid: int, pid: uuid.UUID) -> list[dict]:
    """
    Retrieve all instances for a given series, challenge, and player.
    This function fetches the instance details from the database.

    Args:
        - sid (int) : The ID of the series.
        - pid (uuid.UUID) : The UUID of the player.
    Returns:
        list: A list of dictionaries containing instance details.
    """
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        lease = sql.Literal(int(env('INSTANCE_LEASE', '1800')[0]))
        query = sql.SQL("""
            SELECT sid, cid, host, port, type, status, updated_at, {} AS lease
            FROM {instances_table}
            WHERE sid = %s AND pid = %s AND type = 'private'
            AND status IN ('starting', 'started', 'pausing', 'paused', 'restarting', 'resetting')
        """).format(lease, instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, pid))
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.exception(f"Error retrieving instances for Series ID {sid} and Player ID {pid}: {e}")
        return []

@choragium_bp.get('/instances')
@login_required
def get_all_private_instances(sid: int):
    """
    Endpoint to retrieve all private instances for the current user in a given series.
    This route is protected and requires the user to be logged in.

    Args:
        - sid (int) : The ID of the series.
    Returns:
        JSON response containing a list of instances or an error message.
    """
    pid = as_uuid(current_user.id)
    instances = _get_all_private_instances(sid, pid)
    return jsonify({"success": True, "instances": instances}), 200

def _get_instance(sid: int, cid: int, pid: uuid.UUID) -> dict | None:
    """
    Retrieve a specific instance for a given series, challenge, and player.
    This function fetches the instance details from the database.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
        - pid (uuid.UUID) : The UUID of the player.
    Returns:
        dict | None: A dictionary containing instance details or None if not found.
    """
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        lease = sql.Literal(int(env('INSTANCE_LEASE', '1800')[0]))
        query = sql.SQL("""
            SELECT sid, cid, host, port, type, status, created_at, updated_at, {} AS lease
            FROM {instances_table}
            WHERE sid = %s AND cid = %s AND pid = %s AND type = 'private'
        """).format(lease, instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                row = cursor.fetchone()
                if row:
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    res = dict(zip(columns, row))
                    if res['status'] == 'paused':
                        # NOTE: Read the docstring to understand why 'pauses' are weird
                        updated_at: datetime = res['updated_at']
                        created_at: datetime = res['created_at']
                        now = datetime.now(timezone.utc)
                        interim = (now - updated_at).total_seconds()
                        res['updated_at'] = created_at+ timedelta(seconds=interim)

                    return res
                return None
    except Exception as e:
        logger.exception(f"Error retrieving instance for Series ID {sid}, Challenge ID {cid}, Player ID {pid}: {e}")
        return None

@choragium_bp.get('/challenges/<int:cid>/instance')
@login_required
def get_instance(sid: int, cid: int):
    """
    Endpoint to retrieve a specific private instance for the current user in a given series and challenge.
    This route is protected and requires the user to be logged in.

    Args:
        - sid (int) : The ID of the series.
        - cid (int) : The ID of the challenge.
    Returns:
        JSON response containing instance details or an error message.
    """
    pid = as_uuid(current_user.id)
    instance = _get_instance(sid, cid, pid)
    if instance is None:
        return jsonify({"success": False, "message": "Instance not found."}), 404
    return jsonify({"success": True, "instance": instance}), 200

def _control_instance(sid: int, cid: int, pid: uuid.UUID, action: str, is_admin: bool = False
                    ) -> tuple[bool, str, int]:
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
        instances = len(_get_all_private_instances(sid, pid))
        
        action = action.strip().lower()
        challenges_table = sql.Identifier(env('POSTGRESQL_CHALLENGES_TABLE')[0])
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        query = sql.SQL("""
            SELECT c.requires_instance, i.status, i.type, i.created_at, i.updated_at
            FROM {challenges_table} c
            LEFT JOIN LATERAL (
                SELECT i.sid, i.cid, i.pid, i.type, i.status, i.created_at, i.updated_at
                FROM {instances_table} i
                WHERE i.sid = c.sid AND i.cid = c.cid AND (i.pid = %s OR i.type = 'shared')
                ORDER BY CASE WHEN i.pid = %s THEN 0 ELSE 1 END
                LIMIT 1
            ) i ON c.requires_instance = TRUE
            WHERE c.sid = %s AND c.cid = %s
        """).format(instances_table=instances_table, challenges_table=challenges_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (pid, pid, sid, cid))
                res = cursor.fetchone()
                if not res:
                    return False, f"{sid}:{cid} not found", 404
                
                created_at: datetime | None
                updated_at: datetime | None
                req_inst, status, inst_type, created_at, updated_at = res
                if inst_type == "shared" and not is_admin:
                    return False, "Shared instances can only be controlled by admins.", 403
                
                if not req_inst: raise ValueError(f"The challenge does not require an instance.")
                
                if status is None or created_at is None or updated_at is None:
                    if action != "start": raise ValueError("Instance is non-existent")
                    query = sql.SQL("""
                        SET LOCAL session.bypass_trigger = 'true';
                        INSERT INTO {instances_table} (sid, cid, pid) VALUES (%s, %s, %s)
                    """).format(instances_table=instances_table)
                    cursor.execute(query, (sid, cid, pid))
                    return True, f"Instance is starting", 200
                elif isinstance(status, str) and status in INSTANCE_STATES:
                    if status[-3:] == "ing":
                        raise ValueError(f"Instance is {status}. Please wait...")
                
                    if status == "failed":
                        raise ValueError(f"Instance failed. Please contact admin.")
                else: raise Exception(f"Unexpected instance status: {status}")

                intermediate_status: str
                if action == "start":
                    if status == "started": raise ValueError("Instance is already started.")
                    if instances >= 3 and action == "start" and status != "paused":
                        return False, "Too many instances running!. Stop at least one.", 403
                    intermediate_status = "starting"
                elif action == "pause":
                    # NOTE: Instances should pause only if no action has been taken on them
                    if status != "started": raise ValueError("Instance is not started. Cannot pause.")
                    if created_at != updated_at: raise ValueError("Instance cannot be paused")
                    intermediate_status = "pausing"
                elif action == "stop":
                    if status == "stopped": raise ValueError("Instance is already stopped.")
                    intermediate_status = "stopping"
                elif action == "restart":
                    if status == "stopped": raise ValueError("Instance is stopped. Please start it first.")
                    intermediate_status = "restarting"
                elif action == "reset":
                    if status == "stopped": raise ValueError("Instance is stopped. Please start it first.")
                    intermediate_status = "resetting"
                else: raise ValueError(f"Invalid action: {action}")
                query = sql.SQL("""
                        SET LOCAL session.bypass_trigger = %s;
                        UPDATE {instances_table} SET status = %s
                        WHERE sid = %s AND cid = %s AND pid = %s
                    """).format(instances_table=instances_table)
                bypass_trigger = 'true' if action == "reset" or status == "paused" else 'false'
                cursor.execute(query, (bypass_trigger, intermediate_status, sid, cid, pid))
                return True, f"Instance is {intermediate_status}", 200  
    except ValueError as ve:
        logger.debug(f"Validation error in controlling challenge instance: {ve}")
        return False, str(ve), 400
    except Exception as e:
        logger.exception(f"Error controlling challenge instance for Series ID {sid} and Challenge ID {cid}: {e}")
        return False, "Internal server error", 500

@choragium_bp.patch('/challenges/<int:cid>')
@login_required
@locked_challenge_check
def control_challenge_instance(sid: int, cid: int):  
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    action = data.get("action")
    if not action or not isinstance(action, str):
        return jsonify({"success": False, "message": "Action is required."}), 400
    action = action.strip().lower()
    pid = as_uuid(current_user.id)
    is_admin = current_user.is_admin
    
    success, message, status_code = _control_instance(sid, cid, pid, action, is_admin)
    return jsonify({"success": success, "message": message}), status_code

# --- Mock Service for Testing Purposes ---

def _mock_service(sid: int, cid: int, pid: uuid.UUID) -> None:
    """
    Mock service to simulate instance control actions.
    In a real-world scenario, this function would interact with the actual instance management service.
    """
    logger = logging.getLogger(__name__)
    logger.warning(f"Mock service called for Series ID {sid}, Challenge ID {cid}, Player ID {pid}.")
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        query = sql.SQL("""
            SELECT status FROM {instances_table} WHERE sid = %s AND cid = %s AND pid = %s
        """).format(instances_table=instances_table)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                res = cursor.fetchone()
                if not res:
                    logger.error(f"No instance found for Series {sid}, Challenge {cid}, Player {pid}.")
                    return
                current_status = res[0]
                if current_status is None:
                    raise Exception("Instance status is None, cannot perform mock action.")
                
                columns_and_values: dict[str, str] = {}
                bypass_trigger = 'false'
                if current_status == "starting":
                    bypass_trigger = 'true'
                    columns_and_values["status"] = "started"
                    columns_and_values["host"] = "localhost"  # Mock host
                    columns_and_values["port"] = "8080"  # Mock port
                elif current_status == "pausing":
                    columns_and_values["status"] = "paused"
                    bypass_trigger = 'true'
                elif current_status == "stopping":
                    columns_and_values["status"] = "stopped"
                elif current_status == "restarting":
                    columns_and_values["status"] = "started"
                    columns_and_values["host"] = "localhost"  # Mock host
                    columns_and_values["port"] = "8080"  # Mock port
                elif current_status == "resetting":
                    # NOTE: Resetting the instance should not renew the lease
                    columns_and_values["status"] = "started"
                    bypass_trigger = 'true'
                query = sql.SQL("""
                    SET LOCAL session.bypass_trigger = %s;
                    UPDATE {instances_table} SET {columns_and_values}
                    WHERE sid = %s AND cid = %s AND pid = %s
                """).format(
                    instances_table=sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0]),
                    columns_and_values=sql.SQL(', ').join(
                        sql.SQL("{} = %s").format(sql.Identifier(col))
                        for col in columns_and_values.keys()
                    )
                )
                cursor.execute(query, [bypass_trigger, *columns_and_values.values(), sid, cid, pid])
    except Exception as e:
        logger.exception(f"Error in mock service for {sid}:{cid}:{pid}: {e}")


async def main():
    logger = logging.getLogger(__name__)
    instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
    intermediate_states = [state for state in INSTANCE_STATES if state.endswith("ing")]
    intermediate_query = sql.SQL("""
        SELECT sid, cid, pid FROM {instances_table}
        WHERE status = ANY(%s)
    """).format(instances_table=instances_table)
    lease_duration = int(env('INSTANCE_LEASE', '1800')[0])
    expiry_query = sql.SQL("""
        SET LOCAL session.bypass_trigger = 'true';
        UPDATE {instances_table} SET status = 'stopping'
        WHERE (status = 'started' OR status = 'paused') AND updated_at + INTERVAL {lease} < NOW()
    """).format(
        lease=sql.Literal(f"{lease_duration} seconds"),
        instances_table=instances_table
    )
    
    try:
        with db_connect() as conn:
            with conn.cursor() as cursor:
                while True:
                    cursor.execute(expiry_query)

                    cursor.execute(intermediate_query, (intermediate_states,))
                    instances = cursor.fetchall()
                    
                    for sid, cid, pid in instances: _mock_service(sid, cid, pid)
                    
                    conn.commit()

                    # NOTE: Don't be like me and DDoS your database
                    await asyncio.sleep(5)
                    
    except asyncio.CancelledError:
        print("\nShutdown signal received. Breaking loop.")
        raise
    except Exception as e:
        logger.exception(f"Error handling intermediate instances: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass