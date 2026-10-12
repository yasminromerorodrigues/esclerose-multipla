# Robô "Notícias do ano"
# Toda semana lê notícias sobre esclerose múltipla e pede a uma inteligência artificial gratuita
# (GitHub Models, pela ação oficial actions/ai-inference, sem chave e sem custo) para escolher
# só os marcos importantes. O resultado vai para noticias.json, que o site mostra na linha do tempo.
#
# O robô roda em três etapas (veja .github/workflows/noticias.yml):
#   python3 noticias.py preparar  -> lê as notícias e escreve o pedido para a IA
#   (a ação actions/ai-inference envia o pedido e guarda a resposta)
#   python3 noticias.py salvar    -> lê a resposta da IA e atualiza noticias.json

import datetime
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARQUIVO = "noticias.json"
PAGINA = "index.html"
PASTA_IA = os.environ.get("RUNNER_TEMP", "/tmp")
SISTEMA = os.path.join(PASTA_IA, "ia_sistema.txt")
PEDIDO = os.path.join(PASTA_IA, "ia_pedido.txt")
CANDIDATOS = os.path.join(PASTA_IA, "ia_candidatos.json")
DIAS = 8             # notícias dos últimos 8 dias (com folga, já que o robô roda toda semana)
MAX_POR_SEMANA = 3   # no máximo 3 marcos novos por semana
MAX_CANDIDATOS = 30
BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))


def google(q, idioma):
    params = {"q": q + " when:%dd" % DIAS}
    params.update({"en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
                   "pt": {"hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}}[idioma])
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


FONTES = [
    ("Multiple Sclerosis News Today", "https://multiplesclerosisnewstoday.com/feed/"),
    ("Google Notícias", google('"multiple sclerosis" (FDA OR EMA OR approval OR approved OR "phase 3" OR rejects OR trial)', "en")),
    ("Google Notícias", google('"esclerose múltipla" (Anvisa OR SUS OR Conitec OR aprova OR aprovação OR medicamento OR estudo)', "pt")),
]

INSTRUCOES = """Você cuida da linha do tempo "O que aconteceu em {ano}" de um site brasileiro sobre esclerose múltipla (EM), lido por pacientes e familiares.

Você vai receber:
1. Os eventos que JÁ ESTÃO na linha do tempo.
2. Notícias da última semana (id, data, fonte, título e trecho).

Escolha no máximo {n} notícias que sejam MARCOS de verdade para quem convive com EM, como:
- decisões de agências sobre remédios para EM (FDA, EMA, Anvisa) ou incorporação/retirada no SUS (Conitec, Ministério da Saúde);
- resultados de estudos de fase 3 ou de grandes estudos clínicos;
- destaques de congressos importantes (ECTRIMS, ACTRIMS, AAN, CMSC);
- descobertas científicas grandes, publicadas em revistas de peso;
- mudanças relevantes de acesso, direitos ou tratamento no Brasil.

Deixe de fora: histórias pessoais, opinião, textos explicativos, estudos pequenos ou iniciais, propaganda, notícias sobre famosos e qualquer evento que já esteja na linha do tempo, mesmo com outras palavras. Na maioria das semanas não há marco nenhum: nesse caso devolva a lista vazia. Se várias notícias falam do mesmo evento, escolha só uma, a mais completa.

Para cada escolhida, escreva em português do Brasil:
- "id": o id da notícia.
- "titulo": no máximo 6 palavras, no estilo dos títulos que já existem (exemplos: "Europa recomenda o tolebrutinibe", "FDA recusa o tolebrutinibe").
- "texto": 1 ou 2 frases, no máximo 40 palavras, só com informações que estão no título ou no trecho, sem acrescentar nada. Use linguagem simples, não prometa cura e não dê conselho de tratamento.
- "reves": true se for um revés (recusa, estudo que falhou, alerta de segurança, retirada do mercado); senão false.

Responda só com JSON, sem texto antes ou depois e sem crases, neste formato:
{{"eventos": [{{"id": 3, "titulo": "...", "texto": "...", "reves": false}}]}}"""


def baixar(url, dados=None, cabecalhos=None, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, headers=cabecalhos or {
                "User-Agent": "Mozilla/5.0 (compatible; noticias-bot; +https://yasminromerorodrigues.github.io/esclerose-multipla/)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))


def sem_html(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def ler_feed(nome, url, limite):
    raiz = ET.fromstring(baixar(url))
    itens = []
    for it in raiz.iter("item"):
        titulo = sem_html(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        try:
            quando = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(BRASILIA)
        except Exception:
            continue
        if quando < limite or not titulo or not link.startswith("https://"):
            continue
        fonte = sem_html(it.findtext("source")) or nome
        if fonte != nome and titulo.endswith(" - " + fonte):
            titulo = titulo[: -len(" - " + fonte)]
        trecho = sem_html(it.findtext("description"))
        if trecho.startswith(titulo):  # no Google Notícias o trecho costuma repetir só o título
            trecho = ""
        itens.append({"titulo": titulo, "url": link, "fonte": fonte,
                      "data": quando.date().isoformat(), "trecho": trecho[:600]})
    return itens


def eventos_da_pagina():
    try:
        s = open(PAGINA, encoding="utf-8").read()
    except Exception:
        return []
    ini = s.find('<section id="noticias">')
    fim = s.find("</section>", ini)
    trecho = s[ini:fim] if ini > -1 else ""
    eventos = [sem_html(h) for h in re.findall(r"<h4>(.*?)</h4>", trecho, re.S)]
    radar = re.search(r"No radar:(.*?)</p>", trecho, re.S)
    if radar:
        eventos.append("Já mencionado como esperado: " + sem_html(radar.group(1)))
    return eventos


def extrair_json(txt):
    txt = re.sub(r"```(?:json)?", "", txt or "").strip()
    ini, fim = txt.find("{"), txt.rfind("}")
    if ini == -1:
        if not txt or "eventos" not in txt:
            return []  # o modelo respondeu sem JSON: entende como "nenhum marco"
        raise ValueError("resposta sem JSON")
    if fim < ini:
        raise ValueError("resposta cortada no meio")
    return json.loads(txt[ini:fim + 1]).get("eventos", [])


def limpar(s, limite):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= limite else s[:limite].rsplit(" ", 1)[0] + "…"


def carregar():
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            atual = json.load(f)
    except Exception:
        atual = {}
    return atual.get("items", []) if isinstance(atual.get("items"), list) else []


def gravar(itens, erros):
    saida = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "items": sorted(itens, key=lambda i: i.get("date", ""), reverse=True)[:200], "errors": erros}
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)


def saida_github(nome, valor):
    caminho = os.environ.get("GITHUB_OUTPUT")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (nome, valor))


def preparar():
    agora = datetime.datetime.now(BRASILIA)
    ano = str(agora.year)
    limite = agora - datetime.timedelta(days=DIAS)
    itens = carregar()
    erros, candidatos = [], []
    vistos = {i.get("url") for i in itens} | {str(i.get("original_title", "")).lower() for i in itens}
    for nome, url in FONTES:
        try:
            for c in ler_feed(nome, url, limite):
                chave_titulo = c["titulo"].lower()
                if c["url"] in vistos or chave_titulo in vistos or not c["data"].startswith(ano):
                    continue
                vistos.update([c["url"], chave_titulo])
                candidatos.append(c)
            print("%s: ok" % nome)
        except Exception as ex:
            print("%s: erro %r" % (nome, ex))
            erros.append("%s: %s" % (nome, type(ex).__name__))
    if len(erros) == len(FONTES):
        print("Nenhuma fonte respondeu; nada foi salvo.")
        sys.exit(1)
    candidatos = candidatos[:MAX_CANDIDATOS]
    existentes = eventos_da_pagina() + ["%s (%s)" % (i["title"], i["date"]) for i in itens
                                        if str(i.get("date", "")).startswith(ano)]
    texto = "EVENTOS QUE JÁ ESTÃO NA LINHA DO TEMPO:\n" + "\n".join("- " + e for e in existentes)
    texto += "\n\nNOTÍCIAS DA SEMANA:\n\n" + "\n\n".join(
        "id: %d\ndata: %s\nfonte: %s\ntítulo: %s%s" % (i, c["data"], c["fonte"], c["titulo"],
                                                      "\ntrecho: " + c["trecho"][:300] if c["trecho"] else "")
        for i, c in enumerate(candidatos))
    with open(SISTEMA, "w", encoding="utf-8") as f:
        f.write(INSTRUCOES.format(ano=ano, n=MAX_POR_SEMANA))
    with open(PEDIDO, "w", encoding="utf-8") as f:
        f.write(texto)
    with open(CANDIDATOS, "w", encoding="utf-8") as f:
        json.dump({"candidatos": candidatos, "erros": erros}, f, ensure_ascii=False)
    print("%d notícias novas para a IA avaliar" % len(candidatos))
    saida_github("tem_noticias", "sim" if candidatos else "nao")


def salvar():
    itens = carregar()
    try:
        with open(CANDIDATOS, encoding="utf-8") as f:
            dados = json.load(f)
    except Exception:
        dados = {}
    candidatos, erros = dados.get("candidatos", []), dados.get("erros", [])
    arquivo = os.environ.get("RESPOSTA", "").strip()
    novos = []
    if candidatos:
        if not arquivo or not os.path.exists(arquivo):
            print("ERRO: a IA não devolveu resposta.")
            sys.exit(1)
        resposta = open(arquivo, encoding="utf-8").read()
        try:
            eventos = extrair_json(resposta)
        except Exception as ex:
            print("ERRO ao ler a resposta da IA: %r | início: %r" % (ex, resposta[:300]))
            sys.exit(1)
        hoje = datetime.datetime.now(BRASILIA).date().isoformat()
        for e in eventos[:MAX_POR_SEMANA]:
            try:
                c = candidatos[int(e.get("id"))]
            except Exception:
                continue
            titulo, texto = limpar(e.get("titulo"), 70), limpar(e.get("texto"), 320)
            if not titulo or not texto:
                continue
            novos.append({"date": c["data"], "title": titulo, "text": texto, "neg": e.get("reves") is True,
                          "url": c["url"], "source": c["fonte"], "original_title": c["titulo"], "added": hoje})
    print("%d notícias avaliadas, %d marcos novos" % (len(candidatos), len(novos)))
    gravar(novos + itens, erros)


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "preparar":
        preparar()
    elif modo == "salvar":
        salvar()
    else:
        print("Use: python3 noticias.py preparar | salvar")
        sys.exit(1)
