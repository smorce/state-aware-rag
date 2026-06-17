from __future__ import annotations

import argparse

import state_aware_rag.cli as cli
from state_aware_rag.config import RagConfig


def test_cli_defaults_to_helix_backend() -> None:
    args = cli.build_parser().parse_args(["ask", "What is stored?"])

    assert args.backend == "helix"


def test_cli_reports_helix_startup_hint_when_default_backend_unavailable(monkeypatch, capsys) -> None:
    def unavailable(args: argparse.Namespace, config: RagConfig):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cli, "_build_store", unavailable)

    exit_code = cli.main(["ask", "What is stored?"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "HelixDB backend is not available: connection refused" in captured.err
    assert "Use --backend sqlite for local development without HelixDB." in captured.err
