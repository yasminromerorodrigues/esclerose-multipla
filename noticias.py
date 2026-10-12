# Robô "Notícias do ano" (sem inteligência artificial, 100% gratuito)
# Toda semana lê notícias sobre esclerose múltipla e separa as que têm cara de marco
# (aprovações, recusas, fase 3, Anvisa, SUS...) pelas palavras do título e do trecho.
#
# Cada notícia passa por três perguntas:
#   1. É sobre mercado financeiro, política ou entretenimento?  -> descartada
#   2. Tem cara de marco (aprovação, fase 3, Anvisa...)?          -> se não, descartada
#   3. Fala de esclerose múltipla?
#        sim                                    -> "watch": aparece no site em "Para acompanhar"
#        não, mas cita um remédio ligado à EM   -> "review": fica guardada para revisão, NÃO aparece no site
#        não                                    -> descartada
#
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
DIAS_LISTA = 45     # quanto tempo uma notícia fica na lista
MAX_LISTA = 30
BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))


def google(q, idioma):
    params = {"q": q + " when:%dd" % DIAS_BUSCA}
    params.update({"en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
                   "pt": {"hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}}[idioma])
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


# (nome, idioma, endereço, a fonte só fala de EM?)
FONTES = [
    ("Multiple Sclerosis News Today", "en", "https://multiplesclerosisnewstoday.com/feed/", True),
    ("Google Notícias", "en", google('"multiple sclerosis" (FDA OR EMA OR approval OR approved OR "phase 3" OR rejects)', "en"), False),
    ("Google Notícias", "pt", google('"esclerose múltipla" (Anvisa OR SUS OR Conitec OR aprova OR aprovação OR "fase 3")', "pt"), False),
]

# 1. Assuntos que não são marcos de EM, mesmo que citem a doença de passagem.
RUIDO = re.compile(r"""(
    \bstocks?\b | \bshares?\b | \binvestors?\b | \banalysts?\b | price\s+target | \bearnings\b | \bdividend
    | \bvaluation\b | Wall\s+Street | \bNYSE\b | \bNasdaq\b | \bSWX\b | \bbuy\s+rating | \bhold\s+rating
    | \ba[çc][õo]es\b | bolsa\s+de\s+valores | \binvestidor | \bcota[çc][ãa]o
    | \belei[çc] | \beleitora | \bprefeit | \bvereador | \bdeputad | \bsenador | \b1º\s+turno | \bsegundo\s+turno
    | \bopinion\b | \bcolumn\b | \bpodcast\b | \bquiz\b | \bhor[óo]scopo\b | \bceleb | \bfamos[oa]
)""", re.I | re.X)

# 2. Palavras que indicam um possível marco. Basta uma aparecer no título ou no trecho.
MARCO = re.compile(r"""(
    \bapprov | \bFDA\b | \bEMA\b | \bCHMP\b | \bphase\s?(3|III)\b | \breject | complete\s+response\s+letter
    | \bwithdraw | \brecall | clinical\s+hold | primary\s+endpoint | \bfail(s|ed)?\b | \bmarketing\s+authori[sz]ation
    | \baprova | \bAnvisa\b | \bSUS\b | \bConitec\b | \bincorpora | \bfase\s?(3|III)\b | \brecusa | \bsuspen
    | Minist[ée]rio\s+da\s+Sa[úu]de | \bPCDT\b | \bregistro\b
)""", re.I | re.X)

# 3a. A notícia fala de esclerose múltipla? ("MS" sozinho não conta: pode ser Microsoft, Mississippi...)
EM = re.compile(r"""(
    multiple\s+sclerosis | escler[oó]se\s+m[uú]ltipla | esclerosis\s+m[uú]ltiple | scl[ée]rose\s+en\s+plaques
    | \b(RRMS|SPMS|PPMS|nrSPMS)\b | \b(relapsing|progressive)\s+MS\b | \bEM(RR|SP|PP)\b
)""", re.I | re.X)

# 3b. Remédios ligados à EM. Sem citar a doença, a notícia vai para revisão (pode ser sobre outra indicação).
CANDIDATO = re.compile(r"""\b(
    tolebrutinib | fenebrutinib | remibrutinib | evobrutinib | frexalimab | vidofludimus | foralumab
    | ublituximab | ocrelizumab | ofatumumab | ozanimod | ponesimod | siponimod | cladribin\w* | natalizumab
    | Ocrevus | Kesimpta | Briumvi | Cenrifki | Mavenclad | Tysabri
)\b""", re.I | re.X)


def classificar(titulo, trecho="", fonte_so_de_em=False):
    """Devolve (situação, motivo). Situação: 'aprovado', 'revisao' ou 'descartado'."""
    texto = "%s %s" % (titulo, trecho)
    if RUIDO.search(texto):
        return "descartado", "mercado, política ou entretenimento"
    if not MARCO.search(texto):
        return "descartado", "sem cara de marco"
    if EM.search(texto) or fonte_so_de_em:
        return "aprovado", "fala de esclerose múltipla"
    if CANDIDATO.search(texto):
        return "revisao", "remédio ligado à EM, mas a notícia não cita a doença"
    return "descartado", "não fala de esclerose múltipla"


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


def ler_feed(nome, idioma, url, so_de_em, limite, agora):
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
        situacao, motivo = classificar(titulo, trecho, so_de_em)
        if situacao == "descartado":
            continue
        itens.append({"date": quando.date().isoformat(), "title": titulo[:220], "url": link,
                      "source": fonte, "lang": idioma, "status": situacao, "reason": motivo,
                      "captured_at": agora.isoformat(timespec="minutes"), "dedicated": so_de_em})
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
    antigos = []
    for k in ("watch", "review"):
        if isinstance(atual.get(k), list):
            antigos += atual[k]
    corte = (agora - datetime.timedelta(days=DIAS_LISTA)).date().isoformat()
    # As notícias que já estavam na lista passam de novo pelo filtro (assim as antigas erradas saem).
    lista = []
    for i in antigos:
        if i.get("date", "") < corte or not i.get("title"):
            continue
        situacao, motivo = classificar(i["title"], "", bool(i.get("dedicated")))
        if situacao != "descartado":
            i.update({"status": situacao, "reason": motivo})
            lista.append(i)
    vistos = {i["url"] for i in lista} | {chave(i["title"]) for i in lista}

    erros, novos = [], 0
    for nome, idioma, url, so_de_em in FONTES:
        try:
            for item in ler_feed(nome, idioma, url, so_de_em, agora - datetime.timedelta(days=DIAS_BUSCA), agora):
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

    lista = sorted(lista, key=lambda i: i.get("date", ""), reverse=True)
    watch = [i for i in lista if i["status"] == "aprovado"][:MAX_LISTA]
    review = [i for i in lista if i["status"] == "revisao"][:MAX_LISTA]
    saida = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "watch": watch, "review": review, "errors": erros}
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print("%d notícias novas. No site: %d. Aguardando revisão: %d." % (novos, len(watch), len(review)))


if __name__ == "__main__":
    main()
