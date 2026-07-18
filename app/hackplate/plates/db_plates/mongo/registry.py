from typing import Type
from beanie import Document

__registered_documents: list[Type[Document]] = []


def register_document(cls: Type[Document]) -> Type[Document]:
    """
    Mark a Beanie Document as one to be initialized by MongoPlate.connect().

    Feature models must be decorated with this to be picked up — nothing
    walks the class hierarchy automatically, so an undecorated Document
    silently never gets registered with init_beanie() and any
    find()/insert() calls against it will fail at runtime.

    Example:

        from beanie import Document
        from app.hackplate.plates.db_plates.mongo.registry import register_document

        @register_document
        class Order(Document):
            user_id: str
            total: float

            class Settings:
                name = "orders"
    """
    __registered_documents.append(cls)
    return cls


def get_registered_documents() -> list[Type[Document]]:
    return list(__registered_documents)
