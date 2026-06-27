from armamentarium import env
from dotenv import load_dotenv
from psycopg2 import sql

import hashlib
import getpass
import psycopg2

load_dotenv()  # Remove for prod

def create_db_and_admin(conn: psycopg2.extensions.connection) -> None:
    database, username, password = env('POSTGRESQL_DBNAME,POSTGRESQL_USER,POSTGRESQL_PASSWD')

    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (username,),)

        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(username)),
                (password,),
            )

        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,),)

        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database),
                    sql.Identifier(username),
                )
            )

def create_series_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_SERIES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sid SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                start_date TIMESTAMP WITH TIME ZONE NOT NULL,
                end_date TIMESTAMP WITH TIME ZONE,
                image VARCHAR(255)
            );
        """).format(sql.Identifier(table_name))
    )


def create_challenges_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                cid SERIAL PRIMARY KEY,
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                difficulty VARCHAR(15) NOT NULL CHECK (
                    difficulty IN ('Sanity Check', 'Easy', 'Medium', 'Hard')
                ),
                points INTEGER NOT NULL,
                category VARCHAR(20) NOT NULL CHECK (
                    category IN ('Warmup', 'Web', 'Crypto', 'Forensics', 'Pwn', 'Misc')
                ),
                flag VARCHAR(255) NOT NULL
            );
        """).format(sql.Identifier(table_name), sql.Identifier(series_table))
    )

def create_user_table(cursor: psycopg2.extensions.cursor) -> None:
    user_table = env('POSTGRESQL_USER_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                pid UUID PRIMARY KEY,
                display_name VARCHAR(255) NOT NULL DEFAULT 'Anonymous',
                avatar VARCHAR(255) NOT NULL DEFAULT 'default',
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                sids INTEGER[] DEFAULT ARRAY[0]::INTEGER[]
            );
        """).format(sql.Identifier(user_table), sql.Identifier(series_table))
    )

def create_flag_submissions_table(cursor: psycopg2.extensions.cursor) -> None:
    table_name = env('POSTGRESQL_SUBMISSIONS_TABLE')[0]
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    user_table = env('POSTGRESQL_USER_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]

    cursor.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                fid SERIAL PRIMARY KEY,
                sid INTEGER REFERENCES {}(sid) ON DELETE CASCADE,
                pid UUID REFERENCES {}(pid) ON DELETE CASCADE,
                cid INTEGER REFERENCES {}(cid) ON DELETE CASCADE,
                submission_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                points INTEGER NOT NULL,
                UNIQUE (pid, cid, sid)
                );
            """).format(
                sql.Identifier(table_name),
                sql.Identifier(series_table),
                sql.Identifier(user_table),
                sql.Identifier(challenges_table)
        )
    )

def insert_test_data(cursor: psycopg2.extensions.cursor) -> None:
    series_table = env('POSTGRESQL_SERIES_TABLE')[0]
    challenges_table = env('POSTGRESQL_CHALLENGES_TABLE')[0]
    flag = env('COLOSSEUM_TEST_FLAG', "CTF{f4k3_fl4g_f0r_t3st1ng}")[0]
    flag_hash = hashlib.md5(flag.encode('utf-8')).hexdigest()

    cursor.execute(
        sql.SQL("""
            INSERT INTO {} (sid, title, description, start_date, end_date)
            VALUES (0, 'Health', 'This series has no effect', '2024-01-01', '2024-01-31')
            ON CONFLICT DO NOTHING;
        """).format(sql.Identifier(series_table))
    )

    cursor.execute(
        sql.SQL("""
            INSERT INTO {} (cid, sid, title, description, difficulty, points, category, flag)
            VALUES (0, 0, 'Check', 'This challenge has no points', 'Sanity Check', 0, 'Warmup', {})
            ON CONFLICT DO NOTHING;
        """).format(sql.Identifier(challenges_table), sql.Literal(flag_hash))
    )


def main():
    HOST, PORT = env('POSTGRESQL_HOST,POSTGRESQL_PORT', 'localhost,5432')
    DATABASE = input("Enter PostgreSQL database name: ")
    USER = input("Enter PostgreSQL superuser name: ")
    PASSWORD = getpass.getpass("Enter PostgreSQL superuser password: ")

    conn = psycopg2.connect(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
    conn.autocommit = True  # Ensure that database creation is committed immediately
    create_db_and_admin(conn)
    conn.close()
    print(f"Database '{DATABASE}' and admin user created successfully.")
    
    DATABASE, USER, PASSWORD = env('POSTGRESQL_DBNAME,POSTGRESQL_USER,POSTGRESQL_PASSWD')
    with psycopg2.connect(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT) as conn:
        with conn.cursor() as cursor:
            create_series_table(cursor)
            print(f"Series table created successfully in database '{DATABASE}'.")
            create_challenges_table(cursor)
            print(f"Challenges table created successfully in database '{DATABASE}'.")
            create_user_table(cursor)
            print(f"User table created successfully in database '{DATABASE}'.")
            create_flag_submissions_table(cursor)
            print(f"Flag submissions table created successfully in database '{DATABASE}'.")
    
    conn = psycopg2.connect(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
    with conn.cursor() as cursor:
        insert_test_data(cursor)
        print(f"Test data inserted successfully in database '{DATABASE}'.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()