# Robô "Estudos pelo mundo"
# Toda semana busca no PubMed estudos sobre esclerose múltipla feitos em vários países,
# pede ao Claude um título e um resumo simples em português e grava tudo em estudos.json.
# O site lê o estudos.json e mostra as fichas com o aviso de resumo automático.

import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARQUIVO = "estudos.json"
MODELO = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
DIAS = 14            # janela de busca no PubMed (com folga, para nada escapar entre uma semana e outra)
POR_GRUPO = 2        # máximo de estudos novos por grupo a cada semana
CANDIDATOS = 8       # quantos artigos de cada grupo vão para o Claude escolher
MAXIMO_NO_SITE = 30  # quantas fichas automáticas ficam no site

# Grupos = botões de filtro do site (idioma do país de origem).
# Cada país: (nome em português, sigla da ficha, termos de afiliação no PubMed, palavras para reconhecer o país)
GRUPOS = [
    ("pt", [("Brasil", "PT", ["Brazil"], ["Brazil", "Brasil"]),
            ("Portugal", "PT", ["Portugal"], ["Portugal"])]),
    ("ja", [("Japão", "JA", ["Japan"], ["Japan"])]),
    ("es", [("Espanha", "ES", ["Spain"], ["Spain", "España"]),
            ("México", "ES", ["Mexico"], ["Mexico", "México"]),
            ("Argentina", "ES", ["Argentina"], ["Argentina"]),
            ("Chile", "ES", ["Chile"], ["Chile"]),
            ("Colômbia", "ES", ["Colombia"], ["Colombia"])]),
    ("fr", [("França", "FR", ["France"], ["France"]),
            ("Bélgica", "NL", ["Belgium"], ["Belgium", "België", "Belgique"]),
            ("Holanda", "NL", ["Netherlands"], ["Netherlands", "Nederland"])]),
    ("de", [("Alemanha", "DE", ["Germany"], ["Germany", "Deutschland"]),
            ("Áustria", "DE", ["Austria"], ["Austria", "Österreich"]),
            ("Suíça", "DE", ["Switzerland"], ["Switzerland", "Schweiz", "Suisse"])]),
    ("en", [("EUA", "EN", ["USA", "United States"], ["USA", "United States", "U.S.A."]),
            ("Reino Unido", "EN", ["UK", "United Kingdom", "England", "Scotland"],
             ["UK", "United Kingdom", "England", "Scotland", "Wales"]),
            ("Canadá", "EN", ["Canada"], ["Canada"]),
            ("Austrália", "EN", ["Australia"], ["Australia"])]),
]

TIPOS = ["Em pessoas", "Em animais", "Em laboratório", "Revisão de estudos"]

EM = '("multiple sclerosis"[MeSH Terms] OR "multiple sclerosis"[Title/Abstract])'
FORA = ('NOT (case reports[pt] OR comment[pt] OR editorial[pt] OR letter[pt] '
        'OR published erratum[pt] OR retraction of publication[pt] OR news[pt])')

PESO_TIPO = {
    "Randomized Controlled Trial": 4, "Meta-Analysis": 4, "Systematic Review": 4,
    "Clinical Trial": 3, "Clinical Trial, Phase III": 4, "Clinical Trial, Phase II": 3,
    "Multicenter Study": 2, "Observational Study": 2, "Review": 1,
}

INSTRUCOES = """Você escreve para um site brasileiro de informação sobre esclerose múltipla (EM), lido por pacientes e familiares, em linguagem simples.

Você vai receber artigos científicos recentes (PMID, título, revista, tipo de publicação e resumo em inglês). Escolha no máximo {n} que sejam mais relevantes ou interessantes para quem convive com EM: dê preferência a ensaios clínicos, revisões, estudos grandes e achados com importância prática. Deixe de fora artigos muito técnicos ou de metodologia, e os que não tratam de EM de verdade. Se nenhum servir, devolva a lista vazia.

Para cada escolhido, escreva em português do Brasil:
- "titulo": no máximo 6 palavras, chamativo mas fiel ao estudo. Exemplos de tom: "Borda larga, piora rápida", "Musculação protege nervos", "Sono na adolescência".
- "resumo": 2 ou 3 frases, no máximo 60 palavras. Diga quem foi estudado (e quantas pessoas, se o resumo informar) e o principal achado. Use só o que está no resumo do artigo, sem acrescentar nada. Explique termos técnicos em poucas palavras. Não prometa cura, não dê conselho de tratamento e não exagere a importância. Se for estudo em animais ou em laboratório, deixe isso claro no texto.
- "tipo": exatamente um destes: "Em pessoas", "Em animais", "Em laboratório", "Revisão de estudos".

Responda só com JSON, sem nenhum texto antes ou depois e sem crases, neste formato:
{{"escolhidos": [{{"pmid": "123", "titulo": "...", "resumo": "...", "tipo": "Em pessoas"}}]}}"""


def baixar(url, dados=None, cabecalhos=None, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, headers=cabecalhos or {"User-Agent": "estudos-bot"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))


def buscar_pmids(paises):
    afil = " OR ".join('"%s"[ad]' % t for _, _, termos, _ in paises for t in termos)
    termo = "%s AND (%s) AND hasabstract %s" % (EM, afil, FORA)
    q = urllib.parse.urlencode({"db": "pubmed", "term": termo, "retmode": "json", "retmax": 40,
                                "reldate": DIAS, "datetype": "edat", "sort": "relevance",
                                "tool": "estudos-bot", "email": "tudosobreesclerosemultipla@gmail.com"})
    d = json.loads(baixar("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + q))
    time.sleep(0.5)
    return d.get("esearchresult", {}).get("idlist", [])


def texto(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def data_publicacao(art):
    for caminho in ("ArticleDate", "Journal/JournalIssue/PubDate"):
        d = art.find(caminho)
        if d is not None and d.findtext("Year"):
            meses = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
                     "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
            mes = d.findtext("Month") or ""
            mes = meses.get(mes[:3], mes.zfill(2) if mes.isdigit() else "")
            dia = (d.findtext("Day") or "").zfill(2) if d.findtext("Day") else ""
            return "-".join(x for x in (d.findtext("Year"), mes, dia) if x)
    return ""


def detalhes(pmids):
    if not pmids:
        return []
    q = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
                                "tool": "estudos-bot", "email": "tudosobreesclerosemultipla@gmail.com"})
    raiz = ET.fromstring(baixar("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + q))
    time.sleep(0.5)
    artigos = []
    for pa in raiz.findall("PubmedArticle"):
        mc = pa.find("MedlineCitation")
        art = mc.find("Article")
        partes = []
        for ab in art.findall("Abstract/AbstractText"):
            rotulo = ab.get("Label")
            partes.append((rotulo + ": " if rotulo else "") + texto(ab))
        afils = [texto(a) for a in art.findall("AuthorList/Author/AffiliationInfo/Affiliation")]
        artigos.append({
            "pmid": mc.findtext("PMID"),
            "titulo": texto(art.find("ArticleTitle")),
            "revista": art.findtext("Journal/ISOAbbreviation") or art.findtext("Journal/Title") or "",
            "tipos": [texto(t) for t in art.findall("PublicationTypeList/PublicationType")],
            "resumo": " ".join(partes)[:2500],
            "afiliacoes": afils,
            "data": data_publicacao(art),
        })
    return artigos


def achar_pais(artigo, paises):
    def bate(afil, palavras):
        return any(re.search(r"(?<![A-Za-z])" + re.escape(p) + r"(?![A-Za-z])", afil) for p in palavras)
    afils = artigo["afiliacoes"]
    for afil in afils[:1] + afils:  # primeiro autor tem preferência
        for nome, sigla, _, palavras in paises:
            if bate(afil, palavras):
                return nome, sigla
    return None, None


def pontuar(artigo):
    return max([PESO_TIPO.get(t, 0) for t in artigo["tipos"]] + [0])


def perguntar_claude(chave, candidatos):
    lista = "\n\n".join(
        "PMID: %s\nTítulo: %s\nRevista: %s\nTipo de publicação: %s\nResumo: %s"
        % (a["pmid"], a["titulo"], a["revista"], ", ".join(a["tipos"]), a["resumo"]) for a in candidatos)
    corpo = json.dumps({
        "model": MODELO,
        "max_tokens": 1500,
        "system": INSTRUCOES.format(n=POR_GRUPO),
        "messages": [{"role": "user", "content": lista}],
    }).encode("utf-8")
    resp = json.loads(baixar("https://api.anthropic.com/v1/messages", corpo, {
        "x-api-key": chave, "anthropic-version": "2023-06-01", "content-type": "application/json"}))
    txt = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    txt = re.sub(r"```(?:json)?", "", txt).strip()
    ini, fim = txt.find("{"), txt.rfind("}")
    return json.loads(txt[ini:fim + 1]).get("escolhidos", []) if ini > -1 else []


def limpar(s, limite):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= limite else s[:limite].rsplit(" ", 1)[0] + "…"


def main():
    hoje = datetime.date.today().isoformat()
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            atual = json.load(f)
    except Exception:
        atual = {}
    itens = atual.get("items", []) if isinstance(atual.get("items"), list) else []
    ja_tem = {i.get("pmid") for i in itens}
    erros = []

    chave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not chave:
        print("ERRO: falta o segredo ANTHROPIC_API_KEY em Settings > Secrets and variables > Actions.")
        sys.exit(1)

    novos = []
    for grupo, paises in GRUPOS:
        try:
            pmids = [p for p in buscar_pmids(paises) if p not in ja_tem]
            artigos = []
            for a in detalhes(pmids):
                nome, sigla = achar_pais(a, paises)
                if nome and len(a["resumo"]) > 200:
                    a["pais"], a["sigla"] = nome, sigla
                    artigos.append(a)
            artigos.sort(key=pontuar, reverse=True)
            candidatos = artigos[:CANDIDATOS]
            if not candidatos:
                print("%s: nenhum estudo novo" % grupo)
                continue
            por_pmid = {a["pmid"]: a for a in candidatos}
            escolhidos = perguntar_claude(chave, candidatos)
            n = 0
            for e in escolhidos:
                a = por_pmid.get(str(e.get("pmid", "")).strip())
                if not a or a["pmid"] in ja_tem or n >= POR_GRUPO:
                    continue
                titulo, resumo = limpar(e.get("titulo"), 80), limpar(e.get("resumo"), 600)
                if not titulo or not resumo:
                    continue
                novos.append({
                    "pmid": a["pmid"],
                    "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % a["pmid"],
                    "lang": grupo,
                    "code": a["sigla"],
                    "country": a["pais"],
                    "title": titulo,
                    "summary": resumo,
                    "kind": e.get("tipo") if e.get("tipo") in TIPOS else "",
                    "journal": a["revista"],
                    "date": a["data"],
                    "original_title": limpar(a["titulo"], 300),
                    "added": hoje,
                })
                ja_tem.add(a["pmid"])
                n += 1
            print("%s: %d candidatos, %d escolhidos" % (grupo, len(candidatos), n))
        except Exception as ex:
            print("%s: erro %r" % (grupo, ex))
            erros.append("%s: %s" % (grupo, type(ex).__name__))

    if erros and not novos and len(erros) == len(GRUPOS):
        print("Todos os grupos falharam; nada foi salvo.")
        sys.exit(1)

    itens = (novos + itens)[:MAXIMO_NO_SITE]
    saida = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "items": itens, "errors": erros}
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print("Pronto: %d novos, %d no total." % (len(novos), len(itens)))


if __name__ == "__main__":
    main()
