# python

## Toolchain

- `uv` owns the environment and dependencies. Commit `uv.lock`; install with `uv sync --locked` and run with `uv run`.
- ruff owns lint and formatting; its config is `[tool.ruff]` in `pyproject.toml`. Do not add black, isort, flake8 or pylint.
- ty owns type checking; its config is `[tool.ty]` in `pyproject.toml`.
- Target the version in `requires-python`. Use built-in generics (`list[str]`, `dict[str, int]`) and `X | None`.

## Types

- Annotate every public function, method and module constant. Let a local's type come from inference.
- Use `collections.abc` types for parameters (`Sequence`, `Mapping`, `Iterable`) and concrete types for returns.
- Use a dataclass or `NamedTuple` for structured data. A bare tuple is fine only while it stays inside one function.
- Use `enum.Enum` for a closed set of values, not module-level strings.
- Keep `Any` at the boundary. Convert it with validation before the rest of the code sees it. Use concrete types or protocols instead of `object` when the type is known.
- Use `typing.Protocol` for a dependency you substitute in a test.

## Code

- Use `pathlib.Path` for paths and `with` for every resource.
- Catch the narrowest exception you can handle. Never `except Exception` without re-raising an unrecognised error.
- No mutable default arguments; use `None` and create the value inside the function.
- Prefer a comprehension or generator expression to manual accumulation.
- Package layout is `src/<package>/`; tests live in `tests/` and import the installed package.
- Build strings with f-strings or `str.join`; never concatenate in a loop.

