"""Narrow guest intents; document edits and visitor IDs are never accepted."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SelectionProblem(Exception):
    def __init__(self, code, message, status=409, retry_after=None):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


class VisitorStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published_revision_id: UUID


class ClientChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: UUID = Field(strict=False)
    selected: bool
    published_revision_id: UUID = Field(strict=False)
    mutation_id: UUID = Field(strict=False)
    version: int = Field(ge=0, le=2**63 - 1)


class NotificationRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: int = Field(ge=1, le=2**63 - 1)
