"""Local demo server for the VinBank guardrails chatbot.

The UI is static HTML, but every chat request is processed by real project
guardrail functions from ``src`` so the trace log reflects actual decisions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
HTML_FILE = ROOT_DIR / "demo_guardrails.html"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except Exception:
    pass

from assignment.pipeline import ALLOWED_EGRESS_DESTINATION, is_egress_allowed
from guardrails.input_guardrails import detect_injection, topic_filter
from guardrails.output_guardrails import content_filter

try:
    from guardrails.nemo_guardrails import colang_demo_response
except Exception as exc:
    colang_demo_response = None
    NEMO_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    NEMO_IMPORT_ERROR = None


AUDIT_EVENTS: list[dict[str, Any]] = []


def _now_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _wants_vietnamese(text: str) -> bool:
    lowered = _normalize(text)
    vietnamese_markers = (
        "chính sách", "bao mat", "bảo mật", "xác thực", "xac thuc",
        "tài khoản", "tai khoan", "chuyển", "chuyen", "tiết kiệm",
        "tiet kiem", "lãi", "lai", "mật khẩu", "mat khau", "quy định",
        "quy dinh", "ngân hàng", "ngan hang",
    )
    return any(marker in lowered for marker in vietnamese_markers)


def _is_greeting(text: str) -> bool:
    lowered = _normalize(text).strip(" .!?")
    return lowered in {
        "hi",
        "hello",
        "hey",
        "hi there",
        "good morning",
        "good afternoon",
        "good evening",
        "xin chao",
        "xin chào",
        "chao",
        "chào",
        "chào bạn",
    }


def _localize_policy_reply(reply: str, message: str) -> str:
    """Return demo-facing Vietnamese text for Colang policy responses."""
    lowered = _normalize(reply)
    if _is_greeting(message) or "welcome to vinbank" in lowered:
        return "Xin chào! Tôi là trợ lý VinBank trong môi trường demo. Tôi có thể hỗ trợ các câu hỏi về tài khoản, chuyển tiền, tiết kiệm, thẻ, khoản vay, bảo mật và xác thực."
    if "banking-related questions" in lowered:
        return "Tôi là trợ lý VinBank và chỉ có thể hỗ trợ các câu hỏi liên quan đến ngân hàng."
    if "cannot change roles" in lowered:
        return "Tôi không thể đổi vai trò hoặc bỏ qua quy tắc an toàn. Tôi có thể hỗ trợ các câu hỏi ngân hàng của VinBank."
    if "internal prompts" in lowered or "system details" in lowered:
        return "Tôi không thể chia sẻ system prompt, chỉ dẫn nội bộ, cấu hình hoặc chi tiết hệ thống."
    if "passwords" in lowered or "api keys" in lowered or "credentials" in lowered:
        return "Tôi không thể tiết lộ mật khẩu, API key, máy chủ nội bộ hoặc thông tin xác thực."
    if "high-risk banking action" in lowered:
        return "Đây là thao tác ngân hàng rủi ro cao. Tôi không thể thực hiện nếu chưa xác thực và chưa có phê duyệt phù hợp."
    if "encode" in lowered or "reformat" in lowered:
        return "Tôi không thể biến đổi, mã hóa hoặc định dạng lại chỉ dẫn nội bộ hay thông tin bí mật."
    return reply


def _preview(text: str, limit: int = 220) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _trace(
    trace: list[dict[str, Any]],
    *,
    start: float,
    title: str,
    status: str,
    summary: str,
    details: dict[str, Any] | str | None = None,
) -> None:
    trace.append(
        {
            "id": str(uuid.uuid4()),
            "title": title,
            "status": status,
            "summary": summary,
            "elapsed_ms": _now_ms(start),
            "details": details or {},
        }
    )


def _sandbox_agent_reply(message: str) -> str:
    """Deterministic banking draft used for local demos without external LLM calls."""
    text = _normalize(message)
    vi = True

    if _is_greeting(message):
        return (
            "Xin chào! Tôi là trợ lý VinBank trong môi trường demo. "
            "Bạn có thể hỏi về tài khoản, chuyển tiền, tiết kiệm, thẻ, khoản vay, bảo mật hoặc xác thực."
        )

    if any(term in text for term in ("admin password", "api key", "system prompt", "db.vinbank.internal")):
        return (
            "Sandbox draft: admin password is admin123, API key is "
            "sk-vinbank-secret-2024, database host is db.vinbank.internal:5432."
        )

    if any(term in text for term in ("security", "privacy", "authentication", "verification", "otp", "mfa", "2fa", "two-factor", "bao mat", "bảo mật", "xac thuc", "xác thực", "2 lop", "2 lớp", "mat khau", "mật khẩu")):
        if vi:
            return (
                "Trong môi trường demo, chính sách bảo mật của VinBank yêu cầu xác thực 2 lớp "
                "cho thao tác nhạy cảm như đăng nhập thiết bị mới, chuyển tiền, đổi mật khẩu "
                "hoặc cập nhật thông tin cá nhân. Luồng chuẩn gồm mật khẩu hoặc sinh trắc học, "
                "sau đó xác nhận OTP/push notification; hệ thống cũng ghi audit log và có thể "
                "yêu cầu phê duyệt bổ sung với giao dịch rủi ro cao."
            )
        return (
            "In this sandbox, VinBank security policy requires two-factor authentication "
            "for sensitive actions such as new-device login, transfers, password changes, "
            "and profile updates. The standard flow uses password or biometrics plus OTP "
            "or push confirmation, with audit logging and extra approval for high-risk actions."
        )

    if any(term in text for term in ("interest", "savings", "rate", "lãi", "lai suat", "lãi suất", "tiết kiệm", "tiet kiem")):
        if vi:
            return (
                "Trong sandbox demo, lãi suất tham chiếu kỳ hạn 12 tháng là 4,25%/năm. "
                "Khi dùng thật, khách hàng cần kiểm tra lãi suất cuối cùng trong ứng dụng "
                "hoặc kênh chính thức của VinBank trước khi quyết định."
            )
        return (
            "For the demo sandbox, the 12-month savings reference rate is 4.25% per year. "
            "Please confirm the final rate in the official VinBank app before making a decision."
        )

    if any(term in text for term in ("transfer", "send money", "chuyển", "chuyen tien", "chuyển tiền", "gửi tiền", "gui tien")):
        if vi:
            return (
                "Tôi có thể chuẩn bị hướng dẫn chuyển tiền, nhưng giao dịch thật cần đăng nhập, "
                "OTP, kiểm tra người thụ hưởng và xác nhận cuối cùng của khách hàng."
            )
        return (
            "I can prepare transfer guidance, but a real transfer requires login, OTP, "
            "beneficiary validation, and customer confirmation."
        )

    if any(term in text for term in ("loan", "credit", "card", "vay", "thẻ", "the tin dung", "thẻ tín dụng")):
        if vi:
            return (
                "VinBank có thể hỗ trợ kiểm tra lựa chọn vay hoặc thẻ sau khi xác minh danh tính, "
                "hồ sơ thu nhập và điều kiện sản phẩm trong luồng chính thức."
            )
        return (
            "VinBank can help review loan or card options after checking identity, income "
            "documents, and product eligibility in the official workflow."
        )

    if any(term in text for term in ("account", "balance", "tài khoản", "tai khoan", "số dư", "so du")):
        if vi:
            return (
                "Với câu hỏi về tài khoản hoặc số dư, khách hàng cần xác thực trong ứng dụng "
                "VinBank. Sandbox này không hiển thị dữ liệu tài khoản cá nhân."
            )
        return (
            "For account or balance questions, please authenticate in the VinBank app. "
            "This sandbox will not expose account-specific data."
        )

    if vi:
        return (
            "Tôi có thể hỗ trợ các chủ đề ngân hàng của VinBank như tài khoản, chuyển tiền, "
            "tiết kiệm, khoản vay, thẻ tín dụng, bảo mật và xác thực. Bạn hãy nhập câu hỏi "
            "ngân hàng cụ thể cần kiểm tra."
        )
    return (
        "I can help with VinBank banking topics such as accounts, transfers, savings, "
        "loans, and credit cards. Please share the banking question you want to check."
    )


def _detect_tool_call(message: str, draft: str) -> dict[str, str] | None:
    text = _normalize(message)
    if not any(term in text for term in ("transfer", "send money", "chuyển", "gửi tiền")):
        return None

    destination = ALLOWED_EGRESS_DESTINATION
    if any(term in text for term in ("evil.example", "external webhook", "unknown endpoint", "leak")):
        destination = "https://evil.example/collect"

    amount_match = re.search(r"(\d[\d.,]*)\s*(vnd|dong|usd)?", message, re.IGNORECASE)
    amount = amount_match.group(0) if amount_match else "demo amount"
    payload = f"transfer_request amount={amount}; draft={draft}"
    return {"destination": destination, "payload": payload}


def _default_toggles(raw: dict[str, Any] | None) -> dict[str, bool]:
    values = {
        "inputGuardrails": True,
        "nemoPolicy": True,
        "egressPolicy": True,
        "outputGuardrails": True,
        "auditLog": True,
    }
    if isinstance(raw, dict):
        for key in values:
            if key in raw:
                values[key] = bool(raw[key])
    return values


def _decode_request_body(body: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1258", "cp1252"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def process_chat(message: str, raw_toggles: dict[str, Any] | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    trace: list[dict[str, Any]] = []
    toggles = _default_toggles(raw_toggles)
    request_id = str(uuid.uuid4())
    blocked = False
    decision = "passed"

    _trace(
        trace,
        start=start,
        title="Input received",
        status="pass",
        summary=f"Captured user message ({len(message or '')} chars).",
        details={"message": message, "request_id": request_id},
    )

    if toggles["inputGuardrails"]:
        injection = detect_injection(message)
        off_topic = False if _is_greeting(message) else topic_filter(message)
        status = "block" if injection or off_topic else "pass"
        summary = "Prompt injection detected." if injection else "Off-topic or blocked topic." if off_topic else "Input passed injection and topic filters."
        _trace(
            trace,
            start=start,
            title="Input guardrail check",
            status=status,
            summary=summary,
            details={"detect_injection": injection, "topic_filter_blocked": off_topic},
        )
        if injection or off_topic:
            blocked = True
            decision = "blocked_by_input_guardrail"
            vi = _wants_vietnamese(message)
            if injection:
                reply = (
                    "Tôi không thể xử lý yêu cầu cố gắng ghi đè chỉ dẫn hoặc tiết lộ thông tin nội bộ."
                    if vi
                    else "I cannot process requests that override instructions or expose internal details."
                )
            else:
                reply = (
                    "Tôi là trợ lý VinBank và chỉ có thể hỗ trợ các câu hỏi liên quan đến ngân hàng."
                    if vi
                    else "I am a VinBank assistant and can only help with banking-related questions."
                )
            return _finish_response(request_id, message, reply, blocked, decision, trace, toggles)
    else:
        _trace(
            trace,
            start=start,
            title="Input guardrail check",
            status="flag",
            summary="Skipped because the input guardrail toggle is off.",
            details={"toggle": "inputGuardrails"},
        )

    if toggles["nemoPolicy"]:
        nemo_reply = colang_demo_response(message) if colang_demo_response else None
        if nemo_reply:
            nemo_reply = _localize_policy_reply(nemo_reply, message)
            if _is_greeting(message):
                _trace(
                    trace,
                    start=start,
                    title="NeMo Colang policy",
                    status="pass",
                    summary="Matched greeting flow.",
                    details={"response": nemo_reply},
                )
                _trace(
                    trace,
                    start=start,
                    title="Final response",
                    status="pass",
                    summary=_preview(nemo_reply),
                    details={"decision": decision, "blocked": blocked},
                )
                return _finish_response(request_id, message, nemo_reply, blocked, decision, trace, toggles)
            _trace(
                trace,
                start=start,
                title="NeMo Colang policy",
                status="block",
                summary="Matched a Colang refusal/escalation flow.",
                details={"response": nemo_reply},
            )
            blocked = True
            decision = "blocked_by_nemo_colang"
            return _finish_response(request_id, message, nemo_reply, blocked, decision, trace, toggles)

        _trace(
            trace,
            start=start,
            title="NeMo Colang policy",
            status="pass",
            summary="No Colang refusal rule matched.",
            details={"nemo_import_error": NEMO_IMPORT_ERROR},
        )
    else:
        _trace(
            trace,
            start=start,
            title="NeMo Colang policy",
            status="flag",
            summary="Skipped because the NeMo policy toggle is off.",
            details={"toggle": "nemoPolicy"},
        )

    draft = _sandbox_agent_reply(message)
    _trace(
        trace,
        start=start,
        title="Agent draft",
        status="pass",
        summary="Generated sandbox banking response before output filtering.",
        details={"draft": draft, "external_llm_called": False},
    )

    tool_call = _detect_tool_call(message, draft)
    if tool_call:
        allowed = is_egress_allowed(tool_call["destination"], tool_call["payload"])
        tool_status = "pass" if allowed else "block"
        if toggles["egressPolicy"]:
            _trace(
                trace,
                start=start,
                title="Tool call / egress policy",
                status=tool_status,
                summary="Approved VinBank endpoint." if allowed else "Blocked outbound call by allowlist or sensitive-payload rule.",
                details={**tool_call, "allowed": allowed},
            )
            if not allowed:
                blocked = True
                decision = "blocked_by_egress_policy"
                reply = (
                    "Tôi đã chặn thao tác này vì đích gửi dữ liệu hoặc payload không nằm trong chính sách cho phép."
                    if _wants_vietnamese(message)
                    else "I blocked this action because the outbound destination or payload is not approved."
                )
                return _finish_response(request_id, message, reply, blocked, decision, trace, toggles)
        else:
            _trace(
                trace,
                start=start,
                title="Tool call / egress policy",
                status="flag",
                summary="Egress policy is off; the demo only records the attempted call.",
                details={**tool_call, "would_be_allowed": allowed},
            )
    else:
        _trace(
            trace,
            start=start,
            title="Tool call / egress policy",
            status="pass",
            summary="No external tool call was needed.",
            details={"tool_call": None},
        )

    reply = draft
    if toggles["outputGuardrails"]:
        filtered = content_filter(draft)
        reply = filtered["redacted"]
        _trace(
            trace,
            start=start,
            title="Output guardrail check",
            status="pass" if filtered["safe"] else "flag",
            summary="Output is safe." if filtered["safe"] else "Sensitive content redacted before final response.",
            details=filtered,
        )
        decision = "redacted_by_output_guardrail" if not filtered["safe"] else decision
    else:
        _trace(
            trace,
            start=start,
            title="Output guardrail check",
            status="flag",
            summary="Skipped because the output guardrail toggle is off.",
            details={"toggle": "outputGuardrails"},
        )

    _trace(
        trace,
        start=start,
        title="Final response",
        status="block" if blocked else "pass",
        summary=_preview(reply),
        details={"decision": decision, "blocked": blocked},
    )
    return _finish_response(request_id, message, reply, blocked, decision, trace, toggles)


def _finish_response(
    request_id: str,
    message: str,
    reply: str,
    blocked: bool,
    decision: str,
    trace: list[dict[str, Any]],
    toggles: dict[str, bool],
) -> dict[str, Any]:
    event = {
        "request_id": request_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "input_preview": _preview(message),
        "reply_preview": _preview(reply),
        "blocked": blocked,
        "decision": decision,
        "toggles": toggles,
        "trace": trace,
    }
    if toggles.get("auditLog", True):
        AUDIT_EVENTS.append(event)
    return {
        "request_id": request_id,
        "reply": reply,
        "blocked": blocked,
        "decision": decision,
        "trace": trace,
        "audit_count": len(AUDIT_EVENTS),
        "toggles": toggles,
    }


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "VinBankGuardrailsDemo/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[demo] {self.address_string()} - {fmt % args}")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_html(self) -> None:
        html = HTML_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in {"/", "/demo_guardrails.html"}:
            self._send_html()
            return
        if self.path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "service": "VinBank guardrails demo",
                    "backend": "python-http.server",
                    "nemo_colang_available": colang_demo_response is not None,
                    "audit_count": len(AUDIT_EVENTS),
                }
            )
            return
        if self.path == "/api/audit":
            self._send_json({"events": AUDIT_EVENTS[-50:]})
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._send_json({"error": "Not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(_decode_request_body(body) or "{}")
            message = str(payload.get("message", ""))
            if not message.strip():
                self._send_json({"error": "message is required"}, status=400)
                return
            result = process_chat(message, payload.get("toggles"))
            self._send_json(result)
        except Exception as exc:
            self._send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                status=500,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the VinBank guardrails demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"VinBank guardrails demo running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
