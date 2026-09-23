"""
Painel de tarefas em atraso por setor — ClickUp -> Slack.

Busca no ClickUp as tarefas com prazo vencido e ainda abertas, descobre o setor
de cada responsável pelo e-mail (tabela `email_submissions` do Supabase, que o
time preencheu pelo formulário aprovasim-emails-site) e posta a contagem por
setor no Slack. Tarefa sem responsável é ignorada.

Uso:
    python tarefas_atrasadas.py            # busca e posta no Slack
    python tarefas_atrasadas.py --dry-run  # só imprime a mensagem e o diagnóstico
    python tarefas_atrasadas.py --teste    # posta com aviso de mensagem de teste

Variáveis de ambiente:
    CLICKUP_API_TOKEN     token pessoal do ClickUp (pk_...)
    CLICKUP_TEAM_ID       id do workspace (padrão 9013878636)
    SUPABASE_URL          projeto onde está email_submissions
    SUPABASE_SERVICE_KEY  secret key (a publishable não lê a tabela por causa do RLS)
    SLACK_BOT_TOKEN       bot token (xoxb-...), bot convidado no canal
    SLACK_CHANNEL         ex.: #operacional
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict

import requests

# Mesma ordem e nomes de área do formulário (aprovasim-emails-site) e da
# mensagem que o time já mandava na mão.
SETORES = [
    ("Administrativo", "🧾"),
    ("RH", "👥"),
    ("Financeiro", "💰"),
    ("Comercial", "🤝"),
    ("CX", "💬"),
    ("Marketing", "📣"),
    ("Operacional", "⚙️"),
    ("Produto", "📚"),
    ("Produção de Conteúdo", "🎥"),
]

# Várias listas usam status de "concluído" que não estão marcados como fechados
# no ClickUp — a API devolve essas tarefas como abertas. Filtra pelo nome.
STATUS_CONCLUIDO = {"complete", "completo", "concluído", "concluido", "finalizado", "feito", "done", "closed"}

CLICKUP_API = "https://api.clickup.com/api/v2"


def _env(nome: str, padrao: str | None = None) -> str:
    valor = os.environ.get(nome, padrao)
    if not valor:
        sys.exit(f"Variável de ambiente {nome} não configurada.")
    return valor


def buscar_setores_por_email() -> dict[str, str]:
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_SERVICE_KEY")
    r = requests.get(
        f"{url}/rest/v1/email_submissions",
        params={"select": "area,email"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    return {row["email"].strip().lower(): row["area"] for row in r.json() if row.get("email")}


def buscar_tarefas_atrasadas() -> list[dict]:
    token = _env("CLICKUP_API_TOKEN")
    team_id = _env("CLICKUP_TEAM_ID", "9013878636")
    agora_ms = int(time.time() * 1000)
    tarefas, page = [], 0
    while True:
        r = requests.get(
            f"{CLICKUP_API}/team/{team_id}/task",
            params={
                "due_date_lt": agora_ms,
                "include_closed": "false",
                "subtasks": "true",
                "page": page,
            },
            headers={"Authorization": token},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        tarefas.extend(data.get("tasks", []))
        if data.get("last_page", True) or not data.get("tasks"):
            break
        page += 1
    return tarefas


def _concluida(tarefa: dict) -> bool:
    status = tarefa.get("status") or {}
    return status.get("type") in ("closed", "done") or (status.get("status") or "").strip().lower() in STATUS_CONCLUIDO


def contar_por_setor(tarefas: list[dict], setor_por_email: dict[str, str]):
    contagem: dict[str, int] = defaultdict(int)
    sem_setor: dict[str, int] = defaultdict(int)
    for t in tarefas:
        if _concluida(t) or not t.get("assignees"):
            continue
        # Tarefa com responsáveis de setores diferentes conta uma vez em cada setor.
        setores = set()
        for a in t["assignees"]:
            email = (a.get("email") or "").strip().lower()
            setor = setor_por_email.get(email)
            if setor:
                setores.add(setor)
            else:
                sem_setor[f"{a.get('username')} <{email}>"] += 1
        for s in setores:
            contagem[s] += 1
    return contagem, sem_setor


def montar_mensagem(contagem: dict[str, int], teste: bool) -> str:
    linhas = []
    if teste:
        linhas += ["🧪 *[TESTE] Mensagem automática — pode ignorar*", ""]
    linhas += [
        "Bom dia, time! ☀️",
        "",
        "Passando aqui com a atualização do nosso painel de tarefas.",
        "",
        "Hoje, em nosso acompanhamento de tarefas em atraso por setor, está assim:",
        "",
    ]
    for setor, emoji in SETORES:
        n = contagem.get(setor, 0)
        linhas.append(f"{emoji} {setor}: {n} {'tarefa' if n == 1 else 'tarefas'} em atraso")
    total = sum(contagem.get(s, 0) for s, _ in SETORES)
    linhas += [
        "",
        f"📌 Total geral: {total:02d} tarefas em atraso",
        "",
        "Peço que cada setor olhe com atenção para suas pendências ainda na parte da manhã.",
        "",
        "Se a tarefa já foi concluída, atualizem no ClickUp. Se ainda está em andamento, "
        "ajustem o prazo e deixem o status correto.",
        "",
        "A ideia é mantermos a operação organizada, sem deixar tarefa parada ou atraso "
        "acumulando. Vamos cuidar disso com responsabilidade ao longo do dia. 👊",
    ]
    return "\n".join(linhas)


def enviar_slack(mensagem: str) -> None:
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {_env('SLACK_BOT_TOKEN')}"},
        json={"channel": _env("SLACK_CHANNEL"), "text": mensagem},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        sys.exit(f"Slack recusou o envio: {data.get('error')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="não posta, só imprime")
    parser.add_argument("--teste", action="store_true", help="marca a mensagem como teste")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # console do Windows não imprime emoji em cp1252

    setor_por_email = buscar_setores_por_email()
    tarefas = buscar_tarefas_atrasadas()
    contagem, sem_setor = contar_por_setor(tarefas, setor_por_email)
    mensagem = montar_mensagem(contagem, teste=args.teste)

    print(f"{len(tarefas)} tarefas vencidas retornadas pelo ClickUp; {len(setor_por_email)} e-mails mapeados.")
    if sem_setor:
        print("Responsáveis sem setor (e-mail do ClickUp não está em email_submissions):")
        for pessoa, n in sorted(sem_setor.items(), key=lambda x: -x[1]):
            print(f"  {n:>3}  {pessoa}")
    print("\n" + mensagem)

    if not args.dry_run:
        enviar_slack(mensagem)
        print("\nEnviado ao Slack.")


if __name__ == "__main__":
    main()
