#!/usr/bin/env python3
"""DLSS 5 game launcher. Game files and Steam preferences are never edited."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import photo_runtime

from PySide6.QtCore import Qt, QTimer, QSize, Signal, QObject, QRunnable, QThreadPool, QUrl
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QLinearGradient, QPixmap, QDesktopServices, QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem, QFrame, QComboBox,
    QSlider, QCheckBox, QFileDialog, QDialog, QDialogButtonBox, QFormLayout, QPlainTextEdit,
    QSystemTrayIcon, QMenu, QScrollArea)

from backend import (ROOT, STATE, DEFAULTS, NATIVE_WIDTH, NATIVE_HEIGHT, Game, LaunchOption,
    Telemetry, discover_games,
    custom_game, load_settings, save_settings, launch_plan, start_helper, stop_helper,
    control, apply_preferences, spawn_game, parse_environment)

STYLE = '''
QWidget { background: #101319; color: #edf0f4; font-family: "Inter", "Noto Sans", sans-serif; font-size: 13px; }
QMainWindow { background: #101319; }
QFrame#Sidebar { background: #151920; border-right: 1px solid #272d36; }
QFrame#Card { background: #191e26; border: 1px solid #2c333e; border-radius: 14px; }
QFrame#Card QLabel, QFrame#Card QCheckBox { background: transparent; }
QLabel#Muted { color: #9da7b5; background: transparent; }
QLabel#Eyebrow { color: #b5c0ce; font-size: 10px; font-weight: 700; letter-spacing: 2px; background: transparent; }
QLabel#Section { font-size: 17px; font-weight: 600; background: transparent; }
QLabel#Title { font-size: 31px; font-weight: 700; background: transparent; }
QLabel#Status { font-size: 14px; font-weight: 600; background: transparent; }
QLabel#Metric { font-size: 23px; font-weight: 600; background: transparent; }
QLineEdit, QComboBox, QPlainTextEdit { background: #11151c; border: 1px solid #343d4a; border-radius: 7px; padding: 9px 11px; selection-background-color: #586d35; }
QLineEdit:focus, QComboBox:focus { border-color: #b7ef77; }
QComboBox::drop-down { border: none; width: 27px; }
QComboBox QAbstractItemView { background: #202631; selection-background-color: #384536; color: #edf0f4; }
QPushButton { background: #252c36; border: 1px solid #38414e; border-radius: 8px; padding: 10px 15px; font-weight: 600; }
QPushButton:hover { background: #303b47; border-color: #6b7d90; }
QPushButton:pressed { background: #374354; }
QPushButton:disabled { color: #6d7786; background: #1c222b; border-color: #2c3440; }
QPushButton#Primary { background: #b8ef78; color: #17220c; border-color: #b8ef78; padding: 13px 22px; font-size: 15px; }
QPushButton#Primary:hover { background: #caf89b; }
QPushButton#Primary:disabled { background: #3b4c2a; color: #859674; border-color: #3b4c2a; }
QPushButton#Link { background: transparent; border: none; color: #b7c2d1; padding: 6px; text-align: left; font-weight: 400; }
QPushButton#Link:hover { color: #c5f497; }
QListWidget { background: transparent; border: none; outline: none; padding: 4px; }
QListWidget::item { padding: 12px 10px; border-radius: 8px; margin: 3px 0; color: #c4cdd9; }
QListWidget::item:selected { background: #2a3826; color: #cff3ae; border: 1px solid #526643; }
QListWidget::item:hover:!selected { background: #242b35; }
QSlider::groove:horizontal { height: 5px; background: #343e4b; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #b8ef78; border-radius: 2px; }
QSlider::handle:horizontal { background: #e3fbc6; width: 16px; margin: -6px 0; border-radius: 8px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #566473; border-radius: 4px; background: #11151c; }
QCheckBox::indicator:checked { background: #b8ef78; border: 4px solid #668c41; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #141820; width: 8px; }
QScrollBar::handle:vertical { background: #384453; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #283240; color: #fff; padding: 6px; border: 1px solid #46566a; }
QMenu { background: #1a2029; padding: 5px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #34432c; }
'''


def label(text='', name='', wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('Primary')
    widget.clicked.connect(callback)
    return widget


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class Hero(QFrame):
    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.setMinimumHeight(174)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 23, 26, 23)
        self.tag = label('YOUR GAME. A NEW LOOK.', 'Eyebrow')
        self.title = label('Choose a game', 'Title')
        self.subtitle = label('Real-time neural rendering on your RTX 4070.', 'Muted', True)
        layout.addWidget(self.tag)
        layout.addStretch()
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

    def set_game(self, game: Game):
        self.pixmap = QPixmap(game.artwork) if game.artwork else QPixmap()
        self.title.setText(game.name)
        platform = 'Linux + Proton' if len({o.platform for o in game.options}) > 1 else ('Proton' if game.options and game.options[0].platform == 'windows' else 'Linux')
        self.tag.setText(f'{game.source.upper()}   /   {platform.upper()}')
        self.subtitle.setText('Choose a renderer below, then launch your game.')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor('#202c28'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 14, 14)
        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        gradient = QLinearGradient(0, 0, self.width(), self.height() * .55)
        gradient.setColorAt(0, QColor(15, 21, 24, 245))
        gradient.setColorAt(.58, QColor(15, 21, 24, 220))
        gradient.setColorAt(1, QColor(15, 21, 24, 120))
        painter.fillRect(self.rect(), gradient)


class GameDialog(QDialog):
    def __init__(self, parent, game: Game | None = None):
        super().__init__(parent)
        self.setWindowTitle('Configure launch' if game else 'Add a game')
        self.resize(640, 330)
        layout = QVBoxLayout(self)
        layout.addWidget(label('Choose a game executable', 'Section'))
        layout.addWidget(label('Linux executables, launch scripts, AppImages, and Windows .exe files are supported.', 'Muted', True))
        form = QFormLayout()
        self.name = QLineEdit(game.name if game else '')
        self.exe = QLineEdit(game.options[0].executable if game and game.options else '')
        self.args = QLineEdit(game.options[0].arguments if game and game.options else '')
        self.cwd = QLineEdit(game.directory if game else '')
        row = QHBoxLayout()
        row.addWidget(self.exe, 1)
        row.addWidget(button('Browse…', self.browse))
        form.addRow('Name', self.name)
        form.addRow('Executable', row)
        form.addRow('Arguments', self.args)
        form.addRow('Working folder', self.cwd)
        layout.addLayout(form)
        self.error = label('', 'Muted', True)
        layout.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.validate)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.result_game = None

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose game executable', self.cwd.text() or str(Path.home()))
        if path:
            self.exe.setText(path)
            self.cwd.setText(str(Path(path).parent))
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def validate(self):
        try:
            shlex.split(self.args.text())
            if self.cwd.text() and not Path(self.cwd.text()).is_dir():
                raise ValueError('The working folder does not exist.')
            self.result_game = custom_game(self.exe.text(), self.name.text(), self.args.text(), self.cwd.text())
            self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('DLSS 5 · Game Launcher')
        self.setWindowIcon(QIcon(str(ROOT / 'launcher/icon.svg')))
        self.resize(1100, 920)
        self.setMinimumSize(960, 700)
        self.saved = load_settings()
        self.saved.setdefault('profiles', {})
        self.saved.setdefault('custom', [])
        self.games: list[Game] = []
        self.current: Game | None = None
        self.active_id = ''
        self.game_process = None
        self.session_log = ''
        self.session_mode = ''
        self.owns_helper = False
        self.busy = False
        self.loading = False
        self.quitting = False
        self.launch_at = 0.0
        self.exited_at = 0.0
        self.telemetry = Telemetry()
        self.latest = {}
        self.pending_settings = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.jobs = set()
        self._build()
        self._tray()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.flush_controls)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(750)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.refresh()

    def job(self, function, done=None, failed=None):
        job = Job(function)
        self.jobs.add(job)
        def success(result):
            self.jobs.discard(job)
            if done:
                done(result)
        def failure(message):
            self.jobs.discard(job)
            (failed or self.show_error)(message)
        job.signals.done.connect(success)
        job.signals.failed.connect(failure)
        self.pool.start(job)

    def _build(self):
        container = QWidget()
        self.setCentralWidget(container)
        horizontal = QHBoxLayout(container)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(265)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 25, 16, 20)
        side.setSpacing(12)
        logo = label('DLSS 5', 'Title')
        logo.setStyleSheet('color: #c5f59a; font-size: 29px; background: transparent;')
        side.addWidget(logo)
        side.addWidget(label('GAME LAUNCHER', 'Eyebrow'))
        side.addSpacing(16)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search your games')
        self.search.textChanged.connect(self.filter_games)
        side.addWidget(self.search)
        self.count = label('Scanning Steam…', 'Muted')
        side.addWidget(self.count)
        self.library = QListWidget()
        self.library.setSpacing(1)
        self.library.currentItemChanged.connect(self.select_item)
        side.addWidget(self.library, 1)
        side.addWidget(button('+  Add game', self.add_game))
        self.refresh_button = button('Refresh library', self.refresh)
        self.refresh_button.setObjectName('Link')
        side.addWidget(self.refresh_button)
        side.addSpacing(10)
        side.addWidget(label('Choose DLSS NR or experimental photorealistic AI for each game.', 'Muted', True))
        horizontal.addWidget(sidebar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        horizontal.addWidget(scroll, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 25, 28, 22)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(label('PLAY WITH NEURAL RENDERING', 'Eyebrow'))
        top.addStretch()
        top.addWidget(label('RTX 4070  /  LINUX', 'Eyebrow'))
        layout.addLayout(top)
        self.hero = Hero()
        layout.addWidget(self.hero)
        self.error = label('', '', True)
        self.error.setStyleSheet('background: #342724; color: #ffcfad; border: 1px solid #684839; border-radius: 8px; padding: 10px;')
        self.error.hide()
        layout.addWidget(self.error)
        status_card, status_layout = self.card()
        status_top = QHBoxLayout()
        self.status_dot = label('●')
        self.status_dot.setStyleSheet('color: #667281; background: transparent; font-size: 20px;')
        status_top.addWidget(self.status_dot)
        self.status_text = label('DLSS 5 is off', 'Status')
        status_top.addWidget(self.status_text, 1)
        self.effect = QCheckBox('Effect enabled')
        self.effect.setChecked(True)
        self.effect.toggled.connect(self.toggle_effect)
        status_top.addWidget(self.effect)
        status_layout.addLayout(status_top)
        metrics = QHBoxLayout()
        self.metric_labels = []
        for title in ('OUTPUT', 'MODEL TIME', 'PROCESSED FRAMES'):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label(title, 'Eyebrow'))
            value = label('—', 'Metric')
            column.addWidget(value)
            metrics.addLayout(column, 1)
            self.metric_labels.append(value)
        status_layout.addLayout(metrics)
        self.status_hint = label('Launch a game to start the renderer.', 'Muted', True)
        status_layout.addWidget(self.status_hint)
        layout.addWidget(status_card)
        controls_card, controls = self.card()
        controls.addWidget(label('Make it yours', 'Section'))
        renderer_row = QHBoxLayout()
        renderer_row.addWidget(label('Renderer', 'Muted'))
        self.renderer = QComboBox()
        self.renderer.addItem('DLSS NR · experimental compatibility', 'dlss')
        self.renderer.addItem('Fake · photorealistic AI (SD-Turbo)', 'photo')
        self.renderer.currentIndexChanged.connect(self.renderer_changed)
        renderer_row.addWidget(self.renderer, 1)
        controls.addLayout(renderer_row)
        self.photo_options = QWidget()
        self.photo_options.setObjectName('PhotoOptions')
        self.photo_options.setStyleSheet('QWidget#PhotoOptions { background: transparent; }')
        photo_form = QFormLayout(self.photo_options)
        photo_form.setContentsMargins(0, 0, 0, 0)
        self.photo_prompt = QLineEdit(photo_runtime.DEFAULT_PROMPT)
        self.photo_prompt.editingFinished.connect(self.save_profile)
        photo_form.addRow('Describe the look', self.photo_prompt)
        self.photo_resolution = QComboBox()
        for title, edge in [('Fast · 256 pixels', 256), ('Balanced · 384 pixels', 384), ('Detailed · 512 pixels', 512)]:
            self.photo_resolution.addItem(title, edge)
        self.photo_resolution.setCurrentIndex(1)
        self.photo_resolution.currentIndexChanged.connect(self.save_profile)
        photo_form.addRow('AI resolution', self.photo_resolution)
        self.photo_steps = QComboBox()
        for count in range(1, 5):
            self.photo_steps.addItem(f'{count} step' + (' · fastest' if count == 1 else 's'), count)
        self.photo_steps.currentIndexChanged.connect(self.save_profile)
        photo_form.addRow('Denoising steps', self.photo_steps)
        self.photo_fps = QComboBox()
        for fps in (10, 15, 20, 30, 45, 60, 0):
            self.photo_fps.addItem(f'{fps} AI FPS' if fps else 'Unlimited', fps)
        self.photo_fps.setCurrentIndex(3)
        self.photo_fps.currentIndexChanged.connect(self.save_profile)
        photo_form.addRow('AI FPS limit', self.photo_fps)
        self.photo_change = QSlider(Qt.Orientation.Horizontal)
        self.photo_change.setRange(20, 85)
        self.photo_change.setValue(35)
        self.photo_change.setToolTip('Lower values preserve more of the game and reduce flicker. Start at 35%; higher values reinvent more.')
        self.photo_change.valueChanged.connect(self.save_profile)
        photo_form.addRow('Reimagination', self.photo_change)
        photo_form.addRow(label('More steps cost FPS. The AI limit leaves the game’s FPS uncapped. '
                               'Can change geometry, text and faces; motion can flicker. Set these before launching the game.', 'Muted', True))
        controls.addWidget(self.photo_options)
        self.photo_options.hide()
        self.nr_options = QWidget()
        self.nr_options.setObjectName('NrOptions')
        self.nr_options.setStyleSheet('QWidget#NrOptions { background: transparent; }')
        nr_controls = QVBoxLayout(self.nr_options)
        nr_controls.setContentsMargins(0, 0, 0, 0)
        quality_row = QHBoxLayout()
        quality_row.addWidget(label('Quality', 'Muted'))
        self.quality = QComboBox()
        for name, value in [('Performance · 50%', .5), ('Balanced · 67%', .67), ('Quality · 100%', 1.0)]:
            self.quality.addItem(name, value)
        self.quality.setCurrentIndex(1)
        self.quality.currentIndexChanged.connect(lambda: self.preferences_changed('workingscale', self.quality.currentData()))
        quality_row.addWidget(self.quality, 1)
        quality_row.addSpacing(15)
        quality_row.addWidget(label('Look', 'Muted'))
        self.look = QComboBox()
        self.look.addItems(['Native', 'Natural', 'Cinematic'])
        self.look.currentIndexChanged.connect(lambda i: self.preferences_changed('style', i))
        quality_row.addWidget(self.look, 1)
        nr_controls.addLayout(quality_row)
        strength_row = QHBoxLayout()
        strength_row.addWidget(label('Output amplification', 'Muted'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 1000)
        self.strength.setValue(100)
        self.strength.valueChanged.connect(self.strength_changed)
        strength_row.addWidget(self.strength, 1)
        self.strength_value = label('100%', 'Muted')
        self.strength_value.setMinimumWidth(40)
        strength_row.addWidget(self.strength_value)
        self.max_slop = button('MAX SLOP', lambda: self.strength.setValue(1000))
        self.max_slop.setToolTip('Set effect strength to 1000%. Expect maximum visual nonsense.')
        self.max_slop.setStyleSheet('color: #d9ffad; border-color: #617d45; padding: 6px 9px;')
        strength_row.addWidget(self.max_slop)
        nr_controls.addLayout(strength_row)
        controls.addWidget(self.nr_options)
        self.compare = QCheckBox('Before / after comparison')
        self.compare.toggled.connect(lambda value: self.preferences_changed('compare', 2 if value else 0))
        controls.addWidget(self.compare)
        layout.addWidget(controls_card)
        launch_card, launch_layout = self.card()
        mode_row = QHBoxLayout()
        mode_row.addWidget(label('Launch mode', 'Muted'))
        self.mode = QComboBox()
        for title, value in [('Automatic', 'auto'), ('Direct · Vulkan / Proton', 'direct'),
                             ('Compatibility window · Gamescope', 'gamescope'),
                             ('Entire screen · fullscreen game', 'screen')]:
            self.mode.addItem(title, value)
        self.mode.currentIndexChanged.connect(self.launch_preferences_changed)
        mode_row.addWidget(self.mode, 1)
        self.configure = button('Configure launch', self.configure_game)
        self.configure.setObjectName('Link')
        mode_row.addWidget(self.configure)
        launch_layout.addLayout(mode_row)
        self.mode_hint = label('', 'Muted', True)
        launch_layout.addWidget(self.mode_hint)
        self.advanced_button = button('Launch options ▸', self.toggle_advanced)
        self.advanced_button.setObjectName('Link')
        launch_layout.addWidget(self.advanced_button)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 3, 0, 8)
        self.version = QComboBox()
        self.version.currentIndexChanged.connect(self.launch_preferences_changed)
        self.arguments = QLineEdit()
        self.arguments.setPlaceholderText('Optional game arguments')
        self.arguments.editingFinished.connect(self.launch_preferences_changed)
        self.environment = QLineEdit()
        self.environment.setPlaceholderText('Optional: NAME=value')
        self.environment.editingFinished.connect(self.launch_preferences_changed)
        self.resolution = QComboBox()
        # The display's own resolution comes first and is the default: a smaller
        # compositor window makes the game pick a smaller video mode than the
        # panel can show.
        native = (NATIVE_WIDTH, NATIVE_HEIGHT)
        sizes = [(1280, 720), (1920, 1080), (2560, 1440)]
        for dimensions in sorted({native, *sizes}, reverse=True):
            suffix = ' · display' if dimensions == native else ''
            self.resolution.addItem(f'{dimensions[0]} × {dimensions[1]}{suffix}', dimensions)
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(native)))
        self.resolution.currentIndexChanged.connect(self.launch_preferences_changed)
        self.fullscreen = QCheckBox('Fullscreen compatibility window')
        self.fullscreen.toggled.connect(self.launch_preferences_changed)
        advanced.addRow('Version', self.version)
        advanced.addRow('Arguments', self.arguments)
        advanced.addRow('Environment', self.environment)
        advanced.addRow('Window size', self.resolution)
        advanced.addRow('', self.fullscreen)
        steam_option = button('Copy optional Steam launch option', self.copy_steam_option)
        steam_option.setObjectName('Link')
        advanced.addRow('', steam_option)
        advanced.addRow('', label('For games requiring Steam’s own launch flow. Nothing is applied automatically.', 'Muted', True))
        self.advanced.hide()
        launch_layout.addWidget(self.advanced)
        actions = QHBoxLayout()
        self.launch_button = button('Launch with DLSS 5', self.launch, True)
        self.launch_button.setEnabled(False)
        actions.addWidget(self.launch_button, 1)
        self.stop_button = button('Stop DLSS 5', self.stop)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        launch_layout.addLayout(actions)
        layout.addWidget(launch_card)
        footer = QHBoxLayout()
        for text, action in [('Session log', self.open_log), ('Helper log', self.open_helper_log), ('Quick test', self.quick_test)]:
            b = button(text, action)
            b.setObjectName('Link')
            footer.addWidget(b)
        footer.addStretch()
        layout.addLayout(footer)
        layout.addStretch()
        self.footer_hint = label('Vulkan and Proton work directly. Compatibility mode also supports OpenGL games.', 'Muted', True)
        layout.addWidget(self.footer_hint)

    def card(self):
        card = QFrame()
        card.setObjectName('Card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(19, 17, 19, 17)
        layout.setSpacing(14)
        return card, layout

    def _tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('DLSS 5 Game Launcher')
        menu = QMenu(self)
        show = menu.addAction('Show launcher')
        show.triggered.connect(self.show_window)
        toggle = menu.addAction('Toggle effect')
        toggle.triggered.connect(lambda: self.effect.setChecked(not self.effect.isChecked()))
        stop = menu.addAction('Stop DLSS 5')
        stop.triggered.connect(self.stop)
        menu.addSeparator()
        quit_action = menu.addAction('Quit launcher')
        quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        self.refresh_button.setEnabled(False)
        self.job(lambda: discover_games(self.saved['custom']), self.library_loaded,
                 lambda error: (self.refresh_button.setEnabled(True), self.show_error(error)))

    def library_loaded(self, result):
        self.games, notes = result
        overrides = self.saved.get('launch_overrides', {})
        for game in self.games:
            if game.id in overrides:
                game.options = [LaunchOption(**o) for o in overrides[game.id]]
        self.refresh_button.setEnabled(True)
        old = self.current.id if self.current else self.saved.get('selected', '')
        self.library.clear()
        for game in self.games:
            item = QListWidgetItem(game.name)
            item.setData(Qt.ItemDataRole.UserRole, game.id)
            item.setToolTip(f'{game.source} · {game.directory}')
            self.library.addItem(item)
            if game.id == old:
                self.library.setCurrentItem(item)
        if self.library.currentRow() < 0 and self.library.count():
            self.library.setCurrentRow(0)
        self.count.setText(f'{len(self.games)} installed games')
        self.filter_games(self.search.text())
        if notes:
            self.footer_hint.setText(notes[0])

    def filter_games(self, text):
        for index in range(self.library.count()):
            item = self.library.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def select_item(self, item, previous=None):
        if not item:
            return
        self.current = next(g for g in self.games if g.id == item.data(Qt.ItemDataRole.UserRole))
        p = {**DEFAULTS, **self.saved['profiles'].get(self.current.id, {})}
        self.loading = True
        self.hero.set_game(self.current)
        self.renderer.setCurrentIndex(max(0, self.renderer.findData(p['renderer'])))
        self.photo_prompt.setText(p['photo_prompt'])
        self.photo_change.setValue(round(p['photo_change']*100))
        self.photo_steps.setCurrentIndex(max(0, self.photo_steps.findData(p['photo_steps'])))
        self.photo_fps.setCurrentIndex(max(0, self.photo_fps.findData(p['photo_fps'])))
        self.photo_resolution.setCurrentIndex(max(0, self.photo_resolution.findData(p['photo_edge'])))
        self.quality.setCurrentIndex(max(0, self.quality.findData(p['scale'])))
        self.look.setCurrentIndex(int(p['style']))
        self.strength.setValue(round(float(p['strength']) * 100))
        self.strength_value.setText(f'{self.strength.value()}%')
        self.compare.setChecked(bool(p['compare']))
        self.mode.setCurrentIndex(max(0, self.mode.findData(p['mode'])))
        self.version.clear()
        for option in self.current.options:
            self.version.addItem(option.description or Path(option.executable).name)
        self.version.setCurrentIndex(min(int(p['launch']), max(0, self.version.count() - 1)))
        self.arguments.setText(p['arguments'])
        self.environment.setText(p['environment'])
        self.resolution.setCurrentIndex(max(0, self.resolution.findData((p['width'], p['height']))))
        self.fullscreen.setChecked(bool(p['fullscreen']))
        self.saved['selected'] = self.current.id
        save_settings(self.saved)
        self.loading = False
        self.renderer_changed()
        self.launch_preferences_changed()
        self.update_buttons()

    def profile(self):
        width, height = self.resolution.currentData()
        return dict(scale=self.quality.currentData(), strength=self.strength.value() / 100,
                    style=self.look.currentIndex(), compare=self.compare.isChecked(),
                    mode=self.mode.currentData(), width=width, height=height,
                    fullscreen=self.fullscreen.isChecked(), arguments=self.arguments.text(),
                    environment=self.environment.text(), launch=max(0, self.version.currentIndex()),
                    renderer=self.renderer.currentData(), photo_prompt=self.photo_prompt.text(),
                    photo_change=self.photo_change.value()/100, photo_edge=self.photo_resolution.currentData(),
                    photo_steps=self.photo_steps.currentData(), photo_fps=self.photo_fps.currentData())

    def renderer_changed(self, *args):
        if self.loading:
            return
        fake = self.renderer.currentData() == 'photo'
        self.photo_options.setVisible(fake)
        self.nr_options.setVisible(not fake)
        for widget in (self.quality, self.look, self.strength, self.max_slop):
            widget.setEnabled(not fake)
        self.save_profile()
        self.update_buttons()

    def save_profile(self):
        if self.loading or not self.current:
            return
        self.saved['profiles'][self.current.id] = self.profile()
        save_settings(self.saved)

    def preferences_changed(self, key, value):
        if self.loading:
            return
        self.save_profile()
        if self.current and self.current.id == self.active_id and self.latest.get('live'):
            self.pending_settings[key] = value
            if key == 'detail':
                self.pending_settings['colour'] = value
            self.debounce.start()

    def strength_changed(self, value):
        self.strength_value.setText(f'{value}%')
        self.preferences_changed('detail', value / 100)

    def flush_controls(self):
        updates, self.pending_settings = self.pending_settings, {}
        if updates:
            self.job(lambda: [control('set', k, str(v)) for k, v in updates.items()])

    def launch_preferences_changed(self, *args):
        if self.loading:
            return
        self.save_profile()
        hints = {
            'auto': 'Chooses direct rendering for Proton and FNA games; a compatibility window for other Linux games.',
            'direct': 'For Vulkan games and DirectX games through Proton. Select Vulkan in the game if needed.',
            'gamescope': 'Processes the whole game window, including OpenGL games. Uses the window size below.',
            'screen': 'Processes the entire fullscreen game output through Gamescope. This does not capture other desktop windows.',
            'zink': 'Translates native OpenGL into Vulkan. Use if direct mode does not process frames.',
        }
        self.mode_hint.setText(hints[self.mode.currentData()])
        if self.mode.currentData() == 'screen' and not self.fullscreen.isChecked():
            self.loading = True
            self.fullscreen.setChecked(True)
            self.loading = False
            self.save_profile()

    def toggle_advanced(self):
        visible = not self.advanced.isVisible()
        self.advanced.setVisible(visible)
        self.advanced_button.setText('Launch options ▾' if visible else 'Launch options ▸')

    def add_game(self):
        dialog = GameDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            game = dialog.result_game
            self.saved['custom'].append(dataclasses.asdict(game))
            self.saved['selected'] = game.id
            self.current = None
            save_settings(self.saved)
            self.refresh()

    def configure_game(self):
        if not self.current:
            return
        dialog = GameDialog(self, self.current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.current.options = dialog.result_game.options
            self.saved.setdefault('launch_overrides', {})[self.current.id] = [dataclasses.asdict(o) for o in self.current.options]
            save_settings(self.saved)
            self.select_item(self.library.currentItem())

    def toggle_effect(self, checked):
        if self.loading or not self.latest.get('live'):
            return
        self.job(lambda: control('set', 'enabled', '1' if checked else '0'))

    def launch(self):
        if self.busy or not self.current:
            return
        if self.active_id == self.current.id and self.game_process and self.game_process.poll() is None:
            self.resume()
            return
        self.start_game(self.current, self.profile())

    def start_game(self, game, preferences):
        try:
            parse_environment(preferences.get('environment', ''))
            plan = launch_plan(game, preferences)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))
            return
        self.busy = True
        self.error.hide()
        self.status_text.setText('Starting the neural renderer…')
        self.update_buttons()
        def run():
            start_helper(preferences)
            apply_preferences(preferences)
            try:
                return spawn_game(plan)
            except Exception:
                stop_helper()
                raise
        def ready(process):
            self.game_process = process
            self.active_id = game.id
            self.session_log = plan.log
            self.session_mode = plan.mode
            self.session_renderer = preferences.get('renderer', 'dlss')
            self.owns_helper = True
            self.busy = False
            self.launch_at = time.monotonic()
            self.exited_at = 0
            self.loading = True
            self.effect.setChecked(True)
            self.loading = False
            self.update_buttons()
        self.job(run, ready, self.launch_failed)

    def launch_failed(self, message):
        self.busy = False
        self.update_buttons()
        self.show_error(message)

    def resume(self):
        self.busy = True
        self.update_buttons()
        preferences = self.profile()
        def run():
            start_helper(preferences)
            apply_preferences(preferences)
        def done(_):
            self.busy = False
            self.owns_helper = True
            self.launch_at = time.monotonic()
            self.update_buttons()
        self.job(run, done, self.launch_failed)

    def stop(self):
        if self.busy:
            return
        self.busy = True
        self.status_text.setText('Stopping the renderer…')
        self.update_buttons()
        def done(_):
            self.busy = False
            self.owns_helper = False
            self.loading = True
            self.effect.setChecked(False)
            self.loading = False
            self.update_buttons()
        self.job(stop_helper, done, self.launch_failed)

    def quick_test(self):
        if self.busy or (self.game_process and self.game_process.poll() is None) or self.latest.get('layer_live'):
            self.show_error('Close the current game before starting a test.')
            return
        game = Game('test:vkcube', 'Vulkan test', '/tmp', [LaunchOption('/usr/bin/vkcube', '--c 600 --width 960 --height 540', '/tmp')])
        self.start_game(game, {**self.profile(), 'mode': 'direct', 'width': 960, 'height': 540})

    def update_buttons(self):
        running = bool(self.game_process and self.game_process.poll() is None)
        connected = self.latest.get('layer_live', False)
        same = self.current and self.active_id == self.current.id
        can_resume = running and same and not self.owns_helper
        engine = 'Fake AI' if self.renderer.currentData() == 'photo' else 'DLSS NR'
        self.launch_button.setText('Starting…' if self.busy else f'Resume {engine}' if can_resume else f'Launch with {engine}')
        self.stop_button.setText('Stop renderer')
        self.renderer.setEnabled(not self.busy and not running and not connected)
        self.photo_options.setEnabled(not self.busy and not running and not connected)
        self.launch_button.setEnabled(bool(self.current and self.current.options and not self.busy and
                                     (can_resume or (not running and not connected))))
        self.stop_button.setEnabled(not self.busy and (self.owns_helper or self.latest.get('live', False)))
        self.effect.setEnabled(not self.busy and self.latest.get('live', False))

    def poll(self):
        status = self.telemetry.read()
        fake = (getattr(self, 'session_renderer', 'dlss') if status.get('live') else self.renderer.currentData()) == 'photo'
        if fake:
            status['label'] = status['label'].replace('DLSS 5', 'Fake AI')
            photo_status = photo_runtime.status()
            if self.owns_helper and photo_status.get('state') == 'error':
                status.update(state='error', label=photo_status['label'])
        self.latest = status
        if self.busy and self.renderer.currentData() == 'photo':
            progress = photo_runtime.status()
            if progress.get('state') == 'loading':
                self.status_text.setText(progress['label'])
        if not self.busy:
            self.status_text.setText(status['label'])
        color = '#b8ef78' if status['state'] == 'active' else '#e8b965' if status['state'] in ('waiting', 'ready') else '#ed9980' if status['state'] == 'error' else '#788696'
        self.status_dot.setStyleSheet(f'color: {color}; background: transparent; font-size: 20px;')
        active = status['state'] in ('active', 'waiting', 'disabled') and status.get('layer_live')
        self.metric_labels[0].setText(f'{status.get("layerWidth", 0)} × {status.get("layerHeight", 0)}' if active else '—')
        self.metric_labels[1].setText(f'{status.get("helperEvalMsBits", 0):.1f} ms' if status['state'] == 'active' else '—')
        self.metric_labels[2].setText(f'{status.get("successful", 0):,}' if active else '—')
        if status['state'] == 'active':
            if fake:
                rate = status.get('rate', 0)
                limit = photo_runtime.status().get('fps_limit', 30)
                target = f'{limit} FPS limit' if limit else 'Uncapped AI'
                self.status_hint.setText(f'{rate:.1f} AI frames/s · {target} · Nonblocking frame processing')
            else:
                self.status_hint.setText(f'{status.get("rate", 0):.0f} neural frames/s  ·  Effect controls update live')
        elif status['state'] == 'disabled':
            self.status_hint.setText('The original image is showing. Turn the effect on to resume processing.')
        elif status['state'] == 'ready':
            elapsed = time.monotonic() - self.launch_at
            self.status_hint.setText('No game frames yet. For OpenGL, try Compatibility window. See Session log for launch errors.' if self.owns_helper and elapsed > 15 else 'Renderer is ready. Waiting for the game to present frames.')
        elif status['state'] == 'error':
            self.status_hint.setText('The game can continue without the effect. Open Helper log for details.')
        else:
            self.status_hint.setText('Launch a game to start the renderer. Stopping the renderer leaves your game open.')
        if status.get('live') and not self.effect.hasFocus():
            self.loading = True
            self.effect.setChecked(bool(status.get('enabled')))
            self.loading = False
        if self.game_process and self.game_process.poll() is not None and not status.get('layer_live'):
            if not self.exited_at:
                self.exited_at = time.monotonic()
            if time.monotonic() - self.exited_at > 4 and not self.busy:
                rc = self.game_process.returncode
                self.game_process = None
                if rc:
                    self.show_error(f'The launch process exited with code {rc}. Open Session log; another launch mode may be needed.')
                if self.owns_helper:
                    self.stop()
        self.update_buttons()

    def copy_steam_option(self):
        if not self.current:
            return
        self.save_profile()
        command = f'{shlex.quote(str(ROOT / "dlss5-launcher"))} --wrap {shlex.quote(self.current.id)} -- %command%'
        QApplication.clipboard().setText(command)
        self.footer_hint.setText('Copied. Paste it into this game’s Steam launch options only if you want to opt in there.')

    def open_log(self):
        path = self.session_log or str(STATE / 'logs')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_helper_log(self):
        path = photo_runtime.LOG if getattr(self, 'session_renderer', 'dlss') == 'photo' else ROOT / 'runtime/state/dlssnr/helper.log'
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        elif self.tray:
            self.hide()
            self.tray.showMessage('DLSS 5 Launcher', 'Still available in the system tray. Quit from the tray to stop DLSS 5.', QSystemTrayIcon.MessageIcon.Information, 2500)
            event.ignore()
        else:
            event.ignore()
            self.quit_app()

    def quit_app(self):
        if self.quitting:
            return
        if self.busy:
            QTimer.singleShot(300, self.quit_app)
            return
        self.quitting = True
        self.poll_timer.stop()
        self.debounce.stop()
        def finish(_=None):
            if self.tray:
                self.tray.hide()
            QApplication.instance().quit()
        if self.owns_helper:
            self.job(stop_helper, finish, lambda error: finish())
        else:
            finish()


def wrap_game(game_id: str, command: list[str]) -> int:
    """Optional Steam wrapper: opt-in only; never writes Steam configuration."""
    if not command:
        raise ValueError('Missing game command after --')
    settings = load_settings()
    p = {**DEFAULTS, **settings.get('profiles', {}).get(game_id, {})}
    start_helper(p)
    apply_preferences(p)
    games, _ = discover_games(settings.get('custom', []))
    game = next((g for g in games if g.id == game_id), None)
    env = os.environ.copy()
    env.update(VKLayer_DLSS5='1', DLSSNR_SHM=f'/tmp/dlssnr-{os.getuid()}/shm.bin',
               XDG_DATA_DIRS=f'{ROOT}/runtime/share:{env.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")}')
    env.pop('DLSSNR_DISABLE', None)
    if game and (Path(game.directory) / 'lib64/libFNA3D.so.0').exists():
        env['FNA3D_FORCE_DRIVER'] = 'Vulkan'
    mode = p['mode']
    if mode == 'auto':
        mode = 'direct'  # Steam already supplies its selected runtime; explicit compatibility is available.
    if mode == 'gamescope':
        command = ['gamescope', '--backend', 'sdl', '-W', str(p['width']), '-H', str(p['height']),
                   '-w', str(p['width']), '-h', str(p['height']), *(['-f'] if p['fullscreen'] else []),
                   '--', 'env', '-u', 'VKLayer_DLSS5', 'DLSSNR_DISABLE=1', *command]
    elif mode == 'zink':
        env.update(__GLX_VENDOR_LIBRARY_NAME='mesa', MESA_LOADER_DRIVER_OVERRIDE='zink', GALLIUM_DRIVER='zink')
    env.update(parse_environment(p['environment']))
    if p['renderer'] == 'photo':
        env.update(photo_runtime.environment(p['photo_edge'], p['photo_fps']))
    try:
        return subprocess.call(command, env=env)
    finally:
        stop_helper()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wrap')
    parser.add_argument('--screenshot', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--offscreen-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.wrap:
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        return wrap_game(args.wrap, command)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('DLSS 5 Launcher')
    app.setOrganizationName('Local DLSS Tools')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    server = None
    if not args.offscreen_test:
        server_name = f'dlss5-launcher-{os.getuid()}'
        client = QLocalSocket()
        client.connectToServer(server_name)
        if client.waitForConnected(250):
            client.write(b'show')
            client.waitForBytesWritten(250)
            return 0
        QLocalServer.removeServer(server_name)
        server = QLocalServer(app)
        if not server.listen(server_name):
            raise RuntimeError('Could not create the launcher session socket.')
    window = Launcher()
    if server:
        def show_existing():
            connection = server.nextPendingConnection()
            if connection:
                connection.disconnectFromServer()
                connection.deleteLater()
            window.show_window()
        server.newConnection.connect(show_existing)
    window.show()
    if args.screenshot:
        QTimer.singleShot(1800, lambda: window.grab().save(str(args.screenshot)))
    if args.offscreen_test:
        QTimer.singleShot(2200, app.quit)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
