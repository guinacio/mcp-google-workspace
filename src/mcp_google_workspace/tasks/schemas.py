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
    tasklist_id: str = Field(description="Tasklist ID.")
    task_id: str = Field(description="Task ID.")
    title: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    due: str | None = Field(default=None)
    status: TaskStatus | None = Field(default=None)
    completed: str | None = Field(default=None)
    deleted: bool | None = Field(default=None)
    hidden: bool | None = Field(default=None)

    @field_validator("due", "completed", mode="before")
    @classmethod
    def _normalize_times(cls, value: str | None) -> str | None:
        return _normalize_timestamp(value)

    @model_validator(mode="after")
    def _ensure_updates(self) -> "UpdateTaskRequest":
        if all(
            value is None
            for value in (self.title, self.notes, self.due, self.status, self.completed, self.deleted, self.hidden)
        ):
            raise ValueError("At least one mutable task field must be provided")
        return self


class CompleteTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    task_id: str = Field(description="Task ID.")
    completed_at: str | None = Field(default=None)

    @field_validator("completed_at", mode="before")
    @classmethod
    def _normalize_completed(cls, value: str | None) -> str | None:
        return _normalize_timestamp(value)


class MoveTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Source tasklist ID.")
    task_id: str = Field(description="Task ID.")
    parent: str | None = Field(default=None)
    previous: str | None = Field(default=None)
    destination_tasklist_id: str | None = Field(default=None)


class DeleteTaskRequest(ToolRequestModel):
    tasklist_id: str = Field(description="Tasklist ID.")
    task_id: str = Field(description="Task ID.")
