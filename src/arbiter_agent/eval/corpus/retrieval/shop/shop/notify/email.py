import logging

log = logging.getLogger("shop.email")


def send_email(user_id, subject, body):
    log.info("email to %s: %s", user_id, subject)
    return True
