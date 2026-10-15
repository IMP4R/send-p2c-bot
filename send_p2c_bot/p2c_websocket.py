import asyncio
import json
import logging
import time
import ssl
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from config import SEND_TG_API_URL, USER_AGENT
from database import get_account

logger = logging.getLogger(__name__)

# Один SSL-контекст на весь процесс
_SSL_CTX = ssl.create_default_context()


@dataclass
class P2COrder:
    id: str
    payload: str
    url: str
    brand_name: str
    in_asset: str
    out_asset: str
    in_amount: float
    out_amount: str
    exchange_rate: float
    fee_amount: str
    mcc: str
    mcc_info: list
    expires_at: str
    boost: int = 0
    provider: str = "nspk"

    @classmethod
    def from_dict(cls, data: dict) -> "P2COrder":
        return cls(
            id=data.get("id", ""),
            payload=data.get("payload", ""),
            url=data.get("url", ""),
            brand_name=data.get("brand_name", ""),
            in_asset=data.get("in_asset", "RUB"),
            out_asset=data.get("out_asset", "USDT"),
            in_amount=float(data.get("in_amount", 0)),
            out_amount=data.get("out_amount", "0"),
            exchange_rate=float(data.get("exchange_rate", 0)),
            fee_amount=data.get("fee_amount", "0"),
            mcc=data.get("mcc", ""),
            mcc_info=data.get("mcc_info", []),
            expires_at=data.get("expires_at", ""),
            boost=data.get("boost", 0),
            provider=data.get("provider", "nspk"),
        )

    @property
    def mcc_name_ru(self) -> str:
        for info in self.mcc_info:
            if info.get("locale") == "ru":
                return info.get("name", "")
        return ""

    @property
    def mcc_category_icon(self) -> str:
        if self.mcc_info:
            return self.mcc_info[0].get("category_icon", "")
        return ""


@dataclass
class TakenOrder:
    order_id: str
    payment_id: int
    payload: str
    url: str
    brand_name: str
    in_amount: float
    in_asset: str
    out_amount: str
    out_asset: str
    exchange_rate: float
    reward_amount: str
    boost_amount: str
    status: str
    taken_at: float
    take_ping_ms: float
    message_id: Optional[int] = None


class P2CSession:
    def __init__(self):
        self.bot = None
        self.active_sessions: dict[str, asyncio.Task] = {}
        self.session_configs: dict[str, dict] = {}
        self.order_lists: dict[str, list[P2COrder]] = {}
        self.taken_orders: dict[str, dict[int, TakenOrder]] = {}
        self.session_stats: dict[str, dict] = {}
        self._payment_methods: dict[int, list[dict]] = {}

        # Persistent HTTP sessions per account (connection pooling)
        self._http_sessions: dict[int, aiohttp.ClientSession] = {}

    def set_bot(self, bot):
        self.bot = bot

    async def _get_http_session(self, account_id: int) -> aiohttp.ClientSession:
        """Переиспользуемая HTTP-сессия с keep-alive."""
        if account_id in self._http_sessions:
            session = self._http_sessions[account_id]
            if not session.closed:
                return session

        account = get_account(account_id)
        if not account:
            raise ValueError(f"Account {account_id} not found")

        cookies_str = self._build_cookie_string(account)

        # TCPConnector с keep-alive и пулом соединений
        connector = aiohttp.TCPConnector(
            ssl=_SSL_CTX,
            limit=10,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        )

        session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "ru-RU,ru;q=0.9",
                "origin": "https://app.send.tg",
                "user-agent": USER_AGENT,
                "cookie": cookies_str,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            },
            timeout=aiohttp.ClientTimeout(total=5, connect=2),
        )

        self._http_sessions[account_id] = session
        return session

    async def _close_http_session(self, account_id: int):
        if account_id in self._http_sessions:
            session = self._http_sessions.pop(account_id)
            if not session.closed:
                await session.close()

    def get_active_taken_count(self, session_key: str) -> int:
        orders = self.taken_orders.get(session_key, {})
        return sum(1 for o in orders.values() if o.status == "processing")

    def get_taken_order(self, session_key: str, payment_id: int) -> Optional[TakenOrder]:
        return self.taken_orders.get(session_key, {}).get(payment_id)

    def find_session_by_payment(self, payment_id: int) -> Optional[str]:
        for sk, orders in self.taken_orders.items():
            if payment_id in orders:
                return sk
        return None

    # ==========================================
    # Payment Methods
    # ==========================================

    async def fetch_payment_methods(self, account_id: int) -> list[dict]:
        account = get_account(account_id)
        if not account:
            return []

        try:
            http = await self._get_http_session(account_id)

            async with http.get(
                f"{SEND_TG_API_URL}/p2c/accounts",
                headers={"referer": "https://app.send.tg/p2c/payments"},
            ) as resp:
                if resp.status == 200:
                    raw = await resp.json()

                    if isinstance(raw, dict) and "data" in raw:
                        methods = raw["data"]
                    elif isinstance(raw, list):
                        methods = raw
                    else:
                        methods = [raw]

                    active_methods = [
                        m for m in methods
                        if m.get("status") == "active"
                    ]

                    self._payment_methods[account_id] = active_methods

                    logger.info(
                        f"P2C accounts for #{account_id}: "
                        f"{len(active_methods)} active / {len(methods)} total"
                    )
                    return active_methods
                else:
                    body = await resp.text()
                    logger.warning(
                        f"GET /p2c/accounts failed: "
                        f"{resp.status} | {body[:300]}"
                    )
                    return []
        except Exception as e:
            logger.error(f"fetch_payment_methods error: {e}")
            return []

    def get_default_method_id(self, account_id: int) -> Optional[str]:
        methods = self._payment_methods.get(account_id, [])
        if methods:
            return methods[0].get("id")
        return None

    # ==========================================
    # Session lifecycle
    # ==========================================

    async def start_session(
        self,
        session_key: str,
        account_id: int,
        chat_id: int,
        min_amount: float = 0,
        max_amount: float = float("inf"),
        max_concurrent: int = 1,
    ):
        if session_key in self.active_sessions:
            self.active_sessions[session_key].cancel()
            await asyncio.sleep(0.1)

        self.session_configs[session_key] = {
            "account_id": account_id,
            "chat_id": chat_id,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "max_concurrent": max_concurrent,
        }
        self.order_lists[session_key] = []
        self.taken_orders[session_key] = {}
        self.session_stats[session_key] = {
            "started_at": time.time(),
            "orders_seen": 0,
            "orders_taken": 0,
            "orders_completed": 0,
            "orders_cancelled": 0,
            "orders_skipped_filter": 0,
            "orders_skipped_limit": 0,
            "orders_take_failed": 0,
            "total_rub": 0,
            "total_usdt": 0,
        }

        # Прогрев HTTP-сессии (TCP + TLS handshake заранее)
        await self._get_http_session(account_id)

        methods = await self.fetch_payment_methods(account_id)
        method_id = self.get_default_method_id(account_id)

        if self.bot:
            if not method_id:
                await self.bot.send_message(
                    chat_id,
                    "Не найден привязанный банковский счёт.\n"
                    "Привяжите его в app.send.tg — иначе подтверждение оплаты не сработает.",
                    parse_mode="Markdown",
                )

        task = asyncio.create_task(self._session_loop(session_key))
        self.active_sessions[session_key] = task

    async def stop_session(self, session_key: str):
        if session_key in self.active_sessions:
            self.active_sessions[session_key].cancel()
            del self.active_sessions[session_key]

        # Закрываем HTTP-сессию
        config = self.session_configs.get(session_key, {})
        account_id = config.get("account_id")
        if account_id:
            # Не закрываем если есть другие активные сессии на этот аккаунт
            other_active = any(
                k != session_key
                and self.session_configs.get(k, {}).get("account_id") == account_id
                and self.is_running(k)
                for k in self.active_sessions
            )
            if not other_active:
                await self._close_http_session(account_id)

        logger.info(f"Session stopped: {session_key}")

    def is_running(self, session_key: str) -> bool:
        if session_key not in self.active_sessions:
            return False
        return not self.active_sessions[session_key].done()

    # ==========================================
    # API calls
    # ==========================================

    async def take_order(self, session_key: str, order: P2COrder) -> Optional[TakenOrder]:
        config = self.session_configs.get(session_key)
        if not config:
            return None

        account_id = config["account_id"]
        url = f"{SEND_TG_API_URL}/p2c/payments/take/{order.id}"

        take_start = time.time()

        try:
            http = await self._get_http_session(account_id)

            async with http.post(
                url,
                headers={
                    "referer": "https://app.send.tg/p2c/orders",
                    "content-length": "0",
                },
            ) as resp:
                take_ping = (time.time() - take_start) * 1000

                if resp.status == 200:
                    data = await resp.json()
                    pd = data.get("data", {})

                    taken = TakenOrder(
                        order_id=order.id,
                        payment_id=pd.get("id", 0),
                        payload=pd.get("payload", order.payload),
                        url=pd.get("url", order.url),
                        brand_name=pd.get("brand_name", order.brand_name),
                        in_amount=float(pd.get("in_amount", order.in_amount)),
                        in_asset=pd.get("in_asset", order.in_asset),
                        out_amount=pd.get("out_amount", order.out_amount),
                        out_asset=pd.get("out_asset", order.out_asset),
                        exchange_rate=float(pd.get("exchange_rate", order.exchange_rate)),
                        reward_amount=pd.get("reward_amount", "0"),
                        boost_amount=pd.get("boost_amount", "0"),
                        status=pd.get("status", "processing"),
                        taken_at=time.time(),
                        take_ping_ms=take_ping,
                    )

                    if session_key not in self.taken_orders:
                        self.taken_orders[session_key] = {}
                    self.taken_orders[session_key][taken.payment_id] = taken

                    stats = self.session_stats.get(session_key, {})
                    stats["orders_taken"] = stats.get("orders_taken", 0) + 1

                    logger.info(
                        f"TAKE OK: {order.brand_name} | "
                        f"{order.in_amount} RUB | "
                        f"payment_id={taken.payment_id} | "
                        f"ping={take_ping:.0f}ms"
                    )
                    return taken
                else:
                    body = await resp.text()
                    logger.warning(
                        f"TAKE FAIL: {order.brand_name} | "
                        f"{order.in_amount} RUB | "
                        f"status={resp.status} | {body[:200]} | "
                        f"ping={take_ping:.0f}ms"
                    )
                    stats = self.session_stats.get(session_key, {})
                    stats["orders_take_failed"] = stats.get("orders_take_failed", 0) + 1
                    return None

        except Exception as e:
            take_ping = (time.time() - take_start) * 1000
            logger.error(
                f"TAKE ERROR: {order.brand_name} | "
                f"{order.in_amount} RUB | "
                f"{e} | ping={take_ping:.0f}ms"
            )
            stats = self.session_stats.get(session_key, {})
            stats["orders_take_failed"] = stats.get("orders_take_failed", 0) + 1
            return None

    async def cancel_order(self, session_key: str, payment_id: int) -> bool:
        config = self.session_configs.get(session_key)
        if not config:
            return False

        account_id = config["account_id"]

        try:
            http = await self._get_http_session(account_id)

            async with http.post(
                f"{SEND_TG_API_URL}/p2c/payments/{payment_id}/cancel",
                headers={
                    "content-type": "application/json",
                    "referer": f"https://app.send.tg/p2c/orders/{payment_id}?back=orders",
                },
                json={"reason": "bank"},
            ) as resp:
                if resp.status == 200:
                    taken = self.taken_orders.get(session_key, {}).get(payment_id)
                    if taken:
                        taken.status = "cancelled"
                    stats = self.session_stats.get(session_key, {})
                    stats["orders_cancelled"] = stats.get("orders_cancelled", 0) + 1
                    logger.info(f"CANCEL OK: payment_id={payment_id}")
                    return True
                else:
                    body = await resp.text()
                    logger.warning(f"CANCEL FAIL: {resp.status} | {body[:200]}")
                    return False
        except Exception as e:
            logger.error(f"CANCEL ERROR: {e}")
            return False

    async def complete_order(self, session_key: str, payment_id: int) -> bool:
        config = self.session_configs.get(session_key)
        if not config:
            return False

        account_id = config["account_id"]

        method_id = self.get_default_method_id(account_id)

        if not method_id:
            await self.fetch_payment_methods(account_id)
            method_id = self.get_default_method_id(account_id)

        if not method_id:
            logger.error(f"COMPLETE FAIL: no method_id for account #{account_id}")
            return False

        try:
            http = await self._get_http_session(account_id)

            async with http.post(
                f"{SEND_TG_API_URL}/p2c/payments/{payment_id}/complete",
                headers={
                    "content-type": "application/json",
                    "referer": f"https://app.send.tg/p2c/orders/{payment_id}?back=payments",
                },
                json={"method": method_id},
            ) as resp:
                if resp.status == 200:
                    taken = self.taken_orders.get(session_key, {}).get(payment_id)
                    if taken:
                        taken.status = "completed"
                        stats = self.session_stats.get(session_key, {})
                        stats["orders_completed"] = stats.get("orders_completed", 0) + 1
                        stats["total_rub"] = stats.get("total_rub", 0) + taken.in_amount
                        try:
                            usdt = int(taken.out_amount) / 1e18
                            stats["total_usdt"] = stats.get("total_usdt", 0) + usdt
                        except (ValueError, TypeError):
                            pass
                    logger.info(f"COMPLETE OK: payment_id={payment_id}")
                    return True
                else:
                    body = await resp.text()
                    logger.warning(f"COMPLETE FAIL: {resp.status} | {body[:300]}")
                    return False
        except Exception as e:
            logger.error(f"COMPLETE ERROR: {e}")
            return False

    # ==========================================
    # Helpers
    # ==========================================

    def _build_cookie_string(self, account: dict) -> str:
        cookies_data = json.loads(account["cookies"])
        if isinstance(cookies_data, list):
            return "; ".join(f"{c['name']}={c['value']}" for c in cookies_data)
        elif isinstance(cookies_data, dict):
            return "; ".join(f"{k}={v}" for k, v in cookies_data.items())
        return ""

    def _matches_filter(self, order: P2COrder, session_key: str) -> bool:
        config = self.session_configs.get(session_key, {})
        min_amt = config.get("min_amount", 0)
        max_amt = config.get("max_amount", float("inf"))
        if order.in_amount < min_amt or order.in_amount > max_amt:
            return False
        return True

    # ==========================================
    # WebSocket loop
    # ==========================================

    async def _session_loop(self, session_key: str):
        config = self.session_configs[session_key]
        account_id = config["account_id"]
        chat_id = config["chat_id"]
        reconnect_delay = 1

        while True:
            try:
                account = get_account(account_id)
                if not account:
                    logger.error(f"Account {account_id} not found")
                    return

                cookies_str = self._build_cookie_string(account)

                ws_url = (
                    "wss://app.send.tg/internal/v1/p2c-socket/"
                    "?EIO=4&transport=websocket"
                )

                headers = {
                    "User-Agent": USER_AGENT,
                    "Origin": "https://app.send.tg",
                    "Cookie": cookies_str,
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                }

                async with aiohttp.ClientSession() as ws_session:
                    async with ws_session.ws_connect(
                        ws_url,
                        headers=headers,
                        ssl=_SSL_CTX,
                        heartbeat=25,
                        timeout=aiohttp.ClientWSTimeout(ws_close=10),
                    ) as ws:
                        logger.info(f"WS connected: {session_key}")
                        reconnect_delay = 1

                        # Handshake
                        hs_msg = await asyncio.wait_for(ws.receive(), timeout=10)
                        hs_text = hs_msg.data

                        if not hs_text.startswith("0"):
                            logger.error(f"Bad handshake: {hs_text[:100]}")
                            continue

                        # Connect
                        await ws.send_str("40")
                        await asyncio.wait_for(ws.receive(), timeout=10)

                        # Initialize
                        await ws.send_str('42["list:initialize"]')

                        if self.bot:
                            cfg = self.session_configs[session_key]
                            min_a = cfg["min_amount"]
                            max_a = cfg["max_amount"]
                            max_c = cfg["max_concurrent"]

                            if max_a == float("inf"):
                                range_text = f"от {min_a:,.0f} ₽"
                            elif min_a == 0:
                                range_text = "все ордера"
                            else:
                                range_text = f"{min_a:,.0f} – {max_a:,.0f} ₽"

                            await self.bot.send_message(
                                chat_id,
                                f"🟢 *Подключено*\n\n"
                                f"Диапазон: {range_text}\n"
                                f"Макс. заказов: {max_c}\n"
                                f"Автоматический take включён",
                                parse_mode="Markdown",
                            )

                        # Main loop
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.data

                                if data == "2":
                                    await ws.send_str("3")
                                    continue

                                if data == "3":
                                    continue

                                if data.startswith("42"):
                                    await self._handle_message(
                                        session_key, data[2:]
                                    )

                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                logger.warning(f"WS closed: {msg.type}")
                                break

            except asyncio.CancelledError:
                logger.info(f"Session cancelled: {session_key}")
                if self.bot:
                    chat_id = self.session_configs.get(
                        session_key, {}
                    ).get("chat_id")
                    if chat_id:
                        try:
                            await self.bot.send_message(
                                chat_id,
                                "*Сессия завершена*",
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass
                return

            except Exception as e:
                logger.error(f"WS Error ({session_key}): {e}")
                if self.bot:
                    chat_id = self.session_configs.get(
                        session_key, {}
                    ).get("chat_id")
                    if chat_id:
                        try:
                            await self.bot.send_message(
                                chat_id,
                                f"Ошибка соединения. Переподключение через {reconnect_delay}с...",
                            )
                        except Exception:
                            pass

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 30)

    async def _handle_message(self, session_key: str, payload: str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return

        if not isinstance(parsed, list) or len(parsed) < 2:
            return

        event = parsed[0]
        data = parsed[1]

        if event == "list:snapshot":
            self.order_lists[session_key] = []
            if isinstance(data, list):
                for item in data:
                    order = P2COrder.from_dict(item)
                    self.order_lists[session_key].append(order)
                    await self._try_auto_take(session_key, order)

        elif event == "list:update":
            if not isinstance(data, list):
                return

            for update in data:
                op = update.get("op")

                if op == "add":
                    order_data = update.get("data", {})
                    order = P2COrder.from_dict(order_data)

                    orders = self.order_lists.get(session_key, [])
                    orders.append(order)
                    self.order_lists[session_key] = orders

                    stats = self.session_stats.get(session_key, {})
                    stats["orders_seen"] = stats.get("orders_seen", 0) + 1

                    await self._try_auto_take(session_key, order)

                elif op == "remove":
                    pos = update.get("pos", 0)
                    orders = self.order_lists.get(session_key, [])
                    if 0 <= pos < len(orders):
                        orders.pop(pos)

    async def _try_auto_take(self, session_key: str, order: P2COrder):
        config = self.session_configs.get(session_key, {})
        stats = self.session_stats.get(session_key, {})

        if not self._matches_filter(order, session_key):
            stats["orders_skipped_filter"] = (
                stats.get("orders_skipped_filter", 0) + 1
            )
            return

        active_count = self.get_active_taken_count(session_key)
        max_concurrent = config.get("max_concurrent", 1)

        if active_count >= max_concurrent:
            stats["orders_skipped_limit"] = (
                stats.get("orders_skipped_limit", 0) + 1
            )
            return

        taken = await self.take_order(session_key, order)

        if taken and self.bot:
            chat_id = config.get("chat_id")
            if chat_id:
                await self._send_taken_notification(
                    session_key, chat_id, taken
                )

    async def _send_taken_notification(
        self, session_key: str, chat_id: int, taken: TakenOrder
    ):
        try:
            out_usdt = int(taken.out_amount) / 1e18
        except (ValueError, TypeError):
            out_usdt = 0

        try:
            reward = int(taken.reward_amount) / 1e18
        except (ValueError, TypeError):
            reward = 0

        active_count = self.get_active_taken_count(session_key)
        max_c = self.session_configs.get(session_key, {}).get(
            "max_concurrent", 1
        )

        text = (
            f"*Ордер взят*\n\n"
            f"*{taken.brand_name}*\n"
            f"{taken.in_amount:,.2f} {taken.in_asset} → "
            f"{out_usdt:,.2f} {taken.out_asset}\n"
            f"Курс: {taken.exchange_rate}\n"
        )

        if reward > 0.0001:
            text += f"Награда: +{reward:,.4f} {taken.out_asset}\n"

        text += (
            f"\nСлоты: {active_count}/{max_c}\n"
            f"[Оплатить по QR]({taken.url})"
        )

        from keyboards import taken_order_kb
        from qr_generator import generate_simple_qr
        from aiogram.types import BufferedInputFile

        qr_image = generate_simple_qr(taken.url)
        photo = BufferedInputFile(
            qr_image.read(),
            filename=f"qr_{taken.payment_id}.png",
        )

        try:
            sent = await self.bot.send_photo(
                chat_id,
                photo=photo,
                caption=text,
                parse_mode="Markdown",
                reply_markup=taken_order_kb(taken.payment_id),
            )
            taken.message_id = sent.message_id
        except Exception as e:
            logger.error(f"Send photo error: {e}")
            try:
                sent = await self.bot.send_message(
                    chat_id,
                    text,
                    parse_mode="Markdown",
                    reply_markup=taken_order_kb(taken.payment_id),
                    disable_web_page_preview=True,
                )
                taken.message_id = sent.message_id
            except Exception as e2:
                logger.error(f"Send text error: {e2}")

    # ==========================================
    # Stats
    # ==========================================

    def get_session_stats_text(self, session_key: str) -> str:
        stats = self.session_stats.get(session_key, {})
        config = self.session_configs.get(session_key, {})

        started = stats.get("started_at", 0)
        uptime = time.time() - started if started else 0
        h = int(uptime // 3600)
        m = int((uptime % 3600) // 60)
        s = int(uptime % 60)

        if h > 0:
            uptime_str = f"{h}ч {m}м"
        elif m > 0:
            uptime_str = f"{m}м {s}с"
        else:
            uptime_str = f"{s}с"

        active_count = self.get_active_taken_count(session_key)
        max_c = config.get("max_concurrent", 1)

        min_a = config.get("min_amount", 0)
        max_a = config.get("max_amount", float("inf"))
        if max_a == float("inf"):
            if min_a == 0:
                range_text = "все ордера"
            else:
                range_text = f"от {min_a:,.0f} ₽"
        else:
            range_text = f"{min_a:,.0f} – {max_a:,.0f} ₽"

        running = self.is_running(session_key)
        status_dot = "🟢" if running else "⚫"

        taken = stats.get("orders_taken", 0)
        completed = stats.get("orders_completed", 0)
        cancelled = stats.get("orders_cancelled", 0)
        failed = stats.get("orders_take_failed", 0)
        seen = stats.get("orders_seen", 0)

        lines = [
            f"*Статус P2C*\n",
            f"{status_dot} {'Работает' if running else 'Остановлена'} · {uptime_str}",
            f"Диапазон: {range_text}",
            f"Слоты: {active_count}/{max_c}",
            f"",
            f"Увидено: {seen}",
            f"Взято: {taken}",
            f"Оплачено: {completed}",
            f"Отменено: {cancelled}",
        ]

        if failed > 0:
            lines.append(f"Не удалось взять: {failed}")

        total_rub = stats.get("total_rub", 0)
        total_usdt = stats.get("total_usdt", 0)

        if total_rub > 0 or total_usdt > 0:
            lines.append("")
            lines.append(f"*Итого:* {total_rub:,.0f} ₽ → {total_usdt:,.2f} USDT")

        return "\n".join(lines)


# Глобальный экземпляр
p2c_session = P2CSession()