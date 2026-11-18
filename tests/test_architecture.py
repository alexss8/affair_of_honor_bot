"""Layer rules from AGENTS.md, checked on the source instead of trusted to memory."""

import ast
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parents[1] / "bot"

# Methods that handlers must reach only through bot/telegram/safe_api.py.
UNSAFE_TELEGRAM_CALLS = {"answer", "reply", "edit_text", "edit_reply_markup", "edit_message_text", "get_chat"}


def python_files(package: str) -> list[Path]:
    return sorted((BOT / package).rglob("*.py"))


def imported_modules(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    found = []
    for path in python_files(package):
        for module in imported_modules(path):
            if any(module == name or module.startswith(f"{name}.") for name in forbidden):
                found.append(f"{path.relative_to(BOT.parent)}: imports {module}")
    return found


@pytest.mark.parametrize(
    ("package", "forbidden"),
    [
        ("domain", ("aiogram", "bot.storage", "bot.services", "bot.telegram")),
        ("telegram/ui", ("bot.storage",)),
    ],
)
def test_layer_does_not_import_forbidden_modules(package: str, forbidden: tuple[str, ...]) -> None:
    assert python_files(package), f"no files found under bot/{package}"
    assert violations(package, forbidden) == []


def test_handlers_call_telegram_only_through_safe_api() -> None:
    found = []
    for path in python_files("telegram/handlers"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in UNSAFE_TELEGRAM_CALLS
            ):
                found.append(f"{path.relative_to(BOT.parent)}:{node.lineno}: .{node.func.attr}()")
    assert python_files("telegram/handlers"), "no handler files found"
    assert found == []
