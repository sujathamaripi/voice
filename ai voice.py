# project.py
from tkinter import *
import math
import re
import threading
import time
import queue
import subprocess
import sys
import platform

# speech modules (pyttsx3 only used on non-Windows fallback)
try:
    import pyttsx3
except Exception:
    pyttsx3 = None

import speech_recognition as sr

# ---------- Text-to-Speech - reliable queued worker ----------
_tts_queue = queue.Queue()

def _speak_with_sapi_windows(text):
    """
    Use PowerShell + System.Speech (SAPI) to speak text on Windows.
    This runs synchronously and returns when speaking finished.
    """
    if not text:
        return
    # escape single quotes by doubling them for PowerShell literal string
    esc = str(text).replace("'", "''")
    ps_cmd = f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('{esc}')"
    # Call powershell -Command "<cmd>"
    # Use subprocess.run so worker waits until spoken
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print("SAPI speak error:", e)

def _speak_with_pyttsx3_one_shot(text):
    """Create a fresh pyttsx3 engine and speak. Used as fallback on non-Windows systems."""
    if not pyttsx3:
        print("pyttsx3 not available for TTS.")
        return
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
        engine.setProperty("volume", 1.0)
        engine.say(str(text))
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:
            pass
    except Exception as e:
        print("pyttsx3 one-shot error:", e)

def _tts_worker():
    """
    Single background worker. Consumes texts from the queue and speaks them.
    For Windows: uses PowerShell/SAPI (reliable).
    Otherwise: uses pyttsx3 one-shot engine.
    """
    is_windows = (platform.system().lower().startswith("win"))
    while True:
        item = _tts_queue.get()
        if item is None:
            # sentinel to stop
            try:
                _tts_queue.task_done()
            except Exception:
                pass
            break
        text = str(item)
        try:
            if is_windows:
                _speak_with_sapi_windows(text)
            else:
                _speak_with_pyttsx3_one_shot(text)
        except Exception as e:
            print("TTS worker error:", e)
        finally:
            try:
                _tts_queue.task_done()
            except Exception:
                pass
        # small sleep to yield CPU
        time.sleep(0.01)

# start worker
_tts_thread = threading.Thread(target=_tts_worker, daemon=True)
_tts_thread.start()

def speak(text):
    """Queue a phrase to be spoken (non-blocking)."""
    if not text:
        return
    try:
        _tts_queue.put_nowait(str(text))
    except Exception as e:
        print("TTS queue put failed, fallback:", e)
        # fallback: try speaking directly (best-effort)
        if platform.system().lower().startswith("win"):
            _speak_with_sapi_windows(text)
        else:
            _speak_with_pyttsx3_one_shot(text)


# ---------- UI ----------
root = Tk()
entry = Entry(root, font=('arial', 20, 'bold'), bg="dodgerblue1", fg='white', bd=10,
              justify="right", relief=SUNKEN, width=30)

# ---------- Number word maps ----------
_UNITS = {"zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9}
_TEENS = {"ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
          "seventeen":17,"eighteen":18,"nineteen":19}
_TENS = {"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}
_MAG = {"hundred":100,"thousand":1000,"million":1000000}
_NUMBER_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_MAG)

# ---------- Helpers ----------
def words_to_number(tokens):
    total = 0
    current = 0
    for w in tokens:
        w = w.lower()
        if re.fullmatch(r"-?\d+(\.\d+)?", w):
            current += float(w)
        elif w in _UNITS:
            current += _UNITS[w]
        elif w in _TEENS:
            current += _TEENS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in _MAG:
            if current == 0:
                current = 1
            current *= _MAG[w]
            total += current
            current = 0
    total += current
    return float(total)

def consume_number(tokens, start):
    n = len(tokens)
    if start >= n:
        return None, start
    if re.fullmatch(r"-?\d+(\.\d+)?", tokens[start]):
        return tokens[start], start + 1
    j = start
    collect = []
    while j < n and tokens[j].lower() in _NUMBER_WORDS:
        collect.append(tokens[j])
        j += 1
    if not collect:
        return None, start
    val = words_to_number(collect)
    if float(val).is_integer():
        return str(int(val)), j
    return str(val), j

def safe_eval(expr):
    try:
        res = eval(expr, {"__builtins__": None}, {"math": math, "round": round, "int": int})
        if isinstance(res, float):
            res = round(res, 10)
            if abs(res - round(res)) < 1e-10:
                res = int(round(res))
        return res, None
    except Exception as e:
        return None, e

# ---------- Main parser & evaluator ----------
def calculate_voice(text, entry_widget):
    if not text:
        entry_widget.delete(0, END)
        entry_widget.insert(0, "No input")
        speak("No input")
        return

    txt = text.lower().strip()

    # Normalize common phrases (improved)
    txt = txt.replace("multiplied by", "times")
    txt = txt.replace("multiplied", "times")
    txt = txt.replace("multiply by", "times")
    txt = txt.replace("multiply", "times")
    txt = txt.replace("into", "times")           # common spoken form
    txt = txt.replace("divided by", "divide")
    txt = txt.replace("to the power of", "power")
    txt = txt.replace("raised to", "power")
    txt = txt.replace("square root of", "sqrt of")
    txt = txt.replace("cube root of", "cbrt of")
    txt = txt.replace("factorial of", "factorial of")
    txt = txt.replace("log of", "log of")
    txt = txt.replace("ln of", "ln of")
    txt = re.sub(r"\bsine\b", "sin", txt)
    txt = re.sub(r"\bcosine\b", "cos", txt)
    txt = re.sub(r"\btangent\b", "tan", txt)

    tokens = [t for t in re.split(r"\s+", txt) if t != ""]

    if not tokens:
        entry_widget.delete(0, END)
        entry_widget.insert(0, "No input")
        speak("No input")
        return

    expr_parts = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok in ("+", "-", "*", "/", "**", "(", ")", "%", "."):
            expr_parts.append(tok); i += 1; continue

        if tok in ("plus", "add"):
            expr_parts.append("+"); i += 1; continue
        if tok in ("minus", "subtract"):
            expr_parts.append("-"); i += 1; continue
        if tok in ("times", "multiply", "x"):
            expr_parts.append("*"); i += 1; continue
        if tok in ("divide", "over"):
            expr_parts.append("/"); i += 1; continue
        if tok in ("power",):
            expr_parts.append("**"); i += 1; continue

        if tok in ("sin", "cos", "tan"):
            j = i + 1
            if j < len(tokens) and tokens[j] == "of":
                j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            if tok == "sin":
                expr_parts.append(f"round(math.sin(math.radians({num_str})),10)")
            elif tok == "cos":
                expr_parts.append(f"round(math.cos(math.radians({num_str})),10)")
            else:
                expr_parts.append(f"round(math.tan(math.radians({num_str})),10)")
            i = next_i
            continue

        if tok == "sqrt":
            j = i + 1
            if j < len(tokens) and tokens[j] == "of":
                j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"math.sqrt({num_str})")
            i = next_i
            continue

        if tok in ("cbrt", "cuberoot", "cube-root"):
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"round(({num_str})**(1/3),10)")
            i = next_i
            continue

        if tok == "cube" and i+1 < len(tokens) and tokens[i+1] == "root":
            j = i + 2
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 2
                continue
            expr_parts.append(f"round(({num_str})**(1/3),10)")
            i = next_i
            continue

        if tok == "square":
            if i+1 < len(tokens) and tokens[i+1] == "root":
                i += 1
                continue
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"({num_str})**2")
            i = next_i
            continue

        if tok == "cube":
            if i+1 < len(tokens) and tokens[i+1] == "root":
                i += 1
                continue
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"({num_str})**3")
            i = next_i
            continue

        if tok in ("factorial", "fact", "!"):
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                if expr_parts and expr_parts[-1].replace('.','').replace('-','').isdigit():
                    last_num = expr_parts.pop()
                    expr_parts.append(f"math.factorial(int({last_num}))")
                    i += 1
                    continue
                i += 1
                continue
            expr_parts.append(f"math.factorial(int({num_str}))")
            i = next_i
            continue

        if tok == "log":
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"math.log10({num_str})")
            i = next_i
            continue

        if tok == "ln":
            j = i + 1
            if j < len(tokens) and tokens[j] == "of": j += 1
            num_str, next_i = consume_number(tokens, j)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(f"math.log({num_str})")
            i = next_i
            continue

        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            expr_parts.append(tok)
            i += 1
            continue

        if tok.lower() in _NUMBER_WORDS:
            num_str, next_i = consume_number(tokens, i)
            if num_str is None:
                i += 1
                continue
            expr_parts.append(num_str)
            i = next_i
            continue

        if tok in ("pi", "π"):
            expr_parts.append("math.pi"); i += 1; continue
        if tok == "e":
            expr_parts.append("math.e"); i += 1; continue

        i += 1

    expr = " ".join(expr_parts).strip()
    print(f"Input: {text}")
    print(f"Generated expression: {expr}")

    if not expr:
        entry_widget.delete(0, END)
        entry_widget.insert(0, "Could not calculate")
        speak("Could not calculate")
        return

    result, err = safe_eval(expr)
    if err:
        print("Eval error:", err, "expr:", expr)
        entry_widget.delete(0, END)
        entry_widget.insert(0, "Error")
        speak("Error")
        return

    entry_widget.delete(0, END)
    entry_widget.insert(0, str(result))
    speak(f"Result is {result}")


# ---------- Audio thread (recognition) ----------
def _recognize_worker(entry_widget, mic_btn):
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, "Listening..."), mic_btn.config(state="disabled")))
            r.adjust_for_ambient_noise(source, duration=0.6)
            audio_data = r.listen(source, timeout=6, phrase_time_limit=8)
            text = r.recognize_google(audio_data)
            print("Google recognized:", text)
            root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, text)))
            root.after(0, lambda: calculate_voice(text, entry_widget))
    except sr.WaitTimeoutError:
        root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, "No speech detected")))
        speak("No speech detected")
    except sr.UnknownValueError:
        root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, "Could not understand")))
        speak("Could not understand")
    except sr.RequestError as e:
        print("SR RequestError:", e)
        root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, "Service error")))
        speak("Service error")
    except Exception as e:
        print("Audio worker error:", e)
        root.after(0, lambda: (entry_widget.delete(0, END), entry_widget.insert(0, "Error")))
        speak("Error")
    finally:
        root.after(0, lambda: mic_btn.config(state="normal"))

def audio(entry_widget, mic_btn):
    t = threading.Thread(target=_recognize_worker, args=(entry_widget, mic_btn), daemon=True)
    t.start()

# ---------- Button click ----------
def click(entry_widget, value):
    try:
        ex = entry_widget.get()
        if value == 'C':
            if ex:
                entry_widget.delete(len(ex)-1, END)
            return
        if value == 'CE':
            entry_widget.delete(0, END)
            return
        if value == '=':
            try:
                res = eval(entry_widget.get(), {"__builtins__": None}, {"math": math, "round": round})
                if isinstance(res, float) and abs(res - round(res)) < 1e-10:
                    res = int(round(res))
                entry_widget.delete(0, END)
                entry_widget.insert(0, str(res))
                speak(f"Result is {res}")
            except Exception:
                entry_widget.delete(0, END)
                entry_widget.insert(0, "Error")
                speak("Error")
            return
        entry_widget.insert(END, value)
    except Exception:
        entry_widget.delete(0, END)
        entry_widget.insert(0, "Error")
        speak("Error")

# ---------- GUI ----------
def main():
    root.title("VOICE CALCULATOR")
    root.config(bg="dodgerblue1")
    for c in range(8):
        root.grid_columnconfigure(c, weight=1, minsize=64)

    entry.grid(row=0, column=0, columnspan=6, padx=6, pady=8, sticky="ew")

    try:
        logo_img = PhotoImage(file="Calculator_icon.png")
        logo_lbl = Label(root, image=logo_img, bg="dodgerblue2")
        logo_lbl.image = logo_img
    except Exception:
        logo_lbl = Label(root, text="CALCULATOR", bg="dodgerblue2", fg="white",
                         font=('arial', 14, 'bold'), width=12, height=2)
    logo_lbl.grid(row=0, column=6, sticky="w", padx=(6, 0))

    try:
        mic_img = PhotoImage(file="microphone.png")
        mic_btn = Button(root, image=mic_img, bd=0, bg="dodgerblue1", activebackground="dodgerblue1",
                         command=lambda: audio(entry, mic_btn))
        mic_btn.image = mic_img
    except Exception:
        mic_btn = Button(root, text="🎤", bd=0, bg="dodgerblue1", fg='white',
                         activebackground="dodgerblue1", command=lambda: audio(entry, mic_btn),
                         font=('arial', 14))
    mic_btn.grid(row=0, column=7, padx=6, pady=8, sticky="e")

    button_text_list = ['C', 'CE', '√', '+', 'π', 'cosθ', 'tanθ', 'sinθ',
                        '1', '2', '3', '-', '2π', 'cosh', 'tanh', 'sinh',
                        '4', '5', '6', '*', chr(8731), 'x\u02b8', 'x\u00B3', 'x\u00B2',
                        '7', '8', '9', chr(247), 'ln', 'deg', 'rad', 'e',
                        '0', '.', '%', '=', 'log₁₀', '(', ')', 'x!']

    rv, cv = 1, 0
    for txt in button_text_list:
        btn = Button(root, width=5, height=2, bd=2, relief=SUNKEN, text=txt,
                     command=lambda t=txt: click(entry, t),
                     bg="dodgerblue1", fg='white', font=('arial', 18, 'bold'),
                     activebackground="dodgerblue2")
        btn.grid(row=rv, column=cv, padx=2, pady=2, sticky="nsew")
        cv += 1
        if cv > 7:
            cv = 0
            rv += 1

    root.geometry("720x520+100+100")
    root.minsize(640, 480)
    root.mainloop()

if __name__ == "__main__":
    main()