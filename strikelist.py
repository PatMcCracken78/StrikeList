#!/usr/bin/env python3
#
# Strike List - Copyright (C) 2026 _Storm_ [2678385]
# Free software under the GNU General Public License v3 or later (see LICENSE.txt).
# Includes the awards engine from YATA by Kivou [2000607] (GPL-3).
"""
Strike List - desktop target tracker for Torn (macOS + Windows).
Your key and data stay on this computer; it only talks to api.torn.com.
"""
import json
import sys
import time
import traceback
import webbrowser

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QRect, QRunnable,
                            QSortFilterProxyModel, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QDialog, QDialogButtonBox, QDoubleSpinBox, QListWidget, QListWidgetItem, QPlainTextEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QButtonGroup, QProgressBar, QScrollArea, QStackedWidget, QStyle, QStyledItemDelegate, QSystemTrayIcon, QTableView, QTabWidget,
                               QVBoxLayout, QWidget)

import core
from pathlib import Path

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"  # works when packaged

APP_NAME = "Strike List"
VERSION = "1.13"

GREEN, YELLOW, RED, BLUE, MUTED = "#5fd38a", "#e8c547", "#ff6b6b", "#6aa9ff", "#8a929c"
FLAG_COLORS = [None, GREEN, YELLOW, RED]
PROFILE_URL = "https://www.torn.com/profiles.php?XID={}"

SOURCE_URL = "https://github.com/PatMcCracken78/StrikeList"   # where the source code is published (GPL)

TOS_HTML = (
    "<table cellspacing='0' cellpadding='4' style='color:#aab2bc'>"
    "<tr><td><b>Data storage</b></td><td>Only on this computer (your data folder below)</td></tr>"
    "<tr><td><b>Data sharing</b></td><td>Nobody. Exception: if you turn on FF Scouter, your API key and the "
    "player IDs you look up are sent to ffscouter.com to get stat estimates</td></tr>"
    "<tr><td><b>Purpose of use</b></td><td>Personal tools: your targets, attack log, chains, awards and war lists</td></tr>"
    "<tr><td><b>Key storage &amp; sharing</b></td><td>Saved only on this computer, readable by your user account only. "
    "Never shared, except with ffscouter.com if you turn FF Scouter on "
    "(<a href='https://ffscouter.com/' style='color:#7aa7ff'>their terms</a>)</td></tr>"
    "<tr><td><b>Key access level</b></td><td>Limited Access</td></tr></table>"
)

ABOUT_HTML = (
    "Strike List {version} — free software under the "
    "<a href='https://www.gnu.org/licenses/gpl-3.0.html' style='color:#7aa7ff'>GNU GPL v3</a>. "
    "Source code: <a href='" + SOURCE_URL + "' style='color:#7aa7ff'>" + SOURCE_URL.replace("https://", "") + "</a><br>"
    "Awards engine from <a href='https://github.com/Kivou-2000607/yata' style='color:#7aa7ff'>YATA</a> by Kivou [2000607]. "
    "Stat estimates by <a href='https://ffscouter.com/' style='color:#7aa7ff'>FF Scouter</a> (optional). "
    "Not affiliated with Torn."
)

ATTACK_URL = "https://www.torn.com/page.php?sid=attack&user2ID={}"


# ===================================================================== helpers
def fmt_ff(v):
    """Torn caps fair fight at 3.00; FF Scouter's estimate can run higher, meaning 'max respect'."""
    if v is None:
        return ""
    return "3.00+" if v > 3.0 else f"{v:.2f}"


def ago(ts):
    if not ts:
        return ""
    s = max(0, int(time.time()) - int(ts))
    if s < 60:
        return f"{s}s ago"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "min")):
        if s >= size:
            n = s // size
            return f"{n} {unit}{'' if n == 1 or unit == 'min' else 's'} ago"


def hms(s):
    s = max(0, int(s))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02}:{x:02}" if h else f"{m}:{x:02}"


def clock(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def is_win(result):
    return result in core.WIN_RESULTS


def status_info(t):
    """-> (text, color, attackable)"""
    if t.get("error"):
        return "error", RED, False
    st, until = t.get("status_state"), t.get("status_until") or 0
    left = until - time.time()
    if not st:
        return "—", MUTED, False
    if st in ("Hospital", "Jail"):
        if left > 0:
            return f"{st[0]} for {hms(left)}", RED, False
        return f"Out {hms(-left)} ago*", YELLOW, True
    if st == "Federal":
        return "Federal", RED, False
    if st in ("Traveling", "Abroad"):
        return t.get("status_desc") or st, BLUE, False
    if st == "Okay":
        return "Okay", GREEN, True
    return t.get("status_desc") or st, MUTED, False


# ===================================================================== background work
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Job(QRunnable):
    """Runs fn(progress=cb) off the GUI thread and reports back with signals."""

    def __init__(self, fn, *args, with_progress=False):
        super().__init__()
        self.fn, self.args, self.with_progress = fn, args, with_progress
        self.signals = _Signals()

    def run(self):
        try:
            if self.with_progress:
                res = self.fn(*self.args, progress=self.signals.progress.emit)
            else:
                res = self.fn(*self.args)
            self.signals.done.emit(res)
        except core.TornError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # never let a worker crash the app
            traceback.print_exc()
            self.signals.failed.emit(f"{e.__class__.__name__}: {e}")


_live_jobs = set()  # keep Python refs alive until the worker reports back


def run_job(fn, *args, done=None, failed=None, progress=None):
    job = Job(fn, *args, with_progress=progress is not None)
    job.setAutoDelete(False)
    _live_jobs.add(job)
    if done:
        job.signals.done.connect(done)
    if failed:
        job.signals.failed.connect(failed)
    if progress:
        job.signals.progress.connect(progress)
    # Release the job only AFTER the callbacks above have run. Dropping it inside the
    # same emit deletes its signal object and silently cancels the remaining callbacks
    # (that is what left "Pulling..." stuck in v1.0).
    release = lambda *_: QTimer.singleShot(0, lambda: _live_jobs.discard(job))
    job.signals.done.connect(release)
    job.signals.failed.connect(release)
    QThreadPool.globalInstance().start(job)
    return job


# ===================================================================== table models
SORT_ROLE = Qt.UserRole + 1


class RowModel(QAbstractTableModel):
    columns = []  # (header, key)

    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section][0]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            return self.tooltips.get(self.columns[section][1]) if hasattr(self, "tooltips") else None

    def set_rows(self, rows):
        rid = lambda r: r.get("id", r.get("key"))
        old = [rid(r) for r in self.rows]
        if old == [rid(r) for r in rows]:
            self.rows = rows  # same rows: update in place so selection/scroll is kept
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.columns) - 1))
        else:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()

    def row_at(self, i):
        return self.rows[i]

    def tick(self, keys):
        """Repaint time-based columns (countdowns, 'x min ago')."""
        if not self.rows:
            return
        for c, (_, k) in enumerate(self.columns):
            if k in keys:
                self.dataChanged.emit(self.index(0, c), self.index(len(self.rows) - 1, c))


class TargetModel(RowModel):
    columns = [("Last attack", "last_attack"), ("Result", "last_result"), ("Name", "name"),
               ("Lvl", "level"), ("", "online"), ("Last action", "last_action_ts"), ("FF", "ff"),
               ("FF now", "ff_now"), ("FR", "base_respect"), ("Life", "life"), ("Status", "status"),
               ("Update", "updated"), ("Flag", "color"), ("", "attack"), ("Notes", "note")]
    tooltips = {"base_respect": "Flat respect: your last win on them with chain/war/group/overseas bonuses removed (like YATA's FR)",
                "ff": "Fair fight on your last win against them",
                "ff_now": "FF Scouter's current fair-fight estimate against you (needs FF Scouter on in Settings)",
                "color": "Click to cycle flag",
                "attack": "Click to open the attack page"}
    COL = {k: i for i, (_, k) in enumerate(columns)}
    min_ff = 2.5

    def flags(self, index):
        f = super().flags(index)
        if self.columns[index.column()][1] == "note":
            f |= Qt.ItemIsEditable
        return f

    def data(self, index, role=Qt.DisplayRole):
        t = self.rows[index.row()]
        key = self.columns[index.column()][1]

        if role == SORT_ROLE:
            return {
                "name": (t.get("name") or "").lower(), "online": t.get("last_action_status") or "",
                "life": (t["life_cur"] / t["life_max"]) if t.get("life_max") else None,
                "status": (t.get("status_until") or 0) if not status_info(t)[2] else -1,
                "attack": None,
            }.get(key, t.get(key))

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if key == "last_attack":
                return ago(t["last_attack"]) if t.get("last_attack") else "never"
            if key == "name":
                return f"{t.get('name') or '…'} [{t['id']}]"
            if key == "online":
                return "●"
            if key == "last_action_ts":
                return ago(t.get("last_action_ts"))
            if key in ("ff", "ff_now"):
                return fmt_ff(t.get(key))
            if key == "base_respect":
                return f"{t[key]:.2f}" if t.get(key) is not None else ""
            if key == "life":
                return f"{t.get('life_cur') or 0:,}/{t.get('life_max') or 0:,}" if t.get("life_max") else ""
            if key == "status":
                return status_info(t)[0]
            if key == "updated":
                return clock(t.get("updated"))
            if key == "color":
                return ""
            if key == "attack":
                return "Attack ›"
            v = t.get(key)
            return "" if v is None else str(v)

        if role == Qt.ForegroundRole:
            if key == "last_result" and t.get("last_result"):
                return QBrush(QColor(GREEN if is_win(t["last_result"]) else RED))
            if key == "online":
                return QBrush(QColor({"Online": GREEN, "Idle": YELLOW}.get(t.get("last_action_status"), "#555")))
            if key == "status":
                return QBrush(QColor(status_info(t)[1]))
            if key == "name":
                return QBrush(QColor(BLUE))
            if key == "attack":
                return QBrush(QColor(BLUE))
            if key == "last_attack" and not t.get("last_attack"):
                return QBrush(QColor(MUTED))
            if key in ("ff", "ff_now") and t.get(key) is not None:
                below = t[key] < self.min_ff
                return QBrush(QColor(RED if below else (MUTED if key == "ff" and t.get("ff_now") is not None else "#d9dde2")))

        if role == Qt.BackgroundRole and key == "color" and FLAG_COLORS[t.get("color") or 0]:
            return QBrush(QColor(FLAG_COLORS[t["color"]]))

        if role == Qt.FontRole and key == "base_respect":
            f = QFont()
            f.setBold(True)
            return f

        if role == Qt.TextAlignmentRole:
            if key in ("level", "ff", "ff_now", "base_respect"):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if key in ("online", "color"):
                return int(Qt.AlignCenter)

        if role == Qt.ToolTipRole:
            if key == "status":
                return t.get("error") or t.get("status_desc")
            if key == "name" and t.get("faction"):
                return f"{t['faction']} · {t.get('n_attacks', 0)} attacks by you · double-click for profile"
            if key == "note":
                return "Double-click to edit"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole and self.columns[index.column()][1] == "note":
            t = self.rows[index.row()]
            t["note"] = value
            core.set_note(t["id"], value)
            self.dataChanged.emit(index, index)
            return True
        return False


class AttackModel(RowModel):
    columns = [("When", "ended"), ("Attacker", "attacker"), ("Defender", "defender"), ("Result", "result"),
               ("Respect", "respect"), ("FF", "fair_fight"), ("FR", "base_respect"),
               ("Chain", "chain"), ("Flags", "flags"), ("Target list", "is_target")]

    def data(self, index, role=Qt.DisplayRole):
        a = self.rows[index.row()]
        key = self.columns[index.column()][1]
        if role == SORT_ROLE:
            return {"attacker": (a.get("attacker_name") or "").lower(),
                    "defender": (a.get("defender_name") or "").lower(), "flags": ""}.get(key, a.get(key))
        if role == Qt.DisplayRole:
            if key == "ended":
                return ago(a["ended"])
            if key in ("attacker", "defender"):
                pid, name, fac = a.get(f"{key}_id"), a.get(f"{key}_name"), a.get(f"{key}_faction")
                s = f"{name or 'Someone'}" + (f" [{pid}]" if pid else "")
                return s + (f"  ·  {fac}" if fac else "")
            if key in ("respect", "fair_fight", "base_respect"):
                return f"{a[key]:.2f}" if a.get(key) else ""
            if key == "chain":
                return str(a["chain"]) if a.get("chain") else ""
            if key == "flags":
                return " ".join(n for n, k in (("stealth", "stealthed"), ("RW", "ranked_war"), ("raid", "raid")) if a.get(k))
            if key == "is_target":
                return "on list" if a["is_target"] else ("right-click to add" if a.get("other_id") else "")
            return str(a.get(key) or "")
        if role == Qt.ForegroundRole:
            if key == "result":
                good = is_win(a["result"]) if a["outgoing"] else not is_win(a["result"])
                return QBrush(QColor(GREEN if good else RED))
            if key in ("flags", "is_target"):
                return QBrush(QColor(MUTED))
        if role == Qt.TextAlignmentRole and key in ("respect", "fair_fight", "base_respect", "chain"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and key == "ended":
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a["ended"]))
        return None


class SortProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(SORT_ROLE)
        self.text, self.flag, self.only_ok = "", None, False
        self.extra = None  # optional callable(row) -> bool

    def lessThan(self, l, r):
        a, b = l.data(SORT_ROLE), r.data(SORT_ROLE)
        desc = self.sortOrder() == Qt.DescendingOrder
        if a is None or b is None:  # blanks always sink to the bottom
            if a is None and b is None:
                return False
            return desc if a is None else not desc
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def filterAcceptsRow(self, row, parent):
        m = self.sourceModel()
        r = m.row_at(row)
        if self.text:
            hay = " ".join(str(r.get(k) or "") for k in ("id", "name", "note", "attacker_name", "defender_name",
                                                          "description", "subcategory", "category")).lower()
            if self.text not in hay:
                return False
        if self.flag is not None and (r.get("color") or 0) != self.flag:
            return False
        if self.only_ok and not status_info(r)[2]:
            return False
        if self.extra and not self.extra(r):
            return False
        return True

    def refilter(self):
        self.invalidateFilter()


class LifeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        t = index.model().sourceModel().row_at(index.model().mapToSource(index).row())
        self.initStyleOption(option, index)
        option.widget.style().drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)
        r = option.rect.adjusted(6, option.rect.height() // 2 - 5, -6, -(option.rect.height() // 2 - 5))
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3a3f46"))
        painter.drawRoundedRect(r, 3, 3)
        if t.get("life_max"):
            frac = max(0.0, min(1.0, (t.get("life_cur") or 0) / t["life_max"]))
            painter.setBrush(QColor("#6a7fd8"))
            painter.drawRoundedRect(QRect(r.x(), r.y(), int(r.width() * frac), r.height()), 3, 3)
        painter.restore()


def fmt_num(v):
    if v is None:
        return ""
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return f"{int(v):,}"


def fmt_days(d):
    if d is None:
        return "?"
    if d <= 0:
        return "—"
    if d < 1:
        return f"{d * 24:.1f} h"
    if d < 365:
        return f"{d:.1f} days"
    return f"{d / 365:.1f} years"


class ChainModel(RowModel):
    columns = [("Ended", "end"), ("Started", "start"), ("Chain length", "length"), ("Your hits", "hits"),
               ("Your share", "share"), ("Your respect", "respect"), ("Avg respect", "avg_respect"),
               ("Avg FR", "avg_fr"), ("Avg FF", "avg_ff"), ("Best hit", "best"), ("War hits", "war_hits"),
               ("Faction respect", "faction_respect"), ("Source", "source")]
    tooltips = {"share": "Your hits as a share of the whole chain (faction chain list only)",
                "avg_fr": "Average flat respect per hit (chain/war bonuses removed)",
                "source": "'Faction list' = exact chain from Torn; 'Your hits' = grouped from your own attacks"}

    def data(self, index, role=Qt.DisplayRole):
        c = self.rows[index.row()]
        key = self.columns[index.column()][1]
        if role == SORT_ROLE:
            return c.get(key) if key != "source" else int(bool(c.get("faction_chain")))
        if role == Qt.DisplayRole:
            if key in ("start", "end"):
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(c[key])) if c.get(key) else ""
            if key == "share":
                return f"{c['share']:.1f}%" if c.get("share") is not None else ""
            if key == "source":
                return "Faction list" if c.get("faction_chain") else "Your hits"
            return fmt_num(c.get(key))
        if role == Qt.ForegroundRole and key == "source" and not c.get("faction_chain"):
            return QBrush(QColor(MUTED))
        if role == Qt.FontRole and key in ("hits", "respect"):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and key not in ("start", "end", "source"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class AwardModel(RowModel):
    columns = [("Category", "category"), ("Group", "subcategory"), ("Type", "type"), ("Award", "name"),
               ("Requirement", "description"), ("Progress", "achieve"), ("Current", "current"),
               ("Goal", "goal"), ("Est. time left", "left"), ("Held by", "circulation")]
    tooltips = {"left": "YATA's estimate at your current pace (hover a row for the breakdown)",
                "circulation": "How many players have it — lower is rarer"}

    def data(self, index, role=Qt.DisplayRole):
        a = self.rows[index.row()]
        key = self.columns[index.column()][1]
        if role == SORT_ROLE:
            if key == "left":
                return a["left"] if a.get("left") is not None and a["achieve"] < 1 else (10**9 if a["achieve"] < 1 else -1)
            return a.get(key)
        if role == Qt.DisplayRole:
            if key == "achieve":
                return f"{a['achieve'] * 100:.0f}%"
            if key in ("current", "goal"):
                return fmt_num(a.get(key))
            if key == "left":
                if a["achieve"] >= 1:
                    return "Done " + (time.strftime("%Y-%m-%d", time.localtime(a["awarded_time"])) if a.get("awarded_time") else "")
                return fmt_days(a.get("left"))
            if key == "circulation":
                return f"{a['circulation']:,}" + (f"  ·  {a['rarity']}" if a.get("rarity") else "")
            return str(a.get(key) or "")
        if role == Qt.ForegroundRole:
            if a["achieve"] >= 1:
                return QBrush(QColor(GREEN if key in ("name", "left") else MUTED))
            if a.get("unreach"):
                return QBrush(QColor(MUTED))
            if key == "type":
                return QBrush(QColor(YELLOW if a["type"] == "Medal" else BLUE))
        if role == Qt.FontRole and key == "name":
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and key in ("current", "goal", "left", "circulation"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            tip = a.get("comment") or ""
            if a.get("unreach"):
                tip = "No longer obtainable.\n" + tip
            return f"{a['name']}: {a['description']}" + (f"\n\n{tip}" if tip else "")
        return None


class ProgressDelegate(QStyledItemDelegate):
    """Draws a progress bar from the row's 'achieve' (0..1)."""

    def paint(self, painter, option, index):
        a = index.model().sourceModel().row_at(index.model().mapToSource(index).row())
        self.initStyleOption(option, index)
        option.widget.style().drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)
        frac = max(0.0, min(1.0, float(a.get("achieve") or 0)))
        r = option.rect.adjusted(6, option.rect.height() // 2 - 7, -6, -(option.rect.height() // 2 - 7))
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3a3f46"))
        painter.drawRoundedRect(r, 3, 3)
        painter.setBrush(QColor(GREEN if frac >= 1 else ("#e8c547" if frac >= 0.75 else "#6a7fd8")))
        painter.drawRoundedRect(QRect(r.x(), r.y(), int(r.width() * frac), r.height()), 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(r, Qt.AlignCenter, f"{frac * 100:.0f}%")
        painter.restore()


def fmt_bs(v):
    if not v:
        return ""
    for div, suf in ((1e12, "t"), (1e9, "b"), (1e6, "m"), (1e3, "k")):
        if v >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:.0f}"


class EnemyModel(RowModel):
    """Enemy faction members - used by War Scout (all members) and War List (picked ones)."""
    ALL = {
        "on_list": "List", "name": "Name", "level": "Lvl", "position": "Position", "bs_estimate": "Est. stats",
        "vs_me": "vs me", "fair_fight": "FF", "est_fr": "Est. FR", "record": "My record", "online": "",
        "last_action_ts": "Last action", "status": "Status", "days_in_faction": "Days in fac", "color": "Flag",
        "attack": "", "note": "Notes",
    }
    tooltips = {"bs_estimate": "FF Scouter's estimated total battle stats",
                "vs_me": "Their estimated stats ÷ your total stats (below 1.0 = weaker than you)",
                "fair_fight": "FF Scouter's fair-fight estimate against YOU (3.00 = max respect)",
                "est_fr": "Estimated flat respect for a win: FF × (1 + ln level) ÷ 4",
                "record": "Your past wins-losses on them (and FR of your last win)",
                "on_list": "✓ = on your War List", "color": "Click to cycle flag", "attack": "Click to attack"}

    def __init__(self, keys, editable_note=False):
        super().__init__()
        self.columns = [(self.ALL[k], k) for k in keys]
        self.COL = {k: i for i, k in enumerate(keys)}
        self.editable_note = editable_note

    def flags(self, index):
        f = super().flags(index)
        if self.editable_note and self.columns[index.column()][1] == "note":
            f |= Qt.ItemIsEditable
        return f

    def data(self, index, role=Qt.DisplayRole):
        e = self.rows[index.row()]
        key = self.columns[index.column()][1]
        if role == SORT_ROLE:
            if key == "name":
                return (e.get("name") or "").lower()
            if key == "status":
                info = status_info(e)
                return -1 if info[2] else (e.get("status_until") or 10**10)
            if key == "record":
                return (e.get("my_wins") or 0) - (e.get("my_losses") or 0)
            if key == "online":
                return {"Online": 0, "Idle": 1}.get(e.get("last_action_status"), 2)
            if key == "on_list":
                return int(bool(e.get("on_list")))
            if key == "attack":
                return None
            return e.get(key)
        if role in (Qt.DisplayRole, Qt.EditRole):
            if key == "on_list":
                return "✓" if e.get("on_list") else ""
            if key == "name":
                return f"{e.get('name') or '?'} [{e['id']}]"
            if key == "bs_estimate":
                return e.get("bs_human") or fmt_bs(e.get("bs_estimate"))
            if key == "vs_me":
                return f"{e['vs_me']:.2f}×" if e.get("vs_me") is not None else ""
            if key == "fair_fight":
                return fmt_ff(e.get(key))
            if key == "est_fr":
                return f"{e[key]:.2f}" if e.get(key) is not None else ""
            if key == "record":
                w, l = e.get("my_wins") or 0, e.get("my_losses") or 0
                if not (w or l):
                    return ""
                return f"{w}-{l}" + (f"  ({e['my_fr']:.2f})" if e.get("my_fr") else "")
            if key == "online":
                return "●"
            if key == "last_action_ts":
                return ago(e.get("last_action_ts"))
            if key == "status":
                return status_info(e)[0] if e.get("status_state") != "Gone" else "Left faction"
            if key == "color":
                return ""
            if key == "attack":
                return "Attack ›"
            v = e.get(key)
            return "" if v is None else str(v)
        if role == Qt.ForegroundRole:
            if key == "status":
                return QBrush(QColor(status_info(e)[1] if e.get("status_state") != "Gone" else MUTED))
            if key == "online":
                return QBrush(QColor({"Online": GREEN, "Idle": YELLOW}.get(e.get("last_action_status"), "#555")))
            if key in ("name", "attack"):
                return QBrush(QColor(BLUE))
            if key == "on_list":
                return QBrush(QColor(GREEN))
            if key == "vs_me" and e.get("vs_me") is not None:
                v = e["vs_me"]
                return QBrush(QColor(GREEN if v < 0.75 else (YELLOW if v < 1.25 else RED)))
            if key == "fair_fight" and e.get("fair_fight"):
                return QBrush(QColor(GREEN if e["fair_fight"] >= 2.5 else (YELLOW if e["fair_fight"] >= 1.5 else MUTED)))
        if role == Qt.BackgroundRole and key == "color" and FLAG_COLORS[e.get("color") or 0]:
            return QBrush(QColor(FLAG_COLORS[e["color"]]))
        if role == Qt.FontRole and key == "est_fr":
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole:
            if key in ("level", "bs_estimate", "vs_me", "fair_fight", "est_fr", "days_in_faction", "record"):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if key in ("on_list", "online", "color"):
                return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if key == "bs_estimate" and e.get("ff_updated"):
                return f"FF Scouter estimate ({e.get('source') or 'bss'}), updated {ago(e['ff_updated'])}"
            if key == "status":
                return e.get("status_desc")
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole and self.columns[index.column()][1] == "note":
            e = self.rows[index.row()]
            e["note"] = value
            core.warlist_set(e["id"], note=value)
            self.dataChanged.emit(index, index)
            return True
        return False


NAV_ICONS = {"Targets": "🎯", "Attacks": "🗡️", "Chains": "⛓️", "Awards": "🏅",
             "War Scout": "🔭", "War List": "📋", "Settings": "⚙️"}


class SideNav(QObject):
    """Collapsible side menu + page stack. Offers the same calls the app used on QTabWidget."""
    currentChanged = Signal(int)
    collapsedChanged = Signal(bool)
    WIDE, NARROW = 196, 58

    def __init__(self, collapsed=False):
        super().__init__()
        self.stack = QStackedWidget()
        self.menu = QFrame(objectName="sidenav")
        self.lay = QVBoxLayout(self.menu)
        self.lay.setContentsMargins(0, 10, 0, 10)
        self.lay.setSpacing(2)
        self.buttons, self.names, self.pages = [], [], []
        self.group = QButtonGroup(self.menu)
        self.group.setExclusive(True)
        self.lay.addStretch()
        self.toggle = QPushButton(objectName="navitem", clicked=lambda: self.set_collapsed(not self.collapsed))
        self.lay.addWidget(self.toggle)
        self.collapsed = collapsed
        self._apply()

    def addTab(self, widget, name):
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setWidget(widget)
        self.pages.append(widget)
        i = self.stack.addWidget(holder)
        b = QPushButton(objectName="navitem", checkable=True)
        b.clicked.connect(lambda _=False, i=i: self.setCurrentIndex(i))
        self.group.addButton(b)
        self.lay.insertWidget(len(self.buttons), b)
        self.buttons.append(b)
        self.names.append(name)
        if i == 0:
            b.setChecked(True)
        self._apply()
        return i

    def tabText(self, i):
        return self.names[i] if 0 <= i < len(self.names) else ""

    def currentIndex(self):
        return self.stack.currentIndex()

    def setCurrentIndex(self, i):
        if i == self.stack.currentIndex():
            self.buttons[i].setChecked(True)
            return
        self.stack.setCurrentIndex(i)
        self.buttons[i].setChecked(True)
        self.currentChanged.emit(i)

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self.pages.index(w))

    def set_collapsed(self, c):
        self.collapsed = bool(c)
        self._apply()
        self.collapsedChanged.emit(self.collapsed)

    def _apply(self):
        self.menu.setFixedWidth(self.NARROW if self.collapsed else self.WIDE)
        for b, n in zip(self.buttons, self.names):
            icon = NAV_ICONS.get(n, "•")
            b.setText(icon if self.collapsed else f"{icon}   {n}")
            b.setToolTip(n if self.collapsed else "")
        self.toggle.setText("»" if self.collapsed else "«   Collapse")
        self.toggle.setToolTip("Expand menu" if self.collapsed else "Collapse menu")


class PlayerCard(QFrame):
    """Header card: initial badge · name [id] · status · faction/level · life bar · sync state."""

    def __init__(self):
        super().__init__(objectName="playerCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 14, 8)
        lay.setSpacing(12)
        self.badge = QLabel("?", objectName="badge", alignment=Qt.AlignCenter)
        self.badge.setFixedSize(38, 38)
        lay.addWidget(self.badge)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        self.name = QLabel(textFormat=Qt.RichText)
        self.sub = QLabel(objectName="cardSub")
        life_row = QHBoxLayout()
        life_row.setSpacing(6)
        self.life_lbl = QLabel("Life", objectName="cardSub")
        self.life_bar = QProgressBar(objectName="lifeBar", textVisible=False)
        self.life_bar.setFixedSize(120, 6)
        self.life_txt = QLabel(objectName="cardSub")
        for w_ in (self.life_lbl, self.life_bar, self.life_txt):
            life_row.addWidget(w_)
        life_row.addStretch()
        mid.addWidget(self.name)
        mid.addWidget(self.sub)
        mid.addLayout(life_row)
        lay.addLayout(mid)
        sep = QFrame(objectName="cardSep")
        sep.setFixedWidth(1)
        lay.addWidget(sep)
        self.sync = QLabel(textFormat=Qt.RichText, objectName="cardSub")
        self.sync.setWordWrap(True)
        self.sync.setFixedWidth(150)
        lay.addWidget(self.sync)

    def show_player(self, name, pid, status_html, sub, life, life_max):
        self.badge.setText(next((ch for ch in (name or "") if ch.isalnum()), "?").upper())
        self.name.setText(f"<b style='font-size:15px;color:#ffffff'>{name or '…'}</b> "
                          f"<span style='color:{MUTED}'>[{pid or '?'}]</span>"
                          + (f"&nbsp;&nbsp;{status_html}" if status_html else ""))
        self.sub.setText(sub)
        has_life = bool(life_max)
        for w_ in (self.life_lbl, self.life_bar, self.life_txt):
            w_.setVisible(has_life)
        if has_life:
            frac = life / life_max
            col = GREEN if frac >= 0.75 else (YELLOW if frac >= 0.35 else RED)
            self.life_bar.setMaximum(life_max)
            self.life_bar.setValue(life)
            self.life_bar.setStyleSheet(f"QProgressBar::chunk {{ background: {col}; border-radius: 3px; }}")
            self.life_txt.setText(f"{life:,} / {life_max:,}")

    def show_sync(self, html):
        self.sync.setText(html)


# ===================================================================== UI pieces
def make_table(model, sort_col, order=Qt.DescendingOrder):
    proxy = SortProxy()
    proxy.setSourceModel(model)
    v = QTableView()
    v.setModel(proxy)
    v.setSortingEnabled(True)
    v.sortByColumn(sort_col, order)
    v.setSelectionBehavior(QAbstractItemView.SelectRows)
    v.setSelectionMode(QAbstractItemView.ExtendedSelection)
    v.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
    v.setAlternatingRowColors(True)
    v.setShowGrid(False)
    v.verticalHeader().hide()
    v.verticalHeader().setDefaultSectionSize(30)
    v.horizontalHeader().setHighlightSections(False)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    v.setContextMenuPolicy(Qt.CustomContextMenu)
    v.setWordWrap(False)
    return v, proxy


class Tile(QFrame):
    def __init__(self, label):
        super().__init__()
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        self.value = QLabel("—")
        self.value.setObjectName("tileValue")
        cap = QLabel(label.upper())
        cap.setObjectName("tileCap")
        lay.addWidget(self.value)
        lay.addWidget(cap)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        avail = QApplication.primaryScreen().availableGeometry()  # screen minus menu bar and Dock
        self.setMinimumSize(900, 520)
        self.resize(min(1400, int(avail.width() * 0.95)), min(820, int(avail.height() * 0.92)))
        self.syncing = False
        self.refreshing = False
        self.last_sync_try = 0.0
        self.last_auto_refresh = time.time()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="header")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(36, 10, 18, 10)
        hv.setSpacing(6)
        head = QHBoxLayout()          # row 1: logo ............ player
        cards = QHBoxLayout()         # row 2: small status cards + armory shortcuts
        cards.setSpacing(6)
        title = QLabel()
        title.setObjectName("title")
        banner = QPixmap(str(ASSETS / "header.png"))
        if banner.isNull():
            title.setText("⌖  Strike List")
        else:
            dpr = self.devicePixelRatioF() or 1.0  # crisp on Retina / high-DPI screens
            banner = banner.scaledToHeight(int(80 * dpr), Qt.SmoothTransformation)
            banner.setDevicePixelRatio(dpr)
            title.setPixmap(banner)
            title.setToolTip(f"Strike List {VERSION}")
        head.addWidget(title)
        head.addStretch()
        self.pill_energy = QLabel(objectName="pill")
        self.pill_drug = QLabel(objectName="pill")
        self.pill_booster = QLabel(objectName="pill")
        for p_ in (self.pill_energy, self.pill_drug, self.pill_booster):
            p_.setTextFormat(Qt.RichText)
            cards.addWidget(p_)
        self.pill_energy.setToolTip("Energy now / maximum, and time until full")
        self.pill_drug.setToolTip("Drug cooldown (e.g. Xanax) — green when you can take one")
        self.pill_booster.setToolTip("Booster cooldown — green when you can use one")
        self.pill_chain = QLabel(objectName="pill")
        self.pill_chain.setTextFormat(Qt.RichText)
        self.pill_chain.setToolTip("Your faction's live chain: hits / next bonus · time until it drops")
        self.pill_chain.hide()
        cards.addWidget(self.pill_chain)
        self.chain_data = None
        self.last_chain_poll = 0.0
        self.chain_alerted = False
        self.btn_med = QPushButton("🩸  Armory: Medical", objectName="shortcut",
                                   clicked=lambda: webbrowser.open(core.load_cfg()["armory_medical_url"]))
        self.btn_drugs = QPushButton("💊  Armory: Drugs", objectName="shortcut",
                                     clicked=lambda: webbrowser.open(core.load_cfg()["armory_drugs_url"]))
        self.btn_med.setToolTip("Opens your faction armory's Medical section in your browser")
        self.btn_drugs.setToolTip("Opens your faction armory's Drugs section in your browser")
        cards.addSpacing(8)
        cards.addWidget(self.btn_med)
        cards.addWidget(self.btn_drugs)
        cards.addStretch()
        self.status_data = None
        self.last_status_poll = 0.0
        self.who = PlayerCard()
        head.addWidget(self.who, 0, Qt.AlignVCenter)
        hv.addLayout(head)
        hv.addLayout(cards)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.tabs = SideNav(collapsed=bool(core.load_cfg().get("nav_collapsed", False)))
        self.tabs.collapsedChanged.connect(self._nav_collapsed)
        self.tabs.addTab(self._targets_tab(), "Targets")
        self.tabs.addTab(self._attacks_tab(), "Attacks")
        self.tabs.addTab(self._chains_tab(), "Chains")
        self.tabs.addTab(self._awards_tab(), "Awards")
        self.tabs.addTab(self._scout_tab(), "War Scout")
        self.tabs.addTab(self._warlist_tab(), "War List")
        self.settings_page = self._settings_tab()
        self.tabs.addTab(self.settings_page, "Settings")
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs.menu)
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(14, 10, 14, 6)
        pl.addWidget(self.tabs.stack)
        body.addWidget(page, 1)
        root.addLayout(body, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Data folder: {core.data_dir()}")

        self.load_targets()
        self.load_attacks()
        self.update_header()

        self.clock = QTimer(self, interval=1000, timeout=self._each_second)
        self.clock.start()
        self.bg = QTimer(self, interval=15000, timeout=self._background)
        self.bg.start()
        QTimer.singleShot(800, self._first_run)

    # ------------------------------------------------------------- targets tab
    def _targets_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.add_edit = QLineEdit(placeholderText="Add player IDs or paste profile links (comma / space separated)")
        self.add_edit.setMinimumWidth(380)
        self.add_edit.returnPressed.connect(self.add_targets)
        add_btn = QPushButton("Add", objectName="primary", clicked=self.add_targets)
        bar.addWidget(self.add_edit)
        bar.addWidget(add_btn)
        bar.addStretch()
        self.only_ok = QCheckBox("Only attackable", toggled=self._apply_filters)
        self.flag_filter = QComboBox()
        self.flag_filter.addItems(["All flags", "No flag", "Green", "Yellow", "Red"])
        self.flag_filter.currentIndexChanged.connect(self._apply_filters)
        self.text_filter = QLineEdit(placeholderText="Filter name / note / ID", textChanged=self._apply_filters)
        self.text_filter.setFixedWidth(170)
        self.refresh_btn = QPushButton("⟳  Refresh all", clicked=self.refresh_all)
        cfg = core.load_cfg()
        self.min_ff_box = QDoubleSpinBox(prefix="Min FF ", decimals=2, singleStep=0.1)
        self.min_ff_box.setRange(1.0, 3.0)
        self.min_ff_box.setValue(float(cfg.get("target_min_ff", 2.5)))
        self.min_ff_box.setToolTip("Targets below this fair fight show in red and are offered in Clean up")
        self.min_ff_box.valueChanged.connect(self._min_ff_changed)
        TargetModel.min_ff = self.min_ff_box.value()
        cleanup_btn = QPushButton("Clean up…", clicked=self.cleanup_targets)
        share_btn = QPushButton("⇄  Share…", clicked=self.share_targets)
        share_btn.setToolTip("Copy your list to Strike List on another phone or computer, or import one")
        cleanup_btn.setToolTip("Review and remove targets whose FF dropped below your minimum")
        for x in (self.only_ok, self.flag_filter, self.text_filter, self.min_ff_box, cleanup_btn, share_btn, self.refresh_btn):
            bar.addWidget(x)
        lay.addLayout(bar)

        self.tmodel = TargetModel()
        self.tview, self.tproxy = make_table(self.tmodel, TargetModel.COL["base_respect"])
        self.tview.setItemDelegateForColumn(TargetModel.COL["life"], LifeDelegate(self.tview))
        widths = {"last_attack": 100, "last_result": 95, "name": 200, "level": 40, "online": 26,
                  "last_action_ts": 100, "ff": 50, "ff_now": 60, "base_respect": 55, "life": 100, "status": 140,
                  "updated": 55, "color": 42, "attack": 75, "note": 160}
        for k, wd in widths.items():
            self.tview.setColumnWidth(TargetModel.COL[k], wd)
        self.tview.horizontalHeader().setStretchLastSection(False)
        self.tview.horizontalHeader().setSectionResizeMode(TargetModel.COL["name"], QHeaderView.Stretch)
        self.tview.setColumnWidth(TargetModel.COL["note"], 220)
        self.tview.clicked.connect(self._target_clicked)
        self.tview.doubleClicked.connect(self._target_double)
        self.tview.customContextMenuRequested.connect(self._target_menu)
        lay.addWidget(self.tview)
        self.tcount = QLabel(objectName="muted")
        lay.addWidget(self.tcount)
        return w

    def load_targets(self):
        self.tmodel.set_rows(core.list_targets())
        self._count()

    def _count(self):
        self.tcount.setText(f"{self.tproxy.rowCount()} of {self.tmodel.rowCount()} targets   ·   "
                            "click Attack › · double-click a name for their profile · right-click for more · "
                            "* = timer ran out since last update, refresh to confirm")

    def _apply_filters(self, *_):
        self.tproxy.text = self.text_filter.text().strip().lower()
        i = self.flag_filter.currentIndex()
        self.tproxy.flag = None if i == 0 else i - 1
        self.tproxy.only_ok = self.only_ok.isChecked()
        self.tproxy.refilter()
        self._count()

    def _selected_targets(self):
        rows = {self.tproxy.mapToSource(i).row() for i in self.tview.selectionModel().selectedRows()}
        return [self.tmodel.row_at(r) for r in sorted(rows)]

    def _target_clicked(self, idx):
        src = self.tproxy.mapToSource(idx)
        t = self.tmodel.row_at(src.row())
        key = TargetModel.columns[src.column()][1]
        if key == "attack":
            webbrowser.open(ATTACK_URL.format(t["id"]))
        elif key == "color":
            t["color"] = ((t.get("color") or 0) + 1) % 4
            core.set_color(t["id"], t["color"])
            self.tmodel.dataChanged.emit(src, src)

    def _target_double(self, idx):
        src = self.tproxy.mapToSource(idx)
        if TargetModel.columns[src.column()][1] == "name":
            webbrowser.open(PROFILE_URL.format(self.tmodel.row_at(src.row())["id"]))

    def _target_menu(self, pos):
        sel = self._selected_targets()
        if not sel:
            return
        m = QMenu(self)
        if len(sel) == 1:
            t = sel[0]
            m.addAction("Attack", lambda: webbrowser.open(ATTACK_URL.format(t["id"])))
            m.addAction("Open profile", lambda: webbrowser.open(PROFILE_URL.format(t["id"])))
            m.addSeparator()
        m.addAction(f"⟳  Refresh {len(sel)} target(s)", lambda: self.refresh_ids([t["id"] for t in sel]))
        flags = m.addMenu("Set flag")
        for i, n in enumerate(["None", "Green", "Yellow", "Red"]):
            flags.addAction(n, lambda i=i: self._set_flags(sel, i))
        m.addSeparator()
        m.addAction(f"✕  Remove {len(sel)} target(s)", lambda: self.remove(sel))
        m.exec(self.tview.viewport().mapToGlobal(pos))

    def _set_flags(self, sel, color):
        for t in sel:
            core.set_color(t["id"], color)
        self.load_targets()

    def remove(self, sel):
        names = ", ".join((t.get("name") or str(t["id"])) for t in sel[:5]) + ("…" if len(sel) > 5 else "")
        if QMessageBox.question(self, "Remove targets", f"Remove {names} from your list?") == QMessageBox.Yes:
            for t in sel:
                core.remove_target(t["id"])
            self.load_targets()
            self.load_attacks()

    def _min_ff_changed(self, v):
        cfg = core.load_cfg()
        cfg["target_min_ff"] = round(float(v), 2)
        core.save_cfg(cfg)
        TargetModel.min_ff = float(v)
        self.load_targets()

    def cleanup_targets(self):
        cfg = core.load_cfg()
        min_ff = self.min_ff_box.value()
        dlg = QDialog(self)
        dlg.setWindowTitle("Clean up targets")
        dlg.resize(620, 520)
        lay = QVBoxLayout(dlg)
        head = QLabel()
        head.setWordWrap(True)
        lay.addWidget(head)
        keep = QCheckBox("Keep flagged targets (green / yellow / red)", checked=bool(cfg.get("cleanup_keep_flagged", True)))
        lay.addWidget(keep)
        lst = QListWidget()
        lay.addWidget(lst)
        btns = QDialogButtonBox()
        remove_btn = btns.addButton("Remove ticked", QDialogButtonBox.AcceptRole)
        btns.addButton(QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        note = QLabel("Uses FF Scouter's current FF when available, otherwise the FF from your last win. "
                      "Targets you've never beaten (no FF yet) are never suggested.", objectName="muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        def fill():
            lst.clear()
            cands = core.cleanup_candidates(min_ff, keep.isChecked())
            for t in cands:
                it = QListWidgetItem(f"{t.get('name') or '?'} [{t['id']}]   —   FF {t['eff_ff']:.2f} ({t['ff_source']})"
                                     + (f"   ·   lvl {t['level']}" if t.get("level") else "")
                                     + (f"   ·   “{t['note']}”" if t.get("note") else ""))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                it.setData(Qt.UserRole, t["id"])
                lst.addItem(it)
            head.setText(f"<b>{len(cands)}</b> of {self.tmodel.rowCount()} targets are below FF {min_ff:.2f}. "
                         "Untick any you want to keep.")
            remove_btn.setEnabled(bool(cands))

        keep.toggled.connect(lambda *_: fill())
        fill()
        if dlg.exec() != QDialog.Accepted:
            return
        cfg = core.load_cfg()
        cfg["cleanup_keep_flagged"] = keep.isChecked()
        core.save_cfg(cfg)
        ids = [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
        if ids:
            core.remove_targets(ids)
            self.load_targets()
            self.load_attacks()
            self.statusBar().showMessage(f"Removed {len(ids)} target(s) below FF {min_ff:.2f}", 6000)

    def share_targets(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Share your target list")
        dlg.resize(560, 460)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("<b>Export</b> — copy this code and paste it into Strike List on any other phone or "
                             "computer (Strike List Mobile in Torn PDA / your browser, or this app on Mac or Windows).",
                             wordWrap=True))
        out = QPlainTextEdit(core.export_code(), readOnly=True)
        out.setFixedHeight(90)
        lay.addWidget(out)
        copy_btn = QPushButton("Copy code")
        copy_btn.clicked.connect(lambda: (QApplication.clipboard().setText(out.toPlainText()),
                                          copy_btn.setText("Copied ✓")))
        lay.addWidget(copy_btn, 0, Qt.AlignLeft)
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Import</b> — paste a Strike List code (or player IDs / profile links). "
                             "Targets are added to your list; nothing is removed.", wordWrap=True))
        inp = QPlainTextEdit()
        inp.setPlaceholderText("SL1:…")
        inp.setFixedHeight(90)
        lay.addWidget(inp)
        msg = QLabel(objectName="muted")
        row = QHBoxLayout()
        imp_btn = QPushButton("Import", objectName="primary")
        close_btn = QPushButton("Close", clicked=dlg.accept)
        row.addWidget(imp_btn)
        row.addWidget(msg, 1)
        row.addWidget(close_btn)
        lay.addLayout(row)

        def do_import():
            try:
                added, updated = core.import_code(inp.toPlainText())
            except Exception:
                msg.setText(f"<span style='color:{RED}'>That code didn't work — copy all of it, starting with SL1:</span>")
                return
            msg.setText(f"<span style='color:{GREEN}'>Imported: {added} new, {updated} already on your list</span>")
            out.setPlainText(core.export_code())
            self.load_targets()
            if added:
                self.refresh_all()

        imp_btn.clicked.connect(do_import)
        dlg.exec()

    def add_targets(self):
        ids = core.parse_ids(self.add_edit.text())
        if not ids:
            return
        core.add_targets(ids)
        self.add_edit.clear()
        self.load_targets()
        self.refresh_ids(ids, quiet=True)
        self.statusBar().showMessage(f"Added {len(ids)} target(s) — fetching their details…", 5000)

    def refresh_all(self):
        self.refresh_ids(core.target_ids_oldest_first())

    def refresh_ids(self, ids, quiet=False):
        if self.refreshing or not ids:
            return
        if not self._need_key():
            return
        self.refreshing = True
        self.refresh_started = time.time()
        total = len(ids)

        def work(progress):
            errors = []
            for n, tid in enumerate(ids, 1):
                try:
                    core.refresh_target(tid)
                except core.TornError as e:
                    errors.append(str(e))
                    if e.code in core.TornError.FATAL:
                        break
                progress(f"{n}/{total}")
            try:  # current FF for every target in one FF Scouter call (only if turned on)
                core.fetch_ffstats(ids)
            except core.FFSError as e:
                errors.append(str(e))
            return errors

        def done(errors):
            self.refreshing = False
            self.refresh_btn.setText("⟳  Refresh all")
            self.load_targets()
            if errors:
                self.statusBar().showMessage(f"Refresh finished with {len(errors)} error(s): {errors[-1]}", 10000)
            elif not quiet:
                self.statusBar().showMessage(f"Refreshed {total} target(s)", 4000)

        def prog(msg):
            self.refresh_btn.setText(f"Refreshing {msg}…")
            self.load_targets()

        self.refresh_btn.setText(f"Refreshing 0/{total}…")
        run_job(work, done=done, failed=lambda e: (setattr(self, "refreshing", False), self._error(e)), progress=prog)

    # ------------------------------------------------------------- attacks tab
    def _attacks_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tiles_row = QHBoxLayout()
        self.tiles = {k: Tile(lbl) for k, lbl in (("out_n", "My attacks"), ("in_n", "Attacks on me"),
                                                   ("respect", "Respect earned"), ("avg_ff", "Avg FF"))}
        for t in self.tiles.values():
            self.tiles_row.addWidget(t)
        self.result_tiles = [Tile("") for _ in range(4)]
        for t in self.result_tiles:
            self.tiles_row.addWidget(t)
        self.tiles_row.addStretch()
        lay.addLayout(self.tiles_row)

        bar = QHBoxLayout()
        self.dir_box = QComboBox()
        self.dir_box.addItems(["All attacks", "My attacks", "Attacks on me"])
        self.dir_box.currentIndexChanged.connect(self.load_attacks)
        self.limit_box = QComboBox()
        self.limit_box.addItems(["250", "1000", "5000"])
        self.limit_box.currentIndexChanged.connect(self.load_attacks)
        self.sync_btn = QPushButton("⟳  Pull new attacks", clicked=lambda: self.sync(manual=True))
        self.sync_info = QLabel(objectName="muted")
        for x in (self.dir_box, QLabel("Show"), self.limit_box, self.sync_btn, self.sync_info):
            bar.addWidget(x)
        bar.addStretch()
        lay.addLayout(bar)

        self.amodel = AttackModel()
        self.aview, self.aproxy = make_table(self.amodel, 0)
        for c, wd in enumerate([110, 280, 280, 110, 80, 60, 80, 60, 90, 120]):
            self.aview.setColumnWidth(c, wd)
        self.aview.horizontalHeader().setStretchLastSection(True)
        self.aview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.aview.customContextMenuRequested.connect(self._attack_menu)
        self.aview.doubleClicked.connect(self._attack_double)
        lay.addWidget(self.aview)
        return w

    def load_attacks(self, *_):
        d = ["all", "out", "in"][self.dir_box.currentIndex()]
        self.amodel.set_rows(core.list_attacks(d, int(self.limit_box.currentText())))
        s = core.stats()
        for k, t in self.tiles.items():
            t.value.setText(str(s.get(k) if s.get(k) is not None else "—"))
        for i, t in enumerate(self.result_tiles):
            r = s["results"][i] if i < len(s["results"]) else None
            t.setVisible(bool(r))
            if r:
                t.value.setText(str(r["n"]))
                t.findChild(QLabel, "tileCap").setText((r["result"] or "").upper())

    def _attack_double(self, idx):
        a = self.amodel.row_at(self.aproxy.mapToSource(idx).row())
        if a.get("other_id"):
            webbrowser.open(PROFILE_URL.format(a["other_id"]))

    def _attack_menu(self, pos):
        rows = {self.aproxy.mapToSource(i).row() for i in self.aview.selectionModel().selectedRows()}
        others = sorted({self.amodel.row_at(r)["other_id"] for r in rows if self.amodel.row_at(r).get("other_id")})
        if not others:
            return
        m = QMenu(self)
        m.addAction(f"+  Add {len(others)} player(s) to targets", lambda: self._add_from_attacks(others))
        if len(others) == 1:
            m.addAction("Open profile", lambda: webbrowser.open(PROFILE_URL.format(others[0])))
            m.addAction("Attack", lambda: webbrowser.open(ATTACK_URL.format(others[0])))
        m.exec(self.aview.viewport().mapToGlobal(pos))

    def _add_from_attacks(self, ids):
        core.add_targets(ids)
        self.load_targets()
        self.load_attacks()
        self.refresh_ids(ids, quiet=True)
        self.statusBar().showMessage(f"Added {len(ids)} player(s) to targets", 4000)

    def sync(self, manual=False):
        if self.syncing or not core.load_cfg()["api_key"]:
            if manual:
                self._need_key()
            return
        self.syncing = True
        self.sync_started = time.time()
        self.last_sync_try = time.time()
        self.sync_btn.setText("Pulling…")

        def done(added):
            self.syncing = False
            self.sync_btn.setText("⟳  Pull new attacks")
            self.load_attacks()
            self.load_targets()
            self.load_chains()
            self.last_error = None
            self.update_header()
            if added or manual:
                self.statusBar().showMessage(f"{added} new attack(s) saved", 5000)

        def fail(msg):
            self.syncing = False
            self.sync_btn.setText("⟳  Pull new attacks")
            self._error(msg, popup=manual)

        run_job(core.sync_attacks, done=done, failed=fail,
                progress=lambda m: self.statusBar().showMessage(m))

    # ------------------------------------------------------------- chains tab
    def _chains_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.chain_btn = QPushButton("⟳  Update faction chains", clicked=self.update_chains)
        self.chain_info = QLabel(objectName="muted")
        self.chain_info.setWordWrap(True)
        self.chain_min = QSpinBox(prefix="Hide chains with fewer than ", suffix=" of my hits")
        self.chain_min.setRange(1, 1000)
        self.chain_min.setValue(int(core.load_cfg().get("chain_min_hits", 10)))
        self.chain_min.setToolTip("Hides chains you only dropped a couple of hits into. Set to 1 to show everything.")
        self.chain_min.valueChanged.connect(self._chain_min_changed)
        bar.addWidget(self.chain_btn)
        bar.addWidget(self.chain_min)
        bar.addWidget(self.chain_info, 1)
        lay.addLayout(bar)
        self.cmodel = ChainModel()
        self.cview, self.cproxy = make_table(self.cmodel, 0)
        self.cproxy.extra = lambda c: c["hits"] >= self.chain_min.value()
        self.cview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for c, wd in enumerate([130, 130, 95, 80, 85, 100, 90, 70, 65, 75, 70, 115, 90]):
            self.cview.setColumnWidth(c, wd)
        self.cview.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.cview)
        lay.addWidget(QLabel("Built from your stored attacks — pull new attacks to update. "
                             "Only hits that counted toward a chain are included.", objectName="muted"))
        return w

    def _chain_min_changed(self, v):
        cfg = core.load_cfg()
        cfg["chain_min_hits"] = int(v)
        core.save_cfg(cfg)
        self.cproxy.refilter()
        self.load_chains()

    def load_chains(self):
        all_rows = core.list_chains()
        self.cmodel.set_rows(all_rows)
        self.cproxy.refilter()
        mn = self.chain_min.value()
        rows = [r for r in all_rows if r["hits"] >= mn]
        hidden = len(all_rows) - len(rows)
        err = core.meta_get("fchains_error")
        n_fac = sum(1 for r in rows if r.get("faction_chain"))
        msg = f"Showing {len(rows)} chains · {sum(r['hits'] for r in rows):,} of your hits · " \
              f"{sum(r['respect'] for r in rows):,.2f} respect"
        if hidden:
            msg += f"   ·   {hidden} small chains hidden"
        if err:
            msg += f"   ·   Faction chain list unavailable ({err}) — grouped from your own hits instead."
        elif n_fac:
            msg += f"   ·   {n_fac} matched to your faction's chain list"
        self.chain_info.setText(msg)

    def update_chains(self):
        if getattr(self, "chains_busy", False) or not self._need_key():
            return
        self.chains_busy = True
        self.chain_btn.setText("Updating…")

        def done(_):
            self.chains_busy = False
            self.chain_btn.setText("⟳  Update faction chains")
            self.load_chains()

        def fail(msg):
            self.chains_busy = False
            self.chain_btn.setText("⟳  Update faction chains")
            self._error(msg)

        run_job(core.sync_faction_chains, done=done, failed=fail)

    # ------------------------------------------------------------- awards tab
    def _awards_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.aw_tiles = {k: Tile(lbl) for k, lbl in (("honors", "Honors"), ("medals", "Medals"),
                                                     ("close", "≥ 75% done"), ("week", "Within a week"))}
        for t in self.aw_tiles.values():
            top.addWidget(t)
        top.addStretch()
        lay.addLayout(top)

        bar = QHBoxLayout()
        self.aw_cat = QComboBox()
        self.aw_cat.addItem("All categories")
        self.aw_type = QComboBox()
        self.aw_type.addItems(["Honors + medals", "Honors", "Medals"])
        self.aw_hide_done = QCheckBox("Hide achieved", checked=True)
        self.aw_hide_unreach = QCheckBox("Hide unobtainable", checked=True)
        self.aw_text = QLineEdit(placeholderText="Search awards")
        self.aw_text.setFixedWidth(180)
        self.aw_btn = QPushButton("⟳  Refresh awards", clicked=self.refresh_awards)
        for x in (self.aw_cat, self.aw_type, self.aw_hide_done, self.aw_hide_unreach, self.aw_text):
            bar.addWidget(x)
        bar.addStretch()
        bar.addWidget(self.aw_btn)
        lay.addLayout(bar)
        for sig in (self.aw_cat.currentIndexChanged, self.aw_type.currentIndexChanged,
                    self.aw_hide_done.toggled, self.aw_hide_unreach.toggled, self.aw_text.textChanged):
            sig.connect(self._filter_awards)

        self.awmodel = AwardModel()
        self.awview, self.awproxy = make_table(self.awmodel, AwardModel.columns.index(("Progress", "achieve")))
        self.awview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.awview.setItemDelegateForColumn(AwardModel.columns.index(("Progress", "achieve")), ProgressDelegate(self.awview))
        for c, wd in enumerate([100, 130, 60, 170, 330, 120, 90, 90, 110, 150]):
            self.awview.setColumnWidth(c, wd)
        self.awview.horizontalHeader().setStretchLastSection(True)
        self.awproxy.extra = self._award_visible
        lay.addWidget(self.awview)
        self.aw_info = QLabel(objectName="muted")
        self.aw_info.setWordWrap(True)
        lay.addWidget(self.aw_info)
        self.awards_data = core.cached_awards()
        return w

    def _award_visible(self, a):
        if self.aw_hide_done.isChecked() and a["achieve"] >= 1:
            return False
        if self.aw_hide_unreach.isChecked() and a.get("unreach"):
            return False
        if self.aw_cat.currentIndex() > 0 and a["category"] != self.aw_cat.currentText():
            return False
        t = self.aw_type.currentIndex()
        if (t == 1 and a["type"] != "Honor") or (t == 2 and a["type"] != "Medal"):
            return False
        return True

    def _filter_awards(self, *_):
        self.awproxy.text = self.aw_text.text().strip().lower()
        self.awproxy.refilter()

    def show_awards(self):
        d = self.awards_data
        if not d:
            self.aw_info.setText("Click Refresh awards to load your progress (about 2–3 API calls).")
            return
        rows = d["rows"]
        self.awmodel.set_rows(rows)
        cats = sorted({r["category"] for r in rows})
        cur = self.aw_cat.currentText()
        self.aw_cat.blockSignals(True)
        self.aw_cat.clear()
        self.aw_cat.addItems(["All categories"] + cats)
        if cur in cats:
            self.aw_cat.setCurrentText(cur)
        self.aw_cat.blockSignals(False)
        open_ = [r for r in rows if r["achieve"] < 1 and not r.get("unreach")]
        self.aw_tiles["honors"].value.setText(f"{d['have_honors']} / {d['n_honors']}")
        self.aw_tiles["medals"].value.setText(f"{d['have_medals']} / {d['n_medals']}")
        self.aw_tiles["close"].value.setText(str(sum(1 for r in open_ if r["achieve"] >= 0.75)))
        self.aw_tiles["week"].value.setText(str(sum(1 for r in open_ if r.get("left") is not None and 0 < r["left"] <= 7)))
        msg = (f"Updated {ago(d['ts'])}.  Progress and time estimates use YATA's award engine (GPL-3, by Kivou). "
               "Hover a row for the breakdown. Sort by 'Est. time left' to see what you'll get soonest.")
        if d.get("errors"):
            msg += "\nSome categories could not be calculated: " + "; ".join(d["errors"])
        self.aw_info.setText(msg)
        self._filter_awards()

    def refresh_awards(self):
        if getattr(self, "awards_busy", False) or not self._need_key():
            return
        self.awards_busy = True
        self.aw_btn.setText("Loading…")

        def done(d):
            self.awards_busy = False
            self.aw_btn.setText("⟳  Refresh awards")
            self.awards_data = d
            self.show_awards()
            self.statusBar().showMessage("Awards updated", 4000)

        def fail(msg):
            self.awards_busy = False
            self.aw_btn.setText("⟳  Refresh awards")
            self._error(msg, popup=True)

        run_job(core.fetch_awards, done=done, failed=fail,
                progress=lambda m: self.statusBar().showMessage(m))

    # ------------------------------------------------------------- war scout tab
    def _scout_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.war_banner = QLabel(objectName="warBanner")
        self.war_banner.setWordWrap(True)
        lay.addWidget(self.war_banner)

        bar = QHBoxLayout()
        self.fac_edit = QLineEdit(placeholderText="Faction ID or link to scout")
        self.fac_edit.setFixedWidth(220)
        self.fac_edit.returnPressed.connect(lambda: self.scout_faction(self.fac_edit.text()))
        self.scout_btn = QPushButton("Load faction", clicked=lambda: self.scout_faction(self.fac_edit.text()))
        self.war_btn = QPushButton("⟳  Check for ranked war", clicked=self.check_war)
        self.ffs_btn = QPushButton("⟳  Refresh stat estimates", clicked=lambda: self.refresh_ffstats(force=True))
        for x in (self.fac_edit, self.scout_btn, self.war_btn, self.ffs_btn):
            bar.addWidget(x)
        bar.addStretch()
        lay.addLayout(bar)

        flt = QHBoxLayout()
        self.max_vs = QDoubleSpinBox(prefix="Est. stats ≤ ", suffix=" × mine", decimals=2, singleStep=0.25)
        self.max_vs.setRange(0, 100)
        self.max_vs.setSpecialValueText("Est. stats: any")
        self.max_vs.setValue(0)
        self.min_ff = QDoubleSpinBox(prefix="FF ≥ ", decimals=2, singleStep=0.1)
        self.min_ff.setRange(0, 3)
        self.min_ff.setSpecialValueText("FF: any")
        self.min_ff.setValue(0)
        self.scout_ok = QCheckBox("Only attackable now")
        self.scout_hide_listed = QCheckBox("Hide ones already on list")
        self.scout_text = QLineEdit(placeholderText="Filter name / ID / position")
        self.scout_text.setFixedWidth(170)
        for x in (self.max_vs, self.min_ff, self.scout_ok, self.scout_hide_listed, self.scout_text):
            flt.addWidget(x)
        flt.addStretch()
        add_btn = QPushButton("+  Add selected to War List", objectName="primary", clicked=self.add_selected_to_war)
        pick_btn = QPushButton("Add all matching filters", clicked=self.add_matching_to_war)
        flt.addWidget(add_btn)
        flt.addWidget(pick_btn)
        lay.addLayout(flt)
        for sig in (self.max_vs.valueChanged, self.min_ff.valueChanged, self.scout_ok.toggled,
                    self.scout_hide_listed.toggled, self.scout_text.textChanged):
            sig.connect(self._filter_scout)

        keys = ["on_list", "name", "level", "position", "bs_estimate", "vs_me", "fair_fight", "est_fr",
                "record", "online", "last_action_ts", "status", "days_in_faction"]
        self.smodel = EnemyModel(keys)
        self.sview, self.sproxy = make_table(self.smodel, keys.index("est_fr"))
        self.sview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for k, wd in {"on_list": 40, "name": 210, "level": 45, "position": 110, "bs_estimate": 90, "vs_me": 65,
                      "fair_fight": 55, "est_fr": 65, "record": 110, "online": 26, "last_action_ts": 105,
                      "status": 160, "days_in_faction": 90}.items():
            self.sview.setColumnWidth(keys.index(k), wd)
        self.sview.horizontalHeader().setStretchLastSection(True)
        self.sproxy.extra = self._scout_visible
        self.sview.doubleClicked.connect(lambda idx: self._open_profile(self.smodel, self.sproxy, idx))
        self.sview.customContextMenuRequested.connect(self._scout_menu)
        lay.addWidget(self.sview)
        self.scout_info = QLabel(objectName="muted")
        self.scout_info.setWordWrap(True)
        lay.addWidget(self.scout_info)
        self.war = json.loads(core.meta_get("war_detected") or "null")
        return w

    def _scout_visible(self, e):
        if self.max_vs.value() > 0 and (e.get("vs_me") is None or e["vs_me"] > self.max_vs.value()):
            return False
        if self.min_ff.value() > 0 and (e.get("fair_fight") or 0) < self.min_ff.value():
            return False
        if self.scout_ok.isChecked() and not status_info(e)[2]:
            return False
        if self.scout_hide_listed.isChecked() and e.get("on_list"):
            return False
        return True

    def _filter_scout(self, *_):
        self.sproxy.text = self.scout_text.text().strip().lower()
        self.sproxy.refilter()
        self._scout_summary()

    def _scout_summary(self):
        rows = self.smodel.rows
        fac = core.meta_get("war_faction_name")
        if not rows:
            self.scout_info.setText("No faction loaded yet.")
            return
        upd = core.meta_get("enemy_updated")
        has_ff = sum(1 for r in rows if r.get("bs_estimate"))
        cfg = core.load_cfg()
        ff_note = (f"{has_ff}/{len(rows)} have FF Scouter estimates" if cfg.get("ffscouter_enabled")
                   else "FF Scouter is off — turn it on in Settings to get estimated stats")
        my = core.meta_get("my_bs_total")
        self.scout_info.setText(
            f"{fac} [{core.meta_get('war_faction_id')}] · {len(rows)} members · showing {self.sproxy.rowCount()} · "
            f"roster updated {ago(int(upd)) if upd else 'never'} · {ff_note}"
            + (f" · your total stats {fmt_bs(float(my))}" if my else "")
            + "\nDouble-click a name for their profile · right-click for more · Est. FR sorts best respect to the top.")

    def update_war_banner(self):
        wv = self.war
        if not wv:
            self.war_banner.setText("No ranked war found for your faction. Enter a faction ID below to scout one anyway.")
            return
        now = time.time()
        if wv["start"] > now:
            when = f"starts in {self._countdown(wv['start'] - now)}"
        elif not wv.get("end"):
            when = f"<b style='color:{RED}'>IN PROGRESS</b> for {self._countdown(now - wv['start'])}"
        else:
            when = "ended"
        self.war_banner.setText(f"🎯  Ranked war vs <b>{wv['enemy_name']}</b> [{wv['enemy_id']}] — {when}"
                                + (f" · target {wv['target']:,}" if wv.get("target") else ""))

    @staticmethod
    def _countdown(sec):
        sec = int(sec)
        d, rem = divmod(sec, 86400)
        return (f"{d}d " if d else "") + hms(rem)

    def load_scout(self):
        rows = core.list_enemy()
        self.smodel.set_rows(rows)
        self.wmodel.set_rows([r for r in core.list_enemy(only_list=True)])
        self._filter_scout()
        self._warlist_summary()

    def check_war(self):
        if not self._need_key() or getattr(self, "war_busy", False):
            return
        self.war_busy = True
        self.war_btn.setText("Checking…")

        def work():
            war = core.detect_war()
            if war:
                core.load_enemy(war["enemy_id"])
            core.my_battlestats()
            return war

        def done(war):
            self.war_busy = False
            self.war_btn.setText("⟳  Check for ranked war")
            self.war = war
            self.update_war_banner()
            self.load_scout()
            if war:
                self.refresh_ffstats()

        def fail(msg):
            self.war_busy = False
            self.war_btn.setText("⟳  Check for ranked war")
            self._error(msg, popup=True)

        run_job(work, done=done, failed=fail)

    def scout_faction(self, text):
        ids = core.parse_ids(text)
        if not ids or not self._need_key() or getattr(self, "war_busy", False):
            return
        fid = ids[-1]
        same = str(fid) == str(core.meta_get("war_faction_id"))
        if not same and core.list_enemy(only_list=True):
            if QMessageBox.question(self, "Switch faction",
                                    "Your War List has targets from another faction. Keep them on the list?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
                core.warlist_clear()
        self.war_busy = True
        self.scout_btn.setText("Loading…")

        def work():
            name = core.load_enemy(fid)
            core.my_battlestats()
            return name

        def done(name):
            self.war_busy = False
            self.scout_btn.setText("Load faction")
            self.fac_edit.clear()
            self.load_scout()
            self.statusBar().showMessage(f"Loaded {name}", 4000)
            self.refresh_ffstats()

        def fail(msg):
            self.war_busy = False
            self.scout_btn.setText("Load faction")
            self._error(msg, popup=True)

        run_job(work, done=done, failed=fail)

    def refresh_ffstats(self, force=False):
        cfg = core.load_cfg()
        if not cfg.get("ffscouter_enabled"):
            if force:
                QMessageBox.information(self, "FF Scouter is off",
                                        "Estimated battle stats come from FF Scouter. Turn it on in Settings first.")
            return
        ids = [r["id"] for r in self.smodel.rows]
        if not ids or getattr(self, "ffs_busy", False):
            return
        self.ffs_busy = True
        self.ffs_btn.setText("Fetching estimates…")

        def done(n):
            self.ffs_busy = False
            self.ffs_btn.setText("⟳  Refresh stat estimates")
            self.load_scout()

        def fail(msg):
            self.ffs_busy = False
            self.ffs_btn.setText("⟳  Refresh stat estimates")
            self._error(msg, popup=force)

        run_job(core.fetch_ffstats, ids, 0 if force else 6 * 3600, done=done, failed=fail)

    def _scout_selected(self):
        rows = {self.sproxy.mapToSource(i).row() for i in self.sview.selectionModel().selectedRows()}
        return [self.smodel.row_at(r) for r in sorted(rows)]

    def add_selected_to_war(self):
        sel = [e["id"] for e in self._scout_selected()]
        if not sel:
            self.statusBar().showMessage("Select rows first (Shift/Cmd-click for several)", 4000)
            return
        core.warlist_add(sel)
        self.load_scout()
        self.statusBar().showMessage(f"Added {len(sel)} to War List", 4000)

    def add_matching_to_war(self):
        ids = [e["id"] for i, e in enumerate(self.smodel.rows) if self.sproxy.filterAcceptsRow(i, QModelIndex())]
        if not ids:
            return
        if QMessageBox.question(self, "Add to War List", f"Add all {len(ids)} players currently shown?") == QMessageBox.Yes:
            core.warlist_add(ids)
            self.load_scout()

    def _scout_menu(self, pos):
        sel = self._scout_selected()
        if not sel:
            return
        m = QMenu(self)
        m.addAction(f"+  Add {len(sel)} to War List", self.add_selected_to_war)
        on = [e["id"] for e in sel if e.get("on_list")]
        if on:
            m.addAction(f"–  Remove {len(on)} from War List", lambda: (core.warlist_remove(on), self.load_scout()))
        m.addAction(f"Add {len(sel)} to my normal Targets", lambda: self._add_from_attacks([e["id"] for e in sel]))
        if len(sel) == 1:
            m.addSeparator()
            m.addAction("Open profile", lambda: webbrowser.open(PROFILE_URL.format(sel[0]["id"])))
            m.addAction("Attack", lambda: webbrowser.open(ATTACK_URL.format(sel[0]["id"])))
        m.exec(self.sview.viewport().mapToGlobal(pos))

    def _open_profile(self, model, proxy, idx):
        src = proxy.mapToSource(idx)
        if model.columns[src.column()][1] == "note":
            return
        webbrowser.open(PROFILE_URL.format(model.row_at(src.row())["id"]))

    # ------------------------------------------------------------- war list tab
    def _warlist_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.wl_refresh_btn = QPushButton("⟳  Refresh statuses", clicked=self.refresh_war_statuses)
        self.wl_auto = QCheckBox("Auto-refresh every 30 s")
        self.wl_auto.setToolTip("1 API call per refresh for the whole enemy faction")
        self.wl_ok = QCheckBox("Only attackable now", toggled=lambda *_: self.wproxy.refilter())
        copy_btn = QPushButton("Copy list", clicked=self.copy_warlist)
        copy_btn.setToolTip("Copies a text version with attack links — paste into Discord / faction chat")
        clear_btn = QPushButton("Clear list", clicked=self.clear_warlist)
        for x in (self.wl_refresh_btn, self.wl_auto, self.wl_ok):
            bar.addWidget(x)
        bar.addStretch()
        bar.addWidget(copy_btn)
        bar.addWidget(clear_btn)
        lay.addLayout(bar)
        keys = ["name", "level", "bs_estimate", "vs_me", "fair_fight", "est_fr", "online", "last_action_ts",
                "status", "color", "attack", "note"]
        self.wmodel = EnemyModel(keys, editable_note=True)
        self.wview, self.wproxy = make_table(self.wmodel, keys.index("est_fr"))
        for k, wd in {"name": 210, "level": 45, "bs_estimate": 90, "vs_me": 65, "fair_fight": 55, "est_fr": 65,
                      "online": 26, "last_action_ts": 105, "status": 170, "color": 42, "attack": 75, "note": 200}.items():
            self.wview.setColumnWidth(keys.index(k), wd)
        self.wview.horizontalHeader().setStretchLastSection(True)
        self.wproxy.extra = lambda e: not self.wl_ok.isChecked() or status_info(e)[2]
        self.wview.clicked.connect(self._warlist_clicked)
        self.wview.doubleClicked.connect(lambda idx: self._open_profile(self.wmodel, self.wproxy, idx))
        self.wview.customContextMenuRequested.connect(self._warlist_menu)
        lay.addWidget(self.wview)
        self.wl_info = QLabel(objectName="muted")
        lay.addWidget(self.wl_info)
        self.last_war_refresh = 0.0
        return w

    def _warlist_summary(self):
        rows = self.wmodel.rows
        ready = sum(1 for r in rows if status_info(r)[2])
        upd = core.meta_get("enemy_updated")
        self.wl_info.setText(f"{len(rows)} targets · {ready} attackable now · statuses updated "
                             f"{ago(int(upd)) if upd else 'never'} · click Attack › · click Flag to cycle · "
                             "double-click Notes to edit")

    def _warlist_clicked(self, idx):
        src = self.wproxy.mapToSource(idx)
        e = self.wmodel.row_at(src.row())
        key = self.wmodel.columns[src.column()][1]
        if key == "attack":
            webbrowser.open(ATTACK_URL.format(e["id"]))
        elif key == "color":
            e["color"] = ((e.get("color") or 0) + 1) % 4
            core.warlist_set(e["id"], color=e["color"])
            self.wmodel.dataChanged.emit(src, src)

    def _warlist_menu(self, pos):
        rows = {self.wproxy.mapToSource(i).row() for i in self.wview.selectionModel().selectedRows()}
        sel = [self.wmodel.row_at(r) for r in sorted(rows)]
        if not sel:
            return
        m = QMenu(self)
        if len(sel) == 1:
            m.addAction("Attack", lambda: webbrowser.open(ATTACK_URL.format(sel[0]["id"])))
            m.addAction("Open profile", lambda: webbrowser.open(PROFILE_URL.format(sel[0]["id"])))
            m.addSeparator()
        m.addAction(f"–  Remove {len(sel)} from War List",
                    lambda: (core.warlist_remove([e["id"] for e in sel]), self.load_scout()))
        m.exec(self.wview.viewport().mapToGlobal(pos))

    def copy_warlist(self):
        QApplication.clipboard().setText(core.warlist_text())
        self.statusBar().showMessage("War List copied — paste it anywhere", 4000)

    def clear_warlist(self):
        if self.wmodel.rows and QMessageBox.question(self, "Clear War List",
                                                     f"Remove all {len(self.wmodel.rows)} targets?") == QMessageBox.Yes:
            core.warlist_clear()
            self.load_scout()

    def refresh_war_statuses(self, quiet=False):
        fid = core.meta_get("war_faction_id")
        if not fid or getattr(self, "war_busy", False) or not core.load_cfg()["api_key"]:
            return
        self.war_busy = True
        self.last_war_refresh = time.time()
        self.wl_refresh_btn.setText("Refreshing…")

        def done(_):
            self.war_busy = False
            self.wl_refresh_btn.setText("⟳  Refresh statuses")
            self.load_scout()

        def fail(msg):
            self.war_busy = False
            self.wl_refresh_btn.setText("⟳  Refresh statuses")
            self._error(msg, popup=not quiet)

        run_job(core.load_enemy, int(fid), done=done, failed=fail)

    # ------------------------------------------------------------- FF Scouter settings
    def _ffs_toggled(self, on):
        cfg = core.load_cfg()
        if not on:
            cfg["ffscouter_enabled"] = False
            core.save_cfg(cfg)
            self.ffs_msg.setText("Off")
            return
        if not self._need_key():
            self.ffs_check.setChecked(False)
            return
        self.ffs_msg.setText("Checking your key with FF Scouter…")

        def checked(res):
            registered, _ = res
            if registered:
                return self._ffs_enable()
            box = QMessageBox(self)
            box.setWindowTitle("Register with FF Scouter")
            box.setText("Your API key isn't registered with FF Scouter yet.\n\n"
                        "FF Scouter is a separate service (ffscouter.com). Registering sends your Torn API key "
                        "to them, and they use it under their own Data Policy and Terms.\n\n"
                        "Please read those on their homepage before you agree.")
            read_btn = box.addButton("Open ffscouter.com", QMessageBox.ActionRole)
            ok_btn = box.addButton("I've read and agree — register", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            while True:  # "Open ffscouter.com" opens the site and shows the question again
                box.exec()
                if box.clickedButton() is read_btn:
                    webbrowser.open(core.FFS_HOME)
                    continue
                break
            if box.clickedButton() is ok_btn:
                run_job(core.ffs_register, done=lambda _: self._ffs_enable(), failed=self._ffs_failed)
            else:
                self._ffs_failed("Not registered — FF Scouter stays off")

        run_job(core.ffs_check_key, done=checked, failed=self._ffs_failed)

    def _ffs_enable(self):
        cfg = core.load_cfg()
        cfg["ffscouter_enabled"] = True
        core.save_cfg(cfg)
        self.ffs_msg.setText(f"<span style='color:{GREEN}'>On — estimates load in War Scout</span>")
        if self.smodel.rows:
            self.refresh_ffstats()

    def _ffs_failed(self, msg):
        self.ffs_check.blockSignals(True)
        self.ffs_check.setChecked(False)
        self.ffs_check.blockSignals(False)
        cfg = core.load_cfg()
        cfg["ffscouter_enabled"] = False
        core.save_cfg(cfg)
        self.ffs_msg.setText(f"<span style='color:{RED}'>{msg}</span>")

    # ------------------------------------------------------------- settings tab
    def _settings_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        card = QFrame(objectName="card")
        card.setMaximumWidth(680)
        form = QFormLayout(card)
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(12)
        cfg = core.load_cfg()
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit(cfg["api_key"], echoMode=QLineEdit.Password,
                                  placeholderText="Paste your Torn API key (Limited Access is enough)")
        show = QCheckBox("show", toggled=lambda on: self.key_edit.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.key_edit)
        key_row.addWidget(show)
        form.addRow("API key", key_row)
        tos = QLabel(TOS_HTML, textFormat=Qt.RichText, objectName="tos", wordWrap=True)
        tos.setOpenExternalLinks(True)
        form.addRow("How your key is used", tos)
        self.rate = QSpinBox(minimum=1, maximum=90, value=cfg["max_calls_per_min"], suffix=" calls / min")
        form.addRow("Max API rate", self.rate)
        form.addRow("", QLabel("Torn's cap is 100/min per player across every tool using your keys.\n"
                               "Leave room for anything else you run (Titan, Sentinel, TornPDA…).", objectName="muted"))
        self.sync_min = QSpinBox(minimum=1, maximum=240, value=cfg["attack_sync_minutes"], suffix=" min")
        form.addRow("Pull new attacks every", self.sync_min)
        self.auto_min = QSpinBox(minimum=0, maximum=240, value=cfg["auto_refresh_minutes"], suffix=" min",
                                 specialValueText="Off (manual only)")
        form.addRow("Auto-refresh all targets", self.auto_min)
        self.auto_add = QCheckBox("Automatically add every player I beat to Targets",
                                  checked=cfg["auto_add_from_attacks"])
        form.addRow("", self.auto_add)
        self.ffs_check = QCheckBox("Use FF Scouter for estimated battle stats (War Scout)",
                                   checked=bool(cfg.get("ffscouter_enabled")))
        self.ffs_check.toggled.connect(self._ffs_toggled)
        self.med_url = QLineEdit(cfg["armory_medical_url"])
        self.drug_url = QLineEdit(cfg["armory_drugs_url"])
        form.addRow("Armory Medical link", self.med_url)
        form.addRow("Armory Drugs link", self.drug_url)
        form.addRow("", QLabel("The header buttons just open these pages in your browser. If Torn changes the\n"
                               "address, open the armory section yourself and paste its address here.", objectName="muted"))
        self.chain_thr = QSpinBox(suffix=" s left", specialValueText="Off")
        self.chain_thr.setRange(0, 290)
        self.chain_thr.setValue(int(cfg.get("chain_alert_seconds", 60)))
        self.chain_snd = QCheckBox("Sound", checked=bool(cfg.get("chain_alert_sound", True)))
        self.chain_ntf = QCheckBox("Desktop notification", checked=bool(cfg.get("chain_alert_notify", True)))
        crow = QHBoxLayout()
        for x in (self.chain_thr, self.chain_snd, self.chain_ntf):
            crow.addWidget(x)
        crow.addStretch()
        form.addRow("Chain alert at", crow)
        form.addRow("FF Scouter", self.ffs_check)
        self.ffs_msg = QLabel("On" if cfg.get("ffscouter_enabled") else "Off", objectName="muted")
        form.addRow("", self.ffs_msg)
        form.addRow("", QLabel("Torn doesn't publish battle stats, so War Scout uses FF Scouter's estimates.\n"
                               "Turning this on sends your Torn API key to ffscouter.com (a separate service).",
                               objectName="muted"))
        btns = QHBoxLayout()
        btns.addWidget(QPushButton("Save", objectName="primary", clicked=self.save_settings))
        self.settings_msg = QLabel()
        btns.addWidget(self.settings_msg)
        btns.addStretch()
        form.addRow("", btns)
        form.addRow("", QLabel(f"Your data folder:\n{core.data_dir()}", objectName="muted",
                               textInteractionFlags=Qt.TextSelectableByMouse))
        about = QLabel(ABOUT_HTML.format(version=VERSION), textFormat=Qt.RichText, objectName="muted", wordWrap=True)
        about.setOpenExternalLinks(True)
        form.addRow("About", about)
        outer.addWidget(card)
        outer.addStretch()
        return w

    def save_settings(self):
        cfg = core.load_cfg()
        new_key = self.key_edit.text().strip()
        cfg.update(max_calls_per_min=self.rate.value(), attack_sync_minutes=self.sync_min.value(),
                   auto_refresh_minutes=self.auto_min.value(), auto_add_from_attacks=self.auto_add.isChecked())
        cfg.update(chain_alert_seconds=self.chain_thr.value(), chain_alert_sound=self.chain_snd.isChecked(),
                   chain_alert_notify=self.chain_ntf.isChecked())
        for k, box in (("armory_medical_url", self.med_url), ("armory_drugs_url", self.drug_url)):
            u = box.text().strip()
            cfg[k] = u if u.startswith("https://www.torn.com/") else core.DEFAULT_CFG[k]
            box.setText(cfg[k])
        core.save_cfg(cfg)
        if not new_key:
            self.settings_msg.setText("Saved (no API key yet)")
            return
        self.settings_msg.setText("Checking key…")

        def ok(res):
            cfg2 = core.load_cfg()
            changed = cfg2["api_key"] != new_key
            cfg2["api_key"] = new_key
            core.save_cfg(cfg2)
            self.settings_msg.setText(f"<span style='color:{GREEN}'>Saved — hello {res[1]} [{res[0]}]</span>")
            self.update_header()
            self.load_attacks()
            if changed or not core.meta_get("attacks_synced"):
                self.statusBar().showMessage("Pulling your attack history — the first time can take a minute…")
                self.sync()

        run_job(core.check_key, new_key, done=ok,
                failed=lambda e: self.settings_msg.setText(f"<span style='color:{RED}'>{e}</span>"))

    # ------------------------------------------------------------- shared
    def _need_key(self):
        if core.load_cfg()["api_key"]:
            return True
        self.tabs.setCurrentWidget(self.settings_page)
        self.key_edit.setFocus()
        self.statusBar().showMessage("Add your Torn API key first", 5000)
        return False

    def _error(self, msg, popup=False):
        self.statusBar().showMessage(msg, 15000)
        self.update_header(error=msg)
        if popup:
            QMessageBox.warning(self, APP_NAME, msg)

    def update_header(self, error=None):
        if error is not None:
            self.last_error = error
        if not core.load_cfg()["api_key"]:
            self.who.show_player("No key", "—", "", "Add your API key in Settings", 0, 0)
            self.who.show_sync(f"<span style='color:{RED}'>● Not connected</span>")
            return
        d = self.status_data or {}
        name = d.get("name") or core.meta_get("player_name")
        pid = d.get("player_id") or core.meta_get("player_id")
        status_html = ""
        if d.get("status_state"):
            text, col, _ = status_info(d)
            status_html = f"<span style='color:{col};font-size:12px;font-weight:600'>● {text}</span>"
        sub = " · ".join(x for x in (d.get("faction") or core.meta_get("my_faction_name") or "",
                                     f"Level {d['level']}" if d.get("level") else "") if x)
        self.who.show_player(name, pid, status_html, sub, d.get("life", 0), d.get("life_max", 0))
        synced = core.meta_get("attacks_synced")
        api = core.limiter.used_last_minute()
        err = getattr(self, "last_error", None)
        if err:
            self.who.show_sync(f"<span style='color:{RED}'>● Problem</span><br>"
                               f"<span style='color:{MUTED}'>{err[:60]}</span>")
            self.who.sync.setToolTip(err)
        else:
            self.who.show_sync(f"<span style='color:{GREEN}'>● Synced {ago(int(synced)) if synced else 'never'}</span>"
                               f"<br><span style='color:{MUTED}'>API {api}/min</span>")
            self.who.sync.setToolTip("Attack log last pulled from Torn · API calls used in the last minute")

    def _nav_collapsed(self, collapsed):
        cfg = core.load_cfg()
        cfg["nav_collapsed"] = bool(collapsed)
        core.save_cfg(cfg)

    # ------------------------------------------------------------- header: energy + cooldowns
    def poll_status(self):
        if getattr(self, "status_busy", False) or not core.load_cfg()["api_key"]:
            return
        self.status_busy = True
        self.last_status_poll = time.time()

        def done(d):
            self.status_busy = False
            self.status_data = d
            self.last_error = None
            self.render_status()
            self.update_header()

        def fail(msg):
            self.status_busy = False

        run_job(core.fetch_status, done=done, failed=fail)

    # ------------------------------------------------------------- live chain timer
    def poll_chain(self):
        if getattr(self, "chain_busy", False) or not core.load_cfg()["api_key"]:
            return
        self.chain_busy = True
        self.last_chain_poll = time.time()

        def done(d):
            self.chain_busy = False
            self.chain_data = d
            # a hit pushed the timer back above the alert threshold -> re-arm the alert
            if d["timeout"] > int(core.load_cfg().get("chain_alert_seconds", 60) or 0):
                self.chain_alerted = False
            self.render_chain()

        def fail(msg):
            self.chain_busy = False  # no faction / no access: just keep the card hidden

        run_job(core.fetch_chain, done=done, failed=fail)

    def chain_active(self):
        d = self.chain_data
        return bool(d and d["current"] > 0 and d["drops_at"] > time.time())

    def render_chain(self):
        d = self.chain_data
        if not self.chain_active():
            self.pill_chain.hide()
            return
        left = d["drops_at"] - time.time()
        mx = d["max"]
        mx_txt = (f"{mx // 1000}K" if mx >= 1000 and mx % 1000 == 0 else f"{mx:,}") if mx else "?"
        flash = left < 60 and int(time.time()) % 2 == 0
        col = GREEN if left >= 120 else (YELLOW if left >= 60 else RED)
        self.pill_chain.setStyleSheet("QLabel#pill { background: #5a1f24; border-color: #ff6b6b; }" if flash else "")
        self.pill_chain.setText(f"<span style='color:{MUTED}'>⛓ Chain</span> <b>{d['current']:,}</b>"
                                f"<span style='color:{MUTED}'> / {mx_txt}</span>  <b style='color:{col}'>{hms(left)}</b>")
        self.pill_chain.show()
        cfg = core.load_cfg()
        thr = int(cfg.get("chain_alert_seconds", 60) or 0)
        if thr and left <= thr and not self.chain_alerted:
            self.chain_alerted = True
            self.chain_alert(left, d["current"])

    def chain_alert(self, left, count):
        cfg = core.load_cfg()
        msg = f"Chain {count:,} drops in {hms(left)} — hit someone!"
        if cfg.get("chain_alert_sound", True):
            play_alert_sound()
        if cfg.get("chain_alert_notify", True):
            if not getattr(self, "tray", None):
                self.tray = QSystemTrayIcon(QApplication.windowIcon(), self)
                self.tray.setToolTip("Strike List")
                self.tray.show()
            self.tray.showMessage("⛓ Chain timer", msg, QSystemTrayIcon.Warning, 10000)
        self.statusBar().showMessage(msg, 15000)

    def render_status(self):
        d = self.status_data
        for p_ in (self.pill_energy, self.pill_drug, self.pill_booster):
            p_.setVisible(bool(d))
        if not d:
            return
        now = time.time()
        e, emax = d["energy"], d["energy_max"]
        full_in = d["energy_full_at"] - now
        e_col = GREEN if emax and e >= emax else (YELLOW if emax and e >= emax * 0.5 else RED)
        self.pill_energy.setText(f"<span style='color:{MUTED}'>Energy</span> <b style='color:{e_col}'>{e}/{emax}</b>"
                                 + (f" <span style='color:{MUTED}'>full in {hms(full_in)}</span>" if full_in > 0 else ""))

        def cd(label, until):
            left = until - now
            if left <= 0:
                return f"<span style='color:{MUTED}'>{label}</span> <b style='color:{GREEN}'>Ready</b>"
            return f"<span style='color:{MUTED}'>{label}</span> <b style='color:{RED}'>{self._countdown(left)}</b>"

        self.pill_drug.setText(cd("Drug", d["drug_until"]))
        drug_ready = d["drug_until"] <= now
        med_ready = d.get("medical_until", 0) <= now
        self.btn_drugs.setToolTip("Opens your faction armory's Drugs section"
                                  + ("" if drug_ready else f" — drug cooldown {self._countdown(d['drug_until'] - now)} left"))
        self.btn_med.setToolTip("Opens your faction armory's Medical section"
                                + ("" if med_ready else f" — medical cooldown {self._countdown(d['medical_until'] - now)} left"))
        self.pill_booster.setText(cd("Booster", d["booster_until"]))

    def _tab_changed(self, i):
        name = self.tabs.tabText(i)
        if name == "Attacks":
            self.load_attacks()
        elif name == "Chains":
            self.load_chains()
            fs = core.meta_get("fchains_synced")
            if core.load_cfg()["api_key"] and (not fs or time.time() - int(fs) > 3600):
                self.update_chains()
        elif name in ("War Scout", "War List"):
            self.update_war_banner()
            self.load_scout()
            chk = core.meta_get("war_checked")
            if core.load_cfg()["api_key"] and (not chk or time.time() - int(chk) > 1800):
                self.check_war()
        elif name == "Awards":
            self.show_awards()
            if core.load_cfg()["api_key"] and (not self.awards_data or time.time() - self.awards_data["ts"] > 3600):
                self.refresh_awards()

    def _each_second(self):
        self.render_status()
        if time.time() - self.last_status_poll >= 60:
            self.poll_status()
        self.render_chain()
        # poll fast while a chain is running, slowly otherwise just to notice one starting
        if time.time() - self.last_chain_poll >= (12 if self.chain_active() else 120):
            self.poll_chain()
        self.tmodel.tick({"status", "last_attack", "last_action_ts"})
        self.smodel.tick({"status", "last_action_ts"})
        self.wmodel.tick({"status", "last_action_ts"})
        if self.war and self.tabs.tabText(self.tabs.currentIndex()) in ("War Scout", "War List"):
            self.update_war_banner()
        if self.wl_auto.isChecked() and time.time() - self.last_war_refresh >= core.load_cfg().get("war_refresh_seconds", 30):
            self.refresh_war_statuses(quiet=True)
        if int(time.time()) % 5 == 0:
            self.update_header()
            if self.only_ok.isChecked():
                self.tproxy.refilter()

    def _background(self):
        # watchdog: never let a hung request block future pulls/refreshes
        if self.syncing and time.time() - getattr(self, "sync_started", 0) > 600:
            self.syncing = False
            self.sync_btn.setText("⟳  Pull new attacks")
        if self.refreshing and time.time() - getattr(self, "refresh_started", 0) > 600:
            self.refreshing = False
            self.refresh_btn.setText("⟳  Refresh all")
        cfg = core.load_cfg()
        if not cfg["api_key"]:
            return
        if time.time() - self.last_sync_try >= cfg["attack_sync_minutes"] * 60:
            self.sync()
        if cfg["auto_refresh_minutes"] and time.time() - self.last_auto_refresh >= cfg["auto_refresh_minutes"] * 60:
            self.last_auto_refresh = time.time()
            self.refresh_ids(core.target_ids_oldest_first(), quiet=True)

    def _first_run(self):
        if not core.load_cfg()["api_key"]:
            self.tabs.setCurrentWidget(self.settings_page)
            self.key_edit.setFocus()
        else:
            self.sync()


# ===================================================================== theme
STYLE = """
QWidget { font-size: 13px; }
#title { font-size: 19px; font-weight: 700; color: #7aa7ff; }
QFrame#header { background: #111316; border-bottom: 1px solid #2f343a; }
QFrame#sidenav { background: #1a1c20; border-right: 1px solid #2f343a; }
QPushButton#navitem { text-align: left; padding: 10px 18px; border: none; border-radius: 0;
                      background: transparent; color: #8a929c; font-size: 14px; font-weight: 600; }
QPushButton#navitem:hover { color: #e6e9ed; background: #20242a; }
QPushButton#navitem:checked { color: #ffffff; background: #22304a; border-left: 3px solid #7aa7ff; padding-left: 15px; }
QFrame#playerCard { background: #1d2024; border: 1px solid #2f343a; border-radius: 10px; }
QLabel#badge { background: #22304a; border: 2px solid #7aa7ff; border-radius: 19px; color: #7aa7ff;
               font-size: 16px; font-weight: 800; }
QLabel#cardSub { color: #8a929c; font-size: 12px; }
QFrame#cardSep { background: #2f343a; }
QProgressBar#lifeBar { background: #3a3f46; border: none; border-radius: 3px; }
QLabel#tos { background: #1a1c20; border: 1px solid #2f343a; border-radius: 6px; padding: 4px; font-size: 12px; }
#warBanner { font-size: 15px; padding: 8px 10px; background: #2a1f24; border: 1px solid #5a2f3a; border-radius: 6px; }
#who { color: #aab2bc; }
QPushButton#shortcut { padding: 3px 11px; border-radius: 11px; font-size: 12px; }
#pill { background: #1d2024; border: 1px solid #2f343a; border-radius: 11px; padding: 3px 11px; font-size: 12px; }
#muted { color: #8a929c; }
QTabWidget::pane { border: 1px solid #2f343a; border-radius: 6px; top: -1px; }
QTabBar::tab { padding: 7px 18px; color: #8a929c; background: transparent; border: none; font-weight: 600; }
QTabBar::tab:selected { color: #e6e9ed; border-bottom: 2px solid #7aa7ff; }
QTableView { background: #1d2024; alternate-background-color: #212429; border: 1px solid #2f343a;
             border-radius: 6px; selection-background-color: #2b3f66; selection-color: #ffffff; }
QHeaderView::section { background: #24282d; color: #8a929c; padding: 6px; border: none;
                       border-bottom: 1px solid #2f343a; font-size: 11px; font-weight: 700; }
QLineEdit, QComboBox, QSpinBox { background: #24282d; border: 1px solid #2f343a; border-radius: 6px;
                                 padding: 5px 8px; color: #e6e9ed; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #7aa7ff; }
QPushButton { background: #24282d; border: 1px solid #3a4047; border-radius: 6px; padding: 6px 14px;
              color: #e6e9ed; font-weight: 600; }
QPushButton:hover { border-color: #7aa7ff; }
QPushButton#primary { background: #2b4a85; border-color: #3d5fa3; }
QFrame#tile, QFrame#card { background: #1d2024; border: 1px solid #2f343a; border-radius: 8px; }
QFrame#card { padding: 14px; }
#tileValue { font-size: 19px; font-weight: 700; }
#tileCap { font-size: 10px; color: #8a929c; }
QStatusBar { color: #8a929c; }
QMenu { background: #24282d; border: 1px solid #3a4047; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: #2b3f66; }
"""


def dark_palette():
    p = QPalette()
    for role, c in ((QPalette.Window, "#15171a"), (QPalette.WindowText, "#d9dde2"), (QPalette.Base, "#1d2024"),
                    (QPalette.AlternateBase, "#212429"), (QPalette.Text, "#d9dde2"), (QPalette.Button, "#24282d"),
                    (QPalette.ButtonText, "#d9dde2"), (QPalette.Highlight, "#2b3f66"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#24282d"),
                    (QPalette.ToolTipText, "#d9dde2"), (QPalette.PlaceholderText, "#6b737c")):
        p.setColor(role, QColor(c))
    return p


def play_alert_sound():
    """Short system alert sound without extra dependencies."""
    try:
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            QApplication.beep()
    except Exception:
        QApplication.beep()


_APPNAP_TOKEN = None


def disable_app_nap():
    """macOS slows timers of hidden/background apps (App Nap). Tell the OS this app is doing
    user-requested work so attack syncing and timers keep running on time."""
    global _APPNAP_TOKEN
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send0 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(addr)
        send_act = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_uint64, ctypes.c_void_p)(addr)
        sel = objc.sel_registerName
        info = send0(objc.objc_getClass(b"NSProcessInfo"), sel(b"processInfo"))
        reason = send_str(objc.objc_getClass(b"NSString"), sel(b"stringWithUTF8String:"),
                          b"Strike List keeps your Torn data in sync")
        # NSActivityUserInitiatedAllowingIdleSystemSleep: no App Nap, but the Mac may still sleep
        token = send_act(info, sel(b"beginActivityWithOptions:reason:"), 0x00FFFFFF & ~(1 << 20), reason)
        _APPNAP_TOKEN = send0(token, sel(b"retain"))
    except Exception:
        pass


def main():
    QApplication.setApplicationName(APP_NAME)
    disable_app_nap()
    if sys.platform == "win32":  # own taskbar icon instead of Python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StrikeList.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    icon = QIcon(str(ASSETS / "logo.png"))
    if not icon.isNull():
        app.setWindowIcon(icon)  # window icon + Mac Dock + Windows taskbar
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    core.init_db()
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
