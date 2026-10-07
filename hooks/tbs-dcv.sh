#!/bin/sh
# Hook « dcv » TBSCertBot -> enregistrement du challenge (et création DNS si zone gérée par Athéo).
# Les demandes aux clients sont regroupées et envoyées par « tbsdelivery notify ».
# À déclarer dans data/conf.ini de TBSCertBot :  [HOOKS] dcv = "/opt/tbs-delivery/hooks/tbs-dcv.sh"
TBS_DELIVERY_HOME="${TBS_DELIVERY_HOME:-/opt/tbs-delivery}"
PYTHON="${TBS_DELIVERY_PYTHON:-$TBS_DELIVERY_HOME/venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
cd "$TBS_DELIVERY_HOME" || exit 0
"$PYTHON" -m tbsdelivery hook dcv || echo "tbs-delivery: hook dcv en erreur (voir le journal)" >&2
exit 0
