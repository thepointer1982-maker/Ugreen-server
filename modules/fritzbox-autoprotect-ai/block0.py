from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
import argparse
import ipaddress
import json
import os
import time
import tomllib

import requests
from requests.auth import HTTPDigestAuth

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
TRUTHY = {"1", "true", "yes", "on", "enabled"}
REMOTE_KEYS = {
    "NewEnabled", "NewEnable", "NewRemoteAccessEnabled", "NewMyFRITZEnabled",
    "NewUSPMyFRITZEnabled", "NewPeriodicInformEnable",
}
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
    interval: int = 300
    state_dir: str = "/var/lib/aegis/autoprotect"
    fritz_url: str = "http://fritz.box:49000"
    fritz_user: str = ""
    password_env: str = "AEGIS_FRITZ_PASSWORD"
    timeout: int = 8
    verify_tls: bool = False
    max_hosts: int = 256
    max_mappings: int = 256
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
        c, f, m, g = raw.get("core", {}), raw.get("fritzbox", {}), raw.get("models", {}), raw.get("gates", {})
        cfg = Config(
            mode=c.get("mode", "observe"),
            apply_enabled=bool(c.get("apply_enabled", False)),
            min_apply_score=float(c.get("min_apply_score", 90)),
            required_stable_cycles=int(c.get("required_stable_cycles", 3)),
            interval=int(c.get("loop_interval_seconds", 300)),
            state_dir=c.get("state_dir", "/var/lib/aegis/autoprotect"),
            fritz_url=f.get("base_url", "http://fritz.box:49000"),
            fritz_user=f.get("username", ""),
            password_env=f.get("password_env", "AEGIS_FRITZ_PASSWORD"),
            timeout=int(f.get("timeout_seconds", 8)),
            verify_tls=bool(f.get("verify_tls", False)),
            max_hosts=int(f.get("max_hosts", 256)),
            max_mappings=int(f.get("max_port_mappings", 256)),
            ollama_url=m.get("ollama_url", "http://127.0.0.1:11434"),
            allow_remote_models=bool(m.get("allow_remote", False)),
            gate=float(g.get("block_0", 70)),
        )
    validate_config(cfg)
    return cfg


def validate_config(cfg: Config) -> None:
    if cfg.mode not in {"observe", "plan", "apply"}:
        raise ValueError("mode must be observe, plan or apply")
    if not 0 <= cfg.min_apply_score <= 100:
        raise ValueError("min_apply_score must be between 0 and 100")
    if cfg.required_stable_cycles < 1:
        raise ValueError("required_stable_cycles must be >= 1")
    host = (urlparse(cfg.fritz_url).hostname or "").lower()
    local = host in {"fritz.box", "localhost"}
    if not local:
        try:
            ip = ipaddress.ip_address(host)
            local = ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            local = False
    if not local:
        raise ValueError("FRITZ!Box endpoint must be local/private; WAN management is forbidden")


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
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Check:
    name: str
    weight: float
    status: str
    evidence: str
    remediation: str = ""


class HashAudit:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        last = ""
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = line
        if not last:
            return "0" * 64
        try:
            return str(json.loads(last)["entry_hash"])
        except Exception:
            return "CORRUPT"

    def append(self, record: dict[str, Any]) -> str:
        payload = {"prev_hash": self.last_hash(), "record": record}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        entry_hash = sha256(canonical.encode("utf-8")).hexdigest()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**payload, "entry_hash": entry_hash}, sort_keys=True, ensure_ascii=False) + "\n")
        return entry_hash

    def verify(self) -> bool:
        if not self.path.exists():
            return True
        previous = "0" * 64
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            item = json.loads(raw)
            payload = {"prev_hash": item["prev_hash"], "record": item["record"]}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            current = sha256(canonical.encode("utf-8")).hexdigest()
            if item["prev_hash"] != previous or item["entry_hash"] != current:
                return False
            previous = current
        return True


class FritzBox:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = cfg.fritz_url.rstrip("/") + "/"
        self.session = requests.Session()
        if cfg.fritz_user or cfg.password:
            self.session.auth = HTTPDigestAuth(cfg.fritz_user, cfg.password)

    def get_xml(self, path: str) -> ET.Element:
        response = self.session.get(
            urljoin(self.base, path.lstrip("/")),
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_tls,
        )
        response.raise_for_status()
        return ET.fromstring(response.content)

    def services(self) -> list[Service]:
        root = self.get_xml("tr64desc.xml")
        services: list[Service] = []
        for element in root.iter():
            if not element.tag.endswith("service"):
                continue
            fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in list(element)}
            if fields.get("serviceType") and fields.get("controlURL"):
                services.append(
                    Service(
                        fields["serviceType"],
                        fields.get("serviceId", ""),
                        fields["controlURL"],
                        fields.get("SCPDURL", ""),
                    )
                )
        return services

    def action_inputs(self, service: Service) -> dict[str, list[str]]:
        root = self.get_xml(service.scpd_url)
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
                result[name] = inputs
        return result

    def call(self, service: Service, action: str, arguments: dict[str, Any] | None = None) -> dict[str, str]:
        arguments = arguments or {}

        def escape(value: Any) -> str:
            return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        inner = "".join(f"<{key}>{escape(value)}</{key}>" for key, value in arguments.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service.service_type}">{inner}</u:{action}></s:Body></s:Envelope>'
        )
        response = self.session.post(
            urljoin(self.base, service.control_url.lstrip("/")),
            data=body.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service.service_type}#{action}"',
            },
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_tls,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        return {
            element.tag.rsplit("}", 1)[-1]: element.text
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].startswith("New") and element.text is not None
        }

    @staticmethod
    def find(services: list[Service], needle: str) -> list[Service]:
        lowered = needle.lower()
        return [
            service for service in services
            if lowered in service.service_type.lower() or lowered in service.service_id.lower()
        ]

    def snapshot(self) -> Snapshot:
        snap = Snapshot()
        try:
            services = self.services()
            snap.reachable = True
            snap.services = [asdict(service) for service in services]
        except Exception as exc:
            snap.errors.append(f"discovery:{type(exc).__name__}:{exc}")
            return snap

        device = self.find(services, "DeviceInfo")
        if device:
            try:
                snap.device_info = self.call(device[0], "GetInfo")
            except Exception as exc:
                snap.errors.append(f"device_info:{type(exc).__name__}:{exc}")

        hosts = self.find(services, "Hosts")
        if hosts:
            try:
                count = int(self.call(hosts[0], "GetHostNumberOfEntries").get("NewHostNumberOfEntries", "0"))
                complete = count <= self.cfg.max_hosts
                for index in range(min(count, self.cfg.max_hosts)):
                    try:
                        snap.hosts.append(self.call(hosts[0], "GetGenericHostEntry", {"NewIndex": index}))
                    except Exception:
                        complete = False
                        break
                snap.hosts_scanned = complete
                if count > self.cfg.max_hosts:
                    snap.errors.append(f"hosts:truncated:{count}>{self.cfg.max_hosts}")
            except Exception as exc:
                snap.errors.append(f"hosts:{type(exc).__name__}:{exc}")

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
                        self.call(service, "GetPortMappingNumberOfEntries").get("NewPortMappingNumberOfEntries", "0")
                    )
                    if count > self.cfg.max_mappings:
                        complete = False
                        snap.errors.append(f"mappings:truncated:{count}>{self.cfg.max_mappings}")
                    for index in range(min(count, self.cfg.max_mappings)):
                        mapping = self.call(service, "GetGenericPortMappingEntry", {"NewPortMappingIndex": index})
                        mapping["_service_type"] = service.service_type
                        snap.mappings.append(mapping)
                except Exception as exc:
                    complete = False
                    snap.errors.append(f"mappings:{type(exc).__name__}:{exc}")
        snap.mappings_scanned = bool(seen) and complete

        probes = {
            "ManagementServer": ["GetInfo"],
            "X_AVM-DE_RemoteAccess": ["GetInfo"],
            "X_AVM-DE_MyFritz": ["GetInfo"],
            "X_AVM-DE_USPController": ["GetUSPMyFRITZEnable", "GetInfo"],
        }
        for needle, actions in probes.items():
            for service in self.find(services, needle):
                try:
                    inputs = self.action_inputs(service)
                    values: dict[str, Any] = {}
                    for action in actions:
                        if action in inputs and not inputs[action]:
                            try:
                                values[action] = self.call(service, action)
                            except Exception as exc:
                                values[action] = {"error": type(exc).__name__}
                    snap.remote_probes[service.service_type] = values
                except Exception as exc:
                    snap.remote_probes[service.service_type] = {"error": type(exc).__name__}

        return snap


def flatten(data: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                result.update(flatten(value))
            else:
                result[str(key)] = str(value)
    return result


def checks_for(snapshot: Snapshot, audit_ok: bool) -> list[Check]:
    flat = flatten(snapshot.remote_probes)
    flags = {
        key: value for key, value in flat.items()
        if key in REMOTE_KEYS or any(token in key.lower() for token in ("remote", "myfritz", "periodicinform"))
    }
    enabled = {key: value for key, value in flags.items() if value.strip().lower() in TRUTHY}
    remote_status = "unknown" if not flags else ("fail" if enabled else "pass")
    mapping_status = (
        "unknown" if not snapshot.mappings_scanned
        else ("pass" if not snapshot.mappings else "fail")
    )
    inventory_status = "pass" if snapshot.hosts_scanned else ("unknown" if snapshot.reachable else "fail")

    return [
        Check("local_control_plane", 20, "pass" if snapshot.reachable else "fail", f"reachable={snapshot.reachable}"),
        Check("remote_management_surface", 20, remote_status, f"flags={flags};enabled={enabled}"),
        Check("wan_port_mappings", 15, mapping_status, f"complete={snapshot.mappings_scanned};count={len(snapshot.mappings)}"),
        Check("device_inventory", 15, inventory_status, f"complete={snapshot.hosts_scanned};count={len(snapshot.hosts)}"),
        Check("tracker_dns_policy", 10, "unknown", "planned_for_block_3"),
        Check("worker_mesh_health", 10, "unknown", "planned_for_block_4"),
        Check("tamper_evident_audit", 10, "pass" if audit_ok else "fail", f"verified={audit_ok}"),
    ]


def calculate_score(checks: list[Check], threshold: float) -> dict[str, Any]:
    total = sum(max(check.weight, 0.0) for check in checks)
    earned = 0.0
    for check in checks:
        if check.status == "pass":
            earned += check.weight
        elif check.status == "unknown":
            earned += check.weight * 0.35
    value = round(100.0 * earned / total, 2) if total else 0.0
    return {
        "score": value,
        "gate_passed": value >= threshold,
        "checks": [asdict(check) for check in checks],
    }


def rank_models(models: list[str], task: str, memory_gb: float) -> list[str]:
    hints = TASK_HINTS.get(task, TASK_HINTS["general"])
    sizes = {"70b": 70, "32b": 32, "27b": 27, "14b": 14, "13b": 13, "8b": 8, "7b": 7, "3b": 3, "1.5b": 2, "1b": 1}

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
        response = requests.get(cfg.ollama_url.rstrip("/") + "/api/tags", timeout=4)
        response.raise_for_status()
        models = [model["name"] for model in response.json().get("models", []) if model.get("name")]
    except Exception:
        models = []
    ranked = rank_models(models, task, memory_gb)
    if ranked:
        return {"provider": "ollama", "model": ranked[0], "reason": "local_first"}
    return {
        "provider": "none",
        "model": None,
        "reason": "no_local_model;remote_disabled" if not cfg.allow_remote_models else "remote_fallback_may_be_configured",
    }


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = Path(cfg.state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.audit = HashAudit(self.state / "audit.jsonl")
        self.fritz = FritzBox(cfg)

    def stable_scores(self) -> list[float]:
        path = self.state / "scores.jsonl"
        if not path.exists():
            return []
        values: list[float] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                values.append(float(json.loads(line)["score"]))
            except Exception:
                continue
        return values[-self.cfg.required_stable_cycles:]

    def mutation_gate(self, current: float) -> tuple[bool, str]:
        if self.cfg.mode != "apply" or not self.cfg.apply_enabled:
            return False, "apply_disabled"
        if current < self.cfg.min_apply_score:
            return False, "score_below_threshold"
        previous = self.stable_scores()
        if len(previous) < self.cfg.required_stable_cycles:
            return False, "insufficient_stable_cycles"
        if any(value < self.cfg.min_apply_score for value in previous):
            return False, "unstable_prior_score"
        if not self.audit.verify():
            return False, "audit_invalid"
        return True, "open"

    def once(self) -> dict[str, Any]:
        audit_before = self.audit.verify()
        snapshot = self.fritz.snapshot()
        result = calculate_score(checks_for(snapshot, audit_before), self.cfg.gate)
        allowed, reason = self.mutation_gate(result["score"])
        record = {
            "snapshot": asdict(snapshot),
            "score": result,
            "mutation_allowed": allowed,
            "mutation_reason": reason,
        }
        entry_hash = self.audit.append(record)
        with (self.state / "scores.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"score": result["score"], "entry_hash": entry_hash}, sort_keys=True) + "\n")
        (self.state / "latest.json").write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return record


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis-autoprotect")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("once")
    sub.add_parser("status")
    sub.add_parser("watch")
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
        print(json.dumps(engine.once(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "status":
        latest = Path(cfg.state_dir) / "latest.json"
        print(latest.read_text(encoding="utf-8") if latest.exists() else "{}")
        return 0 if latest.exists() else 1

    while True:
        engine.once()
        time.sleep(cfg.interval)


if __name__ == "__main__":
    raise SystemExit(main())
