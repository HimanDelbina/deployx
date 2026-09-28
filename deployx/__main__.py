"""
DeployX main entrypoint for `python -m deployx` execution.
"""

from deployx.cli import app

if __name__ == "__main__":
    app(prog_name="deployx")
