import warnings


def send(to, subject, body, *args, cc=None, bcc=None):
    """Build (and, in production, queue) an email message."""
    if args:
        if len(args) > 1:
            raise TypeError("send() takes at most 4 positional arguments")
        warnings.warn("passing cc positionally is deprecated; use cc=", DeprecationWarning, stacklevel=2)
        cc = args[0]
    msg = {"to": to, "subject": subject, "body": body, "cc": list(cc or [])}
    if bcc:
        msg["bcc"] = list(bcc)
    return msg
