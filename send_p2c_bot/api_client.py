import requests
import json
import time
from config import SEND_TG_API_URL, SEND_TG_BASE_URL, USER_AGENT
from database import get_account, update_account_balance


class SendTGClient:
    def __init__(self, account_id: int):
        self.account_id = account_id
        self.session = requests.Session()
        self._load_cookies()

    def _load_cookies(self):
        account = get_account(self.account_id)
        if not account:
            raise ValueError(f"Аккаунт {self.account_id} не найден")

        cookies_data = json.loads(account["cookies"])

        if isinstance(cookies_data, list):
            for c in cookies_data:
                self.session.cookies.set(
                    c["name"],
                    c["value"],
                    domain=c.get("domain", "app.send.tg"),
                    path=c.get("path", "/"),
                )
        elif isinstance(cookies_data, dict):
            for name, value in cookies_data.items():
                self.session.cookies.set(name, value)

        self.session.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "referer": f"{SEND_TG_BASE_URL}/",
            "user-agent": USER_AGENT,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        })

    def check_auth(self) -> bool:
        try:
            resp = self.session.get(
                f"{SEND_TG_API_URL}/wallet/balance",
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_balance(self) -> dict | None:
        try:
            resp = self.session.get(
                f"{SEND_TG_API_URL}/wallet/balance",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                update_account_balance(self.account_id, data)
                return data
            elif resp.status_code == 401:
                return {"error": "unauthorized", "message": "Куки истекли"}
            else:
                return {"error": "api_error", "status": resp.status_code}
        except Exception as e:
            return {"error": "connection", "message": str(e)}

    def get_formatted_balance(self) -> str:
        data = self.get_balance()
        if not data:
            return "Не удалось получить баланс"

        if "error" in data:
            if data["error"] == "unauthorized":
                return "Куки истекли — нужна переавторизация"
            return f"Ошибка: {data.get('message', 'Неизвестная')}"

        lines = [f"*Баланс:* ${data.get('total', '0')}\n"]

        for b in data.get("balances", []):
            available = float(b.get("available", "0"))
            if available > 0:
                lines.append(
                    f"  *{b['asset']}:* {b['available']} (${b['available_in_usd']})"
                )

        if len(lines) == 1:
            lines.append("  Нет активов с балансом")

        return "\n".join(lines)