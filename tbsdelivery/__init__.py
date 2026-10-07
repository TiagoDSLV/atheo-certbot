"""tbs-delivery : refabrication et livraison automatisées des certificats TBS.

Couche d'orchestration autour de TBSCertBot (TBS CERTIFICATS) :
- TBSCertBot refabrique (forfaits) et télécharge les certificats ;
- tbs-delivery, appelé par les hooks « download » et « dcv », identifie le
  client, fabrique les fichiers livrables et les envoie.
"""

__version__ = "0.1.0"
