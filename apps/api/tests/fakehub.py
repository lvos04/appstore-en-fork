"""Simulator van de Hub (alleen voor tests): JWKS, enrollment, events-inbox en een catalogus-HTTP-bron.

Dit is een **simulator**; resultaten ermee zijn "Sim-getest", nooit een bewijs tegen de echte Hub.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appstore.manifest.provenance import key_id, public_jwk

HUB_HOST = "hub.test"
HUB_URL = f"http://{HUB_HOST}:30400"
SOURCE_HOST = "bron.test"


@dataclass
class FakeHub:
    key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    policy_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    valid_codes: set[str] = field(default_factory=set)
    valid_tokens: set[str] = field(default_factory=set)
    issued_token: str = field(default_factory=lambda: "svc_" + secrets.token_urlsafe(32))
    jwks_status: int = 200
    enroll_status: int | None = None  # forceer een status (bv. 500 of 503)
    inbox_status: int = 202
    down: bool = False
    enroll_requests: list[dict[str, Any]] = field(default_factory=list)
    inbox: list[dict[str, Any]] = field(default_factory=list)
    inbox_auth: list[str] = field(default_factory=list)
    jwks_calls: int = 0
    source_files: dict[str, bytes] = field(default_factory=dict)
    source_etag: str = '"v1"'
    hub_host: str = HUB_HOST

    # -------------------------------------------------------------- tokens
    def jwt(
        self,
        sub: str = "gebruiker-1",
        roles: list[str] | None = None,
        aud: str = "appstore-en-fork",
        ttl: int = 60,
        iat: int | None = None,
        key: Ed25519PrivateKey | None = None,
        iss: str = "hub",
    ) -> str:
        now = int(time.time()) if iat is None else iat
        signer = key or self.key
        claims = {
            "iss": iss,
            "aud": aud,
            "sub": sub,
            "roles": roles if roles is not None else ["admin"],
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(claims, signer, algorithm="EdDSA", headers={"kid": key_id(signer.public_key())})

    def jwks(self) -> dict[str, Any]:
        return {"keys": [public_jwk(self.key.public_key())]}

    def auth(self, **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt(**kw)}"}

    # -------------------------------------------------------------- HTTP
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Hub uit (simulator)", request=request)
        host = request.url.host
        path = request.url.path
        if host == SOURCE_HOST:
            return self._source(request)
        if host != self.hub_host:
            raise httpx.ConnectError(f"onbekende host {host}", request=request)
        if path == "/api/v1/auth/jwks":
            self.jwks_calls += 1
            if self.jwks_status != 200:
                return httpx.Response(self.jwks_status, json={"code": "fout"})
            return httpx.Response(200, json=self.jwks())
        if path == "/api/v1/services/enroll" and request.method == "POST":
            body = json.loads(request.content)
            self.enroll_requests.append(body)
            if self.enroll_status is not None:
                return httpx.Response(self.enroll_status, json={"code": "storing"})
            code = body.get("pairing_code")
            token = body.get("enrollment_token")
            if (code and code in self.valid_codes) or (token and token in self.valid_tokens):
                self.valid_codes.discard(code or "")
                self.valid_tokens.discard(token or "")
                return httpx.Response(
                    201,
                    json={
                        "service_id": "svc-appstore",
                        "service_token": self.issued_token,
                        "jwks": self.jwks(),
                        "policy_public_key": public_jwk(self.policy_key.public_key()),
                    },
                )
            return httpx.Response(401, json={"code": "unauthorized", "message": "ongeldig"})
        if path == "/api/v1/events/inbox" and request.method == "POST":
            self.inbox_auth.append(request.headers.get("authorization", ""))
            if self.inbox_status < 300:
                self.inbox.append(json.loads(request.content))
            return httpx.Response(self.inbox_status, json={})
        return httpx.Response(404, json={"code": "not_found"})

    def _source(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if path == "catalogus/index.json":
            if request.headers.get("if-none-match") == self.source_etag:
                return httpx.Response(304)
            manifests = [
                {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in self.source_files.items()
                if not name.endswith(".sig")
            ]
            return httpx.Response(200, json={"manifests": manifests}, headers={"ETag": self.source_etag})
        name = path.removeprefix("catalogus/")
        if name in self.source_files:
            return httpx.Response(200, content=self.source_files[name])
        return httpx.Response(404)

    def client_factory(self) -> Any:
        return lambda: httpx.Client(transport=httpx.MockTransport(self.handler), timeout=5)

    def asgi(self) -> Any:
        """Dezelfde simulator als echte HTTP-dienst (voor E2E met echte processen)."""

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                return
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
            query = scope.get("query_string", b"").decode()
            url = f"http://{self.hub_host}{scope['path']}" + (f"?{query}" if query else "")
            try:
                resp = self.handler(httpx.Request(scope["method"], url, headers=headers, content=body))
            except httpx.ConnectError:
                resp = httpx.Response(503)
            await send(
                {
                    "type": "http.response.start",
                    "status": resp.status_code,
                    "headers": [
                        (k.encode(), v.encode())
                        for k, v in resp.headers.items()
                        if k.lower() not in ("content-length", "transfer-encoding")
                    ],
                }
            )
            await send({"type": "http.response.body", "body": resp.content})

        return app
