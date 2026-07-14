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

from itsdangerous import exc

from . import INSTANCE_STATES, ALLOWED_TRANSITIONS
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

def _get_all_relevant_instances(sid: int, pid: uuid.UUID) -> list[dict]:
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
        lease = int(env('INSTANCE_LEASE', '1800')[0])
        query = sql.SQL("""
            SELECT sid, cid, host, port, type, status, created_at, {} AS lease,
            updated_at[greatest(array_upper(updated_at, 1) - 3, 1):array_upper(updated_at, 1)] AS updated_at
            FROM {instances_table}
            WHERE sid = %s AND pid = %s OR type = 'shared'
        """).format(sql.Literal(lease), instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, pid))
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                res = [dict(zip(columns, row)) for row in rows]
                for r in res:
                    status: str = r['status']
                    created_at: datetime = r['created_at']
                    updated_at: list[datetime] = r['updated_at']
                    if status == "paused":
                        r['elapsed'] = min((updated_at[-1] - created_at).total_seconds(), lease)
                    elif status == "started":
                        if len(updated_at) == 1:
                            r['elapsed'] = (datetime.now(timezone.utc) - created_at).total_seconds()
                        elif len(updated_at) == 2:
                            elapsed_before_pause = (updated_at[-2] - created_at).total_seconds()
                            elapsed_after_resume = (datetime.now(timezone.utc) - updated_at[-1]).total_seconds()
                            elapsed = elapsed_before_pause + elapsed_after_resume
                            r['elapsed'] = elapsed
                        else:
                            r['elapsed'] = (datetime.now(timezone.utc) - updated_at[-1]).total_seconds()
                    else:
                        r['elapsed'] = 0
                    r['elapsed'] = min(max(r['elapsed'], 0), lease)
                    r['updated_at'] = updated_at[-1] if updated_at else None
                    r['can_pause'] = len(updated_at) == 1
                return res
    except Exception as e:
        logger.exception(f"Error retrieving instances for Series ID {sid} and Player ID {pid}: {e}")
        return []

def _get_all_private_instances(sid: int, pid: uuid.UUID) -> list[dict]:
    """
    Retrieve all private instances for a given series and player.
    This function fetches the instance details from the database.

    Args:
        - sid (int) : The ID of the series.
        - pid (uuid.UUID) : The UUID of the player.
    Returns:
    list: A list of dictionaries containing private instance details.

    """
    all_instances = _get_all_relevant_instances(sid, pid)
    private_instances = [inst for inst in all_instances if inst['type'] == 'private']
    return private_instances

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
        lease = int(env('INSTANCE_LEASE', '1800')[0])
        query = sql.SQL("""
            SELECT sid, cid, host, port, type, status, created_at, {} AS lease,
            updated_at[greatest(array_upper(updated_at, 1) - 3, 1):array_upper(updated_at, 1)] AS updated_at
            FROM {instances_table}
            WHERE sid = %s AND cid = %s AND pid = %s
        """).format(sql.Literal(lease), instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                row = cursor.fetchone()
                if row:
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    res = dict(zip(columns, row))
                    status: str = res['status']
                    created_at: datetime = res['created_at']
                    updated_at: list[datetime] = res['updated_at']
                    if status == "paused":
                        res['elapsed'] = min((updated_at[-1] - created_at).total_seconds(), lease)
                    elif status == "started":
                        if len(updated_at) == 1:
                            res['elapsed'] = (datetime.now(timezone.utc) - created_at).total_seconds()
                        elif len(updated_at) == 3:
                            elapsed_before_pause = (updated_at[-2] - created_at).total_seconds()
                            elapsed_after_resume = (datetime.now(timezone.utc) - updated_at[-1]).total_seconds()
                            elapsed = elapsed_before_pause + elapsed_after_resume
                            res['elapsed'] = elapsed
                        else:
                            res['elapsed'] = (datetime.now(timezone.utc) - updated_at[-1]).total_seconds()
                    else:
                        res['elapsed'] = 0
                    res['elapsed'] = min(max(res['elapsed'], 0), lease)
                    res['updated_at'] = updated_at[-1] if updated_at else None
                    res['can_pause'] = len(updated_at) == 1
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

def _control_instance(sid: int, cid: int, pid: uuid.UUID, action: str, is_admin: bool = False,
    instance_type: str = "private") -> dict:
    """
    Ask PostgreSQL to validate and begin an instance lifecycle transition.

    All expected outcomes are returned as a dictionary. Unexpected database
    errors propagate to the caller.
    """
    try:
        function_name = env(
            "POSTGRESQL_CONTROL_INSTANCE_FUNCTION",
            "control_challenge_instance",
        )[0]

        query = sql.SQL("SELECT {function_name}(%s, %s, %s, %s, %s, %s)").format(
            function_name=sql.Identifier(function_name),
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid, is_admin, action, instance_type,),)
                row = cursor.fetchone()

                if row is None or row[0] is None:
                    raise RuntimeError("Instance control function returned no result.")

                result = row[0]

                if not isinstance(result, dict):
                    raise TypeError("Instance control function returned an invalid result.")

                return result
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Error controlling instance for {sid}:{cid}:{pid}: {e}")
        return {"success": False, "message": str(e), "status_code": 500}

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
    is_admin: bool = current_user.is_admin
    
    result = _control_instance(sid, cid, pid, action, is_admin,
                                "private" if not is_admin else "shared")
    success = result.get("success", False)
    message = result.get("message", "An error occurred in controlling instance")
    status_code = result.get("status_code", 500)
    instance = result.get("instance", None)
    return jsonify({"success": success, "message": message, "instance": instance}), status_code

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
            DO $$
            DECLARE
                current_status TEXT;
                host TEXT;
                port TEXT;
                created_at TIMESTAMP;
            BEGIN
                SELECT status, host, port, created_at
                INTO current_status, host, port, created_at
                FROM {instances_table}
                WHERE sid = %s AND cid = %s AND pid = %s
        """).format(instances_table=instances_table)
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                res = cursor.fetchone()
                if not res:
                    logger.error(f"No instance found for Series {sid}, Challenge {cid}, Player {pid}.")
                    return
                current_status, host, port, created_at = res
                if current_status is None:
                    raise Exception("Instance status is None, cannot perform mock action.")
                
                columns_and_values: dict[str, str | None] = {}
                bypass_trigger = 'false'
                if current_status == "starting":
                    bypass_trigger = 'true'
                    columns_and_values["status"] = "started"
                    host = "localhost"  if host is None else host  # Mock host
                    port = "8080" if port is None else port  # Mock port
                    columns_and_values["host"] = host  # Mock host
                    columns_and_values["port"] = port  # Mock port
                elif current_status == "pausing":
                    columns_and_values["status"] = "paused"
                    bypass_trigger = 'true'
                elif current_status == "resuming":
                    columns_and_values["status"] = "started"
                    bypass_trigger = 'true'
                elif current_status == "stopping":
                    columns_and_values["status"] = "stopped"
                    columns_and_values["host"] = None
                    columns_and_values["port"] = None
                elif current_status == "restarting":
                    columns_and_values["status"] = "started"
                    host, port = "oluwajuwon.dev", "8081" # Mock values for host and port
                    columns_and_values["host"] = host
                    columns_and_values["port"] = port
                    # Reset updated_at for restart
                    #columns_and_values["updated_at"] = f"array_append(updated_at, {created_at})" 
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
        WHERE status = 'started' AND
        updated_at[array_upper(updated_at, 1)] + INTERVAL {lease} < NOW()
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