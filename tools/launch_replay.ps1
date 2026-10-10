# Lance un replay .rofl avec un client LoL (doit tourner en admin).
# Vanguard (vgk) bloque le lancement d'un client hors Riot Client : on le coupe
# juste le temps du lancement puis on le relance, comme le faisait LolnamEditor.
# Le script reste ensuite en veille (caché) : le jeu tourne en admin, seul un processus admin peut le fermer.
# Quand lolclip crée close_replay.flag, on ferme le jeu ; à la fin on s'assure que Vanguard tourne.
param(
    [Parameter(Mandatory)] [string]$Rofl,
    [Parameter(Mandatory)] [string]$ClientDir,
    [string]$RunDir = $PSScriptRoot  # journal + fichier de fermeture (dossier modifiable de l'appli)
)
$log = Join-Path $RunDir 'launch_replay.log'
$flag = Join-Path $RunDir 'close_replay.flag'
"$(Get-Date -Format s) launch $Rofl with $ClientDir" | Out-File $log -Encoding utf8
Remove-Item $flag -ErrorAction SilentlyContinue
try {
    # une instance précédente (lancée en admin) ne peut être fermée que d'ici
    Get-Process 'League of Legends' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    sc.exe stop vgk *>> $log
    Start-Sleep 1
    Start-Process -FilePath "$ClientDir\Game\League of Legends.exe" -WorkingDirectory "$ClientDir\Game" `
        -ArgumentList "`"$Rofl`"", '-GameBaseDir=..', '-Locale=en_US', '-SkipBuild', '-EnableLNP', '-UseRads'
    "started" | Out-File $log -Append -Encoding utf8
    Start-Sleep 5
} catch {
    "error: $_" | Out-File $log -Append -Encoding utf8
} finally {
    sc.exe start vgk *>> $log
}
# veille : jusqu'à la fermeture du jeu (par l'utilisateur ou demandée par lolclip)
$deadline = (Get-Date).AddSeconds(120)
while (-not (Get-Process 'League of Legends' -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep 1 }
while (Get-Process 'League of Legends' -ErrorAction SilentlyContinue) {
    if (Test-Path $flag) {
        "close requested" | Out-File $log -Append -Encoding utf8
        Get-Process 'League of Legends' -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep 2
    }
    Start-Sleep 1
}
Remove-Item $flag -ErrorAction SilentlyContinue
$svc = Get-Service vgk -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne 'Running') { sc.exe start vgk *>> $log }
"done" | Out-File $log -Append -Encoding utf8
