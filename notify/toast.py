# notify/toast.py
"""Windows desktop notifications for human escalation."""

import logging
import os
import subprocess
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PENDING_DIR = os.environ.get("PENDING_DIR", "data/pending")
ESCALATION_TRACKER = os.path.join(
    os.path.dirname(PENDING_DIR), ".escalation_state.json"
)


def _load_state() -> dict:
    """Load escalation tracking state."""
    if os.path.exists(ESCALATION_TRACKER):
        try:
            with open(ESCALATION_TRACKER, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"count": 0, "first_at": None}


def _save_state(state: dict) -> None:
    try:
        with open(ESCALATION_TRACKER, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _windows_toast(title: str, message: str) -> None:
    """Show Windows toast notification using PowerShell."""
    try:
        # Escape special chars for PowerShell
        msg = message.replace('"', "'").replace('\n', ' ')
        ps_script = f'''
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName("text")
        $textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) > $null
        $textNodes.Item(1).AppendChild($template.CreateTextNode("{msg}")) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("WeCom AutoReply").Show($toast)
        '''
        subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.debug(f"Toast failed (falling back to simple): {e}")
        _simple_notify(title, message)


def _simple_notify(title: str, message: str) -> None:
    """Fallback: simple message box."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    except Exception:
        logger.warning("All notification methods failed")


def notify_escalation(
    customer_name: str,
    message_content: str,
    reason: str = "",
    draft_reply: str | None = None,
    is_group: bool = False,
) -> None:
    """Notify the operator that a message needs manual reply.

    Also updates the escalation tracker for multi-level alerts.
    """
    state = _load_state()

    # Update tracker
    now = datetime.now(timezone.utc).isoformat()
    if state["first_at"] is None:
        state["first_at"] = now
    state["count"] += 1
    _save_state(state)

    # Determine notification level
    level = 1
    extra = ""
    if state["count"] >= 3 and state["count"] < 6:
        level = 2
        extra = f"\n({state['count']}条待处理)"
    elif state["count"] >= 6:
        level = 3
        extra = f"\n⚠ 已累积{state['count']}条，请尽快处理!"

    chat_type = "群聊" if is_group else "私聊"
    draft_info = f"\n建议回复: {draft_reply[:80]}..." if draft_reply else ""

    title = f"智能客服 — 需要人工处理 [{chat_type}]"
    msg = (
        f"「{customer_name}」发来消息:\n"
        f"  {message_content[:100]}\n"
        f"原因: {reason or '置信度不足'}"
        f"{draft_info}"
        f"{extra}"
    )

    _windows_toast(title, msg)
    logger.info(f"Escalation notified: {title} | {msg[:80]}...")


def notify_group_suggestion(
    group_name: str,
    sender_name: str,
    question: str,
    suggested_reply: str,
) -> None:
    """Suggest a reply for a group chat question (operator decides)."""
    title = "智能客服 — 群聊回复建议"
    msg = (
        f"群「{group_name}」中 {sender_name} 发问:\n"
        f"  {question[:100]}\n"
        f"建议回复:\n"
        f"  {suggested_reply[:100]}\n\n"
        f"请手动发送（查看后点击关闭）"
    )
    _windows_toast(title, msg)


def reset_escalation_state() -> None:
    """Reset the escalation counter (call after human handles pending)."""
    _save_state({"count": 0, "first_at": None})
