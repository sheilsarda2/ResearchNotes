#!/usr/bin/env python3
"""Run Harbor with benchmark deadline cleanup and evidence capture.

Use Harbor's Python environment. This entrypoint adds no resource admission
policy; campaign supervisors retain their existing scheduling and task budgets.
"""
from benchmark_deadline import install
from benchmark_mini_tool_runtime import install as install_agent_runtime
from harbor.cli.main import app


def main():
    install_agent_runtime()
    install()
    app()


if __name__ == '__main__':
    main()
