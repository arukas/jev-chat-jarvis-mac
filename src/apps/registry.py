"""按前台 App 分发到适配器，并支持通过 JEV_ENABLED_APPS 全局开关启用的 IM 软件。

frontmost_app() 三种返回值必须区分：某个已启用的适配器（在聊天 App 里）、
None（在别的 App 里或该聊天 App 未启用，是一次真正的「离开」）、
UNKNOWN（NSWorkspace 查询失败，不是离开的证据——HUD 对它冻结一个短 tick，
与原来 frontmost_app_is_wechat() 返回 None 的处理一致）。
"""
from __future__ import annotations

import userconfig
from apps.ax_app import AXApp
from apps.capture_app import CaptureApp

ENABLED_APPS_VAR = "JEV_ENABLED_APPS"
UNKNOWN = object()          # 前台查询失败的哨兵；`is` 比较
APPS = (CaptureApp(), AXApp())
ALL_APP_KEYS = tuple(app.key for app in APPS)


def app_label(app) -> str:
    """面向用户的软件名（优先取适配器主名称，回退 display_name / key）。"""
    names = getattr(app, "app_names", ())
    return names[0] if names else (getattr(app, "display_name", "") or app.key)


def parse_enabled_apps(raw: str | None) -> tuple[str, ...]:
    """解析 JEV_ENABLED_APPS，返回按注册顺序排列的启用 key 元组。

    - 未配置 / 空串 / "all"：启用全部已注册 IM 软件（默认向后兼容）；
    - "none"：显式全部停用；
    - 逗号分隔 key 列表（如 "wechat"、"qq"、"wechat,qq"）：仅启用列出的已知软件。
    """
    text = (raw or "").strip()
    if not text:
        return ALL_APP_KEYS
    tokens = [part.strip().lower() for part in text.split(",") if part.strip()]
    if not tokens or tokens == ["all"]:
        return ALL_APP_KEYS
    if tokens == ["none"]:
        return ()
    wanted = set(tokens)
    return tuple(key for key in ALL_APP_KEYS if key in wanted)


def format_enabled_apps(keys) -> str:
    """规范化启用 key 集合为 env 写入值；空集合写作 'none' 以区别于未配置。"""
    wanted = {str(k).strip().lower() for k in keys if str(k).strip()}
    ordered = [key for key in ALL_APP_KEYS if key in wanted]
    return ",".join(ordered) if ordered else "none"


def validate_enabled_apps(raw: str) -> str:
    """保存前严格校验 JEV_ENABLED_APPS，返回规范化写入值；非法值抛 ValueError。"""
    text = (raw or "").strip()
    if not text:
        return ""
    tokens = [part.strip().lower() for part in text.split(",") if part.strip()]
    if not tokens:
        return ""
    if tokens == ["all"]:
        return ",".join(ALL_APP_KEYS)
    if tokens == ["none"]:
        return "none"
    known = set(ALL_APP_KEYS)
    invalid = [t for t in tokens if t not in known]
    if invalid:
        raise ValueError(
            f"不支持的启用软件配置：{', '.join(invalid)}（可选：{', '.join(ALL_APP_KEYS)} 或 none）"
        )
    return format_enabled_apps(tokens)


def enabled_app_keys() -> tuple[str, ...]:
    return parse_enabled_apps(userconfig.get(ENABLED_APPS_VAR))


def enabled_apps() -> tuple:
    keys = set(enabled_app_keys())
    return tuple(app for app in APPS if app.key in keys)


def is_app_enabled(key: str) -> bool:
    return key in enabled_app_keys()


def _frontmost():
    """(bundle_id, localized_name) of the frontmost app, or None when the query fails."""
    try:
        import AppKit
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return (app.bundleIdentifier() or "", app.localizedName() or "")
    except Exception:
        return None


def match(bundle: str, name: str, apps=None):
    """先按 bundle id、再按显示名精确匹配——名字相近的兄弟应用与未启用软件不算。"""
    pool = enabled_apps() if apps is None else apps
    for app in pool:
        if bundle and bundle in app.bundle_ids:
            return app
    for app in pool:
        if name in app.app_names:
            return app
    return None


def frontmost_app():
    ident = _frontmost()
    if ident is None:
        return UNKNOWN
    return match(*ident)


def app_by_key(key: str):
    for app in APPS:
        if app.key == key:
            return app
    return None
