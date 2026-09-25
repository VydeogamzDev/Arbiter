def update_email(session, user_id, email):
    if "@" not in email:
        raise ValueError("invalid email")
    session.query("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
