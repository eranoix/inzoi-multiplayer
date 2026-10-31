<#
Instala o inZOI Co-op num PC e deixa um unico jeito de abrir: o programa "inZOI Co-op" na area de trabalho.
- baixa a versao mais nova do GitHub (assinada por "inZOI Co-op (eranoix)") como "inZOI Co-op.exe" na area de trabalho
- fecha e apaga as outras copias e atalhos (Downloads, area de trabalho, raiz de Documentos, %LOCALAPPDATA%\Programs\inZOI Co-op,
  a instalacao antiga pelo codigo-fonte)
- nao mexe nos saves nem nas configuracoes (%APPDATA%\inzoi-mp)
O programa se atualiza sozinho no mesmo lugar (a copia de seguranca da versao anterior fica escondida).

    powershell -ExecutionPolicy Bypass -File .\instalar-coop.ps1
#>
param(
    [string]$Repo = "eranoix/inzoi-multiplayer",
    [string]$Pasta = [Environment]::GetFolderPath("Desktop")
)
$ErrorActionPreference = "Stop"
$Thumb = "71F550645942BC38BD8353A6FB3FAEB6DCDA1A14"
$Desktop = $Pasta
$Exe = Join-Path $Pasta "inZOI Co-op.exe"
$Instalado = Join-Path $env:LOCALAPPDATA "Programs\inZOI Co-op"     # onde o 0.9.1 instalava, com um atalho

function Ver($f) { try { [version]((Get-Item $f).VersionInfo.ProductVersion -replace '[^0-9.]', '') } catch { [version]"0.0" } }
function Assinado($f) {
    $s = Get-AuthenticodeSignature $f
    return $s.SignerCertificate -and $s.SignerCertificate.Thumbprint -eq $Thumb
}

# Todas as copias que existem neste PC.
$lugares = @($Desktop, "$env:USERPROFILE\Desktop", "$env:USERPROFILE\Downloads",
             "$env:USERPROFILE\OneDrive\Desktop", "$env:USERPROFILE\OneDrive\Area de Trabalho", "$env:USERPROFILE\OneDrive\$([char]0xC1)rea de Trabalho") |
           Select-Object -Unique | Where-Object { $_ -and (Test-Path $_) }
$nomes = @("inZOI-Multiplayer*.exe", "inZOI-Coop*.exe", "inZOI-Multiplayer*.zip", "inZOI Co-op*.exe", "inZOI*Co-op*.lnk", "inZOI-Coop*.lnk", "inZOI-Multiplayer*.lnk")
# Area de trabalho e Downloads com uma subpasta; Documentos so na raiz (la dentro ficam projetos de outras coisas).
$achados = @(Get-ChildItem $lugares -Recurse -Depth 1 -Include $nomes -File -ErrorAction SilentlyContinue) +
           @(Get-ChildItem ([Environment]::GetFolderPath("MyDocuments")) -Include $nomes -File -ErrorAction SilentlyContinue |
             Where-Object { $_.DirectoryName -eq [Environment]::GetFolderPath("MyDocuments") })
$achados += @(Get-ChildItem $Instalado -Filter *.exe -File -ErrorAction SilentlyContinue)
$achados = @($achados | Where-Object { $_ -and $_.FullName -ne $Exe } | Sort-Object FullName -Unique)
$antiga = Join-Path $env:USERPROFILE "inZOI-Coop"                  # instalacao antiga pelo codigo-fonte (entrar.bat)

# 1. Fechar o programa que estiver aberto (o jogo continua aberto).
Get-Process | Where-Object { $_.Name -like "inZOI-Multiplayer*" -or $_.Name -like "inZOI-Coop*" -or $_.Name -like "inZOI Co-op*" } |
    ForEach-Object { Write-Host "Fechando $($_.Name)..."; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*companion*" -or $_.CommandLine -like "*inZOI-Coop*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# 2. A versao mais nova: do GitHub; sem internet, a mais nova que ja estiver no PC.
$baixado = Join-Path $env:TEMP "inZOI-Multiplayer-novo.exe"
$novo = $baixado
try {
    Write-Host "Baixando a versao mais nova..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest "https://github.com/$Repo/releases/latest/download/inZOI-Multiplayer.exe" -OutFile $baixado -UseBasicParsing
    if (-not (Assinado $baixado)) { throw "o arquivo baixado nao tem a assinatura do inZOI Co-op" }
} catch {
    Write-Host "Nao deu para baixar ($($_.Exception.Message)). Usando a copia mais nova deste PC." -ForegroundColor Yellow
    $novo = $achados + @(Get-Item $Exe -ErrorAction SilentlyContinue) | Where-Object { $_.Extension -eq ".exe" -and (Assinado $_.FullName) } |
            Sort-Object { Ver $_.FullName } -Descending | Select-Object -First 1 -ExpandProperty FullName
    if (-not $novo) { throw "Nenhuma copia do inZOI Co-op encontrada. Verifique a internet e rode de novo." }
}
if ($novo -ne $Exe -and (Ver $novo) -ge (Ver $Exe)) {
    if (Test-Path $Exe) { [IO.File]::SetAttributes($Exe, "Normal") }
    Copy-Item $novo $Exe -Force
}
Unblock-File $Exe -ErrorAction SilentlyContinue
Remove-Item $baixado -Force -ErrorAction SilentlyContinue

# 3. Apagar as outras copias e atalhos.
foreach ($f in $achados) {
    try { Remove-Item $f.FullName -Force; Write-Host "Apagado: $($f.FullName)" }
    catch { Write-Host "Nao deu para apagar $($f.FullName): $($_.Exception.Message)" -ForegroundColor Yellow }
}
if (Test-Path (Join-Path $antiga "entrar.bat")) {
    Remove-Item $antiga -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Apagada a instalacao antiga: $antiga"
}

Remove-Item $Instalado -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Pronto: inZOI Co-op $(Ver $Exe) instalado." -ForegroundColor Green
Write-Host "Abra sempre pelo 'inZOI Co-op' na area de trabalho. Ele se atualiza sozinho."
