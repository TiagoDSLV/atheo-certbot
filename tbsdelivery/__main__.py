"""Interface en ligne de commande : python -m tbsdelivery <commande>."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .bootstrap import CONF_SNIPPET, bootstrap_script
from .config import load_config
from .hooks import flag_interrupted_deliveries, handle_dcv, handle_download, notify_dcv
from .inventory import Inventory, classify, read_tbs_csv
from .report import build_rows, digest, summary, write_csv
from .state import State


def setup_logging(cfg: dict, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_path = Path(cfg["paths"]["log"])
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tbsdelivery", description="Refabrication et livraison des certificats TBS")
    ap.add_argument("-c", "--config", help="fichier de configuration (défaut /etc/tbs-delivery/config.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import-csv", help="créer / mettre à jour l'inventaire depuis l'export CSV TBS")
    p.add_argument("csv")

    p = sub.add_parser("report", help="état du parc et plan d'action")
    p.add_argument("--csv", help="exporter le plan d'action en CSV")
    p.add_argument("--horizon", type=int, default=60, help="jours pour la liste des échéances")

    p = sub.add_parser("bootstrap", help="générer le script de mise sous gestion TBSCertBot")
    p.add_argument("--out", help="fichier de sortie (défaut : stdout)")

    sub.add_parser("conf-snippet", help="afficher l'extrait conf.ini TBSCertBot à appliquer")

    p = sub.add_parser("hook", help="appelé par TBSCertBot (variables PHP_TBS_*)")
    p.add_argument("name", choices=["download", "dcv"])
    p.add_argument("--force", action="store_true", help="relivrer même si déjà livré")

    sub.add_parser("notify", help="envoyer / relancer les demandes DCV (après tbscertbot cron)")

    p = sub.add_parser("digest", help="synthèse interne (DCV en attente, rachats, échecs)")
    p.add_argument("--print", action="store_true", help="afficher sans envoyer")

    sub.add_parser("status", help="dernières livraisons et DCV ouvertes")

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, args.verbose)
    inv_path = cfg["paths"]["inventory"]

    if args.cmd == "import-csv":
        fresh = classify(read_tbs_csv(args.csv), int(cfg["policy"]["forfait_end_margin_days"]))
        inv = Inventory.load(inv_path)
        added, total = inv.merge_from_csv(fresh)
        inv.meta = {"source": Path(args.csv).name, "imported": date.today().isoformat()}
        inv.save(inv_path)
        n = len(inv.managed())
        print(f"Inventaire {inv_path} : {total} certificats ({added} nouveaux), {n} gérés.")
        return 0

    if args.cmd == "report":
        inv = Inventory.load(inv_path)
        rows = build_rows(inv, date.today(), int(cfg["policy"]["reissue_lead_days"]))
        print(summary(rows, date.today(), args.horizon))
        if args.csv:
            write_csv(rows, args.csv)
            print(f"\nPlan d'action exporté : {args.csv}")
        return 0

    if args.cmd == "bootstrap":
        script = bootstrap_script(cfg, Inventory.load(inv_path))
        if args.out:
            Path(args.out).write_text(script, encoding="utf-8")
            Path(args.out).chmod(0o750)
            print(f"Script écrit : {args.out}")
        else:
            print(script)
        return 0

    if args.cmd == "conf-snippet":
        print(CONF_SNIPPET)
        return 0

    if args.cmd == "hook":
        return handle_download(cfg, force=args.force) if args.name == "download" else handle_dcv(cfg)

    if args.cmd == "notify":
        n = notify_dcv(cfg)
        logging.getLogger("tbs-delivery").info("notify : %d message(s) DCV envoyé(s)", n)
        k = flag_interrupted_deliveries(cfg)
        if k:
            logging.getLogger("tbs-delivery").warning("notify : %d livraison(s) interrompue(s) signalée(s)", k)
        return 0

    if args.cmd == "digest":
        print(digest(cfg, send=not args.print))
        return 0

    if args.cmd == "status":
        st = State(cfg["paths"]["state_db"])
        print("Dernières livraisons :")
        for r in st.deliveries(20):
            print(f"  {r['created_at'][:16]}  {r['status']:<8} {r['cn']:<34} {r['client'] or '':<30} {r['recipients'] or ''}")
        print("\nDCV ouvertes :")
        for r in st.dcv_open():
            print(f"  {r['tbs_ref']}  {r['status']:<8} {r['cn']:<30} {r['record_name']} -> {r['record_value']}"
                  f"  (envois : {r['sent_count']})")
        st.close()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
