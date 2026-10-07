#!/bin/sh
# Hook « download » TBSCertBot -> fabrication du livrable et envoi au client.
# À déclarer dans data/conf.ini de TBSCertBot :  [HOOKS] download = "/opt/tbs-delivery/hooks/tbs-download.sh"
# Les variables PHP_TBS_* sont transmises par TBSCertBot.
# Ne jamais faire échouer TBSCertBot : les erreurs sont journalisées et signalées par mail.
TBS_DELIVERY_HOME="${TBS_DELIVERY_HOME:-/opt/tbs-delivery}"
PYTHON="${TBS_DELIVERY_PYTHON:-$TBS_DELIVERY_HOME/venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
cd "$TBS_DELIVERY_HOME" || exit 0
"$PYTHON" -m tbsdelivery hook download || echo "tbs-delivery: hook download en erreur (voir le journal)" >&2
exit 0
