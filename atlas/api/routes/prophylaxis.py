"""HTTP boundary for explicit Profylaxia checks and local history."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from fastapi.responses import FileResponse

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator
from atlas.api.schemas.prophylaxis import ProphylaxisDeviceResponse
from atlas.prophylaxis.models import StoredCPUCheckResult
from atlas.prophylaxis.run_models import ProphylaxisRun
from atlas.prophylaxis.run_store import RunNotFoundError, RunStoreError
from atlas.prophylaxis.store import ResultStoreError
from atlas.services.errors import DependencyError


router = APIRouter(prefix="/prophylaxis", tags=["prophylaxis"])


def _report(request: Request, run_id: UUID, artifact: str) -> dict:
    try:
        return request.app.state.dependencies.prophylaxis_runs.store.read_site_report(
            run_id, artifact
        )
    except RunNotFoundError:
        raise AtlasError(404, "prophylaxis_report_not_found", "The report does not exist") from None
    except RunStoreError:
        raise AtlasError(500, "prophylaxis_run_store_unavailable", "The report is unavailable") from None


def _report_device(payload: dict, device_id: int) -> dict:
    for report in payload.get("devices", []):
        identity = report.get("device") if isinstance(report, dict) else None
        if isinstance(identity, dict) and identity.get("netbox_id") == device_id:
            return report
    raise AtlasError(404, "prophylaxis_device_report_not_found", "The device report does not exist")


def _report_summary(payload: dict) -> dict:
    devices = payload.get("devices", [])
    counters = {
        "devices_total": len(devices), "devices_successful": 0,
        "devices_partial": 0, "devices_failed": 0,
        "commands_attempted": 0, "commands_successful": 0, "commands_unsupported": 0,
        "commands_failed": 0, "parsing_successful": 0,
        "parsing_failed": 0, "parsing_unavailable": 0,
    }
    summaries = []
    for report in devices:
        collection = report.get("collection") or {}
        outputs = report.get("outputs") or {}
        collection_status = collection.get("status")
        if collection_status == "completed":
            counters["devices_successful"] += 1
        elif collection_status == "failed":
            counters["devices_failed"] += 1
        else:
            counters["devices_partial"] += 1
        counters["commands_attempted"] += int(
            collection.get("attempted") or collection.get("total_commands") or 0
        )
        counters["commands_successful"] += int(collection.get("successful") or 0)
        counters["commands_unsupported"] += int(collection.get("unsupported") or 0)
        counters["commands_failed"] += sum(int(collection.get(key) or 0) for key in ("permission_denied", "timeout", "error"))
        counters["parsing_successful"] += sum(item.get("parsing_status") == "success" for item in outputs.values())
        counters["parsing_failed"] += sum(item.get("parsing_status") in {"parse_error", "empty_result"} for item in outputs.values())
        counters["parsing_unavailable"] += sum(item.get("parsing_status") == "no_parser" for item in outputs.values())
        identity = report.get("device") or {}
        summaries.append({
            "netbox_id": identity.get("netbox_id"),
            "hostname": identity.get("hostname"),
            "management_ip": identity.get("management_ip"),
            "platform": identity.get("platform"),
            "device_type": identity.get("device_type"),
            "collection_status": collection.get("status"),
            "commands": len(outputs) + len(report.get("ignored_commands") or []),
        })
    return {
        "schema_version": payload.get("schema_version"),
        "run_id": payload.get("run_id"),
        "tenant": payload.get("tenant"), "site": payload.get("site"),
        "started_at": payload.get("started_at"), "generated_at": payload.get("generated_at"),
        **counters, "devices": summaries,
    }


def run_authorized(request: Request) -> None:
    require_operator(request, scope="prophylaxis")


def _dependency_error(exc: DependencyError) -> AtlasError:
    code = (
        "netbox_authentication_failed"
        if exc.reason == "authentication_failed"
        else "netbox_unavailable"
    )
    return AtlasError(503, code, "NetBox is unavailable")


@router.get(
    "/devices",
    response_model=list[ProphylaxisDeviceResponse],
    status_code=status.HTTP_200_OK,
    summary="List devices eligible for the CPU utilization check",
)
def prophylaxis_devices(request: Request) -> list[ProphylaxisDeviceResponse]:
    try:
        devices = request.app.state.dependencies.prophylaxis.list_devices()
    except DependencyError as exc:
        raise _dependency_error(exc) from None
    return [
        ProphylaxisDeviceResponse(
            id=item.id,
            name=item.name,
            ip=item.ip,
            platform=item.platform,
        )
        for item in devices
    ]


@router.post(
    "/devices/{device_id}/checks/cpu",
    response_model=StoredCPUCheckResult,
    status_code=status.HTTP_200_OK,
    summary="Run and persist one CPU utilization check",
)
def run_cpu_check(device_id: int, request: Request) -> StoredCPUCheckResult:
    try:
        return request.app.state.dependencies.prophylaxis.run_cpu_check(device_id)
    except ResultStoreError:
        raise AtlasError(
            500,
            "result_store_unavailable",
            "The Profylaxia result could not be stored",
        ) from None


@router.get(
    "/results",
    response_model=list[StoredCPUCheckResult],
    status_code=status.HTTP_200_OK,
    summary="List recent locally stored Profylaxia results",
)
def prophylaxis_results(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[StoredCPUCheckResult]:
    try:
        return request.app.state.dependencies.prophylaxis.result_history(limit)
    except ResultStoreError:
        raise AtlasError(
            500,
            "result_store_unavailable",
            "The Profylaxia result history is unavailable",
        ) from None


@router.post(
    "/runs",
    response_model=ProphylaxisRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Launch checks selected in NetBox",
    dependencies=[Depends(run_authorized)],
)
def launch_device_facts_run(
    request: Request,
    background: BackgroundTasks,
) -> ProphylaxisRun:
    service = request.app.state.dependencies.prophylaxis_runs
    try:
        run = service.submit()
    except RunStoreError:
        raise AtlasError(
            500,
            "prophylaxis_run_store_unavailable",
            "The Profylaxia run could not be created",
        ) from None
    background.add_task(service.run, run.run_id)
    return run


@router.get(
    "/runs",
    response_model=list[ProphylaxisRun],
    dependencies=[Depends(run_authorized)],
    summary="List recent Profylaxia collection runs",
)
def list_device_facts_runs(
    request: Request, limit: int = Query(default=20, ge=1, le=100),
) -> tuple[ProphylaxisRun, ...]:
    try:
        return request.app.state.dependencies.prophylaxis_runs.list_runs(limit)
    except RunStoreError:
        raise AtlasError(500, "prophylaxis_run_store_unavailable", "The run history is unavailable") from None


@router.get(
    "/runs/{run_id}",
    response_model=ProphylaxisRun,
    status_code=status.HTTP_200_OK,
    summary="Read selected-check execution status and summary",
    dependencies=[Depends(run_authorized)],
)
def device_facts_run(run_id: UUID, request: Request) -> ProphylaxisRun:
    try:
        return request.app.state.dependencies.prophylaxis_runs.get(run_id)
    except RunNotFoundError:
        raise AtlasError(
            404,
            "prophylaxis_run_not_found",
            "The Profylaxia run does not exist",
        ) from None
    except RunStoreError:
        raise AtlasError(
            500,
            "prophylaxis_run_store_unavailable",
            "The Profylaxia run status is unavailable",
        ) from None


@router.get(
    "/runs/{run_id}/charts/{artifact}",
    response_class=FileResponse,
    dependencies=[Depends(run_authorized)],
    summary="Read one generated Profylaxia chart",
)
def prophylaxis_chart(
    run_id: UUID,
    artifact: str,
    request: Request,
) -> FileResponse:
    try:
        path = request.app.state.dependencies.prophylaxis_runs.store.chart_path(
            run_id, artifact
        )
    except RunNotFoundError:
        raise AtlasError(
            404,
            "prophylaxis_chart_not_found",
            "The Profylaxia chart does not exist",
        ) from None
    except RunStoreError:
        raise AtlasError(
            500,
            "prophylaxis_run_store_unavailable",
            "The Profylaxia chart is unavailable",
        ) from None
    return FileResponse(
        path,
        media_type="image/svg+xml",
        filename=artifact,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/runs/{run_id}/reports/{artifact}",
    dependencies=[Depends(run_authorized)],
    summary="Read a bounded site-report summary",
)
def prophylaxis_report_summary(run_id: UUID, artifact: str, request: Request) -> dict:
    return _report_summary(_report(request, run_id, artifact))


@router.get(
    "/runs/{run_id}/reports/{artifact}/devices/{device_id}",
    dependencies=[Depends(run_authorized)],
    summary="Read one device report without raw command output",
)
def prophylaxis_report_device(
    run_id: UUID, artifact: str, device_id: int, request: Request,
) -> dict:
    report = _report_device(_report(request, run_id, artifact), device_id)
    outputs = []
    for command, item in (report.get("outputs") or {}).items():
        outputs.append({key: value for key, value in item.items() if key != "stdout"} | {"command": command})
    return {
        **{key: value for key, value in report.items() if key != "outputs"},
        "outputs": outputs,
    }


@router.get(
    "/runs/{run_id}/reports/{artifact}/devices/{device_id}/commands/{command_id}",
    dependencies=[Depends(run_authorized)],
    summary="Read one bounded raw command result",
)
def prophylaxis_report_command(
    run_id: UUID, artifact: str, device_id: int, command_id: str, request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=32768, ge=1, le=65536),
) -> dict:
    report = _report_device(_report(request, run_id, artifact), device_id)
    for command, item in (report.get("outputs") or {}).items():
        if item.get("command_id") != command_id:
            continue
        raw = str(item.get("stdout") or "")
        return {
            **{key: value for key, value in item.items() if key != "stdout"},
            "command": command, "raw_output": raw[offset:offset + limit],
            "raw_offset": offset, "raw_total_characters": len(raw),
            "raw_response_truncated": offset + limit < len(raw),
        }
    raise AtlasError(404, "prophylaxis_command_not_found", "The command result does not exist")


@router.get(
    "/runs/{run_id}/reports/{artifact}/download",
    response_class=FileResponse,
    dependencies=[Depends(run_authorized)],
    summary="Download a complete authorized JSON report",
)
def prophylaxis_report_download(run_id: UUID, artifact: str, request: Request) -> FileResponse:
    try:
        path = request.app.state.dependencies.prophylaxis_runs.store.site_report_path(run_id, artifact)
    except RunNotFoundError:
        raise AtlasError(404, "prophylaxis_report_not_found", "The report does not exist") from None
    except RunStoreError:
        raise AtlasError(500, "prophylaxis_run_store_unavailable", "The report is unavailable") from None
    return FileResponse(path, media_type="application/json", filename=artifact, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get(
    "/runs/{run_id}/report.txt",
    response_class=FileResponse,
    dependencies=[Depends(run_authorized)],
    summary="Download the existing readable text report",
)
def prophylaxis_text_report(run_id: UUID, request: Request) -> FileResponse:
    try:
        path = request.app.state.dependencies.prophylaxis_runs.store.text_report_path(run_id)
    except RunNotFoundError:
        raise AtlasError(404, "prophylaxis_report_not_found", "The report does not exist") from None
    except RunStoreError:
        raise AtlasError(500, "prophylaxis_run_store_unavailable", "The report is unavailable") from None
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=f"{run_id}.txt", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


__all__ = ["router"]
