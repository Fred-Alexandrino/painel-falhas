#!/bin/bash
set -e

echo "================================================================"
echo "  Instalação — Backend Principal (painel-falhas)"
echo "  Servidor 1 de 2 — VM.Standard.E2.1.Micro (1GB RAM)"
echo "================================================================"
echo ""

# ── 0. Criar memória de troca (swap) — essencial com só 1GB de RAM,
#      evita que instalações pesadas (pip, compilação) travem a máquina ──
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
sudo apt-get install -y python3 python3-pip python3-venv git curl build-essential

# ── 2. Instalar Caddy (HTTPS automático) ───────────────────────────────────
echo ">> Instalando Caddy..."
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y
sudo apt-get install -y caddy

# ── 3. Descobrir o IP público desta máquina ────────────────────────────────
IP_PUBLICO=$(curl -s ifconfig.me)
echo ">> IP público detectado: $IP_PUBLICO"
DOMINIO_API="api.${IP_PUBLICO}.sslip.io"

echo ""
echo "================================================================"
echo "Cole abaixo o endereço do servidor da PONTE DO WHATSAPP"
echo "(o segundo servidor — se ainda não tiver criado, deixe em branco"
echo "e aperte Enter, a gente ajusta isso depois com /marcar-config)"
echo "================================================================"
read -r DOMINIO_WPP
if [ -z "$DOMINIO_WPP" ]; then
    DOMINIO_WPP="PENDENTE-configurar-depois"
fi
DOMINIO_WPP="${DOMINIO_WPP#https://}"
DOMINIO_WPP="${DOMINIO_WPP#http://}"

# ── 4. Clonar o repositório ─────────────────────────────────────────────────
echo ">> Baixando painel-falhas..."
cd ~
git clone https://github.com/Fred-Alexandrino/painel-falhas.git

# ── 5. Configurar ambiente Python ───────────────────────────────────────────
echo ">> Configurando ambiente Python..."
cd ~/painel-falhas
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
deactivate

echo ""
echo "================================================================"
echo "Cole abaixo o valor de GOOGLE_CREDENTIALS_JSON"
echo "(copie do Render → whatsapp-painel-falhas → Environment)"
echo "Cole tudo numa linha só e aperte Enter:"
echo "================================================================"
read -r GOOGLE_CREDENTIALS_JSON

echo ""
echo "================================================================"
echo "Cole abaixo o valor de GEMINI_API_KEY e aperte Enter:"
echo "================================================================"
read -r GEMINI_API_KEY

echo ""
echo "================================================================"
echo "Cole abaixo o valor de GEMINI_API_KEY_TESTE (ou repita a mesma"
echo "chave de cima se não tiver uma separada) e aperte Enter:"
echo "================================================================"
read -r GEMINI_API_KEY_TESTE

cat > ~/painel-falhas/.env <<EOF
SHEET_ID=1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs
WEBHOOK_SECRET=falhas2026
WPP_SERVER_URL=https://${DOMINIO_WPP}
GOOGLE_CREDENTIALS_JSON='${GOOGLE_CREDENTIALS_JSON}'
GEMINI_API_KEY=${GEMINI_API_KEY}
GEMINI_API_KEY_TESTE=${GEMINI_API_KEY_TESTE}
EOF

# ── 6. Criar serviço systemd (roda pra sempre, reinicia sozinho) ──────────
echo ">> Criando serviço permanente..."
sudo tee /etc/systemd/system/painel-falhas.service > /dev/null <<EOF
[Unit]
Description=Painel Falhas - Backend Flask
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/painel-falhas
EnvironmentFile=/home/ubuntu/painel-falhas/.env
ExecStart=/home/ubuntu/painel-falhas/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 --timeout 120 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ── 7. Configurar Caddy (proxy reverso + HTTPS automático) ─────────────────
echo ">> Configurando Caddy..."
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
${DOMINIO_API} {
    reverse_proxy 127.0.0.1:5000
}
EOF

# ── 8. Iniciar tudo ──────────────────────────────────────────────────────────
echo ">> Iniciando serviços..."
sudo systemctl daemon-reload
sudo systemctl enable painel-falhas caddy
sudo systemctl restart painel-falhas caddy

sleep 5

echo ""
echo "================================================================"
echo "  INSTALAÇÃO CONCLUÍDA — Backend Principal"
echo "================================================================"
echo ""
echo "  Endereço:  https://${DOMINIO_API}"
echo ""
echo "  Anote esse endereço — vai precisar dele ao configurar o"
echo "  segundo servidor (ponte do WhatsApp) e ao final da migração."
echo ""
echo "  Pra checar se está rodando:  sudo systemctl status painel-falhas"
echo "  Pra ver os logs ao vivo:     sudo journalctl -u painel-falhas -f"
echo "================================================================"
