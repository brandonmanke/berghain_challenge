"""Shared utility functions."""

from __future__ import annotations

import os
from typing import Optional


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, ignore comments/blank.
    Only sets variables not already in the environment.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


def validate_param(
    name: str,
    value: float | int | None,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    min_exclusive: bool = False,
) -> None:
    """Validate a numeric parameter is within acceptable bounds.

    Args:
        name: Parameter name for error messages
        value: Value to validate (skips if None)
        min_val: Minimum allowed value (inclusive by default)
        max_val: Maximum allowed value (inclusive)
        min_exclusive: If True, min_val is exclusive (value must be > min_val, not >=)

    Raises:
        ValueError: If parameter is out of bounds
    """
    if value is None:
        return

    if min_val is not None:
        if min_exclusive and value <= min_val:
            raise ValueError(f"{name} must be > {min_val}, got {value}")
        elif not min_exclusive and value < min_val:
            raise ValueError(f"{name} must be >= {min_val}, got {value}")

    if max_val is not None and value > max_val:
        raise ValueError(f"{name} must be <= {max_val}, got {value}")
