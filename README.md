<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/asustor-logo-white.svg">
    <img src="assets/asustor-logo-dark.svg" alt="ASUSTOR" width="260">
  </picture>
</p>

# ASUSTOR NAS pour Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Version](https://img.shields.io/github/v/release/aubierr/ha-asustor)](https://github.com/aubierr/ha-asustor/releases)
[![Licence](https://img.shields.io/github/license/aubierr/ha-asustor)](LICENSE)

Intégration personnalisée pour surveiller un NAS **ASUSTOR** (ADM) depuis Home Assistant, en **SNMP v2c** (ADM n'a pas d'API publique documentée). Tout est local, sans cloud.

[![Ouvrir le dépôt dans HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=aubierr&repository=ha-asustor&category=integration)

## Fonctionnalités

| Catégorie | Capteurs |
|---|---|
| Système | Utilisation CPU (globale et par cœur), mémoire (utilisation, totale, disponible), swap, charge (1/5/15 min), durée de fonctionnement, modèle du CPU, état des mises à jour ADM, fuseau horaire, heure |
| Matériel | Température CPU et système, vitesse des ventilateurs |
| Volumes | Utilisation (%), espace libre/utilisé/total, état, niveau RAID, système de fichiers |
| Disques | Température, santé S.M.A.R.T., état, taille ; modèle et type en attributs |
| Réseau | Pour `eth*` / `bond*` : octets reçus/envoyés, état du lien, vitesse ; adresses IPv4/IPv6 et MAC du NAS |
| Disques (E/S) | Octets lus/écrits par disque, si le NAS expose UCD-DISKIO |
| Onduleur (UPS) | État, charge batterie, autonomie, seuil de batterie faible, tension d'entrée, sensibilité — uniquement si un onduleur est connecté |
| Appareil | Modèle, version ADM, version BIOS, numéro de série |

### Appareils

Les entités sont rangées par appareil dans Home Assistant :

- **NAS** : système, CPU, mémoire, températures, ventilateurs, réseau (adresses) et l'entité de **mise à jour du firmware ADM**
- **Un appareil par disque** (`Disk0`, `Disk1`…) : température, santé S.M.A.R.T., état, taille
- **Un appareil par volume** (`Volume 1`…) : utilisation, espace, état, RAID, système de fichiers
- **Un appareil par interface réseau** (`eth0`, `eth1`, `bond0`…) : débit cumulé, lien, vitesse
- **Un appareil UPS**, si un onduleur est connecté

Chaque appareil enfant est rattaché au NAS (« via le NAS »).

### Mise à jour ADM

L'entité `update` indique si une nouvelle version d'ADM est disponible (information fournie par le NAS en SNMP). L'installation reste à faire depuis ADM : l'intégration est en lecture seule.

Certains capteurs de diagnostic (adresse MAC, fuseau horaire, taille des disques, etc.) sont désactivés par défaut : activez-les depuis la fiche de l'entité.

Les volumes, disques et interfaces sont détectés automatiquement.

## Prérequis

Dans ADM : **Services → SNMP**, activer le service et définir une communauté (par défaut `public`).

> Pour limiter l'exposition, préférez une communauté personnalisée et restreignez l'accès à l'IP de Home Assistant.

## Installation

### Via HACS (recommandé)

1. Cliquez sur le bouton ci-dessus, ou : HACS → ⋮ → **Dépôts personnalisés** → ajoutez `https://github.com/aubierr/ha-asustor` en catégorie **Intégration**.
2. Téléchargez **ASUSTOR NAS** et redémarrez Home Assistant.

### Manuelle

Copiez `custom_components/asustor` dans le dossier `config/custom_components/` de Home Assistant, puis redémarrez.

## Configuration

[![Configurer l'intégration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=asustor)

Ou : **Paramètres → Appareils et services → Ajouter une intégration → ASUSTOR NAS**, puis renseignez :

- **Hôte** : l'IP du NAS (ex. `192.168.10.18`)
- **Port** : `161`
- **Communauté SNMP** : `public` (ou la vôtre)

Les données sont rafraîchies toutes les 60 secondes.

## Compatibilité

Testé sur un **AS5202T** (ADM 5.1.4), avec les fichiers MIB fournis par ce NAS. Les OID ASUSTOR (`1.3.6.1.4.1.44738`) peuvent varier selon les modèles : les capteurs absents sont simplement ignorés. Les capteurs UPS suivent le MIB officiel mais n'ont pas pu être testés avec un onduleur réel. Pour vérifier un modèle :

```bash
snmpwalk -v2c -c public <ip-du-nas> 1.3.6.1.4.1.44738
```

Le fichier MIB est téléchargeable dans ADM (Services → SNMP).

## Dépannage

- **Impossible de se connecter** : vérifiez que SNMP est activé, la communauté, et que le port UDP 161 est joignable depuis Home Assistant.
- **Capteur manquant** : votre modèle n'expose peut-être pas l'OID correspondant (voir Compatibilité).

## Contribuer

Les issues et pull requests sont les bienvenues, notamment des sorties `snmpwalk` d'autres modèles.

## Licence

Version 0.1.0-beta.3 (pré-release) — distribué sous licence [MIT](LICENSE) © 2026 Kévin Aubier.

> ASUSTOR et son logo sont des marques de leur propriétaire (ASUSTOR Inc.). Ce projet est indépendant et n'est ni affilié ni approuvé par ASUSTOR ; le logo n'est utilisé que pour identifier les NAS compatibles.
