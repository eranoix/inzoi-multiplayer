# Instala o co-op do inZOI neste PC (o PC de quem vai ENTRAR no servidor do Arthur).
# Como rodar: abrir o PowerShell na pasta onde este arquivo está e digitar
#     powershell -ExecutionPolicy Bypass -File .\instalar-coop.ps1
param(
    [string]$Servidor = "100.99.177.28",                     # IP do Tailscale do PC do Arthur
    [int]$Porta = 24242,
    [string]$Pasta = "$env:USERPROFILE\inZOI-Coop"
)
$ErrorActionPreference = "Stop"
$Commit = "bafbdf4"                                          # versão do mod revisada (Darker117/inzoi-multiplayer)
$ZipUrl = "https://codeload.github.com/Darker117/inzoi-multiplayer/zip/$Commit"

function Passo($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t)    { Write-Host "    OK: $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "    ATENÇÃO: $t" -ForegroundColor Yellow }

# Um python.exe de verdade com versão >= 3.10 (ignora o atalho falso da Microsoft Store)
function Achar-Python {
    $candidatos = @()
    foreach ($cmd in @("py", "python")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $args_ = if ($cmd -eq "py") { @("-3", "-c") } else { @("-c") }
            try {
                $exe = & $cmd @args_ "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe) { $candidatos += $exe.Trim() }
            } catch {}
        }
    }
    $candidatos += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "C:\Program Files\Python3*\python.exe", "C:\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($exe in $candidatos | Select-Object -Unique) {
        if (-not (Test-Path $exe)) { continue }
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -and [version]$v -ge [version]"3.10") { return $exe }
    }
    return $null
}

Write-Host "Co-op inZOI - instalação" -ForegroundColor Magenta

# 1. Python
Passo "Python"
$py = Achar-Python
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "O winget não existe neste Windows. Instale o Python manualmente em https://www.python.org/downloads/ (marque 'Add python.exe to PATH') e rode este script de novo."
    }
    Write-Host "    Instalando o Python 3.13 (pode levar alguns minutos)..."
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    $py = Achar-Python
    if (-not $py) { throw "O Python foi instalado, mas não foi encontrado. Feche o terminal, abra de novo e rode o script outra vez." }
}
Ok $py

# 2. O mod
Passo "Baixando o mod (versão $Commit)"
$tmp = Join-Path $env:TEMP "inzoi-coop-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "mod.zip"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest $ZipUrl -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath $tmp
$extraido = Get-ChildItem $tmp -Directory | Select-Object -First 1
if (Test-Path $Pasta) { Remove-Item $Pasta -Recurse -Force }
Move-Item $extraido.FullName $Pasta
Remove-Item $tmp -Recurse -Force
Ok $Pasta

# 3. entrar.bat + atalho na área de trabalho
Passo "Criando o atalho"
$bat = Join-Path $Pasta "entrar.bat"
@"
@echo off
rem Entra no servidor do Arthur e abre o inZOI.
cd /d "%~dp0"
"$py" client\launcher.py --play --server $Servidor --port $Porta %*
if errorlevel 1 pause
"@ | Set-Content -Path $bat -Encoding ASCII
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "inZOI Co-op.lnk"))
$lnk.TargetPath = $bat
$lnk.WorkingDirectory = $Pasta
$jogo = $null
$libs = @("${env:ProgramFiles(x86)}\Steam\steamapps")
$vdf = "${env:ProgramFiles(x86)}\Steam\steamapps\libraryfolders.vdf"
if (Test-Path $vdf) {
    (Select-String -Path $vdf -Pattern '"path"\s+"(.+)"').Matches | ForEach-Object { $libs += ($_.Groups[1].Value -replace '\\\\', '\') + "\steamapps" }
}
foreach ($l in $libs | Select-Object -Unique) {
    $p = Join-Path $l "common\inZOI"
    if (Test-Path $p) { $jogo = $p; break }
}
$icone = if ($jogo) { Get-ChildItem $jogo -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 } else { $null }
if ($icone) { $lnk.IconLocation = $icone.FullName }
$lnk.Save()
Ok "'inZOI Co-op' na área de trabalho"

# 4. Conferências
Passo "Conferindo"
if ($jogo) { Ok "inZOI encontrado em $jogo" } else { Aviso "não achei o inZOI na Steam. Ele precisa estar instalado (e na mesma versão do Arthur)." }

$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (Test-Path $ts) {
    $estado = & $ts status --json --peers=false 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($estado -and $estado.BackendState -eq "Running") { Ok "Tailscale conectado" }
    else { Aviso "o Tailscale está instalado, mas não está conectado. Abra o Tailscale e conecte antes de jogar." }
} else {
    Aviso "o Tailscale não está instalado. Instale em https://tailscale.com/download e entre na rede do Arthur."
}
if (Get-Process -Name "NordVPN*" -ErrorAction SilentlyContinue) {
    Aviso "o NordVPN está aberto e pode bloquear a conexão. Se não conectar, desligue-o."
}

Write-Host "`nPronto! Para jogar: espere o Arthur abrir o jogo e dê dois cliques em 'inZOI Co-op' na área de trabalho." -ForegroundColor Magenta
Write-Host "Na primeira vez, aceite criar a cópia multiplayer do save (os saves normais não são mexidos)."
