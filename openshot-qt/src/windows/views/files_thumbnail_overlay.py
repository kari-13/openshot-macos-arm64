"""
 @file
 @brief Dynamic media-type overlay painter for project file thumbnails.
"""

import os

from qt_api import QRectF
from qt_api import QPainter
from qt_api import QSvgRenderer

from classes import info


_VIDEO_OVERLAY_ICON = "tool-media-play.svg"
_OPTIMIZE_PREVIEW_READY_ICON = "tool-optimize-preview.svg"
_OPTIMIZE_PREVIEW_MISSING_ICON = "tool-optimize-preview-missing.svg"


def _overlay_icon_path(media_type):
    if str(media_type or "").strip().lower() != "video":
        return ""
    return os.path.join(info.PATH, "themes", "cosmic", "images", _VIDEO_OVERLAY_ICON)


def paint_media_overlay(painter, deco_rect, media_type):
    """Paint a centered translucent play glyph for video thumbnails."""
    if not deco_rect or not deco_rect.isValid():
        return

    icon_path = _overlay_icon_path(media_type)
    if not icon_path or not os.path.exists(icon_path):
        return

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setOpacity(0.7)

    glyph_size = max(16.0, min(deco_rect.width(), deco_rect.height()) * 0.36)
    glyph_rect = QRectF(
        deco_rect.center().x() - (glyph_size / 2.0),
        deco_rect.center().y() - (glyph_size / 2.0),
        glyph_size,
        glyph_size,
    )
    renderer = QSvgRenderer(icon_path)
    renderer.render(painter, glyph_rect)

    painter.restore()


def paint_proxy_badge(painter, deco_rect, proxy_state):
    """Paint a bottom-right lightning badge for proxy-ready/missing files."""
    proxy_state = str(proxy_state or "").strip().lower()
    if proxy_state not in ("ready", "missing"):
        return
    if not deco_rect or not deco_rect.isValid():
        return

    icon_name = _OPTIMIZE_PREVIEW_MISSING_ICON if proxy_state == "missing" else _OPTIMIZE_PREVIEW_READY_ICON
    icon_path = os.path.join(info.PATH, "themes", "cosmic", "images", icon_name)
    if not os.path.exists(icon_path):
        return

    badge_size = max(14.0, min(deco_rect.width(), deco_rect.height()) * 0.24)
    margin_x = 1.5
    margin_y = 4.0
    glyph_rect = QRectF(
        deco_rect.right() - badge_size - margin_x,
        deco_rect.bottom() - badge_size - margin_y,
        badge_size,
        badge_size,
    )

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setOpacity(0.95)
    renderer = QSvgRenderer(icon_path)
    renderer.render(painter, glyph_rect)
    painter.restore()
