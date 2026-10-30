@echo off
rem PC da esposa: entra no servidor do Arthur e abre o inZOI.
rem Troque o IP abaixo pelo 192.168.1.31 se preferir a rede local em vez do Tailscale.
cd /d "%~dp0"
python client\launcher.py --play --server 100.99.177.28 --port 24242 %*
