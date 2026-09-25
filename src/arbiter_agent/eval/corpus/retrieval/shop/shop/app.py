from shop.config import load_settings
from shop.api.routes import register_routes
from shop.db.session import open_session


def create_app(env="dev"):
    settings = load_settings(env)
    app = {"settings": settings, "routes": {}}
    app["db"] = open_session(settings["database_url"])
    register_routes(app)
    return app
