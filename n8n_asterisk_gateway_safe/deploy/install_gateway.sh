#!/usr/bin/env bash
set -euo pipefail

# This installer intentionally contains no real secrets.
# Admin may override values with environment variables before running it:
#   REPO_URL=https://github.com/ORG/REPO.git PROJECT_USER=danil_z bash install_gateway.sh

PROJECT_DIR="${PROJECT_DIR:-/opt/n8n_asterisk_gateway}"
PROJECT_USER="${PROJECT_USER:-danil_z}"
PROJECT_GROUP="${PROJECT_GROUP:-asterisk}"
REPO_URL="${REPO_URL:-https://github.com/YOUR_GITHUB_USERNAME/n8n-asterisk-gateway.git}"
BRANCH="${BRANCH:-main}"

GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${GATEWAY_PORT:-8088}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://127.0.0.1:8088}"

AMI_HOST="${AMI_HOST:-127.0.0.1}"
AMI_PORT="${AMI_PORT:-5038}"
AMI_USERNAME="${AMI_USERNAME:-n8n_gateway}"
AMI_SECRET="${AMI_SECRET:-}"

ASTERISK_TRUNK="${ASTERISK_TRUNK:-provider}"
ASTERISK_CHANNEL_TEMPLATE="${ASTERISK_CHANNEL_TEMPLATE:-SIP/{phone}@provider}"
ASTERISK_CONTEXT="${ASTERISK_CONTEXT:-n8n-gateway-call}"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root: sudo bash deploy/install_gateway.sh"
  exit 1
fi

if [ -z "${AMI_SECRET}" ]; then
  echo "ERROR: AMI_SECRET is empty. Pass it only at install time, not in GitHub."
  echo "Example: AMI_SECRET='real-secret' REPO_URL='https://github.com/me/repo.git' sudo -E bash install_gateway.sh"
  exit 1
fi

if command -v apt >/dev/null 2>&1; then
  apt update
  apt install -y git unzip python3-venv python3-pip ffmpeg curl
elif command -v yum >/dev/null 2>&1; then
  yum install -y git unzip python3 python3-pip ffmpeg curl
else
  echo "ERROR: unsupported package manager. Install git/python3/ffmpeg manually."
  exit 1
fi

id asterisk >/dev/null 2>&1 || { echo "ERROR: Linux user 'asterisk' not found"; exit 1; }
id "${PROJECT_USER}" >/dev/null 2>&1 || { echo "ERROR: Linux user '${PROJECT_USER}' not found"; exit 1; }
usermod -aG asterisk "${PROJECT_USER}" || true

if [ -d "${PROJECT_DIR}/.git" ]; then
  cd "${PROJECT_DIR}"
  git fetch origin
  git checkout "${BRANCH}"
  git pull origin "${BRANCH}"
else
  rm -rf "${PROJECT_DIR}"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${PROJECT_DIR}"
fi

cd "${PROJECT_DIR}"
python3 -m venv .venv
"${PROJECT_DIR}/.venv/bin/pip" install --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install -r requirements.txt

cat > "${PROJECT_DIR}/.env" <<EOF
HOST=${GATEWAY_HOST}
PORT=${GATEWAY_PORT}
GATEWAY_BASE_URL=${GATEWAY_BASE_URL}
GATEWAY_TOKEN=

AMI_HOST=${AMI_HOST}
AMI_PORT=${AMI_PORT}
AMI_USERNAME=${AMI_USERNAME}
AMI_SECRET=${AMI_SECRET}
AMI_CONNECT_TIMEOUT=5

ASTERISK_CHANNEL_TECH=SIP
ASTERISK_TRUNK=${ASTERISK_TRUNK}
ASTERISK_CHANNEL_TEMPLATE=${ASTERISK_CHANNEL_TEMPLATE}
ASTERISK_CONTEXT=${ASTERISK_CONTEXT}
ASTERISK_EXTENSION=s
ASTERISK_PRIORITY=1
CALLER_ID=n8n-gateway <70000000000>
ORIGINATE_TIMEOUT_MS=45000

AUDIO_WORK_DIR=/var/lib/n8n-asterisk-gateway
ASTERISK_SOUNDS_DIR=/var/lib/asterisk/sounds/n8n-gateway
ASTERISK_RECORDINGS_DIR=/var/spool/asterisk/monitor/n8n-gateway

RECORD_MAX_SECONDS=10
RECORD_SILENCE_SECONDS=2
RECORD_BEEP=false
MAX_TURNS=20

DOWNLOAD_TIMEOUT_SECONDS=30
N8N_TIMEOUT_SECONDS=120
EOF
chmod 600 "${PROJECT_DIR}/.env"

mkdir -p \
  /var/lib/n8n-asterisk-gateway \
  /var/lib/asterisk/sounds/n8n-gateway \
  /var/spool/asterisk/monitor/n8n-gateway

chown -R "${PROJECT_USER}:${PROJECT_GROUP}" "${PROJECT_DIR}"
chown -R "${PROJECT_USER}:${PROJECT_GROUP}" /var/lib/n8n-asterisk-gateway
chown -R asterisk:asterisk /var/lib/asterisk/sounds/n8n-gateway
chown -R asterisk:asterisk /var/spool/asterisk/monitor/n8n-gateway
chmod -R 775 /var/lib/n8n-asterisk-gateway /var/lib/asterisk/sounds/n8n-gateway /var/spool/asterisk/monitor/n8n-gateway

install -m 755 -o asterisk -g asterisk "${PROJECT_DIR}/agi/n8n_gateway_agi.py" /var/lib/asterisk/agi-bin/n8n_gateway_agi.py
install -m 755 -o asterisk -g asterisk "${PROJECT_DIR}/agi/n8n_gateway_finalize.py" /var/lib/asterisk/agi-bin/n8n_gateway_finalize.py

cat > /etc/systemd/system/n8n-asterisk-gateway.service <<EOF
[Unit]
Description=n8n Asterisk Gateway
After=network.target asterisk.service

[Service]
Type=simple
User=${PROJECT_USER}
Group=${PROJECT_GROUP}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn app.main:app --host ${GATEWAY_HOST} --port ${GATEWAY_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/update-n8n-asterisk-gateway <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd ${PROJECT_DIR}
git fetch origin
git checkout ${BRANCH}
git pull origin ${BRANCH}
${PROJECT_DIR}/.venv/bin/pip install -r requirements.txt
install -m 755 -o asterisk -g asterisk ${PROJECT_DIR}/agi/n8n_gateway_agi.py /var/lib/asterisk/agi-bin/n8n_gateway_agi.py
install -m 755 -o asterisk -g asterisk ${PROJECT_DIR}/agi/n8n_gateway_finalize.py /var/lib/asterisk/agi-bin/n8n_gateway_finalize.py
systemctl restart n8n-asterisk-gateway
systemctl status n8n-asterisk-gateway --no-pager
EOF
chmod +x /usr/local/bin/update-n8n-asterisk-gateway

systemctl daemon-reload
systemctl enable n8n-asterisk-gateway
systemctl restart n8n-asterisk-gateway
systemctl status n8n-asterisk-gateway --no-pager || true

echo "OK: gateway installed. Check: curl http://127.0.0.1:8088/health"
echo "Remember to configure /etc/asterisk/manager.conf, sip.conf, extensions.conf on the server."
