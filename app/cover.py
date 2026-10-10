"""Écran de repérage : une fenêtre noire plein écran, toujours au-dessus, posée sur le jeu pendant le repérage des
champions (la caméra y saute d'un champion à l'autre plusieurs fois par seconde, carte masquée : pénible à voir).
Le jeu continue de calculer ses images derrière (fenêtre sans bordure) ; la fenêtre ne prend pas le focus (le jeu
bride ses FPS quand il ne l'a pas) et ne s'affiche jamais pendant une capture.
La fenêtre tourne dans un processus à part (tkinter) piloté par des lignes de texte sur son entrée standard."""
import os, subprocess, sys, threading

_SCRIPT = r'''
import ctypes, sys, threading, tkinter as tk
u = ctypes.windll.user32
root = tk.Tk()
root.overrideredirect(True)
root.configure(bg="#0b0a09", cursor="none")
w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
root.geometry(f"{w}x{h}+0+0")
root.attributes("-topmost", True)
title = tk.Label(root, text="lolclip", fg="#e0a453", bg="#0b0a09", font=("Segoe UI Semibold", 30))
msg = tk.Label(root, text="", fg="#efe6da", bg="#0b0a09", font=("Segoe UI", 18))
sub = tk.Label(root, text="Ne touche pas au PC : la vidéo démarre juste après.", fg="#9c907f", bg="#0b0a09",
               font=("Segoe UI", 13))
title.place(relx=.5, rely=.44, anchor="center"); msg.place(relx=.5, rely=.5, anchor="center")
sub.place(relx=.5, rely=.55, anchor="center")
root.update_idletasks()
hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
GWL_EXSTYLE, NOACT, TOOL, TOP = -20, 0x08000000, 0x80, 0x8
u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | NOACT | TOOL | TOP)
def top():  # le jeu peut repasser au-dessus en reprenant le focus : on se remet devant, sans activer
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)
    root.after(400, top)
def read():
    for line in sys.stdin:
        line = line.rstrip("\n")
        if line == "quit":
            break
        root.after(0, lambda t=line: msg.config(text=t))
    root.after(0, root.destroy)
threading.Thread(target=read, daemon=True).start()
top()
root.mainloop()
'''

_proc = None
_lock = threading.Lock()


def show(text=""):
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return say(text)
        try:
            # le processus de la fenêtre lit son entrée en UTF-8 (sinon cp1252 : « RepÃ©rage »)
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            _proc = subprocess.Popen([sys.executable, "-c", _SCRIPT], stdin=subprocess.PIPE, text=True,
                                     encoding="utf-8", env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            _proc = None
            return
    say(text)
    from . import capture
    capture.focus_game()  # le jeu garde le focus (FPS non bridés), la fenêtre reste devant


def say(text):
    p = _proc
    if p and p.poll() is None:
        try:
            p.stdin.write(text.replace("\n", " ") + "\n")
            p.stdin.flush()
        except OSError:
            pass


def hide():
    global _proc
    with _lock:
        p, _proc = _proc, None
    if p and p.poll() is None:
        try:
            p.stdin.write("quit\n")
            p.stdin.flush()
            p.wait(timeout=3)
        except Exception:
            p.kill()


class shown:
    """with cover.shown("texte"): ... -> écran affiché pendant le bloc, retiré même en cas d'erreur."""

    def __init__(self, text=""):
        self.text = text

    def __enter__(self):
        show(self.text)
        return self

    def __exit__(self, *a):
        hide()
