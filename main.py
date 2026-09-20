import sys
import os
import json
import time
import requests
from datetime import datetime, timezone
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QSpinBox, QMessageBox, QFrame,
    QTabWidget, QTextEdit, QProgressBar, QAbstractItemView,
    QDialog, QDialogButtonBox, QFormLayout, QFileDialog, QListWidget,
    QListWidgetItem, QSlider, QCheckBox, QRadioButton
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QFont, QColor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# ═══════════════════════════════════════════════════════════
# ⚙️  CONFIGURATION
# ═══════════════════════════════════════════════════════════
API_URL = "https://smartcollect.onrender.com"

ADMIN_API_KEY = os.getenv("SMARTCOLLECT_ADMIN_KEY", "").strip()

_CONFIG_FILE_EARLY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
if not ADMIN_API_KEY and os.path.exists(_CONFIG_FILE_EARLY):
    try:
        with open(_CONFIG_FILE_EARLY, "r", encoding="utf-8") as _f:
            _cfg = json.load(_f)
            ADMIN_API_KEY = str(_cfg.get("admin_api_key", "")).strip()
    except Exception:
        pass


def _headers() -> dict:
    return {"X-Admin-Key": ADMIN_API_KEY} if ADMIN_API_KEY else {}


KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base")
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_library")
CONFIG_FILE = _CONFIG_FILE_EARLY
LAST_MUSIC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_music.json")

os.makedirs(KB_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════
# 🎵 BIBLIOTHÈQUE MUSICALE
# ═══════════════════════════════════════════════════════════
class MusicLibraryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Bibliothèque Musicale")
        self.resize(600, 440)
        self.setStyleSheet("""
            QDialog { background-color: #0b1120; color: #f8fafc; }
            QListWidget {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 8px;
                color: #f1f5f9;
                padding: 6px;
                font-size: 13px;
            }
            QListWidget::item { padding: 8px 12px; border-radius: 6px; margin-bottom: 2px; }
            QListWidget::item:hover { background-color: #1f2937; }
            QListWidget::item:selected { background-color: #4338ca; color: #ffffff; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Bibliothèque Audio")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        layout.addWidget(title)

        self.music_list = QListWidget()
        self.music_list.itemDoubleClicked.connect(self.play_selected_music)
        layout.addWidget(self.music_list)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self.btn_add = QPushButton("Importer des morceaux")
        self.btn_add.clicked.connect(self.add_music)
        btn_box.addWidget(self.btn_add)

        self.btn_play = QPushButton("Lire la sélection")
        self.btn_play.clicked.connect(self.play_selected_music)
        btn_box.addWidget(self.btn_play)

        btn_box.addStretch()

        self.btn_del = QPushButton("Supprimer")
        self.btn_del.setObjectName("btn_danger")
        self.btn_del.clicked.connect(self.delete_selected_music)
        btn_box.addWidget(self.btn_del)

        layout.addLayout(btn_box)
        self.load_music_list()

    def load_music_list(self):
        self.music_list.clear()
        try:
            files = [f for f in os.listdir(MUSIC_DIR)
                     if f.endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg"))]
        except Exception:
            files = []
        if not files:
            self.music_list.addItem(QListWidgetItem("Aucun morceau présent"))
            return
        for f in sorted(files):
            item = QListWidgetItem(f"🎵  {f}")
            item.setData(Qt.UserRole, os.path.join(MUSIC_DIR, f))
            self.music_list.addItem(item)

    def add_music(self):
        fpaths, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter Audio", "", "Audio (*.mp3 *.wav *.flac *.m4a *.ogg)"
        )
        if not fpaths:
            return
        import shutil
        for fp in fpaths:
            dest = os.path.join(MUSIC_DIR, os.path.basename(fp))
            if not os.path.exists(dest):
                try:
                    shutil.copy2(fp, dest)
                except Exception as e:
                    print(f"[MusicLibrary] copie échouée : {e}")
        self.load_music_list()
        if self.parent_window:
            self.parent_window.refresh_music_list()

    def play_selected_music(self):
        item = self.music_list.currentItem()
        if item and item.data(Qt.UserRole) and self.parent_window:
            self.parent_window.play_music(item.data(Qt.UserRole))
            self.accept()

    def delete_selected_music(self):
        item = self.music_list.currentItem()
        if item and item.data(Qt.UserRole):
            p = item.data(Qt.UserRole)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception as e:
                print(f"[MusicLibrary] suppression échouée : {e}")
            self.load_music_list()
            if self.parent_window:
                self.parent_window.refresh_music_list()


# ═══════════════════════════════════════════════════════════
# 🎼 LECTEUR MUSICAL
# ═══════════════════════════════════════════════════════════
class MusicPlayerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.current_music_path = None
        self.is_playing = False
        self.playlist = []
        self.current_index = -1
        self.use_pygame = False

        self.player = None
        self.audio_output = None
        try:
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)
            self.player.positionChanged.connect(self.update_position)
            self.player.mediaStatusChanged.connect(self.handle_media_status)
            self.audio_output.setVolume(0.7)
        except Exception as e:
            print(f"[MusicPlayer] QMediaPlayer indisponible : {e}")
            self.player = None
            self.audio_output = None

        if not self.player and PYGAME_AVAILABLE:
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                self.use_pygame = True
            except Exception:
                self.use_pygame = False

        self.setup_ui()
        self.load_playlist()
        self.load_last_music()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)

        self.btn_library = QPushButton("Bibliothèque")
        self.btn_library.setObjectName("btn_secondary")
        self.btn_library.clicked.connect(self.open_library)
        layout.addWidget(self.btn_library)

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setObjectName("btn_icon")
        self.btn_prev.clicked.connect(self.previous_track)
        layout.addWidget(self.btn_prev)

        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("btn_play")
        self.btn_play.clicked.connect(self.toggle_play)
        layout.addWidget(self.btn_play)

        self.btn_next = QPushButton("⏭")
        self.btn_next.setObjectName("btn_icon")
        self.btn_next.clicked.connect(self.next_track)
        layout.addWidget(self.btn_next)

        self.music_info = QLabel("Aucun morceau")
        self.music_info.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: 500;")
        self.music_info.setMaximumWidth(220)
        layout.addWidget(self.music_info)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.sliderMoved.connect(self.set_position)
        layout.addWidget(self.position_slider, 1)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(self.time_label)

        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(vol_icon)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(75)
        self.volume_slider.valueChanged.connect(self.set_volume)
        layout.addWidget(self.volume_slider)

    def open_library(self):
        MusicLibraryDialog(self.parent_window).exec()

    def load_playlist(self):
        try:
            files = [f for f in os.listdir(MUSIC_DIR)
                     if f.endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg"))]
        except Exception:
            files = []
        self.playlist = [os.path.join(MUSIC_DIR, f) for f in sorted(files)]

    def refresh_playlist(self):
        self.load_playlist()

    def load_last_music(self):
        if os.path.exists(LAST_MUSIC_FILE):
            try:
                with open(LAST_MUSIC_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    p = data.get("last_music_path")
                    if p and os.path.exists(p):
                        self.current_music_path = p
                        self.music_info.setText(os.path.basename(p))
                        if p in self.playlist:
                            self.current_index = self.playlist.index(p)
            except Exception:
                pass

    def play_music(self, music_path):
        if not music_path or not os.path.exists(music_path):
            return
        self.current_music_path = music_path
        self.music_info.setText(os.path.basename(music_path))
        try:
            with open(LAST_MUSIC_FILE, "w", encoding="utf-8") as f:
                json.dump({"last_music_path": music_path}, f)
        except Exception:
            pass

        if self.player:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(music_path))
            self.player.play()
            self.is_playing = True
            self.btn_play.setText("⏸")
            return

        if self.use_pygame:
            try:
                pygame.mixer.music.load(music_path)
                pygame.mixer.music.play()
                self.is_playing = True
                self.btn_play.setText("⏸")
            except Exception:
                pass

    def toggle_play(self):
        if not self.player and not self.use_pygame:
            QMessageBox.information(
                self, "Lecture audio indisponible",
                "Aucun backend audio (Qt Multimedia ni pygame) n'est disponible."
            )
            return

        if not self.current_music_path:
            self.load_last_music()
            if not self.current_music_path and self.playlist:
                self.current_music_path = self.playlist[0]

        if not self.current_music_path:
            return

        if not self.is_playing:
            if self.player and self.player.source().isEmpty():
                self.play_music(self.current_music_path)
                return
            elif self.player and not self.player.source().isEmpty():
                self.player.play()
                self.is_playing = True
                self.btn_play.setText("⏸")
                return
            elif self.use_pygame:
                self.play_music(self.current_music_path)
                return
        else:
            if self.player:
                self.player.pause()
            elif self.use_pygame:
                pygame.mixer.music.pause()
            self.is_playing = False
            self.btn_play.setText("▶")

    def next_track(self):
        if not self.playlist:
            return
        self.current_index = (self.current_index + 1) % len(self.playlist)
        self.play_music(self.playlist[self.current_index])

    def previous_track(self):
        if not self.playlist:
            return
        self.current_index = (self.current_index - 1) % len(self.playlist)
        self.play_music(self.playlist[self.current_index])

    def update_position(self, pos):
        if self.player and self.player.duration() > 0:
            self.position_slider.setValue(int((pos / self.player.duration()) * 1000))
            self.time_label.setText(
                f"{pos//60000}:{(pos%60000)//1000:02d} / "
                f"{self.player.duration()//60000}:{(self.player.duration()%60000)//1000:02d}"
            )

    def set_position(self, val):
        if self.player and self.player.duration() > 0:
            self.player.setPosition(int((val / 1000) * self.player.duration()))

    def set_volume(self, val):
        if self.audio_output:
            self.audio_output.setVolume(val / 100.0)
        if self.use_pygame:
            try:
                pygame.mixer.music.set_volume(val / 100.0)
            except Exception:
                pass

    def handle_media_status(self, st):
        if self.player and st == QMediaPlayer.EndOfMedia:
            self.next_track()


# ═══════════════════════════════════════════════════════════
# 📝 DIALOGUES
# ═══════════════════════════════════════════════════════════
class EditLicenseDialog(QDialog):
    def __init__(self, license_data: dict, parent=None):
        super().__init__(parent)
        self.license_data = license_data
        self.setWindowTitle(f"Modifier la licence — {license_data.get('key', '')}")
        self.resize(460, 400)
        self.setStyleSheet("""
            QDialog { background-color: #0b1120; color: #f8fafc; }
            QLabel { color: #cbd5e1; font-size: 12px; font-weight: 600; }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QPushButton {
                background-color: #4f46e5;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btn_secondary {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #e2e8f0;
            }
            QCheckBox { color: #cbd5e1; font-size: 12px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        info = QLabel(
            f"Clé : {license_data.get('key', '—')}\n"
            f"Client actuel : {license_data.get('user_name') or 'Non activé'}\n"
            f"Expiration actuelle : {license_data.get('expires_at') or 'Non activée'}"
        )
        info.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 8px; "
                           "background-color: #111827; border-radius: 6px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)

        self.input_phone = QLineEdit(license_data.get("phone_number") or "")
        form.addRow("Téléphone :", self.input_phone)

        self.input_client = QLineEdit(license_data.get("user_name") or "")
        form.addRow("Client :", self.input_client)

        self.input_org = QLineEdit(license_data.get("organization") or "")
        form.addRow("Organisation :", self.input_org)

        self.spin_devices = QSpinBox()
        self.spin_devices.setRange(1, 50)
        try:
            self.spin_devices.setValue(int(license_data.get("max_devices", 1)))
        except (ValueError, TypeError):
            self.spin_devices.setValue(1)
        form.addRow("Appareils max :", self.spin_devices)

        self.chk_active = QCheckBox("Licence active")
        self.chk_active.setChecked(bool(license_data.get("is_active", True)))
        form.addRow("Statut :", self.chk_active)

        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(0, 999)
        self.spin_duration.setValue(0)
        form.addRow("Prolonger de :", self.spin_duration)

        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["Mois", "Jours", "Ans"])
        form.addRow("Unité :", self.combo_unit)

        layout.addLayout(form)

        btns = QDialogButtonBox()
        btn_save = QPushButton("💾 Enregistrer")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("Annuler")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)
        btns.addButton(btn_save, QDialogButtonBox.AcceptRole)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(btns)

    def get_payload(self) -> dict:
        extend_val = self.spin_duration.value()
        return {
            "phone_number": self.input_phone.text().strip() or None,
            "user_name": self.input_client.text().strip() or None,
            "organization": self.input_org.text().strip() or None,
            "max_devices": self.spin_devices.value(),
            "is_active": self.chk_active.isChecked(),
            "extend_duration_val": extend_val if extend_val > 0 else None,
            "extend_duration_unit": self.combo_unit.currentText() if extend_val > 0 else None,
        }


class GrantFullAccessDialog(QDialog):
    def __init__(self, license_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Accès complet — {license_key}")
        self.resize(420, 320)
        self.setStyleSheet("""
            QDialog { background-color: #0b1120; color: #f8fafc; }
            QLabel { color: #cbd5e1; font-size: 12px; font-weight: 600; }
            QSpinBox, QComboBox {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QPushButton {
                background-color: #047857;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btn_secondary {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #e2e8f0;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        info = QLabel(
            f"Clé : {license_key}\n\n"
            "Cette action va :\n"
            "  ✓ Sortir la licence du mode essai\n"
            "  ✓ Débloquer les exports, imports et fusions\n"
            "  ✓ Limiter l'accès à la durée choisie ci-dessous"
        )
        info.setStyleSheet(
            "color: #a5b4fc; font-size: 11.5px; padding: 12px; "
            "background-color: #111827; border-radius: 6px; "
            "border: 1px solid #1f2937;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)

        self.spin_val = QSpinBox()
        self.spin_val.setRange(1, 999)
        self.spin_val.setValue(1)
        form.addRow("Durée :", self.spin_val)

        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["Mois", "Jours", "Ans"])
        form.addRow("Unité :", self.combo_unit)

        layout.addLayout(form)

        btns = QDialogButtonBox()
        btn_ok = QPushButton("⭐ Accorder l'accès complet")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Annuler")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)
        btns.addButton(btn_ok, QDialogButtonBox.AcceptRole)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(btns)

    def get_values(self):
        return self.spin_val.value(), self.combo_unit.currentText()


class ResetToTrialDialog(QDialog):
    def __init__(self, license_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Remettre en essai — {license_key}")
        self.resize(420, 320)
        self.setStyleSheet("""
            QDialog { background-color: #0b1120; color: #f8fafc; }
            QLabel { color: #cbd5e1; font-size: 12px; font-weight: 600; }
            QSpinBox, QComboBox {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QPushButton {
                background-color: #b45309;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btn_secondary {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #e2e8f0;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        info = QLabel(
            f"Clé : {license_key}\n\n"
            "Cette action va :\n"
            "  ⚠ Repasser la licence en MODE ESSAI\n"
            "  ⚠ Re-verrouiller les exports, imports et fusions\n"
            "  ⚠ Limiter à 1 tableau et 50 lignes max\n"
            "  ⚠ Remettre la minuterie à zéro"
        )
        info.setStyleSheet(
            "color: #fcd34d; font-size: 11.5px; padding: 12px; "
            "background-color: #111827; border-radius: 6px; "
            "border: 1px solid #78350f;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)

        self.spin_val = QSpinBox()
        self.spin_val.setRange(1, 999)
        self.spin_val.setValue(7)
        form.addRow("Durée essai :", self.spin_val)

        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["Jours", "Mois", "Ans"])
        form.addRow("Unité :", self.combo_unit)

        layout.addLayout(form)

        btns = QDialogButtonBox()
        btn_ok = QPushButton("↩ Remettre en essai")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Annuler")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)
        btns.addButton(btn_ok, QDialogButtonBox.AcceptRole)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(btns)

    def get_values(self):
        return self.spin_val.value(), self.combo_unit.currentText()


# ═══════════════════════════════════════════════════════════
# ✅ DIALOGUE DÉBLOCAGE — 2 MODES (Essai / Accès complet)
# ═══════════════════════════════════════════════════════════
class UnblockDeviceDialog(QDialog):
    """
    Dialogue de déblocage avec 2 modes :
      1. 🎟 Essai supplémentaire (limité : 1 tableau, 50 lignes, pas d'export)
      2. ⭐ Accès complet temporaire (toutes fonctions, durée limitée)

    L'admin peut configurer les durées par défaut dans l'onglet Licences.
    """

    def __init__(
        self,
        device_id: str,
        current_quota: int,
        attempts_used: int,
        parent=None,
        default_mode: str = "trial",
        default_trial_days: int = 7,
        default_trial_unit: str = "Jours",
        default_full_days: int = 7,
        default_full_unit: str = "Jours",
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Débloquer — {device_id[:24]}...")
        self.resize(560, 620)
        self.setStyleSheet("""
            QDialog { background-color: #0b1120; color: #f8fafc; }
            QLabel { color: #cbd5e1; font-size: 12px; font-weight: 600; }
            QSpinBox, QComboBox {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #047857;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btn_secondary {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #e2e8f0;
            }
            QRadioButton {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 600;
                padding: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Bandeau rappel politique
        policy_banner = QLabel(
            "📌 Politique actuelle : 1 essai par appareil.\n"
            "Vous pouvez débloquer cet appareil soit par un essai supplémentaire, "
            "soit par un accès complet temporaire."
        )
        policy_banner.setStyleSheet(
            "color: #fcd34d; font-size: 11.5px; padding: 10px; "
            "background-color: #1c1917; border-radius: 6px; "
            "border: 1px solid #78350f;"
        )
        policy_banner.setWordWrap(True)
        layout.addWidget(policy_banner)

        # Infos appareil
        info = QLabel(
            f"Appareil : {device_id}\n\n"
            f"Quota actuel : {current_quota} tentative(s)\n"
            f"Déjà utilisées : {attempts_used} tentative(s)\n"
            f"Reste à consommer : {max(0, current_quota - attempts_used)} essai(s)"
        )
        info.setStyleSheet(
            "color: #a5b4fc; font-size: 12px; padding: 14px; "
            "background-color: #111827; border-radius: 8px; "
            "border: 1px solid #4f46e5;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        instruction = QLabel("Choisissez le type d'accès à accorder :")
        instruction.setStyleSheet("color: #cbd5e1; font-size: 13px; font-weight: bold; padding-top: 6px;")
        layout.addWidget(instruction)

        # ═══════════ MODE 1 : ESSAI ═══════════
        self.radio_trial = QRadioButton("🎟 Essai supplémentaire (limité)")
        self.radio_trial.toggled.connect(self._update_preview)
        layout.addWidget(self.radio_trial)

        trial_row = QHBoxLayout()
        trial_row.setContentsMargins(30, 0, 0, 0)
        trial_row.addWidget(QLabel("Tentatives à ajouter :"))
        trial_row.addStretch()
        self.spin_trial_attempts = QSpinBox()
        self.spin_trial_attempts.setRange(1, 100)
        self.spin_trial_attempts.setValue(1)
        self.spin_trial_attempts.setFixedHeight(38)
        self.spin_trial_attempts.setFixedWidth(100)
        self.spin_trial_attempts.valueChanged.connect(self._update_preview)
        trial_row.addWidget(self.spin_trial_attempts)
        layout.addLayout(trial_row)

        trial_dur_row = QHBoxLayout()
        trial_dur_row.setContentsMargins(30, 0, 0, 8)
        trial_dur_row.addWidget(QLabel("Durée de l'essai :"))
        trial_dur_row.addStretch()
        self.spin_trial_days = QSpinBox()
        self.spin_trial_days.setRange(1, 999)
        self.spin_trial_days.setValue(default_trial_days)
        self.spin_trial_days.setFixedHeight(38)
        self.spin_trial_days.setFixedWidth(100)
        trial_dur_row.addWidget(self.spin_trial_days)
        self.combo_trial_unit = QComboBox()
        self.combo_trial_unit.addItems(["Jours", "Mois", "Ans"])
        idx = self.combo_trial_unit.findText(default_trial_unit)
        if idx >= 0:
            self.combo_trial_unit.setCurrentIndex(idx)
        self.combo_trial_unit.setFixedHeight(38)
        self.combo_trial_unit.setFixedWidth(110)
        trial_dur_row.addWidget(self.combo_trial_unit)
        layout.addLayout(trial_dur_row)

        # ═══════════ MODE 2 : ACCÈS COMPLET ═══════════
        self.radio_full = QRadioButton("⭐ Accès complet temporaire (toutes fonctions)")
        self.radio_full.toggled.connect(self._update_preview)
        layout.addWidget(self.radio_full)

        full_dur_row = QHBoxLayout()
        full_dur_row.setContentsMargins(30, 0, 0, 8)
        full_dur_row.addWidget(QLabel("Durée de l'accès :"))
        full_dur_row.addStretch()
        self.spin_full_days = QSpinBox()
        self.spin_full_days.setRange(1, 999)
        self.spin_full_days.setValue(default_full_days)
        self.spin_full_days.setFixedHeight(38)
        self.spin_full_days.setFixedWidth(100)
        full_dur_row.addWidget(self.spin_full_days)
        self.combo_full_unit = QComboBox()
        self.combo_full_unit.addItems(["Jours", "Mois", "Ans"])
        idx = self.combo_full_unit.findText(default_full_unit)
        if idx >= 0:
            self.combo_full_unit.setCurrentIndex(idx)
        self.combo_full_unit.setFixedHeight(38)
        self.combo_full_unit.setFixedWidth(110)
        full_dur_row.addWidget(self.combo_full_unit)
        layout.addLayout(full_dur_row)

        # Sélection par défaut
        if default_mode == "full":
            self.radio_full.setChecked(True)
        else:
            self.radio_trial.setChecked(True)

        # Preview
        self.lbl_preview = QLabel("")
        self.lbl_preview.setStyleSheet(
            "color: #34d399; font-size: 12.5px; font-weight: bold; "
            "padding: 12px; background-color: #0f172a; "
            "border-radius: 8px; border: 1px solid #10b981;"
        )
        self.lbl_preview.setWordWrap(True)
        layout.addWidget(self.lbl_preview)

        layout.addStretch()

        btns = QDialogButtonBox()
        btn_ok = QPushButton("Débloquer l'appareil")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Annuler")
        btn_cancel.setObjectName("btn_secondary")
        btn_cancel.clicked.connect(self.reject)
        btns.addButton(btn_ok, QDialogButtonBox.AcceptRole)
        btns.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(btns)

        self._current_quota = current_quota
        self._attempts_used = attempts_used
        self._update_preview()

    def _update_preview(self):
        is_trial_mode = self.radio_trial.isChecked()

        # Active/désactive les champs selon le mode
        self.spin_trial_attempts.setEnabled(is_trial_mode)
        self.spin_trial_days.setEnabled(is_trial_mode)
        self.combo_trial_unit.setEnabled(is_trial_mode)
        self.spin_full_days.setEnabled(not is_trial_mode)
        self.combo_full_unit.setEnabled(not is_trial_mode)

        if is_trial_mode:
            new_quota = max(0, self._current_quota) + self.spin_trial_attempts.value()
            remaining = max(0, new_quota - max(0, self._attempts_used))
            days = self.spin_trial_days.value()
            unit = self.combo_trial_unit.currentText()
            self.lbl_preview.setText(
                f"🎟 ESSAI SUPPLÉMENTAIRE\n"
                f"Nouveau quota : {new_quota} tentative(s)\n"
                f"Reste à consommer : {remaining} essai(s)\n"
                f"Durée : {days} {unit}\n"
                f"Limites : 1 tableau • 50 lignes • pas d'export"
            )
            self.lbl_preview.setStyleSheet(
                "color: #34d399; font-size: 12.5px; font-weight: bold; "
                "padding: 12px; background-color: #0f172a; "
                "border-radius: 8px; border: 1px solid #10b981;"
            )
        else:
            days = self.spin_full_days.value()
            unit = self.combo_full_unit.currentText()
            self.lbl_preview.setText(
                f"⭐ ACCÈS COMPLET TEMPORAIRE\n"
                f"Durée : {days} {unit}\n"
                f"Tableaux & lignes illimités\n"
                f"Exports PDF/Excel, imports, fusions, appels débloqués\n"
                f"Le device sera automatiquement débloqué."
            )
            self.lbl_preview.setStyleSheet(
                "color: #60a5fa; font-size: 12.5px; font-weight: bold; "
                "padding: 12px; background-color: #0f172a; "
                "border-radius: 8px; border: 1px solid #3b82f6;"
            )

    def get_payload(self) -> dict:
        if self.radio_trial.isChecked():
            return {
                "add_attempts": self.spin_trial_attempts.value(),
                "is_blocked": False,
                "grant_full_access": False,
            }
        else:
            return {
                "is_blocked": False,
                "grant_full_access": True,
                "duration_val": self.spin_full_days.value(),
                "duration_unit": self.combo_full_unit.currentText(),
            }


# ═══════════════════════════════════════════════════════════
# 🧵 THREADS
# ═══════════════════════════════════════════════════════════
class WorkerThread(QThread):
    finished_signal = Signal(bool, str, object)

    def __init__(self, target_func, *args, **kwargs):
        super().__init__()
        self.target_func = target_func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            ok, msg, data = self.target_func(*self.args, **self.kwargs)
            self.finished_signal.emit(ok, msg, data)
        except Exception as e:
            self.finished_signal.emit(False, str(e), None)


class PingWorker(QThread):
    ping_signal = Signal(bool, int, str)

    def run(self):
        try:
            start = time.time()
            resp = requests.get(f"{API_URL}/", timeout=5)
            ms = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                self.ping_signal.emit(True, ms, resp.json().get("database", "PostgreSQL"))
            else:
                self.ping_signal.emit(False, 0, f"Erreur {resp.status_code}")
        except Exception as e:
            self.ping_signal.emit(False, 0, str(e))


# ═══════════════════════════════════════════════════════════
# 🖥️  FENÊTRE PRINCIPALE
# ═══════════════════════════════════════════════════════════
class AdminGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SmartCollect — Administration")
        self.resize(1360, 880)
        self.setMinimumSize(1100, 720)

        self.threads: list = []
        self.current_news_list = []

        # ✅ Config étendue : durées par défaut essai + accès complet
        self.config = {
            "admin_api_key": "",
            "gemini_api_key": "",
            "default_trial_days": 7,
            "default_trial_unit": "Jours",
            "default_full_days": 7,
            "default_full_unit": "Jours",
            "default_unblock_mode": "trial",
        }

        self.setStyleSheet("""
            QMainWindow { background-color: #0b1120; }
            QWidget {
                font-family: 'Segoe UI', -apple-system, sans-serif;
                color: #f1f5f9;
            }
            QTabWidget::pane {
                border: 1px solid #1e293b;
                background-color: #0f172a;
                border-radius: 10px;
                padding: 10px;
            }
            QTabBar::tab {
                background-color: transparent;
                color: #94a3b8;
                padding: 8px 18px;
                font-weight: 600;
                border-radius: 6px;
                margin-right: 4px;
                font-size: 13px;
            }
            QTabBar::tab:hover { color: #ffffff; background-color: #1e293b; }
            QTabBar::tab:selected { background-color: #4f46e5; color: #ffffff; }
            QLineEdit, QComboBox, QSpinBox, QTextEdit {
                background-color: #111827;
                border: 1px solid #1f2937;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QPushButton {
                background-color: #4f46e5;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#btn_secondary {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #e2e8f0;
            }
            QPushButton#btn_danger { background-color: #991b1b; }
            QPushButton#btn_warning { background-color: #b45309; }
            QPushButton#btn_success { background-color: #047857; }
            QTableWidget {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 8px;
                gridline-color: #1e293b;
                color: #f8fafc;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #111827;
                color: #94a3b8;
                padding: 10px;
                border: none;
                border-bottom: 1px solid #1f2937;
                font-weight: 600;
                font-size: 12px;
            }
        """)

        self._setup_ui()
        self.load_config()

        self.ping_timer = QTimer(self)
        self.ping_timer.timeout.connect(self.check_server_status)
        self.ping_timer.start(10000)

        self.check_server_status()
        self.load_licenses()
        self.load_devices()
        self.load_news()
        self.refresh_kb_list()

        if not ADMIN_API_KEY:
            QTimer.singleShot(600, self._warn_missing_admin_key)

    def _warn_missing_admin_key(self):
        QMessageBox.warning(
            self, "Clé admin manquante",
            "La clé admin (SMARTCOLLECT_ADMIN_KEY) n'est pas définie.\n\n"
            "Toutes les opérations admin échoueront en 401.\n\n"
            "➡ Remplis l'onglet '🔑 Clé Admin' puis clique 💾 Sauvegarder."
        )
        if hasattr(self, "tabs"):
            for i in range(self.tabs.count()):
                if "Clé Admin" in self.tabs.tabText(i):
                    self.tabs.setCurrentIndex(i)
                    break

    # ═══════════════════════════════════════════════════════════
    # 🔐 HELPERS ADMIN AUTHENTIFIÉS
    # ═══════════════════════════════════════════════════════════
    def _admin_get(self, path: str, timeout: int = 12):
        r = requests.get(f"{API_URL}{path}", headers=_headers(), timeout=timeout)
        if r.status_code == 401:
            raise Exception("Clé admin invalide ou manquante (401).")
        if r.status_code == 503:
            raise Exception("Service admin désactivé côté serveur.")
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code} : {r.text[:200]}")
        return r.json()

    def _admin_post(self, path: str, payload: dict, timeout: int = 12):
        r = requests.post(f"{API_URL}{path}", json=payload, headers=_headers(), timeout=timeout)
        if r.status_code == 401:
            raise Exception("Clé admin invalide ou manquante (401).")
        if r.status_code not in (200, 201, 204):
            raise Exception(f"HTTP {r.status_code} : {r.text[:200]}")
        return r.json() if r.content else None

    def _admin_put(self, path: str, payload: dict, timeout: int = 12):
        r = requests.put(f"{API_URL}{path}", json=payload, headers=_headers(), timeout=timeout)
        if r.status_code == 401:
            raise Exception("Clé admin invalide ou manquante (401).")
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code} : {r.text[:200]}")
        return r.json() if r.content else None

    def _admin_delete(self, path: str, timeout: int = 12):
        r = requests.delete(f"{API_URL}{path}", headers=_headers(), timeout=timeout)
        if r.status_code == 401:
            raise Exception("Clé admin invalide ou manquante (401).")
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code} : {r.text[:200]}")
        return r.json() if r.content else None

    # ═══════════════════════════════════════════════════════════
    # ⚙️  CONFIG
    # ═══════════════════════════════════════════════════════════
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.config.update(loaded)
            except Exception:
                pass

        admin_key = self.config.get("admin_api_key", "") or ADMIN_API_KEY
        if admin_key:
            self.input_admin_key.setText(admin_key)

        gemini_key = self.config.get("gemini_api_key", "") or os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            self.input_gemini_key.setText(gemini_key)
            self.chk_save_api.setChecked(True)

        # Durée essai par défaut
        try:
            self.spin_default_trial_days.setValue(int(self.config.get("default_trial_days", 7)))
        except (ValueError, TypeError):
            self.spin_default_trial_days.setValue(7)
        unit = self.config.get("default_trial_unit", "Jours")
        idx = self.combo_default_trial_unit.findText(unit)
        if idx >= 0:
            self.combo_default_trial_unit.setCurrentIndex(idx)

        # ✅ Durée accès complet par défaut
        try:
            self.spin_default_full_days.setValue(int(self.config.get("default_full_days", 7)))
        except (ValueError, TypeError):
            self.spin_default_full_days.setValue(7)
        unit_full = self.config.get("default_full_unit", "Jours")
        idx_full = self.combo_default_full_unit.findText(unit_full)
        if idx_full >= 0:
            self.combo_default_full_unit.setCurrentIndex(idx_full)

        # ✅ Mode par défaut
        mode = self.config.get("default_unblock_mode", "trial")
        if mode == "full":
            self.radio_default_full.setChecked(True)
        else:
            self.radio_default_trial.setChecked(True)

        if hasattr(self, "lbl_active_duration"):
            self.lbl_active_duration.setText(
                f"{self.config.get('default_trial_days', 7)} "
                f"{self.config.get('default_trial_unit', 'Jours')}"
            )

    def save_config(self):
        if self.chk_save_api.isChecked():
            self.config["gemini_api_key"] = self.input_gemini_key.text().strip()
        else:
            self.config["gemini_api_key"] = ""

        self.config["default_trial_days"] = self.spin_default_trial_days.value()
        self.config["default_trial_unit"] = self.combo_default_trial_unit.currentText()
        self.config["default_full_days"] = self.spin_default_full_days.value()
        self.config["default_full_unit"] = self.combo_default_full_unit.currentText()
        self.config["default_unblock_mode"] = (
            "full" if self.radio_default_full.isChecked() else "trial"
        )

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Impossible de sauvegarder : {e}")

    def apply_default_trial_config(self):
        self.save_config()
        days = self.spin_default_trial_days.value()
        unit = self.combo_default_trial_unit.currentText()
        full_days = self.spin_default_full_days.value()
        full_unit = self.combo_default_full_unit.currentText()
        mode = "Accès complet" if self.radio_default_full.isChecked() else "Essai"

        if hasattr(self, "lbl_active_duration"):
            self.lbl_active_duration.setText(f"{days} {unit}")

        QMessageBox.information(
            self, "Configuration enregistrée",
            f"✅ Configuration de déblocage enregistrée :\n\n"
            f"🎟 Essai par défaut : {days} {unit}\n"
            f"⭐ Accès complet par défaut : {full_days} {full_unit}\n"
            f"🎯 Mode par défaut : {mode}"
        )

    def refresh_music_list(self):
        if hasattr(self, "music_player"):
            self.music_player.refresh_playlist()

    def play_music(self, path):
        if hasattr(self, "music_player"):
            self.music_player.play_music(path)

    # ═══════════════════════════════════════════════════════════
    # 🎨 INTERFACE
    # ═══════════════════════════════════════════════════════════
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(12)

        top_bar = QFrame()
        top_bar.setStyleSheet("""
            QFrame {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 10px;
            }
        """)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 8, 14, 8)
        top_layout.setSpacing(14)

        title = QLabel("SmartCollect")
        title.setFont(QFont("Segoe UI", 15, QFont.Bold))
        title.setStyleSheet("color: #ffffff;")
        top_layout.addWidget(title)

        status_badge = QFrame()
        status_badge.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 6px;")
        s_layout = QHBoxLayout(status_badge)
        s_layout.setContentsMargins(8, 4, 8, 4)
        s_layout.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #64748b; font-size: 11px;")
        s_layout.addWidget(self.status_dot)

        self.status_text = QLabel("Vérification...")
        self.status_text.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;")
        s_layout.addWidget(self.status_text)
        top_layout.addWidget(status_badge)

        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setObjectName("btn_secondary")
        self.btn_refresh.setFixedWidth(32)
        self.btn_refresh.clicked.connect(self.manual_refresh)
        top_layout.addWidget(self.btn_refresh)

        self.music_player = MusicPlayerWidget(self)
        top_layout.addWidget(self.music_player, 1)

        main_layout.addWidget(top_bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_news_tab(), "Actualités & Diffusion")
        self.tabs.addTab(self._build_licenses_tab(), "Licences WhatsApp")
        self.tabs.addTab(self._build_devices_tab(), "📱 Appareils & Essais")
        self.tabs.addTab(self._build_admin_key_tab(), "🔑 Clé Admin")
        self.tabs.addTab(self._build_ai_tab(), "Génération IA (Gemini)")
        self.tabs.addTab(self._build_kb_tab(), "Base Documentaire (.txt)")
        main_layout.addWidget(self.tabs, 1)

    def _build_admin_key_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #111827;
                border: 1px solid #4f46e5;
                border-radius: 8px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(8)

        info_title = QLabel("🔑 Clé d'authentification admin")
        info_title.setStyleSheet("color: #a5b4fc; font-size: 14px; font-weight: bold; border: none;")
        info_layout.addWidget(info_title)

        info_text = QLabel(
            "Cette clé doit être IDENTIQUE à la variable ADMIN_API_KEY configurée "
            "sur Render. Elle protège toutes les opérations d'administration."
        )
        info_text.setStyleSheet("color: #cbd5e1; font-size: 12px; border: none;")
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)

        layout.addWidget(info_frame)

        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        fl = QVBoxLayout(form_frame)
        fl.setContentsMargins(14, 12, 14, 12)
        fl.setSpacing(10)

        row = QHBoxLayout()
        self.input_admin_key = QLineEdit()
        self.input_admin_key.setEchoMode(QLineEdit.Password)
        self.input_admin_key.setPlaceholderText("Colle ici la clé admin (identique à Render)")
        self.input_admin_key.setFixedHeight(40)
        row.addWidget(self.input_admin_key, 1)
        fl.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_show = QPushButton("👁 Afficher")
        btn_show.setObjectName("btn_secondary")
        btn_show.clicked.connect(self._toggle_admin_key_visibility)
        btn_row.addWidget(btn_show)

        btn_test = QPushButton("🧪 Tester la connexion")
        btn_test.setObjectName("btn_success")
        btn_test.clicked.connect(self._test_admin_key)
        btn_row.addWidget(btn_test)

        btn_save = QPushButton("💾 Sauvegarder")
        btn_save.clicked.connect(self._save_admin_key)
        btn_row.addWidget(btn_save)

        fl.addLayout(btn_row)
        layout.addWidget(form_frame)

        layout.addStretch()
        return tab

    def _toggle_admin_key_visibility(self):
        if self.input_admin_key.echoMode() == QLineEdit.Password:
            self.input_admin_key.setEchoMode(QLineEdit.Normal)
        else:
            self.input_admin_key.setEchoMode(QLineEdit.Password)

    def _save_admin_key(self):
        global ADMIN_API_KEY
        key = self.input_admin_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Erreur", "La clé ne peut pas être vide.")
            return

        ADMIN_API_KEY = key
        self.config["admin_api_key"] = key

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Sauvegarde impossible : {e}")
            return

        QMessageBox.information(
            self, "Clé enregistrée",
            "La clé admin a été sauvegardée dans config.json."
        )
        self.load_licenses()
        self.load_devices()

    def _test_admin_key(self):
        key = self.input_admin_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Erreur", "Saisissez une clé.")
            return

        def req():
            try:
                r = requests.get(
                    f"{API_URL}/api/admin/licenses",
                    headers={"X-Admin-Key": key},
                    timeout=10,
                )
                if r.status_code == 401:
                    return False, "Clé invalide (401).", None
                if r.status_code == 503:
                    return False, "Service admin désactivé (503).", None
                if r.status_code != 200:
                    return False, f"HTTP {r.status_code}", None
                return True, "OK", None
            except Exception as e:
                return False, str(e), None

        def on_finish(ok, msg, _):
            if ok:
                QMessageBox.information(self, "✅ Succès", "La clé admin est VALIDE !")
            else:
                QMessageBox.critical(self, "❌ Échec", f"Clé invalide ou problème serveur :\n{msg}")

        self._start_worker(req, lambda _: None, on_finish=on_finish)

    def _build_news_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        fl = QVBoxLayout(form_frame)
        fl.setContentsMargins(14, 12, 14, 12)
        fl.setSpacing(10)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.news_title = QLineEdit()
        self.news_title.setPlaceholderText("Titre de l'actualité...")
        self.news_title.setFixedHeight(36)
        r1.addWidget(self.news_title, 4)

        self.news_category = QComboBox()
        self.news_category.addItems([
            "Mise à jour (update)", "Astuce & Conseil (tip)",
            "Actualité générale (news)", "Alerte (alert)"
        ])
        self.news_category.setFixedHeight(36)
        r1.addWidget(self.news_category, 2)

        self.news_version = QLineEdit()
        self.news_version.setPlaceholderText("Version (ex: 2.1.0)")
        self.news_version.setFixedHeight(36)
        r1.addWidget(self.news_version, 1)
        fl.addLayout(r1)

        self.news_summary = QLineEdit()
        self.news_summary.setPlaceholderText("Résumé court...")
        self.news_summary.setFixedHeight(36)
        fl.addWidget(self.news_summary)

        self.news_content = QTextEdit()
        self.news_content.setPlaceholderText("Contenu bilingue ou descriptif complet...")
        self.news_content.setFixedHeight(85)
        fl.addWidget(self.news_content)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.news_url = QLineEdit()
        self.news_url.setPlaceholderText("Lien de téléchargement (optionnel)")
        self.news_url.setFixedHeight(36)
        r2.addWidget(self.news_url, 4)

        self.btn_publish_news = QPushButton("Publier l'actualité")
        self.btn_publish_news.setFixedHeight(36)
        self.btn_publish_news.clicked.connect(self.publish_news)
        r2.addWidget(self.btn_publish_news, 1)
        fl.addLayout(r2)

        layout.addWidget(form_frame)

        self.news_table = QTableWidget()
        self.news_table.setColumnCount(5)
        self.news_table.setHorizontalHeaderLabels(["ID", "Catégorie", "Titre (FR / EN)", "Version", "Date"])
        self.news_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.news_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.news_table.setSelectionMode(QTableWidget.SingleSelection)
        self.news_table.verticalHeader().setVisible(False)
        self.news_table.horizontalHeader().setStretchLastSection(True)

        nh = self.news_table.horizontalHeader()
        nh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        nh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        nh.setSectionResizeMode(2, QHeaderView.Stretch)
        nh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        nh.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        layout.addWidget(self.news_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        btn_del_all = QPushButton("Purger tout")
        btn_del_all.setObjectName("btn_danger")
        btn_del_all.clicked.connect(self.delete_all_news)
        actions.addWidget(btn_del_all)

        actions.addStretch()

        btn_del_one = QPushButton("Supprimer sélection")
        btn_del_one.setObjectName("btn_danger")
        btn_del_one.clicked.connect(self.delete_news)
        actions.addWidget(btn_del_one)

        layout.addLayout(actions)
        return tab

    def _build_licenses_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # ═══════════ CONFIG DURÉE ESSAI ═══════════
        config_frame = QFrame()
        config_frame.setStyleSheet("""
            QFrame {
                background-color: #111827;
                border: 1px solid #4f46e5;
                border-radius: 8px;
            }
        """)
        cf_layout = QVBoxLayout(config_frame)
        cf_layout.setContentsMargins(14, 12, 14, 12)
        cf_layout.setSpacing(10)

        config_title = QLabel("⚙  Configuration des déblocages par défaut")
        config_title.setStyleSheet("color: #a5b4fc; font-size: 13px; font-weight: bold; border: none;")
        cf_layout.addWidget(config_title)

        # Ligne 1 : Essai
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("🎟 Durée essai :"))

        self.spin_default_trial_days = QSpinBox()
        self.spin_default_trial_days.setRange(1, 999)
        self.spin_default_trial_days.setValue(7)
        self.spin_default_trial_days.setFixedHeight(36)
        self.spin_default_trial_days.setFixedWidth(90)
        row1.addWidget(self.spin_default_trial_days)

        self.combo_default_trial_unit = QComboBox()
        self.combo_default_trial_unit.addItems(["Jours", "Mois", "Ans"])
        self.combo_default_trial_unit.setFixedHeight(36)
        self.combo_default_trial_unit.setFixedWidth(110)
        row1.addWidget(self.combo_default_trial_unit)
        row1.addStretch()
        cf_layout.addLayout(row1)

        # Ligne 2 : Accès complet
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(QLabel("⭐ Durée accès complet :"))

        self.spin_default_full_days = QSpinBox()
        self.spin_default_full_days.setRange(1, 999)
        self.spin_default_full_days.setValue(7)
        self.spin_default_full_days.setFixedHeight(36)
        self.spin_default_full_days.setFixedWidth(90)
        row2.addWidget(self.spin_default_full_days)

        self.combo_default_full_unit = QComboBox()
        self.combo_default_full_unit.addItems(["Jours", "Mois", "Ans"])
        self.combo_default_full_unit.setFixedHeight(36)
        self.combo_default_full_unit.setFixedWidth(110)
        row2.addWidget(self.combo_default_full_unit)
        row2.addStretch()
        cf_layout.addLayout(row2)

        # Ligne 3 : Mode par défaut + bouton save
        row3 = QHBoxLayout()
        row3.setSpacing(10)
        row3.addWidget(QLabel("🎯 Mode par défaut :"))

        self.radio_default_trial = QRadioButton("Essai")
        self.radio_default_trial.setChecked(True)
        row3.addWidget(self.radio_default_trial)

        self.radio_default_full = QRadioButton("Accès complet")
        row3.addWidget(self.radio_default_full)

        row3.addStretch()

        self.btn_save_trial_config = QPushButton("💾 Enregistrer la configuration")
        self.btn_save_trial_config.setObjectName("btn_success")
        self.btn_save_trial_config.setFixedHeight(36)
        self.btn_save_trial_config.clicked.connect(self.apply_default_trial_config)
        row3.addWidget(self.btn_save_trial_config)

        cf_layout.addLayout(row3)
        layout.addWidget(config_frame)

        # ═══════════ CRÉATION DE LICENCE ═══════════
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        cl = QHBoxLayout(form_frame)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        self.input_phone = QLineEdit()
        self.input_phone.setPlaceholderText("Numéro WhatsApp (+224...)")
        self.input_phone.setFixedHeight(36)
        cl.addWidget(self.input_phone, 3)

        self.lbl_active_duration = QLabel("7 Jours")
        self.lbl_active_duration.setStyleSheet(
            "color: #34d399; font-size: 13px; font-weight: bold; "
            "padding: 8px 12px; background-color: #0f172a; "
            "border: 1px solid #334155; border-radius: 6px;"
        )
        self.lbl_active_duration.setFixedHeight(36)
        self.lbl_active_duration.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.lbl_active_duration, 2)

        self.spin_devices = QSpinBox()
        self.spin_devices.setRange(1, 50)
        self.spin_devices.setValue(1)
        self.spin_devices.setPrefix("Max : ")
        self.spin_devices.setFixedHeight(36)
        cl.addWidget(self.spin_devices, 1)

        self.btn_generate = QPushButton("Générer la clé")
        self.btn_generate.setFixedHeight(36)
        self.btn_generate.clicked.connect(self.create_license)
        cl.addWidget(self.btn_generate, 1)

        layout.addWidget(form_frame)

        # ═══════════ TABLE LICENCES ═══════════
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "ID", "Clé d'Activation", "Téléphone", "Client",
            "Organisation", "Appareils", "Durée", "Mode", "Statut", "Expiration"
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.edit_license)

        lh = self.table.horizontalHeader()
        for i in range(10):
            lh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lh.setSectionResizeMode(3, QHeaderView.Stretch)

        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        btn_edit = QPushButton("✏ Modifier")
        btn_edit.setObjectName("btn_secondary")
        btn_edit.clicked.connect(self.edit_license)
        actions.addWidget(btn_edit)

        btn_copy = QPushButton("Copier la clé")
        btn_copy.setObjectName("btn_secondary")
        btn_copy.clicked.connect(self.copy_selected_key)
        actions.addWidget(btn_copy)

        btn_full = QPushButton("⭐ Accès complet")
        btn_full.setObjectName("btn_success")
        btn_full.clicked.connect(self.grant_full_access)
        actions.addWidget(btn_full)

        btn_reset_trial = QPushButton("↩ Remettre en essai")
        btn_reset_trial.setObjectName("btn_warning")
        btn_reset_trial.clicked.connect(self.reset_to_trial)
        actions.addWidget(btn_reset_trial)

        btn_toggle = QPushButton("Activer / Révoquer")
        btn_toggle.setObjectName("btn_warning")
        btn_toggle.clicked.connect(self.toggle_status)
        actions.addWidget(btn_toggle)

        btn_reset = QPushButton("Dissocier Appareils")
        btn_reset.setObjectName("btn_secondary")
        btn_reset.clicked.connect(self.reset_devices)
        actions.addWidget(btn_reset)

        actions.addStretch()

        btn_del = QPushButton("Supprimer")
        btn_del.setObjectName("btn_danger")
        btn_del.clicked.connect(self.delete_license)
        actions.addWidget(btn_del)

        layout.addLayout(actions)
        return tab

    def _build_devices_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #111827;
                border: 1px solid #4f46e5;
                border-radius: 8px;
            }
        """)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(14, 10, 14, 10)
        info_layout.setSpacing(10)

        info_icon = QLabel("📱")
        info_icon.setStyleSheet("font-size: 20px; border: none;")
        info_layout.addWidget(info_icon)

        info_text = QLabel(
            "📌 Politique : 1 essai par appareil. "
            "Utilisez 'Débloquer' pour accorder soit un essai supplémentaire (limité), "
            "soit un accès complet temporaire (toutes fonctions)."
        )
        info_text.setStyleSheet("color: #a5b4fc; font-size: 12px; border: none;")
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text, 1)

        self.btn_refresh_devices = QPushButton("↻ Actualiser")
        self.btn_refresh_devices.setObjectName("btn_secondary")
        self.btn_refresh_devices.setFixedHeight(32)
        self.btn_refresh_devices.clicked.connect(self.load_devices)
        info_layout.addWidget(self.btn_refresh_devices)

        layout.addWidget(info_frame)

        filter_frame = QFrame()
        filter_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        fl = QHBoxLayout(filter_frame)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(10)

        fl.addWidget(QLabel("🔍 Rechercher :"))
        self.input_device_search = QLineEdit()
        self.input_device_search.setPlaceholderText("device_id, téléphone, note...")
        self.input_device_search.setFixedHeight(34)
        self.input_device_search.textChanged.connect(self._filter_devices_table)
        fl.addWidget(self.input_device_search, 1)

        fl.addWidget(QLabel("Statut :"))
        self.combo_device_filter = QComboBox()
        self.combo_device_filter.addItems([
            "Tous", "🚫 Bloqués", "✅ Débloqués",
            "🔓 Autorisés par admin", "⚠ Multi-tentatives",
        ])
        self.combo_device_filter.setFixedHeight(34)
        self.combo_device_filter.currentIndexChanged.connect(self._filter_devices_table)
        fl.addWidget(self.combo_device_filter)

        layout.addWidget(filter_frame)

        self.devices_table = QTableWidget()
        self.devices_table.setColumnCount(9)
        self.devices_table.setHorizontalHeaderLabels([
            "ID", "Device ID (fingerprint)", "Téléphone", "Utilisées / Quota",
            "Bloqué", "Débloqué admin", "Raison / Notes",
            "1ère tentative", "Dernière tentative",
        ])
        self.devices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.devices_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.devices_table.setSelectionMode(QTableWidget.SingleSelection)
        self.devices_table.verticalHeader().setVisible(False)
        self.devices_table.horizontalHeader().setStretchLastSection(True)

        dh = self.devices_table.horizontalHeader()
        dh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        dh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, 9):
            dh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        dh.setSectionResizeMode(6, QHeaderView.Stretch)

        layout.addWidget(self.devices_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        btn_unblock = QPushButton("✅ Débloquer le device")
        btn_unblock.setObjectName("btn_success")
        btn_unblock.setFixedHeight(36)
        btn_unblock.clicked.connect(self.unblock_device)
        actions.addWidget(btn_unblock)

        btn_block = QPushButton("🚫 Bloquer le device")
        btn_block.setObjectName("btn_warning")
        btn_block.setFixedHeight(36)
        btn_block.clicked.connect(self.block_device)
        actions.addWidget(btn_block)

        btn_copy_device = QPushButton("📋 Copier le Device ID")
        btn_copy_device.setObjectName("btn_secondary")
        btn_copy_device.setFixedHeight(36)
        btn_copy_device.clicked.connect(self.copy_device_id)
        actions.addWidget(btn_copy_device)

        btn_notes = QPushButton("📝 Modifier les notes")
        btn_notes.setObjectName("btn_secondary")
        btn_notes.setFixedHeight(36)
        btn_notes.clicked.connect(self.edit_device_notes)
        actions.addWidget(btn_notes)

        actions.addStretch()

        btn_delete = QPushButton("🗑 Supprimer la trace")
        btn_delete.setObjectName("btn_danger")
        btn_delete.setFixedHeight(36)
        btn_delete.clicked.connect(self.delete_device)
        actions.addWidget(btn_delete)

        layout.addLayout(actions)
        return tab

    def _build_ai_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        fl = QVBoxLayout(form_frame)
        fl.setContentsMargins(14, 12, 14, 12)
        fl.setSpacing(10)

        k_row = QHBoxLayout()
        self.input_gemini_key = QLineEdit()
        self.input_gemini_key.setEchoMode(QLineEdit.Password)
        self.input_gemini_key.setPlaceholderText("Clé API Google Gemini...")
        self.input_gemini_key.setFixedHeight(36)
        k_row.addWidget(self.input_gemini_key, 1)

        self.chk_save_api = QCheckBox("Sauvegarder")
        self.chk_save_api.stateChanged.connect(self.save_config)
        k_row.addWidget(self.chk_save_api)
        fl.addLayout(k_row)

        c_row = QHBoxLayout()
        self.combo_context_file = QComboBox()
        self.combo_context_file.setFixedHeight(36)
        c_row.addWidget(self.combo_context_file, 1)

        btn_refresh_kb = QPushButton("↻ Actualiser")
        btn_refresh_kb.setObjectName("btn_secondary")
        btn_refresh_kb.setFixedHeight(36)
        btn_refresh_kb.clicked.connect(self.refresh_kb_list)
        c_row.addWidget(btn_refresh_kb)
        fl.addLayout(c_row)

        self.ai_prompt = QTextEdit()
        self.ai_prompt.setPlaceholderText("Décrivez ce que vous souhaitez diffuser...")
        self.ai_prompt.setFixedHeight(85)
        fl.addWidget(self.ai_prompt)

        run_row = QHBoxLayout()
        self.ai_progress = QProgressBar()
        self.ai_progress.setVisible(False)
        self.ai_progress.setRange(0, 0)
        self.ai_progress.setFixedHeight(24)
        run_row.addWidget(self.ai_progress, 1)

        self.btn_run_ai = QPushButton("✨ Générer & Publier")
        self.btn_run_ai.setFixedHeight(36)
        self.btn_run_ai.clicked.connect(self.process_ai_prompt)
        run_row.addWidget(self.btn_run_ai)
        fl.addLayout(run_row)

        layout.addWidget(form_frame)

        self.ai_log = QTextEdit()
        self.ai_log.setReadOnly(True)
        self.ai_log.setPlaceholderText("Journal de publication...")
        layout.addWidget(self.ai_log, 1)

        return tab

    def _build_kb_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #111827; border: 1px solid #1f2937; border-radius: 8px;")
        il = QVBoxLayout(form_frame)
        il.setContentsMargins(14, 12, 14, 12)
        il.setSpacing(8)

        self.kb_input_text = QTextEdit()
        self.kb_input_text.setPlaceholderText("Collez ici un texte ou une documentation...")
        self.kb_input_text.setFixedHeight(85)
        il.addWidget(self.kb_input_text)

        btn_save = QPushButton("Enregistrer dans la base (.txt)")
        btn_save.setFixedHeight(36)
        btn_save.clicked.connect(self.save_and_name_kb_doc)
        il.addWidget(btn_save)

        layout.addWidget(form_frame)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Trier par :"))
        self.combo_sort_field = QComboBox()
        self.combo_sort_field.addItems(["Date", "Nom"])
        self.combo_sort_field.currentIndexChanged.connect(self.refresh_kb_list)
        filter_row.addWidget(self.combo_sort_field)

        self.combo_sort_order = QComboBox()
        self.combo_sort_order.addItems(["Décroissant", "Croissant"])
        self.combo_sort_order.currentIndexChanged.connect(self.refresh_kb_list)
        filter_row.addWidget(self.combo_sort_order)

        filter_row.addStretch()

        btn_del_kb = QPushButton("Supprimer le fichier")
        btn_del_kb.setObjectName("btn_danger")
        btn_del_kb.clicked.connect(self.delete_selected_kb_file)
        filter_row.addWidget(btn_del_kb)

        layout.addLayout(filter_row)

        self.kb_table = QTableWidget()
        self.kb_table.setColumnCount(3)
        self.kb_table.setHorizontalHeaderLabels(["Nom du Fichier", "Taille (Ko)", "Dernière Modification"])
        self.kb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.kb_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.kb_table.setSelectionMode(QTableWidget.SingleSelection)
        self.kb_table.verticalHeader().setVisible(False)
        self.kb_table.horizontalHeader().setStretchLastSection(True)

        kbh = self.kb_table.horizontalHeader()
        kbh.setSectionResizeMode(0, QHeaderView.Stretch)
        kbh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        kbh.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        layout.addWidget(self.kb_table, 1)
        return tab

    # ═══════════════════════════════════════════════════════════
    # 🔄 STATUS SERVEUR
    # ═══════════════════════════════════════════════════════════
    def check_server_status(self):
        worker = PingWorker()
        worker.ping_signal.connect(self._on_ping_result)
        worker.finished.connect(lambda w=worker: self._cleanup_thread(w))
        self.threads.append(worker)
        worker.start()

    def _cleanup_thread(self, w):
        if w in self.threads:
            self.threads.remove(w)

    def _on_ping_result(self, ok, ms, db_name):
        if ok:
            self.status_dot.setStyleSheet("color: #10b981; font-size: 11px;")
            self.status_text.setText(f"En ligne ({ms} ms)")
            self.status_text.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
        else:
            self.status_dot.setStyleSheet("color: #ef4444; font-size: 11px;")
            self.status_text.setText("Hors-ligne")
            self.status_text.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold;")

    def manual_refresh(self):
        self.check_server_status()
        self.load_licenses()
        self.load_devices()
        self.load_news()
        self.refresh_kb_list()

    # ═══════════════════════════════════════════════════════════
    # 🧵 WORKER DISPATCHER
    # ═══════════════════════════════════════════════════════════
    def _start_worker(self, fn, callback=None, *args, on_finish=None, **kwargs):
        w = WorkerThread(fn, *args, **kwargs)

        def handler(ok, msg, data):
            self._cleanup_thread(w)
            if on_finish is not None:
                on_finish(ok, msg, data)
                return
            if not ok:
                QMessageBox.critical(self, "Erreur", f"Échec de l'opération :\n{msg}")
                return
            if callback:
                callback(data)

        w.finished_signal.connect(handler)
        self.threads.append(w)
        w.start()

    # ═══════════════════════════════════════════════════════════
    # 📜 LICENCES
    # ═══════════════════════════════════════════════════════════
    def load_licenses(self):
        def req():
            try:
                data = self._admin_get("/api/admin/licenses")
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, self._on_licenses_loaded)

    def _on_licenses_loaded(self, data):
        self.table.setRowCount(0)
        if not isinstance(data, list):
            return
        for row, lic in enumerate(data):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 36)
            is_active = bool(lic.get("is_active", True))
            is_trial = bool(lic.get("is_trial", True))

            duration_str = self._compute_duration(lic)
            remaining_str = self._compute_remaining(lic)
            mode_str = "🎟 Essai" if is_trial else "⭐ Complet"

            cols = [
                str(lic.get("id", "")),
                str(lic.get("key", "")),
                str(lic.get("phone_number", "—")),
                str(lic.get("user_name", "Non activé")),
                str(lic.get("organization", "—")),
                f"{lic.get('used_devices', 0)} / {lic.get('max_devices', 1)}",
                duration_str,
                mode_str,
                "● ACTIF" if is_active else "● RÉVOQUÉ",
                str(lic.get("expires_at") or "")[:19].replace("T", " ") or "Non activée"
            ]

            for col_idx, text in enumerate(cols):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignCenter)
                if col_idx == 1:
                    item.setFont(QFont("Consolas", 10, QFont.Bold))
                    item.setForeground(QColor("#818cf8"))
                elif col_idx == 6:
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    if remaining_str is None:
                        item.setForeground(QColor("#94a3b8"))
                    elif remaining_str.startswith("Expiré"):
                        item.setForeground(QColor("#f87171"))
                    else:
                        item.setForeground(QColor("#34d399"))
                elif col_idx == 7:
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    item.setForeground(QColor("#fbbf24") if is_trial else QColor("#34d399"))
                elif col_idx == 8:
                    item.setForeground(QColor("#34d399") if is_active else QColor("#f87171"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                self.table.setItem(row, col_idx, item)

    def _compute_duration(self, lic: dict) -> str:
        val = lic.get("duration_val")
        unit = lic.get("duration_unit")
        if val is not None and unit:
            try:
                val = int(val)
                u = str(unit).lower()
                if u.startswith("jour"):
                    days = val
                elif u.startswith("mois"):
                    days = val * 30
                elif u.startswith("an"):
                    days = val * 365
                else:
                    days = val
                return self._format_days(days)
            except Exception:
                pass
        days = lic.get("duration_days")
        if days is not None:
            try:
                return self._format_days(int(days))
            except Exception:
                pass
        return "—"

    def _format_days(self, days: int) -> str:
        if days <= 0:
            return "0 j"
        if days < 30:
            return f"{days} j"
        if days < 365:
            months = days // 30
            rem = days % 30
            return f"{months} mois" + (f" {rem} j" if rem else "")
        years = days // 365
        rem_days = days % 365
        months = rem_days // 30
        result = f"{years} an" + ("s" if years > 1 else "")
        if months:
            result += f" {months} mois"
        return result

    def _compute_remaining(self, lic: dict):
        expires = lic.get("expires_at")
        if not expires:
            return None
        try:
            e = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            delta = e - now
            days = delta.days
            if days < 0:
                return f"Expiré ({abs(days)} j)"
            if days == 0:
                return "< 1 j"
            return f"{days} j"
        except Exception:
            return None

    def copy_selected_key(self):
        row = self.table.currentRow()
        if row >= 0:
            key = self.table.item(row, 1).text()
            QApplication.clipboard().setText(key)
            QMessageBox.information(self, "Succès", f"Clé copiée : {key}")

    def create_license(self):
        phone = self.input_phone.text().strip()
        if not phone:
            QMessageBox.warning(self, "Erreur", "Saisissez un numéro WhatsApp.")
            return

        days = self.spin_default_trial_days.value()
        unit = self.combo_default_trial_unit.currentText()

        payload = {
            "phone_number": phone,
            "duration_val": days,
            "duration_unit": unit,
            "max_devices": self.spin_devices.value(),
            "is_active": False,
        }

        def req():
            try:
                data = self._admin_post("/api/admin/licenses/create", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.input_phone.clear()
            self.load_licenses()
            QMessageBox.information(
                self, "Clé créée",
                f"Clé créée en mode INACTIF.\nDurée : {days} {unit}."
            )
        self._start_worker(req, on_done)

    def edit_license(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez une licence.")
            return

        max_dev = 1
        try:
            raw_dev = self.table.item(row, 5).text()
            if "/" in raw_dev:
                max_dev = int(raw_dev.split("/")[1].strip())
        except Exception:
            max_dev = 1

        license_data = {
            "id": self.table.item(row, 0).text(),
            "key": self.table.item(row, 1).text(),
            "phone_number": self.table.item(row, 2).text(),
            "user_name": self.table.item(row, 3).text(),
            "organization": self.table.item(row, 4).text(),
            "max_devices": max_dev,
            "is_active": "ACTIF" in self.table.item(row, 8).text(),
            "expires_at": self.table.item(row, 9).text(),
        }

        dlg = EditLicenseDialog(license_data, self)
        if dlg.exec() != QDialog.Accepted:
            return

        payload = dlg.get_payload()
        key = license_data["key"]

        def req():
            try:
                data = self._admin_put(f"/api/admin/licenses/{key}", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_licenses()
            QMessageBox.information(self, "Succès", f"Licence {key} mise à jour.")
        self._start_worker(req, on_done)

    def grant_full_access(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez une licence.")
            return

        key = self.table.item(row, 1).text()
        dlg = GrantFullAccessDialog(key, self)
        if dlg.exec() != QDialog.Accepted:
            return

        val, unit = dlg.get_values()
        payload = {"duration_val": val, "duration_unit": unit}

        def req():
            try:
                data = self._admin_post(f"/api/admin/licenses/{key}/grant-full-access", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_licenses()
            QMessageBox.information(self, "Accès complet", f"Licence {key} → {val} {unit}.")
        self._start_worker(req, on_done)

    def reset_to_trial(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez une licence.")
            return

        key = self.table.item(row, 1).text()
        dlg = ResetToTrialDialog(key, self)
        if dlg.exec() != QDialog.Accepted:
            return

        val, unit = dlg.get_values()
        confirm = QMessageBox.question(
            self, "Confirmation",
            f"Remettre {key} en essai pour {val} {unit} ?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        payload = {"duration_val": val, "duration_unit": unit}

        def req():
            try:
                data = self._admin_post(f"/api/admin/licenses/{key}/reset-to-trial", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_licenses()
            QMessageBox.information(self, "Remise en essai", f"Licence {key} est en mode essai.")
        self._start_worker(req, on_done)

    def toggle_status(self):
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 1).text()

        def req():
            try:
                data = self._admin_post(f"/api/admin/licenses/{key}/status", {})
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, lambda _: self.load_licenses())

    def reset_devices(self):
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 1).text()

        def req():
            try:
                data = self._admin_post(f"/api/admin/licenses/{key}/reset-devices", {})
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, lambda _: self.load_licenses())

    def delete_license(self):
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 1).text()
        if QMessageBox.question(
            self, "Supprimer",
            f"Supprimer définitivement {key} ?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        def req():
            try:
                data = self._admin_delete(f"/api/admin/licenses/{key}")
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, lambda _: self.load_licenses())

    # ═══════════════════════════════════════════════════════════
    # 📱 APPAREILS
    # ═══════════════════════════════════════════════════════════
    def load_devices(self):
        def req():
            try:
                data = self._admin_get("/api/admin/devices")
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, self._on_devices_loaded)

    def _on_devices_loaded(self, data):
        if not hasattr(self, "devices_table"):
            return
        self.devices_table.setRowCount(0)
        if not isinstance(data, list):
            return

        for row, dev in enumerate(data):
            self.devices_table.insertRow(row)
            self.devices_table.setRowHeight(row, 36)

            is_blocked = bool(dev.get("is_blocked", False))
            admin_unblocked = bool(dev.get("admin_unblocked", False))
            attempts = int(dev.get("attempts_count", 0) or 0)
            max_allowed = int(dev.get("max_attempts_allowed", 0) or 0)

            first_att = str(dev.get("first_attempt_at", "") or "").replace("T", " ")[:19]
            last_att = str(dev.get("last_attempt_at", "") or "").replace("T", " ")[:19]

            block_reason = str(dev.get("block_reason", "") or "").strip()
            notes = str(dev.get("notes", "") or "").strip()
            raison_parts = []
            if block_reason:
                raison_parts.append(f"🚫 {block_reason}")
            if notes:
                raison_parts.append(f"📝 {notes}")
            raison_str = " — ".join(raison_parts) if raison_parts else "—"

            attempts_display = f"{attempts} / {max_allowed}"

            cols = [
                str(dev.get("id", "")),
                str(dev.get("device_id", "")),
                str(dev.get("phone_number", "—")),
                attempts_display,
                "🚫 OUI" if is_blocked else "✅ NON",
                "🔓 OUI" if admin_unblocked else "—",
                raison_str,
                first_att,
                last_att,
            ]

            for c_idx, val in enumerate(cols):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignCenter)

                if c_idx == 1:
                    item.setFont(QFont("Consolas", 9))
                    item.setForeground(QColor("#a78bfa"))
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                elif c_idx == 3:
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    if is_blocked or (max_allowed > 0 and attempts >= max_allowed):
                        item.setForeground(QColor("#f87171"))
                    elif attempts > 0 and max_allowed > attempts:
                        item.setForeground(QColor("#fbbf24"))
                    elif max_allowed > 0:
                        item.setForeground(QColor("#34d399"))
                    else:
                        item.setForeground(QColor("#64748b"))
                elif c_idx == 4:
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    item.setForeground(QColor("#f87171") if is_blocked else QColor("#34d399"))
                elif c_idx == 5:
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                    item.setForeground(QColor("#60a5fa") if admin_unblocked else QColor("#64748b"))
                elif c_idx == 6:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    item.setForeground(QColor("#94a3b8"))

                self.devices_table.setItem(row, c_idx, item)

        self._filter_devices_table()

    def _filter_devices_table(self):
        if not hasattr(self, "devices_table"):
            return
        search = self.input_device_search.text().strip().lower()
        filter_idx = self.combo_device_filter.currentIndex()

        for row in range(self.devices_table.rowCount()):
            show = True
            if search:
                match = False
                for col in range(self.devices_table.columnCount()):
                    item = self.devices_table.item(row, col)
                    if item and search in item.text().lower():
                        match = True
                        break
                show = show and match

            if filter_idx == 1:
                item = self.devices_table.item(row, 4)
                show = show and (item is not None and "OUI" in item.text())
            elif filter_idx == 2:
                item = self.devices_table.item(row, 4)
                show = show and (item is not None and "NON" in item.text())
            elif filter_idx == 3:
                item = self.devices_table.item(row, 5)
                show = show and (item is not None and "OUI" in item.text())
            elif filter_idx == 4:
                item = self.devices_table.item(row, 3)
                if item:
                    try:
                        used_part = item.text().split("/")[0].strip()
                        show = show and int(used_part) > 1
                    except ValueError:
                        show = False

            self.devices_table.setRowHidden(row, not show)

    def _get_selected_device_id(self) -> str:
        row = self.devices_table.currentRow()
        if row < 0:
            return ""
        item = self.devices_table.item(row, 1)
        return item.text() if item else ""

    def copy_device_id(self):
        device_id = self._get_selected_device_id()
        if not device_id:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez un appareil.")
            return
        QApplication.clipboard().setText(device_id)
        QMessageBox.information(self, "Copié", f"Device ID copié :\n{device_id}")

    def unblock_device(self):
        device_id = self._get_selected_device_id()
        if not device_id:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez un appareil.")
            return

        row = self.devices_table.currentRow()
        current_quota = 0
        attempts_used = 0
        try:
            cell = self.devices_table.item(row, 3).text()
            if "/" in cell:
                parts = cell.split("/")
                attempts_used = int(parts[0].strip())
                current_quota = int(parts[1].strip())
        except Exception:
            pass

        # ✅ Récupère les valeurs par défaut depuis la config
        default_mode = self.config.get("default_unblock_mode", "trial")
        default_trial_days = self.config.get("default_trial_days", 7)
        default_trial_unit = self.config.get("default_trial_unit", "Jours")
        default_full_days = self.config.get("default_full_days", 7)
        default_full_unit = self.config.get("default_full_unit", "Jours")

        dlg = UnblockDeviceDialog(
            device_id,
            current_quota,
            attempts_used,
            self,
            default_mode=default_mode,
            default_trial_days=default_trial_days,
            default_trial_unit=default_trial_unit,
            default_full_days=default_full_days,
            default_full_unit=default_full_unit,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        payload = dlg.get_payload()

        def req():
            try:
                data = self._admin_put(f"/api/admin/devices/{device_id}", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(data):
            full_info = data.get("full_access") if isinstance(data, dict) else None
            self.load_devices()
            self.load_licenses()  # ✅ Rafraîchit aussi les licences si accès complet accordé

            if full_info:
                QMessageBox.information(
                    self, "⭐ Accès complet accordé",
                    f"Le device {device_id[:16]}... a reçu un ACCÈS COMPLET.\n\n"
                    f"Clé générée : {full_info.get('key', '?')}\n"
                    f"Durée : {full_info.get('duration_val')} {full_info.get('duration_unit')}\n"
                    f"Expire : {full_info.get('expires_at', '?')}"
                )
            else:
                new_quota = data.get("max_attempts_allowed", "?") if isinstance(data, dict) else "?"
                new_used = data.get("attempts_count", "?") if isinstance(data, dict) else "?"
                QMessageBox.information(
                    self, "Appareil débloqué",
                    f"Le device {device_id[:16]}... est débloqué.\n\n"
                    f"Quota : {new_used} / {new_quota}"
                )
        self._start_worker(req, on_done)

    def block_device(self):
        device_id = self._get_selected_device_id()
        if not device_id:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez un appareil.")
            return

        confirm = QMessageBox.question(
            self, "Confirmer",
            f"Bloquer {device_id} ?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        def req():
            try:
                data = self._admin_put(
                    f"/api/admin/devices/{device_id}",
                    {"is_blocked": True, "notes": "Bloqué manuellement"}
                )
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_devices()
            QMessageBox.information(self, "Bloqué", f"{device_id[:16]}... est bloqué.")
        self._start_worker(req, on_done)

    def edit_device_notes(self):
        device_id = self._get_selected_device_id()
        if not device_id:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez un appareil.")
            return

        row = self.devices_table.currentRow()
        notes_item = self.devices_table.item(row, 6)
        current_text = notes_item.text() if notes_item else ""
        current_notes = current_text
        if " — " in current_notes:
            for p in current_notes.split(" — "):
                if p.startswith("📝 "):
                    current_notes = p.replace("📝 ", "").strip()
                    break
        elif current_notes.startswith("📝 "):
            current_notes = current_notes.replace("📝 ", "").strip()
        elif current_notes == "—":
            current_notes = ""

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Notes — {device_id[:24]}...")
        dialog.resize(460, 280)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(QLabel("Notes internes :"))
        notes_edit = QTextEdit()
        notes_edit.setPlainText(current_notes)
        layout.addWidget(notes_edit, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(dialog.reject)
        btns.addWidget(btn_cancel)
        btn_save = QPushButton("💾 Enregistrer")
        btn_save.clicked.connect(dialog.accept)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

        if dialog.exec() != QDialog.Accepted:
            return

        new_notes = notes_edit.toPlainText().strip()

        def req():
            try:
                data = self._admin_put(f"/api/admin/devices/{device_id}", {"notes": new_notes})
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_devices()
            QMessageBox.information(self, "Enregistré", "Notes sauvegardées.")
        self._start_worker(req, on_done)

    def delete_device(self):
        device_id = self._get_selected_device_id()
        if not device_id:
            QMessageBox.warning(self, "Aucune sélection", "Sélectionnez un appareil.")
            return

        confirm = QMessageBox.question(
            self, "Suppression",
            f"Supprimer la trace {device_id} ?\n\nIRRÉVERSIBLE.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        def req():
            try:
                data = self._admin_delete(f"/api/admin/devices/{device_id}")
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.load_devices()
            QMessageBox.information(self, "Supprimé", "Trace supprimée.")
        self._start_worker(req, on_done)

    # ═══════════════════════════════════════════════════════════
    # 📰 ACTUALITÉS
    # ═══════════════════════════════════════════════════════════
    def load_news(self):
        def req():
            try:
                r = requests.get(f"{API_URL}/api/news", timeout=12)
                if r.status_code == 200:
                    return True, "OK", r.json()
                return False, f"HTTP {r.status_code} : {r.text[:200]}", None
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, self._on_news_loaded)

    def _on_news_loaded(self, data):
        self.news_table.setRowCount(0)
        self.current_news_list = data if isinstance(data, list) else []
        for row, n in enumerate(self.current_news_list):
            self.news_table.insertRow(row)
            self.news_table.setRowHeight(row, 36)
            cat = str(n.get("category", "")).upper()
            cols = [
                str(n.get("id", "")),
                cat,
                str(n.get("title", "")),
                str(n.get("version") or "—"),
                str(n.get("created_at", ""))[:10]
            ]
            for c_idx, val in enumerate(cols):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignCenter)
                if c_idx == 1:
                    item.setFont(QFont("Segoe UI", 9, QFont.Bold))
                    if cat == "UPDATE":
                        item.setForeground(QColor("#38bdf8"))
                    elif cat == "TIP":
                        item.setForeground(QColor("#34d399"))
                    elif cat == "ALERT":
                        item.setForeground(QColor("#f87171"))
                    else:
                        item.setForeground(QColor("#a78bfa"))
                elif c_idx == 2:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.news_table.setItem(row, c_idx, item)

    def publish_news(self):
        title = self.news_title.text().strip()
        summary = self.news_summary.text().strip()
        content = self.news_content.toPlainText().strip()
        if not title or not summary:
            QMessageBox.warning(self, "Requis", "Titre + résumé obligatoires.")
            return

        cat = "news"
        raw_cat = self.news_category.currentText()
        if "update" in raw_cat:
            cat = "update"
        elif "tip" in raw_cat:
            cat = "tip"
        elif "alert" in raw_cat:
            cat = "alert"

        payload = {
            "title": title,
            "summary": summary,
            "content": content,
            "category": cat,
            "version": self.news_version.text().strip() or None,
            "download_url": self.news_url.text().strip() or None
        }

        def req():
            try:
                data = self._admin_post("/api/admin/news/create", payload)
                return True, "OK", data
            except Exception as e:
                return False, str(e), None

        def on_done(_):
            self.news_title.clear()
            self.news_summary.clear()
            self.news_content.clear()
            self.news_version.clear()
            self.news_url.clear()
            self.load_news()
        self._start_worker(req, on_done)

    def delete_news(self):
        row = self.news_table.currentRow()
        if row < 0:
            return
        nid = self.news_table.item(row, 0).text()

        def req():
            try:
                data = self._admin_delete(f"/api/admin/news/{nid}")
                return True, "OK", data
            except Exception as e:
                return False, str(e), None
        self._start_worker(req, lambda _: self.load_news())

    def delete_all_news(self):
        count = self.news_table.rowCount()
        if count == 0:
            return
        if QMessageBox.question(
            self, "Purger", f"Supprimer les {count} actualités ?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        ids = []
        for r in range(count):
            it = self.news_table.item(r, 0)
            if it:
                ids.append(it.text())

        def req():
            failed = []
            for nid in ids:
                try:
                    self._admin_delete(f"/api/admin/news/{nid}")
                except Exception as e:
                    failed.append(f"{nid}: {e}")
            if failed:
                return False, f"{len(failed)} échecs : {failed[:3]}", None
            return True, "OK", None
        self._start_worker(req, lambda _: self.load_news())

    # ═══════════════════════════════════════════════════════════
    # 📚 BASE DOCUMENTAIRE
    # ═══════════════════════════════════════════════════════════
    def refresh_kb_list(self):
        self.kb_table.setRowCount(0)
        self.combo_context_file.clear()
        self.combo_context_file.addItem("— Aucun contexte direct —", "")
        try:
            files = [f for f in os.listdir(KB_DIR) if f.endswith(".txt")]
        except Exception:
            files = []
        if not files:
            return

        infos = []
        for f in files:
            p = os.path.join(KB_DIR, f)
            try:
                s = os.stat(p)
            except Exception:
                continue
            infos.append({
                "name": f, "path": p,
                "size": round(s.st_size / 1024, 2),
                "mtime": s.st_mtime,
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.st_mtime))
            })

        by_date = (self.combo_sort_field.currentIndex() == 0)
        desc = (self.combo_sort_order.currentIndex() == 0)
        infos.sort(key=lambda x: x["mtime"] if by_date else x["name"].lower(), reverse=desc)

        for r, info in enumerate(infos):
            self.kb_table.insertRow(r)
            self.kb_table.setRowHeight(r, 34)
            self.kb_table.setItem(r, 0, QTableWidgetItem(info["name"]))
            self.kb_table.setItem(r, 1, QTableWidgetItem(f"{info['size']} Ko"))
            self.kb_table.setItem(r, 2, QTableWidgetItem(info["date"]))
            self.combo_context_file.addItem(f"{info['name']} ({info['date']})", info["path"])

    def save_and_name_kb_doc(self):
        txt = self.kb_input_text.toPlainText().strip()
        if not txt:
            return
        api_key = self.input_gemini_key.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Clé manquante", "Renseignez votre clé Gemini.")
            return

        def worker():
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"Generate a short snake_case filename slug (no extension) for this documentation:\n\n{txt[:2000]}"
            )
            slug = "".join(c for c in resp.text.strip().replace(" ", "_") if c.isalnum() or c == "_")
            fname = f"{slug or 'doc'}.txt"
            with open(os.path.join(KB_DIR, fname), "w", encoding="utf-8") as f:
                f.write(txt)
            return True, "OK", fname

        def on_saved(fn):
            self.kb_input_text.clear()
            self.refresh_kb_list()
            QMessageBox.information(self, "Enregistré", f"Fichier créé : {fn}")

        self._start_worker(worker, on_saved)

    def delete_selected_kb_file(self):
        row = self.kb_table.currentRow()
        if row >= 0:
            fn = self.kb_table.item(row, 0).text()
            p = os.path.join(KB_DIR, fn)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception as e:
                QMessageBox.warning(self, "Erreur", f"Suppression impossible : {e}")
            self.refresh_kb_list()

    # ═══════════════════════════════════════════════════════════
    # 🤖 GEMINI
    # ═══════════════════════════════════════════════════════════
    def process_ai_prompt(self):
        if not GENAI_AVAILABLE:
            QMessageBox.warning(self, "Gemini indisponible", "pip install google-genai")
            return
        api_key = self.input_gemini_key.text().strip()
        prompt = self.ai_prompt.toPlainText().strip()
        if not api_key or not prompt:
            QMessageBox.warning(self, "Champs requis", "Clé + prompt requis.")
            return

        ctx_file = self.combo_context_file.currentData()
        context_str = ""
        if ctx_file and os.path.exists(ctx_file):
            try:
                with open(ctx_file, "r", encoding="utf-8") as f:
                    context_str = f.read()
            except Exception:
                context_str = ""

        self.btn_run_ai.setEnabled(False)
        self.ai_progress.setVisible(True)

        def worker():
            client = genai.Client(api_key=api_key)
            instruction = (
                "Tu es l'assistant SmartCollect. Génère des actualités bilingues. "
                "Format JSON strict : "
                "{'title': 'Titre FR / EN', 'summary': '🇫🇷...\\n🇬🇧...', "
                "'content': '🇫🇷 FRANÇAIS\\n...\\n🇬🇧 ENGLISH\\n...', "
                "'category': 'update|tip|news|alert', 'version': 'X.Y.Z'|null, "
                "'download_url': null}. Retourne UNIQUEMENT un tableau JSON."
            )
            full = f"Context:\n{context_str}\n\nConsigne:\n{prompt}" if context_str else prompt

            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=full,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json"
                )
            )
            items = json.loads(resp.text)
            if isinstance(items, dict):
                items = [items]

            ok_count = 0
            logs = []
            for item in items:
                try:
                    self._admin_post("/api/admin/news/create", item)
                    ok_count += 1
                    logs.append(f"✓ {item.get('title')}")
                except Exception as e:
                    logs.append(f"✗ {item.get('title')} : {str(e)[:80]}")
            return True, "OK", (ok_count, logs)

        def on_done(res):
            self.btn_run_ai.setEnabled(True)
            self.ai_progress.setVisible(False)
            count, logs = res
            self.ai_log.setPlainText(f"--- {count} publiée(s) ---\n" + "\n".join(logs))
            self.load_news()

        self._start_worker(worker, on_done)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = AdminGUI()
    win.show()
    sys.exit(app.exec())
