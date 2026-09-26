"""Audit trail for admin actions."""
LOG: list = []


def record(event, actor, target):
    LOG.append({"event": event, "actor": actor, "target": target})


def clear():
    LOG.clear()
