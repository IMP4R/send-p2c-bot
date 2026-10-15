import json
import time
import os
import logging
from config import COOKIES_DIR

logger = logging.getLogger(__name__)


def save_cookies_to_file(account_id: int, cookies: list):
    """Сохранить куки в файл."""
    path = os.path.join(COOKIES_DIR, f"account_{account_id}.json")
    with open(path, "w") as f:
        json.dump(cookies, f, indent=2)
    logger.info(f"💾 Куки сохранены: {path}")


def load_cookies_from_file(account_id: int) -> list | None:
    """Загрузить куки из файла."""
    path = os.path.join(COOKIES_DIR, f"account_{account_id}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


async def refresh_cookies_playwright(phone: str, headless: bool = False) -> list | None:
    """
    Переавторизация через Playwright.
    
    Args:
        phone: Номер телефона с +
        headless: False = откроет окно браузера (для отладки)
                  True = без окна (для сервера)
    
    Returns:
        Список кук или None
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error(
            "❌ Playwright не установлен!\n"
            "Выполни:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        raise RuntimeError(
            "Playwright не установлен. "
            "Выполни: pip install playwright && playwright install chromium"
        )

    cookies = None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            logger.info(f"🌐 Браузер запущен (headless={headless})")
        except Exception as e:
            logger.error(f"❌ Не удалось запустить браузер: {e}")
            raise RuntimeError(
                f"Не удалось запустить Chromium: {e}\n"
                f"Выполни: playwright install chromium"
            )

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.1 Safari/537.36"
            ),
        )

        page = await context.new_page()

        try:
            # ШАГ 1: Загружаем страницу
            logger.info("🔄 Загружаем app.send.tg...")
            await page.goto("https://app.send.tg/", wait_until="networkidle")
            logger.info("✅ Страница загружена")

            # ШАГ 2: Кликаем кнопку входа
            logger.info("🔄 Ищем кнопку входа...")
            auth_button = page.locator('[data-test-id="auth-button"]')
            await auth_button.wait_for(state="visible", timeout=15000)
            logger.info("✅ Кнопка найдена, кликаем...")
            await auth_button.click()
            await page.wait_for_timeout(3000)

            # Определяем страницу с формой (может быть popup)
            login_page = page
            if len(context.pages) > 1:
                login_page = context.pages[-1]
                logger.info(f"📑 Popup открыт: {login_page.url}")
            else:
                logger.info(f"📍 Форма на: {page.url}")

            # ШАГ 3: Ждём форму
            logger.info("🔄 Ждём форму ввода номера...")
            code_input = login_page.locator("#login-phone-code")
            await code_input.wait_for(state="visible", timeout=15000)
            logger.info("✅ Форма готова")

            # Парсим номер
            clean = phone.lstrip("+")
            if clean.startswith("7") and len(clean) == 11:
                c_code, p_number = "7", clean[1:]
            elif clean.startswith("1") and len(clean) == 11:
                c_code, p_number = "1", clean[1:]
            elif clean.startswith("380"):
                c_code, p_number = "380", clean[3:]
            elif clean.startswith("375"):
                c_code, p_number = "375", clean[3:]
            elif clean.startswith("49"):
                c_code, p_number = "49", clean[2:]
            elif clean.startswith("44"):
                c_code, p_number = "44", clean[2:]
            else:
                c_code, p_number = clean[0], clean[1:]

            logger.info(f"📱 Код: +{c_code}, Номер: {p_number}")

            # Вводим код страны
            await login_page.evaluate(f'''
                const codeField = document.getElementById("login-phone-code");
                const ns = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, "value"
                ).set;
                ns.call(codeField, "+{c_code}");
                codeField.dispatchEvent(new Event("input", {{bubbles: true}}));
                codeField.dispatchEvent(new Event("change", {{bubbles: true}}));
                codeField.dispatchEvent(new KeyboardEvent("keyup", {{bubbles: true}}));
            ''')
            await login_page.wait_for_timeout(300)

            # Вводим номер
            await login_page.evaluate(f'''
                const phoneField = document.getElementById("login-phone");
                const ns = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, "value"
                ).set;
                ns.call(phoneField, "{p_number}");
                phoneField.dispatchEvent(new Event("input", {{bubbles: true}}));
                phoneField.dispatchEvent(new Event("change", {{bubbles: true}}));
                phoneField.dispatchEvent(new KeyboardEvent("keyup", {{bubbles: true}}));
            ''')
            await login_page.wait_for_timeout(500)
            logger.info(f"✅ Номер +{c_code}{p_number} введён")

            # ШАГ 4: Нажимаем "Далее"
            logger.info("🔄 Нажимаем 'Далее'...")
            await login_page.locator('button[type="submit"]').first.click()
            logger.info("✅ Нажато! Ждём подтверждения в Telegram...")

            # ШАГ 5: Ждём подтверждения (до 120 секунд)
            authorized = False
            for i in range(120):
                await login_page.wait_for_timeout(1000)

                # Проверяем кнопку "Принять" / "Accept"
                for btn_text in ["Принять", "Accept", "Confirm", "Log In"]:
                    accept_btn = login_page.locator(f'button:has-text("{btn_text}")')
                    try:
                        if await accept_btn.count() > 0 and await accept_btn.first.is_visible():
                            logger.info(f"🔄 Найдена кнопка '{btn_text}', нажимаем...")
                            await accept_btn.first.click()
                            await login_page.wait_for_timeout(3000)
                            authorized = True
                            break
                    except Exception:
                        pass

                if authorized:
                    break

                # Проверяем переход на основную страницу
                main_url = context.pages[0].url if len(context.pages) > 0 else ""
                if "send.tg" in main_url and "oauth" not in main_url and "telegram" not in main_url:
                    await page.wait_for_timeout(2000)
                    authorized = True
                    logger.info(f"✅ Авторизация через редирект: {main_url}")
                    break

                if i % 10 == 0 and i > 0:
                    logger.info(f"   ⏳ Ждём подтверждения... ({i} сек)")

            if not authorized:
                logger.warning("⚠️ Таймаут авторизации (120 сек)")
                await browser.close()
                return None

            logger.info("✅ Авторизация успешна!")

            # ШАГ 6: Собираем куки
            await page.wait_for_timeout(3000)  # Ждём установки кук
            cookies = await context.cookies()
            logger.info(f"🍪 Получено {len(cookies)} кук")

            for c in cookies:
                logger.info(f"   🍪 {c['name']} = {c['value'][:40]}...")

            # Проверяем наличие access_token
            has_token = any(c["name"] == "access_token" for c in cookies)
            if not has_token:
                logger.warning("⚠️ access_token не найден в куках!")

        except Exception as e:
            logger.error(f"❌ Ошибка авторизации: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()
            logger.info("🔒 Браузер закрыт")

    return cookies