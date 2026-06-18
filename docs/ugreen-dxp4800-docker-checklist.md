# UGREEN DXP4800 Docker Reparatur- und Optimierungs-Checkliste

## Vor der Reparatur

- Wichtige Container-Daten und Volumes sichern.
- Prüfen, ob UGREENs Container-/Docker-Oberfläche gerade Jobs ausführt.
- Genügend freien Speicherplatz auf System- und Docker-Partition sicherstellen, ohne die 1.8-TB-Datenfestplatte zu verändern.
- Wenn möglich, ein Wartungsfenster einplanen.
- Aktuellen Pfad der Compose-Projekte prüfen, z. B. `/volume1/docker` oder ein eigener Freigabeordner.
- SSD-Pfad festlegen, der für neue Installationen, Cache und Quarantäne genutzt werden darf.
- 1.8-TB-Datenfestplatte nicht formatieren, nicht reparieren, nicht mounten/unmounten und nicht für automatische Bereinigung freigeben.

## Reparaturablauf

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --dry-run
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply
```

Wenn Docker fehlt und auf dem System über den Paketmanager installiert werden soll:

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply --install-docker
```

Bei knappem Speicherplatz **nicht automatisch löschen**. Das Toolkit verweigert `--prune` und schreibt nur einen Sicherheitshinweis ins Log. Duplikate können zuerst read-only geprüft und danach optional auf die SSD in Quarantäne verschoben werden.

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --dry-run --duplicate-scan-root /pfad/zum/checken
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply --ssd-root /pfad/zur/ssd --duplicate-scan-root /pfad/zum/checken --quarantine-duplicates
```

Für zusätzliche Verbindungsprüfungen, z. B. Router und DNS:

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --dry-run --probe-host 192.168.1.1 --probe-host 1.1.1.1
```

Bei Compose-Dateien an einem anderen Ort:

```bash
sudo ./scripts/ugreen_docker_repair_optimize.sh --apply --compose-root /pfad/zu/docker
```

## Nach der Reparatur prüfen

```bash
docker ps -a
docker system df
docker compose ls
```

Achte besonders auf Container mit Status `Exited`, `Restarting` oder `unhealthy`.

## Schutz der 1.8-TB-Datenfestplatte

Das Toolkit behandelt erkannte 1.7-1.9-TB-Datenträger als geschützt. Es führt keine Partitionierung, Formatierung, Dateisystemreparatur, Mount-/Unmount-Aktion oder Wipe-Aktion aus. Docker-Prune wird grundsätzlich verweigert, damit keine Docker-Daten auf der 1.8-TB-Festplatte gelöscht werden.

## Verbindungen, die geprüft werden

- IP-Adressen und Netzwerkschnittstellen
- Standardrouten und Gateway-Erreichbarkeit
- DNS-Konfiguration
- lauschende TCP-/UDP-Dienste auf dem NAS
- Docker-Netzwerke und veröffentlichte Container-Ports
- Docker-Volumes, Mounts, Images, Restart-Policies und Healthchecks
- Duplikat-Kandidaten, wenn `--duplicate-scan-root` gesetzt wurde
- optionale Probe-Hosts über `--probe-host`

## Optimierungen, die das Toolkit setzt

- Docker-Logrotation mit `json-file`, `max-size=10m` und `max-file=3`, sofern keine bestehende Daemon-Konfiguration geschützt wird.
- `live-restore`, damit laufende Container bei Docker-Daemon-Wartung robuster bleiben.
- Höhere Inotify-Limits für Medienserver, Sync-Dienste und viele kleine Dateien.
- Höhere TCP-Backlog- und Portbereichswerte für mehrere parallele Containerdienste.

## Typische Ursachen für Docker-Probleme

- Vollgelaufene Docker-Partition oder Systempartition
- Defekte oder unvollständige Compose-Dateien
- Container mit fehlerhaften Restart-Loops
- Netzwerkprobleme nach Firmware- oder Paket-Updates
- Zu niedrige Inotify-Limits bei vielen Dateien, z. B. Medienservern oder Sync-Diensten
- Zu große Container-Logs ohne Logrotation

## Vorsicht bei Datenlöschung

Das Toolkit löscht keine Docker-Volumes, Images, Container, Netzwerke oder Build-Caches. `--prune` wird nur protokolliert und verweigert. Duplikate werden ebenfalls nicht direkt gelöscht; mit `--quarantine-duplicates` werden sie nur auf die freigegebene SSD verschoben, damit sie vor endgültiger Löschung geprüft werden können.
