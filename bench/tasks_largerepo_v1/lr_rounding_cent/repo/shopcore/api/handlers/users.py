from shopcore.audit import record


def create_user(users, user_id, actor):
    users[user_id] = {"id": user_id, "active": True}
    record("user.create", actor, user_id)
    return users[user_id]
