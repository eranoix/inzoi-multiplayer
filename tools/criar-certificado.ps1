# Cria (uma única vez) o certificado que assina o inZOI-Multiplayer.exe do co-op.
# A chave privada fica só no repositório de certificados do Windows deste PC (Cert:\CurrentUser\My).
# O certificado público vai para release\coop-cert.cer (pode ir para o GitHub) e o thumbprint para client\updater.py.
#     powershell -ExecutionPolicy Bypass -File tools\criar-certificado.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$cer = Join-Path $root "release\coop-cert.cer"
$subject = "CN=inZOI Co-op (eranoix)"

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object Subject -eq $subject | Select-Object -First 1
if ($cert) {
    Write-Host "Já existe: $($cert.Thumbprint) (válido até $($cert.NotAfter))"
} else {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject -CertStoreLocation Cert:\CurrentUser\My `
        -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(15)
    Write-Host "Criado: $($cert.Thumbprint)"
}
Export-Certificate -Cert $cert -FilePath $cer -Type CERT | Out-Null
Write-Host "Certificado público: $cer"

$updater = Join-Path $root "client\updater.py"
$text = [IO.File]::ReadAllText($updater)
$text = [regex]::Replace($text, '(?m)^SIGNER = "[0-9A-Fa-f]*"', "SIGNER = `"$($cert.Thumbprint)`"")
[IO.File]::WriteAllText($updater, $text, (New-Object Text.UTF8Encoding $false))
Write-Host "client\updater.py: SIGNER = $($cert.Thumbprint)"

Write-Host ""
Write-Host "FAÇA UM BACKUP da chave (sem ela, as próximas versões não serão aceitas pelo PC dela):" -ForegroundColor Yellow
Write-Host "    `$p = Read-Host -AsSecureString 'senha do backup'"
Write-Host "    Export-PfxCertificate -Cert Cert:\CurrentUser\My\$($cert.Thumbprint) -FilePath <pendrive>\coop-cert.pfx -Password `$p"
