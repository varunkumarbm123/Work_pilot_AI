from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting


def setting_value(db: Session, key: str, default: object = None) -> object:
    setting = db.get(Setting, key)
    return setting.value if setting is not None else default


def upsert_setting(db: Session, key: str, value: object, description: str = "") -> Setting:
    setting = db.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=value, description=description)
        db.add(setting)
    else:
        setting.value = value
        if description:
            setting.description = description
    return setting


def settings_map(db: Session) -> dict[str, Setting]:
    return {entry.key: entry for entry in db.scalars(select(Setting)).all()}
