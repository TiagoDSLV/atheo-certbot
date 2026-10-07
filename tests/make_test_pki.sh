#!/bin/sh
# Fabrique une petite PKI de test (racine -> intermédiaire -> feuille) qui imite
# les fichiers déposés par TBSCertBot : key-<ref>.pkey, cert-<ref>.cer, chain-<ref>.txt
# usage : make_test_pki.sh <dossier> <ref> <cn> [san1,san2,...]
set -eu
D="$1"; REF="$2"; CN="$3"; SANS="${4:-}"
mkdir -p "$D"; cd "$D"
if [ ! -f root.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout root.key -out root.crt -days 3650 \
    -subj "/C=FR/O=Test PKI/CN=Test Root CA" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -keyout inter.key -out inter.csr \
    -subj "/C=FR/O=Test PKI/CN=Test Intermediate CA" >/dev/null 2>&1
  printf "basicConstraints=critical,CA:true,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n" > ca.ext
  openssl x509 -req -in inter.csr -CA root.crt -CAkey root.key -CAcreateserial -out inter.crt \
    -days 1825 -extfile ca.ext >/dev/null 2>&1
fi
ALT="DNS:$CN"
if [ -n "$SANS" ]; then
  for s in $(echo "$SANS" | tr ',' ' '); do ALT="$ALT,DNS:$s"; done
fi
openssl req -newkey rsa:2048 -nodes -keyout "key-$REF.pkey" -out "$REF.csr" -subj "/C=FR/CN=$CN" >/dev/null 2>&1
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\n" "$ALT" > "$REF.ext"
openssl x509 -req -in "$REF.csr" -CA inter.crt -CAkey inter.key -CAcreateserial -out "cert-$REF.cer" \
  -days 199 -extfile "$REF.ext" >/dev/null 2>&1
cat inter.crt root.crt > "chain-$REF.txt"
echo "$D/key-$REF.pkey $D/cert-$REF.cer $D/chain-$REF.txt"
