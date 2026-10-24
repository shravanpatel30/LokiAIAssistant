"""Generate the test images (with exactly known answers) for run_eval.py.
Already run once — the PNGs are in images/. Re-run only to regenerate them."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).parent / "images"
OUT.mkdir(exist_ok=True)
plt.rcParams.update({"mathtext.fontset": "cm", "font.family": "serif", "font.size": 13})


def equation(name, tex_lines, size=24):
    """Render equations on white, like a screenshot from a paper."""
    fig = plt.figure(figsize=(8, 0.9 * len(tex_lines) + 0.4), dpi=150)
    for i, tex in enumerate(tex_lines):
        fig.text(0.5, 1 - (i + 0.7) / (len(tex_lines) + 0.4), f"${tex}$",
                 ha="center", va="center", fontsize=size)
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", pad_inches=0.25, facecolor="white")
    plt.close(fig)


equation("eq_gaussian", [r"\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}"])
equation("eq_schrodinger",
         [r"i\hbar\,\frac{\partial \psi}{\partial t} = -\frac{\hbar^2}{2m}\nabla^2\psi + V(\mathbf{r})\,\psi"])
equation("eq_transmon", [r"\hat{H} = 4E_C\,(\hat{n} - n_g)^2 - E_J \cos\hat{\varphi}"])
equation("eq_partition", [r"Z = \sum_{n} g_n\, e^{-E_n / k_B T}",
                          r"\langle E \rangle = -\frac{\partial \ln Z}{\partial \beta}"])

rng = np.random.default_rng(7)

# Resonator transmission: Lorentzian peak at 4.140 GHz, FWHM 4 MHz
f0, fwhm = 4.140, 0.004
f = np.linspace(4.120, 4.160, 400)
s = 1 / (1 + ((f - f0) / (fwhm / 2)) ** 2) + rng.normal(0, 0.015, f.size)
fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
ax.plot(f, s, ".", ms=2.5, color="#1f4e9c")
ax.set_xlabel("Frequency (GHz)"); ax.set_ylabel(r"$|S_{21}|^2$ (normalized)")
ax.set_title("Readout resonator transmission"); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "plot_resonance.png"); plt.close(fig)

# T1 decay: P_excited = exp(-t/T1), T1 = 30.4 us
T1 = 30.4
t = np.linspace(0, 150, 31)
p = np.exp(-t / T1) + rng.normal(0, 0.02, t.size)
fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
ax.plot(t, p, "o", ms=5, color="#b03a2e")
ax.set_xlabel(r"Delay time ($\mu$s)"); ax.set_ylabel("Excited-state population")
ax.set_title("Qubit energy relaxation"); ax.set_ylim(-0.05, 1.05); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "plot_t1.png"); plt.close(fig)

# Measurement table (Ohm's law)
volts = [1.10, 2.20, 3.35, 4.40, 5.50]
milliamps = [5.0, 10.1, 15.2, 20.0, 25.1]
fig, ax = plt.subplots(figsize=(5.5, 2.4), dpi=150); ax.axis("off")
tab = ax.table(cellText=[[str(i + 1), f"{v:.2f}", f"{c:.1f}"] for i, (v, c) in enumerate(zip(volts, milliamps))],
               colLabels=["Trial", "Voltage (V)", "Current (mA)"], loc="center", cellLoc="center")
tab.scale(1, 1.5); tab.set_fontsize(12)
fig.savefig(OUT / "table_ohm.png", bbox_inches="tight", facecolor="white"); plt.close(fig)
R = [v / (c / 1000) for v, c in zip(volts, milliamps)]
print("Table resistances (ohm):", [round(r, 1) for r in R], "mean:", round(sum(R) / len(R), 1))

# RC low-pass filter: R = 1 kOhm, C = 100 nF  ->  fc = 1/(2 pi R C)
import schemdraw
import schemdraw.elements as elm
with schemdraw.Drawing(file=str(OUT / "circuit_rc.png"), show=False, dpi=150) as d:
    d.config(fontsize=14)
    d += elm.SourceSin().up().label(r"$V_{in}$", loc="left")
    d += elm.Resistor().right().label(r"$R = 1\ \mathrm{k}\Omega$")
    d += elm.Dot()
    d.push()
    d += elm.Capacitor().down().label(r"$C = 100\ \mathrm{nF}$", loc="bottom")
    d += elm.Line().left()
    d.pop()
    d += elm.Line().right(1.5)
    d += elm.Dot(open=True).label(r"$V_{out}$", loc="right")
print("RC cutoff (Hz):", round(1 / (2 * np.pi * 1000 * 100e-9), 1))
