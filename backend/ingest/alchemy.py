"""Minimal Alchemy NFT API v3 + JSON-RPC client (free tier: throttled, retried, key never logged)."""

import threading
import time

import requests

from config import env

NFT_PAGE = 100


class AlchemyError(RuntimeError):
    pass


class Alchemy:
    def __init__(self, network: str, key: str | None = None, min_interval=0.25):
        self.key = key or env("ALCHEMY_API_KEY", "")
        if not self.key:
            raise AlchemyError("ALCHEMY_API_KEY is not set (backend/.env)")
        self.network = network
        self.http = requests.Session()
        self.min_interval = min_interval  # free tier: stay well below the CU/s limit
        self._last = 0.0
        self._lock = threading.Lock()

    def _url(self, kind):
        base = f"https://{self.network}.g.alchemy.com"
        return f"{base}/nft/v3/{self.key}" if kind == "nft" else f"{base}/v2/{self.key}"

    def _throttle(self):
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _request(self, method, kind, path="", retries=8, **kw):
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.http.request(method, self._url(kind) + path, timeout=90, **kw)
            except requests.RequestException as e:
                err = type(e).__name__  # never str(e): the URL contains the key
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise AlchemyError(f"{path or method}: HTTP {r.status_code}: {r.text[:200]}")
                err = f"HTTP {r.status_code}"
            time.sleep(min(60, 2**attempt))
            print(f"[alchemy] {path or method}: {err}, retry {attempt + 1}", flush=True)
        raise AlchemyError(f"{path or method}: gave up after {retries} attempts")

    # ---- NFT API -------------------------------------------------------------------------------

    def contract_metadata(self, address):
        return self._request("GET", "nft", "/getContractMetadata", params={"contractAddress": address})

    def nfts_for_contract(self, address, page_key=None, limit=NFT_PAGE):
        params = {"contractAddress": address, "withMetadata": "true", "limit": limit}
        if page_key:
            params["pageKey"] = page_key
        return self._request("GET", "nft", "/getNFTsForContract", params=params)

    def nft_metadata(self, address, token_id):
        return self._request(
            "GET", "nft", "/getNFTMetadata", params={"contractAddress": address, "tokenId": str(token_id)}
        )

    # ---- JSON-RPC ------------------------------------------------------------------------------

    def rpc(self, method, params):
        j = self._request("POST", "rpc", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in j:
            raise AlchemyError(f"{method}: {j['error']}")
        return j["result"]
