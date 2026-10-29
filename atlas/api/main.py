"""FastAPI application factory and ASGI entry point."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from atlas.api.errors import register_error_handlers
from atlas.api.middleware import install_request_middleware
from atlas.api.routes import (
    auth,
    credentials,
    health,
    inventory,
    integrations,
    monitoring,
    oxidized,
    prophylaxis,
    readiness,
    status,
    vpn,
    zabbix,
)
from atlas.config import Settings, get_settings
from atlas.auth import AdminSessionService
from atlas.logging import configure_logging
from atlas.services.factory import DependencyServices, build_dependency_services


STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "static"


class NoCacheStaticFiles(StaticFiles):
    """Prevent a new HTML shell from running stale JavaScript or CSS."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(
    settings: Optional[Settings] = None,
    dependencies: Optional[DependencyServices] = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app):
        service = getattr(app.state.dependencies, "monitoring", None)
        # Image smoke tests intentionally run read-only without runtime mounts.
        # Production deployment pre-creates and mounts this persistent directory.
        if service is not None and service.store.directory.is_dir():
            service.store.recover()
        yield

    application = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version=settings.app_version,
        description="Aricoma Atlas network-management API",
    )
    application.state.settings = settings
    application.state.dependencies = dependencies or build_dependency_services(settings)
    application.state.auth = AdminSessionService(
        getattr(application.state.dependencies, "openbao", None),
        ttl_seconds=settings.admin_session_ttl_seconds,
    )

    install_request_middleware(application)
    register_error_handlers(application)
    application.include_router(auth.router, prefix=settings.api_prefix)
    if settings.zabbix_enabled:
        application.include_router(monitoring.router, prefix=settings.api_prefix)
    application.include_router(health.router, prefix=settings.api_prefix)
    application.include_router(readiness.router, prefix=settings.api_prefix)
    application.include_router(status.router, prefix=settings.api_prefix)
    if settings.oxidized_enabled:
        application.include_router(oxidized.router, prefix=settings.api_prefix)
    application.include_router(prophylaxis.router, prefix=settings.api_prefix)
    application.include_router(inventory.router, prefix=settings.api_prefix)
    application.include_router(credentials.router, prefix=settings.api_prefix)
    application.include_router(integrations.router, prefix=settings.api_prefix)
    application.include_router(vpn.router, prefix=settings.api_prefix)
    if settings.zabbix_enabled:
        application.include_router(zabbix.router, prefix=settings.api_prefix)
    application.mount(
        "/static",
        NoCacheStaticFiles(directory=STATIC_DIRECTORY),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    def operations_ui() -> FileResponse:
        return FileResponse(
            STATIC_DIRECTORY / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "script-src 'self'; "
                    "style-src 'self'; "
                    "img-src 'self' data:; "
                    "connect-src 'self'; "
                    "object-src 'none'; "
                    "base-uri 'none'; "
                    "frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )
    return application


app = create_app()
