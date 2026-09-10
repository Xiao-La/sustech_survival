# Language Tutoring (CLE / 语言中心语言指导服务)

One-on-one tutoring with **语言中心** (Center for Language Education) staff on
e-hall: exam prep, writing feedback, speaking practice, 出国文书 review.
One reservation books **one 25-minute session** in 琳恩图书馆二楼203 —
the room text comes from the slot, so read it rather than assuming.

**Auth:** `CleClient` needs an e-hall session bootstrapped in a browser
(`EhallSession`) — a bare CAS ticket answers 403 for the app's data
endpoints. See [SSO](sso.md) for the credential chain.

---

## CLI

```bash
sustech cle semester              # term, service window, teaching week, quota
sustech cle types                 # 指导范围 on offer this semester
sustech cle teachers              # teacher × service × room resources
sustech cle buckets               # fixed 25-minute time buckets
sustech cle schedule --week 3     # teacher grid for one week
sustech cle slots --days 5        # free slots, joined against live occupancy
sustech cle walkin                # today's leftovers (walk-in, quota-exempt)
sustech cle mine --all            # my reservations, cancelled/no-show included
sustech cle quota                 # reservations used / remaining
sustech cle policy                # the published rules, verbatim
sustech cle email --topic "IELTS 写作"   # 特别专项指导 request (email-only)
```

Writes are dry-run by default; `--commit --yes` fires the request:

```bash
sustech cle book preview --slot <slot_id> --topic "IELTS writing feedback"
sustech cle book apply   --slot <slot_id> --topic "IELTS writing feedback" --commit --yes
sustech cle cancel preview --reservation <wid>
sustech cle cancel apply   --reservation <wid> --commit --yes
```

`--slot` accepts the first characters of a slot id as printed by
`cle slots`; an ambiguous prefix is an error, not a guess. `cle mine` prints
the reservation id that `cle cancel` wants.

---

## Python API

```python
from sustech_survival.ehall.cle import CleClient

c = CleClient()
sem = c.semester()                        # term, window, week, quota
slots = c.slots(days=5, service="英语指导")
booked = c.book(slots[0], note="IELTS writing feedback")   # dry-run by default
```

Reads: `semester`, `service_types`, `teachers`, `time_buckets`, `schedule`,
`slots`, `walkin`, `my_reservations`, `quota`, `find_slot`,
`find_reservation`. Writes: `book`, `cancel`, each with a `preflight` gate in
`ehall/cle/reservation/`.

---

## Rules the service enforces

- **3 reservations per semester.** Walk-in slots do not count toward the limit.
- **Book at least one day ahead.** Same-day free slots are walk-ins only.
- **Cancel at least two days ahead.** Inside that window the app refuses and
  points at `cle@sustech.edu.cn`.
- **Two un-cancelled no-shows stop booking for the rest of the semester.**
- **预约说明 is required** and is where the topic goes (exam prep, task-2
  feedback, statement review). Written material is capped at 500 字.
- **特别专项指导 (Thursday ≥10:00) is email-only** and cannot be booked here;
  `cle email` renders the request with the topic, name and student id filled in.

---

## Wire

The CLE app serves EMAP model endpoints under `/dxggyw/sys/yyzxyy/`. Booking
posts the reservation model's **entire control set** (the server rejects a
subset with `#E2140600091`), and cancellation is a **status write** on the
我的预约 route — not a delete. Field-level detail lives with the module:
`ehall/cle/reservation/wire.py` (docstrings quote the captured request).

---

## See also

- [E-Hall Booking](booking.md) — campus venue reservations.
- [Library Booking](lib-booking.md) — IC library discussion rooms.
- [TIS](tis.md) — class schedule, to find a free tutoring window.
