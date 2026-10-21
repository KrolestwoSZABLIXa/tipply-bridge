"""
Moduł automatycznej autoryzacji i komunikacji z Tipply Proxy API.
Obsługuje:
- Odczyt ciasteczek sesyjnych 'auth_token' z przeglądarek (Firefox, Chrome, Edge, Brave, Opera)
- Weryfikację sesji i pobieranie danych konta użytkownika oraz awatara
- Pobieranie prawdziwych adresów e-mail widzów dla przychodzących donejtów (z priorytetem i ponawianiem)
"""

import os
import glob
import json
import base64
import sqlite3
import shutil
import tempfile
import ctypes
from ctypes import wintypes
import time
import requests

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte))
    ]

def _dpapi_decrypt(encrypted_bytes):
    try:
        blob_in = DATA_BLOB(
            len(encrypted_bytes),
            ctypes.cast(ctypes.create_string_buffer(encrypted_bytes), ctypes.POINTER(ctypes.c_byte))
        )
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            length = blob_out.cbData
            res = ctypes.string_at(blob_out.pbData, length)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return res
    except Exception:
        pass
    return None

def _get_chromium_master_key(local_state_path):
    if not os.path.exists(local_state_path):
        return None
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
        if encrypted_key.startswith(b"DPAPI"):
            return _dpapi_decrypt(encrypted_key[5:])
    except Exception:
        pass
    return None

def _decrypt_chromium_cookie(key, encrypted_value):
    try:
        if encrypted_value.startswith(b'v10') or encrypted_value.startswith(b'v11'):
            nonce = encrypted_value[3:15]
            ciphertext_and_tag = encrypted_value[15:]
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                aesgcm = AESGCM(key)
                return aesgcm.decrypt(nonce, ciphertext_and_tag, None).decode('utf-8')
            except ImportError:
                pass
    except Exception:
        pass
    return None

def _extract_from_firefox():
    pattern = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles\*\cookies.sqlite")
    for db_path in glob.glob(pattern):
        tmp = tempfile.mktemp(suffix=".sqlite")
        try:
            shutil.copyfile(db_path, tmp)
            con = sqlite3.connect(tmp)
            cur = con.cursor()
            cur.execute(
                "SELECT value FROM moz_cookies WHERE host LIKE ? AND name = ? ORDER BY lastAccessed DESC",
                ('%tipply%', 'auth_token')
            )
            row = cur.fetchone()
            con.close()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
    return None

def _extract_from_chromium_browser(user_data_path):
    local_state = os.path.join(user_data_path, "Local State")
    master_key = _get_chromium_master_key(local_state)
    if not master_key:
        return None

    cookie_candidates = glob.glob(os.path.join(user_data_path, "*", "Network", "Cookies")) + \
                        glob.glob(os.path.join(user_data_path, "*", "Cookies"))

    for db_path in cookie_candidates:
        tmp = tempfile.mktemp(suffix=".sqlite")
        try:
            shutil.copyfile(db_path, tmp)
            con = sqlite3.connect(tmp)
            cur = con.cursor()
            cur.execute(
                "SELECT encrypted_value FROM cookies WHERE host_key LIKE ? AND name = ? ORDER BY last_access_utc DESC",
                ('%tipply%', 'auth_token')
            )
            rows = cur.fetchall()
            con.close()
            for (encrypted_val,) in rows:
                decrypted = _decrypt_chromium_cookie(master_key, encrypted_val)
                if decrypted:
                    return decrypted.strip()
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
    return None

def find_tipply_auth_token_in_browsers():
    """
    Automatycznie przeszukuje popularne przeglądarki i zwraca znaleziony auth_token Tipply.
    """
    token = _extract_from_firefox()
    if token:
        return token

    chromium_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
        os.path.expandvars(r"%APPDATA%\Opera Software\Opera Stable"),
        os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\User Data"),
    ]

    for c_path in chromium_paths:
        if os.path.exists(c_path):
            token = _extract_from_chromium_browser(c_path)
            if token:
                return token

    return None

def verify_tipply_session(auth_token):
    """
    Sprawdza ważność sesji na proxy.tipply.pl/user.
    Zwraca krotkę: (sukces: bool, dane_lub_komunikat: dict | str)
    """
    if not auth_token:
        return False, "Brak tokenu autoryzacji"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0)",
        "Accept": "application/json",
        "Referer": "https://app.tipply.pl/",
        "Cookie": f"auth_token={auth_token.strip()}"
    }

    try:
        r = requests.get("https://proxy.tipply.pl/user", headers=headers, timeout=5)
        if r.status_code == 200:
            return True, r.json()
        elif r.status_code == 401:
            return False, "Sesja wygasła (zaloguj się ponownie w przeglądarce)"
        else:
            return False, f"Błąd serwera Tipply: {r.status_code}"
    except Exception as e:
        return False, f"Błąd połączenia: {str(e)}"

def get_tipply_user_profile(auth_token):
    """
    Pobiera pełne dane profilu użytkownika (w tym google_avatar_url i avatar).
    Zwraca dict z danymi lub None.
    """
    if not auth_token:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0)",
        "Accept": "application/json",
        "Referer": "https://app.tipply.pl/",
        "Cookie": f"auth_token={auth_token.strip()}"
    }

    profile_data = {}
    try:
        # 1. Pobierz profil (avatar, google_avatar_url)
        r_prof = requests.get("https://proxy.tipply.pl/user/profile", headers=headers, timeout=4)
        if r_prof.status_code == 200:
            prof = r_prof.json()
            profile_data["google_avatar_url"] = prof.get("google_avatar_url")
            profile_data["avatar"] = prof.get("avatar")

        # 2. Pobierz konto (username, id)
        r_user = requests.get("https://proxy.tipply.pl/user", headers=headers, timeout=4)
        if r_user.status_code == 200:
            user = r_user.json()
            profile_data["username"] = user.get("username")
            profile_data["id"] = user.get("id")

        return profile_data if profile_data else None
    except Exception:
        return None

import unicodedata

def _normalize_nick(nick):
    if not nick:
        return ""
    nfd = unicodedata.normalize('NFKD', str(nick).strip().lower())
    return "".join(c for c in nfd if not unicodedata.combining(c))

def fetch_real_email_from_tipply(auth_token, tip_id=None, payment_id=None, nickname=None, amount_pln=None, max_attempts=5):
    """
    Pobiera prawdziwy adres e-mail widza z API Tipply z najwyższym priorytetem i próbami ponawiania (retry loop),
    aby wyeliminować opóźnienie zapisu bazy danych Tipply względem alertu WebSocket.
    """
    if not auth_token:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0)",
        "Accept": "application/json",
        "Referer": "https://app.tipply.pl/",
        "Cookie": f"auth_token={auth_token.strip()}"
    }

    delays = [0, 0.7, 1.2, 1.8, 2.5]
    norm_target_nick = _normalize_nick(nickname)

    for attempt in range(min(max_attempts, len(delays))):
        delay = delays[attempt]
        if delay > 0:
            time.sleep(delay)

        try:
            r = requests.get("https://proxy.tipply.pl/user/tips?limit=15", headers=headers, timeout=4)
            if r.status_code == 200:
                tips = r.json()
                for t in tips:
                    t_id = str(t.get("id", "")).strip()
                    t_payment_id = str(t.get("payment_id", "")).strip()
                    t_nick = _normalize_nick(t.get("nickname", ""))
                    t_amount = float(t.get("amount", 0)) / 100.0

                    is_match = False

                    # 1. Dopasowanie po identyfikatorze transakcji (id lub payment_id)
                    check_ids = [str(x).strip() for x in (tip_id, payment_id) if x]
                    for cid in check_ids:
                        if cid and (cid == t_id or cid == t_payment_id or cid in t_id or cid in t_payment_id):
                            is_match = True
                            break

                    # 2. Dopasowanie po nicku i kwocie
                    if not is_match and norm_target_nick:
                        if norm_target_nick == t_nick:
                            if amount_pln is None or abs(t_amount - float(amount_pln)) < 0.05:
                                is_match = True

                    if is_match:
                        email = t.get("email")
                        if email and isinstance(email, str) and "@" in email:
                            return email.strip()

            elif r.status_code == 401:
                # Token sesji wygasł w trakcie pracy
                break
        except Exception:
            pass

    return None

