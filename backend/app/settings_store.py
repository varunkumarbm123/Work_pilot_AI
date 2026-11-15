from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting, WorkspaceSetting


_MISSING = object()


def setting_value(
    db: Session,
    key: str,
    default: object = _MISSING,
    workspace_id: str | None = None,
) -> object:
    if workspace_id is not None:
        override = db.get(WorkspaceSetting, (workspace_id, key))
        if override is not None:
            return override.value
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


def settings_map(
    db: Session, workspace_id: str | None = None
) -> dict[str, Setting | WorkspaceSetting]:
    result: dict[str, Setting | WorkspaceSetting] = {
        entry.key: entry for entry in db.scalars(select(Setting)).all()
    }
    if workspace_id is not None:
        result.update(
            {
                entry.key: entry
                for entry in db.scalars(
                    select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == workspace_id)
                ).all()
            }
        )
    return result


def upsert_workspace_setting(
    db: Session,
    workspace_id: str,
    key: str,
    value: object,
    description: str = "",
) -> WorkspaceSetting:
    setting = db.get(WorkspaceSetting, (workspace_id, key))
    if setting is None:
        setting = WorkspaceSetting(
            workspace_id=workspace_id,
            key=key,
            value=value,
            description=description,
        )
        db.add(setting)
    else:
        setting.value = value
        if description:
            setting.description = description
    return setting
