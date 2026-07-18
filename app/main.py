from app.hackplate import Hackplate
from app.hackplate.lifespan import configure
from app.lifespan import lifespan, pre_hackplate_lifespan


def register_routes(app: Hackplate) -> None:
    """
    Function for registering routers.

    Args:
        app: initialized Hackplate object originating from main.py
    """
    pass


app = Hackplate(
    pre_hackplate_lifespan=pre_hackplate_lifespan,
    post_hackplate_lifespan=lifespan,
)

configure(
    app,
    [
        register_routes,
    ],
)
