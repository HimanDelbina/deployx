"""
DeployX Generators Registry & Dispatcher
Dispatches Dockerfile and Docker Compose generation to the appropriate framework generator.
"""

from __future__ import annotations

from pathlib import Path

from deployx.detectors.base import DetectionResult
from deployx.generators.django import (
    generate_compose_file as generate_django_compose,
    generate_django_dockerfile,
    is_deployx_generated_file,
)
from deployx.generators.fastapi import (
    generate_fastapi_compose_file,
    generate_fastapi_dockerfile,
)
from deployx.generators.flask import (
    generate_flask_compose_file,
    generate_flask_dockerfile,
)
from deployx.generators.generic_python import (
    generate_generic_compose_file,
    generate_generic_dockerfile,
)
from deployx.models import FrameworkType, ProjectConfig


def generate_dockerfile_for_project(
    project_config: ProjectConfig,
    detection: DetectionResult,
    target_path: Path,
) -> Path:
    fw = project_config.deployment.framework
    if fw == FrameworkType.DJANGO:
        return generate_django_dockerfile(project_config, detection, target_path)
    elif fw == FrameworkType.FASTAPI:
        return generate_fastapi_dockerfile(project_config, detection, target_path)
    elif fw == FrameworkType.FLASK:
        return generate_flask_dockerfile(project_config, detection, target_path)
    else:
        return generate_generic_dockerfile(project_config, detection, target_path)


def generate_compose_for_project(
    project_config: ProjectConfig,
    detection: DetectionResult,
    image_tag: str,
    target_path: Path,
) -> Path:
    fw = project_config.deployment.framework
    if fw == FrameworkType.DJANGO:
        return generate_django_compose(project_config, detection, image_tag, target_path)
    elif fw == FrameworkType.FASTAPI:
        return generate_fastapi_compose_file(project_config, detection, image_tag, target_path)
    elif fw == FrameworkType.FLASK:
        return generate_flask_compose_file(project_config, detection, image_tag, target_path)
    else:
        return generate_generic_compose_file(project_config, detection, image_tag, target_path)
