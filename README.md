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
| Réseau | Pour `eth*` / `bond*` : octets reçus/envoyés, **débit en réception/émission (Mbit/s)**, état du lien, vitesse ; adresses IPv4/IPv6 et MAC du NAS |
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

### Capteurs binaires

Pratiques pour les automatisations et les notifications :

| Appareil | Capteur | Actif quand… |
|---|---|---|
| Disque | **Problème** | santé S.M.A.R.T. `Risky`/`Dangerous`/`Bad`, ou état `Abnormal` |
| Volume | **Problème** | état `Degraded`, `Failed` ou `Inactive` (les resynchronisations en cours ne comptent pas) |
| Interface | **Connecté** | le lien réseau est actif |
| UPS | **Sur batterie**, **Batterie faible** | l'onduleur signale `OB` / `LB` |

Chaque appareil enfant est rattaché au NAS (« via le NAS »).

### Mise à jour ADM

L'entité `update` indique si une nouvelle version d'ADM est disponible (information fournie par le NAS en SNMP). L'installation reste à faire depuis ADM : l'intégration est en lecture seule.

Certains capteurs de diagnostic (adresse MAC, fuseau horaire, taille des disques, etc.) sont désactivés par défaut : activez-les depuis la fiche de l'entité.

Les volumes, disques et interfaces sont détectés automatiquement.

## Prérequis

Dans ADM : **Services → SNMP**, activer le service et définir une communauté (par défaut `public`).

> Pour limiter l'exposition, préférez une communauté personnalisée et restreignez l'accès à l'IP de Home Assistant.

### SNMP v2c ou v3

- **v2c** : une communauté (mot de passe en clair sur le réseau). Simple, adapté à un réseau local de confiance.
- **v3** : un utilisateur avec **authentification** (MD5, SHA, SHA-224/256/384/512) et **chiffrement** (DES, 3DES, AES-128/192/256). Créez l'utilisateur dans ADM (*Services → SNMP*), puis choisissez « v3 » à l'ajout de l'intégration. Les phrases secrètes font au moins 8 caractères.

Si les identifiants v3 sont refusés plus tard, Home Assistant propose de les ressaisir (ré-authentification). Avec v2c, une mauvaise communauté ressemble à un NAS éteint : aucune ré-authentification n'est possible.

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

### Nettoyage automatique

Si un disque, un volume, une interface ou un onduleur disparaît du NAS (3 relevés consécutifs), son appareil est supprimé. Un tableau de disques ou de volumes **vide** (lecture ratée) ne supprime jamais rien. Vous pouvez aussi supprimer à la main un appareil qui n'existe plus, depuis sa page.

## Options et reconfiguration

- **Options** (⚙ sur l'intégration) : intervalle de rafraîchissement (10 s à 1 h, 60 s par défaut) et interfaces réseau à suivre. L'enregistrement recharge l'intégration.
- **Reconfigurer** (⋮ → *Reconfigurer*) : changer l'IP, le port, la version SNMP (v2c ↔ v3) ou les identifiants sans supprimer l'intégration.

## Diagnostics

Sur la page de l'appareil : ⋮ → **Télécharger les diagnostics**. Le fichier contient les données lues et un dump du MIB ASUSTOR ; l'IP, la communauté, le numéro de série, les adresses MAC/IP, le fuseau horaire et l'identifiant ASUSTOR sont masqués. Joignez-le à une issue pour faire prendre en charge un autre modèle.

## Langues

Interface et noms d'entités traduits en : English, Français, Deutsch, Español, Italiano, Português (PT et BR), Nederlands, Polski, Русский, Українська, Türkçe, Čeština, Svenska, Dansk, Norsk bokmål, Suomi, Magyar, Română, 日本語, 한국어, 简体中文, 繁體中文, Tiếng Việt.

Une traduction à corriger ? Les fichiers sont dans `custom_components/asustor/translations/` : une pull request suffit.

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

Version 0.1.0-beta.4 (pré-release) — distribué sous licence [MIT](LICENSE) © 2026 Kévin Aubier.

> ASUSTOR et son logo sont des marques de leur propriétaire (ASUSTOR Inc.). Ce projet est indépendant et n'est ni affilié ni approuvé par ASUSTOR ; le logo n'est utilisé que pour identifier les NAS compatibles.
