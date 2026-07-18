from __future__ import annotations
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, WebSocket
from starlette.datastructures import State
from typing import Callable, AsyncContextManager

if TYPE_CHECKING:
    from app.hackplate.config import BackendConfig
    from app.hackplate.toml_settings import BackendTOMLSettings


class _AppState(State):
    """
    Intermediary class to bind BackendConfig to the state
    """

    config: BackendConfig
    settings: BackendTOMLSettings


class Hackplate(FastAPI):
    """
    Custom class to to bind BackendConfig to the app object. Use in place of type and class `FastAPI`
    """

    state: _AppState
    pre_hackplate_lifespan: Callable[["Hackplate"], AsyncContextManager] | None = None
    post_hackplate_lifespan: Callable[["Hackplate"], AsyncContextManager] | None = None

    def __init__(
        self, pre_hackplate_lifespan=None, post_hackplate_lifespan=None, **kwargs
    ):
        from app.hackplate.lifespan import hackplate_lifespan
        from app.hackplate.toml_settings import ProjectDetails

        kwargs.setdefault("lifespan", hackplate_lifespan)
        super().__init__(**kwargs)

        project_details = ProjectDetails()

        self.title = project_details.name
        self.description = project_details.description
        self.version = project_details.version

        self.pre_hackplate_lifespan = pre_hackplate_lifespan
        self.post_hackplate_lifespan = post_hackplate_lifespan


class HackplateRequest(Request):
    """
    Custom class to to bind BackendConfig to request objects. Use in place of type and class `Request`
    """

    app: Hackplate


class HackplateWebSocket(WebSocket):
    """
    Custom class to to bind BackendConfig to websocket objects. Use in place of type and class `WebSocket`
    """

    app: Hackplate
