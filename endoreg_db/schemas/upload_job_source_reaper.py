from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReapUploadJobSourcesOptions(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    upload_job_id: uuid.UUID | None = None
    limit: int | None = Field(default=None, gt=0)
    repeat_until_empty: bool = False
    apply: bool = False
    json_output: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "ReapUploadJobSourcesOptions":
        if self.upload_job_id is not None and self.limit is not None:
            raise ValueError("--upload-job-id and --limit are mutually exclusive")
        if self.upload_job_id is None and self.limit is None:
            raise ValueError("batch selection requires an explicit positive --limit")
        if self.repeat_until_empty and self.upload_job_id is not None:
            raise ValueError("--repeat-until-empty cannot be used with --upload-job-id")
        return self
