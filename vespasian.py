from __init__ import NAME, DIFFICULTY_LEVELS, CATEGORIES, USER_STATUS, REDIS_CLIENT
from dotenv import load_dotenv
from psycopg2 import sql
from armamentarium import env, db_connect

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
                sid SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
                ends_at TIMESTAMP WITH TIME ZONE,
                image VARCHAR(255)
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
                cid SERIAL PRIMARY KEY,
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                author VARCHAR(255) NOT NULL,
                difficulty VARCHAR(15) NOT NULL CHECK (difficulty IN ({difficulty})),
                points INTEGER NOT NULL,
                category VARCHAR(20) NOT NULL CHECK (category IN ({category})),
                prerequisite INTEGER REFERENCES {}(cid) ON DELETE SET NULL,
                flag VARCHAR(255) NOT NULL
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
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
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
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                cid INTEGER REFERENCES {}(cid) ON DELETE CASCADE,
                submitted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                cid INTEGER REFERENCES {}(cid) ON DELETE CASCADE,
                subid BIGINT REFERENCES {}(subid) ON DELETE CASCADE,
                solved_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                points INTEGER NOT NULL,
                PRIMARY KEY (sid, pid, cid)
            );
        """).format(
            sql.Identifier(table_name),
            sql.Identifier(series_table),
            sql.Identifier(user_table),
            sql.Identifier(challenges_table),
            sql.Identifier(submissions_table)
        )
    )

def _create_instances_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_INSTANCES_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    player_table = env('POSTGRESQL_USER_TABLE')[0]
    status = sql.SQL(', ').join(
        sql.Literal(state) for state in ['running', 'stopped', 'restarted', 'exited']
    )

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                iid UUID PRIMARY KEY,
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                cid INTEGER REFERENCES {}(cid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                host VARCHAR(255) NOT NULL,
                port INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL CHECK (status IN ({status})),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (sid, cid, pid, status)
            );
        """).format(
            sql.Identifier(table_name),
            sql.Identifier(series_table),
            sql.Identifier(challenges_table),
            sql.Identifier(player_table),
            status=status
        )
    )

def _create_update_at_trigger(cursor: psycopg2.extensions.cursor) -> None:
    table_names = [
        sql.Identifier(table) for table in [
            env('POSTGRESQL_USER_TABLE')[0],
            env('POSTGRESQL_INSTANCES_TABLE')[0],
        ]
    ]
    for table_name in table_names:
        trigger_function_name = f"{table_name}_update_timestamp"
        trigger_name = f"{table_name}_update_timestamp_trigger"

        cursor.execute(
            sql.SQL("""
                CREATE OR REPLACE FUNCTION {}()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = CURRENT_TIMESTAMP;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """).format(sql.Identifier(trigger_function_name))
        )

        cursor.execute(
            sql.SQL("""
                DROP TRIGGER IF EXISTS {} ON {};
                CREATE TRIGGER {}
                BEFORE UPDATE ON {}
                FOR EACH ROW EXECUTE FUNCTION {}();
            """).format(
                sql.Identifier(trigger_name),
                table_name,
                sql.Identifier(trigger_name),
                table_name,
                sql.Identifier(trigger_function_name)
            )
        )

def bootstrap(SUPERDATABASE: str, SUPERUSER: str, SUPERPASSWORD: str, REDISPASSWORD: str) -> None:
    logger = logging.getLogger(NAME)
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
            logger.debug(f"Series table _created successfully in database '{DATABASE}'.")
            _create_challenges_table(cursor)
            logger.debug(f"Challenges table _created successfully in database '{DATABASE}'.")
            _create_user_table(cursor)
            logger.debug(f"User table _created successfully in database '{DATABASE}'.")
            _create_memberships_table(cursor)
            logger.debug(f"Memberships table _created successfully in database '{DATABASE}'.")
            _create_flag_submissions_table(cursor)
            logger.debug(f"Flag submissions table _created successfully in database '{DATABASE}'.")
            _create_challenge_solves_table(cursor)
            logger.debug(f"Challenge solves table _created successfully in database '{DATABASE}'.")
            _create_instances_table(cursor)
            logger.debug(f"Instances table _created successfully in database '{DATABASE}'.")
            _create_update_at_trigger(cursor)
            logger.debug(f"Update timestamp triggers _created successfully in database '{DATABASE}'.")
    
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