# Bebrid Magic Windows 1.0.3

Cette version remplace l'interface HTML/PyWebView de la 1.0.0 par une interface
Windows native Tkinter.

## Pourquoi cette version

La 1.0.0 pouvait rester bloquée sur « Chargement… » si le pont JavaScript/Python
de PyWebView ne s'initialisait pas. La 1.0.2 ne contient plus PyWebView.

## Fonctions

- clé API AllDebrid ;
- test de connexion ;
- liens sauvegardés AllDebrid ;
- magnets ;
- affichage des fichiers d'un magnet ;
- téléchargement local ;
- reprise d'un fichier `.part` si le serveur accepte HTTP Range ;
- trois téléchargements simultanés ;
- suivi taille / vitesse / progression ;
- annulation ;
- choix du dossier avec l'explorateur Windows natif.

## Destination

Bebrid Magic ne gère plus aucun montage ZimaOS, SMB ou NFS.

Le dossier choisi peut néanmoins être n'importe quel dossier que Windows sait
déjà atteindre, par exemple :

- `C:\Users\...\Downloads`
- `D:\Téléchargements`
- un disque USB
- un lecteur réseau déjà monté par Windows

## Configuration

La configuration est conservée ici :

`%LOCALAPPDATA%\BebridMagic\config.json`

La V1.0.3 réutilise donc la même configuration que les versions précédentes.

Journal de diagnostic :

`%LOCALAPPDATA%\BebridMagic\bebrid-magic.log`

## Création de l'EXE

Double-cliquer sur :

`BUILD_EXE.cmd`

Le résultat sera :

`dist\BebridMagic.exe`

La fenêtre de build ne se ferme pas en cas d'erreur.


## Corrections V1.0.2

- correction du plantage **Voir les fichiers** : encodage correct du paramètre `id[]` ;
- lecture correcte de l'arborescence `files` (`n`, `s`, `l`, `e`) des magnets ;
- les **liens sauvegardés** passent maintenant par `/v4/link/unlock` avant téléchargement ;
- les liens `l` issus des magnets passent eux aussi par `/v4/link/unlock` ;
- prise en charge des liens AllDebrid différés avec interrogation toutes les 5 secondes ;
- protection contre l'enregistrement accidentel d'une page HTML AllDebrid à la place du fichier.


## Corrections V1.0.3

- multisélection dans **Voir les fichiers** d’un magnet ;
- `Ctrl + clic` pour sélectionner plusieurs fichiers séparés ;
- `Shift + clic` pour sélectionner une plage ;
- `Ctrl + A` et bouton **Tout sélectionner** ;
- **Télécharger la sélection** ajoute tous les fichiers sélectionnés à la file de traitement ;
- aucun changement du fonctionnement AllDebrid validé en V1.0.2.
