"""Explicit process lifetime and validation for legacy launch options."""
import math


def validate_legacy_intervals(lease: float, grace: float) -> None:
    # Older launchers still pass these options; they no longer control work.
    if not all(math.isfinite(value) and value > 0 for value in (lease, grace)) or not 0 < min(2.0, lease / 3) < lease:
        raise ValueError('Lifetime durations must be finite and positive')
