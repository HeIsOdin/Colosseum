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

from . import WORKER_TRANSITIONS
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

# def ensure_instance_claim_schema(cursor, instances_table: str) -> None:
#     """Ensure the instance table has the columns required for worker claims."""
#     table = sql.Identifier(instances_table)
#     index_name = sql.Identifier(f"{instances_table}_claimable_idx")

#     cursor.execute(
#         sql.SQL("""
#             ALTER TABLE {table}
#                 ADD COLUMN IF NOT EXISTS claim_id UUID,
#                 ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP WITH TIME ZONE;

#             CREATE INDEX IF NOT EXISTS {index_name}
#             ON {table} (updated_at)
#             WHERE claim_id IS NULL
#               AND status IN (
#                   'starting', 'pausing', 'resuming',
#                   'stopping', 'restarting', 'resetting'
#               );
#         """).format(
#             table=table,
#             index_name=index_name,
#         )
#     )

def claim_next_instance() -> dict | None:
    """Claim one intermediate instance without waiting on rows claimed elsewhere."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    claim_id = uuid.uuid4()
    intermediate_states = tuple(WORKER_TRANSITIONS.keys())

    query = sql.SQL("""
        WITH candidate AS (
            SELECT sid, cid, pid FROM {table} WHERE status = ANY(%s) AND claim_id IS NULL
            ORDER BY updated_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE {table} AS i SET claim_id = %s, claimed_at = CURRENT_TIMESTAMP FROM candidate
        WHERE i.sid = candidate.sid AND i.cid = candidate.cid AND i.pid = candidate.pid
        RETURNING
            i.sid, i.cid, i.pid, i.type, i.status,
            i.host, i.port,
            i.started_at, i.paused_at, i.expires_at,
            i.claim_id, i.claimed_at;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (list(intermediate_states), claim_id))
            row = cursor.fetchone()
            if row is None:
                return None

            columns = [description[0] for description in cursor.description] if cursor.description else []
            return dict(zip(columns, row))


def mock_docker_operation(instance: dict) -> dict:
    """Return the infrastructure values produced by a mocked Docker operation."""
    status = instance['status']

    if status not in WORKER_TRANSITIONS:
        raise ValueError(f"Unsupported intermediate instance state: {status}")

    host = instance.get('host')
    port = instance.get('port')

    if status == 'starting':
        host = host or 'localhost'
        port = port or 8080
    elif status == 'stopping':
        host = None
        port = None
    elif status == 'restarting':
        host = 'localhost'
        port = 8081

    return {
        'final_status': WORKER_TRANSITIONS[status],
        'host': host,
        'port': port,
    }


def finalize_instance(instance: dict, docker_result: dict) -> bool:
    """Apply a successful Docker result only while the caller still owns the claim."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    lease_seconds = int(env('INSTANCE_LEASE', '1800')[0])
    intermediate_status = instance['status']

    query = sql.SQL("""
        UPDATE {table}
        SET status = %s, host = %s, port = %s,
        started_at = CASE
            WHEN %s IN ('starting', 'restarting') THEN CURRENT_TIMESTAMP
            ELSE started_at
        END,
        paused_at = CASE
            WHEN %s = 'pausing' THEN CURRENT_TIMESTAMP
            WHEN %s IN ('starting', 'resuming', 'stopping', 'restarting') THEN NULL
            ELSE paused_at
        END,
        expires_at = CASE
            WHEN %s IN ('starting', 'restarting')
                THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
            WHEN %s = 'resuming' AND paused_at IS NOT NULL AND expires_at IS NOT NULL
                THEN expires_at + (CURRENT_TIMESTAMP - paused_at)
            WHEN %s = 'stopping' THEN NULL
            ELSE expires_at
        END,
        claim_id = NULL, claimed_at = NULL
        WHERE sid = %s AND cid = %s AND pid = %s AND status = %s AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    params = (
        docker_result['final_status'],
        docker_result.get('host'), docker_result.get('port'),
        intermediate_status, intermediate_status, intermediate_status, intermediate_status,
        lease_seconds,
        intermediate_status, intermediate_status,
        instance['sid'], instance['cid'], instance['pid'], intermediate_status, instance['claim_id'],
    )

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() is not None


def fail_instance(instance: dict) -> bool:
    """Fail an operation once and clear the claim without retrying it."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    query = sql.SQL("""
        UPDATE {table} SET status = 'failed', claim_id = NULL, claimed_at = NULL
        WHERE sid = %s AND cid = %s AND pid = %s AND status = %s AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (
                instance['sid'], instance['cid'], instance['pid'],
                instance['status'], instance['claim_id'],
            ))
            return cursor.fetchone() is not None


def fail_stale_claims() -> int:
    """Mark abandoned intermediate operations failed and release their claims."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    timeout_seconds = int(env('INSTANCE_CLAIM_TIMEOUT', '300')[0])
    query = sql.SQL("""
        UPDATE {table}
        SET status = 'failed', claim_id = NULL, claimed_at = NULL
        WHERE claim_id IS NOT NULL
        AND claimed_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second') AND status = ANY(%s);
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (timeout_seconds, list(WORKER_TRANSITIONS.keys())))
            return cursor.rowcount


def process_one_instance() -> bool:
    """Claim and process at most one lifecycle operation."""
    logger = logging.getLogger(__name__)
    instance = claim_next_instance()
    if instance is None:
        return False

    try:
        docker_result = mock_docker_operation(instance)
        if not finalize_instance(instance, docker_result):
            logger.warning('Instance claim was no longer current for %s:%s:%s',
                instance['sid'], instance['cid'], instance['pid'],
            )
            return False
        return True
    except Exception:
        logger.exception('Instance operation failed for %s:%s:%s',
            instance['sid'], instance['cid'], instance['pid'],
        )
        fail_instance(instance)
        return False


async def main() -> None:
    logger = logging.getLogger(__name__)
    poll_seconds = float(env('INSTANCE_WORKER_POLL_SECONDS', '5')[0])

    while True:
        try:
            stale_count = fail_stale_claims()
            if stale_count:
                logger.warning('Failed and released %s stale instance claim(s).', stale_count)

            process_one_instance()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Instance worker cycle failed.')

        await asyncio.sleep(poll_seconds)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass