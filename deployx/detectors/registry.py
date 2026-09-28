"""
DeployX Detector Registry
Orchestrates framework and infrastructure detection across extensible detectors.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from deployx.detectors.base import BaseDetector, DetectionResult
from deployx.detectors.django import DjangoDetector


class DetectorRegistry:
    """Maintains list of available framework detectors."""

    def __init__(self):
        self._detectors: List[BaseDetector] = []

    def register(self, detector: BaseDetector) -> None:
        self._detectors.append(detector)

    def detect(self, repo_path: Path) -> DetectionResult:
        """
        Runs all registered detectors against repo_path and returns the result
        with highest confidence. Defaults to Django detector if tied or none confident.
        """
        results: List[DetectionResult] = []
        for det in self._detectors:
            try:
                res = det.detect(repo_path)
                results.append(res)
            except Exception:
                continue

        if not results:
            # Fallback to empty django result
            return DjangoDetector().detect(repo_path)

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results[0]


# Global registry with default detectors
registry = DetectorRegistry()
registry.register(DjangoDetector())


def detect_repository(repo_path: Path) -> DetectionResult:
    """Convenience function to detect framework & infra for a repository path."""
    return registry.detect(repo_path)
