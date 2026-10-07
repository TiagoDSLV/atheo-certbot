#!/bin/sh
# Installation de tbs-delivery sur le hub TBSCertBot (Debian/Ubuntu, en root).
# Hypothèses : TBSCertBot installé dans /opt/tbscertbot et exécuté par l'utilisateur « tbscertbot ».
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/tbs-delivery
USER_TBS=tbscertbot

apt-get install -y python3 python3-venv php-cli php-curl php-xml openssl >/dev/null
id "$USER_TBS" >/dev/null 2>&1 || useradd --system --home /opt/tbscertbot --shell /usr/sbin/nologin "$USER_TBS"

mkdir -p "$DEST" /etc/tbs-delivery /var/lib/tbs-delivery /var/log/tbs-delivery
cp -r "$SRC"/tbsdelivery "$SRC"/hooks "$SRC"/deploy "$SRC"/requirements.txt "$DEST"/
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"

[ -f /etc/tbs-delivery/config.yaml ] || cp "$SRC/config.example.yaml" /etc/tbs-delivery/config.yaml
[ -f /etc/tbs-delivery/env ] || printf 'TBS_DELIVERY_SMTP_PASSWORD=\nGANDI_TOKEN=\nTBS_DELIVERY_CONFIG=/etc/tbs-delivery/config.yaml\n' > /etc/tbs-delivery/env

chown -R root:"$USER_TBS" "$DEST" /etc/tbs-delivery
chmod 640 /etc/tbs-delivery/config.yaml; chmod 600 /etc/tbs-delivery/env; chown "$USER_TBS" /etc/tbs-delivery/env
chown -R "$USER_TBS":"$USER_TBS" /var/lib/tbs-delivery /var/log/tbs-delivery
chmod 700 /var/lib/tbs-delivery
chmod 755 "$DEST"/hooks/*.sh "$DEST"/deploy/*.sh

cp "$DEST"/deploy/*.service "$DEST"/deploy/*.timer /etc/systemd/system/
systemctl daemon-reload
echo "Installé. Étapes suivantes (voir README) :"
echo " 1. éditer /etc/tbs-delivery/config.yaml et /etc/tbs-delivery/env"
echo " 2. sudo -u $USER_TBS env TBS_DELIVERY_CONFIG=/etc/tbs-delivery/config.yaml $DEST/venv/bin/python -m tbsdelivery import-csv export_tbs.csv"
echo " 3. appliquer l'extrait conf.ini (python -m tbsdelivery conf-snippet) puis le script bootstrap"
echo " 4. systemctl enable --now tbs-delivery-cycle.timer tbs-delivery-digest.timer"
