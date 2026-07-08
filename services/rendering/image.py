"""Pillow drawing helpers for renderer cards."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Callable

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class RenderTheme:
    card_w: int = 1120
    outer_bg: str = "#f3f4f6"
    card_bg: str = "#ffffff"
    color_primary: str = "#3b82f6"
    color_success: str = "#059669"
    color_warning: str = "#d97706"
    color_danger: str = "#dc2626"
    color_text_main: str = "#111827"
    color_text_sub: str = "#6b7280"
    color_bg_light: str = "#f8fafc"
    color_bg_badge: str = "#eef4ff"


DEFAULT_THEME = RenderTheme()


def status_color(value: float, kind: str = "tps", theme: RenderTheme = DEFAULT_THEME) -> str:
    if kind == "tps":
        if value >= 19:
            return theme.color_success
        if value >= 15:
            return theme.color_warning
        return theme.color_danger
    if kind == "ping":
        if value < 100:
            return theme.color_success
        if value < 200:
            return theme.color_warning
        return theme.color_danger
    if kind == "memory":
        if value < 70:
            return theme.color_success
        if value < 90:
            return theme.color_warning
        return theme.color_danger
    return theme.color_text_main


def draw_progress(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    percent: int,
    color: str,
):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=5, fill="#e5e7eb")
    fill_w = int(w * (percent / 100))
    if fill_w > 0:
        draw.rounded_rectangle((x, y, x + fill_w, y + h), radius=5, fill=color)


def draw_header(
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    sub_title: str,
    font: Callable[[int], object],
    theme: RenderTheme = DEFAULT_THEME,
) -> int:
    draw.rounded_rectangle(
        (22, y, theme.card_w - 22, y + 126),
        radius=16,
        fill=theme.color_bg_badge,
    )
    draw.rectangle((36, y + 18, 47, y + 108), fill=theme.color_primary)
    draw.text(
        (62, y + 18),
        title,
        font=font(44),
        fill=theme.color_text_main,
    )
    draw.text(
        (62, y + 78),
        sub_title,
        font=font(20),
        fill=theme.color_text_sub,
    )
    return y + 144


def draw_section_box(
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    bg_color: str,
    text_color: str,
    height: int,
    font: Callable[[int], object],
    theme: RenderTheme = DEFAULT_THEME,
):
    draw.rounded_rectangle(
        (28, y, theme.card_w - 28, y + height),
        radius=12,
        fill=theme.card_bg,
        outline="#e5e7eb",
    )
    draw.rounded_rectangle(
        (28, y, theme.card_w - 28, y + 44),
        radius=12,
        fill=bg_color,
    )
    draw.rectangle((28, y + 30, theme.card_w - 28, y + 44), fill=bg_color)
    draw.text((44, y + 10), title, font=font(24), fill=text_color)


def new_card(
    estimate_h: int,
    theme: RenderTheme = DEFAULT_THEME,
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (theme.card_w, max(estimate_h, 240)), theme.card_bg)
    return img, ImageDraw.Draw(img)


def save_png(image: Image.Image) -> BytesIO:
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def merge_images_vertical(
    images: list[Image.Image],
    background: str | None = None,
    gap: int = 8,
    pad: int = 10,
    theme: RenderTheme = DEFAULT_THEME,
) -> BytesIO:
    if len(images) == 1:
        return save_png(images[0])

    bg = background or theme.outer_bg
    max_width = max(im.width for im in images)
    total_h = sum(im.height for im in images) + gap * (len(images) - 1) + pad * 2
    merged = Image.new("RGB", (max_width + pad * 2, total_h), bg)
    y = pad
    for im in images:
        x = (merged.width - im.width) // 2
        merged.paste(im, (x, y))
        y += im.height + gap
    return save_png(merged)
