from mailer.api import send


def send_welcome(email, admin):
    return send(email, "Welcome", "Thanks for joining.", cc=[admin])
