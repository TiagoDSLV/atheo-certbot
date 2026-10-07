"""Tests de bout en bout (sans réseau) : python3 -m unittest discover -s tests -v

Simule TBSCertBot : fabrique une PKI de test, positionne les variables PHP_TBS_*
et appelle les hooks comme le ferait TBSCertBot.
"""

import email
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from email import policy
from pathlib import Path

import pyzipper
from cryptography.hazmat.primitives.serialization import pkcs12

ROOT = Path(__file__).resolve().parent.parent
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


if __name__ == "__main__":
    unittest.main()
