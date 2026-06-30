from __init__ import login_manager, REDIS_CLIENT
from flask import Flask
from flask_session import Session
from psycopg2 import extras
from armamentarium import env, refresh_series_and_challenges
from vomitoria import vomitoria_bp
from auctoramentum import auctoramentum_bp
from gladiator import gladiator_bp
from sanitarium import sanitarium_bp
from pugna import pugna_bp

def configure_app(app: Flask) -> None:
    app.config['COLOSSEUM_SECRET_KEY'] = env('COLOSSEUM_SECRET_KEY')[0]
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = REDIS_CLIENT
    app.config['SESSION_KEY_PREFIX'] = env('REDIS_KEY_PREFIX')[0]
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    login_manager.init_app(app)
    Session(app)
    extras.register_uuid()  # Register UUID adapter for psycopg2

def register_routes(app: Flask) -> None:
    app.register_blueprint(vomitoria_bp)
    app.register_blueprint(auctoramentum_bp)
    app.register_blueprint(gladiator_bp)
    app.register_blueprint(pugna_bp)
    app.register_blueprint(sanitarium_bp)


def create_app() -> Flask:
    app = Flask(__name__)
    configure_app(app)
    register_routes(app)
    try:
        refresh_series_and_challenges(REDIS_CLIENT)
    except Exception as e:
        app.logger.error(f"Failed to refresh series and challenges: {e}")
    return app

app = create_app()