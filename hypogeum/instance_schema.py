from psycopg2 import sql


def ensure_instance_claim_schema(cursor, instances_table: str) -> None:
    """Ensure the instance table has the columns required for worker claims."""
    table = sql.Identifier(instances_table)
    index_name = sql.Identifier(f"{instances_table}_claimable_idx")

    cursor.execute(
        sql.SQL("""
            ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS claim_id UUID,
                ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP WITH TIME ZONE;

            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table} (updated_at)
            WHERE claim_id IS NULL
              AND status IN (
                  'starting', 'pausing', 'resuming',
                  'stopping', 'restarting', 'resetting'
              );
        """).format(
            table=table,
            index_name=index_name,
        )
    )
