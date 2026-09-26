from mailer import api


def send_receipt(email, amount, accountant):
    return api.send(email, "Your receipt", f"Paid: {amount}", [accountant])
