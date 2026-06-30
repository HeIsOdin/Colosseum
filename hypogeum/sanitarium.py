from . import NAME, REDIS_CLIENT
from flask import Blueprint, jsonify, request
from hypogeum.vomitoria import integration_test as vomitoria, admin_required
from hypogeum.gladiator import (
    integration_test as gladiator, integration_test_cleanup as pid_cleanup
)
from hypogeum.auctoramentum import (
    integration_test as auctoramentum, integration_test_cleanup as sid_cleanup
)
from hypogeum.pugna import integration_test as pugna, integration_test_cleanup as cid_cleanup
from vespasian import bootstrap
from hypogeum.armamentarium import env, db_connect, refresh_series_and_challenges

import uuid
import logging
import psycopg2.sql as sql

sanitarium_bp = Blueprint('sanitarium', __name__, url_prefix='/diagnostics')

@sanitarium_bp.get('/ping')
def ping():
    """
    Ping endpoint for Colosseum.
    
    Returns:
        JSON response indicating the service is alive.
    """
    return jsonify({"message": "Salutem Dicit Plurimam"}), 200

@sanitarium_bp.get('/health')
def health_check():
    """
    Health check endpoint for Colosseum.
    
    Returns:
        JSON response indicating the health status of the service.
    """
    logger = logging.getLogger(NAME)
    try:
        table = env('POSTGRESQL_SERIES_TABLE')[0]
        query = sql.SQL("SELECT 1 FROM {table} LIMIT 1").format(table=sql.Identifier(table))
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                db_status = cursor.fetchone()
                if db_status is None or db_status[0] != 1:
                    logger.error(f"Database health check failed: {db_status}")
                    return jsonify({"message": "Database connection failed"}), 503
        refresh_series_and_challenges(REDIS_CLIENT)
        return jsonify({"message": "Si Vales Bene Est, Ego Valeo"}), 200
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return jsonify({"message": "Internal Server Error"}), 500

def _integration_test() -> tuple[dict, bool, str, int]:
    """
    Perform an integration test to check the health of the Colosseum service.

    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    checklist: list[str] = []
    checks: list[bool] = []
    sid: int = 0
    cid: int = 0
    pid: uuid.UUID | None = None
    email: str = uuid.uuid4().hex + "@oluwajuwon.dev"
    password: str = uuid.uuid4().hex

    logger = logging.getLogger(NAME)
    try:
        refresh_series_and_challenges(REDIS_CLIENT)
        pid = vomitoria(checklist, checks, email, password)
        if pid is None: raise ValueError("User ID is None, cannot continue with test.")
        gladiator(checklist, checks, pid)
        sid_str = auctoramentum(checklist, checks, pid)
        if not sid_str.isdigit(): raise ValueError(f"Series ID is not a valid integer: {sid_str}")
        sid = int(sid_str)
        cid_str = pugna(checklist, checks, sid, pid)
        if not cid_str.isdigit(): raise ValueError(f"Challenge ID is not a valid integer: {cid_str}")
        cid = int(cid_str)
        pid_cleanup(checklist, checks, pid)
        cid_cleanup(checklist, checks, sid, cid)
        sid_cleanup(checklist, checks, sid)
        return {"checklist": checklist, "checks": checks}, True, "", 200
    except ValueError as ve:
        logger.error(f"Integration test failed due to a value error: {ve}")
        return {"checklist": checklist, "checks": checks}, False, str(ve), 400
    except Exception as e:
        logger.exception(f"Integration test failed: {e}")
        return {"checklist": checklist, "checks": checks}, False, "Internal Server Error", 500

@sanitarium_bp.get('/integration-test')
#@admin_required
def integration_test():
    """
    Perform an integration test to check the health of the Sanitarium service.

    Returns:
        JSON response indicating the result of the integration test.
    """
    result, success, message, status_code = _integration_test()
    return jsonify({"success": success, "message": message, "result": result}), status_code

def _bootstrap_and_test(data: dict[str, str]) -> tuple[dict, bool, str, int]:
    """
    Bootstrap the Colosseum service by setting up the database and necessary configurations.

    Returns:
        tuple: A tuple containing a boolean indicating success, a message, and an HTTP status code.
    """
    logger = logging.getLogger(NAME)
    try:
        superdatabase = data.get("superdatabase")
        if not superdatabase: raise ValueError("Database name is required for bootstrapping.")
        superuser = data.get("superuser")
        if not superuser: raise ValueError("User name is required for bootstrapping.")
        superpassword = data.get("superpassword")
        if not superpassword: raise ValueError("Password is required for bootstrapping.")
        redispassword = data.get("redispassword")
        if not redispassword: raise ValueError("Redis password is required for bootstrapping.")
        bootstrap(superdatabase, superuser, superpassword, redispassword)
        refresh_series_and_challenges(REDIS_CLIENT)
        result, success, message, status_code = _integration_test()
        if not success:
            logger.error(f"Integration test failed after bootstrapping: {message}")
            return result, False, message, status_code
        return result, True, "Bootstrapping completed successfully.", 200
    except ValueError as ve:
        logger.error(f"Bootstrap failed due to a value error: {data} {ve}")
        return {}, False, str(ve), 400
    except Exception as e:
        logger.exception(f"Bootstrap failed: {e}")
        return {}, False, "Internal Server Error", 500

@sanitarium_bp.patch('/bootstrap')
#@admin_required
def bootstrap_and_test():
    """
    Bootstrap the Colosseum service by setting up the database and necessary configurations.

    Returns:
        JSON response indicating the result of the bootstrap operation.
    """
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    
    result, success, message, status_code = _bootstrap_and_test(data)
    return jsonify({"success": success, "message": message, **result}), status_code