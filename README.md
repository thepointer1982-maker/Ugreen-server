# UGREEN NAS / DXP4800 Docker Reparatur- und Optimierungs-Toolkit

Dieses Repository enthält ein vorsichtiges Reparatur- und Optimierungs-Toolkit für Docker auf einem UGREEN NAS/Server der DXP-Serie, z. B. DXP4800.

## Was wurde verbessert?

Das Skript `scripts/ugreen_docker_repair_optimize.sh` hilft jetzt nicht nur beim Neustart von Docker, sondern führt einen vollständigen Reparaturablauf aus:

- Host-Baseline sichern: Kernel, Speicher, Mounts, Blockgeräte und Plattenbelegung
- 1.8-TB-Datenfestplatten automatisch erkennen und als geschützt markieren
- NAS-Verbindungen prüfen: IP-Adressen, Routen, DNS, Listener, Gateway-Ping und optionale Probe-Hosts
- Docker-Status, Containerliste, Volumes, Mounts, Images, Restart-Policies, Healthchecks, Speicherverbrauch und Journal-Auszug sichern
- Docker bei Bedarf über den vorhandenen Paketmanager installieren, wenn `--install-docker` gesetzt ist
- sichere Docker-Daemon-Konfiguration für Log-Rotation und `live-restore` vorbereiten
- Docker-Dienst über `systemd` oder `service` zurücksetzen, aktivieren und neu starten
- ungesunde Container vor dem Neustart inspizieren und anschließend optional neu starten
- Docker-Compose-Dateien mit `docker compose` oder klassischem `docker-compose` validieren
- Docker-freundliche Linux-Netzwerk- und Inotify-Parameter setzen
- Docker-Daten auf Wichtigkeit prüfen: Volumes, Mounts, Container-Images und App-Muster werden inventarisiert
- doppelte Dateien nur melden; optional werden Duplikate auf die SSD in Quarantäne verschoben, nie direkt gelöscht
- keine Docker-Daten automatisch löschen; `--prune` wird aus Sicherheitsgründen verweigert
- Vorher-/Nachher-Berichte im Log-Verzeichnis schreiben

## Sicherheitsprinzip

Standardmäßig läuft das Skript im **Dry-Run-Modus** und verändert nichts. Änderungen werden erst mit `--apply` durchgeführt.

Die 1.8-TB-Datenfestplatte wird hart geschützt: Das Skript partitioniert, formatiert, mountet, unmountet, repariert oder wischt keine Datenträger. Docker-Prune wird grundsätzlich verweigert, damit keine Docker-Daten auf der 1.8-TB-Disk gelöscht werden.

Das Skript überschreibt bestehende `/etc/docker/daemon.json` nicht automatisch. Wenn eine bestehende Datei ersetzt werden soll, muss zusätzlich `--force-daemon-config` gesetzt werden; vorher wird eine Sicherung im Log-Verzeichnis abgelegt.

## Schnellstart

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --dry-run
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply
```

Wenn Docker auf dem NAS fehlt und über den Paketmanager installiert werden soll:

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply --install-docker
```

Docker-Prune wird bewusst nicht automatisiert. Wenn `--prune` übergeben wird, protokolliert das Skript eine Verweigerung und löscht nichts.

## Häufige Optionen

| Option | Wirkung |
| --- | --- |
| `--dry-run` | zeigt geplante Aktionen, ohne das System zu verändern |
| `--apply` | führt Reparatur- und Optimierungsaktionen aus |
| `--install-docker` | installiert Docker über `apt-get`, `apk`, `dnf` oder `yum`, falls Docker fehlt |
| `--prune` | Sicherheitskompatibilität: wird protokolliert, aber verweigert; es wird nichts gelöscht |
| `--compose-root PATH` | sucht Compose-Dateien unter einem anderen Pfad als `/volume1/docker` |
| `--no-restart-unhealthy` | startet ungesunde Container nicht neu |
| `--no-daemon-config` | überspringt die Docker-Daemon-Optimierung |
| `--force-daemon-config` | ersetzt eine bestehende Docker-Daemon-Konfiguration nach Backup |
| `--probe-host HOST` | prüft zusätzliche Ziele per Ping, z. B. Router, DNS oder Gateway |
| `--ssd-root PATH` | SSD-Pfad für neue Installationen, Cache und Duplikat-Quarantäne |
| `--duplicate-scan-root PATH` | scannt einen Pfad read-only nach Duplikaten |
| `--quarantine-duplicates` | verschiebt Duplikate mit `--apply` in eine SSD-Quarantäne, löscht sie aber nicht dauerhaft |

## Empfohlener Ablauf auf dem UGREEN DXP4800

1. Script auf den Server kopieren.
2. Zuerst Dry-Run ausführen.
3. Logausgabe prüfen.
4. Wenn alles plausibel ist, mit `--apply` ausführen.
5. Nur wenn Docker fehlt, `--install-docker` ergänzen.
6. Für Verbindungschecks optional Router, DNS oder andere Ziele mit `--probe-host` ergänzen.
7. Optional mit `--duplicate-scan-root` zuerst doppelte Daten melden lassen.
8. Wenn die SSD genutzt werden darf, `--ssd-root` setzen; Duplikate werden bei Bedarf nur in Quarantäne verschoben, nicht gelöscht.
9. `--prune` nicht verwenden; das Toolkit verweigert Prune grundsätzlich und löscht keine Docker-Daten.

## Logs und Backups

Alle Läufe schreiben nach `/var/log/ugreen-docker-toolkit`. Dort landen:

- Laufprotokoll
- Host-Baseline mit Kernel, Speicher, Mounts, Blockgeräten und Plattenbelegung
- Netzwerk-/Verbindungsberichte mit IP-Adressen, Routen, DNS, Listenern und Gateway-Erreichbarkeit
- Docker-Status, Volumes, Mounts, Images, Restart-Policies und Healthchecks
- Docker-Journal-Auszug, wenn `journalctl` verfügbar ist
- `docker ps -a`
- `docker system df`
- Compose-Validierungsergebnisse
- Backups vorhandener Docker- oder Sysctl-Konfigurationen, wenn sie ersetzt werden
- Markdown-Zusammenfassung des Docker-Management-Audits
- Duplikatbericht, wenn `--duplicate-scan-root` gesetzt wurde

## Unterstützte Umgebung

Das Toolkit ist für Linux-basierte UGREEN NAS-/Server-Systeme gedacht, auf denen Docker über `systemd`, `service` oder direkt über die Docker-CLI verwaltet wird.
