from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting


_MISSING = object()


def setting_value(db: Session, key: str, default: object = _MISSING) -> object:
    setting = db.get(Setting, key)
    if setting is not None:
        return setting.value
    if default is not _MISSING:
        return default
    raise ValueError(f"Required setting '{key}' is missing; rerun the seed import or configure it in Settings.")


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
