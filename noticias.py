# Robô "Notícias do ano" (sem inteligência artificial, 100% gratuito)
# Toda semana lê notícias sobre esclerose múltipla e separa as que têm cara de marco
# (aprovações, recusas, fase 3, Anvisa, SUS...) pelas palavras do título e do trecho.
# Elas vão para noticias.json e aparecem no site na lista "Para acompanhar".
# Uma vez por mês, os marcos confirmados são escritos com revisão e entram no marcos.json,
# que o site mostra na linha do tempo.

import datetime
import email.utils
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARQUIVO = "noticias.json"
DIAS_BUSCA = 8      # notícias dos últimos 8 dias (com folga, já que o robô roda toda semana)
DIAS_LISTA = 45     # quanto tempo uma notícia fica na lista "Para acompanhar"
MAX_LISTA = 30
BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))


def google(q, idioma):
    params = {"q": q + " when:%dd" % DIAS_BUSCA}
    params.update({"en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
                   "pt": {"hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}}[idioma])
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


FONTES = [
    ("Multiple Sclerosis News Today", "en", "https://multiplesclerosisnewstoday.com/feed/"),
    ("Google Notícias", "en", google('"multiple sclerosis" (FDA OR EMA OR approval OR approved OR "phase 3" OR rejects)', "en")),
    ("Google Notícias", "pt", google('"esclerose múltipla" (Anvisa OR SUS OR Conitec OR aprova OR aprovação OR "fase 3")', "pt")),
]

# Palavras que indicam um possível marco. Basta uma aparecer no título ou no trecho.
MARCO = re.compile(r"""(
    \bapprov | \bFDA\b | \bEMA\b | \bCHMP\b | \bphase\s?(3|III)\b | \breject | complete\s+response\s+letter
    | \bwithdraw | \brecall | clinical\s+hold | primary\s+endpoint | \bfail(s|ed)?\b | \bmarketing\s+authori[sz]ation
    | \baprova | \bAnvisa\b | \bSUS\b | \bConitec\b | \bincorpora | \bfase\s?(3|III)\b | \brecusa | \bsuspen
    | Minist[ée]rio\s+da\s+Sa[úu]de | \bPCDT\b | \bregistro\b
)""", re.I | re.X)

# Notícias que quase nunca são marcos, mesmo com as palavras acima.
FORA = re.compile(r"(\bopinion\b|\bcolumn\b|\bpodcast\b|\bquiz\b|\bhoróscopo\b|\bceleb|\bfamos[oa])", re.I)


def baixar(url, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={
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


def ler_feed(nome, idioma, url, limite):
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
        texto = titulo + " " + trecho
        if not MARCO.search(texto) or FORA.search(texto):
            continue
        itens.append({"date": quando.date().isoformat(), "title": titulo[:220], "url": link,
                      "source": fonte, "lang": idioma})
    return itens


def chave(titulo):
    return " ".join(re.findall(r"\w+", titulo.lower())[:8])


def main():
    agora = datetime.datetime.now(BRASILIA)
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            atual = json.load(f)
    except Exception:
        atual = {}
    lista = atual.get("watch", []) if isinstance(atual.get("watch"), list) else []
    corte = (agora - datetime.timedelta(days=DIAS_LISTA)).date().isoformat()
    lista = [i for i in lista if i.get("date", "") >= corte]
    vistos = {i["url"] for i in lista} | {chave(i["title"]) for i in lista}

    erros, novos = [], 0
    for nome, idioma, url in FONTES:
        try:
            for item in ler_feed(nome, idioma, url, agora - datetime.timedelta(days=DIAS_BUSCA)):
                if item["url"] in vistos or chave(item["title"]) in vistos:
                    continue
                vistos.update([item["url"], chave(item["title"])])
                lista.append(item)
                novos += 1
            print("%s (%s): ok" % (nome, idioma))
        except Exception as ex:
            print("%s (%s): erro %r" % (nome, idioma, ex))
            erros.append("%s (%s): %s" % (nome, idioma, type(ex).__name__))
    if len(erros) == len(FONTES):
        print("Nenhuma fonte respondeu; nada foi salvo.")
        sys.exit(1)

    lista = sorted(lista, key=lambda i: i.get("date", ""), reverse=True)[:MAX_LISTA]
    saida = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "watch": lista, "errors": erros}
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print("%d notícias novas na lista, %d no total." % (novos, len(lista)))


if __name__ == "__main__":
    main()
