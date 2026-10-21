import customtkinter as ctk
from tkinter import messagebox
import threading
import queue
import requests
import logging
import socketio
import json
import os
import platform
import pystray
import sys
from PIL import Image, ImageDraw

# ==========================================
# KONFIGURACJA WYGLĄDU I ŚCIEŻEK
# ==========================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Definicja czcionki Montserrat (z fallbackiem)
APP_FONT = ("Montserrat", 13)
APP_FONT_BOLD = ("Montserrat", 14, "bold")
APP_FONT_HEADER = ("Montserrat", 22, "bold")

def get_safe_config_path():
    if platform.system() == "Windows":
        base_dir = os.getenv('APPDATA', os.path.expanduser('~'))
    else:
        base_dir = os.getenv('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    app_dir = os.path.join(base_dir, "TipplyBridgeBySZABLIX")
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, "config.json")

CONFIG_FILE = get_safe_config_path()

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

class TipplyBridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tipply Bridge by SZABLIX")
        
        # Inteligentne wczytywanie ikony (szuka w ukrytych plikach .exe)
        try:
            if hasattr(sys, '_MEIPASS'):
                ikona_path = os.path.join(sys._MEIPASS, "logo.ico")
            else:
                ikona_path = "logo.ico"
            self.root.iconbitmap(ikona_path)
        except Exception:
            pass # Jeśli nie ma ikony, program po prostu użyje domyślnej
            
        self.root.geometry("650x450")
        self.root.resizable(False, False)

        # Obsługa inteligentnego zamykania
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

        self.config = {"se_jwt": "", "se_channel_id": "", "tipply_token": ""}
        self.is_running = False
        self.sio = None
        self.processed_tips = set()
        self.tray_icon = None

        # Izolowane i czyste logi
        self.log_queue = queue.Queue()
        self.logger = logging.getLogger("TipplyBridge")
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)

        logging.getLogger('socketio').setLevel(logging.ERROR)
        logging.getLogger('engineio').setLevel(logging.ERROR)

        queue_handler = QueueHandler(self.log_queue)
        queue_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(queue_handler)

        self.build_wizard_screen()
        self.build_main_screen()
        self.build_settings_screen()

        if self.load_config():
            self.show_main()
        else:
            self.show_wizard()

        self.root.after(100, self.poll_log_queue)

    def on_closing(self):
        """Inteligentna reakcja na przycisk X"""
        if self.is_running:
            self.logger.info("Aplikacja działa w tle...")
            self.hide_window()
        else:
            self.quit_app_entirely()

    def quit_app_entirely(self):
        if self.sio:
            self.sio.disconnect()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
        os._exit(0) # Gwarantuje ubicie wszystkich wątków

    # --- KONFIGURACJA I TOKENY ---
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.config.update(data)
                    if all(self.config.values()):
                        return True
            except Exception: pass
        return False

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f)

    def extract_tipply_token(self, raw_input):
        raw_input = raw_input.strip()
        if "tipply.pl" in raw_input or "/" in raw_input:
            return raw_input.rstrip('/').split('/')[-1]
        return raw_input

    # --- KREATOR I EKRANY ---
    def build_wizard_screen(self):
        self.wizard_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.wizard_step = 0

        self.wizard_title = ctk.CTkLabel(self.wizard_frame, text="Witaj w Tipply Bridge!", font=APP_FONT_HEADER)
        self.wizard_title.pack(pady=(30, 20))

        self.wizard_desc = ctk.CTkLabel(self.wizard_frame, text="", font=APP_FONT, justify="left", wraplength=550)
        self.wizard_desc.pack(fill="x", padx=40, pady=(0, 20))

        self.wizard_entry = ctk.CTkEntry(self.wizard_frame, width=500, height=40, font=APP_FONT)
        self.wizard_entry.pack(pady=(10, 30))

        btn_frame = ctk.CTkFrame(self.wizard_frame, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=30, padx=40)

        self.btn_wiz_back = ctk.CTkButton(btn_frame, text="← Wstecz", font=APP_FONT, command=self.wizard_go_back, state="disabled", width=120, fg_color="#333333")
        self.btn_wiz_back.pack(side="left")

        self.btn_wiz_next = ctk.CTkButton(btn_frame, text="Dalej →", font=APP_FONT_BOLD, command=self.wizard_go_next, width=150)
        self.btn_wiz_next.pack(side="right")

    def show_wizard(self):
        self.hide_all_frames()
        self.wizard_frame.pack(fill="both", expand=True)
        self.update_wizard_ui()

    def update_wizard_ui(self):
        self.wizard_entry.delete(0, 'end')
        self.wizard_entry.pack(pady=(10, 30))

        if self.wizard_step == 0:
            self.wizard_title.configure(text="Krok 1/3: StreamElements JWT Token")
            self.wizard_desc.configure(text="Zaloguj się na StreamElements, klknij awatar w prawym górnym rogu -> Channel settings\n\nSkopiuj 'JWT Token'.")
            self.wizard_entry.insert(0, self.config.get("se_jwt", ""))
            self.btn_wiz_back.configure(state="disabled")
            self.btn_wiz_next.configure(text="Dalej →")
        elif self.wizard_step == 1:
            self.wizard_title.configure(text="Krok 2/3: StreamElements Account ID")
            self.wizard_desc.configure(text="W tym samym miejscu, skopiuj 'Account ID'.")
            self.wizard_entry.insert(0, self.config.get("se_channel_id", ""))
            self.btn_wiz_back.configure(state="normal")
            self.btn_wiz_next.configure(text="Dalej →")
        elif self.wizard_step == 2:
            self.wizard_title.configure(text="Krok 3/3: Twój Widget Tipply")
            self.wizard_desc.configure(text="Zaloguj się na tipply.pl -> Konfigurator -> Komunikaty o nowej wiadomości. Skopiuj link do OBS i wklej go poniżej.")
            self.wizard_entry.insert(0, self.config.get("tipply_token", ""))
            self.btn_wiz_back.configure(state="normal")
            self.btn_wiz_next.configure(text="Zakończ konfigurację")
        elif self.wizard_step == 3:
            self.wizard_title.configure(text="Wszystko gotowe! 🎉")
            self.wizard_desc.configure(text="Aplikacja jest skonfigurowana.\n\nJeśli zamkniesz okno podczas pracy, schowa się ono do paska zadań.")
            self.wizard_entry.pack_forget()
            self.btn_wiz_next.configure(text="ZACZYNAMY! 🚀")

    def wizard_go_next(self):
        val = self.wizard_entry.get().strip()
        if self.wizard_step == 0:
            if not val: return
            self.config["se_jwt"] = val.replace("Bearer", "").strip()
        elif self.wizard_step == 1:
            if not val: return
            self.config["se_channel_id"] = val
        elif self.wizard_step == 2:
            if not val: return
            self.config["tipply_token"] = self.extract_tipply_token(val)

        if self.wizard_step < 3:
            self.wizard_step += 1
            self.update_wizard_ui()
        else:
            self.save_config()
            self.show_main()

    def wizard_go_back(self):
        if self.wizard_step > 0:
            self.wizard_step -= 1
            self.update_wizard_ui()

    def build_main_screen(self):
        self.main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(header_frame, text="Tipply Bridge by SZABLIX", font=APP_FONT_BOLD).pack(side="left")
        ctk.CTkButton(header_frame, text="⚙ Ustawienia", font=APP_FONT, width=100, command=self.show_settings).pack(side="right")

        content_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        left_frame = ctk.CTkFrame(content_frame, width=200, corner_radius=15)
        left_frame.pack(side="left", fill="y", padx=(0, 20))
        left_frame.pack_propagate(False)

        self.btn_toggle = ctk.CTkButton(left_frame, text="⏻ START", font=APP_FONT_HEADER, fg_color="#2ecc71", hover_color="#27ae60", height=60, command=self.toggle_connection)
        self.btn_toggle.pack(pady=(50, 20), padx=20)

        self.lbl_status = ctk.CTkLabel(left_frame, text="🔴 Rozłączono", font=APP_FONT_BOLD, text_color="#e74c3c")
        self.lbl_status.pack()

        right_frame = ctk.CTkFrame(content_frame, corner_radius=15)
        right_frame.pack(side="right", fill="both", expand=True)
        ctk.CTkLabel(right_frame, text="Dziennik Zdarzeń", font=APP_FONT_BOLD).pack(pady=(10, 0))

        self.log_text = ctk.CTkTextbox(right_frame, state="disabled", fg_color="#1e1e1e", text_color="#2ecc71", font=("Consolas", 12))
        self.log_text.pack(fill="both", expand=True, padx=15, pady=15)

    def build_settings_screen(self):
        self.settings_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        ctk.CTkButton(self.settings_frame, text="← Wróć", font=APP_FONT, width=100, fg_color="#333333", command=self.show_main).pack(anchor="nw", padx=20, pady=20)

        form_frame = ctk.CTkFrame(self.settings_frame, corner_radius=15)
        form_frame.pack(fill="both", expand=True, padx=40, pady=(0, 40))

        ctk.CTkLabel(form_frame, text="SE JWT Token:", font=APP_FONT_BOLD).pack(anchor="w", padx=20, pady=(20, 5))
        self.entry_jwt = ctk.CTkEntry(form_frame, width=500, font=APP_FONT)
        self.entry_jwt.pack(padx=20)

        ctk.CTkLabel(form_frame, text="SE Account ID:", font=APP_FONT_BOLD).pack(anchor="w", padx=20, pady=(15, 5))
        self.entry_channel = ctk.CTkEntry(form_frame, width=500, font=APP_FONT)
        self.entry_channel.pack(padx=20)

        ctk.CTkLabel(form_frame, text="Tipply Widget Link:", font=APP_FONT_BOLD).pack(anchor="w", padx=20, pady=(15, 5))
        self.entry_tipply = ctk.CTkEntry(form_frame, width=500, font=APP_FONT)
        self.entry_tipply.pack(padx=20, pady=(0, 30))

        ctk.CTkButton(form_frame, text="ZAPISZ ZMIANY", font=APP_FONT_BOLD, command=self.save_settings).pack(pady=20)

    def hide_all_frames(self):
        for f in [self.main_frame, self.settings_frame, self.wizard_frame]:
            f.pack_forget()

    def show_main(self):
        self.hide_all_frames()
        self.main_frame.pack(fill="both", expand=True)

    def show_settings(self):
        if self.is_running: return
        self.entry_jwt.delete(0, 'end'); self.entry_jwt.insert(0, self.config["se_jwt"])
        self.entry_channel.delete(0, 'end'); self.entry_channel.insert(0, self.config["se_channel_id"])
        self.entry_tipply.delete(0, 'end'); self.entry_tipply.insert(0, self.config["tipply_token"])
        self.hide_all_frames()
        self.settings_frame.pack(fill="both", expand=True)

    def save_settings(self):
        self.config["se_jwt"] = self.entry_jwt.get().replace("Bearer", "").strip()
        self.config["se_channel_id"] = self.entry_channel.get().strip()
        self.config["tipply_token"] = self.extract_tipply_token(self.entry_tipply.get())
        self.save_config()
        self.show_main()

    def poll_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self.poll_log_queue)

    def update_status(self, connected):
        if connected:
            self.lbl_status.configure(text="🟢 Połączono", text_color="#2ecc71")
            self.btn_toggle.configure(text="⏼ STOP", fg_color="#e74c3c", hover_color="#c0392b")
            self.is_running = True
        else:
            self.lbl_status.configure(text="🔴 Rozłączono", text_color="#e74c3c")
            self.btn_toggle.configure(text="⏻ START", fg_color="#2ecc71", hover_color="#27ae60")
            self.is_running = False

    def toggle_connection(self):
        if not self.is_running:
            self.logger.info("Uruchamianie mostu...")
            threading.Thread(target=self.run_bridge, daemon=True).start()
        else:
            self.logger.info("Zatrzymywanie...")
            if self.sio: self.sio.disconnect()
            self.update_status(False)

    def run_bridge(self):
        self.update_status(True)
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        namespace = f"/{self.config['tipply_token']}"

        @self.sio.event(namespace=namespace)
        def connect(): self.logger.info("🌐 Połączono z Tipply!")

        @self.sio.on('alert', namespace=namespace)
        def on_alert(data):
            try:
                tip_id = data.get('payment_id') or data.get('id')
                nick = data.get('nickname', 'Nieznany')
                amount_pln = float(data.get('amount', 0)) / 100.0
                if tip_id in self.processed_tips: return
                if amount_pln > 0:
                    self.logger.info(f"🔔 DONEJT: {nick} - {amount_pln} PLN")
                    self.send_to_streamelements(nick, amount_pln, data.get('message', ''), data.get('email', ''), tip_id)
                    self.processed_tips.add(tip_id)
            except Exception: pass

        try:
            self.sio.connect("https://alert-ws.tipply.pl", namespaces=[namespace], transports=['websocket', 'polling'])
            self.sio.wait()
        except Exception:
            self.logger.error("🔥 Błąd sieci.")
            self.root.after(0, lambda: self.update_status(False))

    def send_to_streamelements(self, user, amount, message, tip_email, payment_id):
        url = f"https://api.streamelements.com/kappa/v2/tips/{self.config['se_channel_id']}"
        headers = {"Authorization": f"Bearer {self.config['se_jwt']}", "Content-Type": "application/json"}
        data = {
            "user": {"username": user, "email": tip_email},
            "provider": "tipply_bridge_by_SZABLIX",
            "amount": amount, "currency": "PLN", "message": message or "",
            "imported": True, "transactionId": payment_id
        }
        try:
            r = requests.post(url, json=data, headers=headers)
            if r.status_code == 200:
                self.logger.info(f"✅ Przekazano do SE ({amount} PLN)")
            elif r.status_code == 400:
                msg = r.json().get("message")
                self.logger.error(f"🔥 Nieprawidłowe żądanie: {msg}")
            else:
                err = r.json().get("error")
                msg = r.json().get("message")
                self.logger.error(f"🔥 Żądanie zwróciło błąd: {r.status_code}")
                self.logger.error(f"🔥 {err}: {msg}")
        except Exception: pass

    # --- SYSTEM TRAY ---
    def create_image(self):
        # Magiczna ścieżka dla PyInstallera (żeby exe widział plik wewnątrz siebie)
        icon_path = "logo.ico"
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, "logo.ico")

        try:
            # Próba załadowania Twojego logo
            return Image.open(icon_path)
        except Exception:
            # Fallback - jeśli nie znajdzie pliku logo.ico, rysuje stare "TB"
            self.logger.warning("Nie znaleziono logo.ico, ładuję ikonę domyślną.")
            image = Image.new('RGB', (64, 64), color=(30, 30, 30))
            draw = ImageDraw.Draw(image)
            draw.text((16, 24), "TB", fill=(46, 204, 113))
            return image

    def hide_window(self):
        self.root.withdraw()
        menu = pystray.Menu(
            pystray.MenuItem('Pokaż okno', self.show_window_from_tray, default=True),
            pystray.MenuItem('Zakończ program', self.quit_app_entirely)
        )
        self.tray_icon = pystray.Icon("TipplyBridge", self.create_image(), "Tipply Bridge", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window_from_tray(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.deiconify)

    def quit_window(self, icon, item):
        self.quit_app_entirely()

if __name__ == "__main__":
    try:
        root = ctk.CTk()
        app = TipplyBridgeApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        # Ten blok uruchomi się, gdy wciśniesz Ctrl+C w terminalu
        print("\n[!] Wykryto Ctrl+C. Trwa bezpieczne zamykanie mostu...")
        if 'app' in locals():
            app.quit_app_entirely()
    except Exception as e:
        print(f"\n[X] Wystąpił błąd krytyczny: {e}")
