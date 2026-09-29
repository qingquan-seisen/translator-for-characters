from __future__ import annotations

import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from app_core import is_csv_header_row, read_japanese_text, read_text

from PySide6.QtCore import QThread, Signal, QObject, Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSplitter, QTextEdit, QVBoxLayout,
    QWidget, QTabWidget, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QInputDialog
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def resource_path(name: str) -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, name)
    return str(BASE_DIR / name)


def user_data_path(name: str) -> str:
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        return os.path.join(exe_dir, name)
    return str(BASE_DIR / name)


class WorkerSignals(QObject):
    output = Signal(str)
    error = Signal(str)
    result = Signal(str, str)
    finished = Signal(bool)


class ProcessWorker(QThread):
    def __init__(self, cmd: list[str]):
        super().__init__()
        self.cmd = cmd
        self.process: subprocess.Popen | None = None
        self.signals = WorkerSignals()
        self._stop_requested = False

    def run(self):
        success = False
        try:
            creationflags = 0
            startupinfo = None
            kwargs = {}
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            else:
                kwargs["start_new_session"] = True
            self.process = subprocess.Popen(
                self.cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                startupinfo=startupinfo,
                **kwargs,
            )
            assert self.process.stdout is not None
            for raw in iter(self.process.stdout.readline, ""):
                if raw == "":
                    break
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("[LOG]"):
                    self.signals.output.emit(line[5:])
                    continue
                try:
                    payload = json.loads(line)
                    if payload.get("success"):
                        if "result" in payload:
                            self.signals.result.emit(payload["result"], payload.get("filename", ""))
                        elif "report" in payload:
                            self.signals.result.emit(payload["report"], payload.get("filename", ""))
                        else:
                            self.signals.output.emit(json.dumps(payload, ensure_ascii=False))
                        success = True
                    else:
                        self.signals.error.emit(payload.get("error", "任务失败"))
                except json.JSONDecodeError:
                    self.signals.output.emit(line)
            code = self.process.wait()
            success = success and code == 0 and not self._stop_requested
        except Exception as e:
            if not self._stop_requested:
                self.signals.error.emit(f"错误: {e}")
        finally:
            self.signals.finished.emit(success)

    def stop(self):
        self._stop_requested = True
        p = self.process
        if not p or p.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:
                pass


class ApiConfigDialog(QDialog):
    """API 配置对话框"""

    def __init__(self, parent=None, lock_close: bool = False):
        super().__init__(parent)
        self.setWindowTitle("配置 API")
        self.setMinimumWidth(520)
        # lock_close 仅为兼容旧调用保留；配置窗口始终可以取消/关闭
        self.lock_close = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.input_base = QLineEdit()
        self.input_base.setPlaceholderText("https://api.deepseek.com/v1")
        self.input_model = QLineEdit()
        self.input_model.setPlaceholderText("deepseek-v4-flash")
        self.input_key = QLineEdit()
        self.input_key.setPlaceholderText("sk-...")
        self.input_key.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("OPENAI_API_BASE:", self.input_base)
        form.addRow("OPENAI_MODEL_NAME:", self.input_model)
        form.addRow("OPENAI_API_KEY:", self.input_key)

        layout.addLayout(form)

        hint = QLabel("提示：OPENAI_API_KEY 必须填写。保存后写入项目根目录的 .env 文件。")
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(hint)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self._on_save)
        self.button_box.rejected.connect(self._on_cancel)
        layout.addWidget(self.button_box)

        self._load_existing()

    def _load_existing(self):
        env_path = BASE_DIR / ".env"
        config = {"OPENAI_API_BASE": "", "OPENAI_MODEL_NAME": "", "OPENAI_API_KEY": ""}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k in config:
                        config[k] = v
        self.input_base.setText(config["OPENAI_API_BASE"] or "https://api.deepseek.com/v1")
        self.input_model.setText(config["OPENAI_MODEL_NAME"] or "deepseek-v4-flash")
        self.input_key.setText(config["OPENAI_API_KEY"])

    def _on_save(self):
        base = self.input_base.text().strip() or "https://api.deepseek.com/v1"
        model = self.input_model.text().strip() or "deepseek-v4-flash"
        key = self.input_key.text().strip()

        if not key:
            QMessageBox.warning(self, "提示", "OPENAI_API_KEY 必须填写！")
            return

        env_path = BASE_DIR / ".env"
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        config_map = {
            "OPENAI_API_BASE": base,
            "OPENAI_MODEL_NAME": model,
            "OPENAI_API_KEY": key,
        }

        updated = {k: False for k in config_map}
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in config_map:
                    new_lines.append(f"{k}={config_map[k]}")
                    updated[k] = True
                    continue
            new_lines.append(line)

        for k, v in config_map.items():
            if not updated[k]:
                new_lines.append(f"{k}={v}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        os.environ["OPENAI_API_BASE"] = base
        os.environ["OPENAI_MODEL_NAME"] = model
        os.environ["OPENAI_API_KEY"] = key

        self.lock_close = False
        QMessageBox.information(self, "成功", "API 配置已保存！")
        self.accept()

    def _on_cancel(self):
        self.reject()

    def closeEvent(self, event):
        # 允许随时关闭：未填写 API Key 也可以取消配置并退出
        event.accept()

    def reject(self):
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("角色台词翻译器（作者：清泉）")
        self.resize(1100, 780)
        self.worker: ProcessWorker | None = None
        self.current_original_file: str | None = None
        self._temp_files: list[str] = []
        self.last_report = ""
        self._last_was_progress = False   # 上一次是不是进度行

        # 来源类型跟踪
        self.original_source_type: str = "text"
        self.original_csv_data: list[dict] | None = None

        self._build_ui()

        # 启动时检查 API 配置：仅记录提示，不强制弹出配置窗口
        # 用户可以不配置 API Key 直接关闭软件
        if not self.check_api_config():
            QTimer.singleShot(0, self._warn_api_config_missing)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        self.setFont(QFont("Microsoft YaHei", 10))

        # 标题行 + 右上角"配置API"按钮
        title_row = QHBoxLayout()

        title = QLabel("🎙️ 角色台词翻译器 ")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 21px; font-weight: bold; padding: 5px;")

        left_spacer = QLabel()
        left_spacer.setFixedWidth(120)

        self.btn_config = QPushButton("⚙️ 配置API")
        self.btn_config.setFixedSize(120, 32)
        self.btn_config.clicked.connect(self.open_api_config)

        title_row.addWidget(left_spacer)
        title_row.addStretch()
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.btn_config)

        layout.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.original_text = QTextEdit()
        self.original_text.setPlaceholderText("手动输入，或点击下方按钮导入原文")
        self.translation_text = QTextEdit()
        self.translation_text.setPlaceholderText("翻译结果将显示在这里，也可手动输入或导入译文")
        for label, editor, buttons in [
            ("📝 原文", self.original_text, ["📂 导入原文", "🗑️ 清空原文"]),
            ("📄 译文", self.translation_text, ["📂 导入译文", "🗑️ 清空译文"]),
        ]:
            w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(5,5,5,5)
            lab = QLabel(label); lab.setStyleSheet("font-weight:bold; font-size:14px;"); v.addWidget(lab); v.addWidget(editor)
            row = QHBoxLayout()
            if label.startswith("📝"):
                b = QPushButton(buttons[0]); b.clicked.connect(self.load_original_file); row.addWidget(b)
                b = QPushButton(buttons[1]); b.clicked.connect(self.clear_original); row.addWidget(b)
            else:
                b = QPushButton(buttons[0]); b.clicked.connect(self.import_translation_file); row.addWidget(b)
                b = QPushButton(buttons[1]); b.clicked.connect(self.translation_text.clear); row.addWidget(b)
            row.addStretch(); v.addLayout(row); splitter.addWidget(w)
        splitter.setSizes([550, 550]); layout.addWidget(splitter, 3)

        actions = QHBoxLayout()
        self.btn_translate = QPushButton("🔍 翻译"); self.btn_translate.clicked.connect(self.start_translate)
        self.btn_review = QPushButton("📊 译文质检"); self.btn_review.clicked.connect(self.start_review)
        self.btn_extract = QPushButton("📚 提取知识"); self.btn_extract.clicked.connect(self.start_extract)
        self.btn_stop = QPushButton("⛔ 终止任务"); self.btn_stop.clicked.connect(self.stop_task); self.btn_stop.setEnabled(False)
        for b in (self.btn_translate, self.btn_review, self.btn_extract, self.btn_stop):
            b.setMinimumHeight(34); actions.addWidget(b)
        actions.addStretch(); layout.addLayout(actions)

        self.progress = QProgressBar(); self.progress.setVisible(False); self.progress.setMaximumHeight(16); layout.addWidget(self.progress)

        tabs = QTabWidget()
        log_box = QGroupBox("📋 执行日志"); log_layout = QVBoxLayout(log_box)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); log_layout.addWidget(self.log_text)
        tabs.addTab(log_box, "日志")
        report_box = QGroupBox("📊 质检报告"); report_layout = QVBoxLayout(report_box)
        self.report_text = QTextEdit(); self.report_text.setReadOnly(True); report_layout.addWidget(self.report_text)
        tabs.addTab(report_box, "质检报告")
        layout.addWidget(tabs, 1)
        self.status = QLabel("✅ 就绪"); self.statusBar().addWidget(self.status)

    def append_log(self, text: str):
        noise_patterns = [
            "Loading weights",
            "it/s",
            "Downloading",
            "100%",
            "Building prefix",
            "tokenizer",
            "Warning: You are sending unauthenticated requests",
            "HF_TOKEN",
            "huggingface_hub",
        ]
        for pattern in noise_patterns:
            if pattern in text:
                return

        text = text.replace("[LOG]", "")

        # 判断是不是进度行（翻译中 / 质检中）
        is_progress = text.startswith("⌛ 翻译中") or text.startswith("⌛ 质检中")

        if is_progress:
            if self._last_was_progress:
                # 上一次是进度行 → 覆盖最后一行
                cursor = self.log_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                cursor.insertText(text)
                self.log_text.setTextCursor(cursor)
            else:
                # 上一次不是进度行 → 另起一行
                self.log_text.append(text)
                c = self.log_text.textCursor()
                c.movePosition(QTextCursor.MoveOperation.End)
                self.log_text.setTextCursor(c)
            self._last_was_progress = True
        else:
            self.log_text.append(text)
            c = self.log_text.textCursor()
            c.movePosition(QTextCursor.MoveOperation.End)
            self.log_text.setTextCursor(c)
            self._last_was_progress = False

    def set_busy(self, busy: bool, status: str = "处理中..."):
        for b in (self.btn_translate, self.btn_review, self.btn_extract): b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy); self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1); self.status.setText(status)

    def run_command(self, cmd: list[str], result_handler, action: str):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "已有任务正在运行，请先终止当前任务。"); return
        self.set_busy(True, f"🔄 {action}..."); self.append_log(f"🔄 开始{action}...")
        self.worker = ProcessWorker(cmd)
        self.worker.signals.output.connect(self.append_log)
        self.worker.signals.error.connect(lambda e: self.on_worker_error(e))
        self.worker.signals.result.connect(result_handler)
        self.worker.signals.finished.connect(lambda ok: self.on_worker_finished(ok, action))
        self.worker.start()

    def _write_temp_text(self, text: str) -> str:
        """把长文本写到临时文件，避免超出 Windows 命令行长度上限。"""
        fd, path = tempfile.mkstemp(prefix="tat_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self._temp_files.append(path)
        return path

    def _cleanup_temp_files(self):
        for path in self._temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temp_files.clear()

    def start_translate(self):
        text = self.original_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先输入原文或加载 input.txt！")
            return

        text_file = self._write_temp_text(text)
        cmd = [sys.executable, resource_path("gui_translate.py"), "--text-file", text_file]
        # CSV 模式不保存 TXT（只保存 CSV）
        if self.original_source_type == "csv":
            cmd.append("--no-save-txt")
        self.run_command(cmd, self.on_translate_result, "翻译")

    def on_translate_result(self, result: str, filename: str):
        self.translation_text.setPlainText(result)

        # 如果原文来自 CSV，额外保存一份 CSV 译文
        if self.original_source_type == "csv":
            self._save_csv_translation(result)

    def start_review(self):
        original = self.original_text.toPlainText().strip()
        translation = self.translation_text.toPlainText().strip()
        if not original or not translation:
            QMessageBox.warning(self, "提示", "原文和译文都不能为空！")
            return

        # Yes/No 询问是否使用参考译文
        reply = QMessageBox.question(
            self, "参考译文",
            "是否加载参考译文进行辅助对比？\n\n"
            "是：选择参考译文文件（TXT 或 CSV）\n"
            "否：直接质检",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        ref = None
        if reply == QMessageBox.StandardButton.Yes:
            ref, _ = QFileDialog.getOpenFileName(
                self, "选择参考文件",
                "",
                "所有支持 (*.txt *.csv);;文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*)"
            )
            if not ref:
                return

        original_file = self._write_temp_text(original)
        translation_file = self._write_temp_text(translation)
        cmd = [sys.executable, resource_path("gui_review.py"),
               "--original-file", original_file, "--translation-file", translation_file]
        if ref:
            cmd += ["--reference", ref]
        self.run_command(cmd, self.on_review_result, "质检")

    def on_review_result(self, report: str, filename: str):
        self.last_report = report
        self.report_text.setPlainText(report)

    def start_extract(self):
        items = [
            "1 级：只提取世界观（最快）",
            "2 级：提取世界观 + 术语（推荐）",
            "3 级：提取世界观 + 术语 + 角色语气（最完整）"
        ]
        level, ok = QInputDialog.getItem(
            self, "选择提取等级",
            "请选择知识提取的等级：",
            items, 1, False
        )
        if not ok:
            return

        if level.startswith("1"):
            lv = 1
        elif level.startswith("2"):
            lv = 2
        else:
            lv = 3

        cmd = [sys.executable, resource_path("gui_extract.py"), "--level", str(lv)]
        self.run_command(cmd, self.on_extract_result, f"{lv}级提取")

    def on_extract_result(self, payload: str, filename: str):
        try:
            data = json.loads(payload)
            if data.get("success"):
                level = data.get("level", 3)
                mode = data.get("mode", "?")
                scenes = data.get("scenes", 0)
                texts = data.get("texts", 0)
                chars = data.get("characters", 0)
                terms = data.get("terms", 0)
                world_len = data.get("world_setting_len", 0)

                mode_desc = {
                    "bilingual": "双语对照",
                    "ch_only": "仅中文",
                    "jp_only": "仅日文"
                }.get(mode, mode)

                self.append_log(f"✅ 知识提取完成（{level}级 / {mode_desc}）")
                self.append_log(f"   场景: {scenes} 个 | 文本: {texts} 条")
                self.append_log(f"   术语: {terms} 个 | 角色: {chars} 个 | 世界观: {world_len} 字")
                self.append_log(f"   输出: {data.get('output_dir', '')}/knowledge_latest.json")
            else:
                self.append_log(f"❌ 提取失败：{data.get('error', '未知错误')}")
        except json.JSONDecodeError:
            self.append_log(payload)

    def on_worker_error(self, error: str):
        self.append_log(f"❌ {error}")
        self.status.setText("❌ 出错")

    def on_worker_finished(self, ok: bool, action: str):
        self.worker = None
        self._cleanup_temp_files()
        self.set_busy(False, f"{'✅' if ok else '⛔'} {action}{'完成' if ok else '已结束'}")

    def stop_task(self):
        if self.worker and self.worker.isRunning():
            self.append_log("⛔ 正在终止子进程树...")

            import threading

            def kill_process():
                try:
                    if os.name == "nt":
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = subprocess.SW_HIDE
                        subprocess.run(
                            ["taskkill", "/PID", str(self.worker.process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=2,
                            startupinfo=startupinfo
                        )
                    else:
                        try:
                            os.killpg(os.getpgid(self.worker.process.pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                except subprocess.TimeoutExpired:
                    try:
                        self.worker.process.kill()
                    except Exception:
                        pass
                except Exception:
                    try:
                        self.worker.process.kill()
                    except Exception:
                        pass

                try:
                    self.worker.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        self.worker.process.kill()
                        self.worker.process.wait(timeout=1)
                    except Exception:
                        pass

                self.append_log("⛔ 任务已终止")
                self.set_busy(False, "⛔ 已终止")

            threading.Thread(target=kill_process, daemon=True).start()
        else:
            self.append_log("ℹ️ 当前没有运行中的任务")
            self.set_busy(False, "⛔ 已终止")

    # ===================== 清空原文 =====================
    def clear_original(self):
        """清空原文框，并重置来源类型"""
        self.original_text.clear()
        self.current_original_file = None
        self.original_source_type = "text"
        self.original_csv_data = None
        self.append_log("🗑️ 已清空原文")

    # ===================== 导入原文 =====================
    def load_original_file(self):
        """导入原文文件（支持 TXT 和 CSV）"""
        p, _ = QFileDialog.getOpenFileName(
            self, "选择原文",
            "",
            "所有支持 (*.txt *.csv);;文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not p:
            return

        try:
            path = Path(p)
            if path.suffix.lower() == ".csv":
                self._load_csv_original(path)
            else:
                self._load_txt_original(path)
        except Exception as e:
            QMessageBox.critical(self, "读取失败", str(e))

    def _load_txt_original(self, path: Path):
        """导入 TXT 文件"""
        text = read_text(path).strip()
        self.original_text.setPlainText(text)
        self.current_original_file = str(path)
        self.original_source_type = "text"
        self.original_csv_data = None
        self.append_log(f"📂 加载原文: {path.name}（{len(text)} 字符）")

    def _load_csv_original(self, path: Path):
        """导入 CSV 文件（角色名,台词 格式），转成【角色名】台词"""
        content = read_text(path)
        reader = csv.reader(content.splitlines())

        rows = []
        first = True
        for row in reader:
            if len(row) < 2:
                continue
            if first:
                first = False
                if is_csv_header_row(row):
                    continue
            speaker = row[0].strip()
            text = row[1].strip()
            if speaker and text:
                rows.append({"speaker": speaker, "text": text})

        if not rows:
            raise ValueError("CSV 文件为空或格式不正确（需要 角色名,台词 格式）")

        lines = []
        for row in rows:
            lines.append(f"【{row['speaker']}】{row['text']}")
        text = "\n".join(lines)

        self.original_text.setPlainText(text)
        self.current_original_file = str(path)
        self.original_source_type = "csv"
        self.original_csv_data = rows
        self.append_log(f"📂 导入 CSV: {path.name}（{len(rows)} 条）")

    def _save_csv_translation(self, result_text: str):
        """把译文保存成 CSV（角色名,译文）"""
        import re as _re
        segments = []
        pattern = _re.compile(r'^\s*【([^】]+)】\s*(.*)$')
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                segments.append({"speaker": m.group(1).strip(), "text": m.group(2).strip()})
            else:
                if segments:
                    segments[-1]["text"] += "\n" + line
                else:
                    segments.append({"speaker": "", "text": line})

        if not segments:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = OUTPUT_DIR / f"translation_results_{timestamp}.csv"

        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            for seg in segments:
                if seg["speaker"]:
                    writer.writerow([seg["speaker"], seg["text"]])
                else:
                    writer.writerow(["", seg["text"]])

        self.append_log(f"💾 CSV 译文已保存: {csv_path.name}")

    # ===================== 导入译文 =====================
    def import_translation_file(self):
        """导入译文文件（支持 TXT 和 CSV）"""
        p, _ = QFileDialog.getOpenFileName(
            self, "选择译文",
            "",
            "所有支持 (*.txt *.csv);;文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not p:
            return

        try:
            path = Path(p)
            if path.suffix.lower() == ".csv":
                self._load_csv_translation(path)
            else:
                self._load_txt_translation(path)
        except Exception as e:
            QMessageBox.critical(self, "读取失败", str(e))

    def _load_txt_translation(self, path: Path):
        """导入 TXT 译文"""
        text = read_text(path).strip()
        self.translation_text.setPlainText(text)
        self.append_log(f"📂 导入译文: {path.name}（{len(text)} 字符）")

    def _load_csv_translation(self, path: Path):
        """导入 CSV 译文（角色名,译文 格式），转成【角色名】译文"""
        content = read_japanese_text(path)
        reader = csv.reader(content.splitlines())

        rows = []
        first = True
        for row in reader:
            if len(row) < 2:
                continue
            if first:
                first = False
                if is_csv_header_row(row):
                    continue
            speaker = row[0].strip()
            text = row[1].strip()
            if speaker and text:
                rows.append({"speaker": speaker, "text": text})

        if not rows:
            raise ValueError("CSV 文件为空或格式不正确（需要 角色名,译文 格式）")

        lines = []
        for row in rows:
            lines.append(f"【{row['speaker']}】{row['text']}")
        text = "\n".join(lines)

        self.translation_text.setPlainText(text)
        self.append_log(f"📂 导入 CSV 译文: {path.name}（{len(rows)} 条）")

    # ===================== API 配置 =====================
    def check_api_config(self) -> bool:
        env_path = BASE_DIR / ".env"
        if not env_path.exists():
            return False
        config = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
        base = config.get("OPENAI_API_BASE", "").strip()
        model = config.get("OPENAI_MODEL_NAME", "").strip()
        key = config.get("OPENAI_API_KEY", "").strip()
        if not base or not model or not key:
            return False
        if not key.startswith("sk-") or len(key) < 10:
            return False
        return True

    def open_api_config(self):
        dialog = ApiConfigDialog(self, lock_close=False)
        dialog.exec()

    def _warn_api_config_missing(self):
        """配置缺失时只提示一次，不阻塞用户操作。"""
        self.append_log("⚠️ 检测到 API 配置不完整，请先完成配置。")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()