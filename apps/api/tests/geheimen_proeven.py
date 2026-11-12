"""Proefmanifesten voor de weigering van geheimen en accountgegevens (ADR-0016 W2 en W3, ADR-0017).

Alleen testwaarden. De manifesten zijn afgeleid van de testfixtures van deze repo (``valid-custom-upstream`` voor een
Custom App, ``valid-catalog-1-3`` voor een catalogusapp) met de wegen uit ADR-0016, kop "Gevolgen per repo" punt 2:
p1 tot en met p7 (Gotify- en Authentik-vorm), n1 tot en met n10 en c01 tot en met c15 (``FLOWS`` van Node-RED), en uit
ADR-0017: de kruisverwijzing (kr1, kr2) en de bcrypt-vormen die T1 eerst niet kende. Elke hash wordt hier uit stukken
gemaakt: er staat geen hash letterlijk in de repo, en geen enkele waarde is een echt geheim.
"""

from __future__ import annotations

import copy
from typing import Any

from tests.conftest import load_fixture

#: Digest van de Node-RED-image (``nodered/node-red:5.0.9-24``) waarop de uitkomst van de kandidaatvormen c01 tot en
#: met c15 bij ``nopt`` is gemeten (ADR-0016, kop "Gevolgen per repo" punt 2 en 4; W2, T2 stap 3). Bij een nieuwe digest
#: meet de rooktest van jelly-leon-platform dezelfde vormen opnieuw; T2 zelf heeft geen lijst van optievormen.
NODE_RED_DIGEST = "sha256:2ccd38b4066648bd5e17430c50a2f8102c566a82fd4f2fc5c342e61664d6896f"

#: Testwaarden die op een geheim lijken; ze mogen in geen melding, uitvoer of antwoord terugkomen (V-094).
PROEFSLEUTEL = "ProefSleutel-" + "Zt5" * 4
PROEFWACHTWOORD = "ProefWachtwoord-" + "Kq8" * 4

_STAART = "proefNietEchtABouw" * 3


def bcrypt_proef(prefix: str = "$2b$10$", eerste: str = ".") -> str:
    """Een testwaarde in de vorm van een bcrypt-hash: ``prefix`` en 53 tekens uit het bcrypt-alfabet (geen echte hash).
    ``eerste`` is het eerste teken van de salt (een letter valt ook onder ``interpolation_forbidden``)."""
    return prefix + (eerste + _STAART)[:53]


def json_escape_dollar(text: str) -> str:
    """``$`` als JSON-escape ``\\u0024`` (zoals in proef n3, n7 en n9)."""
    return text.replace("$", "\\u0024")


HASH = bcrypt_proef()
#: Dezelfde vorm met ``/`` in de salt, in de tekst geschreven als ``\\/`` (proef c13).
HASH_MET_SLASH = bcrypt_proef(eerste="/")
HASH_MET_SLASH_ESCAPED = HASH_MET_SLASH.replace("/", "\\/")

# ---------------------------------------------------------------------------------------------- FLOWS van Node-RED
#: De veilige delen van ``FLOWS`` uit het Node-RED-manifest van jelly-leon-platform (vorm na S-P2-410, gelezen op
#: 2026-11-12): geen gebruiker, geen geheim. Ze blijven geldig.
FLOWS_REST = (
    '-D nodesIncludes=["05-junction.js","20-inject.js","21-debug.js","24-complete.js","25-catch.js","25-status.js",'
    '"60-link.js","90-comment.js","91-global-config.js","98-unknown.js","10-switch.js","15-change.js","80-template.js",'
    '"89-delay.js","89-trigger.js","rbe.js","05-tls.js","21-httprequest.js","70-JSON.js","17-split.js"] '
    '-D externalModules={"autoInstall":false,"palette":{"allowInstall":false,"allowUpdate":false,"allowUpload":false},'
    '"modules":{"allowInstall":false}} -D functionExternalModules=false -D editorTheme.projects.enabled=false '
    '-D editorTheme.tours=false -D telemetry={"enabled":false,"updateNotification":false} '
    '-D diagnostics={"enabled":false,"ui":false} -D proxyOptions={"env":{}}'
)
ADMIN_DICHT = '-D adminAuth={"type":"credentials","users":[]}'
#: ``FLOWS`` zoals het Node-RED-manifest het nu heeft: editor dicht zonder beheerder (C-11).
FLOWS_DICHT = f"flows.json {ADMIN_DICHT} {FLOWS_REST}"
#: De eerdere vorm met anoniem leesrecht (jelly-leon-platform ADR-0023 besluit 4): geen gebruiker en geen geheim.
FLOWS_ANONIEM_LEZEN = (
    'flows.json -D adminAuth={"type":"credentials","users":[],"default":{"permissions":"read"}} ' + FLOWS_REST
)


def flows(midden: str, *, admin: str = ADMIN_DICHT) -> str:
    return " ".join(deel for deel in ("flows.json", admin, midden, FLOWS_REST) if deel)


def _gebruiker(wachtwoord: str, naam: str = "proef") -> str:
    return (
        '{"type":"credentials","users":[{"username":"' + naam + '","password":"' + wachtwoord + '","permissions":"*"}]}'
    )


_K = PROEFSLEUTEL
#: Gebruikersnaam met een harde spatie (U+00A0): bash splitst daar niet op (proef n9).
NBSP_NAAM = "a\u00a0b"
#: Kruisverwijzing: de sleutel ``users`` en ``password`` staan niet in de tekst, Node-RED vindt ze via ``[msg.k]``
#: en ``[msg.p]`` (gemeten met de gepinde image; ADR-0017, kop "Context").
_KRUIS = (
    '-D k=users -D p=password -D adminAuth={"type":"credentials"} -D adminAuth[msg.k][0].username=proef '
    '-D adminAuth[msg.k][0][msg.p]="HASHPLEK" -D adminAuth[msg.k][0].permissions="*"'
)

#: Tekstproeven op ``custom_app.env[].value`` (``FLOWS``). Elk geeft ``account_gegevens_verboden``.
TEKSTPROEVEN: dict[str, str] = {
    # n1 tot en met n10 (ADR-0016, "Gevolgen per repo" punt 2)
    "n1": f"flows.json -D adminAuth={_gebruiker(HASH, 'b')} {FLOWS_REST}",
    "n2": flows(f"-D credentialSecret={_K}"),
    "n3": (
        'flows.json -D adminAuth={"type":"credentials","\\u0075sers":{"\\u0075sername":"b","p\\u0061ssword":"'
        + json_escape_dollar(HASH)
        + '","permissions":"*"}} '
        + FLOWS_REST
    ),
    "n4b": flows(f"--define=adminAuth.users[0].password={bcrypt_proef(eerste='.')}"),
    "n5": flows(f"--define credentialSecret={_K}"),
    "n6": flows(f"--def=credentialSecret={_K}"),
    "n6b": flows(f"--defi credentialSecret={_K}"),
    "n7": f"flows.json --define adminAuth={_gebruiker(json_escape_dollar(HASH))} {FLOWS_REST}",
    "n8": flows(f"-D=credentialSecret={_K}"),
    "n9": f"flows.json -D adminAuth={_gebruiker(json_escape_dollar(HASH), 'a b')} {FLOWS_REST}",
    "n10": flows("-D @/data/extra.json"),
    # afkortingen, los woord en camelCase (ADR-0016, "Gevolgen per repo" punt 2)
    "d_is": flows(f"--d=credentialSecret={_K}"),
    "de_spatie": flows(f"--de credentialSecret={_K}"),
    "los_woord": flows(f"credentialSecret={_K}"),
    "camelcase": flows('-D settings={"secretKey":"' + _K + '"}'),
    # c01 tot en met c15 (kandidaatvormen; uitkomst bij nopt: NOPT_UITKOMST)
    "c01": flows(f"-d=credentialSecret={_K}"),
    "c02": flows(f"-de=credentialSecret={_K}"),
    "c03": flows(f"-define=credentialSecret={_K}"),
    "c04": flows(f"--D=credentialSecret={_K}"),
    "c05": flows(f"---define=credentialSecret={_K}"),
    "c06": flows(f"--no-define=credentialSecret={_K}"),
    "c07": flows(f"--no-d=credentialSecret={_K}"),
    "c08": flows(f"-vD=credentialSecret={_K}"),
    "c09": flows(f"-d credentialSecret={_K}"),
    "c10": flows(f"-D=credentialSecret={_K}"),
    "c11": flows(f"credentialSecret={_K}"),
    "c12": flows(f"-Dv=credentialSecret={_K}"),
    "c13": flows(
        f'-d=adminAuth.users[0].username=proef -d=adminAuth.users[0].password="{HASH_MET_SLASH_ESCAPED}" '
        '-d=adminAuth.users[0].permissions="*"',
        admin='-D adminAuth={"type":"credentials"}',
    ),
    "c14": flows(
        f'--D=adminAuth.users[0].username=proef --D=adminAuth.users[0].password="{HASH_MET_SLASH_ESCAPED}" '
        f'--D=adminAuth.users[0].permissions="*" -define=credentialSecret={_K}',
        admin='-D adminAuth={"type":"credentials"}',
    ),
    "c15": flows("-d=@/data/extra.json"),
    # ADR-0017: kruisverwijzing met een $2x$-hash (kr1) en met een $2y$-hash (kr2)
    "kr1": flows(_KRUIS.replace("HASHPLEK", bcrypt_proef("$2x$10$")), admin=""),
    "kr2": flows(_KRUIS.replace("HASHPLEK", bcrypt_proef("$2y$10$")), admin=""),
}

#: n4 van R-VEILIG-4 heeft een salt die met een letter begint; die valt daardoor ook onder ``interpolation_forbidden``.
N4_LETTERSALT = flows(f"--define=adminAuth.users[0].password={bcrypt_proef(eerste='a')}")

#: Wat ``nopt`` en ``red.js`` uit de gepinde image (``NODE_RED_DIGEST``) met elke kandidaatvorm doen (ADR-0016,
#: "Gevolgen per repo" punt 2, gemeten bij de herreview van die ADR). T2 weigert ze alle vijftien, ook c11 en c12, die
#: Node-RED niet als instelling aanneemt (fail-closed: T2 heeft geen optielijst).
NOPT_UITKOMST: dict[str, str] = {
    **{c: "instelling credentialSecret" for c in ("c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08")},
    "c09": "instelling credentialSecret",
    "c10": "instelling credentialSecret",
    "c11": "geen instelling (argv.remain, de naam van het flowbestand)",
    "c12": "red.js stopt (de define-waarde wordt --verbose)",
    "c13": "gebruiker adminAuth.users[0] met hash en permissions *",
    "c14": "gebruiker adminAuth.users[0] en instelling credentialSecret",
    "c15": "instellingen uit het bestand /data/extra.json (@)",
}

#: ADR-0017: elke bcrypt-vorm die de bcrypt van Node-RED (``@node-rs/bcrypt``, users.js) aanneemt.
BCRYPT_VORMEN: dict[str, str] = {
    "2a": bcrypt_proef("$2a$10$"),
    "2b": bcrypt_proef("$2b$10$"),
    "2y": bcrypt_proef("$2y$10$"),
    "2x": bcrypt_proef("$2x$10$"),
    "zonder_dollar_vooraan": bcrypt_proef("2b$10$"),
    "dubbele_dollars": bcrypt_proef("$$2b$$10$$"),
    "kosten_voorloopnul": bcrypt_proef("$2b$010$"),
    "kosten_plus": bcrypt_proef("$2b$+10$"),
    "salt_cijfer": bcrypt_proef(eerste="1"),
    "salt_slash": bcrypt_proef(eerste="/"),
}
#: Hashes in PHC-vorm (ADR-0016 T1). Alleen het begin telt; de rest is een testwaarde.
PHC_VORMEN: dict[str, str] = {
    "argon2id": "$argon2id$v=19$m=65536,t=3,p=4$cHJvZWY$" + "A" * 43,
    "scrypt": "$scrypt$ln=16,r=8,p=1$cHJvZWY$" + "B" * 43,
    "pbkdf2": "$pbkdf2-sha256$29000$cHJvZWY$" + "C" * 43,
}


# ---------------------------------------------------------------------------------------------- manifesten
def custom_basis() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("valid-custom-upstream.manifest.yaml"))


def catalogus_basis() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))


def met_env(env: dict[str, Any], *parameters: dict[str, Any], accepted: list[dict[str, str]] | None = None) -> dict:
    """Custom App met een extra omgevingsvariabele (komt op ``custom_app.env[2]``) en extra parameters."""
    data = custom_basis()
    data["parameters"].extend(copy.deepcopy(list(parameters)))
    data["custom_app"]["env"].append(copy.deepcopy(env))
    if accepted is not None:
        data["accepted_findings"] = accepted
    return data


def met_waarden(extra: dict[str, Any], *parameters: dict[str, Any]) -> dict[str, Any]:
    """Catalogusapp met extra sleutels in ``values.voorbeeld`` en extra parameters."""
    data = catalogus_basis()
    data["values"]["voorbeeld"].update(copy.deepcopy(extra))
    data["parameters"].extend(copy.deepcopy(list(parameters)))
    return data


def wizard(naam: str, type_: str = "string") -> dict[str, Any]:
    return {"name": naam, "type": type_, "source": "wizard", "required": True, "description_nl": "Proefparameter."}


def hub_geheim(naam: str) -> dict[str, Any]:
    return {
        "name": naam,
        "type": "string",
        "source": "generated",
        "secret": True,
        "required": True,
        "description_nl": "Geheim dat de Hub maakt (proef).",
    }


BEHEER = "beheer_wachtwoord"
#: Index van de eerste extra parameter (de fixture heeft er zes).
EXTRA_PARAM = 6

#: Naamproeven (ADR-0016, "Gevolgen per repo" punt 2): manifest en per pad de verwachte code.
NAAMPROEVEN: dict[str, tuple[dict[str, Any], dict[str, str]]] = {
    "p1": (
        met_env({"name": "GOTIFY_DEFAULTUSER_PASS", "value_from": {"param": BEHEER}}, wizard(BEHEER)),
        {"custom_app.env[2]": "account_gegevens_verboden"},
    ),
    "p2": (
        met_waarden({"additional_envs": [{"name": "AUTHENTIK_BOOTSTRAP_PASSWORD", "value": PROEFWACHTWOORD}]}),
        {"values.voorbeeld.additional_envs[0].value": "secret_literal_forbidden"},
    ),
    "p3": (
        met_waarden(
            {"additional_envs": [{"name": "AUTHENTIK_BOOTSTRAP_PASSWORD", "value": {"param": BEHEER}}]}, wizard(BEHEER)
        ),
        {"values.voorbeeld.additional_envs[0].value": "account_gegevens_verboden"},
    ),
    "p4": (
        met_waarden({"bootstrap_password": {"param": BEHEER}}, wizard(BEHEER)),
        {"values.voorbeeld.bootstrap_password": "account_gegevens_verboden"},
    ),
    "p5": (
        met_env(
            {"name": "GOTIFY_DEFAULTUSER_PASS", "value_from": {"param": BEHEER}},
            {**wizard(BEHEER), "source": "default", "default": PROEFWACHTWOORD},
        ),
        {
            "custom_app.env[2]": "account_gegevens_verboden",
            f"parameters[{EXTRA_PARAM}].default": "secret_literal_forbidden",
        },
    ),
    "p5b": (
        met_env({"name": "GOTIFY_DEFAULTUSER_PASS", "value": PROEFWACHTWOORD}),
        {"custom_app.env[2]": "secret_literal_forbidden"},
    ),
    "p6": (
        met_env(
            {"name": "GOTIFY_DEFAULTUSER_PASS", "value_from": {"param": BEHEER}},
            hub_geheim(BEHEER),
            accepted=[{"code": "secret_in_form", "reden_nl": "Proef: beginwachtwoord via de vault-weg."}],
        ),
        {"custom_app.env[2]": "account_gegevens_verboden"},
    ),
    "p7": (
        met_env({"name": "APP_SECRET_KEY", "value": PROEFSLEUTEL}),
        {"custom_app.env[2]": "secret_literal_forbidden"},
    ),
    # dezelfde weg als p7 met een camelCase-naam
    "p7_secretKey": (
        met_env({"name": "secretKey", "value": PROEFSLEUTEL}),
        {"custom_app.env[2]": "secret_literal_forbidden"},
    ),
    "p7_passwordHash": (
        met_env({"name": "passwordHash", "value": PROEFSLEUTEL}),
        {"custom_app.env[2]": "secret_literal_forbidden"},
    ),
    "p7_apiKeyValue": (
        met_env({"name": "apiKeyValue", "value": PROEFSLEUTEL}),
        {"custom_app.env[2]": "secret_literal_forbidden"},
    ),
    # dezelfde wegen als p1, p3 en p4 met een geheimnaam zonder accountwoord (N2)
    "p1_n2": (
        met_env({"name": "GOTIFY_DB_PASS", "value_from": {"param": "db_wachtwoord"}}, wizard("db_wachtwoord")),
        {"custom_app.env[2]": "secret_in_form"},
    ),
    "p3_n2": (
        met_waarden(
            {"additional_envs": [{"name": "AUTHENTIK_EMAIL__PASSWORD", "value": {"param": "smtp_wachtwoord"}}]},
            wizard("smtp_wachtwoord"),
        ),
        {"values.voorbeeld.additional_envs[0].value": "secret_in_form"},
    ),
    "p4_n2": (
        met_waarden({"smtp_password": {"param": "smtp_wachtwoord"}}, wizard("smtp_wachtwoord")),
        {"values.voorbeeld.smtp_password": "secret_in_form"},
    ),
}

#: Teksten die Python met ``str.strip()`` leeg maakt, maar die voor bash, ``nopt`` en de apps een waarde zijn
#: (R-VEILIG-1 van S-P2-520; ADR-0018): spaties, U+00A0, U+2003 en de stuurtekens U+001C en U+001F.
WITRUIMTE: dict[str, str] = {
    "spaties": " " * 12,
    "nbsp": " " * 12,
    "em": " " * 12,
    "fs": "\x1c" * 8,
    "us": "\x1f" * 8,
}

#: Alle testwaarden die nooit in een melding of antwoord mogen staan.
TESTWAARDEN: tuple[str, ...] = (
    PROEFSLEUTEL,
    PROEFWACHTWOORD,
    HASH,
    HASH_MET_SLASH,
    HASH_MET_SLASH_ESCAPED,
    bcrypt_proef(eerste="a"),
    *BCRYPT_VORMEN.values(),
    *PHC_VORMEN.values(),
)
