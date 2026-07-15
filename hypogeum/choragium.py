"""Challenge-instance routes and the asynchronous lifecycle worker."""

from . import INSTANCE_TRANSITIONS, WORKER_TRANSITIONS
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from hypogeum.armamentarium import env, db_connect, as_uuid
from hypogeum.vomitoria import locked_challenge_check

import uuid
import asyncio
import logging
import psycopg2.sql as sql


choragium_bp = Blueprint('choragium', __name__, url_prefix='/series/<int:sid>')


INSTANCE_COLUMNS = (
    'sid',
    'cid',
    'host',
    'port',
    'type',
    'status',
    'created_at',
    'started_at',
    'paused_at',
    'expires_at',
    'updated_at',
)


def _serialize_instance(instance: dict) -> dict:
    """Add stable frontend metadata to an instance row."""
    status = instance['status']
    instance['lease_seconds'] = int(env('INSTANCE_LEASE', '1800')[0])
    instance['allowed_actions'] = list(INSTANCE_TRANSITIONS.get(status, {}).keys())
    return instance


def _rows_to_instances(cursor, rows: list[tuple]) -> list[dict]:
    columns = [description[0] for description in cursor.description] if cursor.description else []
    return [_serialize_instance(dict(zip(columns, row))) for row in rows]


# --- Routes and their corresponding private functions ---


def _get_all_relevant_instances(sid: int, pid: uuid.UUID) -> list[dict]:
    """Retrieve the player's private instances and shared instances in one series."""
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        columns = sql.SQL(', ').join(sql.Identifier(column) for column in INSTANCE_COLUMNS)
        query = sql.SQL("""
            SELECT {columns}
            FROM {instances_table}
            WHERE sid = %s
              AND (pid = %s OR type = 'shared')
            ORDER BY cid ASC, type ASC, created_at ASC
        """).format(columns=columns, instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, pid))
                return _rows_to_instances(cursor, cursor.fetchall())
    except Exception as exc:
        logger.exception(
            "Error retrieving instances for Series ID %s and Player ID %s: %s",
            sid,
            pid,
            exc,
        )
        return []


def _get_all_private_instances(sid: int, pid: uuid.UUID) -> list[dict]:
    """Retrieve all private instances for a player in one series."""
    return [
        instance
        for instance in _get_all_relevant_instances(sid, pid)
        if instance['type'] == 'private'
    ]


@choragium_bp.get('/instances')
@login_required
def get_all_private_instances(sid: int):
    """Return the current player's private instances for a series."""
    pid = as_uuid(current_user.id)
    instances = _get_all_private_instances(sid, pid)
    return jsonify({'success': True, 'instances': instances}), 200


def _get_instance(sid: int, cid: int, pid: uuid.UUID) -> dict | None:
    """Retrieve the player's private instance or the challenge's shared instance."""
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        columns = sql.SQL(', ').join(sql.Identifier(column) for column in INSTANCE_COLUMNS)
        query = sql.SQL("""
            SELECT {columns}
            FROM {instances_table}
            WHERE sid = %s
              AND cid = %s
              AND ((type = 'private' AND pid = %s) OR type = 'shared')
            ORDER BY CASE WHEN type = 'private' THEN 0 ELSE 1 END, created_at ASC
            LIMIT 1
        """).format(columns=columns, instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                row = cursor.fetchone()
                if row is None:
                    return None

                columns = [description[0] for description in cursor.description] if cursor.description else []
                return _serialize_instance(dict(zip(columns, row)))
    except Exception as exc:
        logger.exception(
            "Error retrieving instance for Series ID %s, Challenge ID %s, Player ID %s: %s",
            sid,
            cid,
            pid,
            exc,
        )
        return None


@choragium_bp.get('/challenges/<int:cid>/instance')
@login_required
def get_instance(sid: int, cid: int):
    """Return one private or shared instance for the selected challenge."""
    pid = as_uuid(current_user.id)
    instance = _get_instance(sid, cid, pid)
    if instance is None:
        return jsonify({'success': False, 'message': 'Instance not found.'}), 404
    return jsonify({'success': True, 'instance': instance}), 200


def _control_instance(
    sid: int,
    cid: int,
    pid: uuid.UUID,
    action: str,
    is_admin: bool = False,
    instance_type: str = 'private',
) -> dict:
    """Ask PostgreSQL to validate and queue an instance lifecycle command."""
    try:
        function_name = env(
            'POSTGRESQL_CONTROL_INSTANCE_FUNCTION',
            'control_challenge_instance',
        )[0]
        query = sql.SQL('SELECT {function_name}(%s, %s, %s, %s, %s, %s)').format(
            function_name=sql.Identifier(function_name),
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    query,
                    (sid, cid, pid, is_admin, action, instance_type),
                )
                row = cursor.fetchone()

                if row is None or row[0] is None:
                    raise RuntimeError('Instance control function returned no result.')

                result = row[0]
                if not isinstance(result, dict):
                    raise TypeError('Instance control function returned an invalid result.')

                return result
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.exception('Error controlling instance for %s:%s:%s: %s', sid, cid, pid, exc)
        return {'success': False, 'message': str(exc), 'status_code': 500}


@choragium_bp.patch('/challenges/<int:cid>')
@login_required
@locked_challenge_check
def control_challenge_instance(sid: int, cid: int):
    """Validate and queue an instance lifecycle command."""
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()

    action = data.get('action')
    if not action or not isinstance(action, str):
        return jsonify({'success': False, 'message': 'Action is required.'}), 400

    action = action.strip().lower()
    pid = as_uuid(current_user.id)
    is_admin = current_user.is_admin
    result = _control_instance(
        sid,
        cid,
        pid,
        action,
        is_admin,
        'shared' if is_admin else 'private',
    )

    success = result.get('success', False)
    message = result.get('message', 'An error occurred while controlling the instance.')
    status_code = result.get('status_code', 500)
    response = {'success': success, 'message': message}
    if success:
        response['action'] = result.get('action', action)

    return jsonify(response), status_code


# --- Mock Service for Testing Purposes ---


def queue_expired_instances() -> int:
    """Move unlocked expired instances into the stopping state."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    query = sql.SQL("""
        WITH expired AS (
            SELECT sid, cid, pid
            FROM {table}
            WHERE status = 'started'
              AND expires_at IS NOT NULL
              AND expires_at <= CURRENT_TIMESTAMP
              AND claim_id IS NULL
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {table} AS instance
        SET status = 'stopping'
        FROM expired
        WHERE instance.sid = expired.sid
          AND instance.cid = expired.cid
          AND instance.pid = expired.pid;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            return cursor.rowcount


def claim_next_instance() -> dict | None:
    """Claim one intermediate instance without waiting on rows claimed elsewhere."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    claim_id = uuid.uuid4()
    intermediate_states = tuple(WORKER_TRANSITIONS.keys())

    query = sql.SQL("""
        WITH candidate AS (
            SELECT sid, cid, pid
            FROM {table}
            WHERE status = ANY(%s)
              AND claim_id IS NULL
            ORDER BY updated_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE {table} AS instance
        SET claim_id = %s,
            claimed_at = CURRENT_TIMESTAMP
        FROM candidate
        WHERE instance.sid = candidate.sid
          AND instance.cid = candidate.cid
          AND instance.pid = candidate.pid
        RETURNING
            instance.sid,
            instance.cid,
            instance.pid,
            instance.type,
            instance.status,
            instance.host,
            instance.port,
            instance.started_at,
            instance.paused_at,
            instance.expires_at,
            instance.claim_id,
            instance.claimed_at;
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
        raise ValueError(f'Unsupported intermediate instance state: {status}')

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
        SET status = %s,
            host = %s,
            port = %s,
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
            claim_id = NULL,
            claimed_at = NULL
        WHERE sid = %s
          AND cid = %s
          AND pid = %s
          AND status = %s
          AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    params = (
        docker_result['final_status'],
        docker_result.get('host'),
        docker_result.get('port'),
        intermediate_status,
        intermediate_status,
        intermediate_status,
        intermediate_status,
        lease_seconds,
        intermediate_status,
        intermediate_status,
        instance['sid'],
        instance['cid'],
        instance['pid'],
        intermediate_status,
        instance['claim_id'],
    )

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() is not None


def fail_instance(instance: dict) -> bool:
    """Fail an operation once and clear the claim without retrying it."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    query = sql.SQL("""
        UPDATE {table}
        SET status = 'failed',
            claim_id = NULL,
            claimed_at = NULL
        WHERE sid = %s
          AND cid = %s
          AND pid = %s
          AND status = %s
          AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    instance['sid'],
                    instance['cid'],
                    instance['pid'],
                    instance['status'],
                    instance['claim_id'],
                ),
            )
            return cursor.fetchone() is not None


def fail_stale_claims() -> int:
    """Mark abandoned intermediate operations failed and release their claims."""
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    timeout_seconds = int(env('INSTANCE_CLAIM_TIMEOUT', '300')[0])
    query = sql.SQL("""
        UPDATE {table}
        SET status = 'failed',
            claim_id = NULL,
            claimed_at = NULL
        WHERE claim_id IS NOT NULL
          AND claimed_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
          AND status = ANY(%s);
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
            logger.warning(
                'Instance claim was no longer current for %s:%s:%s',
                instance['sid'],
                instance['cid'],
                instance['pid'],
            )
            return False
        return True
    except Exception:
        logger.exception(
            'Instance operation failed for %s:%s:%s',
            instance['sid'],
            instance['cid'],
            instance['pid'],
        )
        fail_instance(instance)
        return False


async def main() -> None:
    """Poll for expired, abandoned, and newly queued instance operations."""
    logger = logging.getLogger(__name__)
    poll_seconds = float(env('INSTANCE_WORKER_POLL_SECONDS', '5')[0])

    while True:
        try:
            stale_count = fail_stale_claims()
            if stale_count:
                logger.warning('Failed and released %s stale instance claim(s).', stale_count)

            expired_count = queue_expired_instances()
            if expired_count:
                logger.info('Queued %s expired instance(s) for stopping.', expired_count)

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
