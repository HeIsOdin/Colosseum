from __future__ import annotations

from typing import Any

import asyncio
import logging
import uuid

from psycopg2 import sql

from . import WORKER_TRANSITIONS
from hypogeum.armamentarium import db_connect, env
from hypogeum.instance_schema import ensure_instance_claim_schema


logger = logging.getLogger(__name__)


def _instances_table() -> str:
    return env('POSTGRESQL_INSTANCES_TABLE')[0]


def claim_next_instance() -> dict[str, Any] | None:
    """Claim one intermediate instance without waiting on rows claimed elsewhere."""
    table_name = _instances_table()
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
            ensure_instance_claim_schema(cursor, table_name)
            cursor.execute(query, (list(intermediate_states), claim_id))
            row = cursor.fetchone()
            if row is None:
                return None

            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))


def mock_docker_operation(instance: dict[str, Any]) -> dict[str, Any]:
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


def finalize_instance(
    instance: dict[str, Any],
    docker_result: dict[str, Any],
) -> bool:
    """Apply a successful Docker result only while the caller still owns the claim."""
    table_name = _instances_table()
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


def fail_instance(instance: dict[str, Any]) -> bool:
    """Fail an operation once and clear the claim without retrying it."""
    table_name = _instances_table()
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
    table_name = _instances_table()
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
            ensure_instance_claim_schema(cursor, table_name)
            cursor.execute(query, (timeout_seconds, list(WORKER_TRANSITIONS.keys())))
            return cursor.rowcount


def process_one_instance() -> bool:
    """Claim and process at most one lifecycle operation."""
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
