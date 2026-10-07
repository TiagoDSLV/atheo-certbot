#!/bin/sh
# Cycle complet lancé par le timer systemd (2 fois par jour) :
#   1. TBSCertBot : téléchargement + refabrications planifiées (déclenche les hooks dcv / download)
#   2. tbs-delivery : envoi / relance des demandes DCV regroupées par certificat
set -u
TBS_HOME="${TBS_HOME:-/opt/tbscertbot}"
TBS_DELIVERY_HOME="${TBS_DELIVERY_HOME:-/opt/tbs-delivery}"
PYTHON="$TBS_DELIVERY_HOME/venv/bin/python"
cd "$TBS_HOME" && /usr/bin/php tbscertbot.php cron
cd "$TBS_DELIVERY_HOME" && "$PYTHON" -m tbsdelivery notify
