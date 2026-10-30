"""Reports page: turnaround time, workload, OOS trends and control charts."""

from __future__ import annotations

from datetime import date

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QDate, QMargins, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import reports
from aurum_lims.services.reports import Period
from aurum_lims.ui.common import Card, GridTable, format_local, muted_label, show_error

GOLD, GOLD_LIGHT, GREEN, RED, GREY, BLUE = "#9a7b1c", "#d9bf6a", "#067647", "#b42318", "#8b98a8", "#3b6ea8"
PERIODS = [("Last 30 days", 30), ("Last 90 days", 90), ("Last 12 months", 365), ("This year", "year"),
           ("Custom range", "custom")]


def _fmt(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.{digits}f}{suffix}"


def _chart(title: str) -> QChart:
    chart = QChart()
    chart.setTitle(title)
    chart.setBackgroundRoundness(0)
    chart.setMargins(QMargins(4, 4, 4, 4))
    chart.legend().setAlignment(Qt.AlignBottom)
    chart.setAnimationOptions(QChart.NoAnimation)
    return chart


def bar_chart(title: str, categories: list[str], series: list[tuple[str, list[float], str]],
              y_title: str = "") -> QChart:
    chart = _chart(title)
    bars = QBarSeries()
    for name, values, colour in series:
        bar_set = QBarSet(name)
        bar_set.append([float(v or 0) for v in values])
        bar_set.setColor(QColor(colour))
        bar_set.setBorderColor(QColor(colour))
        bars.append(bar_set)
    chart.addSeries(bars)
    x_axis = QBarCategoryAxis()
    x_axis.append(categories or [""])
    if len(categories) > 6:
        x_axis.setLabelsAngle(-90)  # many weeks: turn the labels so they are not cut off
    y_axis = QValueAxis()
    y_axis.setTitleText(y_title)
    y_axis.setLabelFormat("%.0f")
    top = max([float(v or 0) for _, values, _ in series for v in values] + [1])
    y_axis.setRange(0, top * 1.15)
    y_axis.applyNiceNumbers()
    chart.addAxis(x_axis, Qt.AlignBottom)
    chart.addAxis(y_axis, Qt.AlignLeft)
    bars.attachAxis(x_axis)
    bars.attachAxis(y_axis)
    chart.legend().setVisible(len(series) > 1)
    return chart


def chart_view(chart: QChart, height: int = 280) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setMinimumHeight(height)
    return view


def _time_label(day: date, months: bool) -> str:
    return day.strftime("%b %y") if months else day.strftime("%d/%m")


def _table(headers: list[str], stretch: int | None = None) -> GridTable:
    table = GridTable(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch is not None:
        table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    table.setMinimumHeight(220)
    return table


def _fill(table: GridTable, rows: list[list], colours: dict[int, list[str | None]] | None = None) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            if isinstance(value, (int, float)):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            colour = (colours or {}).get(c, [None] * len(rows))[r]
            if colour:
                item.setForeground(QColor(colour))
            table.setItem(r, c, item)


class _Tab(QScrollArea):
    """One report: figures across the top, then charts, then the table behind them."""

    def __init__(self, figures: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("Content")
        self.setWidget(content)
        self.layout_ = QVBoxLayout(content)
        self.layout_.setContentsMargins(4, 12, 4, 12)
        self.layout_.setSpacing(12)
        row = QGridLayout()
        row.setSpacing(12)
        self.figures: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(figures):
            card = Card(title)
            value = QLabel("—")
            value.setObjectName("CardValue")
            card.body.addWidget(value)
            self.figures[key] = value
            row.addWidget(card, 0, column)
        self.layout_.addLayout(row)
        self.charts = QHBoxLayout()
        self.charts.setSpacing(12)
        self.layout_.addLayout(self.charts)

    def set_charts(self, *charts: QChart) -> None:
        while self.charts.count():
            item = self.charts.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for chart in charts:
            self.charts.addWidget(chart_view(chart), 1)

    def add_table(self, title: str, table: GridTable, export) -> None:
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{title}</b>"))
        header.addStretch()
        button = QPushButton("Export to CSV…")
        button.clicked.connect(lambda *_: export())
        header.addWidget(button)
        self.layout_.addLayout(header)
        self.layout_.addWidget(table)


class ReportsView(QWidget):
    title = "Reports"

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.exports: dict[str, tuple[list[str], list[list]]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Period"))
        self.period = QComboBox()
        for label, value in PERIODS:
            self.period.addItem(label, value)
        self.period.setCurrentIndex(1)
        self.start_date, self.end_date = QDateEdit(), QDateEdit()
        for edit in (self.start_date, self.end_date):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date.setDate(QDate.currentDate().addMonths(-3))
        self.end_date.setDate(QDate.currentDate())
        self.to_label = QLabel("to")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda *_: self.refresh())
        self.period.currentIndexChanged.connect(lambda *_: self._period_changed())
        for widget in (self.period, self.start_date, self.to_label, self.end_date, refresh):
            bar.addWidget(widget)
        bar.addStretch()
        self.period_label = muted_label("")
        bar.addWidget(self.period_label)
        layout.addLayout(bar)
        self._period_changed(refresh=False)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_turnaround()
        self._build_workload()
        self._build_oos()
        self._build_control()

    # ----- building -----
    def _build_turnaround(self) -> None:
        self.tat_tab = _Tab([("count", "Samples decided"), ("median", "Median turnaround"),
                             ("p90", "90% decided within"), ("on_time", "On time (by due date)")])
        self.tat_table = _table(["Sample ID", "Type", "Material / point", "Priority", "Received", "Decided",
                                 "Decision", "Days", "Due", "On time"], 2)
        self.tat_tab.add_table("Samples decided in the period", self.tat_table, lambda: self._export("Turnaround"))
        self.tabs.addTab(self.tat_tab, "Turnaround")

    def _build_workload(self) -> None:
        self.work_tab = _Tab([("received", "Samples received"), ("decided", "Samples decided"),
                              ("signed", "Tests signed"), ("open", "Open samples now")])
        self.work_table = _table(["Person", "Tests signed", "Tests reviewed"], 0)
        self.work_tab.add_table("Work by person", self.work_table, lambda: self._export("Workload"))
        self.tabs.addTab(self.work_tab, "Workload")

    def _build_oos(self) -> None:
        self.oos_tab = _Tab([("count", "OOS opened"), ("rate", "OOS per 100 tests"), ("open", "Open now"),
                             ("confirmed", "Confirmed OOS")])
        self.oos_table = _table(["Number", "Opened", "Sample ID", "Test", "Parameter", "Result", "Limit",
                                 "Status", "Conclusion"], 8)
        self.oos_tab.add_table("OOS investigations opened in the period", self.oos_table,
                               lambda: self._export("OOS"))
        self.tabs.addTab(self.oos_tab, "OOS")

    def _build_control(self) -> None:
        self.ctl_tab = _Tab([("n", "Results plotted"), ("mean", "Mean"), ("sigma", "Sigma (MR̄/1.128)"),
                             ("cpk", "Cpk"), ("signals", "Points flagged")])
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Result"))
        self.option = QComboBox()
        self.option.setMinimumWidth(320)
        self.material = QComboBox()
        self.material.setMinimumWidth(220)
        pick.addWidget(self.option)
        pick.addWidget(QLabel("Material"))
        pick.addWidget(self.material)
        pick.addStretch()
        self.ctl_tab.layout_.insertLayout(0, pick)
        self.ctl_note = muted_label("")
        self.ctl_tab.layout_.insertWidget(2, self.ctl_note)
        self.option.currentIndexChanged.connect(lambda *_: self._option_changed())
        self.material.currentIndexChanged.connect(lambda *_: self._refresh_control())
        self.ctl_table = _table(["#", "Signed", "Sample ID", "Lot", "Result", "Conformance", "Flags"], 6)
        self.ctl_tab.add_table("Results in order", self.ctl_table, lambda: self._export("Control chart"))
        self.tabs.addTab(self.ctl_tab, "Control chart")

    # ----- period -----
    def _period_changed(self, refresh: bool = True) -> None:
        custom = self.period.currentData() == "custom"
        for widget in (self.start_date, self.to_label, self.end_date):
            widget.setVisible(custom)
        if refresh:
            self.refresh()

    def current_period(self) -> Period:
        choice = self.period.currentData()
        if choice == "custom":
            return Period.between(self.start_date.date().toPython(), self.end_date.date().toPython())
        if choice == "year":
            return Period.between(date.today().replace(month=1, day=1), date.today())
        return Period.last_days(int(choice))

    # ----- data -----
    def refresh(self) -> None:
        period = self.current_period()
        self.period_label.setText(f"{format_local(period.start)} to {format_local(period.end)}")
        with self.db.session() as session:
            tat = reports.turnaround(session, self.user, period)
            work = reports.workload(session, self.user, period)
            oos = reports.oos_summary(session, self.user, period)
            options = reports.trend_options(session)
        self._show_turnaround(tat)
        self._show_workload(work)
        self._show_oos(oos)
        current = self.option.currentData()
        self.option.blockSignals(True)
        self.option.clear()
        for option in options:
            self.option.addItem(option.label, option)
        index = next((i for i, o in enumerate(options) if o == current), 0)
        self.option.setCurrentIndex(index if options else -1)
        self.option.blockSignals(False)
        self._option_changed()

    def _show_turnaround(self, tat: reports.Turnaround) -> None:
        f = self.tat_tab.figures
        f["count"].setText(str(len(tat.rows)))
        f["median"].setText(_fmt(tat.median_days, 1, " days"))
        f["p90"].setText(_fmt(tat.p90_days, 1, " days"))
        f["on_time"].setText(_fmt(tat.on_time_percent, 0, " %"))
        months = reports.use_months(self.current_period())
        weekly = bar_chart(f"Median turnaround by {'month' if months else 'week'} (days)",
                           [_time_label(b, months) for b, _, _ in tat.timeline],
                           [("Median days", [m or 0 for _, _, m in tat.timeline], GOLD)], "days")
        by_type = bar_chart("Median turnaround by sample type (days)", [t for t, _, _ in tat.by_type],
                            [("Median days", [m for _, _, m in tat.by_type], BLUE)], "days")
        self.tat_tab.set_charts(weekly, by_type)
        rows = [[r.sample_id, r.sample_type, r.material or "", r.priority, format_local(r.received_at),
                 format_local(r.decided_at), r.decision, r.days, r.due_date.isoformat() if r.due_date else "",
                 "" if r.on_time is None else ("Yes" if r.on_time else "No")] for r in tat.rows]
        _fill(self.tat_table, rows, {9: [RED if r.on_time is False else None for r in tat.rows]})
        self.exports["Turnaround"] = ([self.tat_table.horizontalHeaderItem(c).text()
                                       for c in range(self.tat_table.columnCount())], rows)

    def _show_workload(self, work: reports.Workload) -> None:
        f = self.work_tab.figures
        f["received"].setText(str(work.received))
        f["decided"].setText(str(work.decided))
        f["signed"].setText(str(work.tests_signed))
        f["open"].setText(str(sum(n for _, n in work.backlog)))
        months = reports.use_months(self.current_period())
        weekly = bar_chart(f"Samples per {'month' if months else 'week'}",
                           [_time_label(b, months) for b, _, _ in work.timeline],
                           [("Received", [r for _, r, _ in work.timeline], GOLD_LIGHT),
                            ("Decided", [d for _, _, d in work.timeline], GREEN)], "samples")
        backlog = bar_chart("Open samples by status (now)", [s for s, _ in work.backlog],
                            [("Samples", [n for _, n in work.backlog], BLUE)], "samples")
        self.work_tab.set_charts(weekly, backlog)
        rows = [[name, signed, reviewed] for name, signed, reviewed in work.people]
        _fill(self.work_table, rows)
        self.exports["Workload"] = (["Person", "Tests signed", "Tests reviewed"], rows)

    def _show_oos(self, oos: reports.OosSummary) -> None:
        f = self.oos_tab.figures
        f["count"].setText(str(len(oos.rows)))
        f["rate"].setText(_fmt(oos.rate_per_100, 1))
        f["open"].setText(str(oos.open_now))
        f["open"].setStyleSheet(f"color:{RED}" if oos.open_now else "")
        f["confirmed"].setText(str(dict(oos.by_conclusion).get("Confirmed OOS", 0)))
        monthly = bar_chart("OOS opened per month", [m.strftime("%b %Y") for m, _ in oos.monthly],
                            [("OOS", [n for _, n in oos.monthly], RED)], "investigations")
        by_test = bar_chart("OOS by test", [t for t, _ in oos.by_test[:8]],
                            [("OOS", [n for _, n in oos.by_test[:8]], GOLD)], "investigations")
        self.oos_tab.set_charts(monthly, by_test)
        rows = [[r.number, format_local(r.opened_at), r.sample_id, r.test, r.parameter, r.result, r.limit or "",
                 r.status, r.conclusion or ""] for r in oos.rows]
        _fill(self.oos_table, rows)
        self.exports["OOS"] = ([self.oos_table.horizontalHeaderItem(c).text()
                                for c in range(self.oos_table.columnCount())], rows)

    def _option_changed(self) -> None:
        option = self.option.currentData()
        current = self.material.currentData()
        self.material.blockSignals(True)
        self.material.clear()
        self.material.addItem("All materials", None)
        if option is not None:
            with self.db.session() as session:
                for name in reports.trend_materials(session, option.method_code):
                    self.material.addItem(name, name)
        self.material.setCurrentIndex(max(self.material.findData(current), 0))
        self.material.blockSignals(False)
        self._refresh_control()

    def _refresh_control(self) -> None:
        option = self.option.currentData()
        if option is None:
            self.ctl_note.setText("No method has a number result yet.")
            self.ctl_tab.set_charts()
            _fill(self.ctl_table, [])
            self.exports["Control chart"] = ([], [])
            return
        with self.db.session() as session:
            trend = reports.trend(session, self.user, option, self.current_period(), self.material.currentData())
        f = self.ctl_tab.figures
        unit = f" {option.unit}" if option.unit else ""
        f["n"].setText(str(len(trend.points)))
        f["mean"].setText(_fmt(trend.mean, 3, unit))
        f["sigma"].setText(_fmt(trend.sigma, 3, unit))
        f["cpk"].setText(_fmt(trend.cpk, 2))
        f["cpk"].setStyleSheet(f"color:{RED}" if trend.cpk is not None and trend.cpk < 1.33 else "")
        f["signals"].setText(str(trend.signal_count))
        f["signals"].setStyleSheet(f"color:{RED}" if trend.signal_count else "")
        notes = [f"Specification: {trend.spec_text}" if trend.spec_text else "No specification limit found.",
                 "Control limits are mean ± 3 sigma; Cpk below 1.33 is shown in red."]
        if trend.not_quantified:
            notes.append(f"{trend.not_quantified} result(s) reported as ND or < LOQ are not plotted.")
        if len(trend.points) < 2:
            notes.append("At least two results are needed for control limits.")
        self.ctl_note.setText("  ·  ".join(notes))
        self.ctl_tab.set_charts(self._control_chart(trend))
        rows = [[i, format_local(p.when), p.sample_id, p.lot_number or "", p.reported, p.conformance,
                 ", ".join(p.signals)] for i, p in enumerate(trend.points, start=1)]
        _fill(self.ctl_table, rows, {6: [RED if p.signals else None for p in trend.points],
                                     5: [RED if p.conformance == "Fail" else None for p in trend.points]})
        self.exports["Control chart"] = (["#", "Signed", "Sample ID", "Lot", "Result", "Conformance", "Flags"], rows)

    def _control_chart(self, trend: reports.Trend) -> QChart:
        chart = _chart(f"Control chart – {trend.option.label}")
        n = len(trend.points)
        values = QLineSeries()
        values.setName("Result")
        values.setPointsVisible(True)
        values.setPen(QPen(QColor(BLUE), 2))
        flagged = QScatterSeries()
        flagged.setName("Flagged")
        flagged.setColor(QColor(RED))
        flagged.setBorderColor(QColor(RED))
        flagged.setMarkerSize(11)
        for i, p in enumerate(trend.points, start=1):
            values.append(i, p.value)
            if p.signals:
                flagged.append(i, p.value)
        series = [values, flagged]
        ys = [p.value for p in trend.points]

        def line(name: str, y: float | None, colour: str, style=Qt.DashLine) -> None:
            if y is None or n == 0:
                return
            s = QLineSeries()
            s.setName(name)
            s.append(0, y)
            s.append(n + 1, y)
            s.setPen(QPen(QColor(colour), 1.5, style))
            series.append(s)
            ys.append(y)

        line("Mean", trend.mean, GOLD, Qt.SolidLine)
        line("UCL / LCL (±3σ)", trend.ucl, GREY)
        line("", trend.lcl, GREY)
        line("Specification", trend.spec_high, RED)
        line("" if trend.spec_high is not None else "Specification", trend.spec_low, RED)
        for s in series:
            chart.addSeries(s)
        x_axis, y_axis = QValueAxis(), QValueAxis()
        x_axis.setRange(0, max(n, 1) + 1)
        x_axis.setLabelFormat("%d")
        x_axis.setTickType(QValueAxis.TicksDynamic)
        x_axis.setTickAnchor(0)
        x_axis.setTickInterval(max(1, -(-n // 15)))  # whole result numbers, at most ~15 labels
        x_axis.setTitleText("Result number (in order signed)")
        if ys:
            low, high = min(ys), max(ys)
            pad = (high - low) * 0.1 or abs(high) * 0.01 or 1
            y_axis.setRange(low - pad, high + pad)
        y_axis.setTitleText(trend.option.unit or "")
        chart.addAxis(x_axis, Qt.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignLeft)
        for s in series:
            s.attachAxis(x_axis)
            s.attachAxis(y_axis)
        for marker in chart.legend().markers():
            if not marker.label():
                marker.setVisible(False)
        return chart

    # ----- export -----
    def _export(self, report: str) -> None:
        headers, rows = self.exports.get(report, ([], []))
        if not rows:
            QMessageBox.information(self, "Export", "There is nothing to export for this period.")
            return
        name = f"{report.lower().replace(' ', '_')}_{date.today().isoformat()}.csv"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {report}", name, "CSV files (*.csv)")
        if not path:
            return
        try:
            with self.db.session() as session:
                reports.export_csv(session, self.user, path, report, headers, rows)
        except (AurumError, OSError) as exc:
            show_error(self, exc, "Could not export")
            return
        QMessageBox.information(self, "Export", f"{len(rows)} row(s) saved to:\n{path}")
