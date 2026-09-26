from shopcore.config.settings import get_setting


def connection_info():
    return {"host": get_setting("smtp_host"), "port": get_setting("smtp_port")}
