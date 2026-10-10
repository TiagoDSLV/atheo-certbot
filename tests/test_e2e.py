"""Tests de bout en bout (sans réseau) : python3 -m unittest discover -s tests -v

Simule TBSCertBot : fabrique une PKI de test, positionne les variables PHP_TBS_*
et appelle les hooks comme le ferait TBSCertBot.
"""

import email
import os
import re
import shutil
import smtplib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path
from unittest import mock

import pyzipper
import yaml
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tbsdelivery import config, hooks, package  # noqa: E402
from tbsdelivery.mailer import Mailer  # noqa: E402
from tbsdelivery.state import State  # noqa: E402
CSV = """Votre réf;Réf CA;CN;Nom Prod.;Réf TBS Certificats;Date d'expiration;Date de fin du forfait;Nom de l'org.;Raison sociale;Contact admin.;Mél du contact admin.;SANs.;
"SO/1";1;"www.client-a.fr";Sectigo SSL forfait 5 ans;1000000001;24/10/2026;18/09/2028;"CLIENT A";"CLIENT A";"Jean A";"jean@client-a.fr";"";
"SO/2";2;"mail.client-b.fr";Sectigo UCC (3+) forfait 3 ans;1000000002;16/12/2026;16/12/2026;"CLIENT B";"CLIENT B";"Paul B";"paul@client-b.fr";"mail.client-b.fr|autodiscover.client-b.fr";
"";3;"Jean A";Sectigo Certificat QSCD eIDAS qualifié pour personne physique valide 3 ans;1000000003;20/11/2026;;"";"Jean A";"Jean A";"jean@client-a.fr";"";
"SO/4";4;"vpn.client-a.fr";Sectigo SSL forfait 2 ans;1000000004;24/10/2026;24/10/2026;"CLIENT A";"CLIENT A";"Jean A";"jean@client-a.fr";"";
"SO/5";5;"vpn.client-a.fr";Sectigo SSL forfait 2 ans;1000000005;31/03/2027;24/10/2028;"CLIENT A";"CLIENT A";"Jean A";"jean@client-a.fr";"";
"""


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tbsdeliv-test-"))
        (self.tmp / "export.csv").write_text(CSV, encoding="utf-8")
        (self.tmp / "config.yaml").write_text(
            "mode: dry-run\npaths:\n  inventory: inventory.yaml\n  state_db: st/state.db\n"
            "  deliveries: st/deliveries\n  outbox: st/outbox\n  log: st/log.txt\n", encoding="utf-8")
        self.env = {**os.environ, "TBS_DELIVERY_CONFIG": str(self.tmp / "config.yaml"),
                    "PYTHONPATH": str(ROOT)}
        self.run_cli("import-csv", str(self.tmp / "export.csv"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, env=None):
        r = subprocess.run(["python3", "-m", "tbsdelivery", *args], cwd=ROOT, env=env or self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout + r.stderr

    def pki(self, ref, cn, sans=""):
        out = subprocess.run([str(ROOT / "tests/make_test_pki.sh"), str(self.tmp / "pki"), ref, cn, sans],
                             capture_output=True, text=True, check=True).stdout.split()
        return out  # key, cert, chain

    def hook(self, name, **php):
        env = dict(self.env)
        env.update({f"PHP_TBS_{k.upper()}": v for k, v in php.items()})
        return self.run_cli("hook", name, env=env)

    def outbox(self):
        return sorted((self.tmp / "st/outbox").glob("*.eml"))

    def msg(self, path):
        with open(path, "rb") as fh:
            return email.message_from_binary_file(fh, policy=policy.default)

    # ------------------------------------------------------------------ tests
    def test_classification(self):
        out = self.run_cli("report")
        self.assertIn("Refabrication sous forfait : 2", out)
        self.assertIn("Fin de forfait - rachat à valider : 1", out)
        self.assertIn("Remplacé par une commande plus récente : 1", out)
        self.assertIn("Hors périmètre (certificat non SSL) : 1", out)

    def test_delivery_after_reissue_new_reference(self):
        key, cert, chain = self.pki("1000000099", "www.client-a.fr")
        self.hook("download", reference="1000000099", cn="www.client-a.fr", san="www.client-a.fr",
                  key=key, cert=cert, chain=chain)
        mails = self.outbox()
        self.assertEqual(len(mails), 2)
        main = self.msg([m for m in mails if "Mot_de_passe" not in m.name][0])
        pwd_mail = self.msg([m for m in mails if "Mot_de_passe" in m.name][0])
        self.assertEqual(main["To"], "jean@client-a.fr")
        self.assertEqual(main["Cc"], "certificats@atheo.net")
        password = re.search(r"\n    (\S+)\n", pwd_mail.get_body().get_content()).group(1)
        zip_bytes = next(main.iter_attachments()).get_content()
        zp = self.tmp / "d.zip"
        zp.write_bytes(zip_bytes)
        with pyzipper.AESZipFile(zp) as z:
            z.setpassword(password.encode())
            names = z.namelist()
            pfx = z.read([n for n in names if n.endswith(".pfx")][0])
        self.assertEqual(len(names), 6)
        k, c, cas = pkcs12.load_key_and_certificates(pfx, password.encode())
        self.assertIn("www.client-a.fr", c.subject.rfc4514_string())
        # même certificat présenté à nouveau : pas de 2e livraison
        self.hook("download", reference="1000000099", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        self.assertEqual(len(self.outbox()), 2)

    def test_dcv_grouped_then_closed(self):
        for name in ("mail", "autodiscover"):
            self.hook("dcv", reference="1000000002", cn="mail.client-b.fr", dcv_method="dns-cname-token",
                      dcv_domain_root="client-b.fr", dcv_domain_sub=f"_abc{name}.{name}",
                      dcv_value=f"{name}123.sectigo.com", registrar="OVH SAS")
        self.run_cli("notify")
        self.run_cli("notify")  # pas de relance immédiate
        dcv_mails = [m for m in self.outbox() if "validation_DNS" in m.name]
        self.assertEqual(len(dcv_mails), 1)
        body = self.msg(dcv_mails[0]).get_body().get_content()
        self.assertIn("_abcmail.mail.client-b.fr", body)
        self.assertIn("_abcautodiscover.autodiscover.client-b.fr", body)
        key, cert, chain = self.pki("1000000002", "mail.client-b.fr", "autodiscover.client-b.fr")
        self.hook("download", reference="1000000002", cn="mail.client-b.fr", key=key, cert=cert, chain=chain)
        self.assertNotIn("dns-cname-token", self.run_cli("status").split("DCV ouvertes")[1])

    def test_key_mismatch_alerts_internal(self):
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        other_key, _, _ = self.pki("1000000098", "www.client-a.fr")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=other_key, cert=cert, chain=chain)
        mails = self.outbox()
        self.assertEqual(len(mails), 1)
        self.assertIn("ALERTE", mails[0].name)

    def test_unknown_certificate_goes_internal_only(self):
        key, cert, chain = self.pki("2000000001", "intranet.inconnu.fr")
        self.hook("download", reference="2000000001", cn="intranet.inconnu.fr", key=key, cert=cert, chain=chain)
        for m in self.outbox():
            self.assertEqual(self.msg(m)["To"], "certificats@atheo.net")

    # ------------------------------------------- surcharges par certificat (défaut 2b)
    INTERNAL = "certificats@atheo.net"

    def set_delivery(self, ref, **delivery):
        inv_path = self.tmp / "inventory.yaml"
        data = yaml.safe_load(inv_path.read_text(encoding="utf-8"))
        for c in data["certificates"]:
            if c["ref"] == ref:
                c["delivery"] = delivery
        inv_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def zip_names(self, zip_bytes, password):
        zp = self.tmp / "d.zip"
        zp.write_bytes(zip_bytes)
        with pyzipper.AESZipFile(zp) as z:
            z.setpassword(password.encode())
            names = z.namelist()
            z.read(names[0])  # lève une erreur si le mot de passe est faux
        return names

    def assert_password_internal_only(self):
        mails = [self.msg(m) for m in self.outbox()]
        self.assertEqual(len(mails), 2)
        to_client = [m for m in mails if "jean@client-a.fr" in f"{m['To']} {m['Cc'] or ''}"]
        internal_only = [m for m in mails if m["To"] == self.INTERNAL and not m["Cc"]]
        self.assertEqual(len(to_client), 1)
        self.assertEqual(len(internal_only), 1)
        password = re.search(r"Mot de passe \(ZIP et PFX\) : (\S+)",
                             internal_only[0].get_body().get_content()).group(1)
        zip_bytes = next(to_client[0].iter_attachments()).get_content()
        self.zip_names(zip_bytes, password)  # c'est bien le mot de passe de l'archive
        self.assertNotIn(password, to_client[0].get_body().get_content())

    def test_per_cert_password_internal_only(self):
        self.set_delivery("1000000001", password_channel="internal_only")
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        self.assert_password_internal_only()

    def test_unknown_password_channel_falls_back_to_internal(self):
        self.set_delivery("1000000001", password_channel="internal-only")  # faute de frappe
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        self.assert_password_internal_only()

    def test_per_cert_pfx_disabled(self):
        self.set_delivery("1000000001", pfx=False)
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        mails = self.outbox()
        main = self.msg([m for m in mails if "Mot_de_passe" not in m.name][0])
        pwd_mail = self.msg([m for m in mails if "Mot_de_passe" in m.name][0])
        password = re.search(r"\n    (\S+)\n", pwd_mail.get_body().get_content()).group(1)
        names = self.zip_names(next(main.iter_attachments()).get_content(), password)
        self.assertEqual(len(names), 5)
        self.assertFalse(any(n.endswith(".pfx") for n in names))

    # ------------------------------------------- erreurs inattendues (défaut 2)
    def assert_single_internal_alert(self):
        mails = self.outbox()
        self.assertEqual(len(mails), 1)
        self.assertIn("ALERTE", mails[0].name)
        alert = self.msg(mails[0])
        self.assertEqual(alert["To"], self.INTERNAL)
        self.assertIsNone(alert["Cc"])
        self.assertNotIn("PRIVATE KEY", alert.get_body().get_content())
        return alert

    def break_inventory(self):
        (self.tmp / "inventory.yaml").write_text("certificates: [ {ref: 1\n", encoding="utf-8")

    def test_corrupt_certificate_alerts_internal(self):
        key, _, chain = self.pki("1000000001", "www.client-a.fr")
        bad = self.tmp / "corrompu.cer"
        bad.write_text("-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n", encoding="utf-8")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=str(bad), chain=chain)
        self.assert_single_internal_alert()
        self.assertIn("failed", self.run_cli("status").split("DCV ouvertes")[0])
        self.assertIn("ValueError", (self.tmp / "st/log.txt").read_text(encoding="utf-8"))

    def test_invalid_inventory_download_alerts_internal(self):
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        self.break_inventory()
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        self.assert_single_internal_alert()

    def test_error_after_sending_says_mails_may_have_left(self):
        self.run_cli("status")  # crée la base d'état
        self.fail_final_write()
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        mails = self.outbox()
        self.assertEqual(len(mails), 3)  # ZIP + mot de passe au client, puis l'alerte
        alert_msg = self.msg([m for m in mails if "ALERTE" in m.name][0])
        alert = alert_msg.get_body().get_content()
        self.assertIn("ont pu partir", alert)
        self.assertNotIn("Rien n'a été envoyé", alert)
        self.assertEqual(alert_msg["To"], self.INTERNAL)
        self.assertIsNone(alert_msg["Cc"])
        pwd_mail = self.msg([m for m in mails if "Mot_de_passe" in m.name][0])
        password = re.search(r"\n    (\S+)\n", pwd_mail.get_body().get_content()).group(1)
        self.assertNotIn(password, alert)
        self.assertNotIn("PRIVATE KEY", alert)

    # ------------------------------------------- idempotence des livraisons (défaut 2c)
    def db(self):
        self.run_cli("status")  # crée la base d'état si besoin
        return sqlite3.connect(self.tmp / "st/state.db")

    def fail_final_write(self):
        """Simule une panne de la base au moment d'enregistrer la livraison terminée."""
        db = self.db()
        for when in ("INSERT", "UPDATE"):
            db.execute(f"CREATE TRIGGER panne_{when} BEFORE {when} ON deliveries "
                       "WHEN NEW.status IN ('sent','dry-run') "
                       "BEGIN SELECT RAISE(ABORT, 'disque plein simule'); END")
        db.commit()
        db.close()

    def statuses(self):
        db = self.db()
        rows = [r[0] for r in db.execute("SELECT status FROM deliveries ORDER BY id")]
        db.close()
        return rows

    def serial_of(self, cert):
        return format(x509.load_pem_x509_certificates(Path(cert).read_bytes())[0].serial_number, "X")

    def deliver_client_a(self, *extra):
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        env = dict(self.env)
        env.update(PHP_TBS_REFERENCE="1000000001", PHP_TBS_CN="www.client-a.fr",
                   PHP_TBS_KEY=key, PHP_TBS_CERT=cert, PHP_TBS_CHAIN=chain)
        self.run_cli("hook", "download", *extra, env=env)
        return key, cert, chain, env

    def test_crash_after_sending_no_redelivery(self):  # cas A
        self.fail_final_write()
        _, _, _, env = self.deliver_client_a()
        self.assertEqual(len(self.outbox()), 3)  # ZIP, mot de passe, alerte
        self.run_cli("hook", "download", env=env)  # TBSCertBot ou un opérateur relance
        self.assertEqual(len(self.outbox()), 3)  # aucune 2e livraison

    def test_partial_send_blocks_redelivery(self):  # cas B
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        cfg = config.load_config(self.tmp / "config.yaml")
        real_send, calls = Mailer.send, []

        def send(mailer, msg):
            calls.append(msg["Subject"])
            if len(calls) == 2:  # le mail du mot de passe échoue
                raise smtplib.SMTPServerDisconnected("connexion perdue")
            return real_send(mailer, msg)

        php = {"PHP_TBS_REFERENCE": "1000000001", "PHP_TBS_CN": "www.client-a.fr",
               "PHP_TBS_KEY": key, "PHP_TBS_CERT": cert, "PHP_TBS_CHAIN": chain}
        with mock.patch.dict(os.environ, php), mock.patch.object(Mailer, "send", send):
            hooks.handle_download(cfg)
        self.assertEqual(self.statuses(), ["partial"])
        alerts = [m for m in self.outbox() if "ALERTE" in m.name]
        self.assertEqual(len(alerts), 1)
        alert = self.msg(alerts[0])
        self.assertEqual(alert["To"], self.INTERNAL)
        body = alert.get_body().get_content()
        self.assertIn("TBS_DELIVERY_FORCE=1", body)
        self.assertNotIn("PRIVATE KEY", body)
        before = len(self.outbox())
        with mock.patch.dict(os.environ, php):
            hooks.handle_download(cfg)  # relance automatique : rien
        self.assertEqual(len(self.outbox()), before)
        with mock.patch.dict(os.environ, php):
            hooks.handle_download(cfg, force=True)  # décision humaine : nouvelle archive
        self.assertEqual(len(self.outbox()), before + 2)

    def test_delivery_in_progress_skips(self):  # cas C
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        db = self.db()
        db.execute("INSERT INTO deliveries(tbs_ref, serial, status, created_at) VALUES (?,?,?,?)",
                   ("1000000001", self.serial_of(cert), "sending", "2026-01-01T00:00:00+00:00"))
        db.commit()
        db.close()
        self.hook("download", reference="1000000001", cn="www.client-a.fr", key=key, cert=cert, chain=chain)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(list((self.tmp / "st").glob("deliveries/**/*.zip")), [])

    def test_reserve_delivery_only_once(self):
        st = State(self.tmp / "st/state.db")
        first = st.reserve_delivery(serial="ABC", tbs_ref="1", cn="www.client-a.fr")
        second = State(self.tmp / "st/state.db").reserve_delivery(serial="ABC", tbs_ref="1", cn="www.client-a.fr")
        forced = st.reserve_delivery(serial="ABC", tbs_ref="1", cn="www.client-a.fr", force=True)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(forced)

    def test_force_redelivers_after_sent(self):
        self.deliver_client_a()
        self.assertEqual(len(self.outbox()), 2)
        self.deliver_client_a("--force")
        self.assertEqual(len(self.outbox()), 4)

    def two_hours_ago(self):
        return (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()

    def test_notify_flags_interrupted_delivery_once(self):
        db = self.db()
        db.execute("INSERT INTO deliveries(tbs_ref, cn, serial, status, created_at) VALUES (?,?,?,?,?)",
                   ("1000000001", "www.client-a.fr", "ABC", "sending", self.two_hours_ago()))
        db.commit()
        db.close()
        self.run_cli("notify")
        alerts = [m for m in self.outbox() if "ALERTE" in m.name]
        self.assertEqual(len(alerts), 1)
        self.assertIn("www.client-a.fr", self.msg(alerts[0])["Subject"])
        self.assertEqual(self.statuses(), ["uncertain"])
        self.run_cli("notify")
        self.assertEqual(len([m for m in self.outbox() if "ALERTE" in m.name]), 1)

    def test_digest_lists_partial_and_uncertain(self):
        db = self.db()
        for cn, status in (("www.client-a.fr", "partial"), ("vpn.client-a.fr", "uncertain")):
            db.execute("INSERT INTO deliveries(tbs_ref, cn, serial, status, created_at) VALUES (?,?,?,?,?)",
                       ("1000000001", cn, cn, status, self.two_hours_ago()))
        db.commit()
        db.close()
        out = self.run_cli("digest", "--print")
        self.assertIn("Livraisons à vérifier", out)
        self.assertIn("www.client-a.fr", out.split("Livraisons à vérifier")[1])
        self.assertIn("vpn.client-a.fr", out.split("Livraisons à vérifier")[1])

    def test_zip_names_unique_within_same_second(self):
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        frozen = datetime.now(timezone.utc)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        out = self.tmp / "zips"
        with mock.patch.object(package, "datetime", FrozenDatetime):
            first = package.build_package(key_path=key, cert_path=cert, chain_path=chain, out_dir=out, client="A")
            second = package.build_package(key_path=key, cert_path=cert, chain_path=chain, out_dir=out, client="A")
        self.assertNotEqual(first.zip_path, second.zip_path)
        self.assertTrue(first.zip_path.exists() and second.zip_path.exists())

    def test_refused_reservation_deletes_only_its_own_zip(self):
        key, cert, chain = self.pki("1000000001", "www.client-a.fr")
        db = self.db()
        db.execute("INSERT INTO deliveries(tbs_ref, serial, status, created_at) VALUES (?,?,?,?)",
                   ("1000000001", self.serial_of(cert), "sending", self.two_hours_ago()))
        db.commit()
        db.close()
        cfg = config.load_config(self.tmp / "config.yaml")
        other = self.tmp / "st/deliveries/client-a/www.client-a.fr/archive-du-gagnant.zip"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"zip")
        php = {"PHP_TBS_REFERENCE": "1000000001", "PHP_TBS_CN": "www.client-a.fr",
               "PHP_TBS_KEY": key, "PHP_TBS_CERT": cert, "PHP_TBS_CHAIN": chain}
        # contrôle anticipé neutralisé : la course se joue à la réservation, après la fabrication
        with mock.patch.dict(os.environ, php), mock.patch.object(hooks, "peek_serial", return_value=None):
            hooks.handle_download(cfg)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(list(other.parent.glob("*.zip")), [other])

    def test_smtp_quit_network_error_counts_as_sent(self):
        cfg = config.load_config(self.tmp / "config.yaml")
        sent = []

        class FakeSMTP:  # aucun serveur réel n'est contacté
            def __init__(self, *args, **kwargs):
                pass

            def send_message(self, msg):
                sent.append(msg)

            def quit(self):
                raise ConnectionResetError("connexion coupée après l'acceptation du mail")

        mailer = Mailer(cfg)
        mailer.dry_run = False
        msg = mailer.build(to=["jean@client-a.fr"], cc=None, subject="test", body="corps")
        with mock.patch.object(smtplib, "SMTP", FakeSMTP), mock.patch.object(smtplib, "SMTP_SSL", FakeSMTP):
            self.assertEqual(mailer.send(msg), "sent")
        self.assertEqual(len(sent), 1)

    def test_invalid_inventory_dcv_alerts_internal(self):
        self.break_inventory()
        self.hook("dcv", reference="1000000002", cn="mail.client-b.fr", dcv_method="dns-cname-token",
                  dcv_domain_root="client-b.fr", dcv_domain_sub="_abc.mail", dcv_value="x.sectigo.com",
                  registrar="OVH SAS")
        self.assert_single_internal_alert()


if __name__ == "__main__":
    unittest.main()
