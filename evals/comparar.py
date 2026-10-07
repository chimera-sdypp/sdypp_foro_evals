"""Regresión A/B: corre el mismo set contra dos versiones y decide si la nueva pasa.

Una versión es una carpeta con `prompt.txt` y `pin.json`. El set, el esquema y el
umbral salen siempre de la carpeta donde vive este script (la rama base), así un PR
no puede aprobarse a sí mismo cambiando los casos.

    python evals/comparar.py --base . --candidata ../candidata
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import jsonschema

AQUI = Path(__file__).parent
URL = "http://127.0.0.1:11434"
CONTENEDOR = "evals-ollama"


def llamar(ruta, cuerpo=None, timeout=900):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    pedido = urllib.request.Request(URL + ruta, data=datos, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(pedido, timeout=timeout) as respuesta:
        return json.loads(respuesta.read())


def levantar_servidor(imagen):
    """Levanta el servidor de inferencia que fija el pin y espera a que responda."""
    subprocess.run(["docker", "rm", "-f", CONTENEDOR], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTENEDOR, "-p", "127.0.0.1:11434:11434",
                    "-v", "evals-ollama:/root/.ollama", imagen], check=True, capture_output=True)
    for _ in range(60):
        try:
            return llamar("/api/version", timeout=2)["version"]
        except OSError:
            time.sleep(1)
    raise SystemExit("el servidor de inferencia no arrancó")


def verificar_pin(pin):
    """Trae el modelo por tag y exige que su digest sea el del pin: el tag se puede mover."""
    llamar("/api/pull", {"model": pin["modelo"], "stream": False})
    locales = {m["name"]: m["digest"] for m in llamar("/api/tags")["models"]}
    digest = locales.get(pin["modelo"], "")
    if digest != pin["digest"]:
        raise SystemExit(f"PIN ROTO: {pin['modelo']} ahora es {digest[:12]}, el pin dice {pin['digest'][:12]}")


def evaluar(carpeta, casos, esquema, repeticiones):
    """Corre todos los casos contra una versión. Devuelve el resumen y el detalle por caso."""
    carpeta = Path(carpeta)
    pin = json.loads((carpeta / "pin.json").read_text())
    prompt = (carpeta / "prompt.txt").read_text()
    version_servidor = levantar_servidor(pin["servidor"])
    verificar_pin(pin)

    def pedir(log):
        return llamar("/api/chat", {
            "model": pin["modelo"], "stream": False, "format": esquema, "options": pin["opciones"],
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": log}],
        })

    pedir(casos[0]["log"])  # carga el modelo: que no cuente en la latencia
    detalle, latencias, invalidas, inestables = {}, [], 0, 0
    for caso in casos:
        salidas = []
        for _ in range(repeticiones):
            respuesta = pedir(caso["log"])
            latencias.append(respuesta["total_duration"] / 1e9)
            texto = " ".join(respuesta["message"]["content"].split())
            try:
                salida = json.loads(texto)
                jsonschema.validate(salida, esquema)
                texto = json.dumps(salida)
            except (ValueError, jsonschema.ValidationError):
                salida = None
                invalidas += 1
            salidas.append((salida, texto))
        aciertos = sum(salida == caso["esperado"] for salida, _ in salidas)
        inestables += len({texto for _, texto in salidas}) > 1
        detalle[caso["id"]] = {"pasa": aciertos * 2 > repeticiones, "salida": salidas[0][1][:80] or "(vacía)"}
    subprocess.run(["docker", "rm", "-f", CONTENEDOR], capture_output=True)
    return {
        "modelo": f"{pin['modelo']}@{pin['digest'][:12]}", "servidor": version_servidor,
        "aciertos": sum(d["pasa"] for d in detalle.values()), "invalidas": invalidas,
        "inestables": inestables, "latencia": statistics.median(latencias), "detalle": detalle,
    }


def informe(base, cand, casos, umbral):
    total, x = len(casos), umbral["x"]
    perdidos = base["aciertos"] - cand["aciertos"]
    bloquea = perdidos > x
    filas = [
        "| | base | candidata |", "|---|---|---|",
        f"| modelo | `{base['modelo']}` | `{cand['modelo']}` |",
        f"| servidor | {base['servidor']} | {cand['servidor']} |",
        f"| aciertos | {base['aciertos']}/{total} | {cand['aciertos']}/{total} |",
        f"| salidas que no cumplen el esquema | {base['invalidas']} | {cand['invalidas']} |",
        f"| casos con salidas distintas entre repeticiones | {base['inestables']} | {cand['inestables']} |",
        f"| latencia mediana | {base['latencia']:.2f} s | {cand['latencia']:.2f} s |",
        "", "Casos que cambiaron de resultado:", "",
        "| caso | esperado | base | candidata | cambio |", "|---|---|---|---|---|",
    ]
    marca = {True: "✓", False: "✗"}
    for caso in casos:
        b, c = base["detalle"][caso["id"]], cand["detalle"][caso["id"]]
        if b["pasa"] == c["pasa"]:
            continue
        cambio = "mejoró" if c["pasa"] else "**empeoró**"
        filas.append(f"| {caso['id']} | `{json.dumps(caso['esperado'])}` | {marca[b['pasa']]} `{b['salida']}` "
                     f"| {marca[c['pasa']]} `{c['salida']}` | {cambio} |")
    veredicto = (f"⛔ BLOQUEADO: la candidata pierde {perdidos} casos y el máximo es X = {x}" if bloquea
                 else f"✅ PASA: diferencia de aciertos {-perdidos:+d} (bloquea si pierde más de X = {x})")
    return "\n".join(["## Regresión A/B", "", *filas, "", veredicto, ""]), bloquea


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidata", required=True)
    args = parser.parse_args()

    casos = [json.loads(linea) for linea in (AQUI / "casos.jsonl").read_text().splitlines() if linea.strip()]
    esquema = json.loads((AQUI / "esquema.json").read_text())
    umbral = json.loads((AQUI / "umbral.json").read_text())

    base = evaluar(args.base, casos, esquema, umbral["repeticiones"])
    cand = evaluar(args.candidata, casos, esquema, umbral["repeticiones"])
    texto, bloquea = informe(base, cand, casos, umbral)
    print(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as resumen:
            resumen.write(texto)
    sys.exit(1 if bloquea else 0)


if __name__ == "__main__":
    main()
