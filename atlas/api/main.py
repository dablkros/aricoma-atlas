"""FastAPI application factory and ASGI entry point."""

from typing import Optional

from fastapi import FastAPI

from atlas.api.errors import register_error_handlers
from atlas.api.middleware import install_request_middleware
from atlas.api.routes import health, oxidized
from atlas.config import Settings, get_settings
from atlas.logging import configure_logging


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Aricoma Atlas network-management API",
    )
    application.state.settings = settings

    install_request_middleware(application)
    register_error_handlers(application)
    application.include_router(health.router, prefix=settings.api_prefix)
    application.include_router(oxidized.router, prefix=settings.api_prefix)
    return application


app = create_app()
