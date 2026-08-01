from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

RolloutMode = Literal["shadow", "canary", "integrated", "fallback", "rollback"]


@dataclass(frozen=True, slots=True)
class RolloutReceipt:
    schema: str
    mode: RolloutMode
    status: str
    generation: str | None
    reason: str | None
    previous_mode: RolloutMode | None


class RolloutController:
    """Persist a small, atomic rollout state for Loop/Runtime coordination."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def transition(
        self,
        mode: RolloutMode,
        *,
        generation: str | None = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        previous = self._read().get("mode") if self.state_path.is_file() else None
        receipt = RolloutReceipt(
            schema="simplicio.fast.rollout-receipt/v1",
            mode=mode,
            status="rolled-back" if mode == "rollback" else "accepted",
            generation=generation,
            reason=reason,
            previous_mode=previous
            if previous in {"shadow", "canary", "integrated", "fallback", "rollback"}
            else None,
        )
        payload = asdict(receipt)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f"{self.state_path.name}.", dir=self.state_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            Path(temporary_name).replace(self.state_path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return payload

    def _read(self) -> dict[str, object]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
