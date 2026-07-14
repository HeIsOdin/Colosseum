from . import (
    DIFFICULTY_LEVELS, CATEGORIES, USER_STATUS, REDIS_CLIENT, INSTANCE_STATES, INSTANCES_TYPES,
    ALLOWED_TRANSITIONS
)
from dotenv import load_dotenv
from psycopg2 import sql
from hypogeum.armamentarium import env, db_connect

import json
import redis
import getpass
import logging
import psycopg2

load_dotenv()  # Remove for prod

def _create_db_admin_and_user(conn: psycopg2.extensions.connection, r: redis.Redis) -> None:
    database = env('POSTGRESQL_DBNAME')[0]
    admin, admin_password = env('POSTGRESQL_ADMIN,POSTGRESQL_ADMIN_PASSWD')
    username, password = env('POSTGRESQL_USER,POSTGRESQL_PASSWD')

    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (admin,),)

        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(admin)),
                (admin_password,),
            )
        
        cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (username,),)

        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(username)),
                (password,),
            )

        r_user, r_pass, key_prefix = env('REDIS_USER,REDIS_PASSWD,REDIS_KEY_PREFIX')
        r.execute_command("ACL", "SETUSER", r_user, 'on', f'>{r_pass}', f'~{key_prefix}*', '&*', '+@all')
        r.execute_command("ACL", "SAVE")

        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,),)

        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database),
                    sql.Identifier(admin),
                )
            )

def _grant_privileges_to_user(conn: psycopg2.extensions.connection) -> None:
    database = env('POSTGRESQL_DBNAME')[0]
    username = env('POSTGRESQL_USER')[0]
    privileges_on_tables = sql.SQL(', ').join(
        sql.SQL(priv) for priv in ['SELECT', 'INSERT', 'UPDATE', 'DELETE']
    )
    privileges_on_sequences = sql.SQL(', ').join(
        sql.SQL(priv) for priv in ['USAGE', 'SELECT']
    )
    query = sql.SQL("GRANT CONNECT ON DATABASE {database} TO {user}; " \
                    "GRANT USAGE ON SCHEMA public TO {user}; " \
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public " \
                    "GRANT {t_privileges} ON TABLES TO {user}; " \
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public " \
                    "GRANT {s_privileges} ON SEQUENCES TO {user};").format(
                        database=sql.Identifier(database),
                        user=sql.Identifier(username),
                        t_privileges=privileges_on_tables,
                        s_privileges=privileges_on_sequences
                    )
    with conn.cursor() as cursor:
        cursor.execute(query)

def _create_series_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_SERIES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sid BIGSERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                host JSONB NOT NULL,
                starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
                ends_at TIMESTAMP WITH TIME ZONE,
                image VARCHAR(255),
                metadata JSONB,
                UNIQUE (sid)
            );
        """).format(sql.Identifier(table_name))
    )


def _create_challenges_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    difficulty = sql.SQL(', ').join(
        sql.Literal(level) for level in DIFFICULTY_LEVELS
    )
    category = sql.SQL(', ').join(
        sql.Literal(cat)
        for cat in CATEGORIES
    )

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                cid BIGSERIAL,
                sid BIGINT REFERENCES {}(sid) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                author VARCHAR(255) NOT NULL,
                difficulty VARCHAR(15) NOT NULL CHECK (difficulty IN ({difficulty})),
                points INTEGER NOT NULL,
                category VARCHAR(20) NOT NULL CHECK (category IN ({category})),
                prerequisite BIGINT,
                flag VARCHAR(255) NOT NULL,
                requires_instance BOOLEAN NOT NULL DEFAULT FALSE,
                file_url VARCHAR(2048),
                PRIMARY KEY (cid, sid),
                FOREIGN KEY (sid, prerequisite) REFERENCES {}(sid, cid) ON DELETE SET NULL
            );
        """).format(
            sql.Identifier(table_name), sql.Identifier(series_table), sql.Identifier(table_name),
            difficulty=difficulty, category=category
        )
    )

def _create_user_table(cursor: psycopg2.extensions.cursor) -> None:
    user_table = env('POSTGRESQL_USER_TABLE')[0]
    status = sql.SQL(', ').join(
        sql.Literal(state) for state in USER_STATUS
    )

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                pid UUID PRIMARY KEY,
                display_name VARCHAR(20) NOT NULL DEFAULT 'Anonymous',
                avatar VARCHAR(10) NOT NULL DEFAULT 'default',
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                status VARCHAR(20) NOT NULL CHECK (status IN ({status})) DEFAULT 'active',
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                profile_updated_at TIMESTAMP WITH TIME ZONE,
                password_changed_at TIMESTAMP WITH TIME ZONE,
                status_changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP WITH TIME ZONE
            );
        """).format(sql.Identifier(user_table), status=status)
    )

def _create_memberships_table(cursor: psycopg2.extensions.cursor) -> None:
    memberships_table = env('POSTGRESQL_MEMBERSHIPS_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    user_table = env('POSTGRESQL_USER_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sid BIGINT REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                PRIMARY KEY (sid, pid)
            );
        """).format(
            sql.Identifier(memberships_table),
            sql.Identifier(series_table),
            sql.Identifier(user_table)
        )
    )

def _create_flag_submissions_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_SUBMISSIONS_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    user_table = env('POSTGRESQL_USER_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                subid BIGSERIAL PRIMARY KEY,
                sid BIGINT REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                cid BIGINT,
                submitted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sid, cid) REFERENCES {}(sid, cid) ON DELETE CASCADE
                );
            """).format(
                sql.Identifier(table_name),
                sql.Identifier(series_table),
                sql.Identifier(user_table),
                sql.Identifier(challenges_table)
        )
    )

def _create_challenge_solves_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_SOLVES_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    user_table = env('POSTGRESQL_USER_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    submissions_table = env('POSTGRESQL_SUBMISSIONS_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sid BIGINT REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                cid BIGINT,
                subid BIGINT REFERENCES {}(subid) ON DELETE CASCADE,
                solved_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                points INTEGER NOT NULL,
                PRIMARY KEY (sid, pid, cid),
                FOREIGN KEY (sid, cid) REFERENCES {}(sid, cid) ON DELETE CASCADE
            );
        """).format(
            sql.Identifier(table_name),
            sql.Identifier(series_table),
            sql.Identifier(user_table),
            sql.Identifier(submissions_table),
            sql.Identifier(challenges_table),
        )
    )

def _create_instances_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    player_table = env('POSTGRESQL_USER_TABLE')[0]
    types = sql.SQL(', ').join(
        sql.Literal(instance_type) for instance_type in INSTANCES_TYPES
    )
    status = sql.SQL(', ').join(
        sql.Literal(state) for state in INSTANCE_STATES
    )

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sid BIGINT REFERENCES {}(sid) ON DELETE CASCADE,
                cid BIGINT,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                host VARCHAR(255),
                port INTEGER,
                type VARCHAR(50) NOT NULL CHECK (type IN ({types})) DEFAULT 'private',
                status VARCHAR(20) NOT NULL CHECK (status IN ({status})) DEFAULT 'starting',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP WITH TIME ZONE,
                paused_at TIMESTAMP WITH TIME ZONE,
                expires_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sid, cid, pid),
                FOREIGN KEY (sid, cid) REFERENCES {}(sid, cid) ON DELETE CASCADE
            );
        """).format(
            sql.Identifier(table_name),
            sql.Identifier(series_table),
            sql.Identifier(player_table),
            sql.Identifier(challenges_table),
            types=types,
            status=status
        )
    )

def _create_update_at_trigger(cursor: psycopg2.extensions.cursor) -> None:
    user_table = env("POSTGRESQL_USER_TABLE")[0]
    instances_table = env("POSTGRESQL_INSTANCES_TABLE")[0]

    user_trigger_function_name = f"{user_table}_update_timestamp"
    user_trigger_name = f"{user_table}_update_timestamp_trigger"

    cursor.execute(
        sql.SQL("""
            CREATE OR REPLACE FUNCTION {function_name}()
            RETURNS TRIGGER AS $$
            BEGIN
                IF current_setting('session.bypass_trigger', true) = 'true' THEN
                    RETURN NEW;
                END IF;

                IF NEW.display_name IS DISTINCT FROM OLD.display_name
                   OR NEW.avatar IS DISTINCT FROM OLD.avatar THEN
                    NEW.profile_updated_at = CURRENT_TIMESTAMP;
                END IF;

                IF NEW.password IS DISTINCT FROM OLD.password THEN
                    NEW.password_changed_at = CURRENT_TIMESTAMP;
                END IF;

                IF NEW.status IS DISTINCT FROM OLD.status THEN
                    NEW.status_changed_at = CURRENT_TIMESTAMP;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """).format(
            function_name=sql.Identifier(user_trigger_function_name),
        )
    )

    cursor.execute(
        sql.SQL("""
            DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION {function_name}();
        """).format(
            trigger_name=sql.Identifier(user_trigger_name),
            table_name=sql.Identifier(user_table),
            function_name=sql.Identifier(user_trigger_function_name),
        )
    )

    instance_trigger_function_name = f"{instances_table}_update_timestamp"
    instance_trigger_name = f"{instances_table}_update_timestamp_trigger"

    cursor.execute(
        sql.SQL("""
            CREATE OR REPLACE FUNCTION {function_name}()
            RETURNS TRIGGER AS $$
            BEGIN
                IF current_setting('session.bypass_trigger', true) = 'true' THEN
                    RETURN NEW;
                END IF;

                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """).format(
            function_name=sql.Identifier(instance_trigger_function_name),
        )
    )

    cursor.execute(
        sql.SQL("""
            DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION {function_name}();
        """).format(
            trigger_name=sql.Identifier(instance_trigger_name),
            table_name=sql.Identifier(instances_table),
            function_name=sql.Identifier(instance_trigger_function_name),
        )
    )

def _create_instance_control_function(cursor: psycopg2.extensions.cursor,) -> None:
    """
    Create the database function responsible for validating and beginning
    challenge-instance lifecycle transitions.

    The function returns JSONB for both successful operations and expected
    validation failures. Unexpected database errors still propagate normally.
    """
    function_name = env(
        "POSTGRESQL_CONTROL_INSTANCE_FUNCTION",
        "control_challenge_instance",
    )[0]

    challenges_table = env("POSTGRESQL_CHALLENGES_TABLE")[0]
    instances_table = env("POSTGRESQL_INSTANCES_TABLE")[0]
    application_user = env("POSTGRESQL_USER")[0]

    query = sql.SQL("""
        CREATE OR REPLACE FUNCTION {function_name}(
            p_sid BIGINT, p_cid BIGINT, p_pid UUID, p_is_admin BOOLEAN,
            p_action TEXT, p_instance_type TEXT DEFAULT 'private'
        )
        RETURNS JSONB
        LANGUAGE plpgsql
        VOLATILE
        SECURITY INVOKER
        AS $function$
        DECLARE
            v_action TEXT; v_requested_type TEXT; v_existing_type TEXT;
            v_lock_key TEXT;

            v_allowed_transitions JSONB := {allowed_transitions}::JSONB;
            v_intermediate_status TEXT;

            v_requires_instance BOOLEAN; v_instance_exists BOOLEAN := FALSE;
            v_active_instances BIGINT := 0;
            v_rows_updated BIGINT := 0;

            v_owner_pid UUID;
            v_status TEXT;
            v_previous_status TEXT;
            v_created_at TIMESTAMP WITH TIME ZONE;
            v_updated_at TIMESTAMP WITH TIME ZONE;
        BEGIN
            /* Normalize and validate the caller-supplied values */
            v_action := LOWER(BTRIM(COALESCE(p_action, '')));
            v_requested_type := LOWER(BTRIM(COALESCE(p_instance_type, 'private')));

            IF p_sid IS NULL OR p_cid IS NULL OR p_pid IS NULL THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', 'Series ID, challenge ID, and player ID are required.',
                    'status_code', 400
                );
            END IF;

            IF v_action NOT IN ('start','pause','resume','stop','restart','reset') THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', FORMAT('Invalid instance action: %s', v_action),
                    'status_code', 400
                );
            END IF;

            IF v_requested_type NOT IN ('private', 'shared') THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', FORMAT('Invalid instance type: %s', v_requested_type),
                    'status_code', 400
                );
            END IF;

            IF v_requested_type = 'shared' AND NOT COALESCE(p_is_admin, FALSE) THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', 'Shared instances can only be controlled by admins.',
                    'status_code', 403
                );
            END IF;

            /* Private-instance quota decisions are serialized by player and series.
             * Shared-instance decisions are serialized by challenge.
            */
            v_lock_key := CASE
                WHEN v_requested_type = 'shared' THEN
                    'shared:' || p_sid::TEXT || ':' || p_cid::TEXT
                ELSE
                    'private:' || p_sid::TEXT || ':' || p_pid::TEXT
            END;

            PERFORM pg_advisory_xact_lock(hashtextextended(v_lock_key, 0));

            /* Lock challenge row to prevent mutations while transacting */
            SELECT c.requires_instance INTO v_requires_instance FROM {challenges_table} AS c
            WHERE c.sid = p_sid AND c.cid = p_cid FOR SHARE;

            IF NOT FOUND THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', FORMAT('Challenge %s:%s not found', p_sid, p_cid),
                    'status_code', 404
                );
            END IF;

            IF NOT v_requires_instance THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', 'The challenge does not require an instance.',
                    'status_code', 400
                );
            END IF;

            /* For private instances, ownership must match the supplied PID.
             * A shared instance is controlled by type and challenge; its actual
             * owner PID is loaded from the selected row.
             */
            SELECT i.pid, i.status, i.type, i.created_at, i.updated_at
            INTO v_owner_pid, v_status, v_existing_type, v_created_at, v_updated_at
            FROM {instances_table} AS i
            WHERE i.sid = p_sid AND i.cid = p_cid
            AND (
                    (v_requested_type = 'private' AND i.type = 'private' AND i.pid = p_pid)
                OR
                    (v_requested_type = 'shared' AND i.type = 'shared')
            )
            ORDER BY CASE WHEN i.pid = p_pid THEN 0 ELSE 1 END, i.created_at ASC, i.pid ASC
            LIMIT 1
            FOR UPDATE;

            v_instance_exists := FOUND;

            /* A missing instance may only receive the start action. */
            IF NOT v_instance_exists THEN
                IF v_action <> 'start' THEN
                    RETURN jsonb_build_object(
                        'success', FALSE,
                        'message', 'Instance is non-existent.',
                        'status_code', 400
                    );
                END IF;

                IF v_requested_type = 'private' THEN
                    SELECT COUNT(*) INTO v_active_instances FROM {instances_table} AS i
                    WHERE i.sid = p_sid AND i.pid = p_pid AND i.type = 'private'
                    AND i.status NOT IN ('stopped', 'failed');

                    IF v_active_instances >= 3 THEN
                        RETURN jsonb_build_object(
                            'success', FALSE,
                            'message', 'Too many instances running! Stop at least one.',
                            'status_code', 403
                        );
                    END IF;
                END IF;

                BEGIN
                    INSERT INTO {instances_table} (sid, cid, pid, type)
                    VALUES (p_sid, p_cid, p_pid, v_requested_type)
                    RETURNING pid, status, type, created_at, updated_at
                    INTO v_owner_pid, v_status, v_existing_type, v_created_at, v_updated_at;

                    EXCEPTION
                        WHEN unique_violation THEN
                            RETURN jsonb_build_object(
                                'success', FALSE,
                                'message', 'The instance was created by another operation.',
                                'status_code', 409
                            );
                    END;

                    RETURN jsonb_build_object(
                        'success', TRUE,
                        'message', 'Instance is starting',
                        'status_code', 200,
                        'instance', jsonb_build_object(
                            'sid', p_sid,
                            'cid', p_cid,
                            'pid', v_owner_pid,
                            'type', v_existing_type,
                            'status', v_status,
                            'created_at', v_created_at,
                            'updated_at', v_updated_at
                        )
                    );
            END IF;

            /* Existing-instance validation. */
            IF v_status = 'failed' THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', 'Instance failed. Please contact admin.',
                    'status_code', 409
                );
            END IF;

            IF RIGHT(v_status, 3) = 'ing' THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', FORMAT('Instance is %s. Please wait...', v_status),
                    'status_code', 409
                );
            END IF;

            v_previous_status := v_status;
            v_intermediate_status :=
                v_allowed_transitions-> v_previous_status->> v_action;

            IF v_intermediate_status IS NULL THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', FORMAT('"%s" on a %s instance is invalid.',
                        v_action, v_previous_status
                    ),
                    'status_code', 409
                );
            END IF;

            /* Starting an existing stopped private instance consumes a quota slot */
            IF v_requested_type = 'private' AND v_action = 'start' THEN
                SELECT COUNT(*) INTO v_active_instances FROM {instances_table} AS i
                WHERE i.sid = p_sid AND i.pid = p_pid AND i.type = 'private'
                  AND i.status NOT IN ('stopped', 'failed');

                IF v_active_instances >= 3 THEN
                    RETURN jsonb_build_object(
                        'success', FALSE,
                        'message', 'Too many instances running! Stop at least one.',
                        'status_code', 403
                    );
                END IF;
            END IF;

            /* Preserve the current reset behavior: resetting does not advance
             * updated_at and therefore does not renew the lease.
             */
            PERFORM set_config(
                'session.bypass_trigger',
                CASE WHEN v_intermediate_status = 'resetting' THEN 'true' ELSE 'false' END,
                TRUE
            );

            /*
             * The previous status remains in the WHERE clause. This prevents
             * the function from overwriting a state changed by another writer.
             */
            UPDATE {instances_table} AS i SET status = v_intermediate_status
            WHERE i.sid = p_sid
              AND i.cid = p_cid
              AND i.pid = v_owner_pid
              AND i.type = v_requested_type
              AND i.status = v_previous_status
            RETURNING
                i.status,
                i.created_at,
                i.updated_at
            INTO
                v_status,
                v_created_at,
                v_updated_at;

            GET DIAGNOSTICS v_rows_updated = ROW_COUNT;

            /*
             * Do not allow the trigger bypass setting to leak into another
             * statement in the caller's transaction.
             */
            PERFORM set_config('session.bypass_trigger', 'false', TRUE);

            IF v_rows_updated = 0 THEN
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'message', 'The instance changed during this operation. Try again.',
                    'status_code', 409
                );
            END IF;

            RETURN jsonb_build_object(
                'success', TRUE,
                'message', FORMAT('Instance is %s', v_status),
                'status_code', 200,
                'instance', jsonb_build_object(
                    'sid', p_sid,
                    'cid', p_cid,
                    'pid', v_owner_pid,
                    'type', v_requested_type,
                    'status', v_status,
                    'created_at', v_created_at,
                    'updated_at', v_updated_at
                )
            );
        END;
        $function$;

        REVOKE ALL ON FUNCTION {function_name}(BIGINT, BIGINT, UUID, BOOLEAN, TEXT, TEXT)
        FROM PUBLIC;

        GRANT EXECUTE ON FUNCTION {function_name}(BIGINT, BIGINT, UUID, BOOLEAN, TEXT, TEXT)
        TO {application_user};
    """).format(
        function_name=sql.Identifier(function_name),
        challenges_table=sql.Identifier(challenges_table),
        instances_table=sql.Identifier(instances_table),
        allowed_transitions=sql.Literal(json.dumps(ALLOWED_TRANSITIONS)),
        application_user=sql.Identifier(application_user),
    )

    cursor.execute(query)

def _create_indexes(cursor: psycopg2.extensions.cursor) -> None:
    # Create indexes for the tables to improve query performance
    memberships_table = env('POSTGRESQL_MEMBERSHIPS_TABLE')[0]
    submissions_table = env('POSTGRESQL_SUBMISSIONS_TABLE')[0]
    solves_table = env('POSTGRESQL_SOLVES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE INDEX IF NOT EXISTS idx_submissions_sid_cid_pid_submitted_at
            ON {} (sid, cid, pid, submitted_at DESC);

            CREATE INDEX IF NOT EXISTS idx_solves_sid_cid ON {} (sid, cid);

            CREATE INDEX IF NOT EXISTS idx_solves_pid ON {} (pid);

            CREATE INDEX IF NOT EXISTS idx_memberships_pid ON {} (pid);
        """).format(
            sql.Identifier(submissions_table),
            sql.Identifier(solves_table),
            sql.Identifier(solves_table),
            sql.Identifier(memberships_table)
        )
    )

def bootstrap(SUPERDATABASE: str, SUPERUSER: str, SUPERPASSWORD: str, REDISPASSWORD: str) -> None:
    logger = logging.getLogger(__name__)
    HOST, PORT = env('POSTGRESQL_HOST,POSTGRESQL_PORT', 'localhost,5432')
    REDIS_HOST = env('REDIS_HOST', 'localhost:6379')[0]
    
    conn = psycopg2.connect(database=SUPERDATABASE, user=SUPERUSER, password=SUPERPASSWORD, host=HOST, port=PORT)
    r = redis.from_url(f"redis://:{REDISPASSWORD}@{REDIS_HOST}/0")
    conn.autocommit = True  # NOTE: Autocommit is necessary for creating databases and roles
    _create_db_admin_and_user(conn, r)
    conn.close()
    logger.debug("Database, admin, and user _created successfully.")
    
    DATABASE, ADMIN, ADMIN_PASSWORD = env('POSTGRESQL_DBNAME,POSTGRESQL_ADMIN,POSTGRESQL_ADMIN_PASSWD')
    with psycopg2.connect(database=DATABASE, user=ADMIN, password=ADMIN_PASSWORD, host=HOST, port=PORT) as conn:
        _grant_privileges_to_user(conn)
        logger.debug(f"Privileges granted to user '{ADMIN}' on database '{DATABASE}'.")
        with conn.cursor() as cursor:
            _create_series_table(cursor)
            logger.debug(f"Series table created successfully in database '{DATABASE}'.")
            _create_challenges_table(cursor)
            logger.debug(f"Challenges table created successfully in database '{DATABASE}'.")
            _create_user_table(cursor)
            logger.debug(f"User table created successfully in database '{DATABASE}'.")
            _create_memberships_table(cursor)
            logger.debug(f"Memberships table created successfully in database '{DATABASE}'.")
            _create_flag_submissions_table(cursor)
            logger.debug(f"Flag submissions table created successfully in database '{DATABASE}'.")
            _create_challenge_solves_table(cursor)
            logger.debug(f"Challenge solves table created successfully in database '{DATABASE}'.")
            _create_instances_table(cursor)
            logger.debug(f"Instances table created successfully in database '{DATABASE}'.")
            _create_update_at_trigger(cursor)
            logger.debug(f"Update timestamp triggers created successfully in database '{DATABASE}'.")
            _create_instance_control_function(cursor)
            logger.debug(f"Instance control function created successfully in database '{DATABASE}'.")
            _create_indexes(cursor)
            logger.debug(f"Indexes created successfully in database '{DATABASE}'.")
    
    with db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            res = cursor.fetchone()
            if res is None or res[0] != 1:
                raise Exception(f"Error: Failed to connect to '{DATABASE}' {res}.")
            if not REDIS_CLIENT.ping():
                raise Exception(f"Error: Failed to connect to Redis at '{REDIS_HOST}'.")


def main():
    SUPERDATABASE = input("Enter PostgreSQL database name: ")
    SUPERUSER = input("Enter PostgreSQL superuser name: ")
    SUPERPASSWORD = getpass.getpass("Enter PostgreSQL superuser password: ")
    REDISPASSWORD = getpass.getpass("Enter Redis password: ")
    bootstrap(SUPERDATABASE, SUPERUSER, SUPERPASSWORD, REDISPASSWORD)

if __name__ == "__main__":
    main()
