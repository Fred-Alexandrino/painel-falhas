#!/bin/bash
set -e

echo "================================================================"
echo "  Instalação — Ponte do WhatsApp (wppconnect-server / Baileys)"
echo "  Servidor 2 de 2 — VM.Standard.E2.1.Micro (1GB RAM)"
echo "================================================================"
echo ""

# ── 0. Criar memória de troca (swap) — essencial com só 1GB de RAM ─────────
echo ">> Configurando memória de troca (swap)..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
free -h

# ── 1. Atualizar sistema e instalar dependências base ─────────────────────
echo ">> Atualizando pacotes do sistema..."
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y git curl build-essential python3

# ── 2. Instalar Node.js 18+ (via NodeSource) ────────────────────────────────
echo ">> Instalando Node.js..."
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v
npm -v

# ── 3. Instalar Caddy (HTTPS automático) ────────────────────────────────────
echo ">> Instalando Caddy..."
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y
sudo apt-get install -y caddy

# ── 4. Liberar portas 80/443 no firewall interno (iptables) ────────────────
#      (lição da instalação do backend principal — sem isso o Caddy nunca
#      consegue emitir o certificado HTTPS, fica preso em "challenge failed")
echo ">> Liberando portas 80/443 no firewall da VM..."
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 4 -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
echo ">> Lembrete: confirme também as Ingress Rules de 80 e 443 na Security List"
echo "   da VCN no console da Oracle Cloud (regra da nuvem é separada da do SO)."

# ── 5. Descobrir o IP público desta máquina ─────────────────────────────────
IP_PUBLICO=$(curl -s ifconfig.me)
echo ">> IP público detectado: $IP_PUBLICO"
DOMINIO_WPP="wpp.${IP_PUBLICO}.sslip.io"

# ── 6. Clonar o repositório ──────────────────────────────────────────────────
echo ">> Baixando wppconnect-server..."
cd ~
git clone https://github.com/Fred-Alexandrino/wppconnect-server.git

# ── 7. Instalar dependências Node ────────────────────────────────────────────
echo ">> Instalando dependências npm..."
cd ~/wppconnect-server
npm install --omit=dev

echo ""
echo "================================================================"
echo "Endereço do BACKEND PRINCIPAL (painel-falhas) já confirmado"
echo "nesta migração: https://api.168.138.232.237.sslip.io"
echo "Aperte Enter pra usar esse valor, ou cole outro endereço:"
echo "================================================================"
read -r SERVIDOR_URL_INPUT
if [ -z "$SERVIDOR_URL_INPUT" ]; then
    SERVIDOR_URL="https://api.168.138.232.237.sslip.io"
else
    SERVIDOR_URL="$SERVIDOR_URL_INPUT"
fi

echo ""
echo "================================================================"
echo "Cole abaixo os IDs dos grupos do WhatsApp monitorados (GRUPOS_IDS)"
echo "separados por vírgula — copie do Render → wppconnect-server →"
echo "Environment, variável GRUPOS_IDS. Cole tudo numa linha só:"
echo "================================================================"
read -r GRUPOS_IDS

echo ""
echo "================================================================"
echo "Cole abaixo o GITHUB_TOKEN usado pro backup da sessão autenticada"
echo "(copie do Render → wppconnect-server → Environment, variável"
echo "GITHUB_TOKEN — pode ser o mesmo token que já usamos nesta migração):"
echo "================================================================"
read -r GITHUB_TOKEN_WPP

cat > ~/wppconnect-server/.env <<EOF
SERVIDOR_URL=${SERVIDOR_URL}
WEBHOOK_SECRET=falhas2026
PORT=3000
GRUPOS_IDS=${GRUPOS_IDS}
GITHUB_TOKEN=${GITHUB_TOKEN_WPP}
GITHUB_BACKUP_REPO=Fred-Alexandrino/wppconnect-auth-backup
GITHUB_BACKUP_PATH=auth_info_backup.json
GITHUB_BACKUP_BRANCH=main
EOF

# ── 8. Criar serviço systemd ──────────────────────────────────────────────────
echo ">> Criando serviço permanente..."
sudo tee /etc/systemd/system/wppconnect.service > /dev/null <<EOF
[Unit]
Description=Ponte WhatsApp - Baileys
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/wppconnect-server
EnvironmentFile=/home/ubuntu/wppconnect-server/.env
ExecStart=/usr/bin/node /home/ubuntu/wppconnect-server/server.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ── 9. Configurar Caddy (proxy reverso + HTTPS automático) ─────────────────
echo ">> Configurando Caddy..."
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
${DOMINIO_WPP} {
    reverse_proxy 127.0.0.1:3000
}
EOF

# ── 10. Iniciar tudo ───────────────────────────────────────────────────────
echo ">> Iniciando serviços..."
sudo systemctl daemon-reload
sudo systemctl enable wppconnect caddy
sudo systemctl restart wppconnect caddy

sleep 5

echo ""
echo "================================================================"
echo "  INSTALAÇÃO CONCLUÍDA — Ponte do WhatsApp"
echo "================================================================"
echo ""
echo "  Endereço:  https://${DOMINIO_WPP}"
echo ""
echo "  Anote esse endereço — vai precisar dele pra atualizar o .env"
echo "  do backend principal (variável WPP_SERVER_URL) e o frontend."
echo ""
echo "  Pra checar se está rodando:  sudo systemctl status wppconnect"
echo "  Pra ver os logs ao vivo:     sudo journalctl -u wppconnect -f"
echo "  (o QR code de conexão do WhatsApp deve aparecer nos logs, ou"
echo "  verifique se há um endpoint /qrcode no server.js pra escaneá-lo)"
echo "================================================================"
