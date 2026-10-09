# PMS (Campus Print)

Read printer status and the upload queue, and upload documents to the campus PMS.

## Authentication

Install the `[pms]` extra, which includes the legacy RSA login dependency.

```python
from sustech_survival.sso import PMSAuth

auth = PMSAuth()
ok, reason = auth.ensure()
if not ok:
    raise RuntimeError(reason)
```

`ensure()` follows the site's `Auth/SSoPage` entry, discovers the current CAS
service callback, and reuses the same in-memory cookie jar through authcenter,
CAS and PMS. It verifies the resulting session with `Auth/Check`. No browser
is required. Interactive authentication challenges stop the flow.

`login_via_cas()` uses this same flow (`headless` is retained for compatibility).
`login_password()` is an explicit legacy RSA print-account login requiring the
`[pms]` extra. It is not the default CAS path, and an invalid-session response
from it does not establish that the campus password is wrong. No automatic
fallback to that endpoint occurs.

## CLI

```bash
sustech pms check
sustech pms jobs --json
sustech pms stations --json
```

Authentication/read failures exit nonzero. Authentication diagnostics go to
stderr so JSON output remains parseable.

## Python API and upload receipts

```python
from sustech_survival.pms import pms

client = pms()  # verifies/reuses the PMS authorizer session
jobs = client.list_print_jobs()
preview = client.upload_print("PMS_TEST.pdf", paper="A4", color="bw",
                              duplex="long", copies=1, dry_run=True)
# After reviewing the file/options and obtaining authorization:
receipt = client.upload_print("PMS_TEST.pdf", paper="A4", color="bw",
                              duplex="long", copies=1)
print(receipt.status, receipt.job_id, receipt.verification_url)
```

Dry runs perform no network calls. A real upload snapshots queue IDs, sends one
multipart POST with the correct queue page as `BackURL`, parses JSON or the
same-site result redirect, and reads the queue once. It does not automatically
follow upload redirects or repeat the POST.

- `status="confirmed"`, `ok=True`, `uploaded=True`: one new matching queue job
  was read back; `job_id` identifies it.
- `status="unknown"`, `ok=False`, `uploaded=None`: the result could not be
  confirmed, including a lost response, delayed/failed queue read or multiple
  new matches. Inspect `observed_job_ids`, `http_status`, `response_format` and
  `verification_error`, then query the queue before considering any retry.
- `status="rejected"`, `uploaded=False`: an explicit rejection and no new
  matching queue job. An unavailable initial queue snapshot sends no upload.

`ok=False` alone is never a reason to resend a file. Uploading queues a document;
this API does not trigger physical printing.

Queue verification URL: <https://pms.sustech.edu.cn/client/new/cprintPc/printDoc.html>.

## Duplex values and unresolved edge semantics

The upload form labels `dwDuplex=2` as short edge and `3` as long edge. Existing
`duplex="short"`/`"long"` aliases and constants retain those values. Live queue
round trips returned `2 -> vdup` and `3 -> hdup`. However, the upload and queue
pages disagree on the edge labels and their translation IDs. These observations
do not verify the printer's physical binding direction.

Consequently, queue records retain `duplex_flag` and report duplex with
`duplex_edge=None` and an explicit unconfirmed-edge label. They do not silently
swap the values or assert that `hdup` means short edge. Missing/conflicting
flags yield `is_duplex=None`. Upload previews identify the website option and
also state that the physical edge is unverified.

Other client methods: `list_server_groups()`, `list_stations(group_sn=None)`,
`list_scan_jobs()`, `history(begin=..., end=...)`, `delete_print_job(job_id)` and
`delete_scan_job(job_id)`. Deletion changes the account and needs authorization.
