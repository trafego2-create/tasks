FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cron: segunda e sexta às 09:30 (America/Sao_Paulo = UTC-3, logo 12:30 UTC)
# /etc/cron.d/ exige o campo "usuário" na linha
RUN echo "30 12 * * 1,5 root cd /app && python tarefas_atrasadas.py >> /var/log/painel.log 2>&1" > /etc/cron.d/painel-tarefas \
    && chmod 0644 /etc/cron.d/painel-tarefas

# Exporta as variáveis de ambiente do container para o cron conseguir lê-las
CMD ["bash", "-c", "printenv > /etc/environment && cron -f"]
