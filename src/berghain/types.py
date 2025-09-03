from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Mapping


AttributeId = str


@dataclass
class Constraint:
    attribute: AttributeId
    minCount: int


@dataclass
class AttributeStatistics:
    relativeFrequencies: Mapping[AttributeId, float]
    correlations: Mapping[AttributeId, Mapping[AttributeId, float]]


@dataclass
class NewGameResponse:
    gameId: str
    constraints: list[Constraint]
    attributeStatistics: AttributeStatistics


@dataclass
class Person:
    personIndex: int
    attributes: Dict[AttributeId, bool]


@dataclass
class DecideAndNextRunning:
    status: str  # "running"
    admittedCount: int
    rejectedCount: int
    nextPerson: Optional[Person]


@dataclass
class DecideAndNextCompleted:
    status: str  # "completed"
    rejectedCount: int
    nextPerson: None


@dataclass
class DecideAndNextFailed:
    status: str  # "failed"
    reason: str
    nextPerson: None


DecideAndNextResponse = DecideAndNextRunning | DecideAndNextCompleted | DecideAndNextFailed

