# PowerShell-, Ollama- und Codex-Tooling für das UGREEN-Toolkit

Diese Ergänzung richtet eine Windows- oder Linux-Admin-Workstation so ein, dass du das UGREEN-Docker-Toolkit bequem per PowerShell vorbereiten, per SSH auf das NAS kopieren und mit Ollama/Codex dokumentieren oder prüfen kannst.

> Hinweis: `ohlama` ist in der Praxis meist `ollama`. Die Beispiele verwenden deshalb den offiziellen Befehlsnamen `ollama`.

## Zielbild

- PowerShell 7 als einheitliche Shell für Windows, Linux und macOS.
- OpenSSH für den Zugriff auf das UGREEN NAS.
- Git für Repository-Updates und Versionierung.
- Ollama für lokale LLM-Läufe, wenn keine Cloud-Anbindung gewünscht ist.
- Codex CLI oder ein anderer Codex-Client für codebezogene Assistenz im Repository.
- Docker CLI optional lokal, wenn Compose-Dateien vorab geprüft werden sollen.

## Schnellprüfung in PowerShell

Führe diese Befehle auf deiner Admin-Workstation aus:

```powershell
$tools = 'pwsh','git','ssh','scp','ollama','docker','codex'
foreach ($tool in $tools) {
  $cmd = Get-Command $tool -ErrorAction SilentlyContinue
  if ($cmd) {
    "OK  {0,-8} {1}" -f $tool, $cmd.Source
  } else {
    "FEHLT {0}" -f $tool
  }
}
```

`codex` ist optional, solange du über eine andere Codex-Integration arbeitest. `docker` ist lokal ebenfalls optional, weil das eigentliche Reparaturskript auf dem NAS läuft.

## Installation mit winget unter Windows

```powershell
winget install --id Microsoft.PowerShell --source winget
winget install --id Git.Git --source winget
winget install --id OpenJS.NodeJS.LTS --source winget
winget install --id Docker.DockerDesktop --source winget
winget install --id Ollama.Ollama --source winget
```

Starte danach ein neues PowerShell-7-Fenster:

```powershell
pwsh
```

Prüfe anschließend:

```powershell
$PSVersionTable.PSVersion
git --version
ssh -V
ollama --version
```

## Ollama vorbereiten

Lokales Modell herunterladen:

```powershell
ollama pull llama3.1
ollama list
```

Kurzer Test:

```powershell
ollama run llama3.1 "Fasse in einem Satz zusammen, was Docker-Volumes sind."
```

Wenn Ollama im Hintergrund nicht läuft, starte den Dienst oder die Desktop-App und wiederhole den Test.

## Codex-Workflow im Repository

Repository öffnen:

```powershell
cd C:\Pfad\zu\Ugreen-server
git status
```

Empfohlene Arbeitsweise:

1. Änderungen in einer eigenen Branch durchführen.
2. Vor jeder Ausführung auf dem NAS `git status` prüfen.
3. Skript zuerst im Dry-Run verwenden.
4. Logs aus `/var/log/ugreen-docker-toolkit` sichern.
5. Änderungen committen und Pull Request erstellen.

Falls deine Codex-Installation eine CLI mit dem Befehl `codex` bereitstellt, prüfe sie so:

```powershell
codex --version
codex --help
```

## Toolkit auf das UGREEN NAS kopieren

Variablen setzen:

```powershell
$NasHost = '192.168.1.10'
$NasUser = 'admin'
$RemoteDir = '/tmp/ugreen-toolkit'
```

Verzeichnis auf dem NAS anlegen:

```powershell
ssh "$NasUser@$NasHost" "mkdir -p $RemoteDir/scripts"
```

Skript kopieren:

```powershell
scp .\scripts\ugreen_docker_repair_optimize.sh "$NasUser@$NasHost`:$RemoteDir/scripts/"
```

Dry-Run ausführen:

```powershell
ssh "$NasUser@$NasHost" "chmod +x $RemoteDir/scripts/ugreen_docker_repair_optimize.sh && sudo $RemoteDir/scripts/ugreen_docker_repair_optimize.sh --dry-run"
```

Erst nach Prüfung der Ausgabe anwenden:

```powershell
ssh "$NasUser@$NasHost" "sudo $RemoteDir/scripts/ugreen_docker_repair_optimize.sh --apply"
```

## Nützliche PowerShell-Helfer

### Logdateien vom NAS abholen

```powershell
$LocalLogDir = Join-Path $PWD 'ugreen-logs'
New-Item -ItemType Directory -Force -Path $LocalLogDir | Out-Null
scp "$NasUser@$NasHost`:/var/log/ugreen-docker-toolkit/*" $LocalLogDir
```

### Aktuelle NAS-Docker-Sicht prüfen

```powershell
ssh "$NasUser@$NasHost" "docker ps -a; docker system df"
```

### Zusätzliche Probe-Hosts testen

```powershell
ssh "$NasUser@$NasHost" "sudo $RemoteDir/scripts/ugreen_docker_repair_optimize.sh --dry-run --probe-host 192.168.1.1 --probe-host 1.1.1.1"
```

## Sicherheitsregeln

- Das Toolkit zuerst immer mit `--dry-run` starten.
- Keine Datenfestplatten formatieren, reparieren, mounten oder unmounten.
- `--prune` nicht als Bereinigung verwenden; das Skript verweigert Docker-Prune bewusst.
- Duplikate nur mit `--duplicate-scan-root` melden lassen und nur mit bewusst gesetztem `--ssd-root` in Quarantäne verschieben.
- SSH-Zugangsdaten und API-Schlüssel nie in Git committen.
