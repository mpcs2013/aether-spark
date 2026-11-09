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
    {{ uv }} mypy src/python deploy/ca deploy/compose deploy/images deploy/keycloak deploy/secret-store tests/python tests/stack
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

# Bring the stack up: secrets, CA, certificates, services (compose-base.md; WSL2 or the Spark)
[unix]
up:
    bash deploy/compose/stack/up.sh

# Stop the stack and empty the in-memory secrets folder; volumes stay
[unix]
down:
    bash deploy/compose/stack/down.sh

# The CA (docs/runbooks/certificates.md): status | root | renew-intermediate | init | configure
[unix]
ca *args:
    python3 -I deploy/ca/ca.py {{ args }}

# Keycloak's administrators (docs/runbooks/keycloak-admin.md): setup USERNAME (once per stack).
# These recipes do not echo their command line: it would print the administrator's name
[unix]
keycloak *args:
    @python3 -I deploy/keycloak/admin.py {{ args }}

# Keycloak break-glass: a new temporary administrator when none can sign in (keycloak-admin.md)
[unix]
keycloak-break-glass username:
    @bash deploy/keycloak/break-glass.sh {{ username }}

# The stack end to end: tests C3, C3b, C5, C9a, C9b, C10 so far, C11 (tests/stack/run.sh; ~20 minutes)
[unix]
stack-test:
    bash tests/stack/run.sh

# Ops proxy for owner-started pull jobs: `just egress-ops up`, then `just egress-ops down` (egress.md)
egress-ops action:
    docker compose -f deploy/compose/egress/compose.yaml --profile egress-ops {{ if action == "up" { "up -d --wait egress-ops" } else if action == "down" { "rm -sf egress-ops" } else { error("egress-ops: up or down") } }}

# Egress tests, layers 1 and 2 (tests/egress/run.sh; needs Docker and internet access)
[unix]
egress-test:
    bash tests/egress/run.sh

# Egress layer 3: installs the host rules and tests all layers (Linux with Docker Engine: CI, the Spark)
[linux]
egress-test-layer3:
    sudo bash tests/egress/layer3.sh

# Spark host rules in a lab container (tests/egress/host-lab.sh; needs Docker; AS_LAB_KEEP=1 keeps it)
[unix]
host-lab:
    bash tests/egress/host-lab.sh

# Spark host role in a container with systemd (tests/host-role/run.sh; needs Docker; about 4 minutes)
[unix]
host-role-test:
    bash tests/host-role/run.sh

# Egress lints L1-L3 (tests/egress/lint.sh; needs Docker; root for the rule load, so sudo on Linux)
[linux]
egress-lint:
    sudo bash tests/egress/lint.sh

# On Windows through Git Bash, not the WSL `bash` shim, so the tests use Docker Desktop

# The stack's recipes run in WSL2 from PowerShell too: its in-memory secrets folder, age and the dev
# key live there. `--cd` starts in this repository (plain `wsl` would start in the current folder).
wsl := "wsl -d " + env_var_or_default("AETHERSPARK_WSL_DISTRO", "Ubuntu-24.04") + " --cd '" + justfile_directory() + "' -- just"

# Bring the stack up, in WSL2 (compose-base.md)
[windows]
up:
    {{ wsl }} up

# Stop the stack and empty the in-memory secrets folder, in WSL2; volumes stay
[windows]
down:
    {{ wsl }} down

# The CA, in WSL2 (docs/runbooks/certificates.md): status | root | renew-intermediate | init | configure
[windows]
ca *args:
    {{ wsl }} ca {{ args }}

# Keycloak's administrators, in WSL2 (docs/runbooks/keycloak-admin.md): setup USERNAME
[windows]
keycloak *args:
    @{{ wsl }} keycloak {{ args }}

# Keycloak break-glass, in WSL2 (keycloak-admin.md)
[windows]
keycloak-break-glass username:
    @{{ wsl }} keycloak-break-glass {{ username }}

# The stack end to end, in WSL2 (tests/stack/run.sh; ~20 minutes)
[windows]
stack-test:
    {{ wsl }} stack-test

# Egress tests, layers 1 and 2 (tests/egress/run.sh; needs Docker and internet access)
[windows]
egress-test:
    & "$env:ProgramFiles\Git\bin\bash.exe" tests/egress/run.sh

# Spark host rules in a lab container (tests/egress/host-lab.sh; needs Docker Desktop)
[windows]
host-lab:
    & "$env:ProgramFiles\Git\bin\bash.exe" tests/egress/host-lab.sh

# Spark host role in a container with systemd (tests/host-role/run.sh; needs Docker Desktop)
[windows]
host-role-test:
    & "$env:ProgramFiles\Git\bin\bash.exe" tests/host-role/run.sh

# Egress lints L1-L3 (tests/egress/lint.sh; needs Docker; the host-rule load runs in CI only)
[windows]
egress-lint:
    & "$env:ProgramFiles\Git\bin\bash.exe" tests/egress/lint.sh

# Generate or check shared types from contracts/ (ADR-0002)
contracts *args: (pending "WP2.2/2.3" "contracts")

# Secrets (ADR-0010, docs/runbooks/secrets.md): generate | unseal | clear | check RENDERED...
# generate and unseal run in WSL2 (or on the Spark); the folder they write must be a tmpfs
secrets *args:
    {{ if os() == "windows" { "python" } else { "python3" } }} -I deploy/secret-store/secret_store.py {{ args }}

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
