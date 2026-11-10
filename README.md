# ASUSTOR NAS pour Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Version](https://img.shields.io/github/v/release/aubierr/ha-asustor)](https://github.com/aubierr/ha-asustor/releases)
[![Licence](https://img.shields.io/github/license/aubierr/ha-asustor)](LICENSE)

Intégration personnalisée pour surveiller un NAS **ASUSTOR** (ADM) depuis Home Assistant, en **SNMP v2c** (ADM n'a pas d'API publique documentée). Tout est local, sans cloud.

[![Ouvrir le dépôt dans HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=aubierr&repository=ha-asustor&category=integration)

## Fonctionnalités

| Catégorie | Capteurs |
|---|---|
| Système | Utilisation CPU, mémoire, charge (1/5/15 min), durée de fonctionnement |
| Matériel | Température CPU, température système, vitesse du ventilateur |
| Volumes | État, utilisation (%), espace libre, taille totale |
| Disques | Température et santé de chaque disque |
| Réseau | Octets reçus/envoyés par interface `eth*` / `bond*` |

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

Testé sur un **AS5202T** (ADM 5.1.4). Les OID ASUSTOR (`1.3.6.1.4.1.44738`) peuvent varier selon les modèles : les capteurs absents sont simplement ignorés. Pour vérifier un modèle :

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

Version 0.1.0 — distribué sous licence [MIT](LICENSE) © 2026 Kévin Aubier.
