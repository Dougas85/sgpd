from flask import Flask, render_template, request
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

LIMITE_MIN = timedelta(hours=5, minutes=22)
LIMITE_MAX = timedelta(hours=7, minutes=30)
LIMITE_SAIDA_INICIO = timedelta(minutes=5)
LIMITE_TPC = timedelta(minutes=5)
HORA_MANHA = datetime.strptime("12:00:00", "%H:%M:%S")
HORA_CORTE_INTERVALO = datetime.strptime("14:30:00", "%H:%M:%S")


def parse_hora(h):
    if not h or h.strip() == "":
        return None
    try:
        return datetime.strptime(h.strip(), "%H:%M:%S")
    except ValueError:
        return None


def extrair_data_consulta(soup):
    tag = soup.find("input", {"id": "data"}) or soup.find("input", {"id": "dtOperacao"})
    if tag and tag.get("value"):
        try:
            return datetime.strptime(tag["value"].strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return None
    return None


def extrair_dados(soup):
    dados = []
    for linha in soup.select("table tbody tr"):
        cols = linha.find_all("td")
        if len(cols) < 8:
            continue
        dados.append({
            "matricula": cols[0].text.strip(),
            "nome":      cols[1].text.strip(),
            "distrito":  cols[2].text.strip(),
            "inicio":    parse_hora(cols[4].text),
            "saida":     parse_hora(cols[5].text),
            "retorno":   parse_hora(cols[6].text),
            "fim":       parse_hora(cols[7].text),
        })
    return dados


def agrupar_dados(dados):
    agrupado = {}
    for d in dados:
        mat = d["matricula"]
        if mat not in agrupado:
            agrupado[mat] = {"nome": d["nome"], "registros": [], "inicios": [], "fins": [], "distritos": set()}
        if d["distrito"]:
            agrupado[mat]["distritos"].add(d["distrito"])
        if d["inicio"]:
            agrupado[mat]["inicios"].append(d["inicio"])
        if d["fim"]:
            agrupado[mat]["fins"].append(d["fim"])
        if d["saida"] and d["retorno"]:
            agrupado[mat]["registros"].append({"saida": d["saida"], "retorno": d["retorno"]})
    return agrupado


def verificar_regra1(inicio, registros):
    if not inicio:
        return []
    delta = registros[0]["saida"] - inicio
    if timedelta(0) <= delta < LIMITE_SAIDA_INICIO:
        return [{"msg": "Saída em menos de 5min do início", "nivel": "medio"}]
    return []


def desconto_intervalo(saida, retorno):
    """Desconta 1h de almoço se saída foi de manhã e retorno após 14:30."""
    if saida < HORA_MANHA and retorno >= HORA_CORTE_INTERVALO:
        return timedelta(hours=1)
    return timedelta(0)


def verificar_regra2(tempo_total):
    erros = []
    if tempo_total < LIMITE_MIN:
        erros.append({"msg": f"Tempo externo baixo ({tempo_total})", "nivel": "medio"})
    if tempo_total > LIMITE_MAX:
        erros.append({"msg": f"Tempo externo alto ({tempo_total}) - verificar intervalo", "nivel": "critico"})
    return erros


def verificar_regra3(fim, registros):
    if not fim:
        return []
    ultimo_retorno = max(r["retorno"] for r in registros)
    delta = fim - ultimo_retorno
    if timedelta(0) <= delta < LIMITE_TPC:
        return [{"msg": "Fim de atividade sem TPC mínimo (5min)", "nivel": "critico"}]
    return []


def verificar_regra4(fim):
    if fim is None:
        return [{"msg": "Fim de atividade não registrado", "nivel": "critico"}]
    return []


def deduplicar(erros):
    seen = set()
    result = []
    for e in erros:
        key = (e["msg"], e["nivel"])
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


def calcular_registros_saida(registros):
    tempo_total = timedelta()
    registros_saida = []
    for r in registros:
        tempo_bruto = r["retorno"] - r["saida"]
        desconto = desconto_intervalo(r["saida"], r["retorno"])
        tempo_liquido = tempo_bruto - desconto
        tempo_total += tempo_liquido
        registros_saida.append({
            "saida": r["saida"].strftime("%H:%M:%S"),
            "retorno": r["retorno"].strftime("%H:%M:%S"),
            "tempo": str(tempo_liquido),
        })
    return registros_saida, tempo_total


def processar_funcionario(mat, info):
    registros = sorted(info["registros"], key=lambda x: x["saida"])
    inicio = min(info["inicios"]) if info["inicios"] else None
    fim = max(info["fins"]) if info["fins"] else None

    registros_saida, tempo_total = calcular_registros_saida(registros)

    erros = (
        verificar_regra1(inicio, registros)
        + verificar_regra2(tempo_total)
        + verificar_regra3(fim, registros)
        + verificar_regra4(fim)
    )
    erros = deduplicar(erros)

    if not erros:
        return None

    return {
        "matricula":     mat,
        "nome":          info["nome"],
        "distritos":     sorted(info["distritos"], key=lambda x: x.zfill(10)),
        "inicio":        inicio.strftime("%H:%M:%S") if inicio else "-",
        "fim":           fim.strftime("%H:%M:%S") if fim else "-",
        "tempo_externo": str(tempo_total),
        "registros":     registros_saida,
        "erros":         erros,
    }


def processar_html(html):
    if not html or not html.strip():
        return [], 0, None

    soup     = BeautifulSoup(html, "html.parser")
    data     = extrair_data_consulta(soup)
    agrupado = agrupar_dados(extrair_dados(soup))

    alertas = []
    for mat, info in agrupado.items():
        if not info["registros"]:
            continue
        resultado = processar_funcionario(mat, info)
        if resultado:
            alertas.append(resultado)

    return alertas, len(agrupado), data


app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    alertas = None
    total = 0
    data_consulta = None
    erro_processamento = None

    if request.method == "POST":
        html = request.form.get("dados", "")
        try:
            alertas, total, data_consulta = processar_html(html)
        except Exception:
            erro_processamento = "Erro ao processar o HTML. Verifique se o conteúdo colado é válido."

    return render_template(
        "index.html",
        alertas=alertas,
        total=total,
        data_consulta=data_consulta,
        erro_processamento=erro_processamento,
    )


if __name__ == "__main__":
    app.run(debug=True)
