"""Symbolic calculus via SymPy."""
import re
import requests
from sympy import integrate, diff, latex, Symbol
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations, implicit_multiplication_application
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

# These get set by assistant.py so this module can call the LLM for the
# natural-language fallback. Kept simple to avoid circular imports.
OLLAMA_URL = "http://localhost:11434/api/chat"
CHAT_MODEL = "qwen3:8b"


def _looks_like_latex(text):
    return "\\" in text


def parse_expression(text):
    """Parse LaTeX or standard math syntax. Returns (expr, error_str)."""
    text = text.strip()
    try:
        if _looks_like_latex(text):
            from sympy.parsing.latex import parse_latex
            return parse_latex(text), None
        return parse_expr(text.replace("^", "**"), transformations=_TRANSFORMS), None
    except Exception as e:
        return None, f"Couldn't parse '{text}': {e}"


def _detect_variable(expr):
    free = list(expr.free_symbols)
    if len(free) == 1:
        return free[0]
    names = {s.name: s for s in free}
    for pref in ("x", "t", "y", "z"):
        if pref in names:
            return names[pref]
    return sorted(free, key=lambda s: s.name)[0] if free else Symbol("x")


def _llm_to_math_syntax(text):
    """Translate natural-language math to standard syntax via the LLM."""
    prompt = (
        "Convert this mathematical expression to standard SymPy syntax. "
        "Use ** for powers, * for multiplication, standard function names "
        "(sin, cos, exp, sqrt, log). Output ONLY the expression, nothing else.\n\n"
        f"Expression: {text}"
    )
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0.1},
        }, timeout=30)
        result = r.json()["message"]["content"].strip()
        return re.sub(r'^```\w*\n?|\n?```$', '', result).strip()
    except Exception:
        return None


def _get_expr(expr_text):
    """Parse an expression; if it fails, try LLM translation then reparse."""
    expr, err = parse_expression(expr_text)
    if err:
        translated = _llm_to_math_syntax(expr_text)
        if translated:
            expr, err = parse_expression(translated)
    return expr, err


def do_integrate(expr_text, lower=None, upper=None):
    expr, err = _get_expr(expr_text)
    if err:
        return None, err
    try:
        v = _detect_variable(expr)
        if lower is not None and upper is not None:
            lo, _ = parse_expression(str(lower))
            hi, _ = parse_expression(str(upper))
            result = integrate(expr, (v, lo, hi))
            definite = True
        else:
            result = integrate(expr, v)
            definite = False
        return {"result_latex": latex(result), "definite": definite}, None
    except Exception as e:
        return None, f"Couldn't integrate: {e}"


def do_differentiate(expr_text, at=None):
    expr, err = _get_expr(expr_text)
    if err:
        return None, err
    try:
        v = _detect_variable(expr)
        derivative = diff(expr, v)
        info = {"result_latex": latex(derivative)}
        if at is not None:
            point, _ = parse_expression(str(at))
            evaluated = derivative.subs(v, point)
            info["evaluated_at"] = str(point)
            info["evaluated_result"] = str(evaluated)
        return info, None
    except Exception as e:
        return None, f"Couldn't differentiate: {e}"


def parse_calculus_command(text):
    """Detect a calculus command. Returns a dict or None."""
    # differentiate <expr> [at <point>]
    m = re.match(r'(?:differentiate|diff|derivative of)\s+(.+)', text.strip(), re.IGNORECASE)
    if m:
        body = m.group(1)
        at = None
        at_match = re.search(r'\s+at\s+(.+)$', body, re.IGNORECASE)
        if at_match:
            at = at_match.group(1).strip()
            body = body[:at_match.start()].strip()
        return {"op": "differentiate", "expr": body, "at": at}

    # integrate <expr> [from <a> to <b>]
    m = re.match(r'(?:integrate|integral of)\s+(.+)', text.strip(), re.IGNORECASE)
    if m:
        body = m.group(1)
        lo = hi = None
        fromto = re.search(r'\s+from\s+(.+?)\s+to\s+(.+)$', body, re.IGNORECASE)
        if fromto:
            lo = fromto.group(1).strip()
            hi = fromto.group(2).strip()
            body = body[:fromto.start()].strip()
        return {"op": "integrate", "expr": body, "lower": lo, "upper": hi}

    return None


def run(cmd):
    """Execute a parsed calculus command. Returns (display_text, error)."""
    if cmd["op"] == "integrate":
        info, err = do_integrate(cmd["expr"], cmd.get("lower"), cmd.get("upper"))
        if err:
            return None, err
        if cmd.get("lower") is not None:
            return f"Definite integral:\n\n```latex\n\\[ {info['result_latex']} \\]\n```", None
        return f"Indefinite integral:\n\n```latex\n\\[ {info['result_latex']} + C \\]\n```", None
    else:
        info, err = do_differentiate(cmd["expr"], cmd.get("at"))
        if err:
            return None, err
        body = f"Derivative:\n\n```latex\n\\[ {info['result_latex']} \\]\n```"
        if "evaluated_result" in info:
            body += f"\n\nEvaluated at {info['evaluated_at']}: `{info['evaluated_result']}`"
        return body, None