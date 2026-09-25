from shop.api.handlers import handle_checkout, handle_login, handle_search, handle_webhook


def register_routes(app):
    app["routes"].update({"POST /login": handle_login, "GET /search": handle_search,
                           "POST /checkout": handle_checkout, "POST /webhooks/payments": handle_webhook})
