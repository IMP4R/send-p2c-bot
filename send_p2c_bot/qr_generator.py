import qrcode
from qrcode.image.styledpil import StyledPilImage
from PIL import Image, ImageDraw, ImageFont
import os
import time
from io import BytesIO
from urllib.parse import urlparse, parse_qs
from config import QR_DIR


def generate_simple_qr(qr_link: str) -> BytesIO:
    """Простой QR без оформления."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output


def generate_beautiful_qr(
    qr_link: str,
    sum_amount: int = None,
    bank_name: str = None,
    session_id: int = None,
) -> BytesIO:
    """
    Генерация красивого QR-кода для оплаты.
    Возвращает BytesIO с PNG-изображением.
    """

    parsed = urlparse(qr_link)
    params = parse_qs(parsed.query)

    if not sum_amount and "sum" in params:
        sum_amount = int(params["sum"][0])
    if not bank_name and "bank" in params:
        bank_name = params["bank"][0]

    sum_rub = sum_amount / 100 if sum_amount else 0

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_link)
    qr.make(fit=True)

    qr_img = qr.make_image(
        fill_color="#1a1a2e",
        back_color="white",
    ).convert("RGBA")

    qr_size = qr_img.size[0]

    card_width = max(qr_size + 80, 500)
    card_height = qr_size + 280
    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    bg_color_top = (15, 15, 35)
    bg_color_bottom = (25, 25, 60)

    for y in range(card_height):
        ratio = y / card_height
        r = int(bg_color_top[0] + (bg_color_bottom[0] - bg_color_top[0]) * ratio)
        g = int(bg_color_top[1] + (bg_color_bottom[1] - bg_color_top[1]) * ratio)
        b = int(bg_color_top[2] + (bg_color_bottom[2] - bg_color_top[2]) * ratio)
        draw.line([(0, y), (card_width, y)], fill=(r, g, b, 255))

    qr_container_left = (card_width - qr_size - 40) // 2
    qr_container_right = qr_container_left + qr_size + 40
    qr_container_top = 100
    qr_container_bottom = qr_container_top + qr_size + 40

    draw.rounded_rectangle(
        [qr_container_left, qr_container_top,
         qr_container_right, qr_container_bottom],
        radius=20,
        fill=(255, 255, 255, 245),
    )

    qr_x = qr_container_left + 20
    qr_y = qr_container_top + 20
    card.paste(qr_img, (qr_x, qr_y), qr_img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_amount = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_info = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except OSError:
        try:
            font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
            font_amount = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
            font_info = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
            font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except OSError:
            font_title = ImageFont.load_default()
            font_amount = font_title
            font_info = font_title
            font_small = font_title

    title = "⚡ Оплата по QR"
    bbox = draw.textbbox((0, 0), title, font=font_title)
    title_w = bbox[2] - bbox[0]
    draw.text(
        ((card_width - title_w) // 2, 40),
        title,
        fill=(255, 255, 255, 255),
        font=font_title,
    )

    info_y = qr_container_bottom + 25

    if sum_rub > 0:
        amount_text = f"💳 {sum_rub:,.2f} ₽"
        bbox = draw.textbbox((0, 0), amount_text, font=font_amount)
        amount_w = bbox[2] - bbox[0]
        draw.text(
            ((card_width - amount_w) // 2, info_y),
            amount_text,
            fill=(0, 255, 136, 255),
            font=font_amount,
        )
        info_y += 50

    if bank_name:
        bank_text = f"🏦 Банк: {bank_name}"
        bbox = draw.textbbox((0, 0), bank_text, font=font_info)
        bank_w = bbox[2] - bbox[0]
        draw.text(
            ((card_width - bank_w) // 2, info_y),
            bank_text,
            fill=(180, 180, 220, 255),
            font=font_info,
        )
        info_y += 30

    meta_parts = []
    if session_id:
        meta_parts.append(f"Сессия #{session_id}")
    meta_parts.append(time.strftime("%H:%M:%S"))

    meta_text = " • ".join(meta_parts)
    bbox = draw.textbbox((0, 0), meta_text, font=font_small)
    meta_w = bbox[2] - bbox[0]
    draw.text(
        ((card_width - meta_w) // 2, info_y + 10),
        meta_text,
        fill=(120, 120, 160, 255),
        font=font_small,
    )

    draw.rectangle(
        [0, 0, card_width, 4],
        fill=(0, 255, 136, 255),
    )

    output = BytesIO()
    card = card.convert("RGB")
    card.save(output, format="PNG", quality=95)
    output.seek(0)

    if session_id:
        filepath = os.path.join(QR_DIR, f"qr_session_{session_id}.png")
        card.save(filepath, format="PNG", quality=95)

    return output