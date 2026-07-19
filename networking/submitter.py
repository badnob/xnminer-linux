from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from core.models import BlockHit
from monitoring.logger import SessionLogger
from networking.submit_result import submit_accepted


class Submitter:
    def __init__(self, verify_url: str, account: str, worker: str, logger: SessionLogger) -> None:
        self.verify_url = verify_url
        self.account = account
        self.worker = worker
        self.logger = logger

    def submit(self, hit: BlockHit, timeout_s: int = 20) -> dict:
        payload = hit.to_submit_payload(self.account, self.worker)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.verify_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        result = {
            "ok": False,
            "status": 0,
            "body": "",
            "payload": payload,
            "submitted_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                result["status"] = resp.status
                result["body"] = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            result["status"] = exc.code
            result["body"] = exc.read().decode("utf-8", errors="replace")
        except Exception as exc:
            result["body"] = str(exc)

        result["ok"] = submit_accepted(result["status"], result["body"])

        if self.logger.echo_console:
            level = self.logger.info if result["ok"] else self.logger.warn
            level(
                f"SUBMIT {hit.block_type} {hit.strategy} "
                f"status={result['status']} ok={result['ok']}"
            )
        return result