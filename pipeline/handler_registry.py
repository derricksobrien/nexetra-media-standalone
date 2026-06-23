"""Execution handler registry keyed by JCL job_type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageSpec:
    name: str
    script: str


@dataclass(frozen=True)
class ExecutionHandler:
    job_type: str
    stages: tuple[StageSpec, ...]
    capability_names: tuple[str, ...]


MEDIA_HANDLER = ExecutionHandler(
    job_type="media_pipeline",
    stages=(
        StageSpec("scriptgen", "pipeline/scriptgen/agent.py"),
        StageSpec("translate", "pipeline/translate/agent.py"),
        StageSpec("tts", "pipeline/tts/agent.py"),
        StageSpec("assembly", "pipeline/assembly/agent.py"),
        StageSpec("export", "pipeline/export/agent.py"),
    ),
    capability_names=("scriptgen", "translate", "tts", "assembly", "export"),
)


CONTENT_DEVELOPMENT_HANDLER = ExecutionHandler(
    job_type="content_development",
    stages=(
        StageSpec("plan", "pipeline/content_development/plan.py"),
        StageSpec("draft", "pipeline/content_development/draft.py"),
        StageSpec("variants", "pipeline/content_development/variants.py"),
        StageSpec("review", "pipeline/content_development/review.py"),
        StageSpec("package", "pipeline/content_development/package.py"),
    ),
    capability_names=("content_plan", "content_draft", "content_variants", "content_review", "content_package"),
)


HANDLERS = {
    MEDIA_HANDLER.job_type: MEDIA_HANDLER,
    CONTENT_DEVELOPMENT_HANDLER.job_type: CONTENT_DEVELOPMENT_HANDLER,
}


def get_handler(job_type: str) -> ExecutionHandler | None:
    return HANDLERS.get(job_type)


def executable_job_types() -> set[str]:
    return set(HANDLERS)
