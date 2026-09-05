import hashlib
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class NepViewerError(RuntimeError):
    pass


class NepViewerClient:
    API_ROOT = "https://api.nepviewer.net/"

    def __init__(self, account: str, password: str, timeout: int = 30):
        self.account = account.strip()
        self.password = password
        self.timeout = timeout
        self.token = ""
        self.company_id = 0

    @staticmethod
    def _body_and_sign(body_object):
        if body_object is None:
            body = b""
        else:
            body = json.dumps(
                body_object,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        normalized = (
            body.decode("utf-8")
            .replace(" ", "")
            .replace("\r", "")
            .replace("\n", "")
            .replace("e", "NEP")
            .encode("utf-8")
        )
        return body, hashlib.md5(normalized).hexdigest().upper()

    def _post(self, path: str, body_object=None):
        body, sign = self._body_and_sign(body_object)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "app": str(self.company_id),
            "client": "web",
            "lan": "6",
            "oem": "NEP",
            "sign": sign,
        }
        if self.token:
            headers["Authorization"] = self.token
        request = Request(
            self.API_ROOT + path,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
            except Exception:
                payload = None
            return error.code, payload

    def login(self):
        self.token = ""
        self.company_id = 0
        status, payload = self._post(
            "v2/sign-in",
            {"account": self.account, "password": self.password},
        )
        if status != 200 or not payload or payload.get("code") != 200:
            code = payload.get("code") if payload else None
            raise NepViewerError(f"login failed (http={status}, api={code})")
        data = payload["data"]
        self.token = data["tokenInfo"]["token"]
        self.company_id = data.get("userInfo", {}).get("companyId", 0)

    def current_power(self):
        if not self.token:
            self.login()
        status, payload = self._post("v2/overview/overview")
        api_code = payload.get("code") if payload else None
        if status == 401 or api_code == 401:
            self.login()
            status, payload = self._post("v2/overview/overview")
            api_code = payload.get("code") if payload else None
        if status != 200 or not payload or api_code != 200:
            raise NepViewerError(f"overview failed (http={status}, api={api_code})")
        production = payload["data"]["statisticsProduction"]
        return float(production["totalNow"]), production["totalNowUnit"]
