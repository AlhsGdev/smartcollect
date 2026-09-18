def _build_devices_tab(self):
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(12)

    info_frame = QFrame()
    info_frame.setStyleSheet(
        "background-color: #111827; border: 1px solid #4f46e5; border-radius: 8px;"
    )
    info_layout = QVBoxLayout(info_frame)
    info_layout.setContentsMargins(14, 12, 14, 12)
    info_layout.setSpacing(6)
    
    title = QLabel("🔒 Appareils ayant tenté un essai gratuit")
    title.setStyleSheet(
        "color: #a5b4fc; font-size: 14px; font-weight: bold; border: none;"
    )
    info_layout.addWidget(title)
    
    subtitle = QLabel(
        "Ces appareils sont identifiés par un ID unique. "
        "Un appareil bloqué ne peut plus créer de nouvelle clé d'essai."
    )
    subtitle.setStyleSheet("color: #94a3b8; font-size: 11px; border: none;")
    subtitle.setWordWrap(True)
    info_layout.addWidget(subtitle)
    
    layout.addWidget(info_frame)

    self.devices_table = QTableWidget()
    self.devices_table.setColumnCount(6)
    self.devices_table.setHorizontalHeaderLabels([
        "ID", "Device ID", "Téléphone", "Tentatives", "Statut", "Notes"
    ])
    self.devices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    self.devices_table.setSelectionBehavior(QTableWidget.SelectRows)
    self.devices_table.setSelectionMode(QTableWidget.SingleSelection)
    self.devices_table.verticalHeader().setVisible(False)
    self.devices_table.horizontalHeader().setStretchLastSection(True)

    dh = self.devices_table.horizontalHeader()
    dh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    dh.setSectionResizeMode(1, QHeaderView.Stretch)
    dh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    dh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    dh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    dh.setSectionResizeMode(5, QHeaderView.Stretch)

    layout.addWidget(self.devices_table, 1)

    actions = QHBoxLayout()
    actions.setSpacing(8)

    btn_refresh = QPushButton("↻ Actualiser")
    btn_refresh.setObjectName("btn_secondary")
    btn_refresh.clicked.connect(self.load_devices)
    actions.addWidget(btn_refresh)

    btn_unblock = QPushButton("✅ Débloquer")
    btn_unblock.setObjectName("btn_success")
    btn_unblock.clicked.connect(self.unblock_device)
    actions.addWidget(btn_unblock)

    btn_block = QPushButton("🚫 Bloquer")
    btn_block.setObjectName("btn_warning")
    btn_block.clicked.connect(self.block_device)
    actions.addWidget(btn_block)

    actions.addStretch()

    btn_delete = QPushButton("🗑 Supprimer la trace")
    btn_delete.setObjectName("btn_danger")
    btn_delete.clicked.connect(self.delete_device_trace)
    actions.addWidget(btn_delete)

    layout.addLayout(actions)
    return tab


def load_devices(self):
    def req():
        r = requests.get(f"{API_URL}/api/admin/devices", timeout=12)
        return (True, "OK", r.json()) if r.status_code == 200 else (False, r.text, None)
    self._start_worker(req, self._on_devices_loaded)


def _on_devices_loaded(self, data):
    self.devices_table.setRowCount(0)
    if not isinstance(data, list):
        return
    for row, dev in enumerate(data):
        self.devices_table.insertRow(row)
        self.devices_table.setRowHeight(row, 36)
        is_blocked = bool(dev.get("is_blocked", False))
        cols = [
            str(dev.get("id", "")),
            str(dev.get("device_id", "")),
            str(dev.get("phone_number", "—")),
            str(dev.get("attempts_count", 1)),
            "🔴 BLOQUÉ" if is_blocked else "🟢 Autorisé",
            str(dev.get("notes") or dev.get("block_reason") or "—"),
        ]
        for c_idx, text in enumerate(cols):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignCenter)
            if c_idx == 1:
                item.setFont(QFont("Consolas", 9))
                item.setForeground(QColor("#818cf8"))
            elif c_idx == 4:
                item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                item.setForeground(
                    QColor("#f87171") if is_blocked else QColor("#34d399")
                )
            self.devices_table.setItem(row, c_idx, item)


def unblock_device(self):
    row = self.devices_table.currentRow()
    if row < 0:
        QMessageBox.warning(self, "Sélection requise",
                            "Sélectionnez un appareil à débloquer.")
        return
    device_id = self.devices_table.item(row, 1).text()
    def req():
        r = requests.put(
            f"{API_URL}/api/admin/devices/{device_id}",
            json={"is_blocked": False, "notes": "Débloqué manuellement"},
            timeout=12
        )
        return (True, "OK", r.json()) if r.status_code == 200 else (False, r.text, None)
    def on_done(_):
        self.load_devices()
        QMessageBox.information(self, "Succès", f"Appareil {device_id} débloqué.")
    self._start_worker(req, on_done)


def block_device(self):
    row = self.devices_table.currentRow()
    if row < 0:
        QMessageBox.warning(self, "Sélection requise",
                            "Sélectionnez un appareil à bloquer.")
        return
    device_id = self.devices_table.item(row, 1).text()
    def req():
        r = requests.put(
            f"{API_URL}/api/admin/devices/{device_id}",
            json={"is_blocked": True, "notes": "Bloqué manuellement"},
            timeout=12
        )
        return (True, "OK", r.json()) if r.status_code == 200 else (False, r.text, None)
    def on_done(_):
        self.load_devices()
        QMessageBox.information(self, "Succès", f"Appareil {device_id} bloqué.")
    self._start_worker(req, on_done)


def delete_device_trace(self):
    row = self.devices_table.currentRow()
    if row < 0:
        QMessageBox.warning(self, "Sélection requise",
                            "Sélectionnez un appareil à supprimer.")
        return
    device_id = self.devices_table.item(row, 1).text()
    if QMessageBox.question(
        self, "Supprimer",
        f"Supprimer la trace de l'appareil {device_id} ?\n\n"
        "L'utilisateur pourra recommencer un essai.",
        QMessageBox.Yes | QMessageBox.No
    ) == QMessageBox.Yes:
        def req():
            r = requests.delete(f"{API_URL}/api/admin/devices/{device_id}", timeout=12)
            return (True, "OK", r.json()) if r.status_code == 200 else (False, r.text, None)
        self._start_worker(req, lambda _: self.load_devices())
