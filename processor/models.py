import hashlib
import json
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
RecordID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
FileID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.jsonl$")]


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_id: RecordID
    payload: dict[str, Any]
    work_ms: int = Field(ge=25, le=60000)
    timeout_ms: int = Field(ge=25, le=120000)
    max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def payload_limit(self):
        if len(canonical(self.payload).encode("utf-8")) > 16384:
            raise ValueError("payload exceeds 16384 UTF-8 bytes")
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical(self.model_dump()).encode("utf-8")).hexdigest()


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: ID
    job_id: ID
    task_id: FileID
    attempt: int = Field(ge=1, le=5)
    record: Record

    @model_validator(mode="after")
    def attempt_limit(self):
        if self.attempt > self.record.max_attempts:
            raise ValueError("attempt cannot exceed record.max_attempts")
        return self
