"""Transactional calendar tools for the local sample salon."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Los_Angeles")


def today():
    return datetime.now(TIMEZONE).date()


class BookingConflict(Exception):
    pass


class DetailsChanged(Exception):
    pass


class SalonStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS services (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    minutes INTEGER NOT NULL, cents INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS stylists (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, services TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    proposal_id TEXT NOT NULL, customer TEXT NOT NULL,
                    service_id TEXT NOT NULL, stylist_id TEXT NOT NULL,
                    start TEXT NOT NULL, end TEXT NOT NULL,
                    cents INTEGER NOT NULL, approved_at TEXT NOT NULL,
                    simulated INTEGER NOT NULL DEFAULT 0);
            """)
            db.executemany("INSERT OR IGNORE INTO services VALUES (?,?,?,?)", [
                ("haircut", "Haircut", 45, 4000),
                ("blowdry", "Wash and blow-dry", 30, 3000),
                ("color", "Hair coloring", 90, 9500),
            ])
            db.executemany("INSERT OR IGNORE INTO stylists VALUES (?,?,?)", [
                ("maya", "Maya", "haircut,blowdry"),
                ("lily", "Lily", "haircut,color"),
                ("zoe", "Zoe", "blowdry,color"),
            ])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def catalog(self):
        with self.connect() as db:
            return {
                "services": [dict(row) for row in db.execute("SELECT * FROM services ORDER BY id")],
                "stylists": [dict(row) for row in db.execute("SELECT * FROM stylists ORDER BY id")],
            }

    def availability(self, day, intake):
        catalog = self.catalog()
        service = next(row for row in catalog["services"] if row["id"] == intake["service_id"])
        day_date = datetime.fromisoformat(day).date()
        slots = []
        with self.connect() as db:
            booked = list(db.execute("SELECT * FROM appointments WHERE substr(start,1,10)=?", (day,)))
        for stylist in catalog["stylists"]:
            if service["id"] not in stylist["services"].split(","):
                continue
            for minute in range(10 * 60, 18 * 60, 30):
                start = datetime.combine(day_date, time(minute // 60, minute % 60))
                end = start + timedelta(minutes=service["minutes"])
                if end.hour > 18 or (end.hour == 18 and end.minute):
                    continue
                if start.replace(tzinfo=TIMEZONE) <= datetime.now(TIMEZONE):
                    continue
                if intake["period"] == "morning" and start.hour >= 12:
                    continue
                if intake["period"] == "afternoon" and start.hour < 12:
                    continue
                if any(row["stylist_id"] == stylist["id"] and row["start"] < end.isoformat()
                       and row["end"] > start.isoformat() for row in booked):
                    continue
                slots.append({
                    "service_id": service["id"], "service": service["name"],
                    "stylist_id": stylist["id"], "stylist": stylist["name"],
                    "start": start.isoformat(), "end": end.isoformat(),
                    "minutes": service["minutes"], "cents": service["cents"],
                })
        requested = intake.get("requested_time", "")
        def preference(slot):
            clock = slot["start"][11:16]
            distance = 0
            earlier_than_requested = False
            if requested:
                wanted = int(requested[:2]) * 60 + int(requested[3:])
                actual = int(clock[:2]) * 60 + int(clock[3:])
                distance = abs(wanted - actual)
                earlier_than_requested = actual < wanted
            return (
                intake["stylist_id"] not in ("any", slot["stylist_id"]),
                distance, earlier_than_requested, slot["start"], slot["stylist_id"],
            )
        return sorted(slots, key=preference)[:8]

    def book(self, request_id, customer, proposal, approved_id, simulated=False):
        if not approved_id or approved_id != proposal["id"]:
            raise DetailsChanged("Approval must match the current proposal.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM appointments WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                return dict(existing), True
            service = db.execute("SELECT * FROM services WHERE id=?", (proposal["service_id"],)).fetchone()
            stylist = db.execute("SELECT * FROM stylists WHERE id=?", (proposal["stylist_id"],)).fetchone()
            start = datetime.fromisoformat(proposal["start"])
            if not service or not stylist or service["id"] not in stylist["services"].split(","):
                raise DetailsChanged("The service or stylist has changed.")
            end = start + timedelta(minutes=service["minutes"])
            if start.hour < 10 or end.date() != start.date() or end.time() > time(18):
                raise DetailsChanged("The appointment is outside salon opening hours.")
            if service["cents"] != proposal["cents"] or end.isoformat() != proposal["end"]:
                raise DetailsChanged("The price or duration changed. Fresh approval is required.")
            if start.replace(tzinfo=TIMEZONE) <= datetime.now(TIMEZONE):
                raise DetailsChanged("The proposed time is now in the past.")
            conflict = db.execute(
                "SELECT id FROM appointments WHERE stylist_id=? AND start < ? AND end > ?",
                (proposal["stylist_id"], proposal["end"], proposal["start"]),
            ).fetchone()
            if conflict:
                raise BookingConflict("Another appointment now overlaps this slot.")
            booking = {
                "id": uuid4().hex, "request_id": request_id, "proposal_id": proposal["id"],
                "customer": customer, "service_id": service["id"], "stylist_id": stylist["id"],
                "start": proposal["start"], "end": proposal["end"], "cents": proposal["cents"],
                "approved_at": datetime.now(TIMEZONE).isoformat(), "simulated": int(simulated),
            }
            db.execute(
                "INSERT INTO appointments VALUES (:id,:request_id,:proposal_id,:customer,:service_id,"
                ":stylist_id,:start,:end,:cents,:approved_at,:simulated)", booking,
            )
            return booking, False

    def appointments(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT a.*, s.name AS service, t.name AS stylist
                FROM appointments a JOIN services s ON s.id=a.service_id
                JOIN stylists t ON t.id=a.stylist_id ORDER BY a.start
            """)]

    def find_booking(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM appointments WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None
