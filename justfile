set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Members of the uv workspace are not dependencies of the root, so sync them all
uv := "uv run --all-packages"

# List the recipes
default:
    @just --list

# Recipes named in the docs whose work package has not landed yet fail loudly
[private]
pending wp what:
    {{ error("not implemented yet: " + what + " (" + wp + ")") }}

# Lint, type-check, build and test everything (Gate G2 also needs GitHub Actions, WP2.5)
ci: dotnet-ci py-ci

# Python: lint, format check, strict type check, tests
py-ci:
    {{ uv }} ruff check
    {{ uv }} ruff format --check
    {{ uv }} mypy src/python deploy/workstation
    {{ uv }} pytest

# .NET: locked restore, build (warnings are errors), tests
dotnet-ci:
    dotnet restore AetherSpark.slnx --locked-mode
    dotnet build AetherSpark.slnx --no-restore
    dotnet test --solution AetherSpark.slnx --no-build

# Python: apply formatting and safe lint fixes
py-fix:
    -{{ uv }} ruff check --fix
    {{ uv }} ruff format

# Bring the dev stack up (Gate G3)
up: (pending "WP3.1" "up")

# Generate or check shared types from contracts/ (ADR-0002)
contracts *args: (pending "WP2.2/2.3" "contracts")

# Dev secrets store (ADR-0010)
secrets *args: (pending "WP3.3" "secrets")

# Stop all agent runs (ADR-0019)
agents-off: (pending "WP5a.5" "agents-off")

# Prune Docker build cache and dangling images
clean: (pending "WP2.4" "clean")
