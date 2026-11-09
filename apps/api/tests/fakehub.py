"""Simulator van de Hub (alleen voor tests) volgens platformcontract 1.2 §3.3–§3.8, §4.3–§4.6.

Strikt zoals het contract: onbekende velden of een JWK met ``use``/``alg`` → 422 ``validation_error``; onbekend,
verlopen of al gebruikt token → 401 ``invalid_enrollment`` (één code, geen orakel); na 10 mislukte pogingen binnen
15 min → 429 met ``Retry-After``; toegestane herhaling (zelfde grant, zelfde thumbprint, uitgegeven token nooit
gebruikt) → 200 met ``enrollment_replayed: true``; ``GET /api/v1/services/self``, ``POST …/self/rotate``,
``Hub-Token-Rotation: required`` en ondertekende sleutelrotaties.

Dit is een **simulator**; resultaten ermee zijn "Sim-getest", nooit een bewijs tegen de echte Hub.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appstore.hubkeys import CROCKFORD, jwk_thumbprint, keyset_fingerprint, normalize_code
from appstore.manifest.provenance import b64url, key_id, raw_public

HUB_HOST = "hub.test"
HUB_URL = f"http://{HUB_HOST}:30400"
SOURCE_HOST = "bron.test"
APP_ID = "appstore-en-fork"
ENROLL_FIELDS = {"app_id", "enrollment_token", "pairing_code", "version", "base_url", "public_key_jwk"}
_APP_ID = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")
_TOKEN = re.compile(r"^enr_[A-Za-z0-9_-]{43}$")


def hub_jwk(key: Ed25519PrivateKey) -> dict[str, str]:
    """Zoals de Hub: ``kid`` = sha256(raw)[:16], met ``use``/``alg`` (contract 1.2 §3.3, antwoord)."""
    pub = key.public_key()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": b64url(raw_public(pub)),
        "kid": key_id(pub),
        "use": "sig",
        "alg": "EdDSA",
    }


def new_pairing_code() -> str:
    raw = "".join(secrets.choice(CROCKFORD) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


@dataclass
class Grant:
    kind: str  # token|koppelcode
    app_id: str
    created: float
    used_with_thumbprint: str | None = None
    issued_token: str | None = None


@dataclass
class ServiceToken:
    service_id: str
    revoked: bool = False
    last_used_at: float | None = None


@dataclass
class FakeHub:
    key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    policy_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    grants: dict[str, Grant] = field(default_factory=dict)  # sha256(token | genormaliseerde code) → grant
    tokens: dict[str, ServiceToken] = field(default_factory=dict)  # service-token → registratie
    service_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ttl_seconds: float = 3600.0
    enroll_status: int | None = None  # forceer een status (bv. 500, 503, 302)
    response_patch: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    inbox_status: int = 202
    self_status: int | None = None
    rotation_required: bool = False
    rotate_status: int | None = None
    key_rotation_items: list[dict[str, Any]] = field(default_factory=list)
    key_rotation_status: int = 200
    down: bool = False
    failed_attempts: list[float] = field(default_factory=list)
    enroll_requests: list[dict[str, Any]] = field(default_factory=list)
    enroll_responses: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)
    all_urls: list[str] = field(default_factory=list)  # elk verzoek, ook naar andere hosts (redirect-controle)
    inbox: list[dict[str, Any]] = field(default_factory=list)
    inbox_auth: list[str] = field(default_factory=list)
    key_rotation_calls: int = 0
    source_files: dict[str, bytes] = field(default_factory=dict)
    source_etag: str = '"v1"'
    hub_host: str = HUB_HOST
    last_service_token: str | None = None

    # -------------------------------------------------------------- grants
    def add_token(self, app_id: str = APP_ID, *, age: float = 0.0) -> str:
        token = "enr_" + secrets.token_urlsafe(32)
        self.grants[hashlib.sha256(token.encode()).hexdigest()] = Grant("token", app_id, time.time() - age)
        return token

    def add_code(self, app_id: str = APP_ID) -> str:
        code = new_pairing_code()
        digest = hashlib.sha256(normalize_code(code).encode()).hexdigest()
        self.grants[digest] = Grant("koppelcode", app_id, time.time())
        return code

    # -------------------------------------------------------------- sleutels en tokens
    def jwks(self) -> dict[str, Any]:
        return {"keys": [hub_jwk(self.key)]}

    def policy_jwk(self) -> dict[str, str]:
        return hub_jwk(self.policy_key)

    def fingerprint(self) -> str:
        return keyset_fingerprint(self.jwks()["keys"], [self.policy_jwk()])

    def jwt(
        self,
        sub: str = "gebruiker-1",
        roles: list[str] | None = None,
        aud: str = APP_ID,
        ttl: int = 60,
        iat: int | None = None,
        key: Ed25519PrivateKey | None = None,
        iss: str = "hub",
        token_use: str | None = "user",
        jti: str | None = "auto",
        extra: dict[str, Any] | None = None,
        kid: str | None = None,
    ) -> str:
        now = int(time.time()) if iat is None else iat
        signer = key or self.key
        claims: dict[str, Any] = {
            "iss": iss,
            "aud": aud,
            "sub": sub,
            "roles": roles if roles is not None else ["admin"],
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
        }
        if token_use is not None:
            claims["token_use"] = token_use
        if jti is not None:
            claims["jti"] = str(uuid.uuid4()) if jti == "auto" else jti
        claims.update(extra or {})
        return jwt.encode(claims, signer, algorithm="EdDSA", headers={"kid": kid or key_id(signer.public_key())})

    def service_jwt(self, service_id: str | None = None, **kw: Any) -> str:
        """Dienst-JWT van §4.3 (``token_use: hub_service``)."""
        extra = {"service_id": service_id or self.service_id, **kw.pop("extra", {})}
        return self.jwt(
            sub=kw.pop("sub", "hub"), roles=kw.pop("roles", ["service"]), token_use="hub_service", extra=extra, **kw
        )

    def auth(self, **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt(**kw)}"}

    def service_auth(self, **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.service_jwt(**kw)}"}

    def rotation_statement(
        self, sequence: int, purpose: str, add: list[dict[str, str]], retire: list[str], signer: Ed25519PrivateKey
    ) -> dict[str, Any]:
        payload = {
            "type": "hub_key_rotation",
            "iss": "hub",
            "sequence": sequence,
            "issued_at": "2026-11-09T12:00:00Z",
            "purpose": purpose,
            "add": add,
            "retire": retire,
        }
        statement = jwt.api_jws.encode(
            json.dumps(payload).encode(),
            signer,
            algorithm="EdDSA",
            headers={"kid": key_id(signer.public_key()), "typ": "hub-key-rotation+jws"},
        )
        return {"sequence": sequence, "statement": statement}

    def revoke_all(self) -> None:
        for reg in self.tokens.values():
            reg.revoked = True

    def _bearer(self, request: httpx.Request) -> ServiceToken | None:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        reg = self.tokens.get(token)
        if reg is None or reg.revoked:
            return None
        reg.last_used_at = time.time()
        self._presented = token
        return reg

    def _headers(self) -> dict[str, str]:
        """§3.8: rotatie aangevraagd, of een ouder token gebruikt terwijl het nieuwste nog nooit gebruikt is."""
        newest = self.tokens.get(self.last_service_token or "")
        stale = (
            getattr(self, "_presented", None) not in (None, self.last_service_token)
            and newest is not None
            and newest.last_used_at is None
            and not newest.revoked
        )
        return {"Hub-Token-Rotation": "required"} if self.rotation_required or stale else {}

    # -------------------------------------------------------------- enrollment (§3.3, §3.6)
    @staticmethod
    def _error(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
        body = {"code": code, "message": "simulator", "details": {}, "trace_id": str(uuid.uuid4())}
        return httpx.Response(status, json=body, headers=headers or {})

    def _validate(self, body: Any) -> str | None:
        if not isinstance(body, dict) or set(body) - ENROLL_FIELDS:
            return "onbekend veld"
        if ("enrollment_token" in body) == ("pairing_code" in body):
            return "precies één van enrollment_token/pairing_code"
        for name in ("app_id", "version", "base_url", "public_key_jwk"):
            if name not in body:
                return f"{name} ontbreekt"
        app_id, version, base_url, jwk_ = body["app_id"], body["version"], body["base_url"], body["public_key_jwk"]
        if not isinstance(app_id, str) or not 1 <= len(app_id) <= 40 or not _APP_ID.match(app_id):
            return "app_id"
        if not isinstance(version, str) or not 1 <= len(version) <= 64 or not _VERSION.match(version):
            return "version"
        if base_url is not None and (not isinstance(base_url, str) or len(base_url) > 512):
            return "base_url"
        if "enrollment_token" in body and not (
            isinstance(body["enrollment_token"], str) and _TOKEN.match(body["enrollment_token"])
        ):
            return "enrollment_token"
        if "pairing_code" in body:
            code = body["pairing_code"]
            if not isinstance(code, str) or len(code) > 32:
                return "pairing_code"
            norm = normalize_code(code)
            if len(norm) != 12 or any(ch not in CROCKFORD for ch in norm):
                return "pairing_code"
        if not isinstance(jwk_, dict) or set(jwk_) - {"kty", "crv", "x", "kid"}:
            return "public_key_jwk: alleen kty, crv, x, kid"
        try:
            thumb = jwk_thumbprint(jwk_)
        except ValueError:
            return "public_key_jwk"
        if "kid" in jwk_ and jwk_["kid"] != thumb:
            return "public_key_jwk.kid ≠ RFC 7638-thumbprint"
        return None

    def _enroll(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content)
        except ValueError:
            return self._error(422, "validation_error")
        self.enroll_requests.append(body)
        if self.enroll_status is not None:
            if 300 <= self.enroll_status < 400:
                return httpx.Response(self.enroll_status, headers={"Location": "http://elders.test/"})
            return self._error(self.enroll_status, "service_unavailable" if self.enroll_status >= 500 else "fout")
        now = time.time()
        self.failed_attempts = [t for t in self.failed_attempts if now - t < 15 * 60]
        if len(self.failed_attempts) >= 10:
            return self._error(429, "rate_limited", {"Retry-After": "120"})
        if problem := self._validate(body):
            return self._error(422, "validation_error", {"X-Sim-Reden": problem})
        secret = body.get("enrollment_token") or normalize_code(body["pairing_code"])
        grant = self.grants.get(hashlib.sha256(secret.encode()).hexdigest())
        thumb = jwk_thumbprint(body["public_key_jwk"])
        replay = False
        if grant is None or grant.app_id != body["app_id"] or now - grant.created > self.ttl_seconds:
            grant = None
        elif grant.used_with_thumbprint is not None:
            issued = self.tokens.get(grant.issued_token or "")
            if grant.used_with_thumbprint == thumb and issued is not None and issued.last_used_at is None:
                issued.revoked = True  # toegestane herhaling: ongebruikt token intrekken, nieuw uitgeven
                replay = True
            else:
                grant = None
        if grant is None:
            self.failed_attempts.append(now)
            return self._error(401, "invalid_enrollment")
        self.failed_attempts.clear()
        if not replay:
            for reg in self.tokens.values():  # bestaande registratie: oude tokens intrekken, service_id blijft
                reg.revoked = True
        token = "svc_" + secrets.token_urlsafe(32)
        self.tokens[token] = ServiceToken(self.service_id)
        self.last_service_token = token
        grant.used_with_thumbprint, grant.issued_token = thumb, token
        out: dict[str, Any] = {
            "schema_version": "1.2",
            "service_id": self.service_id,
            "app_id": body["app_id"],
            "service_token": token,
            "token_type": "Bearer",
            "hub_issuer": "hub",
            "jwks": self.jwks(),
            "policy_public_key": self.policy_jwk(),
            "keyset_fingerprint": self.fingerprint(),
            "jwks_url": "/api/v1/auth/jwks",
            "events_inbox_url": "/api/v1/events/inbox",
            "key_rotation_url": "/api/v1/auth/key-rotations",
            "enrollment_replayed": replay,
        }
        if self.response_patch is not None:
            out = self.response_patch(out)
        self.enroll_responses.append((200, out))
        return httpx.Response(200, json=out, headers={"Cache-Control": "no-store"})

    # -------------------------------------------------------------- HTTP
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.all_urls.append(str(request.url))
        if self.down:
            raise httpx.ConnectError("Hub uit (simulator)", request=request)
        host = request.url.host
        path = request.url.path
        if host == SOURCE_HOST:
            return self._source(request)
        if host != self.hub_host:
            raise httpx.ConnectError(f"onbekende host {host}", request=request)
        self.requests.append((request.method, path))
        if path == "/api/v1/auth/jwks":
            return httpx.Response(200, json=self.jwks())
        if path == "/api/v1/services/enroll" and request.method == "POST":
            return self._enroll(request)
        if path == "/api/v1/auth/key-rotations" and request.method == "GET":
            self.key_rotation_calls += 1
            if self.key_rotation_status != 200:
                return self._error(self.key_rotation_status, "not_found")
            after = int(request.url.params.get("after", "0"))
            return httpx.Response(200, json={"items": [i for i in self.key_rotation_items if i["sequence"] > after]})
        if path == "/api/v1/services/self" and request.method == "GET":
            if self.self_status is not None:
                return self._error(self.self_status, "storing")
            reg = self._bearer(request)
            if reg is None:
                return self._error(401, "unauthorized")
            body = {
                "service_id": reg.service_id,
                "app_id": APP_ID,
                "status": "active",
                "version": "0.1.0",
                "enrolled_at": "2026-11-09T12:00:00Z",
                "keyset_fingerprint": self.fingerprint(),
            }
            return httpx.Response(200, json=body, headers=self._headers())
        if path == "/api/v1/services/self/rotate" and request.method == "POST":
            if self.rotate_status is not None:
                return self._error(self.rotate_status, "storing")
            reg = self._bearer(request)
            if reg is None:
                return self._error(401, "unauthorized")
            for other, other_reg in self.tokens.items():  # herhaalde rotatie: ongebruikte nieuwe tokens intrekken
                if other == self.last_service_token and other != self._presented and other_reg.last_used_at is None:
                    other_reg.revoked = True
            token = "svc_" + secrets.token_urlsafe(32)
            self.tokens[token] = ServiceToken(reg.service_id)
            self.last_service_token = token
            self.rotation_required = False
            return httpx.Response(
                200,
                json={
                    "service_id": reg.service_id,
                    "service_token": token,
                    "oude_tokens_geldig_tot": "2026-11-10T12:00:00Z",
                },
                headers={"Cache-Control": "no-store"},
            )
        if path == "/api/v1/events/inbox" and request.method == "POST":
            self.inbox_auth.append(request.headers.get("authorization", ""))
            if self._bearer(request) is None:
                return self._error(401, "unauthorized")
            if self.inbox_status < 300:
                self.inbox.append(json.loads(request.content))
            return httpx.Response(self.inbox_status, json={}, headers=self._headers())
        return self._error(404, "not_found")

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
        return lambda: httpx.Client(transport=httpx.MockTransport(self.handler), timeout=5, follow_redirects=False)

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
