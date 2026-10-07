# Evals como gate de CI/CD — regresión A/B

**Grupo Cerberus** — Baez, Nomico, Resnik · Sistemas Distribuidos y Programación Paralela (UNLu, 2C 2026)

Foro 01, Bloque B, opción 2. La tarea del modelo es clasificar una línea de log del
[servidor de tareas del TP2](https://github.com/chimera-sdypp/sdypp_tp2): devuelve un JSON
con la `categoria` de la línea y, si avisa que un nodo no responde, cuál (`nodo_caido`).

En cada Pull Request el workflow corre el mismo set contra dos versiones, la de `main` y la
del PR, y bloquea el merge si la nueva pierde más de X casos.

## Qué hay

| Archivo | Qué es |
|---|---|
| `prompt.txt` | El prompt. Es lo que cambia en un PR de prompt. |
| `pin.json` | Todo lo demás que define una versión: modelo y su digest, imagen del servidor de inferencia por digest, temperatura, semilla, ventana de contexto y tope de tokens. |
| `evals/casos.jsonl` | El set: 20 líneas de log con la salida esperada. |
| `evals/esquema.json` | JSON Schema de la salida. Se le pasa al modelo y se valida la respuesta. |
| `evals/umbral.json` | `x` (casos que puede perder la versión nueva) y `repeticiones` por caso. |
| `evals/comparar.py` | Corre las dos versiones, arma la tabla y decide. |
| `.github/workflows/evals.yml` | El gate. El check `Regresión A/B` es obligatorio en `main`. |

## Cómo decide

Cada caso se corre 3 veces y se da por bueno si la mayoría de las respuestas cumple el
esquema y coincide con la esperada. El gate bloquea si
`aciertos(base) − aciertos(candidata) > X`, con **X = 1** de 20.

Antes de evaluar se trae el modelo por tag y se compara su digest con el de `pin.json`: si
el tag se movió, el job falla en vez de evaluar otro modelo sin avisar.

El script, el set, el esquema y el umbral se toman de la rama base, no del PR: un PR no se
puede aprobar cambiando los casos.

## Ejecutar

Hace falta Docker y Python 3.11 o superior.

```bash
pip install -r requirements.txt
python evals/comparar.py --base . --candidata ../otra-version
```

`--base` y `--candidata` son carpetas con un `prompt.txt` y un `pin.json`. El script levanta
el servidor de inferencia de cada pin en un contenedor, baja el modelo, imprime la tabla y
termina con código 1 si bloquea.
