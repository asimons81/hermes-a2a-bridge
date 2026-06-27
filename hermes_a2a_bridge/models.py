"""Pydantic models for the deliberately small A2A-shaped surface."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_serializer, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AgentSkill(WireModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    input_modes: list[str] | None = Field(None, alias="inputModes")
    output_modes: list[str] | None = Field(None, alias="outputModes")
    security_requirements: list[dict[str, list[str]]] | None = Field(None, alias="securityRequirements")


class AgentCapabilities(WireModel):
    streaming: bool = True
    push_notifications: bool = Field(False, alias="pushNotifications")
    extended_agent_card: bool = Field(False, alias="extendedAgentCard")
    extensions: list[dict[str, Any]] | None = None


class AgentInterface(WireModel):
    url: str
    transport: Literal["HTTP+JSON"] | None = None
    protocol_binding: Literal["HTTP+JSON"] | None = Field(None, alias="protocolBinding")
    protocol_version: str | None = Field(None, alias="protocolVersion")
    tenant: str | None = None


class AgentProvider(WireModel):
    organization: str
    url: str


class AgentCard(WireModel):
    name: str
    description: str
    version: str
    url: str | None = None
    provider: AgentProvider | None = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = Field(alias="defaultInputModes")
    default_output_modes: list[str] = Field(alias="defaultOutputModes")
    skills: list[AgentSkill]
    additional_interfaces: list[AgentInterface] = Field(default_factory=list, alias="additionalInterfaces")
    supported_interfaces: list[AgentInterface] = Field(default_factory=list, alias="supportedInterfaces")
    preferred_transport: str | None = Field(None, alias="preferredTransport")
    protocol_version: str | None = Field(None, alias="protocolVersion")
    documentation_url: str | None = Field(None, alias="documentationUrl")
    icon_url: str | None = Field(None, alias="iconUrl")
    security_schemes: dict[str, Any] | None = Field(None, alias="securitySchemes")
    security_requirements: list[dict[str, list[str]]] | None = Field(None, alias="securityRequirements")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def has_endpoint(self) -> "AgentCard":
        if not self.url and not self.supported_interfaces and not self.additional_interfaces:
            raise ValueError("Agent Card must declare url or an HTTP+JSON interface")
        return self


def _limited_file_reference_metadata() -> dict[str, Any]:
    return {
        "hermesA2ABridge": {
            "fileReferences": {
                "supported": True,
                "scope": "pre_staged_local_file_id_references_only",
                "acceptedShapes": [{"file": {"fileId": "file_..."}}],
                "requiresAuth": True,
                "requiresConfig": [
                    "parts.allow_file_parts",
                    "parts.allow_file_id_references",
                ],
                "unsupported": [
                    "inline_bytes",
                    "uri_file_references",
                    "remote_url_fetch",
                    "arbitrary_local_paths",
                    "uploads",
                ],
            }
        }
    }


class MessagePart(WireModel):
    kind: Literal["text", "data", "file"] | None = None
    part_type: Literal["text", "data", "file"] | None = Field(None, alias="type", exclude=True)
    text: str | None = None
    data: dict[str, Any] | list[Any] | None = None
    file: dict[str, Any] | None = None
    media_type: Literal["text/plain"] | None = Field(None, alias="mediaType")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_kind(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        kind = item.get("kind")
        part_type = item.get("type")
        if kind is not None and part_type is not None and kind != part_type:
            raise ValueError("part kind and type must match when both are supplied")
        if kind is None and part_type is not None:
            item["kind"] = part_type
        if item.get("kind") is None:
            if "file" in item:
                item["kind"] = "file"
            elif "data" in item:
                item["kind"] = "data"
            else:
                item["kind"] = "text"
        if item.get("kind") == "text" and "mediaType" not in item:
            item["mediaType"] = "text/plain"
        return item

    @model_validator(mode="after")
    def validate_shape(self) -> "MessagePart":
        if self.kind == "text":
            if self.text is None or self.data is not None or self.file is not None:
                raise ValueError("text parts require text and must not include data or file")
            if self.media_type != "text/plain":
                raise ValueError("text parts must use text/plain mediaType")
        elif self.kind == "data":
            if not isinstance(self.data, (dict, list)) or self.text is not None or self.file is not None:
                raise ValueError("data parts require JSON object or array data and must not include text or file")
            self.media_type = None
        elif self.kind == "file":
            if not isinstance(self.file, dict) or self.text is not None or self.data is not None:
                raise ValueError("file parts require file metadata and must not include text or data")
            self.media_type = None
        return self

    @model_serializer(mode="wrap")
    def serialize_part(self, handler):
        value = handler(self)
        value.pop("type", None)
        for key in tuple(value):
            if value[key] is None:
                value.pop(key)
        value.pop("kind", None)
        return value


class Message(WireModel):
    kind: Literal["message"] | None = Field(None, exclude=True)
    role: Literal["user", "agent"]
    parts: list[MessagePart]
    message_id: str = Field(default_factory=lambda: str(uuid4()), alias="messageId")
    context_id: str | None = Field(None, alias="contextId")
    task_id: str | None = Field(None, alias="taskId")
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: list[str] = Field(default_factory=list)
    reference_task_ids: list[str] = Field(default_factory=list, alias="referenceTaskIds")

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: Any) -> Any:
        return {"ROLE_USER": "user", "ROLE_AGENT": "agent"}.get(value, value)

    @field_serializer("role")
    def serialize_role(self, value: str) -> str:
        return {"user": "ROLE_USER", "agent": "ROLE_AGENT"}[value]


class TaskState(StrEnum):
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    REJECTED = "TASK_STATE_REJECTED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


class TaskStatus(WireModel):
    state: TaskState
    timestamp: str = Field(default_factory=utc_now)
    message: Message | None = None


class Task(WireModel):
    id: str
    context_id: str | None = Field(None, alias="contextId")
    status: TaskStatus
    history: list[Message] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactPart(WireModel):
    kind: Literal["text", "data", "file"] | None = None
    part_type: Literal["text", "data", "file"] | None = Field(None, alias="type", exclude=True)
    text: str | None = None
    data: dict[str, Any] | list[Any] | None = None
    file: dict[str, Any] | None = None
    media_type: Literal["text/plain"] | None = Field(None, alias="mediaType")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_kind(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        kind = item.get("kind")
        part_type = item.get("type")
        if kind is not None and part_type is not None and kind != part_type:
            raise ValueError("part kind and type must match when both are supplied")
        if kind is None and part_type is not None:
            item["kind"] = part_type
        if item.get("kind") is None:
            if "file" in item:
                item["kind"] = "file"
            elif "data" in item:
                item["kind"] = "data"
            else:
                item["kind"] = "text"
        if item.get("kind") == "text" and "mediaType" not in item:
            item["mediaType"] = "text/plain"
        return item

    @model_validator(mode="after")
    def validate_shape(self) -> "ArtifactPart":
        if self.kind == "text":
            if self.text is None or self.data is not None or self.file is not None:
                raise ValueError("text parts require text and must not include data or file")
            if self.media_type != "text/plain":
                raise ValueError("text parts must use text/plain mediaType")
        elif self.kind == "data":
            if not isinstance(self.data, (dict, list)) or self.text is not None or self.file is not None:
                raise ValueError("data parts require JSON object or array data and must not include text or file")
            self.media_type = None
        elif self.kind == "file":
            if not isinstance(self.file, dict) or self.text is not None or self.data is not None:
                raise ValueError("file artifact parts require file metadata and must not include text or data")
            self.media_type = None
        return self

    @model_serializer(mode="wrap")
    def serialize_part(self, handler):
        value = handler(self)
        value.pop("type", None)
        for key in tuple(value):
            if value[key] is None:
                value.pop(key)
        value.pop("kind", None)
        return value


class Artifact(WireModel):
    artifact_id: str = Field(alias="artifactId")
    parts: list[ArtifactPart]
    name: str = "result"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStatusUpdateEvent(WireModel):
    task_id: str = Field(alias="taskId")
    context_id: str = Field(alias="contextId")
    status: TaskStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskArtifactUpdateEvent(WireModel):
    task_id: str = Field(alias="taskId")
    context_id: str = Field(alias="contextId")
    artifact: Artifact
    append: bool = False
    last_chunk: bool = Field(False, alias="lastChunk")
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamResponse(WireModel):
    task: Task | None = None
    message: Message | None = None
    status_update: TaskStatusUpdateEvent | None = Field(None, alias="statusUpdate")
    artifact_update: TaskArtifactUpdateEvent | None = Field(None, alias="artifactUpdate")

    @model_validator(mode="after")
    def exactly_one_payload(self) -> "StreamResponse":
        values = (self.task, self.message, self.status_update, self.artifact_update)
        if sum(value is not None for value in values) != 1:
            raise ValueError("A stream response must contain exactly one event payload")
        return self


def build_agent_card(config: dict[str, Any]) -> AgentCard:
    card = config["agent_card"]
    url = config["server"]["public_url"].rstrip("/")
    provider = dict(card["provider"])
    if provider.get("organization") == "local":
        provider["url"] = url
    parts = config.get("parts", {})
    metadata = (
        _limited_file_reference_metadata()
        if parts.get("allow_file_parts", False) and parts.get("allow_file_id_references", False)
        else None
    )
    return AgentCard(
        name=card["name"],
        description=card["description"],
        version=card["version"],
        url=url,
        provider=provider,
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=card["default_input_modes"],
        defaultOutputModes=card["default_output_modes"],
        skills=card["skills"],
        preferredTransport="HTTP+JSON",
        additionalInterfaces=[AgentInterface(url=url, transport="HTTP+JSON")],
        supportedInterfaces=[AgentInterface(
            url=url, protocolBinding="HTTP+JSON", protocolVersion="1.0",
        )],
        metadata=metadata,
    )
