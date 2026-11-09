"""Semantische veiligheidsregels van manifest v1 (platformcontract 1.1 §9, EX47).

Het JSON Schema controleert vorm en types; deze regels controleren betekenis: geen privileged, geen Docker-socket,
geen gevaarlijke hostpaden, geen ``latest``, digest bij upstream-images, geen voorbeeldwaarden als echte waarde,
volledige back-up/upgrade/risico-gegevens. Alle regels zijn deterministisch en werken uitsluitend op het getypte
model; er wordt niets uitgevoerd of opgehaald.
"""

from __future__ import annotations

import ipaddress
import posixpath
import re
from collections.abc import Iterable, Iterator
from typing import Any

from .findings import Finding, error, warning
from .model import (
    Manifest,
    Parameter,
    ParamRef,
    PortBinding,
)

#: Eigen images (release-CI vult de digest). Alles daarbuiten is upstream en moet een digest hebben.
OWN_IMAGE_PREFIXES: tuple[str, ...] = ("ghcr.io/lvos04/",)

#: Tags die per definitie verschuiven.
MUTABLE_TAGS = frozenset(
    {
        "latest",
        "stable",
        "main",
        "master",
        "edge",
        "nightly",
        "dev",
        "develop",
        "beta",
        "alpha",
        "rolling",
        "lts",
        "current",
        "release",
        "head",
        "testing",
        "unstable",
        "next",
        "preview",
        "rc",
    }
)
_IMPRECISE_TAG = re.compile(r"^v?\d+(\.\d+)?(-[a-z][a-z0-9.]*)?$")
_EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+([.+_-][0-9A-Za-z.+_-]+)?$")

#: Platformminimum per lifecycle-actie (contract §8: app.install/update/uninstall minimaal R2).
RISK_MINIMUM = {"install": "R2", "update": "R2", "uninstall": "R2"}
_RISK_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}

#: Hostpaden die nooit gekoppeld mogen worden (exact of als ouder/kind). TrueNAS weigert er een deel zelf al
#: (truenas/apps library 2.3.18 ``validations.RESTRICTED``); wij zijn strenger.
FORBIDDEN_HOST_PATHS: tuple[str, ...] = (
    "/etc",
    "/var/run",
    "/run",
    "/root",
    "/boot",
    "/home",
    "/var/log",
    "/var/db",
    "/var/lib/docker",
    "/data",
    "/conf",
    "/audit",
    "/proc",
    "/sys",
    "/dev",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/opt",
    "/srv",
    "/tmp",  # noqa: S108 - verboden hostpad, geen tijdelijk bestand
    "/var",
    "/mnt/.ix-apps",
)
#: Alleen als exact pad verboden (kinderen zijn wél toegestaan, mits via de datasetkiezer).
FORBIDDEN_EXACT_HOST_PATHS: tuple[str, ...] = ("/", "/mnt")

#: Koppelpaden in de container die nooit overschreven mogen worden.
FORBIDDEN_MOUNT_PATHS: tuple[str, ...] = (
    "/",
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/run",
    "/var/run",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)
_SOCKET_MARKERS = ("docker.sock", "containerd.sock", "podman.sock", "crio.sock", "dockershim.sock", "buildkitd.sock")

#: Linux-capabilities (man 7 capabilities). ``ALL`` is nooit toegestaan.
KNOWN_CAPABILITIES = frozenset(
    {
        "AUDIT_CONTROL",
        "AUDIT_READ",
        "AUDIT_WRITE",
        "BLOCK_SUSPEND",
        "BPF",
        "CHECKPOINT_RESTORE",
        "CHOWN",
        "DAC_OVERRIDE",
        "DAC_READ_SEARCH",
        "FOWNER",
        "FSETID",
        "IPC_LOCK",
        "IPC_OWNER",
        "KILL",
        "LEASE",
        "LINUX_IMMUTABLE",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
        "MKNOD",
        "NET_ADMIN",
        "NET_BIND_SERVICE",
        "NET_BROADCAST",
        "NET_RAW",
        "PERFMON",
        "SETFCAP",
        "SETGID",
        "SETPCAP",
        "SETUID",
        "SYSLOG",
        "SYS_ADMIN",
        "SYS_BOOT",
        "SYS_CHROOT",
        "SYS_MODULE",
        "SYS_NICE",
        "SYS_PACCT",
        "SYS_PTRACE",
        "SYS_RAWIO",
        "SYS_RESOURCE",
        "SYS_TIME",
        "SYS_TTY_CONFIG",
        "WAKE_ALARM",
    }
)
#: Capabilities die gelijkstaan aan (bijna) root op de host: altijd geweigerd, ook mét reden.
FORBIDDEN_CAPABILITIES = frozenset(
    {
        "ALL",
        "SYS_ADMIN",
        "SYS_MODULE",
        "SYS_RAWIO",
        "SYS_PTRACE",
        "SYS_BOOT",
        "SYS_TIME",
        "DAC_READ_SEARCH",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
        "BPF",
        "PERFMON",
        "SYSLOG",
        "LINUX_IMMUTABLE",
        "AUDIT_CONTROL",
        "SETFCAP",
    }
)

#: Apparaten die nooit handmatig aan een container gegeven mogen worden.
_FORBIDDEN_DEVICE_PATTERNS = (
    re.compile(r"^/dev/(zfs|mem|kmem|port|kmsg|console|tty|ptmx|fuse|vhost-net|vsock|autofs)$"),
    re.compile(r"^/dev/(sd[a-z]+\d*|nvme\d+(n\d+)?(p\d+)?|hd[a-z]+\d*|vd[a-z]+\d*|md\d*|dm-\d+|loop\d*|zd\d+)$"),
    re.compile(r"^/dev/(disk|mapper|zvol|cpu|mapper|bsg|sg\d*|block)(/.*)?$"),
)
#: TrueNAS staat deze niet toe als handmatig apparaat; GPU gaat via ``gpu: intel_render``.
_FORM_MANAGED_DEVICES = ("/dev/dri", "/dev/kfd", "/dev/bus/usb", "/dev/snd", "/dev/net/tun")

_SECRET_ENV = re.compile(r"(PASS(WORD|WD)?|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL|AUTH_KEY|SALT)$", re.I)
_PLACEHOLDER = re.compile(r"<[A-Za-z0-9_ -]{1,40}>|\$\{[^}]*\}|\{\{[^}]*\}\}|%\([a-z_]+\)s|\bCHANGEME\b|\bchangeme\b")
_EXAMPLE_HOSTS = re.compile(
    r"(^|[/@.])(example\.(com|org|net)|truenas\.local|nas\.local|nas\.lan|my-?nas|your-?nas|mynas\.[a-z]+)(\b|$)",
    re.I,
)
_EXAMPLE_POOL = re.compile(r"(^|/)mnt/(tank|pool|pool1|data|nas|zpool|main|storage|POOL|<POOL>)(/|$)")
_IPV4 = re.compile(r"(?<![0-9.])(\d{1,3}(?:\.\d{1,3}){3})(?![0-9.])")


# ---------------------------------------------------------------------------------------------- hulpfuncties
def is_own_image(repository: str) -> bool:
    return repository.startswith(OWN_IMAGE_PREFIXES)


def example_reason(value: str) -> str | None:
    """Geeft een Nederlandse reden als ``value`` een voorbeeld-/plaatshouderwaarde lijkt (D01–D18 onbekend)."""
    if _PLACEHOLDER.search(value):
        return "bevat een plaatshouder"
    if _EXAMPLE_POOL.search(value):
        return "bevat een voorbeeld-poolnaam onder /mnt (poolnaam is onbekend, D02)"
    if _EXAMPLE_HOSTS.search(value):
        return "bevat een voorbeeld-hostnaam"
    for match in _IPV4.finditer(value):
        try:
            ip = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
            return f"bevat een vast IP-adres ({ip}); NAS- en LAN-adressen zijn onbekend (D03)"
    return None


def normalize_abs_path(path: str) -> str | None:
    """Normaliseer een absoluut POSIX-pad; ``None`` bij relatieve paden, ``..``-segmenten of lege paden."""
    if not path.startswith("/") or "\x00" in path:
        return None
    if any(part in (".", "..") for part in path.split("/")):
        return None
    return posixpath.normpath(path) if path != "/" else "/"


def host_path_problem(path: str) -> tuple[str, str] | None:
    """Controleer een hostpad. Geeft ``(code, uitleg)`` of ``None`` als het pad acceptabel is.

    Acceptabel = een dataset onder ``/mnt/<pool>/<dataset>[/...]`` die niet in een verboden gebied ligt.
    """
    norm = normalize_abs_path(path)
    if norm is None:
        return "forbidden_host_path", f"Hostpad {path!r} is geen genormaliseerd absoluut pad (geen '.', '..')."
    lowered = norm.lower()
    if any(marker in lowered for marker in _SOCKET_MARKERS):
        return "docker_socket_forbidden", f"Hostpad {path!r} is een container-runtime-socket; dat geeft root op de NAS."
    if norm in FORBIDDEN_EXACT_HOST_PATHS:
        return "forbidden_host_path", f"Hostpad {norm!r} is verboden (te breed: hele systeem of alle pools)."
    for forbidden in FORBIDDEN_HOST_PATHS:
        if norm == forbidden or norm.startswith(forbidden + "/") or forbidden.startswith(norm + "/"):
            return "forbidden_host_path", f"Hostpad {norm!r} valt in het verboden gebied {forbidden!r}."
    parts = [p for p in norm.split("/") if p]
    if parts[0] != "mnt" or len(parts) < 3:
        return (
            "forbidden_host_path",
            f"Hostpad {norm!r} ligt niet in een dataset (verwacht /mnt/<pool>/<dataset>/…).",
        )
    return None


def mount_path_problem(path: str) -> str | None:
    norm = normalize_abs_path(path)
    if norm is None:
        return f"Koppelpad {path!r} is geen genormaliseerd absoluut pad."
    if any(marker in norm.lower() for marker in _SOCKET_MARKERS):
        return f"Koppelpad {path!r} is een container-runtime-socket."
    if norm in FORBIDDEN_MOUNT_PATHS or norm.startswith(("/proc/", "/sys/", "/dev/")):
        return f"Koppelpad {norm!r} overschrijft een systeemmap in de container."
    return None


def device_problem(device: str) -> str | None:
    norm = normalize_abs_path(device) or device
    for managed in _FORM_MANAGED_DEVICES:
        if norm == managed or norm.startswith(managed + "/"):
            return (
                f"Apparaat {norm!r} mag niet handmatig worden toegevoegd; TrueNAS staat dat in het formulier niet toe "
                "(GPU via 'gpu: intel_render')."
            )
    for pattern in _FORBIDDEN_DEVICE_PATTERNS:
        if pattern.match(norm):
            return f"Apparaat {norm!r} geeft directe toegang tot opslag, geheugen of de kernel."
    return None


def _walk(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    """Loop recursief door JSON-achtige data; levert ``(pad, waarde)`` voor elke bladwaarde en elke sleutel."""
    if isinstance(value, dict):
        for key, sub in value.items():
            sub_path = f"{path}.{key}" if path else str(key)
            yield sub_path, ("__key__", key)
            yield from _walk(sub, sub_path)
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _walk(sub, f"{path}[{idx}]")
    else:
        yield path, value


def _param_refs(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """Alle ``{"param": "<naam>"}``-verwijzingen in vrije data (catalogus-``values``)."""
    if isinstance(value, dict):
        if set(value) == {"param"} and isinstance(value["param"], str):
            yield path, value["param"]
            return
        for key, sub in value.items():
            yield from _param_refs(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _param_refs(sub, f"{path}[{idx}]")


# ---------------------------------------------------------------------------------------------- regels
class _Rules:
    def __init__(self, m: Manifest) -> None:
        self.m = m
        self.out: list[Finding] = []
        self.params: dict[str, Parameter] = {p.name: p for p in m.parameters}
        self.used_params: set[str] = set()

    # -- registratie
    def err(self, code: str, path: str, msg: str) -> None:
        self.out.append(error(code, path, msg))

    def warn(self, code: str, path: str, msg: str) -> None:
        self.out.append(warning(code, path, msg))

    def ref(self, ref: ParamRef, path: str, allowed_types: Iterable[str], what: str) -> Parameter | None:
        param = self.params.get(ref.param)
        self.used_params.add(ref.param)
        if param is None:
            self.err("unknown_parameter", path, f"{what} verwijst naar onbekende parameter {ref.param!r}.")
            return None
        allowed = tuple(allowed_types)
        if param.type not in allowed:
            self.err(
                "parameter_type_mismatch",
                path,
                f"{what} verwacht een parameter van type {' of '.join(allowed)}; {ref.param!r} is {param.type!r}.",
            )
        return param

    # -- hoofdroute
    def run(self) -> list[Finding]:
        m = self.m
        self.parameters()
        self.duplicates()
        if m.install.method == "custom_app":
            self.custom_app()
        else:
            self.catalog_app()
        self.network()
        self.privileges()
        self.storage_decls()
        self.backup()
        self.healthcheck()
        self.dependencies()
        self.risk_and_upgrade()
        self.meta()
        for name, param in self.params.items():
            if name not in self.used_params and param.decision is None:
                self.warn("parameter_unused", f"parameters[{name}]", f"Parameter {name!r} wordt nergens gebruikt.")
        return self.out

    # -- parameters
    def parameters(self) -> None:
        for idx, p in enumerate(self.m.parameters):
            path = f"parameters[{idx}]"
            if p.source == "default":
                if p.default is None:
                    self.err(
                        "parameter_default_missing",
                        path,
                        f"Parameter {p.name!r} heeft source 'default' maar geen default.",
                    )
            elif p.default is not None:
                self.err(
                    "parameter_default_not_allowed",
                    f"{path}.default",
                    f"Parameter {p.name!r} (source {p.source!r}) mag geen default hebben: die waarde komt uit "
                    f"{'de wizard' if p.source == 'wizard' else 'de datasetkiezer' if p.source == 'dataset_picker' else 'de app zelf'}"
                    ", nooit uit het manifest.",
                )
            if isinstance(p.default, str) and (reason := example_reason(p.default)):
                self.err("example_value_as_real", f"{path}.default", f"Default van {p.name!r} {reason}.")
            if p.type in ("path", "dataset") and p.source not in ("dataset_picker", "wizard"):
                self.err(
                    "host_path_param_wrong_source",
                    f"{path}.source",
                    f"Pad-/datasetparameter {p.name!r} moet uit de datasetkiezer of wizard komen, niet uit {p.source!r}.",
                )
            if p.type == "secret_ref" and p.source not in ("secret", "generated"):
                self.err(
                    "secret_literal_forbidden",
                    f"{path}.source",
                    f"Geheim {p.name!r} moet source 'secret' of 'generated' hebben.",
                )
            if p.decision and p.decision not in self.m.decisions_required:
                self.err(
                    "decision_not_listed",
                    f"{path}.decision",
                    f"Parameter {p.name!r} hangt af van {p.decision}, maar dat besluit staat niet in decisions_required.",
                )

    def duplicates(self) -> None:
        def dupes(names: list[str], path: str, what: str) -> None:
            seen: set[str] = set()
            for n in names:
                if n in seen:
                    self.err("duplicate_name", path, f"{what} {n!r} komt dubbel voor.")
                seen.add(n)

        dupes([p.name for p in self.m.parameters], "parameters", "Parameter")
        dupes([s.name for s in self.m.storage], "storage", "Opslag")
        dupes([p.name for p in self.m.network.ports], "network.ports", "Poort")
        dupes([d.app_id for d in self.m.dependencies], "dependencies", "Afhankelijkheid")
        if self.m.custom_app:
            dupes([s.name for s in self.m.custom_app.storage], "custom_app.storage", "Opslagregel")
            dupes([e.name for e in self.m.custom_app.env], "custom_app.env", "Omgevingsvariabele")

    # -- custom app (het standaardformulier)
    def custom_app(self) -> None:
        m = self.m
        assert m.image is not None and m.custom_app is not None
        img, ca = m.image, m.custom_app
        tag_l = img.tag.lower()
        if not img.tag.strip():
            self.err("tag_missing", "image.tag", "Image-tag ontbreekt; gebruik een exacte versie.")
        elif tag_l in MUTABLE_TAGS or tag_l.startswith("latest"):
            self.err(
                "mutable_tag_forbidden",
                "image.tag",
                f"Tag {img.tag!r} verandert in de tijd; gebruik een exacte versie.",
            )
        elif _IMPRECISE_TAG.match(img.tag) and not _EXACT_VERSION.match(img.tag):
            self.warn(
                "imprecise_version_tag",
                "image.tag",
                f"Tag {img.tag!r} is geen exacte versie (major/minor schuift mee).",
            )
        if "@" in img.repository or re.search(r":[^/]*$", img.repository.split("/", 1)[-1]):
            self.err(
                "tag_in_repository",
                "image.repository",
                "Repository bevat een tag of digest; zet die in image.tag/image.digest.",
            )
        if img.digest is None:
            if is_own_image(img.repository):
                self.warn(
                    "digest_pending_release",
                    "image.digest",
                    "Eigen image zonder digest: de release-CI vult die in; installeren vereist de digest.",
                )
            else:
                self.err(
                    "digest_missing", "image.digest", f"Upstream-image {img.repository!r} vereist een sha256-digest."
                )
        # poorten: formulier == netwerkdeclaratie
        if _ports_key(ca.ports) != _ports_key(m.network.ports):
            self.err("ports_inconsistent", "custom_app.ports", "custom_app.ports moet gelijk zijn aan network.ports.")
        for idx, port in enumerate(ca.ports):
            self.port(port, f"custom_app.ports[{idx}]")
        # opslag
        decls = {s.name: s for s in m.storage}
        bound: set[str] = set()
        for idx, sb in enumerate(ca.storage):
            path = f"custom_app.storage[{idx}]"
            decl = decls.get(sb.name)
            bound.add(sb.name)
            if decl is None:
                self.err(
                    "storage_unknown_reference", path, f"Opslagregel {sb.name!r} heeft geen declaratie in storage[]."
                )
            if sb.type == "host_path":
                if isinstance(sb.host_path, ParamRef):
                    param = self.ref(sb.host_path, f"{path}.host_path", ("path",), "Hostpad")
                    if param is not None:
                        if param.source != "dataset_picker":
                            self.err(
                                "host_path_param_wrong_source",
                                f"{path}.host_path",
                                f"Hostpad-parameter {param.name!r} moet source 'dataset_picker' hebben.",
                            )
                        role = decl.required_dataset_role if decl else None
                        if decl is not None and role is None:
                            self.err(
                                "dataset_role_missing",
                                f"storage[{sb.name}].required_dataset_role",
                                f"Hostpad-opslag {sb.name!r} heeft geen required_dataset_role.",
                            )
                        if role is not None and param.dataset_role != role:
                            self.err(
                                "dataset_role_inconsistent",
                                f"{path}.host_path",
                                f"Datasetrol van parameter {param.name!r} ({param.dataset_role!r}) verschilt van "
                                f"storage {sb.name!r} ({role!r}).",
                            )
                elif isinstance(sb.host_path, str):
                    problem = host_path_problem(sb.host_path)
                    if problem:
                        self.err(problem[0], f"{path}.host_path", problem[1])
                    else:
                        self.err(
                            "literal_host_path",
                            f"{path}.host_path",
                            f"Hostpad {sb.host_path!r} staat letterlijk in het manifest; poolnaam en datasets zijn "
                            "onbekend (D02). Gebruik een parameter met source 'dataset_picker'.",
                        )
            elif sb.type == "tmpfs" and decl is not None and decl.kind != "scratch":
                self.err(
                    "invalid_value",
                    f"{path}.type",
                    f"tmpfs is alleen toegestaan voor opslag van soort 'scratch' ({sb.name!r}).",
                )
            if sb.type == "ix_volume" and decl is not None and decl.kind == "media":
                self.warn(
                    "invalid_value",
                    f"{path}.type",
                    f"Media-opslag {sb.name!r} op een ixVolume valt buiten de mediadatasets.",
                )
        for name, decl in decls.items():
            if name not in bound:
                self.err(
                    "storage_not_mapped", f"storage[{name}]", f"Opslag {name!r} heeft geen regel in custom_app.storage."
                )
            _ = decl
        # omgevingsvariabelen
        for idx, env in enumerate(ca.env):
            path = f"custom_app.env[{idx}]"
            if env.value is not None:
                low = env.value.lower()
                if env.name.upper() == "DOCKER_HOST" or any(marker in low for marker in _SOCKET_MARKERS):
                    self.err("docker_socket_forbidden", path, f"{env.name} verwijst naar een container-runtime-socket.")
                if _SECRET_ENV.search(env.name) and env.value.strip():
                    self.err(
                        "secret_literal_forbidden",
                        path,
                        f"{env.name} lijkt een geheim; geheimen horen nooit in het manifest of het Custom App-formulier. "
                        "De app maakt ze zelf aan in /appdata/secrets.",
                    )
                elif reason := example_reason(env.value):
                    self.err("example_value_as_real", f"{path}.value", f"Waarde van {env.name} {reason}.")
            if env.value_from is not None:
                param = self.ref(
                    env.value_from,
                    f"{path}.value_from",
                    ("string", "integer", "boolean", "port", "url", "timezone", "duration", "secret_ref", "dataset"),
                    f"Omgevingsvariabele {env.name}",
                )
                if param is not None and (param.type == "secret_ref" or param.source in ("secret", "generated")):
                    self.err(
                        "secret_in_form",
                        path,
                        f"{env.name} zou een geheim ({param.name!r}) in het Custom App-formulier zetten; dat is verboden.",
                    )
        # gpu, run_as, tijdzone, portal
        if ca.gpu != m.privileges.gpu:
            self.err("gpu_inconsistent", "custom_app.gpu", "custom_app.gpu moet gelijk zijn aan privileges.gpu.")
        if ca.run_as.mode == "image_default" and not is_own_image(img.repository):
            self.warn(
                "run_as_image_default_upstream",
                "custom_app.run_as",
                "Upstream-image draait als de gebruiker uit de image (mogelijk root); overweeg run_as custom 568:568.",
            )
        if ca.timezone is None:
            self.warn("timezone_missing", "custom_app.timezone", "Geen tijdzone: het formulier krijgt 'Etc/UTC'.")
        elif isinstance(ca.timezone, ParamRef):
            self.ref(ca.timezone, "custom_app.timezone", ("timezone",), "Tijdzone")
        elif ca.timezone != "Etc/UTC":
            self.warn(
                "example_value_as_real",
                "custom_app.timezone",
                f"Vaste tijdzone {ca.timezone!r}; laat de wizard die kiezen (parameter met type 'timezone').",
            )
        if ca.portal is not None and ca.portal.port not in {p.name for p in ca.ports}:
            self.err(
                "portal_unknown_port",
                "custom_app.portal.port",
                f"Portal verwijst naar onbekende poort {ca.portal.port!r}.",
            )

    def port(self, port: PortBinding, path: str) -> None:
        if isinstance(port.host_port, ParamRef):
            self.ref(port.host_port, f"{path}.host_port", ("port",), "Hostpoort")
        elif port.host_port < 1024:
            self.warn("port_privileged_range", f"{path}.host_port", f"Hostpoort {port.host_port} ligt onder 1024.")

    # -- catalogusapp
    def catalog_app(self) -> None:
        m = self.m
        assert m.catalog is not None and m.values is not None
        version = m.catalog.version.lower()
        if version in MUTABLE_TAGS or not _EXACT_VERSION.match(m.catalog.version):
            self.err(
                "mutable_version_forbidden",
                "catalog.version",
                f"Catalogusversie {m.catalog.version!r} moet een exacte versie zijn (bv. 1.2.3).",
            )
        for path, value in _walk(m.values, "values"):
            if isinstance(value, tuple) and value and value[0] == "__key__":
                key = str(value[1]).lower()
                if key in ("privileged",):
                    continue  # waarde wordt hieronder als bool gecontroleerd
                if key in ("custom_compose_config", "custom_compose_config_string", "compose", "services"):
                    self.err("compose_forbidden", path, "Compose-configuratie in catalogus-waarden is verboden.")
                continue
            leaf_key = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
            if leaf_key == "privileged" and value is True:
                self.err("privileged_forbidden", path, "Privileged-modus is verboden.")
            if leaf_key == "host_network" and value is True and not m.network.host_network_reason:
                self.err(
                    "host_network_without_reason", path, "Host network in catalogus-waarden zonder host_network_reason."
                )
            if leaf_key == "tag" and isinstance(value, str) and value.lower() in MUTABLE_TAGS:
                self.err("mutable_tag_forbidden", path, f"Tag {value!r} is veranderlijk.")
            if isinstance(value, str):
                low = value.lower()
                if any(marker in low for marker in _SOCKET_MARKERS):
                    self.err("docker_socket_forbidden", path, "Verwijzing naar een container-runtime-socket.")
                elif value.startswith("/") and leaf_key in ("path", "host_path", "hostpath"):
                    problem = host_path_problem(value)
                    if problem:
                        self.err(problem[0], path, problem[1])
                    else:
                        self.err(
                            "literal_host_path",
                            path,
                            f"Hostpad {value!r} staat letterlijk in de waarden; gebruik een parameter.",
                        )
                elif reason := example_reason(value):
                    self.err("example_value_as_real", path, f"Waarde {reason}.")
                if _SECRET_ENV.search(leaf_key) and value.strip():
                    self.err("secret_literal_forbidden", path, "Geheim als letterlijke waarde in de catalogus-waarden.")
        for path, name in _param_refs(m.values, "values"):
            param = self.params.get(name)
            self.used_params.add(name)
            if param is None:
                self.err("unknown_parameter", path, f"Waarde verwijst naar onbekende parameter {name!r}.")
            elif param.source in ("secret", "generated") or param.type == "secret_ref":
                self.warn(
                    "secret_in_form",
                    path,
                    f"Geheim {name!r} gaat als (door TrueNAS als privé gemarkeerde) catalogus-waarde mee; "
                    "controleer dat het niet in logs of evidence verschijnt.",
                )

    # -- netwerk
    def network(self) -> None:
        net = self.m.network
        if net.host_network:
            if not (net.host_network_reason or "").strip():
                self.err(
                    "host_network_without_reason", "network.host_network", "Host network zonder host_network_reason."
                )
            else:
                self.warn(
                    "host_network_requested",
                    "network.host_network",
                    "Host network gevraagd; vereist expliciete goedkeuring.",
                )
        if net.joins_platform_network:
            self.err(
                "platform_network_not_supported",
                "network.joins_platform_network",
                "Apps zitten niet in een gedeeld Docker-netwerk (contract §2); ze bereiken elkaar via NAS-adres + hostpoort.",
            )
        seen: set[tuple[int, str]] = set()
        for idx, port in enumerate(net.ports):
            if isinstance(port.host_port, int):
                key = (port.host_port, port.protocol)
                if key in seen:
                    self.err(
                        "duplicate_port",
                        f"network.ports[{idx}]",
                        f"Hostpoort {port.host_port}/{port.protocol} komt dubbel voor.",
                    )
                seen.add(key)
            if self.m.install.method == "catalog_app":
                self.port(port, f"network.ports[{idx}]")

    # -- privileges
    def privileges(self) -> None:
        pr = self.m.privileges
        reason = (self.m.privilege_reason or "").strip()
        if pr.privileged:
            self.err("privileged_forbidden", "privileges.privileged", "Privileged-modus is verboden (ook met reden).")
        for idx, cap in enumerate(pr.cap_add):
            path = f"privileges.cap_add[{idx}]"
            name = cap.upper().removeprefix("CAP_")
            if name in FORBIDDEN_CAPABILITIES:
                self.err(
                    "capability_forbidden", path, f"Capability {name} geeft (bijna) root op de NAS en is verboden."
                )
            elif name not in KNOWN_CAPABILITIES:
                self.err("capability_unknown", path, f"Onbekende capability {cap!r}.")
            elif not reason:
                self.err("capability_without_reason", path, f"Extra capability {name} zonder privilege_reason.")
        for idx, dev in enumerate(pr.devices):
            path = f"privileges.devices[{idx}]"
            for which in (dev.host_device, dev.container_device):
                if problem := device_problem(which):
                    self.err("device_forbidden", path, problem)
                    break
            else:
                if not reason:
                    self.err("device_without_reason", path, f"Apparaat {dev.host_device} zonder privilege_reason.")
        if pr.gpu != "none" and not reason:
            self.err("gpu_without_reason", "privileges.gpu", "GPU-toegang (/dev/dri) zonder privilege_reason.")

    # -- opslagdeclaraties
    def storage_decls(self) -> None:
        roles_from_params = {p.dataset_role for p in self.m.parameters if p.dataset_role}
        for idx, decl in enumerate(self.m.storage):
            path = f"storage[{idx}]"
            if problem := mount_path_problem(decl.mount):
                self.err("forbidden_mount_path", f"{path}.mount", problem)
            if (
                self.m.install.method == "catalog_app"
                and decl.kind in ("data", "media")
                and decl.required_dataset_role is None
            ):
                self.err(
                    "dataset_role_missing",
                    f"{path}.required_dataset_role",
                    f"Opslag {decl.name!r} ({decl.kind}) zonder datasetrol.",
                )
            if (
                self.m.install.method == "catalog_app"
                and decl.required_dataset_role
                and decl.required_dataset_role not in roles_from_params
            ):
                self.err(
                    "dataset_role_inconsistent",
                    f"{path}.required_dataset_role",
                    f"Geen parameter met dataset_role {decl.required_dataset_role!r} voor opslag {decl.name!r}.",
                )
        mounts: set[str] = set()
        for decl in self.m.storage:
            norm = normalize_abs_path(decl.mount) or decl.mount
            if norm in mounts:
                self.err("duplicate_name", "storage", f"Koppelpad {norm!r} komt dubbel voor.")
            mounts.add(norm)

    # -- back-up
    def backup(self) -> None:
        b = self.m.backup
        names = {s.name for s in self.m.storage}
        for idx, ds in enumerate(b.snapshot_datasets):
            if ds not in names:
                self.err("backup_unknown_dataset", f"backup.snapshot_datasets[{idx}]", f"Onbekende opslag {ds!r}.")
        for decl in self.m.storage:
            if decl.kind in ("config", "data") and decl.name not in b.snapshot_datasets:
                tmpfs = self.m.custom_app and any(
                    sb.name == decl.name and sb.type == "tmpfs" for sb in self.m.custom_app.storage
                )
                if not tmpfs:
                    self.warn(
                        "backup_dataset_not_covered",
                        "backup.snapshot_datasets",
                        f"Opslag {decl.name!r} ({decl.kind}) zit niet in de snapshotlijst.",
                    )
        if b.db_export.method == "none" and self.m.upgrade.migrations:
            self.warn(
                "backup_db_export_none",
                "backup.db_export",
                "Databasemigraties zonder consistente database-export; rollback vereist dan een snapshot-restore.",
            )
        for field in ("rpo", "rto"):
            value = getattr(b, field)
            if isinstance(value, ParamRef):
                self.ref(value, f"backup.{field}", ("duration",), field.upper())

    def healthcheck(self) -> None:
        hc = self.m.healthcheck
        if hc.port is not None and hc.port not in {p.name for p in self.m.network.ports}:
            self.err(
                "healthcheck_unknown_port",
                "healthcheck.port",
                f"Healthcheck verwijst naar onbekende poort {hc.port!r}.",
            )

    def dependencies(self) -> None:
        for idx, dep in enumerate(self.m.dependencies):
            if dep.app_id == self.m.app_id:
                self.err("dependency_self", f"dependencies[{idx}]", "Een app kan niet van zichzelf afhangen.")

    def risk_and_upgrade(self) -> None:
        for action, minimum in RISK_MINIMUM.items():
            value = getattr(self.m.risk, action)
            if _RISK_RANK[value] < _RISK_RANK[minimum]:
                self.err(
                    "risk_below_policy_minimum",
                    f"risk.{action}",
                    f"risk.{action} is {value}; het platform vereist minimaal {minimum} (contract §8).",
                )
        if self.m.upgrade.migrations and self.m.upgrade.tag_only_rollback_safe:
            self.err(
                "tag_only_rollback_with_migrations",
                "upgrade.tag_only_rollback_safe",
                "Met databasemigraties is terugzetten van alleen de tag onveilig (EX15); zet tag_only_rollback_safe op false.",
            )

    def meta(self) -> None:
        if self.m.compatibility is None:
            self.warn(
                "compatibility_missing",
                "compatibility",
                "Geen TrueNAS-compatibiliteit opgegeven; compatibiliteit wordt 'onbekend'.",
            )
        if self.m.validation_status != "unvalidated":
            self.warn(
                "validation_status_claim_ignored",
                "validation_status",
                f"Het manifest claimt {self.m.validation_status!r}; de catalogus bepaalt de status zelf.",
            )


def _ports_key(ports: list[PortBinding]) -> list[tuple[str, int, str, str]]:
    return sorted(
        (
            p.name,
            p.container_port,
            p.protocol,
            p.host_port.param if isinstance(p.host_port, ParamRef) else str(p.host_port),
        )
        for p in ports
    )


def check_manifest(manifest: Manifest) -> list[Finding]:
    """Alle semantische regels op een (schema-geldig) manifest."""
    return _Rules(manifest).run()
