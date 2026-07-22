#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
SERVICE_NAME="openclaw-kb-manager"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PNPM_BIN="${PNPM_BIN:-pnpm}"
OPENCLAW_BIN="${OPENCLAW_BIN:-}"
PORT="${PORT:-}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
INSTALL_SERVICE=0
RESTART_SERVICE=0

usage() {
  cat <<'EOF'
用法:
  ./scripts/deploy.sh [选项]

默认行为:
  检查服务器依赖，安装 Python 依赖，并构建前端静态文件。

可选项:
  --env-file PATH       指定环境变量文件，默认是 .env
  --app-dir PATH        指定项目目录，默认是脚本所在项目根目录
  --python PATH         指定 Python 命令或路径，默认是 python3
  --pnpm PATH           指定 pnpm 命令或路径，默认是 pnpm
  --port PORT           systemd 服务监听端口，默认读取 .env 的 APP_PORT 或 8000
  --service-name NAME   systemd 服务名，默认是 openclaw-kb-manager
  --run-user NAME       systemd 服务运行用户，默认是当前用户
  --install-service     写入 systemd 并启动服务，需要 sudo 权限
  --restart             读取最新 .env，刷新并重启已安装的 systemd 服务
  -h, --help            显示帮助

说明:
  脚本不会创建或覆盖 .env，也不会删除知识库目录或旧文件。
  生产部署前请手动配置 .env，并确保 openclaw 命令已安装且当前用户可执行。
EOF
}

fail() {
  printf '错误: %s\n' "$1" >&2
  exit 1
}

info() {
  printf '[deploy] %s\n' "$1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "找不到命令: $1"
}

read_env_value() {
  local key="$1"
  sed -n "s/^${key}=[[:space:]]*//p" "$ENV_FILE" | tail -n 1
}

validate_env() {
  [[ -f "$ENV_FILE" ]] || fail "找不到 $ENV_FILE。请先复制 .env.example 并完成生产配置。"

  grep -Eq '^APP_SECRET_KEY=[^[:space:]]+' "$ENV_FILE" || \
    fail ".env 必须设置 APP_SECRET_KEY"
  grep -Eq '^ADMIN_INITIAL_PASSWORD=[^[:space:]]+' "$ENV_FILE" || \
    fail ".env 必须设置 ADMIN_INITIAL_PASSWORD"

  if grep -Eq '^APP_SECRET_KEY=(replace-with-a-long-random-value|local-dev-secret-change-me)[[:space:]]*$' "$ENV_FILE"; then
    fail ".env 中的 APP_SECRET_KEY 仍是示例值"
  fi
  if grep -Eq '^ADMIN_INITIAL_PASSWORD=change-me-before-use[[:space:]]*$' "$ENV_FILE"; then
    fail ".env 中的 ADMIN_INITIAL_PASSWORD 仍是示例值"
  fi
}

version_at_least_3_10() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
}

sed_escape() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

install_service_unit() {
  require_command systemctl
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
  else
    require_command sudo
    SUDO=(sudo)
  fi

  id "$RUN_USER" >/dev/null 2>&1 || fail "systemd 运行用户不存在: $RUN_USER"
  RUN_GROUP="$(id -gn "$RUN_USER")"
  SYSTEM_PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
  SERVICE_TEMPLATE="$APP_DIR/scripts/openclaw-kb-manager.service.template"
  [[ -f "$SERVICE_TEMPLATE" ]] || fail "找不到 systemd 模板: $SERVICE_TEMPLATE"

  APP_DIR_ESCAPED="$(sed_escape "$APP_DIR")"
  ENV_FILE_ESCAPED="$(sed_escape "$ENV_FILE")"
  RUN_USER_ESCAPED="$(sed_escape "$RUN_USER")"
  RUN_GROUP_ESCAPED="$(sed_escape "$RUN_GROUP")"
  PORT_ESCAPED="$(sed_escape "$PORT")"
  OPENCLAW_DIR_ESCAPED="$(sed_escape "$OPENCLAW_DIR")"
  SYSTEM_PATH_ESCAPED="$(sed_escape "$SYSTEM_PATH")"

  info "写入 systemd 服务: ${SERVICE_NAME}.service"
  sed \
    -e "s|@APP_DIR@|$APP_DIR_ESCAPED|g" \
    -e "s|@ENV_FILE@|$ENV_FILE_ESCAPED|g" \
    -e "s|@RUN_USER@|$RUN_USER_ESCAPED|g" \
    -e "s|@RUN_GROUP@|$RUN_GROUP_ESCAPED|g" \
    -e "s|@PORT@|$PORT_ESCAPED|g" \
    -e "s|@OPENCLAW_DIR@|$OPENCLAW_DIR_ESCAPED|g" \
    -e "s|@SYSTEM_PATH@|$SYSTEM_PATH_ESCAPED|g" \
    "$SERVICE_TEMPLATE" | \
    "${SUDO[@]}" install -m 0644 /dev/stdin "/etc/systemd/system/${SERVICE_NAME}.service"

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable "$SERVICE_NAME.service"
  "${SUDO[@]}" systemctl restart "$SERVICE_NAME.service"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || fail "--env-file 需要参数"
      ENV_FILE="$2"
      shift 2
      ;;
    --app-dir)
      [[ $# -ge 2 ]] || fail "--app-dir 需要参数"
      APP_DIR="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || fail "--python 需要参数"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --pnpm)
      [[ $# -ge 2 ]] || fail "--pnpm 需要参数"
      PNPM_BIN="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || fail "--port 需要参数"
      PORT="$2"
      shift 2
      ;;
    --service-name)
      [[ $# -ge 2 ]] || fail "--service-name 需要参数"
      SERVICE_NAME="$2"
      shift 2
      ;;
    --run-user)
      [[ $# -ge 2 ]] || fail "--run-user 需要参数"
      RUN_USER="$2"
      shift 2
      ;;
    --install-service)
      INSTALL_SERVICE=1
      shift
      ;;
    --restart)
      RESTART_SERVICE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数: $1。使用 --help 查看用法。"
      ;;
  esac
done

[[ -d "$APP_DIR" ]] || fail "项目目录不存在: $APP_DIR"
APP_DIR="$(cd -- "$APP_DIR" && pwd)"

if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$APP_DIR/$ENV_FILE"
fi
ENV_FILE="$(cd -- "$(dirname -- "$ENV_FILE")" && pwd)/$(basename -- "$ENV_FILE")"

[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_.@-]+$ ]] || \
  fail "systemd 服务名包含非法字符: $SERVICE_NAME"

validate_env

if [[ -z "$PORT" ]]; then
  PORT="$(read_env_value APP_PORT)"
fi
PORT="${PORT:-8000}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || \
  fail "端口必须是 1-65535 之间的数字: $PORT"

if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$(read_env_value OPENCLAW_BIN)"
fi
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_PATH="$(command -v "$OPENCLAW_BIN" || true)"
[[ "$OPENCLAW_PATH" == /* && -x "$OPENCLAW_PATH" ]] || \
  fail "找不到可执行的 OpenClaw: $OPENCLAW_BIN。请在 .env 中设置 OPENCLAW_BIN 的绝对路径，例如 /home/ubuntu/.npm-global/bin/openclaw"
OPENCLAW_DIR="$(dirname -- "$OPENCLAW_PATH")"

if (( RESTART_SERVICE == 1 )); then
  install_service_unit
  info "systemd 服务已刷新并重启: $SERVICE_NAME"
  info "查看日志: ${SUDO[*]:-} journalctl -u $SERVICE_NAME -f"
  exit 0
fi

require_command "$PYTHON_BIN"
require_command "$PNPM_BIN"
require_command node

if ! version_at_least_3_10 "$PYTHON_BIN"; then
  fail "Python 版本必须是 3.10 或更高"
fi

VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
[[ -x "$VENV_PYTHON" ]] || {
  info "创建 Python 虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
}

version_at_least_3_10 "$VENV_PYTHON" || fail "虚拟环境中的 Python 版本必须是 3.10 或更高"

info "安装 Python 依赖"
"$VENV_PYTHON" -m pip install -r "$APP_DIR/requirements.txt"

info "安装前端依赖"
CI=true "$PNPM_BIN" --dir "$APP_DIR/frontend" install --frozen-lockfile

info "构建前端"
CI=true "$PNPM_BIN" --dir "$APP_DIR/frontend" build

if (( INSTALL_SERVICE == 0 )); then
  info "构建完成。直接运行命令:"
  printf '  cd %q && %q -m uvicorn --env-file %q backend.main:app --host 0.0.0.0 --port %q --workers 1\n' \
    "$APP_DIR" "$VENV_PYTHON" "$ENV_FILE" "$PORT"
  info "需要由 systemd 托管时，再执行: ./scripts/deploy.sh --install-service"
  exit 0
fi

install_service_unit

info "systemd 服务已部署并重启: $SERVICE_NAME"
info "查看日志: ${SUDO[*]:-} journalctl -u $SERVICE_NAME -f"
