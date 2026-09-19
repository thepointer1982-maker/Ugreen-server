from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape, quoteattr
import argparse
import hmac
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import tomllib

import requests
from defusedxml import ElementTree as SafeET
from requests.auth import HTTPDigestAuth

try:
    import fcntl
except ImportError:  # pragma: no cover - production target is Linux
    fcntl = None

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
TRUTHY = {"1", "true", "yes", "on", "enabled"}
REMOTE_BOOL_KEYS = {
    "NewEnabled",
    "NewEnable",
    "NewRemoteAccessEnabled",
    "NewMyFRITZEnabled",
    "NewUSPMyFRITZEnabled",
    "NewPeriodicInformEnable",
    "NewUpgradesManaged",
}
SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "privatekey")
XML_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
SERVICE_URN_RE = re.compile(r"^urn:[A-Za-z0-9_.-]+:service:[A-Za-z0-9_.-]+:[0-9]+$")
TASK_HINTS = {
    "code": ["coder", "code", "qwen", "deepseek", "starcoder", "codellama"],
    "reason": ["reason", "deepseek-r1", "qwen", "llama"],
    "security": ["qwen", "deepseek", "llama", "mistral"],
    "general": ["qwen", "llama", "mistral", "gemma"],
}


@dataclass(slots=True)
class Config:
    mode: str = "observe"
    apply_enabled: bool = False
    min_apply_score: float = 90.0
    required_stable_cycles: int = 3
    stable_max_age_seconds: int = 1800
    interval: int = 300
    unknown_credit: float = 0.0
    state_dir: str = "/var/lib/aegis/autoprotect"
    audit_key_file: str = "/etc/aegis/autoprotect-audit.key"
    fritz_url: str = "http://fritz.box:49000"
    fritz_user: str = ""
    password_env: str = "AEGIS_FRITZ_PASSWORD"
    timeout: int = 8
    verify_tls: bool = False
    allow_insecure_tls: bool = False
    max_xml_bytes: int = 1_048_576
    max_hosts: int = 256
    max_mappings: int = 256
    max_usp_controllers: int = 16
    ollama_url: str = "http://127.0.0.1:11434"
    allow_remote_models: bool = False
    gate: float = 70.0

    @property
    def password(self) -> str:
        return os.getenv(self.password_env, "")


def load_config(path: str | None) -> Config:
    if not path:
        cfg = Config()
    else:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        c = raw.get("core", {})
        f = raw.get("fritzbox", {})
        m = raw.get("models", {})
        g = raw.get("gates", {})
        cfg = Config(
            mode=c.get("mode", "observe"),
            apply_enabled=bool(c.get("apply_enabled", False)),
            min_apply_score=float(c.get("min_apply_score", 90)),
            required_stable_cycles=int(c.get("required_stable_cycles", 3)),
            stable_max_age_seconds=int(c.get("stable_max_age_seconds", 1800)),
            interval=int(c.get("loop_interval_seconds", 300)),
            unknown_credit=float(c.get("unknown_credit", 0.0)),
            state_dir=c.get("state_dir", "/var/lib/aegis/autoprotect"),
            audit_key_file=c.get("audit_key_file", "/etc/aegis/autoprotect-audit.key"),
            fritz_url=f.get("base_url", "http://fritz.box:49000"),
            fritz_user=f.get("username", ""),
            password_env=f.get("password_env", "AEGIS_FRITZ_PASSWORD"),
            timeout=int(f.get("timeout_seconds", 8)),
            verify_tls=bool(f.get("verify_tls", False)),
            allow_insecure_tls=bool(f.get("allow_insecure_tls", False)),
            max_xml_bytes=int(f.get("max_xml_bytes", 1_048_576)),
            max_hosts=int(f.get("max_hosts", 256)),
            max_mappings=int(f.get("max_port_mappings", 256)),
            max_usp_controllers=int(f.get("max_usp_controllers", 16)),
            ollama_url=m.get("ollama_url", "http://127.0.0.1:11434"),
            allow_remote_models=bool(m.get("allow_remote", False)),
            gate=float(g.get("block_0", 70)),
        )
    validate_config(cfg)
    return cfg


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local) and not (
        ip.is_multicast or ip.is_unspecified
    )


def validate_network_url(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must use http or https")
    if not parsed.hostname:
        raise ValueError(f"{label} must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain query or fragment data")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        host = parsed.hostname.lower()
        if host not in {"fritz.box", "localhost"} and not host.endswith(".local"):
            raise ValueError(f"{label} hostname must be fritz.box, localhost, .local, or a private IP")
        return
    if not _is_private_ip(ip):
        raise ValueError(f"{label} IP must be local/private")


def assert_private_resolution(url: str, label: str) -> set[str]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"{label} has no hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"{label} hostname resolution failed") from exc
    addresses = {item[4][0].split("%", 1)[0] for item in infos}
    if not addresses:
        raise ValueError(f"{label} hostname did not resolve")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValueError(f"{label} resolved to an invalid address") from exc
        if not _is_private_ip(ip):
            raise ValueError(f"{label} resolved outside the private/local network")
    return addresses


def validate_config(cfg: Config) -> None:
    if cfg.mode not in {"observe", "plan", "apply"}:
        raise ValueError("mode must be observe, plan or apply")
    if not 0 <= cfg.min_apply_score <= 100 or not 0 <= cfg.gate <= 100:
        raise ValueError("scores must be between 0 and 100")
    if cfg.required_stable_cycles < 1:
        raise ValueError("required_stable_cycles must be >= 1")
    if cfg.stable_max_age_seconds < cfg.interval:
        raise ValueError("stable_max_age_seconds must be >= loop interval")
    if not 0 <= cfg.unknown_credit <= 1:
        raise ValueError("unknown_credit must be between 0 and 1")
    if cfg.timeout < 1 or cfg.timeout > 60:
        raise ValueError("timeout_seconds must be between 1 and 60")
    for value, name, maximum in (
        (cfg.max_hosts, "max_hosts", 4096),
        (cfg.max_mappings, "max_port_mappings", 4096),
        (cfg.max_usp_controllers, "max_usp_controllers", 256),
        (cfg.max_xml_bytes, "max_xml_bytes", 16 * 1024 * 1024),
    ):
        if value < 1 or value > maximum:
            raise ValueError(f"{name} outside allowed range")
    validate_network_url(cfg.fritz_url, "FRITZ!Box endpoint")
    validate_network_url(cfg.ollama_url, "Ollama endpoint")
    if urlparse(cfg.fritz_url).scheme == "https" and not cfg.verify_tls and not cfg.allow_insecure_tls:
        raise ValueError("HTTPS with disabled certificate verification requires allow_insecure_tls=true")


def config_fingerprint(cfg: Config) -> str:
    data = asdict(cfg)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Service:
    service_type: str
    service_id: str
    control_url: str
    scpd_url: str


@dataclass(slots=True)
class Snapshot:
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reachable: bool = False
    services: list[dict[str, Any]] = field(default_factory=list)
    device_info: dict[str, Any] = field(default_factory=dict)
    hosts: list[dict[str, Any]] = field(default_factory=list)
    hosts_scanned: bool = False
    mappings: list[dict[str, Any]] = field(default_factory=list)
    mappings_scanned: bool = False
    remote_probes: dict[str, Any] = field(default_factory=dict)
    remote_scan_complete: bool = False
    remote_services_seen: int = 0
    usp_controllers: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Check:
    name: str
    weight: float
    status: str
    evidence: str
    remediation: str = ""
    critical: bool = False


def _safe_mode(path: Path, mode: int) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing symlink path: {path}")
    os.chmod(path, mode)


def secure_state_dir(path: str | Path) -> Path:
    state = Path(path)
    if state.exists() and (state.is_symlink() or not state.is_dir()):
        raise RuntimeError("state_dir must be a real directory, not a symlink or file")
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    _safe_mode(state, 0o700)
    return state


def _secure_open_append(path: Path):
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"refusing symlink file: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def secure_atomic_write(path: Path, text: str) -> None:
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"refusing symlink file: {path}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_audit_key(path: str) -> bytes | None:
    key_path = Path(path)
    if not key_path.exists():
        return None
    if key_path.is_symlink() or not key_path.is_file():
        raise RuntimeError("audit key path must be a regular file")
    mode = key_path.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError("audit key file must not be group/world accessible")
    key = key_path.read_bytes().strip()
    if len(key) < 32:
        raise RuntimeError("audit key must contain at least 32 bytes")
    return key


class HashAudit:
    def __init__(self, path: Path, key: bytes | None = None):
        self.path = path
        self.key = key
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def integrity_mode(self) -> str:
        return "hmac-sha256" if self.key else "sha256"

    def _digest(self, canonical: bytes, integrity: str) -> str | None:
        if integrity == "sha256":
            return sha256(canonical).hexdigest()
        if integrity == "hmac-sha256" and self.key:
            return hmac.new(self.key, canonical, sha256).hexdigest()
        return None

    def last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        if self.path.is_symlink():
            return "CORRUPT"
        last = ""
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        last = line
            if not last:
                return "0" * 64
            return str(json.loads(last)["entry_hash"])
        except Exception:
            return "CORRUPT"

    def append(self, record: dict[str, Any]) -> str:
        if self.path.exists() and not self.verify():
            raise RuntimeError("audit chain cannot be verified; refusing append")
        previous = self.last_hash()
        if previous == "CORRUPT":
            raise RuntimeError("audit chain is corrupt; refusing append")
        integrity = self.integrity_mode
        payload = {"prev_hash": previous, "integrity": integrity, "record": record}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        entry_hash = self._digest(canonical, integrity)
        if not entry_hash:
            raise RuntimeError("unable to calculate audit digest")
        with _secure_open_append(self.path) as fh:
            fh.write(json.dumps({**payload, "entry_hash": entry_hash}, sort_keys=True, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry_hash

    def _verify_item(self, item: dict[str, Any], previous: str) -> tuple[bool, str]:
        integrity = str(item.get("integrity", "legacy-sha256"))
        if integrity == "legacy-sha256":
            payload = {"prev_hash": item.get("prev_hash"), "record": item.get("record")}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            expected = sha256(canonical).hexdigest()
        else:
            payload = {
                "prev_hash": item.get("prev_hash"),
                "integrity": integrity,
                "record": item.get("record"),
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            expected = self._digest(canonical, integrity)
        valid = bool(expected) and item.get("prev_hash") == previous and hmac.compare_digest(
            str(item.get("entry_hash", "")), str(expected)
        )
        return valid, str(item.get("entry_hash", ""))

    def verify(self) -> bool:
        if not self.path.exists():
            return True
        if self.path.is_symlink():
            return False
        previous = "0" * 64
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    item = json.loads(raw)
                    valid, current = self._verify_item(item, previous)
                    if not valid:
                        return False
                    previous = current
            return True
        except Exception:
            return False

    def records(self) -> list[dict[str, Any]]:
        if not self.verify() or not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                if raw.strip():
                    records.append(json.loads(raw).get("record", {}))
        return records


class FritzBox:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = cfg.fritz_url.rstrip("/") + "/"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "AEGIS-AutoProtect/0.2"})
        if cfg.fritz_user or cfg.password:
            self.session.auth = HTTPDigestAuth(cfg.fritz_user, cfg.password)

    def _service_url(self, advertised: str) -> str:
        if not advertised or "\\" in advertised or "\x00" in advertised:
            raise ValueError("invalid service path")
        parsed = urlparse(advertised)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("service descriptor attempted an absolute/external URL")
        parts = [part for part in parsed.path.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise ValueError("service descriptor contains path traversal")
        target = urljoin(self.base, parsed.path.lstrip("/"))
        base = urlparse(self.base)
        dest = urlparse(target)
        if (dest.scheme, dest.hostname, dest.port) != (base.scheme, base.hostname, base.port):
            raise ValueError("service descriptor escaped configured FRITZ!Box origin")
        return target

    def _read_bounded(self, response: requests.Response) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > self.cfg.max_xml_bytes:
                    raise ValueError("XML response exceeds configured size limit")
            except ValueError as exc:
                if "exceeds" in str(exc):
                    raise
        data = bytearray()
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > self.cfg.max_xml_bytes:
                raise ValueError("XML response exceeds configured size limit")
        return bytes(data)

    def _request_xml(self, method: str, url: str, **kwargs: Any):
        assert_private_resolution(url, "FRITZ!Box endpoint")
        response = self.session.request(
            method,
            url,
            timeout=(min(self.cfg.timeout, 5), self.cfg.timeout),
            verify=self.cfg.verify_tls,
            allow_redirects=False,
            stream=True,
            **kwargs,
        )
        if 300 <= response.status_code < 400:
            raise requests.HTTPError("redirects are forbidden for FRITZ!Box control", response=response)
        response.raise_for_status()
        return SafeET.fromstring(self._read_bounded(response))

    def get_xml(self, path: str):
        return self._request_xml("GET", self._service_url(path))

    def services(self) -> list[Service]:
        root = self.get_xml("tr64desc.xml")
        services: list[Service] = []
        for element in root.iter():
            if not element.tag.endswith("service"):
                continue
            fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in list(element)}
            service_type = fields.get("serviceType", "")
            control_url = fields.get("controlURL", "")
            scpd_url = fields.get("SCPDURL", "")
            if not service_type or not control_url or not scpd_url:
                continue
            if not SERVICE_URN_RE.fullmatch(service_type):
                raise ValueError("invalid service type in FRITZ!Box descriptor")
            self._service_url(control_url)
            self._service_url(scpd_url)
            services.append(Service(service_type, fields.get("serviceId", ""), control_url, scpd_url))
        return services

    def action_inputs(self, service: Service) -> dict[str, list[str]]:
        root = self._request_xml("GET", self._service_url(service.scpd_url))
        result: dict[str, list[str]] = {}
        for action in [element for element in root.iter() if element.tag.endswith("action")]:
            name = ""
            inputs: list[str] = []
            for child in list(action):
                if child.tag.endswith("name"):
                    name = (child.text or "").strip()
                elif child.tag.endswith("argumentList"):
                    for argument in list(child):
                        values = {item.tag.rsplit("}", 1)[-1]: (item.text or "").strip() for item in list(argument)}
                        if values.get("direction", "").lower() == "in" and values.get("name"):
                            inputs.append(values["name"])
            if name:
                if not XML_NAME_RE.fullmatch(name) or any(not XML_NAME_RE.fullmatch(item) for item in inputs):
                    raise ValueError("invalid action or argument name in SCPD")
                result[name] = inputs
        return result

    def call(self, service: Service, action: str, arguments: dict[str, Any] | None = None) -> dict[str, str]:
        arguments = arguments or {}
        if not SERVICE_URN_RE.fullmatch(service.service_type) or not XML_NAME_RE.fullmatch(action):
            raise ValueError("invalid SOAP service/action name")
        if any(not XML_NAME_RE.fullmatch(str(key)) for key in arguments):
            raise ValueError("invalid SOAP argument name")
        inner = "".join(f"<{key}>{escape(str(value))}</{key}>" for key, value in arguments.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s={quoteattr(SOAP_ENV)} s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u={quoteattr(service.service_type)}>{inner}</u:{action}></s:Body></s:Envelope>'
        )
        root = self._request_xml(
            "POST",
            self._service_url(service.control_url),
            data=body.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service.service_type}#{action}"',
            },
        )
        return {
            element.tag.rsplit("}", 1)[-1]: element.text
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].startswith("New") and element.text is not None
        }

    @staticmethod
    def find(services: list[Service], needle: str) -> list[Service]:
        lowered = needle.lower()
        return [
            service
            for service in services
            if lowered in service.service_type.lower() or lowered in service.service_id.lower()
        ]

    def _safe_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in data.items():
            lowered = key.lower()
            safe[key] = "<redacted>" if any(part in lowered for part in SENSITIVE_KEY_PARTS) else value
        return safe

    def _probe_remote(self, services: list[Service], snap: Snapshot) -> None:
        probes = {
            "ManagementServer": ["GetInfo"],
            "X_AVM-DE_RemoteAccess": ["GetInfo"],
            "X_AVM-DE_MyFritz": ["GetInfo"],
            "X_AVM-DE_USPController": ["GetUSPMyFRITZEnable", "GetInfo"],
        }
        complete = True
        matched_ids: set[str] = set()
        for needle, candidates in probes.items():
            for service in self.find(services, needle):
                identity = service.service_id or service.service_type
                if identity in matched_ids:
                    continue
                matched_ids.add(identity)
                snap.remote_services_seen += 1
                values: dict[str, Any] = {}
                successes = 0
                try:
                    inputs = self.action_inputs(service)
                    for action in candidates:
                        if action in inputs and not inputs[action]:
                            try:
                                values[action] = self._safe_fields(self.call(service, action))
                                successes += 1
                            except Exception as exc:
                                complete = False
                                values[action] = {"error": type(exc).__name__}
                    if successes == 0:
                        complete = False
                except Exception as exc:
                    complete = False
                    values = {"error": type(exc).__name__}
                snap.remote_probes[service.service_type] = values

        for service in self.find(services, "X_AVM-DE_USPController"):
            try:
                inputs = self.action_inputs(service)
                count_action = next(
                    (
                        name
                        for name in ("GetUSPControllerNumberOfEntries", "GetUSPContollerNumberOfEntries")
                        if name in inputs and not inputs[name]
                    ),
                    None,
                )
                if not count_action or "GetUSPControllerByIndex" not in inputs:
                    complete = False
                    continue
                count_data = self.call(service, count_action)
                raw_count = count_data.get(
                    "NewUSPControllerNumberOfEntries",
                    count_data.get("NewUSPContollerNumberOfEntries", "0"),
                )
                count = int(raw_count)
                if count > self.cfg.max_usp_controllers:
                    complete = False
                    snap.errors.append(f"usp_controllers:truncated:{count}>{self.cfg.max_usp_controllers}")
                for index in range(min(count, self.cfg.max_usp_controllers)):
                    item = self.call(service, "GetUSPControllerByIndex", {"NewIndex": index})
                    snap.usp_controllers.append(self._safe_fields(item))
            except Exception as exc:
                complete = False
                snap.errors.append(f"usp_controllers:{type(exc).__name__}")
        snap.remote_scan_complete = complete and snap.remote_services_seen > 0
        if snap.remote_services_seen == 0:
            snap.errors.append("remote_services:none")

    def snapshot(self) -> Snapshot:
        snap = Snapshot()
        try:
            services = self.services()
            snap.reachable = True
            snap.services = [asdict(service) for service in services]
        except Exception as exc:
            snap.errors.append(f"discovery:{type(exc).__name__}")
            return snap

        device = self.find(services, "DeviceInfo")
        if device:
            try:
                snap.device_info = self._safe_fields(self.call(device[0], "GetInfo"))
            except Exception as exc:
                snap.errors.append(f"device_info:{type(exc).__name__}")

        hosts = self.find(services, "Hosts")
        if hosts:
            try:
                count = int(self.call(hosts[0], "GetHostNumberOfEntries").get("NewHostNumberOfEntries", "0"))
                complete = count <= self.cfg.max_hosts
                for index in range(min(count, self.cfg.max_hosts)):
                    try:
                        snap.hosts.append(
                            self._safe_fields(
                                self.call(hosts[0], "GetGenericHostEntry", {"NewIndex": index})
                            )
                        )
                    except Exception:
                        complete = False
                        break
                snap.hosts_scanned = complete
                if count > self.cfg.max_hosts:
                    snap.errors.append(f"hosts:truncated:{count}>{self.cfg.max_hosts}")
            except Exception as exc:
                snap.errors.append(f"hosts:{type(exc).__name__}")

        seen: set[str] = set()
        complete = True
        for needle in ("WANIPConnection", "WANPPPConnection"):
            for service in self.find(services, needle):
                identity = service.service_id or service.service_type
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    inputs = self.action_inputs(service)
                    if "GetPortMappingNumberOfEntries" not in inputs or inputs["GetPortMappingNumberOfEntries"]:
                        complete = False
                        continue
                    count = int(
                        self.call(service, "GetPortMappingNumberOfEntries").get(
                            "NewPortMappingNumberOfEntries", "0"
                        )
                    )
                    if count > self.cfg.max_mappings:
                        complete = False
                        snap.errors.append(f"mappings:truncated:{count}>{self.cfg.max_mappings}")
                    for index in range(min(count, self.cfg.max_mappings)):
                        mapping = self._safe_fields(
                            self.call(
                                service,
                                "GetGenericPortMappingEntry",
                                {"NewPortMappingIndex": index},
                            )
                        )
                        mapping["_service_type"] = service.service_type
                        snap.mappings.append(mapping)
                except Exception as exc:
                    complete = False
                    snap.errors.append(f"mappings:{type(exc).__name__}")
        snap.mappings_scanned = bool(seen) and complete

        self._probe_remote(services, snap)
        return snap


def flatten_paths(data: Any, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                result.update(flatten_paths(value, path))
            else:
                result[path] = str(value)
    return result


def remote_exposure(snapshot: Snapshot) -> dict[str, str]:
    exposed: dict[str, str] = {}
    for path, value in flatten_paths(snapshot.remote_probes).items():
        key = path.rsplit(".", 1)[-1]
        normalized = value.strip().lower()
        if key in REMOTE_BOOL_KEYS and normalized in TRUTHY:
            exposed[path] = value
        if "ManagementServer" in path and key == "NewURL" and value.strip():
            exposed[path] = "configured"
    for index, controller in enumerate(snapshot.usp_controllers):
        if str(controller.get("NewEnable", "")).strip().lower() in TRUTHY:
            exposed[f"usp_controllers[{index}].NewEnable"] = "enabled"
    return exposed


def checks_for(snapshot: Snapshot, audit_ok: bool) -> list[Check]:
    exposed = remote_exposure(snapshot)
    remote_status = "unknown" if not snapshot.remote_scan_complete else ("fail" if exposed else "pass")
    mapping_status = (
        "unknown" if not snapshot.mappings_scanned else ("pass" if not snapshot.mappings else "fail")
    )
    inventory_status = (
        "pass" if snapshot.hosts_scanned else ("unknown" if snapshot.reachable else "fail")
    )
    return [
        Check(
            "local_control_plane",
            20,
            "pass" if snapshot.reachable else "fail",
            f"reachable={snapshot.reachable}",
            critical=True,
        ),
        Check(
            "remote_management_surface",
            20,
            remote_status,
            f"complete={snapshot.remote_scan_complete};services={snapshot.remote_services_seen};exposures={len(exposed)}",
            critical=True,
        ),
        Check(
            "wan_port_mappings",
            15,
            mapping_status,
            f"complete={snapshot.mappings_scanned};count={len(snapshot.mappings)}",
            critical=True,
        ),
        Check(
            "device_inventory",
            15,
            inventory_status,
            f"complete={snapshot.hosts_scanned};count={len(snapshot.hosts)}",
            critical=True,
        ),
        Check("tracker_dns_policy", 10, "unknown", "planned_for_block_3"),
        Check("worker_mesh_health", 10, "unknown", "planned_for_block_4"),
        Check(
            "audit_chain",
            10,
            "pass" if audit_ok else "fail",
            f"verified={audit_ok}",
            critical=True,
        ),
    ]


def calculate_score(
    checks: list[Check], threshold: float, unknown_credit: float = 0.0
) -> dict[str, Any]:
    total = sum(max(check.weight, 0.0) for check in checks)
    earned = 0.0
    blockers: list[str] = []
    for check in checks:
        if check.status not in {"pass", "fail", "unknown"}:
            raise ValueError(f"invalid check status: {check.status}")
        if check.status == "pass":
            earned += check.weight
        elif check.status == "unknown":
            earned += check.weight * unknown_credit
        if check.critical and check.status != "pass":
            blockers.append(check.name)
    value = round(100.0 * earned / total, 2) if total else 0.0
    return {
        "score": value,
        "gate_passed": value >= threshold and not blockers,
        "critical_blockers": blockers,
        "checks": [asdict(check) for check in checks],
    }


def rank_models(models: list[str], task: str, memory_gb: float) -> list[str]:
    hints = TASK_HINTS.get(task, TASK_HINTS["general"])
    sizes = {
        "70b": 70,
        "32b": 32,
        "27b": 27,
        "14b": 14,
        "13b": 13,
        "8b": 8,
        "7b": 7,
        "3b": 3,
        "1.5b": 2,
        "1b": 1,
    }

    def points(name: str) -> tuple[int, str]:
        lowered = name.lower()
        score = sum(25 - index * 3 for index, hint in enumerate(hints) if hint in lowered)
        rough = next((value for key, value in sizes.items() if key in lowered), 0)
        comfortable = max(1.0, memory_gb * 0.9)
        if rough > comfortable * 2:
            score -= 100
        elif rough > comfortable:
            score -= 20
        return score, name

    return sorted(models, key=points, reverse=True)


def route_model(cfg: Config, task: str, memory_gb: float) -> dict[str, Any]:
    try:
        if not cfg.allow_remote_models:
            assert_private_resolution(cfg.ollama_url, "Ollama endpoint")
        session = requests.Session()
        session.trust_env = False
        response = session.get(
            cfg.ollama_url.rstrip("/") + "/api/tags",
            timeout=(3, 4),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise requests.HTTPError("redirects are forbidden for Ollama routing", response=response)
        response.raise_for_status()
        if len(response.content) > 1_048_576:
            raise ValueError("Ollama model list too large")
        models = [
            model["name"]
            for model in response.json().get("models", [])
            if model.get("name")
        ]
    except Exception:
        models = []
    ranked = rank_models(models, task, memory_gb)
    if ranked:
        return {"provider": "ollama", "model": ranked[0], "reason": "local_first"}
    return {
        "provider": "none",
        "model": None,
        "reason": "no_usable_local_model" if not cfg.allow_remote_models else "no_usable_model",
    }


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def public_summary(record: dict[str, Any]) -> dict[str, Any]:
    snapshot = record.get("snapshot", {})
    score = record.get("score", {})
    return {
        "captured_at": snapshot.get("captured_at"),
        "score": score.get("score"),
        "gate_passed": bool(score.get("gate_passed", False)),
        "critical_blockers": list(score.get("critical_blockers", [])),
        "mutation_allowed": bool(record.get("mutation_allowed", False)),
        "mutation_reason": record.get("mutation_reason", "unknown"),
        "audit_integrity": record.get("audit_integrity", "unknown"),
        "fritzbox_reachable": bool(snapshot.get("reachable", False)),
        "hosts_count": len(snapshot.get("hosts", [])),
        "hosts_scan_complete": bool(snapshot.get("hosts_scanned", False)),
        "port_mappings_count": len(snapshot.get("mappings", [])),
        "port_mappings_scan_complete": bool(snapshot.get("mappings_scanned", False)),
        "remote_scan_complete": bool(snapshot.get("remote_scan_complete", False)),
        "remote_services_seen": int(snapshot.get("remote_services_seen", 0)),
        "usp_controller_count": len(snapshot.get("usp_controllers", [])),
        "errors_count": len(snapshot.get("errors", [])),
    }


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = secure_state_dir(cfg.state_dir)
        self.audit = HashAudit(self.state / "audit.jsonl", load_audit_key(cfg.audit_key_file))
        self.fritz = FritzBox(cfg)
        self.fingerprint = config_fingerprint(cfg)

    def _recent_stable_scores(self) -> list[float]:
        if not self.audit.verify():
            return []
        now = datetime.now(timezone.utc)
        scores: list[float] = []
        for record in reversed(self.audit.records()):
            if record.get("config_fingerprint") != self.fingerprint:
                break
            timestamp = record.get("captured_at")
            try:
                captured = datetime.fromisoformat(str(timestamp))
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
            except Exception:
                break
            if (now - captured).total_seconds() > self.cfg.stable_max_age_seconds:
                break
            score = record.get("score", {})
            if not score.get("gate_passed", False):
                break
            try:
                scores.append(float(score["score"]))
            except Exception:
                break
            if len(scores) >= max(0, self.cfg.required_stable_cycles - 1):
                break
        return scores

    def mutation_gate(self, current: float, gate_passed: bool = True) -> tuple[bool, str]:
        if self.cfg.mode != "apply" or not self.cfg.apply_enabled:
            return False, "apply_disabled"
        if not gate_passed:
            return False, "security_gate_blocked"
        if current < self.cfg.min_apply_score:
            return False, "score_below_threshold"
        if self.audit.integrity_mode != "hmac-sha256":
            return False, "audit_not_keyed"
        if not self.audit.verify():
            return False, "audit_invalid"
        needed = max(0, self.cfg.required_stable_cycles - 1)
        previous = self._recent_stable_scores()
        if len(previous) < needed:
            return False, "insufficient_stable_cycles"
        if any(value < self.cfg.min_apply_score for value in previous[:needed]):
            return False, "unstable_prior_score"
        return True, "open"

    def _latest_binding_valid(self, record: dict[str, Any]) -> bool:
        records = self.audit.records()
        if not records:
            return False
        anchor = records[-1]
        try:
            return (
                anchor.get("captured_at") == record.get("captured_at")
                and anchor.get("config_fingerprint") == record.get("config_fingerprint")
                and anchor.get("snapshot_digest") == snapshot_digest(record.get("snapshot", {}))
                and anchor.get("score") == record.get("score")
            )
        except Exception:
            return False

    def latest(self) -> dict[str, Any] | None:
        path = self.state / "latest.json"
        if not path.exists() or path.is_symlink():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return record if self._latest_binding_valid(record) else None

    def current_mutation_gate(self) -> tuple[bool, str]:
        latest = self.latest()
        if not latest:
            return False, "no_verified_cycle"
        score = latest.get("score", {})
        try:
            value = float(score.get("score"))
        except Exception:
            return False, "invalid_latest_score"
        return self.mutation_gate(value, bool(score.get("gate_passed", False)))

    def once(self) -> dict[str, Any]:
        with exclusive_lock(self.state / ".cycle.lock"):
            audit_before = self.audit.verify()
            snapshot = self.fritz.snapshot()
            result = calculate_score(
                checks_for(snapshot, audit_before),
                self.cfg.gate,
                unknown_credit=self.cfg.unknown_credit,
            )
            allowed, reason = self.mutation_gate(result["score"], result["gate_passed"])
            snapshot_data = asdict(snapshot)
            record = {
                "schema_version": 2,
                "captured_at": snapshot.captured_at,
                "config_fingerprint": self.fingerprint,
                "snapshot": snapshot_data,
                "score": result,
                "audit_integrity": self.audit.integrity_mode,
                "mutation_allowed": allowed,
                "mutation_reason": reason,
            }
            audit_record = {
                "schema_version": 2,
                "captured_at": snapshot.captured_at,
                "config_fingerprint": self.fingerprint,
                "snapshot_digest": snapshot_digest(snapshot_data),
                "score": result,
                "audit_integrity": self.audit.integrity_mode,
                "mutation_allowed": allowed,
                "mutation_reason": reason,
            }
            entry_hash = self.audit.append(audit_record)
            with _secure_open_append(self.state / "scores.jsonl") as fh:
                fh.write(
                    json.dumps(
                        {
                            "captured_at": snapshot.captured_at,
                            "score": result["score"],
                            "gate_passed": result["gate_passed"],
                            "config_fingerprint": self.fingerprint,
                            "entry_hash": entry_hash,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                fh.flush()
                os.fsync(fh.fileno())
            secure_atomic_write(
                self.state / "latest.json",
                json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            )
            return record

    def self_check(self) -> dict[str, Any]:
        mode = self.state.stat().st_mode & 0o777
        latest = self.state / "latest.json"
        latest_mode = (
            latest.stat().st_mode & 0o777
            if latest.exists() and not latest.is_symlink()
            else None
        )
        latest_record = None
        latest_path = self.state / "latest.json"
        if latest_path.exists() and not latest_path.is_symlink():
            try:
                latest_record = json.loads(latest_path.read_text(encoding="utf-8"))
            except Exception:
                latest_record = None
        return {
            "config_valid": True,
            "state_dir_mode": oct(mode),
            "state_dir_private": mode & 0o077 == 0,
            "latest_mode": oct(latest_mode) if latest_mode is not None else None,
            "latest_private": latest_mode is None or latest_mode & 0o077 == 0,
            "audit_valid": self.audit.verify(),
            "audit_integrity": self.audit.integrity_mode,
            "latest_bound_to_audit": bool(latest_record) and self._latest_binding_valid(latest_record),
            "apply_enabled": self.cfg.mode == "apply" and self.cfg.apply_enabled,
            "remote_models_enabled": self.cfg.allow_remote_models,
        }


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis-autoprotect")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("once")
    sub.add_parser("status")
    sub.add_parser("watch")
    sub.add_parser("self-check")
    sub.add_parser("verify-audit")
    route = sub.add_parser("route-model")
    route.add_argument("--task", default="general", choices=list(TASK_HINTS))
    route.add_argument("--memory-gb", type=float, default=8.0)
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "route-model":
        print(json.dumps(route_model(cfg, args.task, args.memory_gb), indent=2))
        return 0

    engine = Engine(cfg)
    if args.command == "once":
        record = engine.once()
        print(json.dumps(public_summary(record), indent=2, ensure_ascii=False))
        return 0
    if args.command == "status":
        latest = engine.latest()
        print(json.dumps(public_summary(latest), indent=2) if latest else "{}")
        return 0 if latest else 1
    if args.command == "self-check":
        print(json.dumps(engine.self_check(), indent=2, sort_keys=True))
        return 0
    if args.command == "verify-audit":
        ok = engine.audit.verify()
        print(json.dumps({"audit_valid": ok, "integrity": engine.audit.integrity_mode}))
        return 0 if ok else 20

    while True:
        record = engine.once()
        print(json.dumps(public_summary(record), sort_keys=True), flush=True)
        time.sleep(cfg.interval)


if __name__ == "__main__":
    raise SystemExit(main())
