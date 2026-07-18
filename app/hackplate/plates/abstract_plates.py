from abc import ABC, abstractmethod

from app.hackplate.user.models import AbstractUser, AbstractUserDocument

from app.hackplate.hackplate_types import Hackplate, HackplateRequest


class AuthPlate(ABC):
    @abstractmethod
    async def register_auth_routes(self, app: Hackplate) -> None: ...

    @abstractmethod
    async def authenticate(self, request: HackplateRequest) -> None:
        """Verify the request is authenticated. Raises 401 on failure. No DB call."""
        ...

    @abstractmethod
    async def get_current_user(
        self, request: HackplateRequest
    ) -> AbstractUser | AbstractUserDocument:
        """Verify and return the authenticated user. Raises 401 on failure."""
        ...

    @abstractmethod
    async def ping(self) -> bool: ...


class DatabasePlate(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    def get_db(self): ...
