from mailer.api import send


def send_reset(email, link):
    return send(email, "Reset your password", f"Use this link: {link}", None)
