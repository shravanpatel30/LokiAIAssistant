"""Symbolic calculus via SymPy."""
import re
import requests
from sympy import integrate, diff, latex, Symbol, E
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations, implicit_multiplication_application
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

# These get set by assistant.py so this module can call the LLM for the
# natural-language fallback. Kept simple to avoid circular imports.
OLLAMA_URL = "http://localhost:11434/api/chat"
CHAT_MODEL = "qwen3:8b"


# Multi-letter names that are real math, not English words
_MATH_WORDS = {
    "sin", "cos", "tan", "sec", "csc", "cot", "asin", "acos", "atan",
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "exp", "log", "ln", "sqrt", "abs", "erf", "gamma", "pi", "oo",
    "alpha", "beta", "delta", "epsilon", "theta", "lambda", "mu", "nu",
    "sigma", "tau", "phi", "psi", "omega", "rho", "kappa", "eta", "zeta", "xi", "chi",
}


def _looks_like_latex(text):
    return "\\" in text


def _looks_like_words(text):
    """True for "x squared plus sin x" — English the parser would otherwise
    silently split into single-letter variables (s*q*u*a*r*e*d)."""
    return any(len(w) > 1 and w.lower() not in _MATH_WORDS
               for w in re.findall(r"[A-Za-z]+", text))


def parse_expression(text):
    """Parse LaTeX or standard math syntax. Returns (expr, error_str)."""
    text = text.strip()
    try:
        if _looks_like_latex(text):
            from sympy.parsing.latex import parse_latex
            expr = parse_latex(text)
        else:
            expr = parse_expr(text.replace("^", "**"), transformations=_TRANSFORMS)
        # A bare "e" means Euler's number, not a variable named e
        return expr.subs(Symbol("e"), E), None
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
    """Parse an expression. If it's written in words, or doesn't parse, ask
    the LLM to translate it to math syntax and parse that instead."""
    if not _looks_like_latex(expr_text) and _looks_like_words(expr_text):
        expr, err = None, f"Couldn't parse '{expr_text}' as math."
    else:
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
        f = latex(expr)
        if expr.is_Add:                      # ∫ (x² + sin x) dx, not ∫ x² + sin x dx
            f = f"\\left( {f} \\right)"
        info = {"input_latex": f, "var": latex(v)}
        if lower is not None and upper is not None:
            lo, err_lo = parse_expression(str(lower))
            hi, err_hi = parse_expression(str(upper))
            if err_lo or err_hi:
                return None, err_lo or err_hi
            info.update(lower_latex=latex(lo), upper_latex=latex(hi),
                        result_latex=latex(integrate(expr, (v, lo, hi))))
        else:
            info["result_latex"] = latex(integrate(expr, v))
        return info, None
    except Exception as e:
        return None, f"Couldn't integrate: {e}"


def do_differentiate(expr_text, at=None):
    expr, err = _get_expr(expr_text)
    if err:
        return None, err
    try:
        v = _detect_variable(expr)
        derivative = diff(expr, v)
        info = {"input_latex": latex(expr), "var": latex(v),
                "result_latex": latex(derivative)}
        if at is not None:
            point, err_at = parse_expression(str(at))
            if err_at:
                return None, err_at
            info["point_latex"] = latex(point)
            info["evaluated_latex"] = latex(derivative.subs(v, point))
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


def _show(title, equation):
    """Markdown for the chat window: the equation rendered by KaTeX, then the
    same LaTeX in a code block with a Copy button."""
    return (f"**{title}**\n\n"
            f"$$ {equation} $$\n\n"
            f"```latex\n{equation}\n```")


def run(cmd):
    """Execute a parsed calculus command. Returns (display_text, error)."""
    if cmd["op"] == "integrate":
        info, err = do_integrate(cmd["expr"], cmd.get("lower"), cmd.get("upper"))
        if err:
            return None, err
        f, v, res = info["input_latex"], info["var"], info["result_latex"]
        if "lower_latex" in info:
            eq = (f"\\int_{{{info['lower_latex']}}}^{{{info['upper_latex']}}} "
                  f"{f} \\, d{v} = {res}")
            return _show("Definite integral", eq), None
        return _show("Indefinite integral", f"\\int {f} \\, d{v} = {res} + C"), None

    info, err = do_differentiate(cmd["expr"], cmd.get("at"))
    if err:
        return None, err
    f, v, res = info["input_latex"], info["var"], info["result_latex"]
    body = _show("Derivative", f"\\frac{{d}}{{d{v}}} \\left( {f} \\right) = {res}")
    if "evaluated_latex" in info:
        at_eq = (f"\\left. \\frac{{d}}{{d{v}}} \\left( {f} \\right) "
                 f"\\right|_{{{v} = {info['point_latex']}}} = {info['evaluated_latex']}")
        body += "\n\n" + _show(f"Evaluated at {cmd['at']}", at_eq)
    return body, None