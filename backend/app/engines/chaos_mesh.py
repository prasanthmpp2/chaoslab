"""Optional Chaos Mesh adapter (custom resources chaos-mesh.org/v1alpha1).

Disabled unless CHAOS_CHAOS_MESH_ENABLED=true AND a kubeconfig/in-cluster config is present. CRD availability is
discovered at runtime; a missing CRD is reported as an unverified capability.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.core.exceptions import EngineError, SafetyViolation
from app.engines.base import (
    Capability,
    EngineHealth,
    FaultEngine,
    FaultHandle,
    FaultSpec,
    FaultStatus,
    RecoveryResult,
    ResolvedTarget,
)
from app.schemas.fault import FAULT_CATALOG, validate_fault_parameters

GROUP, VERSION = "chaos-mesh.org", "v1alpha1"
_KIND = {"pod-kill": ("PodChaos", "podchaos"), "network-delay": ("NetworkChaos", "networkchaos")}
OWNER_LABEL = "chaos-platform/run-id"


def build_manifest(spec: FaultSpec) -> dict[str, Any]:
    p = validate_fault_parameters("chaos_mesh", spec.fault_type, spec.parameters).model_dump()
    t = spec.target
    if not t.k8s_namespace:
        raise SafetyViolation("target has no allowlisted namespace")
    selector = {"namespaces": [t.k8s_namespace], "labelSelectors": dict(t.k8s_labels)}
    if not selector["labelSelectors"]:
        raise SafetyViolation("refusing to target a namespace without label selectors")
    kind, _ = _KIND[spec.fault_type]
    body: dict[str, Any] = {
        "apiVersion": f"{GROUP}/{VERSION}", "kind": kind,
        "metadata": {"name": spec.resource_name, "namespace": t.k8s_namespace,
                     "labels": {OWNER_LABEL: spec.run_id, "chaos-platform/fault-id": spec.fault_id}},
        "spec": {"mode": p["mode"], "selector": selector, "duration": f"{spec.duration_seconds}s"},
    }
    if p.get("value") is not None:
        body["spec"]["value"] = p["value"]
    if spec.fault_type == "pod-kill":
        body["spec"]["action"] = "pod-kill"
    else:
        body["spec"]["action"] = "delay"
        body["spec"]["delay"] = {"latency": f"{p['latency_ms']}ms", "jitter": f"{p['jitter_ms']}ms", "correlation": "0"}
    if kind not in {"PodChaos", "NetworkChaos"}:
        raise SafetyViolation("kind not permitted")
    return body


class ChaosMeshEngine(FaultEngine):
    name = "chaos_mesh"

    def __init__(self, settings: Settings, custom_api: Any | None = None, core_api: Any | None = None,
                 ext_api: Any | None = None) -> None:
        self.s = settings
        self._custom, self._core, self._ext = custom_api, core_api, ext_api
        self._mock_objects: dict[str, dict[str, Any]] = {}

    def _configured(self) -> bool:
        return self.s.chaos_mesh_enabled

    def _is_testbed(self) -> bool:
        return self._configured() and not bool(self.s.kubeconfig_path or self.s.kube_in_cluster)

    def _apis(self):
        if not self._configured():
            raise EngineError("Chaos Mesh disabled or Kubernetes not configured", "config")
        if self._custom is None:
            from kubernetes import client, config

            if self.s.kube_in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(config_file=self.s.kubeconfig_path)
            self._custom, self._core, self._ext = (
                client.CustomObjectsApi(), client.CoreV1Api(), client.ApiextensionsV1Api())
        return self._custom, self._core, self._ext

    async def _crd_present(self, plural: str) -> bool:
        if self._is_testbed():
            return True
        _, _, ext = self._apis()
        try:
            await asyncio.to_thread(ext.read_custom_resource_definition, f"{plural}.{GROUP}")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def health_check(self) -> EngineHealth:
        if not self._configured():
            return EngineHealth(name=self.name, enabled=False, available=False,
                                detail="disabled (set CHAOS_CHAOS_MESH_ENABLED and a kubeconfig/in-cluster)")
        if self._is_testbed():
            return EngineHealth(name=self.name, enabled=True, available=True, version="v1alpha1",
                                detail="Chaos Mesh testbed mode active")
        try:
            present = [await self._crd_present(pl) for _, pl in _KIND.values()]
        except Exception as exc:  # noqa: BLE001
            return EngineHealth(name=self.name, enabled=True, available=False, detail=f"kubernetes error: {exc!r}")
        return EngineHealth(name=self.name, enabled=True, available=all(present),
                            detail="CRDs found" if all(present) else "Chaos Mesh CRDs missing; install Chaos Mesh first")

    async def capabilities(self) -> list[Capability]:
        caps = []
        for (e, t), m in FAULT_CATALOG.items():
            if e != self.name:
                continue
            kind, plural = _KIND[t]
            ok = False
            note = "not configured"
            if self._configured():
                if self._is_testbed():
                    ok = kind in self.s.chaos_mesh_allowed_kinds
                    note = "testbed ready" if ok else f"kind {kind} not permitted"
                else:
                    ok = (await self._crd_present(plural)) and kind in self.s.chaos_mesh_allowed_kinds
                    note = "" if ok else f"CRD {plural}.{GROUP} missing or kind {kind} not permitted"
            caps.append(Capability(fault_type=t, description=f"Chaos Mesh {kind}", verified=ok, note=note,
                                   parameters_schema=m.model_json_schema(by_alias=True)))
        return caps

    def validate_target(self, target: ResolvedTarget) -> None:
        if not target.k8s_namespace or not target.k8s_labels:
            raise SafetyViolation(f"service '{target.service}' lacks an allowlisted namespace and label selector")

    def validate_parameters(self, fault_type: str, parameters: dict[str, Any], target: ResolvedTarget) -> None:
        validate_fault_parameters(self.name, fault_type, parameters)
        if _KIND[fault_type][0] not in self.s.chaos_mesh_allowed_kinds:
            raise SafetyViolation(f"{_KIND[fault_type][0]} is not in CHAOS_CHAOS_MESH_ALLOWED_KINDS")

    async def inject_fault(self, spec: FaultSpec) -> FaultHandle:
        body = build_manifest(spec)
        ns, plural = spec.target.k8s_namespace, _KIND[spec.fault_type][1]
        native_id = f"{ns}/{plural}/{spec.resource_name}"
        if not self._is_testbed():
            custom, _, _ = self._apis()
            try:
                await asyncio.wait_for(asyncio.to_thread(
                    custom.create_namespaced_custom_object, GROUP, VERSION, ns, plural, body), timeout=20)
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status", None) != 409:  # 409: already created by an earlier attempt
                    raise EngineError(f"chaos mesh create failed: {exc!r}"[:500], "inject") from exc
        else:
            self._mock_objects[native_id] = body
        return FaultHandle(native_id=native_id, details={"manifest": body})

    @staticmethod
    def _parts(native_id: str) -> tuple[str, str, str]:
        ns, plural, name = native_id.split("/", 2)
        return ns, plural, name

    async def inspect_fault(self, spec: FaultSpec, handle: FaultHandle) -> FaultStatus:
        ns, plural, name = self._parts(handle.native_id)
        if self._is_testbed():
            present = handle.native_id in self._mock_objects
            return FaultStatus(present=present, active=present, finished=False,
                               detail={"conditions": {"AllInjected": "True"} if present else {}})
        custom, core, _ = self._apis()
        try:
            obj = await asyncio.to_thread(custom.get_namespaced_custom_object, GROUP, VERSION, ns, plural, name)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return FaultStatus(present=False)
            raise EngineError(f"chaos mesh inspect failed: {exc!r}"[:300], "inspect") from exc
        conds = {c.get("type"): c.get("status") for c in (obj.get("status", {}).get("conditions") or [])}
        events = await asyncio.to_thread(
            core.list_namespaced_event, ns, field_selector=f"involvedObject.name={name}")
        return FaultStatus(present=True, active=conds.get("AllInjected") == "True",
                           finished=conds.get("AllRecovered") == "True",
                           detail={"conditions": conds, "events": [f"{e.reason}: {e.message}" for e in events.items][-10:]})

    async def remove_fault(self, spec: FaultSpec, handle: FaultHandle | None) -> None:
        if handle is None:
            return
        if self._is_testbed():
            self._mock_objects.pop(handle.native_id, None)
            return
        custom, _, _ = self._apis()
        ns, plural = spec.target.k8s_namespace, _KIND[spec.fault_type][1]
        name = spec.resource_name
        try:  # only delete the object if it carries this run's owner label
            obj = await asyncio.to_thread(custom.get_namespaced_custom_object, GROUP, VERSION, ns, plural, name)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return
            raise EngineError(f"chaos mesh lookup failed: {exc!r}"[:300], "cleanup") from exc
        if obj.get("metadata", {}).get("labels", {}).get(OWNER_LABEL) != spec.run_id:
            raise SafetyViolation("resource is not owned by this run; refusing to delete")
        try:
            await asyncio.to_thread(custom.delete_namespaced_custom_object, GROUP, VERSION, ns, plural, name)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) != 404:
                raise EngineError(f"chaos mesh delete failed: {exc!r}"[:300], "cleanup") from exc

    async def verify_recovery(self, spec: FaultSpec, handle: FaultHandle | None) -> RecoveryResult:
        if handle is None:
            return RecoveryResult(recovered=False, verifiable=False, detail="no handle")
        for _ in range(15):
            st = await self.inspect_fault(spec, handle)
            if not st.present:
                return RecoveryResult(recovered=True, detail="chaos resource deleted; use application probes to confirm service health")
            await asyncio.sleep(0.5 if self._is_testbed() else 2)
        return RecoveryResult(recovered=False, detail="chaos resource still present after deletion")
