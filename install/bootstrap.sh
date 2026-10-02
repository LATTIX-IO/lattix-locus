#!/usr/bin/env sh
set -eu

BOOTSTRAP_DIR="${TMPDIR:-/tmp}/frontier-install"
INSTALLER_URL="https://raw.githubusercontent.com/LATTIX-IO/lattix-xfrontier/main/install/frontier-installer.py"
LOCAL_INSTALLER="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/frontier-installer.py"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=12

echo "==> Lattix xFrontier bootstrap"
echo "==> Preparing installer workspace"
mkdir -p "$BOOTSTRAP_DIR"

prepend_path() {
  candidate="$1"
  if [ -z "$candidate" ] || [ ! -d "$candidate" ]; then
    return 0
  fi
  case ":${PATH:-}:" in
    *":$candidate:"*) ;;
    *) PATH="$candidate:${PATH:-}" ;;
  esac
  export PATH
}

refresh_common_paths() {
  prepend_path /opt/homebrew/bin
  prepend_path /opt/homebrew/sbin
  prepend_path /usr/local/bin
  prepend_path /usr/local/sbin
  prepend_path "$HOME/.local/bin"
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

python_is_supported() {
  candidate="$1"
  "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)" >/dev/null 2>&1
}

select_python() {
  PYTHON_BIN=""
  for candidate in python3 python; do
    if have_command "$candidate" && python_is_supported "$candidate"; then
      PYTHON_BIN="$(command -v "$candidate")"
      return 0
    fi
  done
  return 1
}

run_with_sudo() {
  if have_command sudo; then
    sudo "$@"
  else
    "$@"
  fi
}

install_homebrew() {
  if have_command brew; then
    return 0
  fi
  echo "==> Installing Homebrew"
  local homebrew_install_script
  homebrew_install_script="$(mktemp "${TMPDIR:-/tmp}/homebrew-install.XXXXXX.sh")"
  if ! curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$homebrew_install_script"; then
    rm -f "$homebrew_install_script"
    echo "Failed to download Homebrew installer." >&2
    exit 1
  fi
  /bin/bash "$homebrew_install_script"
  rm -f "$homebrew_install_script"
  refresh_common_paths
  if ! have_command brew; then
    echo "Homebrew installation completed, but 'brew' is still not on PATH. Reopen the shell and retry." >&2
    exit 1
  fi
}

ensure_macos_python() {
  refresh_common_paths
  if select_python; then
    return 0
  fi
  install_homebrew
  echo "==> Installing Python via Homebrew"
  brew install python
  refresh_common_paths
  if ! select_python; then
    echo "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required after bootstrap installation." >&2
    exit 1
  fi
}

ensure_linux_python() {
  refresh_common_paths
  if select_python; then
    return 0
  fi
  if have_command apt-get; then
    echo "==> Installing Python prerequisites via apt-get"
    run_with_sudo apt-get update
    run_with_sudo apt-get install -y python3.12 python3.12-venv python3-pip || \
      run_with_sudo apt-get install -y python3 python3-venv python3-pip
  elif have_command dnf; then
    echo "==> Installing Python prerequisites via dnf"
    run_with_sudo dnf install -y python3 python3-pip
  elif have_command yum; then
    echo "==> Installing Python prerequisites via yum"
    run_with_sudo yum install -y python3 python3-pip
  fi
  refresh_common_paths
  if ! select_python; then
    echo "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but could not be installed automatically on this host." >&2
    exit 1
  fi
}

wait_for_docker() {
  attempts=0
  while :; do
    if docker_output="$(docker info 2>&1)"; then
      return 0
    fi
    docker_output_lc="$(printf '%s' "$docker_output" | tr '[:upper:]' '[:lower:]')"
    case "$docker_output_lc" in
      *permission\ denied*)
        echo "Docker is installed but this user cannot access the daemon. Add your user to the docker group and sign in again, or rerun the bootstrap with sudo." >&2
        exit 1
        ;;
    esac
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 60 ]; then
      echo "Docker is installed but the daemon is not ready. Start Docker and rerun the bootstrap." >&2
      exit 1
    fi
    sleep 2
  done
}

ensure_macos_docker() {
  refresh_common_paths
  if ! have_command docker; then
    install_homebrew
    echo "==> Installing Docker Desktop via Homebrew"
    brew install --cask docker
    refresh_common_paths
  fi
  if ! have_command docker; then
    echo "Docker Desktop installation did not expose the docker CLI on PATH." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "==> Starting Docker Desktop"
    open -a Docker >/dev/null 2>&1 || true
    wait_for_docker
  fi
}

ensure_linux_docker() {
  if ! have_command docker; then
    if have_command apt-get; then
      echo "==> Installing Docker via apt-get"
      run_with_sudo apt-get update
      run_with_sudo apt-get install -y docker.io docker-compose-plugin
    elif have_command dnf; then
      echo "==> Installing Docker via dnf"
      run_with_sudo dnf install -y docker docker-compose-plugin
    elif have_command yum; then
      echo "==> Installing Docker via yum"
      run_with_sudo yum install -y docker docker-compose-plugin
    fi
  fi
  if have_command systemctl; then
    run_with_sudo systemctl enable --now docker >/dev/null 2>&1 || true
  fi
  if ! have_command docker; then
    echo "Docker is required but could not be installed automatically on this host." >&2
    exit 1
  fi
  wait_for_docker
}

detect_and_install_prerequisites() {
  refresh_common_paths
  system_name="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$system_name" in
    darwin)
      ensure_macos_python
      ensure_macos_docker
      ;;
    linux)
      ensure_linux_python
      ensure_linux_docker
      ;;
    *)
      echo "Unsupported POSIX platform '$system_name'. Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and Docker manually, then rerun the bootstrap." >&2
      exit 1
      ;;
  esac
}

detect_and_install_prerequisites

PYTHON_BIN=""
if ! select_python; then
  echo "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but was not found in PATH after prerequisite bootstrap."
  exit 1
fi

if [ -f "$LOCAL_INSTALLER" ]; then
  echo "==> Using local checkout installer"
  INSTALLER_PATH="$LOCAL_INSTALLER"
elif command -v curl >/dev/null 2>&1; then
  echo "==> Downloading installer"
  curl -fsSL "$INSTALLER_URL" -o "$BOOTSTRAP_DIR/frontier-installer.py"
  INSTALLER_PATH="$BOOTSTRAP_DIR/frontier-installer.py"
else
  echo "curl is required to download the installer."
  exit 1
fi

echo "==> Launching interactive installer"
if [ -z "${FRONTIER_INSTALLER_OUTPUT:-}" ] && [ -t 0 ] && [ -t 1 ]; then
  export FRONTIER_INSTALLER_OUTPUT=tui
fi
if "$PYTHON_BIN" "$INSTALLER_PATH"; then
  :
else
  installer_exit_code=$?
  echo "Installer failed with exit code $installer_exit_code. The current shell was left intact so you can inspect the error and retry." >&2
  exit "$installer_exit_code"
fi
