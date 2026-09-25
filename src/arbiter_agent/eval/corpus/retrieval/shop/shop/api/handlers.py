from shop.catalog.products import search_products
from shop.checkout.checkout import checkout
from shop.payments.gateway import verify_webhook
from shop.users.auth import login


def handle_login(app, req):
    return {"user_id": login(app["db"], req["email"], req["password"])}


def handle_search(app, req):
    return [p.sku for p in search_products(app["db"], req["q"])]


def handle_checkout(app, req):
    return checkout(app["db"], req["cart"], app["settings"], req["card_token"])


def handle_webhook(app, req):
    if not verify_webhook(req["body"], req["signature"]):
        return {"status": 401}
    return {"status": 200}
