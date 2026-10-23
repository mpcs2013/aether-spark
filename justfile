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
    {{ uv }} mypy src/python deploy/workstation tests/python
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

# Use the repo's git hooks (gitleaks pre-commit); once per clone
hooks:
    git config core.hooksPath .githooks

# Bring the dev stack up (Gate G3)
up: (pending "WP3.1" "up")

# Ops proxy for owner-started pull jobs: `just egress-ops up`, then `just egress-ops down` (egress.md)
egress-ops action:
    docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops {{ if action == "up" { "up -d --wait egress-ops" } else if action == "down" { "rm -sf egress-ops" } else { error("egress-ops: up or down") } }}

# Egress tests, layers 1 and 2 (tests/egress/run.sh; needs Docker and internet access)
[unix]
egress-test:
    bash tests/egress/run.sh

# On Windows through Git Bash, not the WSL `bash` shim, so the tests use Docker Desktop

# Egress tests, layers 1 and 2 (tests/egress/run.sh; needs Docker and internet access)
[windows]
egress-test:
    & "$env:ProgramFiles\Git\bin\bash.exe" tests/egress/run.sh

# Generate or check shared types from contracts/ (ADR-0002)
contracts *args: (pending "WP2.2/2.3" "contracts")

# Dev secrets store (ADR-0010)
secrets *args: (pending "WP3.3" "secrets")

# Stop all agent runs (ADR-0019)
agents-off: (pending "WP5a.5" "agents-off")

# Prune Docker build cache and dangling images (weekly, roadmap storage rules)
clean:
    docker builder prune -f
    docker image prune -f

# Build the dev container and run `just ci` inside it (CLI path; VS Code: Reopen in Container)
container-ci:
    docker build -t aetherspark-dev .devcontainer
    docker run --rm -v "{{ justfile_directory() }}:/workspace" -w /workspace aetherspark-dev sh -c "git config --global --add safe.directory /workspace && just ci"
