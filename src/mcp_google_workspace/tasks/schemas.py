"""Pydantic models for Tasks tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..common.request_model import ToolRequestModel

TaskStatus = Literal["needsAction", "completed"]


def _normalize_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) == 10:
        return f"{value}T00:00:00.000Z"
    if value.endswith("Z") or "+" in value[-6:]:
        return value
    if len(value) == 19:
        return f"{value}Z"
    return value


class ListTasklistsRequest(ToolRequestModel):
    max_results: int = Field(default=100, ge=1, le=100)
    page_token: str | None = Field(default=None)


class GetTasklistRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")


class CreateTasklistRequest(ToolRequestModel):
    title: str = Field(description="Tasklist title.")


class ListTasksRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    completed_max: str | None = Field(default=None)
    completed_min: str | None = Field(default=None)
    due_max: str | None = Field(default=None)
    due_min: str | None = Field(default=None)
    max_results: int = Field(default=100, ge=1, le=100)
    page_token: str | None = Field(default=None)
    show_assigned: bool = Field(default=False)
    show_completed: bool = Field(default=True)
    show_deleted: bool = Field(default=False)
    show_hidden: bool = Field(default=False)
    updated_min: str | None = Field(default=None)

    @field_validator("completed_max", "completed_min", "due_max", "due_min", "updated_min", mode="before")
    @classmethod
    def _normalize_optional_time(cls, value: str | None) -> str | None:
        return _normalize_timestamp(value)


class GetTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    task_id: str = Field(description="Task ID.")


class CreateTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    title: str = Field(description="Task title.")
    notes: str | None = Field(default=None)
    due: str | None = Field(default=None)
    parent: str | None = Field(default=None)
    previous: str | None = Field(default=None)
    status: TaskStatus = Field(default="needsAction")

    @field_validator("due", mode="before")
    @classmethod
    def _normalize_due(cls, value: str | None) -> str | None:
        return _normalize_timestamp(value)


class UpdateTaskRequest(ToolRequestModel):
    """A field patch and/or a reposition of one task (tasks.move, then tasks.patch)."""

    tasklist_id: str = Field(description="Tasklist ID the task is in now.")
    task_id: str = Field(description="Task ID.")
    title: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    due: str | None = Field(default=None)
    status: TaskStatus | None = Field(default=None)
    completed: str | None = Field(default=None)
    deleted: bool | None = Field(default=None)
    hidden: bool | None = Field(default=None)
    parent: str | None = Field(default=None)
    previous: str | None = Field(default=None)
    destination_tasklist_id: str | None = Field(default=None)
    move_to_top: bool = Field(default=False)

    @field_validator("due", "completed", mode="before")
    @classmethod
    def _normalize_times(cls, value: str | None) -> str | None:
        return _normalize_timestamp(value)

    @field_validator("parent", "previous", "destination_tasklist_id", mode="before")
    @classmethod
    def _blank_is_omitted(cls, value: str | None) -> str | None:
        # An empty string must not turn into a move to the top of the list.
        return value or None

    @property
    def moves(self) -> bool:
        """Whether a tasks.move is requested."""
        return self.move_to_top or any(
            value is not None for value in (self.parent, self.previous, self.destination_tasklist_id)
        )

    def patch_body(self) -> dict[str, object]:
        """The tasks.patch body: the task fields that were set."""
        return self.model_dump(
            exclude_none=True,
            include={"title", "notes", "due", "status", "completed", "deleted", "hidden"},
            mode="json",
        )

    @model_validator(mode="after")
    def _ensure_valid_change(self) -> "UpdateTaskRequest":
        # The Tasks API rejects cross-list moves that also reposition the task.
        if self.destination_tasklist_id is not None and (self.parent or self.previous):
            raise ValueError(
                "parent/previous cannot be combined with destination_tasklist_id; "
                "move the task to the destination tasklist first, then reorder it"
            )
        if self.move_to_top and (self.parent or self.previous or self.destination_tasklist_id):
            raise ValueError("move_to_top cannot be combined with parent, previous or destination_tasklist_id")
        if not self.moves and not self.patch_body():
            raise ValueError(
                "At least one change must be provided: a task field (title, notes, due, status, completed, "
                "deleted, hidden) or a move (parent, previous, destination_tasklist_id, move_to_top)"
            )
        return self


class DeleteTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    task_id: str = Field(description="Task ID.")
