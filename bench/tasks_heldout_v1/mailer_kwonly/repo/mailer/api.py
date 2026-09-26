def send(to, subject, body, cc=None):
    """Build (and, in production, queue) an email message."""
    return {"to": to, "subject": subject, "body": body, "cc": list(cc or [])}
