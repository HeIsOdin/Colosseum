"""Challenge-instance routes and the asynchronous lifecycle worker."""

from . import INSTANCE_TRANSITIONS, WORKER_TRANSITIONS, INSTANCE_COLUMNS
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from hypogeum.armamentarium import env, db_connect, as_uuid
from hypogeum.choragium import (
    DockerInstanceProvider, InstanceProvider, MockInstanceProvider,
    DiscoveredInstance, ProviderResult,
)
from hypogeum.vomitoria import locked_challenge_check

import uuid
import asyncio
import json
import logging
import psycopg2
import psycopg2.sql as sql

choragium_bp = Blueprint('choragium', __name__, url_prefix='/series/<int:sid>')


def _max_active_instances() -> int:
    """Return the configured per-player private-instance limit."""
    try:
        limit = int(env('INSTANCE_MAX_ACTIVE_PER_USER', '3')[0])
    except ValueError as exc:
        raise ValueError('INSTANCE_MAX_ACTIVE_PER_USER must be an integer.') from exc
    if limit < 1:
        raise ValueError('INSTANCE_MAX_ACTIVE_PER_USER must be at least 1.')
    return limit


def _get_instance_quota(pid: uuid.UUID) -> dict[str, int | bool]:
    """Return global private-instance capacity for one player."""
    instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
    query = sql.SQL("""
        SELECT COUNT(*) FROM {instances_table}
        WHERE pid = %s
          AND type = 'private'
          AND (
              status NOT IN ('stopped', 'failed')
              OR (status = 'failed' AND provider_instance_id IS NOT NULL)
          );
    """).format(instances_table=instances_table)

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (pid,))
            row = cursor.fetchone()

    active = int(row[0]) if row is not None else 0
    limit = _max_active_instances()
    return {
        'limit': limit,
        'active': active,
        'remaining': max(0, limit - active),
        'can_start': active < limit,
    }

def _serialize_instance(instance: dict) -> dict:
    """Add stable frontend metadata to an instance row."""
    status = instance['status']
    instance['lease_seconds'] = int(env('INSTANCE_LEASE', '1800')[0])
    instance['allowed_actions'] = list(INSTANCE_TRANSITIONS.get(status, {}).keys())
    return instance

def _rows_to_instances(cursor: psycopg2.extensions.cursor, rows: list[tuple]) -> list[dict]:
    """Convert a list of database rows into a list of serialized instance dictionaries."""
    columns = [description[0] for description in cursor.description] if cursor.description else []
    return [_serialize_instance(dict(list(zip(columns, row)))) for row in rows]


# --- Routes and their corresponding private functions ---

def _get_all_relevant_instances(sid: int, pid: uuid.UUID) -> list[dict]:
    """Retrieve the player's private instances and shared instances in one series."""
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        columns = sql.SQL(', ').join(sql.Identifier(column) for column in INSTANCE_COLUMNS)
        query = sql.SQL("""
            SELECT {columns} FROM {instances_table}
            WHERE sid = %s AND (pid = %s OR type = 'shared')
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
    """Retrieve active private instances for a player in one series."""
    return [
        instance
        for instance in _get_all_relevant_instances(sid, pid)
        if instance['type'] == 'private' and instance['status'] not in {'stopped', 'failed'}
    ]

@choragium_bp.get('/instances')
@login_required
def get_all_private_instances(sid: int):
    """Return the current player's private instances for a series."""
    pid = as_uuid(current_user.id)
    instances = _get_all_private_instances(sid, pid)
    return jsonify({'success': True, 'instances': instances}), 200


@choragium_bp.get('/instance-quota')
@login_required
def get_instance_quota(sid: int):
    """Return the current player's global private-instance capacity."""
    try:
        quota = _get_instance_quota(as_uuid(current_user.id))
        return jsonify({'success': True, 'quota': quota}), 200
    except Exception as exc:
        logging.getLogger(__name__).exception(
            'Error retrieving instance quota for Series ID %s: %s', sid, exc,
        )
        return jsonify({'success': False, 'message': str(exc)}), 500

def _get_instance(sid: int, cid: int, pid: uuid.UUID) -> dict | None:
    """Retrieve the player's private instance or the challenge's shared instance."""
    logger = logging.getLogger(__name__)
    try:
        instances_table = sql.Identifier(env('POSTGRESQL_INSTANCES_TABLE')[0])
        columns = sql.SQL(', ').join(sql.Identifier(column) for column in INSTANCE_COLUMNS)
        query = sql.SQL("""
            SELECT {columns} FROM {instances_table}
            WHERE sid = %s AND cid = %s AND ((type = 'private' AND pid = %s) OR type = 'shared')
            ORDER BY CASE WHEN type = 'private' THEN 0 ELSE 1 END, created_at ASC
            LIMIT 1
        """).format(columns=columns, instances_table=instances_table)

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sid, cid, pid))
                row = cursor.fetchone()
                if row is None: return None

                columns = [description[0] for description in cursor.description] if cursor.description else []
                return _serialize_instance(dict(zip(columns, row)))
    except Exception as exc:
        logger.exception(
            "Error retrieving instance for Series ID %s, Challenge ID %s, Player ID %s: %s",
            sid, cid, pid, exc,
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

def _control_instance(sid: int, cid: int, pid: uuid.UUID, action: str, is_admin: bool = False,
                    instance_type: str = 'private') -> dict:
    """Ask PostgreSQL to validate and queue an instance lifecycle command."""
    try:
        function_name = env(
            'POSTGRESQL_CONTROL_INSTANCE_FUNCTION',
            'control_challenge_instance',
        )[0]
        query = sql.SQL('SELECT {function_name}(%s, %s, %s, %s, %s, %s, %s)').format(
            function_name=sql.Identifier(function_name),
        )

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        sid, cid, pid, is_admin, action, instance_type,
                        _max_active_instances(),
                    ),
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
    if data is None: data = request.form.to_dict()

    action = data.get('action')
    if not action or not isinstance(action, str):
        return jsonify({'success': False, 'message': 'Action is required.'}), 400

    action = action.strip().lower()
    pid = as_uuid(current_user.id)
    is_admin = current_user.is_admin
    result = _control_instance(sid, cid, pid, action, is_admin, 'shared' if is_admin else 'private')

    success = result.get('success', False)
    message = result.get('message', 'An error occurred while controlling the instance.')
    status_code = result.get('status_code', 500)
    response = {'success': success, 'message': message}
    if success:
        response['action'] = result.get('action', action)
    if result.get('code'):
        response['code'] = result['code']
    if result.get('quota'):
        response['quota'] = result['quota']

    return jsonify(response), status_code


# --- Infrastructure worker ---

def queue_expired_instances() -> int:
    """
    Move unlocked expired instances into the stopping state.
    1. Selects all instances that are in the started state with no claim_id
    and has expired (expires_at <= CURRENT_TIMESTAMP).
    2. Updates the status of those instances to 'stopping'.
    3. Returns the number of rows updated.
    """
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    query = sql.SQL("""
        WITH expired AS (
            SELECT sid, cid, pid FROM {table}
            WHERE status = 'started'
              AND expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP AND claim_id IS NULL
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {table} AS instance SET status = 'stopping' FROM expired
        WHERE instance.sid = expired.sid AND instance.cid = expired.cid AND instance.pid = expired.pid;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            return cursor.rowcount

def claim_next_instance(provider_name: str) -> dict | None:
    """
    Claim one intermediate instance for a provider without waiting on other claims.
    1. Selects one instance for the named provider that is in an intermediate state
    and has no claim_id, ordered by updated_at ascending, and locks it for update.
    2. Updates the claim_id and claimed_at fields of that instance to mark it as claimed.
    3. Returns the instance row as a dictionary, or None if no instance was available to claim.  
    """
    instances_table = env('POSTGRESQL_INSTANCES_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    claim_id = uuid.uuid4()
    intermediate_states = tuple(WORKER_TRANSITIONS.keys())

    query = sql.SQL("""
        WITH candidate AS (
            SELECT instance.sid, instance.cid, instance.pid, challenge.instance_config
            FROM {instances_table} AS instance
            JOIN {challenges_table} AS challenge
              ON challenge.sid = instance.sid AND challenge.cid = instance.cid
            WHERE instance.status = ANY(%s)
              AND instance.provider = %s
              AND instance.claim_id IS NULL
            ORDER BY instance.updated_at ASC FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE {instances_table} AS instance
        SET claim_id = %s, claimed_at = CURRENT_TIMESTAMP FROM candidate
        WHERE instance.sid = candidate.sid AND instance.cid = candidate.cid AND instance.pid = candidate.pid
        RETURNING
            instance.sid, instance.cid, instance.pid,
            instance.type, instance.status,
            instance.provider, instance.provider_instance_id, instance.provider_metadata,
            instance.host, instance.port,
            instance.started_at, instance.paused_at, instance.expires_at,
            instance.claim_id, instance.claimed_at,
            candidate.instance_config;
    """).format(
        instances_table=sql.Identifier(instances_table),
        challenges_table=sql.Identifier(challenges_table),
    )

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (list(intermediate_states), provider_name, claim_id))
            row = cursor.fetchone()
            if row is None: return None

            columns = [description[0] for description in cursor.description] if cursor.description else []
            return dict(zip(columns, row))

def mock_docker_operation(instance: dict) -> dict:
    """Compatibility helper retained for database-only worker tests."""
    result = MockInstanceProvider().apply(instance)
    return {
        'final_status': result.final_status,
        'host': result.host,
        'port': result.port,
        'provider_instance_id': result.provider_instance_id,
        'provider_metadata': result.provider_metadata,
    }

def finalize_instance(instance: dict, provider_result: ProviderResult | dict) -> bool:
    """
    Apply a successful provider result only while the caller still owns the claim.
    1. Updates the instance row with the final status, host, port, provider_instance_id,
    provider_metadata, and clears the claim_id and claimed_at fields.
    2. Updates the started_at field if the final status is started and started_at is NULL.
    3. Updates the paused_at field if the final status is paused and paused_at is NULL,
    or clears paused_at if the final status is started, stopped, or failed.
    4. Updates the expires_at field if the final status is started and expires_at is NULL,
    or clears expires_at if the final status is stopped or failed.
    5. Returns True if the row was updated, or False if the row was not updated
    (e.g., due to a concurrent update or the claim being lost).
    """
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    lease_seconds = int(env('INSTANCE_LEASE', '1800')[0])
    intermediate_status = instance['status']

    if isinstance(provider_result, ProviderResult): result = {
        'final_status': provider_result.final_status,
        'host': provider_result.host, 'port': provider_result.port,
        'provider_instance_id': provider_result.provider_instance_id,
        'provider_metadata': provider_result.provider_metadata,
        }
    else: result = provider_result

    query = sql.SQL("""
        UPDATE {table}
        SET status = %s, host = %s, port = %s, claim_id = NULL, claimed_at = NULL,
            provider_instance_id = %s, provider_metadata = %s::JSONB, last_error = NULL,
            started_at = CASE
                WHEN %s IN ('starting', 'restarting', 'resetting') THEN CURRENT_TIMESTAMP
                ELSE started_at
            END,
            paused_at = CASE
                WHEN %s = 'pausing' THEN CURRENT_TIMESTAMP
                WHEN %s IN ('starting', 'resuming', 'stopping', 'restarting', 'resetting') THEN NULL
                ELSE paused_at
            END,
            expires_at = CASE
                WHEN %s IN ('starting', 'restarting', 'resetting')
                    THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                WHEN %s = 'resuming' AND paused_at IS NOT NULL AND expires_at IS NOT NULL
                    THEN expires_at + (CURRENT_TIMESTAMP - paused_at)
                WHEN %s = 'stopping' THEN NULL
                ELSE expires_at
            END
        WHERE sid = %s AND cid = %s AND pid = %s AND status = %s AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    params = (result['final_status'], result.get('host'), result.get('port'),
        result.get('provider_instance_id'), json.dumps(result.get('provider_metadata') or {}),
        intermediate_status, intermediate_status, intermediate_status, intermediate_status,
        lease_seconds, intermediate_status, intermediate_status,
        instance['sid'], instance['cid'], instance['pid'], intermediate_status, instance['claim_id'],
    )

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() is not None

def fail_instance(instance: dict, error: Exception | str | None = None) -> bool:
    """
    Fail an operation once and clear the claim without retrying it.
    1. Updates the instance row with status = 'failed', last_error = error,
    and clears the claim_id and claimed_at fields.
    2. Returns True if the row was updated, or False if the row was not updated
    """
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    query = sql.SQL("""
        UPDATE {table}
        SET status = 'failed', last_error = %s, claim_id = NULL, claimed_at = NULL
        WHERE sid = %s AND cid = %s AND pid = %s AND status = %s AND claim_id = %s
        RETURNING sid;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor: cursor.execute(query,
        (
            str(error)[:4000] if error is not None else 'Instance provider operation failed.',
            instance['sid'], instance['cid'], instance['pid'], instance['status'], instance['claim_id']),
        )
        return cursor.fetchone() is not None

def fail_stale_claims() -> int:
    """
    Mark abandoned intermediate operations failed and release their claims.
    1. Updates the status of any instance that was claimed
    but not updated before the instance timed out.
    2. Returns the number of rows updated.
    """
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    timeout_seconds = int(env('INSTANCE_CLAIM_TIMEOUT', '300')[0])
    query = sql.SQL("""
        UPDATE {table}
        SET status = 'failed', claim_id = NULL, claimed_at = NULL,
            last_error = 'Instance worker claim timed out.'
        WHERE claim_id IS NOT NULL AND claimed_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
          AND status = ANY(%s);
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (timeout_seconds, list(WORKER_TRANSITIONS.keys())))
            return cursor.rowcount


def process_one_instance(provider: InstanceProvider) -> bool:
    """
    Claim and process at most one lifecycle operation for the given provider.
    A successful operation is finalized in the database. A failed operation marks
    the instance as failed and releases its claim.
    """
    logger = logging.getLogger(__name__)
    instance = claim_next_instance(provider.name)
    if instance is None: return False

    try:
        if instance['provider'] != provider.name:
            raise ValueError(f"Unsupported instance provider: {instance['provider']}")
        provider_result = provider.apply(instance)
        if not finalize_instance(instance, provider_result):
            logger.warning(
                'Instance claim was no longer current for %s:%s:%s',
                instance['sid'], instance['cid'], instance['pid'],
            )
            return False
        return True
    except Exception as exc:
        logger.exception(
            'Instance operation failed for %s:%s:%s',
            instance['sid'], instance['cid'], instance['pid'],
        )
        fail_instance(instance, exc)
        return False


def _update_reconciled_instance(snapshot: dict, target_status: str,
        discovered: DiscoveredInstance | None, error: str | None = None) -> bool:
    """
    Update one reconciled row while holding a short row lock.
    1. Selects the current status of the instance row for update.
    2. If the current status does not match the snapshot, return False.
    3. Updates the row to the target status, host, port, provider_instance_id, provider_metadata,
    and last_error.
    4. Updates the started_at if the target status is started and started_at is NULL.
    5. Updates the paused_at if the target status is paused
    and paused_at is NULL, or clears paused_at if the target status is started, stopped, or failed.
    6. Updates the expires_at if the target status is started
    and expires_at is NULL, or clears expires
    7. Clears the claim_id and claimed_at fields to release any existing claim.
    8. Returns True if the row was updated,
    or False if the row was not updated (e.g., due to a concurrent update).
    """
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    lease_seconds = int(env('INSTANCE_LEASE', '1800')[0])
    provider_instance_id = discovered.provider_instance_id if discovered else None
    provider_metadata = discovered.provider_metadata if discovered else {}
    host = discovered.host if discovered else None
    port = discovered.port if discovered else None

    select_query = sql.SQL("""
        SELECT status FROM {table} WHERE sid = %s AND cid = %s AND pid = %s FOR UPDATE;
    """).format(table=sql.Identifier(table_name))
    update_query = sql.SQL("""
        UPDATE {table} SET status = %s, host = %s, port = %s,
            provider_instance_id = %s, provider_metadata = %s::JSONB, last_error = %s,
            started_at = CASE
                WHEN %s = 'started' AND started_at IS NULL THEN CURRENT_TIMESTAMP
                ELSE started_at
            END,
            paused_at = CASE
                WHEN %s = 'paused' AND paused_at IS NULL THEN CURRENT_TIMESTAMP
                WHEN %s IN ('started', 'stopped', 'failed') THEN NULL
                ELSE paused_at
            END,
            expires_at = CASE
                WHEN %s = 'started' AND expires_at IS NULL
                    THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                WHEN %s IN ('stopped', 'failed') THEN NULL
                ELSE expires_at
            END,
            claim_id = NULL, claimed_at = NULL WHERE sid = %s AND cid = %s AND pid = %s;
    """).format(table=sql.Identifier(table_name))

    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(select_query, (snapshot['sid'], snapshot['cid'], snapshot['pid']))
            row = cursor.fetchone()
            if row is None or row[0] != snapshot['status']: return False
            cursor.execute(update_query, (target_status, host, port,
                    provider_instance_id, json.dumps(provider_metadata), error, target_status,
                    target_status, target_status, target_status, lease_seconds, target_status,
                    snapshot['sid'], snapshot['cid'], snapshot['pid'],
                ))
            return cursor.rowcount == 1


def _reconcile_target_status(snapshot: dict, discovered: DiscoveredInstance | None) -> str | None:
    """
    Return the database state implied by the current provider state.  
    1. If the provider does not have the instance, and the database says it is stopping,
    then the database should be marked as stopped.
    2. If the provider does not have the instance, and the database says it is stopped,
    but the database has a provider_instance_id, then the database should be marked as stopped.
    3. If the provider does not have the instance, and the database says it is started or paused,
    then the database should be marked as failed.
    4. If the database says it is stopping, and the provider still has the instance,
    then the database should remain as stopping.
    5. If the provider says the instance is paused, and the database says it is paused, pausing,
    or started, then the database should be marked as paused.
    6. If the provider says the instance is running, and the database says it is starting, started,
    paused, resuming, restarting, or resetting, then the database should be marked as started.
    7. If the provider says the instance is dead, exited, or removing, then the database should be marked as failed.  
    8. If none of the above conditions are met, then the database should remain in its current state and no update is needed.
    """
    database_status = snapshot['status']
    if discovered is None:
        if database_status == 'stopping': return 'stopped'
        if database_status == 'stopped' and snapshot.get('provider_instance_id'): return 'stopped'
        if database_status in {'started', 'paused'}: return 'failed'
        return None

    runtime_status = discovered.runtime_status
    if database_status == 'stopping': return None
    if runtime_status == 'paused':
        if database_status in {'paused', 'pausing', 'started'}: return 'paused'
        return None
    if runtime_status == 'running':
        if database_status in {
            'starting', 'started', 'paused', 'resuming', 'restarting', 'resetting'
        }: return 'started'
        return None
    if runtime_status in {'dead', 'exited', 'removing'}: return 'failed'
    return None


def reconcile_instances(provider: InstanceProvider) -> dict[str, int]:
    """
    Reconcile provider objects and database rows once at worker startup.  
    1. Obtains a global advisory lock to prevent concurrent reconciliation.
    If that fails, it returns immediately with a skipped count of 1.
    2. Fetches all database instances managed by the provider.
    3. Looping through each instance (snapshot):  
        a. Attempt to pull the corresponding provider instance by its provider_instance_id.
        If that fails, attempt to pull the provider instance by its (sid, cid, pid) key.
        If found, add its provider_instance_id to a set of matched provider ids.  
        b. If the database says the instance is stopped, but the provider says it exists,
        remove the container like `docker rm <CONTAINER_ID>` and that instance is None.
        c. Determine the target status for the database based on the current snapshot and instance.
        If the target status is None, skip to the next snapshot.
        d. If the target status is failed, set an error message for the database row.
        e. Update the database row to the target status and clear the claim.
    4. Looping through each discovered provider instance:  
        a. If the provider_instance_id is in the set of matched provider ids,
        skip to the next discovered instance.
        b. If the provider_instance_id is not in the set of matched provider ids,
        remove the container like `docker rm <CONTAINER_ID>`
        and increment the removed_orphans count.
    5. Return a dictionary with counts of updated, removed_orphans, and skipped instances.  
    6. Release the global advisory lock and close the database connection.
    """
    logger = logging.getLogger(__name__)
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    lock_name = 'colosseum:instance-reconcile'
    counts = {'updated': 0, 'removed_orphans': 0, 'skipped': 0}

    lock_conn = db_connect()
    lock_conn.autocommit = True
    acquired = False
    try:
        with lock_conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_name,))
            row = cursor.fetchone()
            acquired = bool(row[0]) if row is not None else False
            if not acquired:
                counts['skipped'] = 1
                return counts

            cursor.execute(
                sql.SQL("""
                    SELECT sid, cid, pid, type, status, provider,
                        provider_instance_id, provider_metadata, claim_id
                    FROM {table} WHERE provider = %s;
                """).format(table=sql.Identifier(table_name)),
                (provider.name,),
            )
            columns = [d[0] for d in cursor.description] if cursor.description else []
            snapshots = [dict(zip(columns, row)) for row in cursor.fetchall()]
            # snapshots will look like: [
            # {'sid': 1, 'cid': 2, 'pid': UUID(...), 'status': 'started', ...},
            # ...]

        discovered_instances = provider.discover() # get all instances from the provider
        discovered_by_id = {i.provider_instance_id: i for i in discovered_instances}
        discovered_by_key = {i.key: i for i in discovered_instances} # key: (sid, cid, pid)
        matched_provider_ids: set[str] = set()

        for snapshot in snapshots:
            key: tuple[int, int, str] = (snapshot['sid'], snapshot['cid'], str(snapshot['pid']))
            discovered: DiscoveredInstance | None = None
            if snapshot['provider_instance_id']:
                discovered = discovered_by_id.get(snapshot['provider_instance_id'])
            if discovered is None: discovered = discovered_by_key.get(key)
            if discovered is not None: matched_provider_ids.add(discovered.provider_instance_id)

            if snapshot['status'] == 'stopped' and discovered is not None:
                provider.remove(discovered.provider_instance_id)
                discovered = None

            # Get the target status based on the current snapshot and discovered instance
            target_status = _reconcile_target_status(snapshot, discovered)
            if target_status is None: continue # No update needed, skip to the next snapshot
            error = None
            if target_status == 'failed':
                error = 'Provider instance was missing or stopped during startup reconciliation.'
            if _update_reconciled_instance(snapshot, target_status, discovered, error):
                # Updates the database row to the target status and clears the claim
                counts['updated'] += 1

        for discovered in discovered_instances:
            if discovered.provider_instance_id in matched_provider_ids: continue
            try:
                provider.remove(discovered.provider_instance_id)
                counts['removed_orphans'] += 1
            except Exception:
                logger.exception(
                    'Failed to remove orphan provider instance %s',
                    discovered.provider_instance_id,
                )
        return counts
    finally:
        if acquired:
            try:
                with lock_conn.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_name,))
            except Exception:
                logger.exception('Failed to release instance reconciliation lock.')
        lock_conn.close()


async def main() -> None:
    """Poll for expired, abandoned, and newly queued instance operations."""
    logger = logging.getLogger(__name__)
    poll_seconds = float(env('INSTANCE_WORKER_POLL_SECONDS', '5')[0])
    provider = DockerInstanceProvider()

    try:
        reconciliation = reconcile_instances(provider)
        logger.info('Instance reconciliation completed: %s', reconciliation)

        while True:
            try:
                stale_count = fail_stale_claims()
                if stale_count:
                    logger.warning('Failed and released %s stale instance claim(s).', stale_count)

                expired_count = queue_expired_instances()
                if expired_count:
                    logger.info('Queued %s expired instance(s) for stopping.', expired_count)

                process_one_instance(provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Instance worker cycle failed.')

            await asyncio.sleep(poll_seconds)
    finally:
        provider.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
