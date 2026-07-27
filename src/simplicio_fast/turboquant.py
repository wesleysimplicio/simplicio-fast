"""Deterministic Python primitives for a TurboQuant-style 4-bit hot layer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

SCHEMA = "simplicio.fast.turboquant-4bit/v1"
_MIN_CODE = -8
_MAX_CODE = 7
_MASK64 = (1 << 64) - 1


class QuantizationError(ValueError):
    """Raised when a vector or packed nibble stream violates the contract."""


def _validate_dimension(dimension: int) -> None:
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        raise QuantizationError("dimension must be a positive integer")


def _validate_seed(seed: int) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise QuantizationError("seed must be an integer")
    return seed & _MASK64


def _splitmix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & _MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (value ^ (value >> 31)) & _MASK64


def _rotation_plan(dimension: int, seed: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    _validate_dimension(dimension)
    state = _validate_seed(seed)
    permutation = list(range(dimension))
    for index in range(dimension - 1, 0, -1):
        state = _splitmix64(state)
        other = state % (index + 1)
        permutation[index], permutation[other] = permutation[other], permutation[index]
    signs: list[int] = []
    for _ in range(dimension):
        state = _splitmix64(state)
        signs.append(-1 if state & 1 else 1)
    return tuple(permutation), tuple(signs)


def rotate(values: Iterable[float], seed: int = 0, *, inverse: bool = False) -> tuple[float, ...]:
    """Apply a deterministic signed-permutation orthogonal rotation."""
    vector = _as_vector(values)
    permutation, signs = _rotation_plan(len(vector), seed)
    if inverse:
        result = [0.0] * len(vector)
        for output_index, source_index in enumerate(permutation):
            result[source_index] = signs[output_index] * vector[output_index]
        return tuple(result)
    return tuple(signs[index] * vector[source] for index, source in enumerate(permutation))


def _as_vector(values: Iterable[float]) -> tuple[float, ...]:
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise QuantizationError("values must be a finite non-empty iterable of numbers") from error
    if not vector or any(not math.isfinite(value) for value in vector):
        raise QuantizationError("values must be a finite non-empty iterable of numbers")
    return vector


def _round_half_away_from_zero(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def pack_nibbles(codes: Iterable[int]) -> bytes:
    """Pack signed 4-bit codes low-nibble first, padding odd tails with zero."""
    try:
        values = tuple(codes)
    except TypeError as error:
        raise QuantizationError("codes must be an iterable") from error
    for code in values:
        if isinstance(code, bool) or not isinstance(code, int) or not _MIN_CODE <= code <= _MAX_CODE:
            raise QuantizationError("codes must be signed 4-bit integers")
    packed = bytearray((len(values) + 1) // 2)
    for index, code in enumerate(values):
        nibble = code & 0x0F
        if index % 2 == 0:
            packed[index // 2] = nibble
        else:
            packed[index // 2] |= nibble << 4
    return bytes(packed)


def unpack_nibbles(packed: bytes, dimension: int) -> tuple[int, ...]:
    """Unpack low-nibble-first signed codes and reject malformed padding."""
    _validate_dimension(dimension)
    if not isinstance(packed, bytes):
        raise QuantizationError("packed values must be bytes")
    expected_bytes = (dimension + 1) // 2
    if len(packed) != expected_bytes:
        raise QuantizationError("packed length does not match dimension")
    if dimension % 2 and packed[-1] & 0xF0:
        raise QuantizationError("odd-dimensional padding nibble must be zero")
    result: list[int] = []
    for index in range(dimension):
        nibble = (packed[index // 2] >> (4 if index % 2 else 0)) & 0x0F
        result.append(nibble - 16 if nibble >= 8 else nibble)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class QuantizedVector:
    dimension: int
    seed: int
    scale: float
    packed: bytes

    @property
    def schema(self) -> str:
        return SCHEMA

    @property
    def codes(self) -> tuple[int, ...]:
        return unpack_nibbles(self.packed, self.dimension)


def quantize(values: Iterable[float], seed: int = 0) -> QuantizedVector:
    """Rotate and symmetrically quantize a vector into signed 4-bit nibbles."""
    vector = _as_vector(values)
    normalized_seed = _validate_seed(seed)
    rotated = rotate(vector, normalized_seed)
    peak = max(abs(value) for value in rotated)
    scale = peak / _MAX_CODE if peak else 1.0
    codes = tuple(
        max(_MIN_CODE, min(_MAX_CODE, _round_half_away_from_zero(value / scale)))
        for value in rotated
    )
    return QuantizedVector(len(vector), normalized_seed, scale, pack_nibbles(codes))


def dequantize(vector: QuantizedVector) -> tuple[float, ...]:
    """Unpack, dequantize, and inverse-rotate a quantized vector."""
    if not isinstance(vector, QuantizedVector):
        raise QuantizationError("vector must be a QuantizedVector")
    if not math.isfinite(vector.scale) or vector.scale <= 0:
        raise QuantizationError("scale must be a positive finite number")
    rotated = tuple(code * vector.scale for code in vector.codes)
    return rotate(rotated, vector.seed, inverse=True)
