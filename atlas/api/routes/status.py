"""User-oriented application status for the Operations UI."""

from fastapi import APIRouter, Request, status

from atlas.api.schemas.status import PlatformStatusResponse


router = APIRouter(tags=["status"])


@router.get(
    "/status",
    response_model=PlatformStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the application-level Atlas platform status",
)
def platform_status(request: Request) -> PlatformStatusResponse:
    result = request.app.state.dependencies.platform_status.check()
    settings = request.app.state.settings
    urls = {
        "netbox": settings.netbox_ui_url,
        "oxidized": settings.oxidized_ui_url,
        "zabbix": settings.zabbix_ui_url,
    }
    return PlatformStatusResponse(
        status=result.status,
        components={
            name: {
                "status": component.status,
                "url": str(urls[name]) if name in urls and urls[name] else None,
            }
            for name, component in result.components.items()
        },
    )
