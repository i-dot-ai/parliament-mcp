"""Unit tests for the parliament-mcp CLI argument parser."""

import logging
from datetime import UTC, datetime

import pytest

from parliament_mcp.cli import configure_logging, create_parser


def test_init_qdrant_command_parses():
    """Bare 'init-qdrant' subcommand parses without extra args."""
    parser = create_parser()
    args = parser.parse_args(["init-qdrant"])
    assert args.command == "init-qdrant"


def test_delete_qdrant_command_parses():
    """Bare 'delete-qdrant' subcommand parses without extra args."""
    parser = create_parser()
    args = parser.parse_args(["delete-qdrant"])
    assert args.command == "delete-qdrant"


def test_load_data_with_iso_dates():
    """'load-data hansard' with ISO date strings parses dates via dateparser."""
    parser = create_parser()
    args = parser.parse_args(
        [
            "load-data",
            "hansard",
            "--from-date",
            "2025-06-23",
            "--to-date",
            "2025-06-25",
        ]
    )
    assert args.command == "load-data"
    assert args.source == "hansard"
    assert args.from_date.year == 2025
    assert args.from_date.month == 6
    assert args.from_date.day == 23
    assert args.to_date.day == 25


def test_load_data_with_human_readable_dates():
    """Human-friendly strings like '3 days ago' resolve to datetimes via dateparser."""
    parser = create_parser()
    args = parser.parse_args(
        [
            "load-data",
            "parliamentary-questions",
            "--from-date",
            "3 days ago",
            "--to-date",
            "today",
        ]
    )
    assert args.source == "parliamentary-questions"
    assert isinstance(args.from_date, datetime)
    assert isinstance(args.to_date, datetime)
    # 'today' should resolve to a datetime within the last 24 hours.
    delta = datetime.now(args.to_date.tzinfo or UTC).replace(tzinfo=args.to_date.tzinfo) - args.to_date
    assert abs(delta.total_seconds()) < 86_400


def test_load_data_to_date_defaults_to_today():
    """Omitting --to-date defaults to today's date."""
    parser = create_parser()
    args = parser.parse_args(["load-data", "hansard", "--from-date", "2025-06-23"])
    assert args.to_date == datetime.now(UTC).date()


def test_load_data_rejects_invalid_source():
    """Unknown data source values raise SystemExit (argparse error)."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["load-data", "not-a-real-source", "--from-date", "2025-06-23"])


def test_load_data_requires_from_date():
    """Missing --from-date raises SystemExit (argparse required-arg error)."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["load-data", "hansard"])


def test_serve_default_reload_is_true():
    """'serve' without flags has reload=True (development default)."""
    parser = create_parser()
    args = parser.parse_args(["serve"])
    assert args.command == "serve"
    assert args.reload is True


def test_serve_no_reload_flag_disables_reload():
    """'serve --no-reload' sets reload=False."""
    parser = create_parser()
    args = parser.parse_args(["serve", "--no-reload"])
    assert args.reload is False


def test_log_level_default_is_warning():
    """Omitting --log-level defaults to WARNING."""
    parser = create_parser()
    args = parser.parse_args(["init-qdrant"])
    assert args.log_level == "WARNING"


def test_log_level_short_alias_ll_works():
    """The --ll short alias for --log-level accepts the same choices."""
    parser = create_parser()
    args = parser.parse_args(["--ll", "DEBUG", "init-qdrant"])
    assert args.log_level == "DEBUG"


def test_log_level_choices_are_enforced():
    """An out-of-range --log-level value raises SystemExit."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--log-level", "VERBOSE", "init-qdrant"])


def test_command_is_required():
    """No subcommand raises SystemExit (subparsers required=True)."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_configure_logging_with_colors_does_not_raise():
    """configure_logging(use_colors=True) installs without error at INFO level."""
    configure_logging(level=logging.INFO, use_colors=True)


def test_configure_logging_without_colors_does_not_raise():
    """configure_logging(use_colors=False) installs without error at DEBUG level."""
    configure_logging(level=logging.DEBUG, use_colors=False)
