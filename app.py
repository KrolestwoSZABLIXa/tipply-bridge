import customtkinter as ctk
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
import time
import re
import ctypes
import io
import webbrowser
from PIL import Image, ImageDraw, ImageOps

import tipply_auth

# ==========================================
# CZCIONKI I WYGLĄD (DM SANS & MODERN DARK)
# ==========================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

def init_custom_fonts():
    """Dynamiczne ładowanie czcionki DM Sans w systemie Windows"""
    candidates = [
        os.path.join(getattr(sys, '_MEIPASS', ''), "assets", "DMSans.ttf"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "DMSans.ttf"),
        "assets/DMSans.ttf"
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                ctypes.windll.gdi32.AddFontResourceExW(os.path.abspath(path), 0x10, 0)
                return "DM Sans"
            except Exception:
                pass
    return "Segoe UI"

FONT_NAME = init_custom_fonts()
# Zwiększone, czytelne czcionki (DM Sans)
APP_FONT = (FONT_NAME, 14.5)
APP_FONT_BOLD = (FONT_NAME, 14.5, "bold")
APP_FONT_HEADER = (FONT_NAME, 22, "bold")
APP_FONT_TITLE = (FONT_NAME, 16, "bold")
APP_FONT_MAIN_HEADER = (FONT_NAME, 19, "bold")
APP_FONT_SMALL = (FONT_NAME, 12.5)
APP_FONT_LOG = (FONT_NAME, 14)

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
        
        # Inteligentne wczytywanie ikony
        try:
            if hasattr(sys, '_MEIPASS'):
                ikona_path = os.path.join(sys._MEIPASS, "logo.ico")
            else:
                ikona_path = "logo.ico"
            self.root.iconbitmap(ikona_path)
        except Exception:
            pass

        self.root.geometry("760x520")
        self.root.resizable(False, False)
        self.root.configure(fg_color="#12141a")

        # Obsługa inteligentnego zamykania
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

        self.config = {
            "se_jwt": "",
            "se_channel_id": "",
            "tipply_token": "",
            "tipply_auth_token": "",
            "tipply_username": ""
        }
        self.is_running = False
        self.sio = None
        self.processed_tips = set()
        self.tray_icon = None
        self.avatar_img = None
        self.github_icon = self.load_github_icon()

        # Izolowane logi
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
            self.update_user_profile_display()
            self.show_main()
        else:
            self.show_wizard()

        self.root.after(100, self.poll_log_queue)

    def load_github_icon(self):
        candidates = [
            os.path.join(getattr(sys, '_MEIPASS', ''), "assets", "github.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "github.png"),
            "assets/github.png"
        ]
        for p in candidates:
            if p and os.path.exists(p):
                try:
                    img = Image.open(p)
                    return ctk.CTkImage(img, size=(16, 16))
                except Exception:
                    pass
        return None

    def open_github_repo(self):
        try:
            webbrowser.open_new_tab("https://github.com/KrolestwoSZABLIXa/tipply-bridge")
        except Exception:
            pass

    def download_and_crop_avatar(self, username="Gość", avatar_url=None, size=(38, 38)):
        """Pobiera i przycina okrągły awatar jako PIL Image (wątkobezpieczne)"""
        cache_path = os.path.join(os.path.dirname(CONFIG_FILE), "avatar.png")
        if avatar_url:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                r = requests.get(avatar_url, headers=headers, timeout=5)
                if r.status_code == 200:
                    raw_img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                    scale = 4
                    mask = Image.new('L', (size[0] * scale, size[1] * scale), 0)
                    draw = ImageDraw.Draw(mask)
                    draw.ellipse((0, 0, mask.size[0] - 1, mask.size[1] - 1), fill=255)
                    fitted = ImageOps.fit(raw_img, mask.size, centering=(0.5, 0.5))
                    fitted.putalpha(mask)
                    output = fitted.resize(size, Image.Resampling.LANCZOS)
                    output.save(cache_path)
                    return output
            except Exception:
                pass

        if os.path.exists(cache_path) and username != "Gość":
            try:
                return Image.open(cache_path)
            except Exception:
                pass

        # Generowany inicjał z antyaliasingiem
        scale = 4
        img = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bg_color = (99, 102, 241) if username and username != "Gość" else (51, 65, 85)
        draw.ellipse((0, 0, img.size[0] - 1, img.size[1] - 1), fill=bg_color)
        initial = (username[0].upper() if username else "G")
        draw.text((img.size[0] // 2, img.size[1] // 2), initial, fill=(255, 255, 255), anchor="mm")
        return img.resize(size, Image.Resampling.LANCZOS)

    def get_avatar_image(self, username="Gość", avatar_url=None, size=(38, 38)):
        pil_img = self.download_and_crop_avatar(username, avatar_url, size)
        return ctk.CTkImage(pil_img, size=size)

    def on_closing(self):
        """Reakcja na przycisk X – minimalizacja do traya podczas pracy"""
        if self.is_running:
            self.logger.info("Aplikacja działa w tle...")
            self.hide_window()
        else:
            self.quit_app_entirely()

    def quit_app_entirely(self):
        self.is_running = False
        if self.sio:
            try: self.sio.disconnect()
            except Exception: pass
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
        self.root.destroy()
        os._exit(0)

    # --- KONFIGURACJA ---
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config.update(data)
                    if (self.config.get("se_jwt") and
                        self.config.get("se_channel_id") and
                        self.config.get("tipply_token")):
                        return True
            except Exception as e:
                self.logger.warning(f"Błąd odczytu konfiguracji: {e}")
        return False

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            self.logger.error(f"Błąd zapisu konfiguracji: {e}")

    def extract_tipply_token(self, raw_input):
        raw_input = raw_input.strip()
        if "tipply.pl" in raw_input or "/" in raw_input:
            return raw_input.rstrip('/').split('/')[-1]
        return raw_input

    # --- KREATOR I EKRANY ---
    def build_wizard_screen(self):
        self.wizard_frame = ctk.CTkFrame(self.root, fg_color="#12141a")
        self.wizard_step = 0

        self.wizard_title = ctk.CTkLabel(self.wizard_frame, text="Witaj w Tipply Bridge!", font=APP_FONT_HEADER, text_color="#f8fafc")
        self.wizard_title.pack(pady=(25, 12))

        self.wizard_desc = ctk.CTkLabel(self.wizard_frame, text="", font=APP_FONT, justify="center", wraplength=580, text_color="#94a3b8")
        self.wizard_desc.pack(fill="x", padx=40, pady=(0, 15))

        self.wizard_content_frame = ctk.CTkFrame(self.wizard_frame, fg_color="transparent")
        self.wizard_content_frame.pack(fill="both", expand=True, padx=40)

        self.wizard_entry = ctk.CTkEntry(
            self.wizard_content_frame, width=520, height=44, font=APP_FONT,
            fg_color="#1a1d24", border_color="#2b303c", border_width=1
        )

        # Kontrolki kroku 3 (Tipply)
        self.wizard_auto_btn = ctk.CTkButton(
            self.wizard_content_frame,
            text="🔍 Pobierz automatycznie z przeglądarki",
            font=APP_FONT_BOLD,
            height=48,
            width=360,
            fg_color="#10b981",
            hover_color="#059669",
            command=self.wizard_detect_tipply
        )
        self.wizard_auto_status = ctk.CTkLabel(
            self.wizard_content_frame,
            text="",
            font=APP_FONT_SMALL,
            wraplength=520
        )
        self.wizard_divider_label = ctk.CTkLabel(
            self.wizard_content_frame,
            text="── LUB UŻYJ SAMEGO WIDŻETU (BEZ MAILI) ──",
            font=APP_FONT_SMALL,
            text_color="#64748b"
        )
        self.wizard_widget_btn = ctk.CTkButton(
            self.wizard_content_frame,
            text="🔗 Chcę użyć linku widgetu",
            font=APP_FONT_BOLD,
            height=38,
            width=230,
            fg_color="#334155",
            hover_color="#475569",
            command=self.confirm_use_widget_link
        )

        btn_frame = ctk.CTkFrame(self.wizard_frame, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=25, padx=40)

        self.btn_wiz_back = ctk.CTkButton(
            btn_frame, text="← Wstecz", font=APP_FONT, command=self.wizard_go_back,
            state="disabled", width=120, fg_color="#1e222a", hover_color="#2b313d"
        )
        self.btn_wiz_back.pack(side="left")

        self.btn_wiz_next = ctk.CTkButton(
            btn_frame, text="Dalej →", font=APP_FONT_BOLD, command=self.wizard_go_next, width=150,
            fg_color="#3b82f6", hover_color="#2563eb"
        )
        self.btn_wiz_next.pack(side="right")

    def show_wizard(self):
        self.hide_all_frames()
        self.wizard_frame.pack(fill="both", expand=True)
        self.update_wizard_ui()

    def update_wizard_ui(self):
        for child in self.wizard_content_frame.winfo_children():
            child.pack_forget()

        if self.wizard_step == 0:
            self.wizard_title.configure(text="Krok 1/3: StreamElements JWT Token")
            self.wizard_desc.configure(
                text="Zaloguj się na streamelements.com, kliknij profilowe w prawym górnym rogu -> My account -> Channels.\n\nSkopiuj 'JWT Token' i wklej go poniżej:"
            )
            self.wizard_entry.delete(0, 'end')
            self.wizard_entry.insert(0, self.config.get("se_jwt", ""))
            self.wizard_entry.pack(pady=20)
            self.btn_wiz_back.configure(state="disabled")
            self.btn_wiz_next.configure(text="Dalej →")

        elif self.wizard_step == 1:
            self.wizard_title.configure(text="Krok 2/3: StreamElements Account ID")
            self.wizard_desc.configure(
                text="W tym samym miejscu (profilowe -> My account -> Channels na StreamElements) skopiuj swój 'Account ID':"
            )
            self.wizard_entry.delete(0, 'end')
            self.wizard_entry.insert(0, self.config.get("se_channel_id", ""))
            self.wizard_entry.pack(pady=20)
            self.btn_wiz_back.configure(state="normal")
            self.btn_wiz_next.configure(text="Dalej →")

        elif self.wizard_step == 2:
            self.wizard_title.configure(text="Krok 3/3: Połącz z Tipply")
            self.wizard_desc.configure(
                text="Możesz połączyć konto 1 kliknięciem z przeglądarki (zalecane – z e-mailami)\nLUB wkleić link do widżetu (tryb prosty z mailami zastępczymi):"
            )
            self.wizard_auto_btn.pack(pady=(5, 5))
            self.wizard_auto_status.pack(pady=(0, 10))

            self.wizard_divider_label.pack(pady=(5, 5))
            self.wizard_entry.delete(0, 'end')
            self.wizard_entry.insert(0, self.config.get("tipply_token", ""))
            self.wizard_entry.pack(pady=(0, 8))

            self.wizard_widget_btn.pack(pady=(0, 10))

            self.btn_wiz_back.configure(state="normal")
            self.btn_wiz_next.configure(text="Zakończ konfigurację")

        elif self.wizard_step == 3:
            self.wizard_title.configure(text="Wszystko gotowe! 🎉")
            self.wizard_desc.configure(
                text="Aplikacja została pomyślnie skonfigurowana.\n\nMostek przekazuje wpłaty w czasie rzeczywistym z zachowaniem pełnego bezpieczeństwa na streamie (Stream-Safe)."
            )
            self.btn_wiz_back.configure(state="normal")
            self.btn_wiz_next.configure(text="ZACZYNAMY! 🚀")

    def wizard_detect_tipply(self):
        self.wizard_auto_status.configure(text="⏳ Przeszukiwanie przeglądarek...", text_color="#f1c40f")
        self.root.update_idletasks()

        token = tipply_auth.find_tipply_auth_token_in_browsers()
        if not token:
            self.wizard_auto_status.configure(
                text="❌ Nie znaleziono sesji w przeglądarce. Zaloguj się na tipply.pl i spróbuj ponownie.",
                text_color="#ef4444"
            )
            return

        prof = tipply_auth.get_tipply_user_profile(token)
        if prof and isinstance(prof, dict):
            user_id = prof.get("id")
            username = prof.get("username", "Użytkownik")
            self.config["tipply_auth_token"] = token
            self.config["tipply_username"] = username
            if user_id:
                self.config["tipply_token"] = str(user_id)
                self.wizard_entry.delete(0, 'end')
                self.wizard_entry.insert(0, str(user_id))

            # Pobranie i zapis awatara
            avatar_url = prof.get("google_avatar_url")
            self.get_avatar_image(username, avatar_url)

            self.wizard_auto_status.configure(
                text=f"✅ Połączono pomyślnie z kontem: {username}!",
                text_color="#10b981"
            )
        else:
            self.config["tipply_auth_token"] = token
            self.wizard_auto_status.configure(
                text=f"⚠️ Znaleziono token, lecz sesja wygasła. Zaloguj się ponownie na tipply.pl.",
                text_color="#f59e0b"
            )

    def confirm_use_widget_link(self):
        """Modal potwierdzający chęć użycia tylko linku widżetu (bez pobierania maili)"""
        val = self.wizard_entry.get().strip()
        if not val:
            self.wizard_auto_status.configure(
                text="❌ Wklej najpierw link widżetu lub token w powyższe pole!",
                text_color="#ef4444"
            )
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Potwierdzenie trybu widgetu")
        dialog.geometry("480x290")
        dialog.resizable(False, False)
        dialog.configure(fg_color="#16181f")
        dialog.transient(self.root)
        dialog.grab_set()

        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 480) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 290) // 2
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(dialog, text="⚠️ Uwaga: Tryb bez e-maili", font=APP_FONT_HEADER, text_color="#f59e0b").pack(pady=(25, 10))

        msg = (
            "W trybie samego linku widgetu program NIE będzie pobierał\n"
            "prawdziwych adresów e-mail z Tipply.\n\n"
            "Do przekazywania wpłat do StreamElements zostaną użyte\n"
            "bezpieczne adresy zastępcze (np. widz_nick@tipply.pl).\n\n"
            "Czy na pewno chcesz kontynuować?"
        )
        ctk.CTkLabel(dialog, text=msg, font=APP_FONT, justify="center", text_color="#cbd5e1").pack(padx=20, pady=5)

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(side="bottom", pady=25)

        def on_confirm():
            dialog.destroy()
            token = self.extract_tipply_token(val)
            self.config["tipply_token"] = token
            self.config["tipply_auth_token"] = ""
            self.config["tipply_username"] = "Gość"
            self.wizard_step = 3
            self.update_wizard_ui()

        ctk.CTkButton(
            btn_box, text="Wróć", font=APP_FONT, width=120,
            fg_color="#334155", hover_color="#475569", command=dialog.destroy
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_box, text="Tak, użyj widgetu", font=APP_FONT_BOLD, width=160,
            fg_color="#3b82f6", hover_color="#2563eb", command=on_confirm
        ).pack(side="right", padx=10)

    def wizard_go_next(self):
        val = self.wizard_entry.get().strip()
        if self.wizard_step == 0:
            if not val: return
            self.config["se_jwt"] = val.replace("Bearer", "").strip()
        elif self.wizard_step == 1:
            if not val: return
            self.config["se_channel_id"] = val
        elif self.wizard_step == 2:
            if not self.config.get("tipply_token"):
                if val:
                    self.confirm_use_widget_link()
                    return
                else:
                    self.wizard_auto_status.configure(
                        text="❌ Wybierz jedną z metod powyżej, aby połączyć Tipply.",
                        text_color="#ef4444"
                    )
                    return

        if self.wizard_step < 3:
            self.wizard_step += 1
            self.update_wizard_ui()
        else:
            self.save_config()
            self.update_user_profile_display()
            self.show_main()

    def wizard_go_back(self):
        if self.wizard_step > 0:
            self.wizard_step -= 1
            self.update_wizard_ui()

    # --- EKRAN GŁÓWNY ---
    def build_main_screen(self):
        self.main_frame = ctk.CTkFrame(self.root, fg_color="#12141a")

        header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        header_frame.pack(fill="x", padx=25, pady=(18, 14))

        # Zwiększony nagłówek programu: "Tipply Bridge" (Bold 700) + mniejsze "by SZABLIX"
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="Tipply Bridge",
            font=ctk.CTkFont(family=FONT_NAME, size=21, weight="bold"),
            text_color="#f8fafc"
        ).pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="by SZABLIX",
            font=ctk.CTkFont(family=FONT_NAME, size=15, weight="bold"),
            text_color="#94a3b8"
        ).pack(side="left", padx=(7, 0), pady=(4, 0))

        # Prawy górny róg: Numer wersji v1.1 oraz logotyp GitHub i link do repozytorium
        top_right_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        top_right_box.pack(side="right")

        lbl_version = ctk.CTkLabel(
            top_right_box,
            text="v1.1",
            font=(FONT_NAME, 13, "bold"),
            text_color="#94a3b8",
            fg_color="#1a1d24",
            corner_radius=8,
            width=50,
            height=28
        )
        lbl_version.pack(side="left", padx=(0, 8))

        btn_github = ctk.CTkButton(
            top_right_box,
            text=" GitHub" if self.github_icon else "GitHub ↗",
            image=self.github_icon,
            compound="left",
            font=(FONT_NAME, 13, "bold"),
            text_color="#e2e8f0",
            fg_color="#1a1d24",
            hover_color="#282d38",
            corner_radius=8,
            height=28,
            width=88,
            cursor="hand2",
            command=self.open_github_repo
        )
        btn_github.pack(side="left")

        content_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=25, pady=(0, 22))

        # LEWA KARTA
        self.left_card = ctk.CTkFrame(
            content_frame, width=250, corner_radius=18,
            fg_color="#1a1d24", border_width=1, border_color="#2a2e39"
        )
        self.left_card.pack(side="left", fill="y", padx=(0, 18))
        self.left_card.pack_propagate(False)

        # Powiększony, wyrazisty przycisk START / STOP
        self.btn_toggle = ctk.CTkButton(
            self.left_card,
            text="⏻ START",
            font=(FONT_NAME, 20, "bold"),
            fg_color="#10b981",
            hover_color="#059669",
            corner_radius=14,
            height=76,
            width=210,
            command=self.toggle_connection
        )
        self.btn_toggle.pack(pady=(45, 16), padx=20)

        # Status
        self.status_box = ctk.CTkFrame(self.left_card, fg_color="transparent")
        self.status_box.pack(pady=(0, 20))

        self.lbl_status = ctk.CTkLabel(
            self.status_box, text="🔴 Rozłączono", font=APP_FONT_BOLD, text_color="#f87171"
        )
        self.lbl_status.pack()

        # Dolny pasek: Profil (Awatar + Nick) + Koło zębate (Ustawienia)
        profile_container = ctk.CTkFrame(
            self.left_card,
            fg_color="#13151b",
            corner_radius=12,
            border_width=1,
            border_color="#242833",
            height=60
        )
        profile_container.pack(side="bottom", fill="x", padx=12, pady=12)
        profile_container.pack_propagate(False)

        # Awatar z Tipply lub wygenerowany
        self.avatar_img = self.get_avatar_image("Gość")
        self.lbl_avatar = ctk.CTkLabel(profile_container, text="", image=self.avatar_img)
        self.lbl_avatar.pack(side="left", padx=(10, 8))

        # Nick i podtytuł
        user_text_frame = ctk.CTkFrame(profile_container, fg_color="transparent")
        user_text_frame.pack(side="left", fill="y", pady=8)

        self.lbl_username = ctk.CTkLabel(
            user_text_frame, text="Gość", font=(FONT_NAME, 12, "bold"),
            text_color="#f1f5f9", anchor="w"
        )
        self.lbl_username.pack(anchor="w")

        self.lbl_user_sub = ctk.CTkLabel(
            user_text_frame, text="Tryb widgetu", font=(FONT_NAME, 10),
            text_color="#64748b", anchor="w"
        )
        self.lbl_user_sub.pack(anchor="w")

        # Koło zębate
        self.btn_settings = ctk.CTkButton(
            profile_container,
            text="⚙",
            font=(FONT_NAME, 16),
            width=34,
            height=34,
            corner_radius=8,
            fg_color="#1e222b",
            hover_color="#2d3340",
            text_color="#cbd5e1",
            command=self.show_settings
        )
        self.btn_settings.pack(side="right", padx=(0, 8))

        # PRAWA KARTA
        self.right_card = ctk.CTkFrame(
            content_frame, corner_radius=18,
            fg_color="#1a1d24", border_width=1, border_color="#2a2e39"
        )
        self.right_card.pack(side="right", fill="both", expand=True)

        ctk.CTkLabel(
            self.right_card, text="Dziennik zdarzeń", font=APP_FONT_TITLE, text_color="#cbd5e1"
        ).pack(pady=(16, 10))

        # Kontener na logi z powiększoną, czytelną czcionką
        self.log_text = ctk.CTkTextbox(
            self.right_card,
            state="disabled",
            fg_color="#12141a",
            border_width=1,
            border_color="#232732",
            corner_radius=14,
            font=APP_FONT_LOG,
            wrap="word"
        )
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # Tagi kolorystyczne bez opcji font (kompatybilne ze skalowaniem DPI)
        self.log_text.tag_config("time", foreground="#64748b")
        self.log_text.tag_config("sep", foreground="#334155")
        self.log_text.tag_config("normal", foreground="#e2e8f0")
        self.log_text.tag_config("tip", foreground="#10b981")
        self.log_text.tag_config("success", foreground="#34d399")
        self.log_text.tag_config("warn", foreground="#fbbf24")
        self.log_text.tag_config("error", foreground="#f87171")
        self.log_text.tag_config("info", foreground="#38bdf8")
        self.log_text.tag_config("muted", foreground="#94a3b8")
        self.log_text.tag_config("new_line", background="#1e293b")

    def update_user_profile_display(self):
        """Pobiera i odświeża awatar z Tipply oraz nazwę użytkownika"""
        username = self.config.get("tipply_username")
        auth_token = self.config.get("tipply_auth_token")

        # Jeśli awatar został wcześniej zapisany w pamięci podręcznej, załaduj go od razu
        cache_path = os.path.join(os.path.dirname(CONFIG_FILE), "avatar.png")
        if os.path.exists(cache_path) and username and username != "Gość":
            try:
                cached = Image.open(cache_path)
                self.avatar_img = ctk.CTkImage(cached, size=(38, 38))
                self.lbl_avatar.configure(image=self.avatar_img)
            except Exception:
                pass

        def fetch_bg():
            prof = tipply_auth.get_tipply_user_profile(auth_token)
            if prof and isinstance(prof, dict):
                name = prof.get("username", username or "Konto Tipply")
                avatar_url = prof.get("google_avatar_url")
                self.config["tipply_username"] = name
                pil_img = self.download_and_crop_avatar(name, avatar_url)
                self.root.after(0, lambda: self._apply_profile_ui(name, True, pil_img))
            else:
                pil_img = self.download_and_crop_avatar(username or "Gość")
                self.root.after(0, lambda: self._apply_profile_ui(username or "Gość", bool(auth_token), pil_img))

        if auth_token:
            threading.Thread(target=fetch_bg, daemon=True).start()
        else:
            pil_img = self.download_and_crop_avatar("Gość")
            self._apply_profile_ui(username or "Gość", False, pil_img)

    def _apply_profile_ui(self, name, has_auth, pil_img=None):
        self.lbl_username.configure(text=name)
        self.lbl_user_sub.configure(text="Tipply połączone" if has_auth else "Tryb widgetu")
        if pil_img:
            self.avatar_img = ctk.CTkImage(pil_img, size=(38, 38))
            self.lbl_avatar.configure(image=self.avatar_img)

    # --- EKRAN USTAWIEŃ ---
    def build_settings_screen(self):
        self.settings_frame = ctk.CTkFrame(self.root, fg_color="#12141a")
        
        top_bar = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        top_bar.pack(fill="x", padx=25, pady=(15, 10))

        ctk.CTkButton(
            top_bar, text="← Wróć", font=APP_FONT, width=100, height=36,
            fg_color="#1e222a", hover_color="#2b313d", command=self.show_main
        ).pack(side="left")

        ctk.CTkLabel(
            top_bar, text="Ustawienia połączenia", font=APP_FONT_TITLE, text_color="#f8fafc"
        ).pack(side="left", padx=20)

        form_frame = ctk.CTkFrame(
            self.settings_frame, corner_radius=18,
            fg_color="#1a1d24", border_width=1, border_color="#2a2e39"
        )
        form_frame.pack(fill="both", expand=True, padx=25, pady=(0, 20))

        ctk.CTkLabel(form_frame, text="StreamElements JWT Token:", font=APP_FONT_BOLD).pack(anchor="w", padx=25, pady=(12, 2))
        self.entry_jwt = ctk.CTkEntry(form_frame, width=600, font=APP_FONT, fg_color="#12141a", border_color="#242833")
        self.entry_jwt.pack(padx=25)

        ctk.CTkLabel(form_frame, text="StreamElements Account ID:", font=APP_FONT_BOLD).pack(anchor="w", padx=25, pady=(8, 2))
        self.entry_channel = ctk.CTkEntry(form_frame, width=600, font=APP_FONT, fg_color="#12141a", border_color="#242833")
        self.entry_channel.pack(padx=25)

        ctk.CTkLabel(form_frame, text="Tipply Token / ID Konta:", font=APP_FONT_BOLD).pack(anchor="w", padx=25, pady=(8, 2))
        self.entry_tipply = ctk.CTkEntry(form_frame, width=600, font=APP_FONT, fg_color="#12141a", border_color="#242833")
        self.entry_tipply.pack(padx=25)

        auth_header_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        auth_header_frame.pack(fill="x", padx=25, pady=(8, 2))

        ctk.CTkLabel(auth_header_frame, text="Tipply Sesja (auth_token do pobierania e-maili):", font=APP_FONT_BOLD).pack(side="left")
        ctk.CTkButton(
            auth_header_frame, text="🔍 Wykryj z przeglądarki", font=APP_FONT_SMALL,
            width=170, height=30, fg_color="#10b981", hover_color="#059669",
            command=self.settings_detect_tipply
        ).pack(side="right")

        self.entry_tipply_auth = ctk.CTkEntry(form_frame, width=600, font=APP_FONT, fg_color="#12141a", border_color="#242833")
        self.entry_tipply_auth.pack(padx=25)

        self.lbl_settings_status = ctk.CTkLabel(form_frame, text="", font=APP_FONT_SMALL)
        self.lbl_settings_status.pack(anchor="w", padx=25, pady=(2, 0))

        ctk.CTkButton(
            form_frame, text="ZAPISZ ZMIANY", font=APP_FONT_BOLD,
            height=42, width=200, fg_color="#3b82f6", hover_color="#2563eb",
            command=self.save_settings
        ).pack(pady=(14, 10))

    def settings_detect_tipply(self):
        self.lbl_settings_status.configure(text="⏳ Przeszukiwanie przeglądarek...", text_color="#f1c40f")
        self.root.update_idletasks()

        token = tipply_auth.find_tipply_auth_token_in_browsers()
        if not token:
            self.lbl_settings_status.configure(
                text="❌ Nie znaleziono sesji w przeglądarce. Zaloguj się na tipply.pl.",
                text_color="#ef4444"
            )
            return

        prof = tipply_auth.get_tipply_user_profile(token)
        self.entry_tipply_auth.delete(0, 'end')
        self.entry_tipply_auth.insert(0, token)

        if prof and isinstance(prof, dict):
            user_id = prof.get("id")
            username = prof.get("username", "Konto")
            self.config["tipply_username"] = username
            if user_id and not self.entry_tipply.get().strip():
                self.entry_tipply.delete(0, 'end')
                self.entry_tipply.insert(0, str(user_id))

            # Pobranie awatara
            avatar_url = prof.get("google_avatar_url")
            self.get_avatar_image(username, avatar_url)

            self.lbl_settings_status.configure(
                text=f"✅ Aktywna sesja Tipply ({username})!",
                text_color="#10b981"
            )
        else:
            self.lbl_settings_status.configure(
                text="⚠️ Znaleziono token, lecz sesja wygasła. Zaloguj się ponownie na tipply.pl.",
                text_color="#f59e0b"
            )

    def hide_all_frames(self):
        for f in [self.main_frame, self.settings_frame, self.wizard_frame]:
            f.pack_forget()

    def show_main(self):
        self.hide_all_frames()
        self.update_user_profile_display()
        self.main_frame.pack(fill="both", expand=True)

    def show_settings(self):
        if self.is_running: return
        self.entry_jwt.delete(0, 'end'); self.entry_jwt.insert(0, self.config.get("se_jwt", ""))
        self.entry_channel.delete(0, 'end'); self.entry_channel.insert(0, self.config.get("se_channel_id", ""))
        self.entry_tipply.delete(0, 'end'); self.entry_tipply.insert(0, self.config.get("tipply_token", ""))
        self.entry_tipply_auth.delete(0, 'end'); self.entry_tipply_auth.insert(0, self.config.get("tipply_auth_token", ""))
        self.lbl_settings_status.configure(text="")
        self.hide_all_frames()
        self.settings_frame.pack(fill="both", expand=True)

    def save_settings(self):
        self.config["se_jwt"] = self.entry_jwt.get().replace("Bearer", "").strip()
        self.config["se_channel_id"] = self.entry_channel.get().strip()
        self.config["tipply_token"] = self.extract_tipply_token(self.entry_tipply.get())
        self.config["tipply_auth_token"] = self.entry_tipply_auth.get().strip()
        self.save_config()
        self.update_user_profile_display()
        self.show_main()

    # --- DZIENNIK ZDARZEŃ ---
    def poll_log_queue(self):
        while not self.log_queue.empty():
            line = self.log_queue.get()
            self._insert_log_line(line)
        self.root.after(100, self.poll_log_queue)

    def _insert_log_line(self, line):
        self.log_text.configure(state="normal")
        start_index = self.log_text.index("end-1c")

        if " | " in line:
            time_part, content_part = line.split(" | ", 1)
            self.log_text.insert("end", time_part + " ", "time")
            self.log_text.insert("end", "│ ", "sep")

            tag = "normal"
            if "DONEJT" in content_part:
                tag = "tip"
            elif "✅" in content_part:
                tag = "success"
            elif "⚠️" in content_part:
                tag = "warn"
            elif "🔥" in content_part or "❌" in content_part:
                tag = "error"
            elif "🌐" in content_part or "📧" in content_part:
                tag = "info"
            elif "⏹" in content_part:
                tag = "muted"

            self.log_text.insert("end", content_part + "\n", tag)
        else:
            self.log_text.insert("end", line + "\n", "normal")

        end_index = self.log_text.index("end-1c")

        # Efekt "wskoczenia" nowego logu
        self.log_text.tag_add("new_line", start_index, end_index)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        self.root.after(350, lambda: self.log_text.tag_remove("new_line", "1.0", "end"))

    def update_status(self, connected):
        """Bezpieczna wielowątkowo zmiana stanu UI"""
        self.root.after(0, lambda: self._apply_status_ui(connected))

    def _apply_status_ui(self, connected):
        if connected:
            self.lbl_status.configure(text="🟢 Połączono", text_color="#10b981")
            self.btn_toggle.configure(text="⏼ STOP", fg_color="#ef4444", hover_color="#dc2626")
            self.is_running = True
        else:
            self.lbl_status.configure(text="🔴 Rozłączono", text_color="#f87171")
            self.btn_toggle.configure(text="⏻ START", fg_color="#10b981", hover_color="#059669")
            self.is_running = False

    def toggle_connection(self):
        if not self.is_running:
            self.logger.info("Uruchamianie mostu...")
            self.is_running = True
            threading.Thread(target=self.run_bridge, daemon=True).start()
        else:
            self.logger.info("Zatrzymywanie mostka...")
            self.is_running = False
            if self.sio:
                try: self.sio.disconnect()
                except Exception: pass

    def run_bridge(self):
        tipply_id = self.config.get("tipply_token")
        if not tipply_id:
            self.logger.error("❌ Brak identyfikatora widżetu / konta Tipply.")
            self.update_status(False)
            return

        namespace = f"/{tipply_id}"
        self.update_status(True)

        while self.is_running:
            try:
                self.sio = socketio.Client(logger=False, engineio_logger=False, reconnection=True, reconnection_delay=3)

                @self.sio.event(namespace=namespace)
                def connect():
                    self.logger.info("🌐 Połączono z serwerem alertów Tipply!")

                @self.sio.event(namespace=namespace)
                def disconnect():
                    if self.is_running:
                        self.logger.warning("⚠️ Rozłączono z Tipply. Próba ponownego połączenia...")

                @self.sio.on('alert', namespace=namespace)
                def on_alert(data):
                    try:
                        tip_id = data.get('payment_id') or data.get('id')
                        nick = data.get('nickname', 'Widz')
                        amount_pln = float(data.get('amount', 0)) / 100.0
                        message = data.get('message', '')

                        if tip_id in self.processed_tips:
                            return

                        if amount_pln > 0:
                            self.logger.info(f"🔔 DONEJT: {nick} - {amount_pln:.2f} PLN")

                            # 1. Próba pobrania prawdziwego maila z Tipply (NAJWYŻSZY PRIORYTET z pętlą retry)
                            real_email = None
                            auth_token = self.config.get("tipply_auth_token")
                            if auth_token:
                                real_email = tipply_auth.fetch_real_email_from_tipply(
                                    auth_token,
                                    tip_id=tip_id,
                                    payment_id=data.get('payment_id'),
                                    nickname=nick,
                                    amount_pln=amount_pln,
                                    max_attempts=5
                                )

                            # 2. Wybór adresu e-mail (prawdziwy lub awaryjny)
                            if real_email:
                                self.logger.info("📧 Pobrano dane widza z Tipply")
                                final_email = real_email
                            elif auth_token:
                                clean_nick = re.sub(r'[^a-zA-Z0-9]', '', nick).lower() or 'widz'
                                safe_id = str(tip_id or 'anon')[:8]
                                final_email = f"{clean_nick}_{safe_id}@tipply.pl"
                                self.logger.warning("⚠️ Nie odnaleziono wpłaty w historii Tipply (alert testowy?) – użyto adresu zastępczego")
                            else:
                                clean_nick = re.sub(r'[^a-zA-Z0-9]', '', nick).lower() or 'widz'
                                safe_id = str(tip_id or 'anon')[:8]
                                final_email = f"{clean_nick}_{safe_id}@tipply.pl"
                                self.logger.info("ℹ️ Tryb widgetu (brak sesji Tipply) – wygenerowano bezpieczny adres zastępczy")

                            # 3. Wysłanie do StreamElements
                            self.send_to_streamelements(nick, amount_pln, message, final_email, tip_id)
                            self.processed_tips.add(tip_id)
                    except Exception as ex:
                        self.logger.error(f"🔥 Błąd przetwarzania donejta: {ex}")

                self.sio.connect("https://alert-ws.tipply.pl", namespaces=[namespace], transports=['websocket', 'polling'])
                self.sio.wait()

            except Exception as e:
                if not self.is_running:
                    break
                self.logger.warning("⚠️ Błąd sieci Tipply. Ponawianie połączenia za 5s...")
                time.sleep(5)

        self.update_status(False)
        self.logger.info("⏹ Mostek został pomyślnie zatrzymany.")

    def send_to_streamelements(self, user, amount, message, tip_email, payment_id):
        url = f"https://api.streamelements.com/kappa/v2/tips/{self.config['se_channel_id']}"
        headers = {
            "Authorization": f"Bearer {self.config['se_jwt']}",
            "Content-Type": "application/json"
        }
        data = {
            "user": {"username": user, "email": tip_email},
            "provider": "tipply_bridge_by_SZABLIX",
            "amount": amount,
            "currency": "PLN",
            "message": message or "",
            "imported": True,
            "transactionId": str(payment_id) if payment_id else None
        }

        try:
            r = requests.post(url, json=data, headers=headers, timeout=5)
            if r.status_code == 200:
                self.logger.info(f"✅ Przekazano do StreamElements ({amount:.2f} PLN)")
            elif r.status_code == 400:
                msg = ""
                try: msg = r.json().get("message", "")
                except Exception: pass
                self.logger.error(f"🔥 StreamElements odrzucił żądanie (kod 400): {msg}")
            else:
                err = ""
                msg = ""
                try:
                    err = r.json().get("error", "")
                    msg = r.json().get("message", "")
                except Exception: pass
                self.logger.error(f"🔥 StreamElements zwrócił błąd {r.status_code}: {err} {msg}".strip())
        except Exception:
            self.logger.error("🔥 Błąd sieci podczas wysyłania do StreamElements.")

    # --- SYSTEM TRAY ---
    def create_image(self):
        icon_path = "logo.ico"
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, "logo.ico")

        try:
            return Image.open(icon_path)
        except Exception:
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
            try: self.tray_icon.stop()
            except Exception: pass
        self.root.after(0, self.root.deiconify)

if __name__ == "__main__":
    try:
        root = ctk.CTk()
        app = TipplyBridgeApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\n[!] Wykryto Ctrl+C. Trwa bezpieczne zamykanie mostu...")
        if 'app' in locals():
            app.quit_app_entirely()
    except Exception as e:
        print(f"\n[X] Wystąpił błąd krytyczny: {e}")
